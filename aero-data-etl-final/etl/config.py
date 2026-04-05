"""
Configuration loader for ETL service.
Loads DB connection info from environment variables.
"""
import os

class Config:
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 5432))
    DB_NAME = os.getenv("DB_NAME", "aviation-index")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
    BATCH_SIZE = int(os.getenv("BATCH_SIZE", 100))
    LOG_EVERY = int(os.getenv("LOG_EVERY", 1000))
