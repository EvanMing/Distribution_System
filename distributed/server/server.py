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

from common.baseline import ACTIVE_REDIS_HOST, EMR_CLUSTER_NAME, EMR_TIMEOUT, FAULT_LEVEL, FAULT_REASON, FIREBASE_CERT_PATH, IDEMPOTENCY_EXPIRE, QUERY_INTERVAL, REDIS_PORT, S3_BUCKET, TASK_COST, TASK_CRIMINAL_CLASSIFICATION, get_ts, makeup_response
from common.logger_config import setup_logger

import boto3
from fastapi import BackgroundTasks

LOG_PATH = "logs/distributed/server.log"

alert_system_token_set: Optional[Set[str]] = set()

# Initialize Redis connection pool
redis_client = redis.Redis(
    host=ACTIVE_REDIS_HOST, 
    port=REDIS_PORT, 
    password=None, # AWS Valkey default internal connection usually doesn't have password
    db=0, 
    decode_responses=True
)

IS_REDIS_CONNECTED = False

class DistributedServer:
    
    def __init__(self,host:str ,port:int):
        self.logger = setup_logger("SERVER", log_file = LOG_PATH, max_bytes = 100*1024*1024)
        
        # Initialize EMR client
        self.emr_client = boto3.client('emr', region_name='us-east-1')
        # Cache cluster ID to avoid repeated queries
        self._cached_cluster_id = None
        
        self.app = FastAPI()
        self._init_redis()
        self.host = host
        self.port = port
        self._init_alert_system()

    def _init_redis(self):
        global IS_REDIS_CONNECTED 
        self.logger.info(f"Initializing Redis...")
        try:
            redis_client.ping()
            IS_REDIS_CONNECTED = True
            self.logger.info(f"Redis connected successfully ({ACTIVE_REDIS_HOST}:{REDIS_PORT}), idempotency guarantee enabled!")
        except redis.ConnectionError as e:
            self.logger.warning(f"Unable to connect to Redis, please confirm Redis service is started. Error: {e}")

    def _init_alert_system(self):
            try:
                # Load Service Account JSON downloaded from Firebase Console
                cred = credentials.Certificate(FIREBASE_CERT_PATH)
                firebase_admin.initialize_app(cred)
                self.logger.info("Firebase Alert System mounted.")
            except Exception as e:
                self.logger.warning(f"Firebase initialization failed: {e}")

    def _push_to_alert_system(self, task_id, task_type, task_priority:str,req_id,timestamp:str,reason:str):
        
            # Prefer getting global token set from Redis
            current_tokens = set()
            if IS_REDIS_CONNECTED:
                # SMEMBERS gets all elements in the set
                current_tokens = redis_client.smembers("alert_system:tokens")
            
            # Merge possible degraded tokens from local memory
            current_tokens.update(alert_system_token_set)

            if len(current_tokens) < 1:
                self.logger.warning("Push failed: No registered device tokens found")
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
                    ttl=3600 # Message cache for 1 hour
            )
                )

                try:
                    response = messaging.send(message)
                    self.logger.info(f"Alert pushed to Android app, MessageID: {response}")
                except Exception as e:
                    self.logger.warning(f"Push failed: {e}")
                    
                    error_msg = str(e).lower()
                    if "not found" in error_msg or "unregistered" in error_msg:
                        if IS_REDIS_CONNECTED:
                            # Remove this dead token from Redis set
                            redis_client.srem("alert_system:tokens", token)
                            self.logger.info(f"Automatically cleaned up invalid Android token from Redis: {token[:15]}...")
                        elif token in alert_system_token_set:
                            # Local memory cleanup in degraded mode
                            alert_system_token_set.remove(token)

    def _get_cluster_id(self) -> str:
        """Get EMR cluster ID (with caching)"""
        if self._cached_cluster_id:
            return self._cached_cluster_id
        
        # Query cluster ID from AWS
        try:
            # Modification: Removed MaxResults=10
            response = self.emr_client.list_clusters(
                ClusterStates=['RUNNING', 'WAITING']
            )
            for cluster in response['Clusters']:
                if cluster['Name'] == EMR_CLUSTER_NAME:
                    self._cached_cluster_id = cluster['Id']
                    self.logger.info(f"Retrieved EMR cluster ID: {self._cached_cluster_id}")
                    return self._cached_cluster_id
            
            self.logger.warning(f"Active EMR cluster with name {EMR_CLUSTER_NAME} not found")
            return None
        except Exception as e:
            self.logger.warning(f"Failed to query EMR cluster ID: {str(e)}")
            return None

    def submit_emr_spark_job(self, task_id: str):
        # Dynamically get cluster ID first
        cluster_id = self._get_cluster_id()
        if not cluster_id:
            self.logger.info(f"Task {task_id} submission failed: No valid EMR cluster ID obtained")
            return None
        
        try:
            response = self.emr_client.add_job_flow_steps(
                JobFlowId=cluster_id,  # Use actual cluster ID
                Steps=[
                    {
                        'Name': f'Spark_Big_Data_Task_{task_id}',
                        'ActionOnFailure': 'CONTINUE',  # Don't terminate cluster on failure
                        'HadoopJarStep': {
                            'Jar': 'command-runner.jar',
                            'Args': [
                                'spark-submit',
                                '--deploy-mode', 'client',
                                f'{S3_BUCKET}/Big_Data_Task.py'  # Script path on S3
                            ]
                        }
                    }
                ]
            )
            self.logger.info(f"Task submitted successfully, Step ID: {response['StepIds'][0]}")
            return response['StepIds'][0]
        
        except self.emr_client.exceptions.ValidationException as e:
            self.logger.warning(f"EMR submission failed (parameter validation error): {e}")
            return None
        except self.emr_client.exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            error_msg = e.response['Error']['Message']
            self.logger.warning(f"EMR client error ({error_code}): {error_msg}")
            return None
        except Exception as e:
            self.logger.warning(f"EMR submission failed: {e}")
            return None

    def _check_emr_status_sync(self, step_id: str) -> str:
        """Synchronous method: Call AWS SDK to query EMR status"""
        # Get cluster ID first to avoid passing None
        cluster_id = self._get_cluster_id()
        if not cluster_id:
            self.logger.info(f"Failed to query Step {step_id} status: Invalid cluster ID")
            return "FAILED"
        
        try:
            response = self.emr_client.describe_step(
                ClusterId=cluster_id,
                StepId=step_id
            )
            state = response['Step']['Status']['State']
            return state
        except self.emr_client.exceptions.InvalidStepIdException:
            self.logger.warning(f"Step ID {step_id} is invalid or does not exist")
            return "FAILED"
        except Exception as e:
            self.logger.warning(f"Failed to query EMR Step {step_id} status: {str(e)}")
            return "UNKNOWN"
        
    async def _monitor_emr_task(self, step_id: str, request_id: str, task_id: str, task_type: str):
        """Async background task: Poll EMR status and trigger Firebase upon completion (with timeout protection)"""
        self.logger.info(f"[REQ-{request_id}] Starting background monitoring for EMR task: {step_id}")
        # New: Timeout protection
        
        start_time = datetime.datetime.now()
        
        while True:
            # 1. Timeout check
            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            if elapsed > EMR_TIMEOUT:
                self.logger.warning(f"[REQ-{request_id}] EMR task {step_id} monitoring timeout ({EMR_TIMEOUT} seconds)")
                self._push_to_alert_system(
                    task_id=task_id,
                    task_type=task_type,
                    task_priority="emergency",
                    req_id=request_id,
                    timestamp=get_ts(),
                    reason=f"EMR task timeout after {EMR_TIMEOUT} seconds (step_id: {step_id})"
                )
                break
            
            # 2. Use to_thread to prevent synchronous network IO from blocking the main event loop
            state = await asyncio.to_thread(self._check_emr_status_sync, step_id)
            self.logger.info(f"[REQ-{request_id}] EMR task {step_id} current status: {state}")
            
            # 3. Check status
            if state == 'COMPLETED':
                self.logger.info(f"[REQ-{request_id}] EMR task executed successfully! Preparing to push Firebase.")
                reason = f"Spark ML Task completed successfully. Results saved to S3."
                self._push_to_alert_system(
                    task_id=task_id,
                    task_type=task_type,
                    task_priority="low",
                    req_id=request_id,
                    timestamp=get_ts(),
                    reason=reason
                )
                break # Exit polling loop
            
            elif state in ['FAILED', 'CANCELLED', 'INTERRUPTED']:
                self.logger.warning(f"[REQ-{request_id}] EMR task execution failed, status: {state}")
                self._push_to_alert_system(
                    task_id=task_id,
                    task_type=task_type,
                    task_priority="emergency",
                    req_id=request_id,
                    timestamp=get_ts(),
                    reason=f"Spark job failed on EMR with state: {state}"
                )
                break # Exit polling loop
            
            # New: Handle UNKNOWN state (retry after 60 seconds to avoid high-frequency queries)
            elif state == 'UNKNOWN':
                self.logger.warning(f"[REQ-{request_id}] EMR task status unknown, retrying after 60 seconds")
                await asyncio.sleep(60)
                continue
            
            # 4. Not completed: sleep 30 seconds before next query
            await asyncio.sleep(QUERY_INTERVAL)

    def _makeup_fault_response(self,outcome,task_priority,explaination):
        return {"status": "success", 'outcome':outcome, 'task_priority':task_priority, 'explaination':explaination}

    def run(self):
        
        @self.app.get("/api/process")
        async def process(request_id: str, background_tasks: BackgroundTasks, task_id: str = "unknown", task_type: str = "default"):
            self.logger.info(f"[REQ-{request_id}] Received request. TaskID: {task_id}, Task type: {task_type}")
            
            # ================= Core: Idempotency Check =================
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
                # Submit to EMR
                step_id = await asyncio.to_thread(self.submit_emr_spark_job, task_id)
                
                if step_id:
                    # Key step: Throw monitoring task to FastAPI background to run slowly, don't block current return
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
                # 2. If it's a new task, proceed with business processing
                # Replace synchronous blocking with asynchronous non-blocking
                await asyncio.sleep(TASK_COST)
                
                final_response = makeup_response(task_type=task_type)
                
                if final_response.get('status')=='success' and IS_REDIS_CONNECTED:
                    redis_client.set(idem_key, json.dumps(final_response), nx=True, ex=IDEMPOTENCY_EXPIRE)
                    self.logger.info(f"[REQ-{request_id}] {task_type} processing completed, result persisted to Redis.") 
            
            return final_response

        @self.app.post("/api/report_fault")
        def report_fault(payload: Dict[str, Any] = Body(...)):
            req_id = payload.get("request_id")
            task_id = payload.get("task_id", "unknown")
            task_type = payload.get("task_type", "unknown")
            timestamp = payload.get("timestamp", "unknown")
            self.logger.info(f"[REQ-{req_id}] Received fault report. Task type: {task_type} Task ID: {task_id}.")
            
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
                    # Use SADD to add token to Redis set, with built-in deduplication
                    redis_client.sadd("alert_system:tokens", token)
                    self.logger.info(f"Android device successfully registered to Redis, Token: {token[:10]}...")
                    return {"status": "success", "storage": "redis"}
                else:
                    # Degradation: If Redis is down, temporarily store in local memory
                    alert_system_token_set.add(token)
                    self.logger.warning(f"Redis not connected, Android device degraded registration to local memory, Token: {token[:10]}...")
                    return {"status": "success", "storage": "local"}
                    
            return {"status": "failed", "msg": "No token provided"}

        self.logger.info(f"Server starting, listening on {self.host}:{self.port} ...")
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="error")