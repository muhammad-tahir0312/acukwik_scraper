"""
Configuration loader for the aviation data scraper.
Loads and validates configuration from YAML files.
"""
import yaml
import os
import logging
from dotenv import dotenv_values
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Load configuration from YAML file with environment variable overrides.
    
    Args:
        config_path: Path to YAML configuration file
        
    Returns:
        Configuration dictionary
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If configuration is invalid
    """
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        
        if not config:
            raise ValueError(f"Empty configuration file: {config_path}")
        
        logger.info(f"Loaded configuration from: {config_path}")
        
        # Load ignored local credentials, without changing the process environment.
        # Explicit environment variables still take precedence below.
        base_dir = config_file.resolve().parent
        local_env = {}
        for env_path in (base_dir.parent / ".env", base_dir / ".env"):
            if env_path.is_file():
                local_env.update(dotenv_values(env_path, interpolate=False))
        for name, key in (("AUTH_EMAIL", "email"), ("AUTH_PASSWORD", "password")):
            if local_env.get(name):
                config.setdefault("authentication", {})[key] = local_env[name]

        # Apply environment variable overrides
        config = _apply_env_overrides(config)

        # Resolve file paths relative to the configuration file, not the
        # caller's current working directory.
        base_dir = config_file.resolve().parent
        input_paths = config.get("input", {}).get("csv_paths", [])
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        config.setdefault("input", {})["csv_paths"] = [
            str((base_dir / path).resolve()) if not Path(path).is_absolute() else path
            for path in input_paths
        ]
        for section, key in (
            ("output", "directory"),
            ("progress", "file"),
            ("authentication", "cookies_file"),
        ):
            value = config.get(section, {}).get(key)
            if value and not Path(value).is_absolute():
                config[section][key] = str((base_dir / value).resolve())
        
        # Validate configuration
        _validate_config(config)
        
        return config
        
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in configuration file: {e}")
    except Exception as e:
        raise ValueError(f"Failed to load configuration: {e}")


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Override configuration values from environment variables.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Updated configuration dictionary
    """
    # Source name
    if os.getenv("SOURCE_NAME"):
        config.setdefault("source", {})["name"] = os.getenv("SOURCE_NAME")
    
    # Input configuration
    if os.getenv("INPUT_CSV_PATH"):
        config.setdefault("input", {})["csv_paths"] = [os.getenv("INPUT_CSV_PATH")]
    
    # Output configuration
    if os.getenv("OUTPUT_DIRECTORY"):
        config.setdefault("output", {})["directory"] = os.getenv("OUTPUT_DIRECTORY")

    if os.getenv("PROGRESS_FILE"):
        config.setdefault("progress", {})["file"] = os.getenv("PROGRESS_FILE")
    
    # Scraping configuration
    if os.getenv("PARALLEL_WORKERS"):
        config.setdefault("scraping", {})["parallel_workers"] = int(os.getenv("PARALLEL_WORKERS"))
    
    if os.getenv("MAX_RETRIES"):
        config.setdefault("scraping", {})["max_retries"] = int(os.getenv("MAX_RETRIES"))

    if os.getenv("FETCH_MODE"):
        config.setdefault("scraping", {})["fetch_mode"] = os.getenv("FETCH_MODE").lower()
    
    # Selenium configuration
    if os.getenv("BROWSER"):
        config.setdefault("selenium", {})["browser"] = os.getenv("BROWSER")
    
    if os.getenv("HEADLESS"):
        config.setdefault("selenium", {})["headless"] = os.getenv("HEADLESS").lower() == "true"

    if os.getenv("USER_AGENT"):
        config.setdefault("selenium", {})["user_agent"] = os.getenv("USER_AGENT")
    
    # Authentication configuration
    if os.getenv("AUTH_ENABLED"):
        config.setdefault("authentication", {})["enabled"] = os.getenv("AUTH_ENABLED").lower() == "true"
    
    if os.getenv("COOKIES_FILE"):
        config.setdefault("authentication", {})["cookies_file"] = os.getenv("COOKIES_FILE")

    if os.getenv("AUTH_EMAIL"):
        config.setdefault("authentication", {})["email"] = os.getenv("AUTH_EMAIL")

    if os.getenv("AUTH_PASSWORD"):
        config.setdefault("authentication", {})["password"] = os.getenv("AUTH_PASSWORD")
    
    # Logging configuration
    if os.getenv("LOG_LEVEL"):
        config.setdefault("logging", {})["level"] = os.getenv("LOG_LEVEL")
    
    logger.debug("Applied environment variable overrides")
    return config


def _validate_config(config: Dict[str, Any]) -> None:
    """
    Validate configuration structure and required fields.
    
    Args:
        config: Configuration dictionary
        
    Raises:
        ValueError: If configuration is invalid
    """
    # Required top-level sections
    required_sections = ["source", "input", "output", "scraping", "selenium"]
    
    for section in required_sections:
        if section not in config:
            raise ValueError(f"Missing required configuration section: '{section}'")
    
    # Validate source configuration
    if "name" not in config["source"]:
        raise ValueError("Missing required field: source.name")
    
    # Validate input configuration
    if "csv_paths" not in config["input"]:
        raise ValueError("Missing required field: input.csv_paths")
    
    # Validate scraping configuration
    scraping = config["scraping"]
    if scraping.get("fetch_mode", "auto") not in {"selenium", "http", "auto"}:
        raise ValueError("scraping.fetch_mode must be selenium, http, or auto")
    if "parallel_workers" not in scraping:
        raise ValueError("Missing required field: scraping.parallel_workers")
    
    if scraping["parallel_workers"] < 1:
        raise ValueError("scraping.parallel_workers must be at least 1")
    
    if "max_retries" not in scraping:
        raise ValueError("Missing required field: scraping.max_retries")
    
    # Validate selenium configuration
    selenium = config["selenium"]
    if "browser" not in selenium:
        raise ValueError("Missing required field: selenium.browser")
    
    valid_browsers = ["chrome", "firefox"]
    if selenium["browser"].lower() not in valid_browsers:
        raise ValueError(f"selenium.browser must be one of: {valid_browsers}")
    
    # Validate authentication if enabled
    auth = config.get("authentication", {})
    if auth.get("enabled", False):
        if "cookies_file" not in auth:
            raise ValueError("authentication.cookies_file required when authentication is enabled")
    
    logger.info("Configuration validated successfully")


def get_config_value(config: Dict[str, Any], *keys, default: Any = None) -> Any:
    """
    Safely get a nested configuration value.
    
    Args:
        config: Configuration dictionary
        *keys: Nested keys to traverse
        default: Default value if key not found
        
    Returns:
        Configuration value or default
        
    Example:
        get_config_value(config, "scraping", "max_retries", default=3)
    """
    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


def save_config(config: Dict[str, Any], config_path: str = "config.yaml") -> None:
    """
    Save configuration to YAML file.
    
    Args:
        config: Configuration dictionary
        config_path: Path to save configuration file
    """
    try:
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        logger.info(f"Configuration saved to: {config_path}")
        
    except Exception as e:
        logger.error(f"Failed to save configuration: {e}")
        raise
