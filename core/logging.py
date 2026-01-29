import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from core.config import settings

def setup_logging(settings):
    """
    Configure logging for the application.
    """
    log_level = settings.LOG_LEVEL.upper()
    log_dir = settings.LOG_DIR
    today = datetime.now().strftime("%Y-%m-%d")
    log_filename = f"{today}.log"
    
    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)
    
    log_path = os.path.join(log_dir, log_filename)
    
    # Define format
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # File Handler (Daily Rotation)
    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # Root Logger Configuration
    logging.basicConfig(
        level=log_level,
        handlers=[file_handler, console_handler]
    )
    
    logging.info(f"Logging initialized. Level: {log_level}, File: {log_path}")
