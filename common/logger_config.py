import logging
import logging.handlers
import os
import queue
import sys

# 用于记录已经启动的监听器，防止重复启动导致的阻塞
_activated_listeners = {}

def setup_logger(name: str, log_file: str, level=logging.INFO, max_bytes=50*1024*1024, backup_count=3):
    logger = logging.getLogger(name)
    
    # --- 关键修改 1：如果已经配置过 Handler，说明已经初始化，直接返回 ---
    if logger.handlers:
        return logger

    # 禁用日志向上传递（防止在某些环境下重复打印到 Root Logger）
    logger.propagate = False
    logger.setLevel(level)

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    formatter = logging.Formatter(
        '[%(asctime)s.%(msecs)03d] [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 磁盘写入 Handler
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)

    # 控制台 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # --- 关键修改 2：为每个实例创建独立的异步队列 ---
    log_queue = queue.Queue(-1)
    
    # 创建异步监听器
    listener = logging.handlers.QueueListener(
        log_queue, file_handler, console_handler, respect_handler_level=True
    )
    
    # 启动后台线程并记录
    listener.start()
    _activated_listeners[name] = listener

    # 挂载 Handler
    queue_handler = logging.handlers.QueueHandler(log_queue)
    logger.addHandler(queue_handler)

    return logger