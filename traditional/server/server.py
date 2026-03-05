import random

from fastapi import FastAPI
import time
import datetime
import os
from typing import Dict, Any
import uvicorn

HOST = "127.0.0.1"
PORT = 8000
LOG_DIR = "logs/traditional"
LOG_FILE = os.path.join(LOG_DIR, "server.log")

FAULT_REASON = [
    "Network issues", 
    "Server delay", 
    "API timeout", 
    "Client-side error", 
    "Server log storage issue", 
    "Log format mismatch", 
    "Permission issue", 
    "Asynchronous handling issue", 
    "Server configuration issue", 
    "Client cache issue"
]

class TraditionalServer:
    def __init__(self,host:str = HOST,port:int = PORT):
        self.app = FastAPI(title="TraditionalServer")
        self.host = host
        self.port = port
        os.makedirs(LOG_DIR, exist_ok=True)

    def _get_ts(self) -> str: 
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _log(self, msg: str):
        # 传统版本：直接追加日志，不限制大小，不做清理
        log_content = f"[{self._get_ts()}] [TRAD_SERVER] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: 
            f.write(log_content)
        print(log_content.strip())

    def run(self):
        @self.app.get("/api/process")
        async def process(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            # 模拟识别 ML 任务
            self._log(f"[REQ-{request_id}] 接收请求。TaskID: {task_id}, 任务类型: {task_type}")
            time.sleep(0.1)
            self._log(f"[REQ-{request_id}] {task_type} 处理成功，已发出响应。")
            
            return self._makeup_response()
            
        self._log(f"服务端启动，监听 {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")

    def _makeup_response(self):
        response = {}
        # 50% 概率成功，50% 概率失败
        status = 'success' if random.random() > 0.5 else 'failed'
        
        if status == 'success':
            response['response_data'] = f"success to processing"
        else:
            response['response_data'] = f"failed to processing"
            
        response['status'] = status
        
        return response