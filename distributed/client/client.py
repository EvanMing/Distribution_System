from concurrent.futures import ThreadPoolExecutor, as_completed

import requests, time, os, json, threading, random
import sys

from requests.adapters import HTTPAdapter

from common.baseline import BACKOFF_FACTOR, CONNECT_TIMEOUT, DOWNSTREAM_FAULT_PROB, EXPERIMENT_RESULT_FILE_NAME, FAULT_QUEUE_POLL_TIME, MAX_WORKERS, ML_TASK_TYPES, REQUEST_TIMEOUT, REQUEST_TIMES, RETRY_TIMES, TASK_COST, UPSTREAM_FAULT_PROB, WAIT_QUEUE_REPORT_TIME, get_ts
from common.logger_config import setup_logger
from distributed.client.RequestResult import RequestResult
from distributed.client.LoggedRetry import LoggedRetry

LOG_DIR, RESULT_DIR = "logs/distributed", "experiment_results/distributed"
LOG_PATH = f"{LOG_DIR}/client.log"
QUEUE_FILE = os.path.join(LOG_DIR, "fault_queue.json")

class DistributedClient:
    
    def __init__(self, gateway_host:str, gateway_port:int):
        # 初始化日志
        self.logger = setup_logger("CLIENT", log_file = LOG_PATH ,max_bytes = 20*1024*1024)
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
        
        # ================================================================
        # 新增：自愈机制（Outcome 0）专项统计指标
        self.self_healed_success = 0           # 成功自愈的任务数
        self.self_healing_bytes_sent = 0       # 自愈流程产生的发送开销
        self.self_healing_bytes_received = 0   # 自愈流程产生的接收开销
        # ================================================================
        
        for d in [LOG_DIR, RESULT_DIR]: os.makedirs(d, exist_ok=True)
        if not os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE, "w") as f: json.dump([], f)
        
        self.running = True
        threading.Thread(target=self._async_worker, daemon=True).start()

    def _enqueue(self, req_id: str, task_id:str, task_type: str, timestamp:str):
        with open(QUEUE_FILE, "r") as f: q = json.load(f)
        q.append({"request_id": req_id, "task_type": task_type, 'task_id':task_id, "timestamp":timestamp})
        with open(QUEUE_FILE, "w") as f: json.dump(q, f)
        self.logger.info(f"[REPORT] [REQ-{req_id}] 异常记录已写入本地队列。")

    def _async_worker(self):
        while self.running:
            try:
                with open(QUEUE_FILE, "r") as f: q = json.load(f)
                if q:
                    task = q[0]
                    req_id, task_id, task_type = task["request_id"], task["task_id"], task["task_type"]
                    self.logger.info(f"[REPORT] [REQ-{req_id}] 异步尝试向服务端上报异常记录 [{task_id} - {task_type}]...")
                    
                    # 1. 统计 Report 请求发送开销
                    report_url = f"{self.gateway_url}/api/report"
                    report_req_size = sys.getsizeof(report_url) + sys.getsizeof(json.dumps(task))
                    self.self_healing_bytes_sent += report_req_size
                    
                    res = requests.post(report_url, json=task)
                    
                    # 2. 统计 Report 请求接收开销
                    self.self_healing_bytes_received += len(res.content)
                    
                    if res.status_code == 200:
                        json_res = res.json()
                        self.logger.info(f"[REPORT] [REQ-{req_id}] [{json_res.get('task_priority')}] {json_res.get('explaination')}")
                        
                        if json_res.get('outcome') == 1:
                            self.logger.info(f"[REPORT] [REQ-{req_id}] 双向确认完成！从队列移除。")
                            q.pop(0)
                            with open(QUEUE_FILE, "w") as f: json.dump(q, f)
                        else:
                            self.logger.warning(f"[REPORT] [REQ-{req_id}] 收到支持重新获取响应异常(outcome 0)，正在重新向网关发起原始业务请求...")
                            
                            params = {
                                "request_id": req_id,
                                "task_id": task_id,
                                "task_type": task_type
                            }
                            
                            # 3. 统计 Retry (Forward) 请求发送开销
                            forward_url = f"{self.gateway_url}/api/forward"
                            retry_req_size = sys.getsizeof(forward_url) + sys.getsizeof(str(params))
                            self.self_healing_bytes_sent += retry_req_size
                            
                            try:
                                retry_res = requests.get(
                                    forward_url, 
                                    params=params, 
                                    timeout=(CONNECT_TIMEOUT, REQUEST_TIMEOUT)
                                )
                                
                                # 4. 统计 Retry (Forward) 请求接收开销
                                self.self_healing_bytes_received += len(retry_res.content)
                                
                                if retry_res.status_code == 200:
                                    # ======================================================
                                    # 记录自愈成功
                                    self.self_healed_success += 1
                                    # ======================================================
                                    self.logger.info(f"[REPORT] [REQ-{req_id}] 重新发起业务请求成功！静默处理完毕。最终响应: {retry_res.json()}")
                                    q.pop(0)
                                    with open(QUEUE_FILE, "w") as f: json.dump(q, f)
                                else:
                                    self.logger.warning(f"[REPORT] [REQ-{req_id}] 重新请求失败(HTTP {retry_res.status_code})，移至队尾等待下次尝试。")
                                    failed_task = q.pop(0)
                                    q.append(failed_task)
                                    with open(QUEUE_FILE, "w") as f: json.dump(q, f)
                                    
                            except Exception as retry_e:
                                self.logger.warning(f"[REPORT] [REQ-{req_id}] 重新发起业务请求时发生网络异常: {retry_e}，移至队尾等待下次尝试。")
                                failed_task = q.pop(0)
                                q.append(failed_task)
                                with open(QUEUE_FILE, "w") as f: json.dump(q, f)

            except Exception as e:
                self.logger.info(f"[REPORT] 异步队列处理异常: {e}")
                
            time.sleep(FAULT_QUEUE_POLL_TIME)

    def _save_result(self):
        response_rate = round(self.success / REQUEST_TIMES * 100, 2) if REQUEST_TIMES > 0 else 0
        total_exec_time = round(self.experiment_end_time - self.experiment_start_time, 3)
        avg_latency = round(sum(self.latencies) / len(self.latencies), 3) if self.latencies else 0
        
        # 主流程网络开销
        main_overhead_kb = round((self.total_bytes_sent + self.total_bytes_received) / 1024, 2)
        # 自愈流程网络开销
        healing_overhead_kb = round((self.self_healing_bytes_sent + self.self_healing_bytes_received) / 1024, 2)
        # 系统总开销
        total_overhead_kb = round(main_overhead_kb + healing_overhead_kb, 2)
        
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
[SELF-HEALING METRICS (OUTCOME 0)]
  Self-Healed Success     : {self.self_healed_success}
  Healing Net Overhead    : {healing_overhead_kb} KB
==================================================
[PERFORMANCE & OVERHEAD SUMMARY]
  Total Exec Time         : {total_exec_time} seconds
  Average Latency         : {avg_latency} seconds/req
  Main Flow Overhead      : {main_overhead_kb} KB
  Total System Overhead   : {total_overhead_kb} KB (Main + Healing)
==================================================
"""
        with open(f"{RESULT_DIR}/{EXPERIMENT_RESULT_FILE_NAME}", "w", encoding="utf-8") as f: f.write(report)
        self.logger.info(f"实验报告已生成: {RESULT_DIR}/{EXPERIMENT_RESULT_FILE_NAME}\n")

    def _send_single_request(self, i, session) -> RequestResult:
        req_id = f"{i+1:03d}"
        task_type = random.choice(ML_TASK_TYPES)
        task_id = f"ML-{task_type[:4].upper()}-{random.randint(1, int(REQUEST_TIMES))}"
        params = {"request_id": req_id, "task_id": task_id, "task_type": task_type}
        
        # 估算发送大小
        req_size = sys.getsizeof(self.gateway_url) + sys.getsizeof(str(params))
        req_start_time = time.time() 
        
        try:
            res = session.get(
                f"{self.gateway_url}/api/forward", 
                params=params, 
                timeout=(CONNECT_TIMEOUT, REQUEST_TIMEOUT)
            )
            res.raise_for_status() 
            latency = time.time() - req_start_time
            
            return RequestResult(
                is_success=True,
                req_size=req_size,
                res_size=len(res.content),
                latency=latency,
                json_res=res.json(),
                req_id=req_id,
                task_id=task_id,
                task_type=task_type
            )
            
        except requests.exceptions.RequestException:
            return RequestResult(
                is_success=False,
                req_size=req_size,
                res_size=0,
                latency=0.0,
                json_res=None,
                req_id=req_id,
                task_id=task_id,
                task_type=task_type
            )

    def _create_session_with_retries(
        self,
        retries=RETRY_TIMES,
        backoff_factor=BACKOFF_FACTOR,
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
        
        # pool_connections: 缓存的连接池数量
        # pool_maxsize: 连接池中最多保存的连接数
        # 将它们设置为与你的 max_workers 一致，可以避免创建多余的底层 TCP 连接
        adapter = HTTPAdapter(
            max_retries = retry_strategy,
            pool_connections = MAX_WORKERS,  
            pool_maxsize = MAX_WORKERS       
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def run(self):
        self.logger.info(f"开始并发模拟 {REQUEST_TIMES} 个请求， Timeout限制为: {REQUEST_TIMEOUT}s")
        self.experiment_start_time = time.time() 
        
        session = self._create_session_with_retries()
        
        # 使用线程池并发发送请求
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # 提交所有任务
            futures = [executor.submit(self._send_single_request, i, session) for i in range(REQUEST_TIMES)]
            
            # 等待结果并统计
            for future in as_completed(futures):
                result: RequestResult = future.result()
                
                self.total_bytes_sent += result.req_size
                self.total_bytes_received += result.res_size
                
                if result.is_success:
                    self.latencies.append(result.latency)
                    self.success += 1
                    
                    response_data = result.json_res.get('response_data')
                    server_note = result.json_res.get('server_note')
                    gateway_note = result.json_res.get('gateway_note')
                    
                    if server_note: self.server_cache_hits += 1
                    if gateway_note: self.gateway_degrade_hits += 1
                    
                    self.logger.info(f'[REQ-{result.req_id}] 成功收到响应: ({response_data}) 耗时 {round(result.latency, 2)}s, server_cache: {server_note if server_note else "No"}, gateway_cache: {gateway_note if gateway_note else "No"}')
                else:
                    self.logger.error(f"[REQ-{result.req_id}] 请求超时（未收到响应）。上报服务端！")
                    self.failed += 1
                    self._enqueue(result.req_id, result.task_id, result.task_type, get_ts())

        self.experiment_end_time = time.time()
        # 等待异常日志全部上报
        time.sleep(WAIT_QUEUE_REPORT_TIME) 
        self.running = False
        self._save_result()