"""
ETL logging utility for progress and error tracking.
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("etl")

def log_progress(count):
    logger.info(f"Processed {count} records.")

def log_error(msg, exc=None, raw=None):
    logger.error(f"ETL ERROR: {msg}")
    if exc:
        logger.error(f"Exception: {exc}")
    if raw:
        logger.error(f"Raw: {raw}")
