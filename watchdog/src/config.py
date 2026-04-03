"""
Configuration loader for the watchdog.
Reads from /config/watchdog.json (mounted read-only from host).
Provides sensible defaults for local development/testing.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default config path inside container; overridden for local dev
_CONFIG_PATH = Path(__file__).parent.parent / "config" / "watchdog.json"

_config = None


def load_config(config_path=None):
    """Load configuration from JSON file."""
    global _config
    path = Path(config_path) if config_path else _CONFIG_PATH

    try:
        with open(path, "r") as f:
            _config = json.load(f)
        logger.info(f"Configuration loaded from {path}")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load config from {path}: {e}; using defaults")
        _config = {}

    return _config


def get(key, default=None):
    """Get a config value by key."""
    global _config
    if _config is None:
        load_config()
    return _config.get(key, default)


def get_path(key, default=None):
    """Get a config value as a Path."""
    val = get(key, default)
    return Path(val) if val else None
