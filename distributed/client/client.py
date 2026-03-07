import requests, time, os, json, threading, random
import sys

from requests.adapters import HTTPAdapter

from common.baseline import CONNECT_TIMEOUT, DOWNSTREAM_FAULT_PROB, ML_TASK_TYPES, REQUEST_TIMEOUT, REQUEST_TIMES, RETRY_TIMES, TASK_COST, UPSTREAM_FAULT_PROB, get_ts
from distributed.client.LoggedRetry import LoggedRetry

LOG_DIR, RESULT_DIR = "logs/distributed", "experiment_results/distributed"
LOG_FILE = os.path.join(LOG_DIR, "client.log")
QUEUE_FILE = os.path.join(LOG_DIR, "fault_queue.json")
MAX_LOG_SIZE = 20 * 1024 * 1024

class DistributedClient:
    
    def __init__(self, gateway_host:str, gateway_port:int):
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.gateway_url = f'http://{self.gateway_host}:{self.gateway_port}'
        
        self.success, self.failed = 0, 0
        self.latencies = []                # 记录所有成功请求的延迟
        self.total_bytes_sent = 0          # 网络开销：发送字节数
        self.total_bytes_received = 0      # 网络开销：接收字节数
        self.server_cache_hits = 0         # 服务端 Valkey 缓存命中数
        self.retried_requests = set()             # 记录总计触发的底层重试次数
        self.gateway_degrade_hits = 0      # 网关 RDS 降级命中数
        self.experiment_start_time = 0     # 主循环开始时间
        self.experiment_end_time = 0       # 主循环结束时间
        # ================================================================
        
        for d in [LOG_DIR, RESULT_DIR]: os.makedirs(d, exist_ok=True)
        if not os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE, "w") as f: json.dump([], f)
            
        self.running = True
        threading.Thread(target=self._async_worker, daemon=True).start()

    def _clean_log(self):
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_SIZE:
            with open(LOG_FILE, "r", encoding="utf-8") as f: lines = f.readlines()
            reserve = int(len(lines) * 2 / 3)
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{get_ts()}] [OPT_CLIENT] [LOG_CLEAN] 日志达20M上限，清理最早1/3\n")
                f.writelines(lines[-reserve:] if reserve > 0 else [])

    def _log(self, level: str, msg: str):
        self._clean_log()
        log_content = f"[{get_ts()}] [OPT_CLIENT] [{level}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(log_content)
        print(log_content.strip())

    def _enqueue(self, req_id: str, task_id:str, task_type: str, timestamp:str):
        with open(QUEUE_FILE, "r") as f: q = json.load(f)
        q.append({"request_id": req_id, "task_type": task_type, 'task_id':task_id, "timestamp":timestamp})
        with open(QUEUE_FILE, "w") as f: json.dump(q, f)
        self._log("QUEUE_ADD", f"[REQ-{req_id}] 异常记录已写入本地队列。")

    def _async_worker(self):
        # 此处不进行任何性能和网络开销的统计，仅负责静默重试
        while self.running:
            try:
                with open(QUEUE_FILE, "r") as f: q = json.load(f)
                if q:
                    task = q[0]
                    req_id, task_id, task_type = task["request_id"], task["task_id"], task["task_type"]
                    self._log("REPORTING", f"[REQ-{req_id}] 异步尝试向服务端上报异常记录 [{task_id} - {task_type}]...")
                    
                    res = requests.post(f"{self.gateway_url}/api/report", json=task)
                    
                    if res.status_code == 200:
                        json_res = res.json()
                        self._log("REPORT_SUCCESS", f"[REQ-{req_id}] explaination: {json_res.get('explaination')}")
                        if json_res.get('outcome') == 1:
                            self._log("REPORT_SUCCESS", f"[REQ-{req_id}] 双向确认完成！从队列移除。")
                            q.pop(0)
                            with open(QUEUE_FILE, "w") as f: json.dump(q, f)
                        else:
                            self._log("REPORT_RETRY", f"[REQ-{req_id}] resent request again")
                            failed_task = q.pop(0)
                            q.append(failed_task)
                            with open(QUEUE_FILE, "w") as f: json.dump(q, f)
            except Exception as e:
                self._log('WARNING', f"{e}")
            time.sleep(2.0)

    def _save_result(self):
        response_rate = round(self.success / REQUEST_TIMES * 100, 2) if REQUEST_TIMES > 0 else 0
        total_exec_time = round(self.experiment_end_time - self.experiment_start_time, 3)
        avg_latency = round(sum(self.latencies) / len(self.latencies), 3) if self.latencies else 0
        total_overhead_kb = round((self.total_bytes_sent + self.total_bytes_received) / 1024, 2)
        
        report = f"""==================================================
DISTRIBUTED ML SYSTEM - DISTRIBUTED MODE REPORT
Generated: {get_ts()}
==================================================
[SYSTEM CONFIGURATION]
  Gateway Address          : {self.gateway_url}
  Client Timeout           : {REQUEST_TIMEOUT}s
  Total Requests           : {REQUEST_TIMES}
  Simulated Tasks          : [{", ".join(ML_TASK_TYPES)}]
  Mimic Fault Config       : Upstream ({UPSTREAM_FAULT_PROB*100}%) + Downstream ({DOWNSTREAM_FAULT_PROB*100}%)
  Server Handler Per Task  : {TASK_COST}s
  client retry config      : {RETRY_TIMES}
==================================================
[RELIABILITY METRICS]
  Total Sent        : {REQUEST_TIMES}
  Direct Success    : {self.success}
  Failed / Dropped  : {self.failed}
  Auto Retries      : {len(self.retried_requests)}
  Response Rate     : {response_rate}%
--------------------------------------------------
[CACHE PERFORMANCE]
  Server Valkey Hits : {self.server_cache_hits} (Idempotency)
  Gateway RDS Hits   : {self.gateway_degrade_hits} (Degradation)
==================================================
[MAIN FLOW PERFORMANCE & OVERHEAD]
  Total Exec Time : {total_exec_time} seconds
  Average Latency : {avg_latency} seconds/req
  Network Overhead: {total_overhead_kb} KB (Approx. Sent + Received)
==================================================
"""
        with open(f"{RESULT_DIR}/result.txt", "w", encoding="utf-8") as f: f.write(report)
        self._log("EXPERIMENT", f"实验报告已生成: {RESULT_DIR}/result.txt\n")

    def run(self):
        self._log('EXPERIMENT', f"开始模拟 {REQUEST_TIMES} 个请求， Timeout限制为: {REQUEST_TIMEOUT}s")
        self.experiment_start_time = time.time() # 记录主循环开始时间
        
        session = self._create_session_with_retries()
        
        for i in range(REQUEST_TIMES):
            req_id = f"{i+1:03d}"
            task_type = random.choice(ML_TASK_TYPES)
            task_id = f"ML-{task_type[:4].upper()}-{random.randint(1, int(REQUEST_TIMES))}"
            
            params = {"request_id": req_id, "task_id": task_id, "task_type": task_type}
            
            # 仅统计发送给网关的主业务请求大小
            req_size = sys.getsizeof(self.gateway_url) + sys.getsizeof(str(params))
            self.total_bytes_sent += req_size
            
            req_start_time = time.time() 
            
            try:
                res = session.get(
                f"{self.gateway_url}/api/forward", 
                params=params, 
                # 前一个数字是 连接超时（握手），后一个是 读取超时（等待处理）。
                timeout=(CONNECT_TIMEOUT, REQUEST_TIMEOUT)
                )
                
                # 只有拿到响应（即使是 Retry 之后的）才进入这里
                res.raise_for_status() # 触发非200状态码的异常
                
                req_end_time = time.time() 
                
                # 仅统计来自网关的主业务响应大小
                self.total_bytes_received += len(res.content)
                
                json_res = res.json()
                response_data = json_res.get('response_data')
                server_note = json_res.get('server_note')
                gateway_note = json_res.get('gateway_note')
                
                if res.status_code == 200:
                    self.latencies.append(req_end_time - req_start_time)
                    self.success += 1
                    
                    if server_note: self.server_cache_hits += 1
                    if gateway_note: self.gateway_degrade_hits += 1
                    
                    self._log("SUCCESS", f'[REQ-{req_id}] - [{task_id} - {task_type}] 成功收到响应: ({response_data}) 耗时 {round(req_end_time - req_start_time, 2)}s, server_cache: {server_note if server_note else "No"}, gateway_cache: {gateway_note if gateway_note else "No"}')
                    
            except requests.exceptions.RequestException:
                self._log("ERROR", f"[REQ-{req_id}] - [{task_id} - {task_type}] 请求超时（未收到响应）。上报服务端！")
                self.failed += 1
                self._enqueue(req_id, task_id, task_type, get_ts())
                
            time.sleep(0.5)

        self.experiment_end_time = time.time() # 记录主循环结束时间（放在休眠前）
        
        time.sleep(8.0) # 等待队列处理完毕
        self.running = False
        self._save_result()

    def _create_session_with_retries(
        self,
        retries=RETRY_TIMES,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 504)
    ):
        session = requests.Session()
        
        retry_strategy = LoggedRetry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
            client=self  
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
