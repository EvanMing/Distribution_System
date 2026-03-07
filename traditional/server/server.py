from fastapi import FastAPI
import time
import os
import uvicorn

from common.baseline import  get_ts, makeup_response

LOG_DIR = "logs/traditional"
LOG_FILE = os.path.join(LOG_DIR, "server.log")

class TraditionalServer:
    
    def __init__(self,host:str , port:int ):
        self.app = FastAPI(title="TraditionalServer")
        self.host = host
        self.port = port
        os.makedirs(LOG_DIR, exist_ok=True)

    def _log(self, msg: str):
        # 传统版本：直接追加日志，不限制大小，不做清理
        log_content = f"[{get_ts()}] [TRAD_SERVER] {msg}\n"
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
            
            return makeup_response(task_type=task_type)
            
        self._log(f"服务端启动，监听 {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")
        
        