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

# Import database connection pool
from dbutils.pooled_db import PooledDB

from common.baseline import (ATTEMPT_TIMES, DOWNSTREAM_FAULT_PROB, FAIL_THRESHOLD, GATEWAY_FORWARD_RESPONSE_TIMEOUT, GATEWAY_MAX_WORKERS, 
                             MAX_CACHE_SIZE, RDS_DB_NAME, RDS_DB_TABLE, RDS_HOST, RDS_PASSWORD, 
                             RDS_PORT, RDS_USER, RESPIRED_TIME, TASK_CRIMINAL_CLASSIFICATION, TIME_SLEEP, UPSTREAM_FAULT_PROB, WINDOW_SIZE )
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
        
        # ================= Circuit Breaking and Scheduling State =================
        self.is_circuit_open = False  # False = use primary node, True = use backup node
        self.window_size = WINDOW_SIZE         # Count recent WINDOW_SIZE requests
        self.fail_threshold = FAIL_THRESHOLD     # Failure rate threshold (FAIL_THRESHOLD)
        self.req_window = deque(maxlen=self.window_size) # Record request status: True(success), False(failure)
        # ==================================================
        
        # 1. Initialize database connection pool first
        self._init_db_pool()
        # 2. Then initialize table structure
        self._init_db()
        # 3. Clear dirty data during initialization (your previously added requirement)
        # self._clear_cache_table()
        # if LOCAL_HOST in self.gateway_host:
        #     self._export_cache_to_csv()
        
        # Use async HTTP client
        self.client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        timeout=GATEWAY_FORWARD_RESPONSE_TIMEOUT)

# ================= AWS RDS (MySQL) Operation Optimization =================

    def _init_db_pool(self):
        """Initialize PyMySQL connection pool (supports multi-threaded high concurrency)"""
        try:
            self.db_pool = PooledDB(
                creator=pymysql,            # Database module to use
                maxconnections=10,          # Maximum number of connections allowed in the pool
                mincached=3,                # Number of idle connections pre-created in the pool during initialization
                maxcached=10,               # Maximum number of idle connections in the pool
                maxshared=0,                # Number of shared connections (not applicable for pymysql, set to 0)
                blocking=True,              # Whether to block and wait when no connections are available in the pool
                maxusage=None,              # Maximum number of times a single connection can be reused (None means unlimited)
                ping=0,                     # 1 = ping each time when retrieving from pool to check if connection is valid, preventing MySQL wait_timeout disconnection; 0 = no connection health check
                host=RDS_HOST,
                user=RDS_USER,
                password=RDS_PASSWORD,
                database=RDS_DB_NAME,
                port=RDS_PORT,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5
            )
            self.logger.info("AWS RDS MySQL connection pool initialized successfully!")
        except Exception as e:
            self.logger.warning(f"RDS connection pool initialization failed: {e}")

    def _get_db_connection(self):
        """Get an available connection from the connection pool"""
        # Note: When calling .close() on the connection obtained here, it will not actually disconnect, but return to the connection pool
        return self.db_pool.connection()
        
    def _init_db(self):
        """Initialize RDS degradation cache table"""
        try:
            # Use 'with closing' to safely manage database connection release
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
            self.logger.info(f"Successfully connected to and mounted AWS RDS MySQL")
        except Exception as e:
            self.logger.warning(f"RDS database initialization failed, please check network or whitelist: {e}")

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
                        
                        # Capacity control
                        cursor.execute(f'SELECT COUNT(*) as count FROM {RDS_DB_TABLE}')
                        count = cursor.fetchone()['count']
                        if count > MAX_CACHE_SIZE:
                            delete_count = int(MAX_CACHE_SIZE * 0.2)
                            cursor.execute(
                                f'DELETE FROM {RDS_DB_TABLE} ORDER BY ts ASC LIMIT %s',
                                (delete_count,)
                            )
                            self.logger.info(f"RDS automatically cleaned up {delete_count} records.")
                    connection.commit()
            except Exception as e:
                self.logger.warning(f"Background write to RDS failed: {e}")

        self.executor.submit(_do_save)
            
    def _get_from_cache(self, task_type: str, task_id: str) -> dict:
        try:
            # [Modification 2] Use 'with closing' to safely manage database connection
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
            self.logger.warning(f"Failed to read RDS cache: {e}")
        return None

    def _clear_cache_table(self):
        """Clear the task cache table in RDS"""
        try:
            connection = self._get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute(f'TRUNCATE TABLE {RDS_DB_TABLE}')
            connection.commit()
            connection.close() # Release back to connection pool
            self.logger.info(f"Successfully cleared RDS cache table: {RDS_DB_TABLE}")
        except Exception as e:
            self.logger.warning(f"Failed to clear RDS cache table: {e}")
                
    def _export_cache_to_csv(self, filename: str = "gateway_task_cache.csv"):
        try:
            query = f"SELECT * FROM {RDS_DB_TABLE}"
            
            with closing(self._get_db_connection()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query)
                    data = cursor.fetchall()
            
            if not data:
                self.logger.info("RDS cache table is empty, no data to export.")
                return pd.DataFrame()
            
            df = pd.DataFrame(data)
            df.to_csv(filename, index=False, encoding='utf-8-sig')
            self.logger.info(f"Successfully exported data from RDS, total: {len(df)} rows, saved to: {filename}")
            return df
            
        except Exception as e:
            self.logger.warning(f"Failed to export CSV: {e}")
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
        """Update sliding window and check if circuit breaking needs to be triggered"""
        if self.is_circuit_open:
            return # Already circuit broken, temporarily not counting primary node status (unless half-open probe mechanism is introduced)
            
        self.req_window.append(is_success)
        
        # Only calculate failure rate when the window is full
        if len(self.req_window) == self.window_size:
            fail_count = self.req_window.count(False)
            fail_rate = fail_count / self.window_size
            
            if fail_rate >= self.fail_threshold:
                global local_upstream_fault_prob
                local_upstream_fault_prob = local_upstream_fault_prob/2
                self.logger.error(f"Primary node failure rate reached {fail_rate*100}%, triggering circuit breaker! Subsequent traffic will be switched to backup server.")
                self.is_circuit_open = True

    def run(self):
        
        @self.app.get("/api/forward")
        async def forward(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            attempt = 0
            success_response = None
            params = {"request_id": request_id, "task_id": task_id, "task_type": task_type}
            
            # Dynamically select target server
            target_url = self.backup_server_url if self.is_circuit_open and self.backup_server_url else self.server_url
            
            while attempt < ATTEMPT_TIMES:
                attempt += 1
                try:
                    res = await self.client.get(f"{target_url}/api/process", params=params, timeout=GATEWAY_FORWARD_RESPONSE_TIMEOUT)
                    self.logger.info(f"[REQ-{request_id}] [Attempt {attempt}]")
                    
                    if random.random() < local_upstream_fault_prob and task_type != TASK_CRIMINAL_CLASSIFICATION:
                        self.logger.error(f"[REQ-{request_id}] Simulating upstream network packet loss. Triggering gateway adaptive retry for {task_type} task!")
                        raise httpx.TimeoutException("Simulated upstream loss") # Raise httpx timeout exception
                    
                    success_response = res.json()
                    cached_data = await asyncio.get_event_loop().run_in_executor(
                                        self.executor, self._get_from_cache, task_type, task_id)
                    
                    # Record success (only count when accessing primary node)
                    if not self.is_circuit_open:
                        self._update_circuit_state(is_success=True)
                    
                    self.logger.info("Successfully received server response.")
                    break
                except Exception as e:
                    self.logger.warning(f"[REQ-{request_id}] [Attempt {attempt}] Failed to get response, reason: {e}, retrying...")
                    
                    # Treat each request exception as failure (optional: only record after the last retry fails)
                    if not self.is_circuit_open:
                        self._update_circuit_state(is_success=False)
                    
                    await asyncio.sleep(RESPIRED_TIME)
            
            # If retries exhausted, retrieve degradation cache from AWS RDS
            if not success_response:
                self.logger.info(f"[REQ-{request_id}] Retries completely exhausted, retrieving fallback cache from AWS RDS...")
                cached_data = self._get_from_cache(task_type, task_id)
                
                if cached_data:
                    self.logger.info(f"[REQ-{request_id}] Hit RDS degradation cache! Executing fallback response.")
                    cached_data["status"] = "success"
                    cached_data["gateway_note"] = "Gateway Cache Fallback (From AWS RDS)"
                    success_response = cached_data
                else:
                    self.logger.error(f"[REQ-{request_id}] No {task_type} cache in RDS, request completely failed.")
                    success_response = self._makeup_response()
                
            if random.random() < DOWNSTREAM_FAULT_PROB and task_type != TASK_CRIMINAL_CLASSIFICATION:
                self.logger.error(f"[REQ-{request_id}] Simulating downstream network packet loss (Gateway->Client). Causing client timeout!")
                await asyncio.sleep(TIME_SLEEP)
                
            return success_response

        # FastAPI mechanism: If an endpoint is declared as def (without async), it will automatically run it in a background thread pool,
        # never blocking the main event loop. Since you're using synchronous requests.post, remove async.
        @self.app.post("/api/report")
        def report(payload: Dict[str, Any] = Body(...)):
            # Dynamically select target based on circuit breaking state
            target_url = getattr(self, 'backup_server_url', self.server_url) if getattr(self, 'is_circuit_open', False) else self.server_url
            return requests.post(f"{target_url}/api/report_fault", json=payload).json()

        @self.app.post("/api/register_device")
        async def register_device(payload: Dict[str, Any] = Body(...)):
            # Dynamically select target server: if primary node is circuit broken, forward to backup node
            # (using getattr for compatibility with your possibly incomplete circuit_open variable)
            target_url = getattr(self, 'backup_server_url', self.server_url) if getattr(self, 'is_circuit_open', False) else self.server_url
            
            forward_url = f"{target_url}/api/register_device"
            self.logger.info(f"Gateway received Android device registration request, forwarding to: {forward_url}")
            
            try:
                # Reuse gateway's existing async httpx client for forwarding
                res = await self.client.post(forward_url, json=payload, timeout=GATEWAY_FORWARD_RESPONSE_TIMEOUT)
                res.raise_for_status() # Raise exception if HTTP status code is not 2xx
                return res.json()
            except Exception as e:
                self.logger.warning(f"Failed to forward device registration request: {e}")
                return {"status": "failed", "msg": f"Gateway forward failed: {e}"}
        
        self.logger.info(f"Gateway starting, listening on {self.gateway_host}:{self.gateway_port} ...")
        uvicorn.run(self.app, host=self.gateway_host, port=self.gateway_port, log_level="error")