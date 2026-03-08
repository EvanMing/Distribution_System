from fastapi import FastAPI
import requests
import time
import random
import uvicorn

from common.baseline import DOWNSTREAM_FAULT_PROB, TIME_SLEEP, UPSTREAM_FAULT_PROB
from common.logger_config import setup_logger

LOG_PATH = "logs/traditional/gateway.log"

class TraditionalGateway:
    
    def __init__(self,server_url:str,gateway_host:str,gateway_port:int):
        self.logger = setup_logger("GATEWAY", log_file=LOG_PATH, max_bytes=50*1024*1024)
        self.app = FastAPI()
        self.gateway_host= gateway_host
        self.gateway_port = gateway_port
        self.server_url = server_url

    def run(self):
        
        @self.app.get("/api/forward")
        def forward(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            self.logger.info(f"[REQ-{request_id}] 转发 {task_type} 请求至服务端")
            params = {"request_id": request_id, "task_id": task_id, "task_type": task_type}
            
            try:
                res = requests.get(f"{self.server_url}/api/process", params=params, timeout=2.0)
                result = res.json()

                if random.random() < UPSTREAM_FAULT_PROB:
                    self.logger.error(f"[REQ-{request_id}] [FAULT] 模拟上游网络丢包。无重试，直接失败。")
                    time.sleep(TIME_SLEEP) 

                if random.random() < DOWNSTREAM_FAULT_PROB:
                    self.logger.error(f"[REQ-{request_id}] [FAULT] 模拟下游网络丢包，客户端超时。")
                    time.sleep(TIME_SLEEP) 

                return result
            except Exception as e:
                self.logger.warning(f"[REQ-{request_id}] 转发异常：{str(e)}")
                return { "status": "failed",'response_data':'gateway timeout'}

        self.logger.info(f"网关启动，监听 {self.gateway_host}:{self.gateway_port} ...")
        uvicorn.run(self.app, host=self.gateway_host, port=self.gateway_port, log_level="error")
        
        