import random

import requests, time, datetime, os

GATEWAY_URL = "http://127.0.0.1:8080"
LOG_DIR, RESULT_DIR = "logs/traditional", "experiment_results/traditional"
LOG_FILE = os.path.join(LOG_DIR, "client.log")
REQUEST_TIMEOUT = 5.0
REQUEST_TIMES = 30
ML_TASK_TYPES = ["Data_Preprocessing", "Feature_Extraction", "Model_Training", "Model_Inference"]

class TraditionalClient:
    def __init__(self):
        self.success, self.failed = 0, 0
        for d in [LOG_DIR, RESULT_DIR]: os.makedirs(d, exist_ok=True)
        
    def _get_ts(self) -> str: return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _log(self, msg: str):
        log_content = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [TRAD_CLIENT] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(log_content)
        print(log_content.strip())
        
    def run(self):
        self._log(f"开始模拟 {REQUEST_TIMES} 个请求， Timeout限制为: {REQUEST_TIMEOUT}s")
        for i in range(REQUEST_TIMES):
            req_id = f"{i+1:03d}"
            task_type = random.choice(ML_TASK_TYPES)   
            task_id = f"ML-{task_type[:4].upper()}-{random.randint(1, int(REQUEST_TIMES))}"
            
            try:
                params = {"request_id": req_id, "task_id": task_id, "task_type": task_type}
                res = requests.get(f"{GATEWAY_URL}/api/forward", params=params, timeout=REQUEST_TIMEOUT)
                json_res = res.json()
                response_data = json_res.get('response_data')
                if res.status_code == 200:
                    self._log(f"[REQ-{req_id}] - [{task_id} - {task_type}] 成功收到响应: ({response_data})")
                    self.success += 1
            except requests.exceptions.Timeout:
                self._log(f"[REQ-{req_id}] - [{task_id} - {task_type}] 请求超时（未收到响应）")
                self.failed += 1
            time.sleep(0.5)
            
        self._save_result()

    def _save_result(self):
        response_rate = round(self.success / REQUEST_TIMES * 100, 2) if REQUEST_TIMES > 0 else 0
        
        report = f"""==================================================
DISTRIBUTED ML SYSTEM - OPTIMIZED MODE REPORT
Generated: {self._get_ts()}
==================================================
[SYSTEM CONFIGURATION]
  Gateway Address : {GATEWAY_URL}
  Client Timeout  : {REQUEST_TIMEOUT}s
  Total Requests  : {REQUEST_TIMES}
  Fault Config    : Upstream (20%) + Downstream (20%)
==================================================
[PERFORMANCE METRICS]
  Total Sent      : {REQUEST_TIMES}
  Direct Success  : {self.success}
  Failed / Dropped: {self.failed}
  
  Direct Success %: {response_rate}%
==================================================
"""
        with open(f"{RESULT_DIR}/result.txt", "w", encoding="utf-8") as f: f.write(report)
        self._log(f"实验报告已生成: {RESULT_DIR}/result.txt")

if __name__ == "__main__": TraditionalClient().run()