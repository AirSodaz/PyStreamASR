"""Gunicorn configuration for PyStreamASR production deployment."""

import multiprocessing

# Bind address
bind = "0.0.0.0:8000"

# Worker configuration
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"

# Timeout settings (seconds)
timeout = 120
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "pystreamasr"

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
