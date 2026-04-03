"""
Writes to the append-only audit log.
In production: /audit/logs/claude-activity.log
For local dev/testing: configurable path.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)


def _log_path():
    return Path(get("audit_log", "/audit/logs/claude-activity.log"))


def log_event(event_type, details):
    """
    Append an event to the audit log.
    Format: [ISO timestamp] [event_type] details
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] [{event_type}] {details}\n"

    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(line)
    except PermissionError:
        logger.error(f"Cannot write to audit log: {path}")
    except OSError as e:
        logger.error(f"Audit log write error: {e}")
