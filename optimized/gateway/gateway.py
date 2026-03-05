from turtle import pd

from fastapi import FastAPI, Body
import requests
import time
import datetime
import os
import random
import pymysql
import pymysql.cursors
import json
from typing import Dict, Any
import json
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

GATEWAY_HOST, GATEWAY_PORT = "127.0.0.1", 8080
SERVER_URL = "http://127.0.0.1:8000"
LOG_DIR = "logs/optimized"
LOG_FILE = os.path.join(LOG_DIR, "gateway.log")
MAX_LOG_SIZE = 50 * 1024 * 1024
MAX_CACHE_SIZE = 1000  # 网关降级库最大缓存条数
ATTEMPT_TIMES = 3

UPSTREAM_FAULT_PROB = 0.2
DOWNSTREAM_FAULT_PROB = 0.2

# ================= AWS RDS MySQL 配置 =================
RDS_HOST = 'database-1.c6e9ussx1jfj.us-east-1.rds.amazonaws.com'
RDS_USER = 'admin'
RDS_PASSWORD = '12345678'
RDS_DB_NAME = 'gatewaycache'
RDS_DB_TABLE = 'task_cache'
RDS_PORT = 3306

class OptimizedGateway:
    def __init__(self,gateway_host:str=GATEWAY_HOST,gateway_port:int=GATEWAY_PORT,server_url:str = SERVER_URL):
        self.app = FastAPI()
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.server_url = server_url
        os.makedirs(LOG_DIR, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=10)
        self._init_db()

    def _get_ts(self) -> str: return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _clean_log(self):
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_SIZE:
            with open(LOG_FILE, "r", encoding="utf-8") as f: lines = f.readlines()
            reserve = int(len(lines) * 2 / 3)
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{self._get_ts()}] [OPT_GATEWAY] [LOG_CLEAN] 日志达50M上限，清理最早1/3\n")
                f.writelines(lines[-reserve:] if reserve > 0 else [])

    def _log(self, level: str, msg: str):
        self._clean_log()
        log_content = f"[{self._get_ts()}] [OPT_GATEWAY] [{level}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(log_content)
        print(log_content.strip())

# ================= AWS RDS (MySQL) 操作 =================

    def _get_db_connection(self):
        """获取 RDS 数据库连接"""
        return pymysql.connect(
            host=RDS_HOST,
            user=RDS_USER,
            password=RDS_PASSWORD,
            database=RDS_DB_NAME,
            port=RDS_PORT,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5  # 防止连不上RDS导致网关启动卡死
        )
        
    def _init_db(self):
        """初始化 RDS 降级缓存表"""
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                # 使用 MySQL 语法: AUTO_INCREMENT, 并利用原生 JSON 数据类型
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
            connection.close()
            self._log("INIT", f"已成功连接并挂载 AWS RDS MySQL: {RDS_HOST}")
        except Exception as e:
            self._log("ERROR", f"RDS 数据库初始化失败，请检查网络或白名单: {e}")

    def _save_to_cache(self, task_type: str, task_id: str, response_data: dict):
        def _do_save():
            try:
                connection = self._get_db_connection()
                with connection.cursor() as cursor:
                    cursor.execute(
                        f'INSERT INTO {RDS_DB_TABLE} (task_type, task_id, response_data, ts) VALUES (%s, %s, %s, %s)',
                        (task_type, task_id, json.dumps(response_data), time.time())
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
                connection.close()
            except Exception as e:
                self._log("ERROR", f"后台写入 RDS 失败: {e}")

        self.executor.submit(_do_save)
            
    def _get_from_cache(self, task_type: str,task_id: str) -> dict:
        """重试耗尽时：从 RDS 取出最新历史脏数据"""
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT response_data FROM task_cache WHERE task_type = %s AND task_id = %s ORDER BY ts DESC LIMIT 1',
                    (task_type,task_id)
                )
                row = cursor.fetchone()
            connection.close()
            
            if row:
                # pymysql DictCursor 结合 JSON 字段，有时候直接返回 dict，有时返回 str，做个兼容
                data = row['response_data']
                return json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            self._log("ERROR", f"读取 RDS 缓存失败: {e}")
        return None

    def _clear_cache_table(self):
            """清空 RDS 中的任务缓存表"""
            try:
                connection = self._get_db_connection()
                with connection.cursor() as cursor:
                    # 使用 TRUNCATE 清空数据并重置自增 ID
                    cursor.execute(f'TRUNCATE TABLE {RDS_DB_TABLE}')
                connection.commit()
                connection.close()
                self._log("INIT", f"已成功清空 RDS 缓存表: {RDS_DB_TABLE}")
            except Exception as e:
                self._log("ERROR", f"清空 RDS 缓存表失败: {e}")
                
    def export_cache_to_dataframe():
        try:
            query = f"SELECT * FROM {RDS_DB_TABLE}"
            df = pd.read_sql(query, self._get_db_connection())
            print(f"成功从 RDS 导出数据，总计: {len(df)} 行。")
            return df
            
        except Exception as e:
            print(f"导出失败: {e}")
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
                    
                    self._save_to_cache(task_type=task_type, task_id=task_id, response_data=success_response)
                    self._log("SUCCESS", "成功拿到服务端响应。")
                    break
                except Exception as _:
                    self._log("RETRY", f"[REQ-{request_id}] [第 {attempt} 次尝试] 获取响应失败，重试中...")
                    time.sleep(0.2)
            
            # 如果重试耗尽，从 AWS RDS 捞取降级缓存
            if not success_response:
                self._log("DEGRADE_START", f"[REQ-{request_id}] 重试完全耗尽，正从 AWS RDS 提取兜底缓存...")
                cached_data = self._get_from_cache(task_type,task_id)
                
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
                time.sleep(6.0)
                
            return success_response

        @self.app.post("/api/report")
        async def report(payload: Dict[str, Any] = Body(...)):
            return requests.post(f"{self.server_url}/api/report_fault", json=payload).json()

        import uvicorn
        self._log("START", f"网关启动，监听 {self.gateway_host}:{self.gateway_port} ...")
        uvicorn.run(self.app, host=self.gateway_host, port=self.gateway_port, log_level="error")
