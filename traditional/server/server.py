from fastapi import FastAPI
import time
import os
import uvicorn

from common.baseline import TASK_COST, makeup_response
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
            # Simulate ML task processing
            self.logger.info(f"[REQ-{request_id}] Received request. TaskID: {task_id}, Task type: {task_type}")
            time.sleep(TASK_COST)
            self.logger.info(f"[REQ-{request_id}] {task_type} processed successfully, response sent.")
            
            return makeup_response(task_type=task_type)
            
        self.logger.info(f"Server starting, listening on {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")