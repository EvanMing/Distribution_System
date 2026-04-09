import logging
import logging.handlers
import os
import queue
import sys

# Used to record already started listeners, preventing blocking caused by duplicate startup
_activated_listeners = {}

def setup_logger(name: str, log_file: str, level=logging.INFO, max_bytes=50*1024*1024, backup_count=3):
    logger = logging.getLogger(name)
    
    # --- Key Modification 1: If already configured with Handlers, return directly ---
    if logger.handlers:
        return logger

    # Disable log upward propagation (prevents duplicate printing to Root Logger in some environments)
    logger.propagate = False
    logger.setLevel(level)

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    formatter = logging.Formatter(
        '[%(asctime)s.%(msecs)03d] [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Disk writing Handler
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # --- Key Modification 2: Create independent async queue for each instance ---
    log_queue = queue.Queue(-1)
    
    # Create async listener
    listener = logging.handlers.QueueListener(
        log_queue, file_handler, console_handler, respect_handler_level=True
    )
    
    # Start background thread and record it
    listener.start()
    _activated_listeners[name] = listener

    # Attach Handler
    queue_handler = logging.handlers.QueueHandler(log_queue)
    logger.addHandler(queue_handler)

    return logger