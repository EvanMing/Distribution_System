# Distributed ML Task System: Resilience & Self-Healing Prototype

This project is a high-performance **Distributed System Prototype** built with **Python (FastAPI + Uvicorn)**. It is designed to evaluate and compare the reliability, network overhead, and success rates of a **Resilient Distributed Architecture** versus a **Traditional Architecture** under simulated unstable network conditions (packet loss and high latency).

The system models a cloud-native Big Data / Machine Learning pipeline, integrating AWS services (EMR, S3, RDS) and distributed caching (Redis) to guarantee task execution even when components fail.

---

## 🚀 Core Architectural Design

### 1. Distributed Mode (Resilient & Fault-Tolerant)

**A. Adaptive API Gateway (`gateway.py`)**
* **Simulated Fault Injection**: Artificially injects upstream (Gateway -> Server) and downstream (Gateway -> Client) network timeouts based on configurable probabilities.
* **Circuit Breaker**: Monitors failure rates using a sliding window (`WINDOW_SIZE=15`). If the failure rate exceeds `FAIL_THRESHOLD` (50%), traffic is automatically rerouted to a **Backup Server**.
* **AWS RDS (MySQL) Fallback Cache**: Utilizes `DBUtils.PooledDB` for high-concurrency database connections. Successful ML task results are asynchronously written to RDS (`task_cache` table). If all backends fail, the gateway retrieves historical JSON responses from RDS to ensure a successful client response. Includes an auto-cleanup mechanism to keep the cache size under `MAX_CACHE_SIZE`.

**B. Server-Side Reliability & Big Data Compute (`server.py`)**
* **Idempotency Guarantee (Valkey/Redis)**: Uses `redis.set(nx=True)` with a `task_id` locking mechanism. Prevents duplicate task executions caused by client network retries, saving expensive cloud compute resources.
* **Asynchronous EMR Dispatcher**: Heavy Machine Learning tasks (`Criminal_Classification`) are offloaded to an **Amazon EMR (Spark)** cluster via `boto3`. The server submits the job in `cluster` mode and tracks it using FastAPI `BackgroundTasks`, preventing HTTP connection timeouts.
* **Distributed FCM Token Management**: Android device tokens are stored in a Redis Set (`SADD`) to support multi-node push notifications, with an automatic cleanup mechanism for expired/invalid tokens.

**C. Client-Side Self-Healing (`client.py`)**
* **Local Fault Queue**: Failed or timed-out requests are stored locally in `fault_queue.json`.
* **Asynchronous Recovery**: A background worker thread polls the queue, reports faults to the server via `/api/report_fault`, and triggers a "silent background retry" (Outcome 0) based on the server's diagnostic feedback.

**D. Cloud Data Lake & Compute (AWS EMR + S3)**
* **PySpark ML Pipeline**: The `crime_classification.py` script runs on EMR, reading raw data from **AWS S3** (`s3://distributed-system-bucket-project/train(in).csv`).
* **Algorithm**: Implements an NLP Pipeline (`Tokenizer` -> `StopWordsRemover` -> `HashingTF` -> `IDF`) followed by a `LogisticRegression` classifier to predict crime categories.

### 2. Traditional Mode (Baseline)
* **Simple Forwarding**: The gateway acts as a transparent proxy with no retry logic, caching, or circuit breaking.
* **Synchronous Processing**: Lacks Redis idempotency, RDS fallback, and async EMR offloading. Network drops or long-running tasks result in immediate HTTP timeouts and permanent task failures.

---

## 🛠️ Technical Stack

* **Core Framework**: Python 3, FastAPI, Uvicorn, Asynchronous I/O (`asyncio`).
* **Databases & Caching**: 
    * **Valkey (Redis)**: Distributed Idempotency locks and FCM Token pooling.
    * **AWS RDS (MySQL)**: Persistent task caching and Gateway degradation via `PyMySQL` + `PooledDB`.
* **Cloud Infrastructure (AWS)**: 
    * **Amazon EMR**: Distributed Spark cluster for heavy ML processing.
    * **Amazon S3**: Object storage for PySpark scripts and `.csv` datasets.
* **Event Notification**: Firebase Cloud Messaging (FCM) Admin SDK.
* **Libraries**: `requests` (Session pooling & custom Retry adapters), `ThreadPoolExecutor` (Concurrent simulation), `boto3` (AWS SDK), `pandas`, `DBUtils`.

---

## 📊 Comparative Metrics

The system generates a detailed `result.txt` in the `experiment_results/` directory after each run. Key metrics tracked include:

| Metric | Traditional Mode | Distributed Mode |
| :--- | :--- | :--- |
| **Response Success Rate** | Low (Fails on injected packet loss) | **Extremely High** (Client Retries + RDS Fallback) |
| **Long-Running ML Tasks** | Fails due to HTTP 504 timeouts | **Succeeds** (Async EMR submission + FCM Callback) |
| **Compute Redundancy** | High (Duplicate tasks on retry) | **Zero** (Redis Idempotency prevents duplication) |
| **Self-Healing Ability** | None | **Automated** (Outcome-based silent queue recovery) |
| **Network Overhead** | Low (Main flow only) | Slightly Higher (Main flow + Healing/Report overhead) |

---

## 📂 Project Structure

```text
project_root/
├── common/                      # Shared base modules
│   ├── baseline.py              # Global configuration, probabilities, & AWS constants
│   └── logger_config.py         # Thread-safe Asynchronous QueueListener logger
├── distributed/                 # Distributed Fault-Tolerant Mode
│   ├── client/                  # Client with async self-healing queue & HTTPAdapter retries
│   ├── gateway/                 # API Gateway with circuit breaker & RDS PooledDB cache
│   └── server/                  # Primary & Backup servers with Redis idempotency & EMR dispatcher
├── traditional/                 # Traditional Baseline Mode (No Fault-Tolerance)
├── crime_classification.py      # PySpark ML Pipeline script (To be uploaded to S3)
├── logs/                        # Auto-generated async log output directory
├── experiment_results/          # Auto-generated experiment report directory
└── .env                         # Local environment configuration
```

---

## ⚙️ Quick Start

### Step 1: Config AWS Environment (Prerequisites)
1. **S3 Bucket**: Create an S3 bucket (`distributed-system-bucket-project`). Upload `train(in).csv`, `test(in).csv`, and `crime_classification.py` to the root directory.
2. **EMR Cluster**: Create an Amazon EMR cluster named `distributed-system-cluster` with Spark installed. 
   * **Crucial**: Disable "Auto-termination" to keep the cluster waiting for API requests (`WAITING` state).
   * **Crucial**: Ensure the EC2 Instance Profile has `AmazonS3FullAccess`.
3. **AWS RDS**: Create a MySQL instance. Create a database named `gatewaycache` (The Gateway will auto-generate the `task_cache` table upon startup).
4. **Valkey/Redis**: Setup a Valkey or Redis instance (e.g., AWS ElastiCache).

### Step 2: Config EC2 Instances
Provision the following Ubuntu 22.04 instances in the same VPC:
* `instance1-client`: t2.small (Ports: 22) 
* `instance1-gateway`: t2.small (Ports: 22, 8080) 
* `instance1-server`: t2.small (Ports: 22, 8000) *(Requires IAM Role with EMR & S3 access)*
* `instance1-server-backup`: t2.micro (Ports: 22, 8001) 

### Step 3: Environment Setup
Run these commands on your EC2 instances to prepare the environment:
```bash
sudo apt update -y
sudo apt install -y python3-pip python3-dev git python3-venv python3-full nginx
python3 -m venv group_6
source group_6/bin/activate
pip install fastapi uvicorn requests dnspython redis firebase-admin pymysql python-dotenv boto3 pandas DBUtils
```

### Step 4: Configure `.env`
Create a `.env` file in the root directory of the Gateway and Server nodes:
```env
VALKEY_ENDPOINT=your-redis-host.cache.amazonaws.com
RDS_HOST=your-mysql-host.rds.amazonaws.com
RDS_USER=root
RDS_PASSWORD=your-secure-password
RDS_DB_NAME=gatewaycache
FIREBASE_CERT_PATH=serviceAccountKey.json
```
*(Ensure you place your downloaded Firebase `serviceAccountKey.json` in the root directory).*

### Step 5: Run the System

**Run Distributed Mode (Open separate terminal sessions):**
1. Start Backup Server: `python -m distributed.server.server_backup_main`
2. Start Main Server: `python -m distributed.server.server_main`
3. Start Gateway: `python -m distributed.gateway.gateway_main`
4. Start Client Simulation: `python -m distributed.client.client_main`

**Run Traditional Mode (For Baseline Comparison):**
```bash
python -m traditional.server.server_main
python -m traditional.gateway.gateway_main
python -m traditional.client.client_main
```

### Local Testing / SSH Tunnels
If running the code locally but connecting to AWS databases, map ports using SSH:
```bash
ssh -i 'my-key.pem' -L 6379:<redis-endpoint>:6379 -L 3306:<rds-endpoint>:3306 ubuntu@<ec2-ip>
```
