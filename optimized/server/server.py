from fastapi import FastAPI, Body
import time, datetime, os, json
from typing import Dict, Any, Optional, Set
import redis
import random
import uvicorn

import firebase_admin
from firebase_admin import credentials, messaging

import os
from dotenv import load_dotenv

load_dotenv()

HOST, PORT = "127.0.0.1", 8000
LOG_DIR = "logs/optimized"
LOG_FILE = os.path.join(LOG_DIR, "server.log")
MAX_LOG_SIZE = 100 * 1024 * 1024

alert_system_token_set: Optional[Set[str]] = set()

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

FAULT_LEVEL=[
    'emergency','high','medium','low'
]

# ================= Valkey (Redis 兼容) 配置 =================

IDEMPOTENCY_EXPIRE = 86400  # 幂等记录保留 24 小时
VALKEY_ENDPOINT = os.getenv('VALKEY_ENDPOINT', 'localhost')
# VALKEY_ENDPOINT = 'com6102-group6-server-cache.gfxyxq.ng.0001.use1.cache.amazonaws.com'
REDIS_PORT = 6379

# 逻辑判断：如果在 EC2 环境则连云端，本地则连隧道映射的 localhost
def get_redis_host():
    import socket
    
    if "laptop" in socket.gethostname().lower():
        return "127.0.0.1"
    return VALKEY_ENDPOINT

ACTIVE_REDIS_HOST = get_redis_host()

# 初始化 Redis 连接池
redis_client = redis.Redis(
    host=ACTIVE_REDIS_HOST, 
    port=REDIS_PORT, 
    password=None, # AWS Valkey 默认内网连接通常不设密码
    db=0, 
    decode_responses=True
)

IS_REDIS_CONNECTED = False
# ==========================================================

FIREBASE_CERT_PATH = os.getenv('FIREBASE_CERT_PATH', 'serviceAccountKey.json')

class OptimizedServer:
    def __init__(self,host:str = HOST,port:int=PORT):
        self.app = FastAPI()
        os.makedirs(LOG_DIR, exist_ok=True)
        self._init_redis()
        self.host = host
        self.port = port
        self._init_alert_system()

    def _init_redis(self):
        self._log("INIT", f"init Redis...")
        try:
            redis_client.ping()
            IS_REDIS_CONNECTED = True
            self._log("INIT", f"Redis 连接成功 ({ACTIVE_REDIS_HOST}:{REDIS_PORT})，幂等性保障已开启！")
        except redis.ConnectionError as e:
            self._log("WARNING", f"无法连接到 Redis，请确认 Redis 服务已启动。错误信息: {e}")

    def _init_alert_system(self):
            try:
                # 加载从 Firebase 控制台下载的 Service Account JSON
                cred = credentials.Certificate(FIREBASE_CERT_PATH)
                firebase_admin.initialize_app(cred)
                self._log("INIT", "Firebase Alert System 已挂载。")
            except Exception as e:
                self._log("WARNING", f"Firebase 初始化失败: {e}")

    def _push_to_alert_system(self, task_id, task_type, task_priority:str,req_id,timestamp:str,reason:str):
        
            if len(alert_system_token_set) < 1:
                self._log("WARNING", "推送失败：没有找到已注册设备 Token")
                return 
            
            for token in alert_system_token_set:
                if not token:
                    continue
            
                message = messaging.Message(
                    data={
                        "request_id": str(req_id),
                        "task_id": task_id,
                        'task_type':task_type,
                        "timestamp": timestamp,
                        'reason':reason,
                        'task_priority':task_priority
                    },
                    token=token,
                    android=messaging.AndroidConfig(
                    priority='high',
                    ttl=3600 # 消息缓存 1 小时
            )
                )

                try:
                    response = messaging.send(message)
                    self._log("INFO", f"告警已推送到 Android 应用，MessageID: {response}")
                except Exception as e:
                    self._log("WARNING", f"推送失败: {e}")

    def _get_ts(self) -> str: 
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _clean_log(self):
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_SIZE:
            with open(LOG_FILE, "r", encoding="utf-8") as f: lines = f.readlines()
            reserve = int(len(lines) * 2 / 3)
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{self._get_ts()}] [OPT_SERVER] [LOG_CLEAN] 日志达100M上限，清理最早1/3\n")
                f.writelines(lines[-reserve:] if reserve > 0 else [])

    def _log(self, level: str, msg: str):
        self._clean_log()
        log_content = f"[{self._get_ts()}] [OPT_SERVER] [{level}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(log_content)
        print(log_content.strip())

    def _makeup_response(self,task_type:str):
        response = {}
        # 50% 概率成功，50% 概率失败
        status = 'success' if random.random() > 0.5 else 'failed'
        
        if status == 'success':
            response['response_data'] = {
                "code": 200,
                "message": "处理成功",
                "data": {
                    "result": f"成功处理了{task_type}任务",
                    "timestamp": self._get_ts()
                }
            }
        else:
            response['response_data'] = {
                "code": 500,
                "message": "处理失败",
                "data": {
                    "error": "处理超时或系统错误",
                    "timestamp": self._get_ts()
                }
            }
        
        response['status'] = status
        return response

    def run(self):
        @self.app.get("/api/process")
        async def process(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            self._log("INFO", f"[REQ-{request_id}] 接收请求。TaskID: {task_id}, 任务类型: {task_type}")
            
            # ================= 核心：幂等性校验 =================
            idem_key = f"idem:task:{task_id}"
            
            cached_data = None
            
            if IS_REDIS_CONNECTED:
                cached_data = redis_client.get(idem_key)

            if cached_data:
                cached_response = json.loads(cached_data)
                cached_response["server_note"] = "Idempotent Cache Hit - Returned Cached Result"
                return cached_response
            
            # =================================================

            # 2. 如果是新任务，往下执行业务处理
            time.sleep(0.1)
            
            final_response = self._makeup_response(task_type=task_type)
            
            if final_response.get('status')=='success' and IS_REDIS_CONNECTED:
                redis_client.set(idem_key, json.dumps(final_response), nx=True, ex=IDEMPOTENCY_EXPIRE)
                self._log("SUCCESS", f"[REQ-{request_id}] {task_type} 处理完毕，结果已固化至 Redis。") 
            
            return final_response

        @self.app.post("/api/report_fault")
        async def report_fault(payload: Dict[str, Any] = Body(...)):
            req_id = payload.get("request_id")
            task_id = payload.get("task_id", "unknown")
            task_type = payload.get("task_type", "unknown")
            timestamp = payload.get("timestamp", "unknown")
            self._log("REPORT", f"[REQ-{req_id}] 收到异常上报。任务类型: {task_type} 任务ID: {task_id}。")
            
            reason = random.choice(FAULT_REASON)
            task_priority = random.choice(FAULT_LEVEL)
            
            self._push_to_alert_system(task_id = task_id, 
                            task_type = task_type,
                            task_priority = task_priority, 
                            req_id = req_id,
                            timestamp = timestamp,
                            reason = reason)
            
            if task_priority == 'low':
                return {"status": "success", 'outcome':0, 'explaination':'resent this request again'}
            else:
                return {"status": "success", 'outcome':1, 'explaination':'Awaiting resolution'}
            
        @self.app.post("/api/register_device")
        async def register_device(payload: Dict[str, Any] = Body(...)):
            token = payload.get("token")
            if token:
                alert_system_token_set.add(token)
                self._log("INFO", f"Android 设备已成功注册，Token: {token[:10]}...")
                return {"status": "ok"}
            return {"status": "error", "msg": "No token provided"}

        self._log("START", f"服务端启动，监听 {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")