import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from core.context import session_id_ctx


class CorrelationIdFilter(logging.Filter):
    """Filter that injects the session ID into the log record."""

    def filter(self, record):
        record.session_id = session_id_ctx.get()
        return True


def setup_logging(settings):
    """Configures logging for the application.

    Sets up both file-based logging (with daily rotation) and console logging.

    Args:
        settings: The application settings object containing LOG_LEVEL and LOG_DIR.
    """
    log_level = settings.LOG_LEVEL.upper()
    log_dir = settings.LOG_DIR
    today = datetime.now().strftime("%Y-%m-%d")
    log_filename = f"{today}.log"

    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, log_filename)

    # Define format including session_id
    formatter = logging.Formatter(
        "%(asctime)s - [%(session_id)s] - %(name)s - %(levelname)s - %(message)s"
    )

    # Filter instance
    correlation_filter = CorrelationIdFilter()

    # File Handler (Daily Rotation)
    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(correlation_filter)

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(correlation_filter)

    # Root Logger Configuration
    logging.basicConfig(
        level=log_level,
        handlers=[file_handler, console_handler],
        force=True
    )

    logging.info(f"Logging initialized. Level: {log_level}, File: {log_path}")
