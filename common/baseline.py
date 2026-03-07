
import datetime
import random
import socket

LOCAL_HOST_NAME = 'laptop'
LOCAL_HOST = "127.0.0.1"
ANY_HOST = '0.0.0.0'

#-----------------------------server---------------------

SERVER_PORT = 8000

#-----------------------------server---------------------


#-----------------------------gateway--------------------

UPSTREAM_FAULT_PROB = 0.4
DOWNSTREAM_FAULT_PROB = 0.2
TIME_SLEEP = 3.1

GATEWAY_PORT =  8080
SERVER_URL = f"http://{LOCAL_HOST}:{SERVER_PORT}"

#-----------------------------gateway--------------------


#-----------------------------client---------------------

REQUEST_TIMEOUT = 3.0
REQUEST_TIMES = 30
ML_TASK_TYPES = ["Data_Preprocessing", "Feature_Extraction", "Model_Training", "Model_Inference" , "Model_Deployment"]

#-----------------------------client---------------------


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
    
    
    
    
    
    
    
    
    
    
    
    