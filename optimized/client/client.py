import requests, time, datetime, os, json, threading, random

GATEWAY_HOST, GATEWAY_PORT = "127.0.0.1", 8080
LOG_DIR, RESULT_DIR = "logs/optimized", "experiment_results/optimized"
LOG_FILE = os.path.join(LOG_DIR, "client.log")
QUEUE_FILE = os.path.join(LOG_DIR, "fault_queue.json")
MAX_LOG_SIZE = 20 * 1024 * 1024

REQUEST_TIMEOUT = 5.0
REQUEST_TIMES = 30
ML_TASK_TYPES = ["Data_Preprocessing", "Feature_Extraction", "Model_Training", "Model_Inference"]

class OptimizedClient:
    def __init__(self,gateway_host:str =GATEWAY_HOST,gateway_port:int=GATEWAY_PORT):
        self.success, self.failed= 0, 0
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.gateway_url = f'http://{self.gateway_host}:{self.gateway_port}'
        
        for d in [LOG_DIR, RESULT_DIR]: os.makedirs(d, exist_ok=True)
        if not os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE, "w") as f: json.dump([], f)
            
        self.running = True
        threading.Thread(target=self._async_worker, daemon=True).start()

    def _get_ts(self) -> str: return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _clean_log(self):
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_SIZE:
            with open(LOG_FILE, "r", encoding="utf-8") as f: lines = f.readlines()
            reserve = int(len(lines) * 2 / 3)
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{self._get_ts()}] [OPT_CLIENT] [LOG_CLEAN] 日志达20M上限，清理最早1/3\n")
                f.writelines(lines[-reserve:] if reserve > 0 else [])

    def _log(self, level: str, msg: str):
        self._clean_log()
        log_content = f"[{self._get_ts()}] [OPT_CLIENT] [{level}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(log_content)
        print(log_content.strip())

    def _enqueue(self, req_id: str,task_id:str, task_type: str,timestamp:str):
        with open(QUEUE_FILE, "r") as f: q = json.load(f)
        q.append({"request_id": req_id, "task_type": task_type,'task_id':task_id,"timestamp":timestamp})
        with open(QUEUE_FILE, "w") as f: json.dump(q, f)
        self._log("QUEUE_ADD", f"[REQ-{req_id}] 异常记录已写入本地队列。")

    def _async_worker(self):
        while self.running:
            try:
                with open(QUEUE_FILE, "r") as f: q = json.load(f)
                if q:
                    task = q[0]
                    req_id, task_id, task_type = task["request_id"], task["task_id"] ,task["task_type"]
                    self._log("REPORTING", f"[REQ-{req_id}] 异步尝试向服务端上报异常记录 [{task_id} - {task_type}]...")
                    res = requests.post(f"{self.gateway_url}/api/report", json=task)
                    if res.json().get("status") == "ack":
                        self._log("REPORT_SUCCESS", f"[REQ-{req_id}] 双向确认完成！从队列移除。")
                        q.pop(0)
                        with open(QUEUE_FILE, "w") as f: json.dump(q, f)
            except: pass
            time.sleep(2.0)

    def _save_result(self):
        response_rate = round(self.success / REQUEST_TIMES * 100, 2) if REQUEST_TIMES > 0 else 0
        
        report = f"""==================================================
DISTRIBUTED ML SYSTEM - OPTIMIZED MODE REPORT
Generated: {self._get_ts()}
==================================================
[SYSTEM CONFIGURATION]
  Gateway Address : {self.gateway_url}
  Client Timeout  : {REQUEST_TIMEOUT}s
  Total Requests  : {REQUEST_TIMES}
  Simulated Tasks : {", ".join(ML_TASK_TYPES)}
  Fault Config    : Upstream (20%) + Downstream (20%)
  Fault Handling  : Adaptive Gateway Retries + Client Async Queue
==================================================
[PERFORMANCE METRICS]
  Total Sent      : {REQUEST_TIMES}
  Direct Success  : {self.success}
  Failed / Dropped: {self.failed}
  
  Direct Success %: {response_rate}%
==================================================
"""
        with open(f"{RESULT_DIR}/result.txt", "w", encoding="utf-8") as f: f.write(report)
        self._log("EXPERIMENT", f"实验报告已生成: {RESULT_DIR}/result.txt")

    def run(self):
        self._log('EXPERIMENT', f"开始模拟 {REQUEST_TIMES} 个请求， Timeout限制为: {REQUEST_TIMEOUT}s")
        for i in range(REQUEST_TIMES):
            req_id = f"{i+1:03d}"
            task_type = random.choice(ML_TASK_TYPES)
            task_id = f"ML-{task_type[:4].upper()}-{random.randint(1, int(REQUEST_TIMES))}"
            try:
                params = {"request_id": req_id, "task_id": task_id, "task_type": task_type}
                res = requests.get(f"{self.gateway_url}/api/forward", params=params, timeout=REQUEST_TIMEOUT)
                json_res = res.json()
                response_data = json_res.get('response_data')
                server_note = json_res.get('server_note')
                gateway_note = json_res.get('gateway_note')
                # 这里不管服务端/或者网关返回的status是success或者是failed都是算有响应，只有连接超时数据没有接收到服务端的响应
                if res.status_code == 200:
                    self._log("SUCCESS", f"""[REQ-{req_id}] - [{task_id} - {task_type}] 成功收到响应: ({response_data}), server_cache: {server_note if server_note else "No"}, gateway_cache: {gateway_note if gateway_note else "No"}""")
                    self.success += 1
                    
            except requests.exceptions.Timeout:
                self._log("ERROR", f"[REQ-{req_id}] - [{task_id} - {task_type}] 请求超时（未收到响应）。上报服务端！")
                self.failed += 1
                self._enqueue(req_id, task_id,task_type,self._get_ts())
            time.sleep(0.5)

        time.sleep(8.0) # 等待队列处理
        self.running = False
        self._save_result()

if __name__ == "__main__": OptimizedClient(gateway_host='172.31.22.227',gateway_port=8080).run()
