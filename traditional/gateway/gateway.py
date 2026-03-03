from fastapi import FastAPI
import requests
import time
import datetime
import os
import random

GATEWAY_HOST, GATEWAY_PORT = "127.0.0.1", 8080
SERVER_URL = "http://127.0.0.1:8000"
LOG_DIR = "logs/traditional"
LOG_FILE = os.path.join(LOG_DIR, "gateway.log")

UPSTREAM_FAULT_PROB = 0.2
DOWNSTREAM_FAULT_PROB = 0.2

class TraditionalGateway:
    def __init__(self):
        self.app = FastAPI()
        os.makedirs(LOG_DIR, exist_ok=True)

    def _get_ts(self) -> str: 
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _log(self, msg: str):
        # 传统版本：直接追加日志，不限制大小，不做清理
        log_content = f"[{self._get_ts()}] [TRAD_GATEWAY] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: 
            f.write(log_content)
        print(log_content.strip())

    def run(self):
        @self.app.get("/api/forward")
        async def forward(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            self._log(f"[REQ-{request_id}] 转发 {task_type} 请求至服务端")
            params = {"request_id": request_id, "task_id": task_id, "task_type": task_type}
            
            try:
                res = requests.get(f"{SERVER_URL}/api/process", params=params, timeout=2.0)
                result = res.json()

                if random.random() < UPSTREAM_FAULT_PROB:
                    self._log(f"[REQ-{request_id}] [FAULT] 模拟上游网络丢包。无重试，直接失败。")
                    time.sleep(6.0) 

                if random.random() < DOWNSTREAM_FAULT_PROB:
                    self._log(f"[REQ-{request_id}] [FAULT] 模拟下游网络丢包，客户端超时。")
                    time.sleep(6.0) 

                return result
            except Exception as e:
                self._log(f"[REQ-{request_id}] 转发异常：{str(e)}")
                return { "status": "failed",'response_data':'gateway timeout'}

        import uvicorn
        self._log(f"网关启动，监听 {GATEWAY_HOST}:{GATEWAY_PORT} ...")
        uvicorn.run(self.app, host=GATEWAY_HOST, port=GATEWAY_PORT, log_level="error")

if __name__ == "__main__": 
    TraditionalGateway().run()