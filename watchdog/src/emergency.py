"""
Emergency stop mechanism.
Writes a signal file to the emergency directory, which is monitored
by a host-level systemd service that executes the kill switch.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)


def _emergency_dir():
    return Path(get("emergency_dir", "/audit/emergency"))


def request_emergency_stop(reason, commit_hash=None, findings=None):
    """
    Request an emergency stop of Claude's container.
    Writes a signal file that the host-level stopper service monitors.
    """
    signal = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "STOP",
        "reason": reason,
        "commit_hash": commit_hash,
        "findings": findings[:5] if findings else [],
    }

    emergency_dir = _emergency_dir()
    emergency_dir.mkdir(parents=True, exist_ok=True)
    signal_file = emergency_dir / f"STOP_{int(datetime.now().timestamp())}.json"
    signal_file.write_text(json.dumps(signal, indent=2))

    logger.critical(f"EMERGENCY STOP REQUESTED: {reason}")
    return True
