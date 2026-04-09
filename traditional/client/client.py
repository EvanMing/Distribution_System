import random
import requests, time, os
import sys

from common.baseline import DOWNSTREAM_FAULT_PROB, EXPERIMENT_RESULT_FILE_NAME, MAX_WORKERS, ML_TASK_TYPES_TRADITIONAL, REQUEST_TIMEOUT, REQUEST_TIMES, TASK_COST, UPSTREAM_FAULT_PROB, get_ts

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
        self.latencies = []                # Record latency for all successful requests
        self.total_bytes_sent = 0          # Network overhead: bytes sent
        self.total_bytes_received = 0      # Network overhead: bytes received
        self.experiment_start_time = 0     # Total experiment start time
        self.experiment_end_time = 0       # Total experiment end time
        # ====================================================
        
        for d in [RESULT_DIR]: os.makedirs(d, exist_ok=True)
   
    def _send_single_request(self, i: int, session: requests.Session):
        """Logic for processing a single request"""
        req_id = f"{i+1:03d}"
        task_type = random.choice(ML_TASK_TYPES_TRADITIONAL)   
        task_id = f"ML-{task_type[:4].upper()}-{random.randint(1, int(REQUEST_TIMES))}"
        
        params = {"request_id": req_id, "task_id": task_id, "task_type": task_type}
        
        req_size = sys.getsizeof(self.gateway_url) + sys.getsizeof(str(params))
        req_start_time = time.time() 
        
        try:
            # Use session to initiate request, without automatic retry mechanism
            res = session.get(f"{self.gateway_url}/api/forward", params=params, timeout=REQUEST_TIMEOUT)
            req_end_time = time.time() 
            res_size = len(res.content)
            latency = req_end_time - req_start_time
            
            json_res = res.json()
            response_data = json_res.get('response_data')
            
            if res.status_code == 200 and json_res.get('status') != 'failed':
                self.logger.info(f"[REQ-{req_id}] - [{task_id} - {task_type}] Successfully received response: ({response_data}) took {round(latency, 2)}s")
                return True, req_size, res_size, latency
            else:
                self.logger.info(f"[REQ-{req_id}] - [{task_id} - {task_type}] Request failed: {response_data}")
                return False, req_size, res_size, latency
                
        except requests.exceptions.Timeout:
            self.logger.error(f"[REQ-{req_id}] - [{task_id} - {task_type}] Request timeout (no response received)")
            return False, req_size, 0, 0.0
        except Exception as e:
            self.logger.error(f"[REQ-{req_id}] - [{task_id} - {task_type}] Network exception: {e}")
            return False, req_size, 0, 0.0
        
    def run(self):
        self.logger.info(f"Starting concurrent simulation of {REQUEST_TIMES} requests, Timeout limit: {REQUEST_TIMEOUT}s")
        self.experiment_start_time = time.time()
        
        # Use Session with adapter to reuse underlying TCP connections, preventing local port exhaustion under high concurrency
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
  Simulated Tasks          : [{", ".join(ML_TASK_TYPES_TRADITIONAL)}]
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
        self.logger.info(f"Experiment report generated: {RESULT_DIR}/{EXPERIMENT_RESULT_FILE_NAME}")