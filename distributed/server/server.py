import asyncio

from fastapi import FastAPI, Body
import json
from typing import Dict, Any, Optional, Set
import redis
import random
import uvicorn

import firebase_admin
from firebase_admin import credentials, messaging

from common.baseline import ACTIVE_REDIS_HOST, FAULT_LEVEL, FAULT_REASON, FIREBASE_CERT_PATH, IDEMPOTENCY_EXPIRE, REDIS_PORT, TASK_COST, makeup_response
from common.logger_config import setup_logger

LOG_PATH = "logs/distributed/server.log"

alert_system_token_set: Optional[Set[str]] = set()

# 初始化 Redis 连接池
redis_client = redis.Redis(
    host=ACTIVE_REDIS_HOST, 
    port=REDIS_PORT, 
    password=None, # AWS Valkey 默认内网连接通常不设密码
    db=0, 
    decode_responses=True
)

IS_REDIS_CONNECTED = False

class DistributedServer:
    
    def __init__(self,host:str ,port:int):
        self.logger = setup_logger("SERVER", log_file = LOG_PATH, max_bytes = 100*1024*1024)
        self.app = FastAPI()
        self._init_redis()
        self.host = host
        self.port = port
        self._init_alert_system()

    def _init_redis(self):
        global IS_REDIS_CONNECTED 
        self.logger.info(f"init Redis...")
        try:
            redis_client.ping()
            IS_REDIS_CONNECTED = True
            self.logger.info(f"Redis 连接成功 ({ACTIVE_REDIS_HOST}:{REDIS_PORT})，幂等性保障已开启！")
        except redis.ConnectionError as e:
            self.logger.warning(f"无法连接到 Redis，请确认 Redis 服务已启动。错误信息: {e}")

    def _init_alert_system(self):
            try:
                # 加载从 Firebase 控制台下载的 Service Account JSON
                cred = credentials.Certificate(FIREBASE_CERT_PATH)
                firebase_admin.initialize_app(cred)
                self.logger.info("Firebase Alert System 已挂载。")
            except Exception as e:
                self.logger.warning(f"Firebase 初始化失败: {e}")

    def _push_to_alert_system(self, task_id, task_type, task_priority:str,req_id,timestamp:str,reason:str):
        
            if len(alert_system_token_set) < 1:
                self.logger.warning("推送失败：没有找到已注册设备 Token")
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
                    self.logger.info(f"告警已推送到 Android 应用，MessageID: {response}")
                except Exception as e:
                    self.logger.warning(f"推送失败: {e}")

    def _makeup_fault_response(self,outcome,task_priority,explaination):
        return {"status": "success", 'outcome':outcome, 'task_priority':task_priority, 'explaination':explaination}

    def run(self):
        
        @self.app.get("/api/process")
        async def process(request_id: str, task_id: str = "unknown", task_type: str = "default"):
            self.logger.info(f"[REQ-{request_id}] 接收请求。TaskID: {task_id}, 任务类型: {task_type}")
            
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
            # 将同步阻塞替换为异步非阻塞
            await asyncio.sleep(TASK_COST)
            
            final_response = makeup_response(task_type=task_type)
            
            if final_response.get('status')=='success' and IS_REDIS_CONNECTED:
                redis_client.set(idem_key, json.dumps(final_response), nx=True, ex=IDEMPOTENCY_EXPIRE)
                self.logger.info(f"[REQ-{request_id}] {task_type} 处理完毕，结果已固化至 Redis。") 
            
            return final_response

        @self.app.post("/api/report_fault")
        def report_fault(payload: Dict[str, Any] = Body(...)):
            req_id = payload.get("request_id")
            task_id = payload.get("task_id", "unknown")
            task_type = payload.get("task_type", "unknown")
            timestamp = payload.get("timestamp", "unknown")
            self.logger.info(f"[REQ-{req_id}] 收到异常上报。任务类型: {task_type} 任务ID: {task_id}。")
            
            reason = random.choice(FAULT_REASON)
            task_priority = random.choice(FAULT_LEVEL)
            
            self._push_to_alert_system(task_id = task_id, 
                            task_type = task_type,
                            task_priority = task_priority, 
                            req_id = req_id,
                            timestamp = timestamp,
                            reason = reason)
            
            if task_priority == 'low':
                return self._makeup_fault_response(outcome=0,task_priority=task_priority,explaination='resent this request again')
            else:
                return self._makeup_fault_response(outcome=1,task_priority=task_priority,explaination='Awaiting resolution')
            
        @self.app.post("/api/register_device")
        def register_device(payload: Dict[str, Any] = Body(...)):
            token = payload.get("token")
            if token:
                alert_system_token_set.add(token)
                self.logger.info(f"Android 设备已成功注册，Token: {token[:10]}...")
                return {"status": "success"}
            return {"status": "failed", "msg": "No token provided"}

        self.logger.info(f"服务端启动，监听 {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")
        
        