
import datetime
import random
import socket
import os
from dotenv import load_dotenv

load_dotenv()

LOCAL_HOST_NAME = 'laptop'
LOCAL_HOST = "127.0.0.1"
ANY_HOST = '0.0.0.0'

#-----------------------------server---------------------

SERVER_PORT = 8000

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

TASK_COST = 0.2

#-----------------------------server---------------------


#-----------------------------gateway--------------------

UPSTREAM_FAULT_PROB = 0.4
DOWNSTREAM_FAULT_PROB = 0.2
TIME_SLEEP = 3.1
RESPIRED_TIME = 0.2
GATEWAY_FORWARD_RESPONSE_TIMEOUT = 2
GATEWAY_PORT =  8080
SERVER_URL = f"http://{LOCAL_HOST}:{SERVER_PORT}"

#-----------------------------gateway--------------------

#-----------------------------client---------------------

REQUEST_TIMEOUT = 3.0
CONNECT_TIMEOUT = 2.0
BACKOFF_FACTOR = 0.3
MAX_WORKERS = 5
REQUEST_TIMES = 30
RETRY_TIMES = 1
ML_TASK_TYPES = ["Data_Preprocessing", "Feature_Extraction", "Model_Training", "Model_Inference" , "Model_Deployment"]

EXPERIMENT_RESULT_FILE_NAME = 'result.txt'

FAULT_QUEUE_POLL_TIME = 2
WAIT_QUEUE_REPORT_TIME = 8

#-----------------------------client---------------------

def get_ts() -> str: 
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def makeup_response(task_type:str):
        response = {}
        # 50% 概率成功，50% 概率失败
        status = 'success' if random.random() > 0.5 else 'failed'
        
        if status == 'success':
            response['response_data'] = {
                "code": 200,
                "message": "处理成功",
                "data": {
                    "result": f"成功处理了{task_type}任务",
                    "timestamp": get_ts()
                }
            }
        else:
            response['response_data'] = {
                "code": 500,
                "message": "处理失败",
                "data": {
                    "error": "处理超时或系统错误",
                    "timestamp": get_ts()
                }
            }
        
        response['status'] = status
        return response
    
def get_host():
        print('hostname:', socket.gethostname())
        if LOCAL_HOST_NAME in socket.gethostname().lower():
            return LOCAL_HOST
        return ANY_HOST
    
    
# ================= Valkey (Redis 兼容) 配置 ================

IDEMPOTENCY_EXPIRE = 86400  # 幂等记录保留 24 小时
VALKEY_ENDPOINT = os.getenv('VALKEY_ENDPOINT', 'localhost')
REDIS_PORT = 6379

# 逻辑判断：如果在 EC2 环境则连云端，本地则连隧道映射的 localhost
def get_redis_host():
    if LOCAL_HOST_NAME in socket.gethostname().lower():
        return LOCAL_HOST
    return VALKEY_ENDPOINT

ACTIVE_REDIS_HOST = get_redis_host()

# ==========================================================    
    
    
# ================= firebase 配置 ===========================    
    
FIREBASE_CERT_PATH = os.getenv('FIREBASE_CERT_PATH', 'serviceAccountKey.json')
    
# ==========================================================   
    
    
# ================= rds 配置 ===============================  
    
MAX_CACHE_SIZE = 1000  # 网关降级库最大缓存条数
ATTEMPT_TIMES = 3

RDS_HOST = os.getenv('RDS_HOST', 'localhost')
RDS_USER = os.getenv('RDS_USER', 'root')
RDS_PASSWORD = os.getenv('RDS_PASSWORD')  # 找不到会返回 None
RDS_DB_NAME = os.getenv('RDS_DB_NAME', 'gatewaycache')
RDS_DB_TABLE = 'task_cache'
RDS_PORT = 3306 

# ==========================================================       
    
    
    