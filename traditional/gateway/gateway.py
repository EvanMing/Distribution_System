from fastapi import FastAPI
import requests
import time
import os
import random

from common.baseline import DOWNSTREAM_FAULT_PROB, TIME_SLEEP, UPSTREAM_FAULT_PROB, get_ts

LOG_DIR = "logs/traditional"
LOG_FILE = os.path.join(LOG_DIR, "gateway.log")

class TraditionalGateway:
    
    def __init__(self,server_url:str,gateway_host:str,gateway_port:int):
        self.app = FastAPI()
        self.gateway_host= gateway_host
        self.gateway_port = gateway_port
        self.server_url = server_url
        os.makedirs(LOG_DIR, exist_ok=True)

    def _log(self, msg: str):
        log_content = f"[{get_ts()}] [TRAD_GATEWAY] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: 
            f.write(log_content)
        print(log_content.strip())

    def run(self):
        @self.app.get("/api/forward")
        async def forward(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            self._log(f"[REQ-{request_id}] 转发 {task_type} 请求至服务端")
            params = {"request_id": request_id, "task_id": task_id, "task_type": task_type}
            
            try:
                res = requests.get(f"{self.server_url}/api/process", params=params, timeout=2.0)
                result = res.json()

                if random.random() < UPSTREAM_FAULT_PROB:
                    self._log(f"[REQ-{request_id}] [FAULT] 模拟上游网络丢包。无重试，直接失败。")
                    time.sleep(TIME_SLEEP) 

                if random.random() < DOWNSTREAM_FAULT_PROB:
                    self._log(f"[REQ-{request_id}] [FAULT] 模拟下游网络丢包，客户端超时。")
                    time.sleep(TIME_SLEEP) 

                return result
            except Exception as e:
                self._log(f"[REQ-{request_id}] 转发异常：{str(e)}")
                return { "status": "failed",'response_data':'gateway timeout'}

        import uvicorn
        self._log(f"网关启动，监听 {self.gateway_host}:{self.gateway_port} ...")
        uvicorn.run(self.app, host=self.gateway_host, port=self.gateway_port, log_level="error")
        
        