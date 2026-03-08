# Distributed ML Task System: Resilience & Self-Healing Comparison

This project is a high-performance **Distributed System Prototype** built with **Python (FastAPI + Uvicorn)**. It is designed to evaluate and compare the reliability, network overhead, and success rates of a **Distributed Architecture** versus a **Traditional Architecture** under unstable network conditions (simulated packet loss and high latency).



## 🚀 Core Architectural Design

### 1. Distributed Mode (Resilient)
* **Adaptive Gateway Dispatching**:
    * **Circuit Breaker**: Monitors failure rates using a sliding window (`WINDOW_SIZE`). If the failure rate exceeds the threshold (`FAIL_THRESHOLD`), traffic is automatically rerouted to a **Backup Server**.
    * **Multi-level Caching**: Integrates **AWS RDS (MySQL)** as a fallback database. If all backends are unreachable, the gateway retrieves historical results from RDS to ensure a response.
* **Server-Side Reliability**:
    * **Idempotency Guarantee**: Powered by **Valkey/Redis**. Uses a `task_id` based locking mechanism to prevent redundant computations caused by network retries, saving cloud compute resources.
    * **Firebase Alerting**: System anomalies are pushed to an Android monitoring app via **Firebase Cloud Messaging (FCM)** for real-time oversight.
* **Client-Side Self-Healing**:
    * **Local Fault Queue**: Failed or timed-out requests are stored in a local `fault_queue.json`.
    * **Async Reporting & Recovery**: A background worker polls the queue, reports faults to the server, and triggers a "silent retry" (Outcome 0) based on the server's diagnostic feedback.

### 2. Traditional Mode (Baseline)
* **Simple Forwarding**: The gateway acts as a transparent proxy with no retry logic or circuit breaking.
* **Synchronous Processing**: Lacks idempotent caching and degradation strategies; network drops result in immediate task failure.

---

## 🛠️ Technical Stack

* **Framework**: FastAPI, Uvicorn (Asynchronous I/O).
* **Databases**: 
    * **Valkey (Redis)**: For Idempotency and Distributed Token storage.
    * **AWS RDS (MySQL)**: For persistent task caching and gateway fallback via `PooledDB`.
* **Cloud Services**: Firebase Admin SDK (Android Push Notifications).
* **Libraries**: Requests (Session pooling), ThreadPoolExecutor (Concurrent simulation), Pandas (Data export).

---

## 📊 Comparative Metrics

The system generates a detailed `result.txt` in the `experiment_results/` directory after each run. Key metrics include:

| Metric | Traditional Mode | Distributed Mode |
| :--- | :--- | :--- |
| **Response Success Rate** | Low (Directly impacted by drops) | **Extremely High** (Retries + RDS Fallback) |
| **Compute Redundancy** | High (Duplicate tasks) | **Zero** (Redis Idempotency) |
| **Self-Healing Ability** | None | **Automated** (Outcome-based recovery) |
| **Network Overhead** | Low (Main flow only) | Slightly Higher (Main + Healing overhead) |

---

## 📂 Project Structure

```text
.
├── common/                 # Global constants (baseline.py), logger config
├── distributed/            # Distributed mode components
│   ├── client/             # Self-healing client with async workers
│   ├── gateway/            # Circuit-breaking gateway with RDS integration
│   └── server/             # Idempotent Primary and Backup servers
├── traditional/            # Baseline comparison components (Client, Gateway, Server)
├── logs/                   # System-generated logs (client.log, gateway.log, etc.)
└── experiment_results/     # Automated experiment reports (result.txt)
```

## ⚙️ Quick Start

### step 1: Create a .env file in the root directory:
* VALKEY_ENDPOINT=your-redis-host
* RDS_HOST=your-mysql-host
* RDS_USER=root
* RDS_PASSWORD=your-password
* RDS_DB_NAME=gatewaycache
* FIREBASE_CERT_PATH=serviceAccountKey.json

### step 2: install essential packages
* sudo apt update -y # 1. 更新系统源
* sudo apt install -y python3-pip python3-dev git nginx # 2. 安装核心工具（Python 基础包、Git、Nginx）
* sudo apt install -y python3-venv python3-full # 3. 安装虚拟环境所需依赖（Ubuntu 推荐方式）
* python3 -m venv group_6 # 4. 创建虚拟环境
* source group_6/bin/activate # 5. 激活虚拟环境
* pip install fastapi uvicorn requests dnspython redis firebase-admin pymysql python-dotenv # 6. 一次性安装所有 Python 依赖（合并了原来的多条命令）
* python --version && uvicorn --version && pip list | grep fastapi # 7. 验证安装
