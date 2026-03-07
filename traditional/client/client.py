import random
import requests, time, os
import sys

from common.baseline import DOWNSTREAM_FAULT_PROB, ML_TASK_TYPES, REQUEST_TIMEOUT, REQUEST_TIMES, TASK_COST, UPSTREAM_FAULT_PROB, get_ts

LOG_DIR, RESULT_DIR = "logs/traditional", "experiment_results/traditional"
LOG_FILE = os.path.join(LOG_DIR, "client.log")

class TraditionalClient:
    
    def __init__(self,gateway_host:str, gateway_port:int ):
        
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
        
        for d in [LOG_DIR, RESULT_DIR]: os.makedirs(d, exist_ok=True)
        
    def _log(self, msg: str):
        log_content = f"[{get_ts()}] [TRAD_CLIENT] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(log_content)
        print(log_content.strip())
        
    def run(self):
        
        self._log(f"开始模拟 {REQUEST_TIMES} 个请求， Timeout限制为: {REQUEST_TIMEOUT}s")
        self.experiment_start_time = time.time() # 记录总开始时间
        
        for i in range(REQUEST_TIMES):
            req_id = f"{i+1:03d}"
            task_type = random.choice(ML_TASK_TYPES)   
            task_id = f"ML-{task_type[:4].upper()}-{random.randint(1, int(REQUEST_TIMES))}"
            
            params = {"request_id": req_id, "task_id": task_id, "task_type": task_type}
            
            # 估算发送的请求大小 (URL + Params)
            req_size = sys.getsizeof(self.gateway_url) + sys.getsizeof(str(params))
            self.total_bytes_sent += req_size
            
            req_start_time = time.time() # 记录单次请求开始时间
            
            try:
                res = requests.get(f"{self.gateway_url}/api/forward", params=params, timeout=REQUEST_TIMEOUT)
                req_end_time = time.time() # 记录单次请求结束时间
                
                # 估算接收到的响应大小
                self.total_bytes_received += len(res.content)
                
                json_res = res.json()
                response_data = json_res.get('response_data')
                
                if res.status_code == 200:
                    # 记录延迟
                    self.latencies.append(req_end_time - req_start_time)
                    self.success += 1
                    self._log(f"[REQ-{req_id}] - [{task_id} - {task_type}] 成功收到响应: ({response_data}) 耗时 {round(req_end_time - req_start_time, 2)}s")
                    
            except requests.exceptions.Timeout:
                self._log(f"[REQ-{req_id}] - [{task_id} - {task_type}] 请求超时（未收到响应）")
                self.failed += 1
                
            time.sleep(0.5)
            
        self.experiment_end_time = time.time() # 记录总结束时间
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
        with open(f"{RESULT_DIR}/result.txt", "w", encoding="utf-8") as f: f.write(report)
        self._log(f"实验报告已生成: {RESULT_DIR}/result.txt")

