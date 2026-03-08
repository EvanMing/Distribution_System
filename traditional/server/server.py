from fastapi import FastAPI
import time
import os
import uvicorn

from common.baseline import  TASK_COST, makeup_response
from common.logger_config import setup_logger

LOG_PATH = "logs/traditional/server.log"

class TraditionalServer:
    
    def __init__(self,host:str , port:int ):
        self.logger = setup_logger("SERVER", log_file=LOG_PATH, max_bytes=100*1024*1024)
        self.app = FastAPI(title="TraditionalServer")
        self.host = host
        self.port = port

    def run(self):
        
        @self.app.get("/api/process")
        def process(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            # 模拟识别 ML 任务
            self.logger.info(f"[REQ-{request_id}] 接收请求。TaskID: {task_id}, 任务类型: {task_type}")
            time.sleep(TASK_COST)
            self.logger.info(f"[REQ-{request_id}] {task_type} 处理成功，已发出响应。")
            
            return makeup_response(task_type=task_type)
            
        self.logger.info(f"服务端启动，监听 {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")
        
        