import random
import requests, time, os
import sys

from common.baseline import DOWNSTREAM_FAULT_PROB, EXPERIMENT_RESULT_FILE_NAME, MAX_WORKERS, ML_TASK_TYPES, REQUEST_TIMEOUT, REQUEST_TIMES, TASK_COST, UPSTREAM_FAULT_PROB, get_ts

from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter

from common.logger_config import setup_logger

RESULT_DIR = "experiment_results/traditional"
LOG_PATH = 'logs/traditional/client.log'

class TraditionalClient:
    
    def __init__(self,gateway_host:str, gateway_port:int ):
        self.logger = setup_logger("CLIENT", log_file=LOG_PATH, max_bytes=20*1024*1024)
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.gateway_url = f'http://{self.gateway_host}:{self.gateway_port}'
        
        self.success, self.failed = 0, 0
        self.latencies = []                # 记录所有成功请求的延迟
        self.total_bytes_sent = 0          # 网络开销：发送字节数
        self.total_bytes_received = 0      # 网络开销：接收字节数
        self.experiment_start_time = 0     # 实验总开始时间
        self.experiment_end_time = 0       # 实验总结束时间
        # ====================================================
        
        for d in [RESULT_DIR]: os.makedirs(d, exist_ok=True)
   
    def _send_single_request(self, i: int, session: requests.Session):
        """处理单次请求的逻辑"""
        req_id = f"{i+1:03d}"
        task_type = random.choice(ML_TASK_TYPES)   
        task_id = f"ML-{task_type[:4].upper()}-{random.randint(1, int(REQUEST_TIMES))}"
        
        params = {"request_id": req_id, "task_id": task_id, "task_type": task_type}
        
        req_size = sys.getsizeof(self.gateway_url) + sys.getsizeof(str(params))
        req_start_time = time.time() 
        
        try:
            # 使用 session 发起请求，且不含自动重试机制
            res = session.get(f"{self.gateway_url}/api/forward", params=params, timeout=REQUEST_TIMEOUT)
            req_end_time = time.time() 
            res_size = len(res.content)
            latency = req_end_time - req_start_time
            
            json_res = res.json()
            response_data = json_res.get('response_data')
            
            if res.status_code == 200 and json_res.get('status') != 'failed':
                self.logger.info(f"[REQ-{req_id}] - [{task_id} - {task_type}] 成功收到响应: ({response_data}) 耗时 {round(latency, 2)}s")
                return True, req_size, res_size, latency
            else:
                self.logger.info(f"[REQ-{req_id}] - [{task_id} - {task_type}] 请求失败: {response_data}")
                return False, req_size, res_size, latency
                
        except requests.exceptions.Timeout:
            self.logger.error(f"[REQ-{req_id}] - [{task_id} - {task_type}] 请求超时（未收到响应）")
            return False, req_size, 0, 0.0
        except Exception as e:
            self.logger.error(f"[REQ-{req_id}] - [{task_id} - {task_type}] 网络异常: {e}")
            return False, req_size, 0, 0.0
        
    def run(self):
        self.logger.info(f"开始并发模拟 {REQUEST_TIMES} 个请求， Timeout限制为: {REQUEST_TIMEOUT}s")
        self.experiment_start_time = time.time()
        
        # 使用 Session 并挂载适配器，复用底层 TCP 连接，防止高并发时耗尽本地端口
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self._send_single_request, i, session) for i in range(REQUEST_TIMES)]
            
            for future in as_completed(futures):
                is_success, req_size, res_size, latency = future.result()
                
                self.total_bytes_sent += req_size
                self.total_bytes_received += res_size
                
                if is_success:
                    self.success += 1
                    self.latencies.append(latency)
                else:
                    self.failed += 1
            
        self.experiment_end_time = time.time()
        self._save_result()

    def _save_result(self):
        response_rate = round(self.success / REQUEST_TIMES * 100, 2) if REQUEST_TIMES > 0 else 0
        total_exec_time = round(self.experiment_end_time - self.experiment_start_time, 3)
        avg_latency = round(sum(self.latencies) / len(self.latencies), 3) if self.latencies else 0
        total_overhead_kb = round((self.total_bytes_sent + self.total_bytes_received) / 1024, 2)
        
        report = f"""==================================================
DISTRIBUTED ML SYSTEM - TRADITIONAL MODE REPORT
Generated: {get_ts()}
==================================================
[SYSTEM CONFIGURATION]
  Gateway Address          : {self.gateway_url}
  Client Timeout           : {REQUEST_TIMEOUT}s
  Total Requests           : {REQUEST_TIMES}
  Simulated Tasks          : [{", ".join(ML_TASK_TYPES)}]
  Mimic Fault Config       : Upstream {UPSTREAM_FAULT_PROB*100}%) + Downstream ({DOWNSTREAM_FAULT_PROB*100}%)
  Server Handler Per Task  : {TASK_COST}s
==================================================
[RELIABILITY METRICS]
  Total Sent      : {REQUEST_TIMES}
  Direct Success  : {self.success}
  Failed / Dropped: {self.failed}
  Response Rate   : {response_rate}%
==================================================
[PERFORMANCE & OVERHEAD METRICS]
  Total Exec Time : {total_exec_time} seconds
  Average Latency : {avg_latency} seconds/req
  Network Overhead: {total_overhead_kb} KB (Approx. Sent + Received)
==================================================
"""
        with open(f"{RESULT_DIR}/{EXPERIMENT_RESULT_FILE_NAME}", "w", encoding="utf-8") as f: f.write(report)
        self.logger.info(f"实验报告已生成: {RESULT_DIR}/{EXPERIMENT_RESULT_FILE_NAME}")

