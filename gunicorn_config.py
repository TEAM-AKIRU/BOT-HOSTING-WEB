# gunicorn_config.py
import multiprocessing

# Bind to 0.0.0.0 to be accessible from Nginx
bind = "0.0.0.0:8000"

# Number of worker processes
# A common formula is (2 * number_of_cpus) + 1
workers = multiprocessing.cpu_count() * 2 + 1

# Worker class for handling requests
worker_class = "gthread"
threads = 2

# Timeout for workers
timeout = 60

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
