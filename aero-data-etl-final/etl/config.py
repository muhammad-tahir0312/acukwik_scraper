"""
Configuration loader for ETL service.
Loads DB connection info from environment variables.
"""
import os
from pathlib import Path
from dotenv import dotenv_values

_etl_directory = Path(__file__).resolve().parents[1]
_local = {}
for _path in (_etl_directory.parent / '.env', _etl_directory / '.env'):
    if _path.is_file():
        _local.update(dotenv_values(_path, interpolate=False))


def _value(name, default=None):
    return os.environ.get(name, _local.get(name, default))

class Config:
    DATABASE_URL = _value("DATABASE_URL")
    DB_HOST = _value("DB_HOST", "localhost")
    DB_PORT = int(_value("DB_PORT", 5432))
    DB_NAME = _value("DB_NAME", "av-new")
    DB_USER = _value("DB_USER", "postgres")
    DB_PASSWORD = _value("DB_PASSWORD", "postgres")
    BATCH_SIZE = int(_value("BATCH_SIZE", 100))
    LOG_EVERY = int(_value("LOG_EVERY", 1000))
