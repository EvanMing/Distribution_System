import os
import time
import datetime
import random
import json
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pymysql
import pymysql.cursors
import requests
from fastapi import FastAPI, Body
from contextlib import closing
import os
from dotenv import load_dotenv

# 引入数据库连接池
from dbutils.pooled_db import PooledDB

from common.baseline import ATTEMPT_TIMES, DOWNSTREAM_FAULT_PROB, MAX_CACHE_SIZE, RDS_DB_NAME, RDS_DB_TABLE, RDS_HOST, RDS_PASSWORD, RDS_PORT, RDS_USER, TIME_SLEEP, UPSTREAM_FAULT_PROB, get_ts 

LOG_DIR = "logs/optimized"
LOG_FILE = os.path.join(LOG_DIR, "gateway.log")
MAX_LOG_SIZE = 50 * 1024 * 1024

class OptimizedGateway:
    
    def __init__(self, gateway_host:str, gateway_port:int, server_url:str):
        self.app = FastAPI()
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.server_url = server_url
        os.makedirs(LOG_DIR, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        # 1. 优先初始化数据库连接池
        self._init_db_pool()
        # 2. 然后再初始化表结构
        self._init_db()
        # 3. 初始化时清空脏数据（你之前加的需求）
        # self._clear_cache_table()
        
        self._export_cache_to_csv()

    def _clean_log(self):
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_SIZE:
            with open(LOG_FILE, "r", encoding="utf-8") as f: lines = f.readlines()
            reserve = int(len(lines) * 2 / 3)
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{get_ts()}] [OPT_GATEWAY] [LOG_CLEAN] 日志达50M上限，清理最早1/3\n")
                f.writelines(lines[-reserve:] if reserve > 0 else [])

    def _log(self, level: str, msg: str):
        self._clean_log()
        log_content = f"[{get_ts()}] [OPT_GATEWAY] [{level}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(log_content)
        print(log_content.strip())

# ================= AWS RDS (MySQL) 操作优化 =================

    def _init_db_pool(self):
        """初始化 PyMySQL 连接池 (支持多线程高并发)"""
        try:
            self.db_pool = PooledDB(
                creator=pymysql,            # 使用的数据库模块
                maxconnections=10,          # 连接池允许的最大连接数
                mincached=3,                # 初始化时，连接池中预先创建的空闲连接
                maxcached=10,               # 链接池中最多闲置的链接
                maxshared=0,                # 共享连接数（pymysql 不适用，设为 0）
                blocking=True,              # 池中无可用连接时是否阻塞等待
                maxusage=None,              # 单个连接最多被重复使用的次数 (None 表示无限制)
                ping=0,                     # 1 = 每次从池中取出时 ping 一下检查连接是否有效，防止 MySQL wait_timeout 断开,0 (不进行连接健康检查)
                host=RDS_HOST,
                user=RDS_USER,
                password=RDS_PASSWORD,
                database=RDS_DB_NAME,
                port=RDS_PORT,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5
            )
            self._log("INIT", "AWS RDS MySQL 连接池初始化成功！")
        except Exception as e:
            self._log("ERROR", f"RDS 连接池初始化失败: {e}")

    def _get_db_connection(self):
        """从连接池中获取一个可用的连接"""
        # 注意：这里拿到的 connection 在调用 .close() 时，不会真正断开，而是归还给连接池
        return self.db_pool.connection()
        
    def _init_db(self):
        """初始化 RDS 降级缓存表"""
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS task_cache (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        task_type VARCHAR(255) NOT NULL,
                        task_id VARCHAR(20) NOT NULL,
                        response_data JSON NOT NULL,
                        ts DOUBLE NOT NULL,
                        INDEX idx_task_type (task_type)
                    )
                ''')
            connection.commit()
            connection.close() # 释放回连接池
            self._log("INIT", f"已成功连接并挂载 AWS RDS MySQL: {RDS_HOST}")
        except Exception as e:
            self._log("ERROR", f"RDS 数据库初始化失败，请检查网络或白名单: {e}")

    def _save_to_cache(self, task_type: str, task_id: str, response):
        def _do_save():
            
            response_data = response.get('response_data')
            if isinstance(response_data, dict):
                response_data = json.dumps(response_data, ensure_ascii=False)
            
            try:
                with closing(self._get_db_connection()) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f'INSERT INTO {RDS_DB_TABLE} (task_type, task_id, response_data, ts) VALUES (%s, %s, %s, %s)',
                            (task_type, task_id, response_data, datetime.datetime.now().timestamp())
                        )
                        
                        # 容量控制
                        cursor.execute(f'SELECT COUNT(*) as count FROM {RDS_DB_TABLE}')
                        count = cursor.fetchone()['count']
                        if count > MAX_CACHE_SIZE:
                            delete_count = int(MAX_CACHE_SIZE * 0.2)
                            cursor.execute(
                                f'DELETE FROM {RDS_DB_TABLE} ORDER BY ts ASC LIMIT %s',
                                (delete_count,)
                            )
                            self._log("CACHE_CLEAN", f"RDS 自动清理了 {delete_count} 条数据。")
                    connection.commit()
            except Exception as e:
                self._log("ERROR", f"后台写入 RDS 失败: {e}")

        self.executor.submit(_do_save)
            
    def _get_from_cache(self, task_type: str, task_id: str) -> dict:
        
        connection = None
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT response_data FROM task_cache WHERE task_type = %s AND task_id = %s ORDER BY ts DESC LIMIT 1',
                    (task_type, task_id)
                )
                row = cursor.fetchone()
                
            if row:
                data = row['response_data']
                return json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            self._log("ERROR", f"读取 RDS 缓存失败: {e}")
        finally:
            if connection:
                connection.close()
        return None

    def _clear_cache_table(self):
        """清空 RDS 中的任务缓存表"""
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute(f'TRUNCATE TABLE {RDS_DB_TABLE}')
            connection.commit()
            connection.close() # 释放回连接池
            self._log("INIT", f"已成功清空 RDS 缓存表: {RDS_DB_TABLE}")
        except Exception as e:
            self._log("ERROR", f"清空 RDS 缓存表失败: {e}")
                
    def _export_cache_to_csv(self, filename: str = "gateway_task_cache.csv"):
        try:
            query = f"SELECT * FROM {RDS_DB_TABLE}"
            connection = self._get_db_connection()
            
            data = None
            
            with connection.cursor() as cursor:
                cursor.execute(query)
                # fetchall() 拿到的直接是形如 [{'id': 1, 'task_type': '...'}, {...}] 的数据
                data = cursor.fetchall()
            
            connection.close() 
            
            if not data:
                self._log("EXPORT", "RDS 缓存表为空，没有数据可导出。")
            
            df = pd.DataFrame(data)
            if not df.empty:
                # index=False 表示不导出 DataFrame 的行索引
                # encoding='utf-8-sig' 可以完美兼容 Windows Excel，防止中文乱码
                df.to_csv(filename, index=False, encoding='utf-8-sig')
                self._log("EXPORT", f"成功从 RDS 导出数据，总计: {len(df)} 行，已保存至: {filename}")
            else:
                self._log("EXPORT", "RDS 缓存表为空，没有数据可导出。")
                
            return df
            
        except Exception as e:
            self._log("ERROR", f"导出 CSV 失败: {e}")
            return pd.DataFrame()
                
# ========================================================

    def run(self):
        
        @self.app.get("/api/forward")
        async def forward(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            attempt = 0
            success_response = None
            params = {"request_id": request_id, "task_id": task_id, "task_type": task_type}
            
            while attempt < ATTEMPT_TIMES:
                attempt += 1
                try:
                    res = requests.get(f"{self.server_url}/api/process", params=params, timeout=2.0)
                    self._log("SUCCESS", f"[REQ-{request_id}] [第 {attempt} 次尝试]")
                    
                    if random.random() < UPSTREAM_FAULT_PROB:
                        self._log("WARNING", f"[REQ-{request_id}] 模拟上游网络丢包。触发网关对 {task_type} 任务的自适应重试！")
                        raise requests.exceptions.Timeout("Simulated upstream loss")
                    
                    success_response = res.json()
                    self._save_to_cache(task_type=task_type, task_id=task_id, response=success_response)
                    self._log("SUCCESS", "成功拿到服务端响应。")
                    break
                except Exception as _:
                    self._log("RETRY", f"[REQ-{request_id}] [第 {attempt} 次尝试] 获取响应失败，重试中...")
                    time.sleep(0.2)
            
            # 如果重试耗尽，从 AWS RDS 捞取降级缓存
            if not success_response:
                self._log("DEGRADE_START", f"[REQ-{request_id}] 重试完全耗尽，正从 AWS RDS 提取兜底缓存...")
                cached_data = self._get_from_cache(task_type, task_id)
                
                if cached_data:
                    self._log("DEGRADE_HIT", f"[REQ-{request_id}] 命中 RDS 降级缓存！执行兜底返回。")
                    cached_data["status"] = "success"
                    cached_data["gateway_note"] = "Gateway Cache Fallback (From AWS RDS)"
                    success_response = cached_data
                else:
                    self._log("DEGRADE_MISS", f"[REQ-{request_id}] RDS 中无 {task_type} 缓存，请求彻底失败。")
                    return {"status": "failed", "response_data": "Retry exhausted & No RDS cache"}
                
            if random.random() < DOWNSTREAM_FAULT_PROB:
                self._log("FAULT", f"[REQ-{request_id}] 模拟下游网络丢包(Gateway->Client)。导致客户端超时！")
                time.sleep(TIME_SLEEP)
                
            return success_response

        @self.app.post("/api/report")
        async def report(payload: Dict[str, Any] = Body(...)):
            return requests.post(f"{self.server_url}/api/report_fault", json=payload).json()

        import uvicorn
        self._log("START", f"网关启动，监听 {self.gateway_host}:{self.gateway_port} ...")
        uvicorn.run(self.app, host=self.gateway_host, port=self.gateway_port, log_level="error")
        
        