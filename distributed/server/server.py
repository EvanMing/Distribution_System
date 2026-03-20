import asyncio
import datetime

from fastapi import FastAPI, Body
import json
from typing import Dict, Any, Optional, Set
import redis
import random
import uvicorn

import firebase_admin
from firebase_admin import credentials, messaging

from common.baseline import ACTIVE_REDIS_HOST, EMR_CLUSTER_NAME, EMR_TIMEOUT, FAULT_LEVEL, FAULT_REASON, FIREBASE_CERT_PATH, IDEMPOTENCY_EXPIRE, REDIS_PORT, S3_BUCKET, TASK_COST, TASK_CRIMINAL_CLASSIFICATION, get_ts, makeup_response
from common.logger_config import setup_logger

import boto3
from fastapi import BackgroundTasks

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
        
        # 初始化 EMR 客户端
        self.emr_client = boto3.client('emr', region_name='us-east-1')
        # 缓存集群 ID，避免重复查询
        self._cached_cluster_id = None
        
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
        
            # 优先从 Redis 获取全局 Token 集合
            current_tokens = set()
            if IS_REDIS_CONNECTED:
                # SMEMBERS 获取集合中所有的元素
                current_tokens = redis_client.smembers("alert_system:tokens")
            
            # 合并本地可能存在的降级 Token
            current_tokens.update(alert_system_token_set)

            if len(current_tokens) < 1:
                self.logger.warning("推送失败：没有找到已注册设备 Token")
                return 
            
            for token in current_tokens:
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

    def _get_cluster_id(self) -> str:
        """获取 EMR 集群 ID（带缓存）"""
        if self._cached_cluster_id:
            return self._cached_cluster_id
        
        # 从 AWS 查询集群 ID
        try:
            # 修改点：删除了 MaxResults=10
            response = self.emr_client.list_clusters(
                ClusterStates=['RUNNING', 'WAITING']
            )
            for cluster in response['Clusters']:
                if cluster['Name'] == EMR_CLUSTER_NAME:
                    self._cached_cluster_id = cluster['Id']
                    self.logger.info(f"获取到 EMR 集群 ID: {self._cached_cluster_id}")
                    return self._cached_cluster_id
            
            self.logger.warning(f"未找到名称为 {EMR_CLUSTER_NAME} 的活跃 EMR 集群")
            return None
        except Exception as e:
            self.logger.warning(f"查询 EMR 集群 ID 失败: {str(e)}")
            return None

    def submit_emr_spark_job(self, task_id: str):
        # 先动态获取集群 ID
        cluster_id = self._get_cluster_id()
        if not cluster_id:
            self.logger.info(f"任务 {task_id} 提交失败：未获取到有效的 EMR 集群 ID")
            return None
        
        try:
            response = self.emr_client.add_job_flow_steps(
                JobFlowId=cluster_id,  # 使用真实的集群 ID
                Steps=[
                    {
                        'Name': f'Spark_Crime_Classification_{task_id}',
                        'ActionOnFailure': 'CONTINUE',  # 失败不关停集群
                        'HadoopJarStep': {
                            'Jar': 'command-runner.jar',
                            'Args': [
                                'spark-submit',
                                '--deploy-mode', 'client',
                                f'{S3_BUCKET}/crime_classification.py'  # S3 上的脚本路径
                            ]
                        }
                    }
                ]
            )
            self.logger.info(f"任务提交成功，Step ID: {response['StepIds'][0]}")
            return response['StepIds'][0]
        
        except self.emr_client.exceptions.ValidationException as e:
            self.logger.warning(f"EMR 提交失败（参数验证错误）: {e}")
            return None
        except self.emr_client.exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            error_msg = e.response['Error']['Message']
            self.logger.warning(f"EMR 客户端错误 ({error_code}): {error_msg}")
            return None
        except Exception as e:
            self.logger.warning(f"EMR 提交失败: {e}")
            return None

    def _check_emr_status_sync(self, step_id: str) -> str:
        """同步方法：调用 AWS SDK 查询 EMR 状态"""
        # 先获取集群 ID，避免传入 None
        cluster_id = self._get_cluster_id()
        if not cluster_id:
            self.logger.info(f"查询 Step {step_id} 状态失败：集群 ID 无效")
            return "FAILED"
        
        try:
            response = self.emr_client.describe_step(
                ClusterId=cluster_id,
                StepId=step_id
            )
            state = response['Step']['Status']['State']
            return state
        except self.emr_client.exceptions.InvalidStepIdException:
            self.logger.warning(f"Step ID {step_id} 无效或不存在")
            return "FAILED"
        except Exception as e:
            self.logger.warning(f"查询 EMR Step {step_id} 状态失败: {str(e)}")
            return "UNKNOWN"
        
    async def _monitor_emr_task(self, step_id: str, request_id: str, task_id: str, task_type: str):
        """异步后台任务：轮询 EMR 状态并在完成后触发 Firebase（增加超时保护）"""
        self.logger.info(f"[REQ-{request_id}] 开始后台监控 EMR 任务: {step_id}")
        # 新增：超时保护（建议设为2小时，可根据业务调整）
        
        start_time = datetime.datetime.now()
        
        while True:
            # 1. 超时检查（优先退出，避免无限循环）
            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            if elapsed > EMR_TIMEOUT:
                self.logger.warning(f"[REQ-{request_id}] EMR 任务 {step_id} 监控超时（{EMR_TIMEOUT}秒）")
                self._push_to_alert_system(
                    task_id=task_id,
                    task_type=task_type,
                    task_priority="emergency",
                    req_id=request_id,
                    timestamp=get_ts(),
                    reason=f"EMR task timeout after {EMR_TIMEOUT} seconds (step_id: {step_id})"
                )
                break
            
            # 2. 使用 to_thread 防止同步的网络 IO 阻塞主事件循环
            state = await asyncio.to_thread(self._check_emr_status_sync, step_id)
            self.logger.info(f"[REQ-{request_id}] EMR 任务 {step_id} 当前状态: {state}")
            
            # 3. 判断状态
            if state == 'COMPLETED':
                self.logger.info(f"[REQ-{request_id}] EMR 任务执行成功！准备推送 Firebase。")
                reason = f"Spark ML Task completed successfully. Results saved to S3."
                self._push_to_alert_system(
                    task_id=task_id,
                    task_type=task_type,
                    task_priority="high",
                    req_id=request_id,
                    timestamp=get_ts(),
                    reason=reason
                )
                break # 退出轮询
            
            elif state in ['FAILED', 'CANCELLED', 'INTERRUPTED']:
                self.logger.warning(f"[REQ-{request_id}] EMR 任务执行失败，状态: {state}")
                self._push_to_alert_system(
                    task_id=task_id,
                    task_type=task_type,
                    task_priority="emergency",
                    req_id=request_id,
                    timestamp=get_ts(),
                    reason=f"Spark job failed on EMR with state: {state}"
                )
                break # 退出轮询
            
            # 新增：处理 UNKNOWN 状态（延迟60秒重试，避免高频查询）
            elif state == 'UNKNOWN':
                self.logger.warning(f"[REQ-{request_id}] EMR 任务状态未知，60秒后重试")
                await asyncio.sleep(60)
                continue
            
            # 4. 未完成：睡 30 秒再查
            await asyncio.sleep(30)

    def _makeup_fault_response(self,outcome,task_priority,explaination):
        return {"status": "success", 'outcome':outcome, 'task_priority':task_priority, 'explaination':explaination}

    def run(self):
        
        @self.app.get("/api/process")
        async def process(request_id: str, background_tasks: BackgroundTasks, task_id: str = "unknown", task_type: str = "default"):
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
            
            if task_type == TASK_CRIMINAL_CLASSIFICATION:
                # 提交到 EMR
                step_id = await asyncio.to_thread(self.submit_emr_spark_job, task_id)
                
                if step_id:
                    # 关键一步：把监控任务扔给 FastAPI 的后台去慢慢跑，不要卡住当前的 return
                    background_tasks.add_task(
                        self._monitor_emr_task, 
                        step_id=step_id, 
                        request_id=request_id, 
                        task_id=task_id, 
                        task_type=task_type
                    )
                    
                    final_response = {
                        "status": "success", 
                        "message": "Task submitted to EMR. You will be notified via Firebase upon completion.",
                        "emr_step_id": step_id
                    }
                else:
                    final_response = {"status": "failed", "message": "Failed to submit EMR step"}
            else:
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
                if IS_REDIS_CONNECTED:
                    # 使用 SADD 将 token 加入 Redis 集合，自带去重功能
                    redis_client.sadd("alert_system:tokens", token)
                    self.logger.info(f"Android 设备已成功注册至 Redis，Token: {token[:10]}...")
                    return {"status": "success", "storage": "redis"}
                else:
                    # 降级：如果 Redis 挂了，暂时存在本地内存
                    alert_system_token_set.add(token)
                    self.logger.warning(f"Redis 未连接，Android 设备降级注册至本地内存，Token: {token[:10]}...")
                    return {"status": "success", "storage": "local"}
                    
            return {"status": "failed", "msg": "No token provided"}

        self.logger.info(f"服务端启动，监听 {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")
        
        