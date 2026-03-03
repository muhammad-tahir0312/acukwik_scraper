"""
Aviation Data Ingestion Layer

A clean, production-ready Selenium-based scraper for aviation data.
Designed to feed structured JSON into an ETL pipeline.
"""

__version__ = "1.0.0"
__author__ = "Aviation Data Team"

from .scraper import ScraperOrchestrator, setup_logging
from .parsers import AirportParser, OrganizationParser
from .validators import validate_record
from .output import OutputWriter, BatchOutputWriter
from .progress import ProgressTracker
from .auth import CookieAuthentication
from .config_loader import load_config

__all__ = [
    "ScraperOrchestrator",
    "setup_logging",
    "AirportParser",
    "OrganizationParser",
    "validate_record",
    "OutputWriter",
    "BatchOutputWriter",
    "ProgressTracker",
    "CookieAuthentication",
    "load_config",
]
