import asyncio
from collections import deque
import os
import time
import datetime
import random
import json
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd
import pymysql
import pymysql.cursors
import requests
from fastapi import FastAPI, Body
from contextlib import closing
import os
import uvicorn

# 引入数据库连接池
from dbutils.pooled_db import PooledDB

from common.baseline import (ATTEMPT_TIMES, DOWNSTREAM_FAULT_PROB, FAIL_THRESHOLD, GATEWAY_FORWARD_RESPONSE_TIMEOUT, GATEWAY_MAX_WORKERS, 
                             MAX_CACHE_SIZE, RDS_DB_NAME, RDS_DB_TABLE, RDS_HOST, RDS_PASSWORD, 
                             RDS_PORT, RDS_USER, RESPIRED_TIME, TIME_SLEEP, UPSTREAM_FAULT_PROB, WINDOW_SIZE )
from common.logger_config import setup_logger

LOG_PATH = "logs/distributed/gateway.log"

local_upstream_fault_prob = UPSTREAM_FAULT_PROB

class DistributedGateway:
    
    def __init__(self, gateway_host:str, gateway_port:int, server_url:str,backup_server_url:str=None):
        self.logger = setup_logger("GATEWAY", log_file = LOG_PATH, max_bytes = 50*1024*1024)
        self.app = FastAPI()
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.server_url = server_url
        self.backup_server_url = backup_server_url
        self.executor = ThreadPoolExecutor(max_workers=GATEWAY_MAX_WORKERS)
        
        # ================= 熔断与调度状态 =================
        self.is_circuit_open = False  # False = 走主节点，True = 走备用节点
        self.window_size = WINDOW_SIZE         # 统计最近 WINDOW_SIZE 次请求
        self.fail_threshold = FAIL_THRESHOLD     # 失败率阈值 (FAIL_THRESHOLD)
        self.req_window = deque(maxlen=self.window_size) # 记录请求状态: True(成功), False(失败)
        # ==================================================
        
        # 1. 优先初始化数据库连接池
        self._init_db_pool()
        # 2. 然后再初始化表结构
        self._init_db()
        # 3. 初始化时清空脏数据（你之前加的需求）
        # self._clear_cache_table()
        # if LOCAL_HOST in self.gateway_host:
        #     self._export_cache_to_csv()
        
        # 使用异步 HTTP 客户端
        self.client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        timeout=GATEWAY_FORWARD_RESPONSE_TIMEOUT)

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
            self.logger.info("AWS RDS MySQL 连接池初始化成功！")
        except Exception as e:
            self.logger.warning(f"RDS 连接池初始化失败: {e}")

    def _get_db_connection(self):
        """从连接池中获取一个可用的连接"""
        # 注意：这里拿到的 connection 在调用 .close() 时，不会真正断开，而是归还给连接池
        return self.db_pool.connection()
        
    def _init_db(self):
        """初始化 RDS 降级缓存表"""
        try:
            # 使用 with closing 安全管理数据库连接的释放
            with closing(self._get_db_connection()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f'''
                        CREATE TABLE IF NOT EXISTS {RDS_DB_TABLE} (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            task_type VARCHAR(255) NOT NULL,
                            task_id VARCHAR(20) NOT NULL,
                            response_data JSON NOT NULL,
                            ts DOUBLE NOT NULL,
                            INDEX idx_task_type (task_type)
                        )
                    ''')
                connection.commit()
            self.logger.info(f"已成功连接并挂载 AWS RDS MySQL")
        except Exception as e:
            self.logger.warning(f"RDS 数据库初始化失败，请检查网络或白名单: {e}")

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
                            self.logger.info(f"RDS 自动清理了 {delete_count} 条数据。")
                    connection.commit()
            except Exception as e:
                self.logger.warning(f"后台写入 RDS 失败: {e}")

        self.executor.submit(_do_save)
            
    def _get_from_cache(self, task_type: str, task_id: str) -> dict:
        try:
            # 【修改 2】使用 with closing 安全管理数据库连接
            with closing(self._get_db_connection()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f'SELECT response_data FROM {RDS_DB_TABLE} WHERE task_type = %s AND task_id = %s ORDER BY ts DESC LIMIT 1',
                        (task_type, task_id)
                    )
                    row = cursor.fetchone()
                
            if row:
                data = row['response_data']
                return json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            self.logger.warning(f"读取 RDS 缓存失败: {e}")
        return None

    def _clear_cache_table(self):
        """清空 RDS 中的任务缓存表"""
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute(f'TRUNCATE TABLE {RDS_DB_TABLE}')
            connection.commit()
            connection.close() # 释放回连接池
            self.logger.info(f"已成功清空 RDS 缓存表: {RDS_DB_TABLE}")
        except Exception as e:
            self.logger.warning(f"清空 RDS 缓存表失败: {e}")
                
    def _export_cache_to_csv(self, filename: str = "gateway_task_cache.csv"):
        try:
            query = f"SELECT * FROM {RDS_DB_TABLE}"
            
            with closing(self._get_db_connection()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query)
                    data = cursor.fetchall()
            
            if not data:
                self.logger.info("RDS 缓存表为空，没有数据可导出。")
                return pd.DataFrame()
            
            df = pd.DataFrame(data)
            df.to_csv(filename, index=False, encoding='utf-8-sig')
            self.logger.info(f"成功从 RDS 导出数据，总计: {len(df)} 行，已保存至: {filename}")
            return df
            
        except Exception as e:
            self.logger.warning(f"导出 CSV 失败: {e}")
            return pd.DataFrame()
                
# ========================================================

    def _makeup_response(self):
        response = {}
        status = 'failed'
        response['response_data'] = {
                "code": 500,
                "message": "Retry exhausted & No RDS cache",
            }
        response['status'] = status
        return response

    def _update_circuit_state(self, is_success: bool):
        """更新滑动窗口并检查是否需要触发熔断"""
        if self.is_circuit_open:
            return # 已经熔断，暂不统计主节点状态（除非引入半开探活机制）
            
        self.req_window.append(is_success)
        
        # 只有当窗口填满时才计算失败率
        if len(self.req_window) == self.window_size:
            fail_count = self.req_window.count(False)
            fail_rate = fail_count / self.window_size
            
            if fail_rate >= self.fail_threshold:
                global local_upstream_fault_prob
                local_upstream_fault_prob = local_upstream_fault_prob/2
                self.logger.error(f"主节点故障率达到 {fail_rate*100}%，触发熔断！后续流量将切换至备用服务器。")
                self.is_circuit_open = True

    def run(self):
        
        @self.app.get("/api/forward")
        async def forward(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            attempt = 0
            success_response = None
            params = {"request_id": request_id, "task_id": task_id, "task_type": task_type}
            
            # 动态选择目标服务器
            target_url = self.backup_server_url if self.is_circuit_open and self.backup_server_url else self.server_url
            
            while attempt < ATTEMPT_TIMES:
                attempt += 1
                try:
                    res = await self.client.get(f"{target_url}/api/process", params=params, timeout=GATEWAY_FORWARD_RESPONSE_TIMEOUT)
                    self.logger.info(f"[REQ-{request_id}] [第 {attempt} 次尝试]")
                    
                    if random.random() < local_upstream_fault_prob:
                        self.logger.error(f"[REQ-{request_id}] 模拟上游网络丢包。触发网关对 {task_type} 任务的自适应重试！")
                        raise httpx.TimeoutException("Simulated upstream loss") # 抛出 httpx 的超时异常
                    
                    success_response = res.json()
                    cached_data = await asyncio.get_event_loop().run_in_executor(
                                        self.executor, self._get_from_cache, task_type, task_id)
                    
                    # 记录成功（只在访问主节点时统计）
                    if not self.is_circuit_open:
                        self._update_circuit_state(is_success=True)
                    
                    self.logger.info("成功拿到服务端响应。")
                    break
                except Exception as e:
                    self.logger.warning(f"[REQ-{request_id}] [第 {attempt} 次尝试] 获取响应失败，原因: {e}，重试中...")
                    
                    # 每次请求异常都视为失败（可选：只在最后一次重试失败后才记录）
                    if not self.is_circuit_open:
                        self._update_circuit_state(is_success=False)
                    
                    await asyncio.sleep(RESPIRED_TIME)
            
            # 如果重试耗尽，从 AWS RDS 捞取降级缓存
            if not success_response:
                self.logger.info(f"[REQ-{request_id}] 重试完全耗尽，正从 AWS RDS 提取兜底缓存...")
                cached_data = self._get_from_cache(task_type, task_id)
                
                if cached_data:
                    self.logger.info(f"[REQ-{request_id}] 命中 RDS 降级缓存！执行兜底返回。")
                    cached_data["status"] = "success"
                    cached_data["gateway_note"] = "Gateway Cache Fallback (From AWS RDS)"
                    success_response = cached_data
                else:
                    self.logger.error(f"[REQ-{request_id}] RDS 中无 {task_type} 缓存，请求彻底失败。")
                    success_response = self._makeup_response()
                
            if random.random() < DOWNSTREAM_FAULT_PROB:
                self.logger.error(f"[REQ-{request_id}] 模拟下游网络丢包(Gateway->Client)。导致客户端超时！")
                await asyncio.sleep(TIME_SLEEP)
                
            return success_response

        # FastAPI 有一个机制：如果接口声明为 def（没有 async），它会自动把它扔进后台线程池运行，
        # 绝不会阻塞主事件循环。既然你用了同步的 requests.post，就要去掉 async。
        @self.app.post("/api/report")
        def report(payload: Dict[str, Any] = Body(...)):
            return requests.post(f"{self.server_url}/api/report_fault", json=payload).json()

        self.logger.info(f"网关启动，监听 {self.gateway_host}:{self.gateway_port} ...")
        uvicorn.run(self.app, host=self.gateway_host, port=self.gateway_port, log_level="error")
        
        