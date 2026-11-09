"""
Structured event log (JSONL) for the Consciousness Engine.

One JSON object per line, appended to EVENT_LOG_PATH. Intended for
machine-readable observability of things that are otherwise only visible
in free-text logs: compaction outcomes, plugin fallbacks, tool errors.

Path: $CC_EVENT_LOG_PATH, default consciousness_data/events.jsonl next to
this file. Logging never raises: a failure to write an event must not
break the agent loop.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone

_base_dir = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EVENT_LOG_PATH = os.path.join(_base_dir, "consciousness_data", "events.jsonl")

_lock = threading.Lock()


def event_log_path():
    return os.environ.get("CC_EVENT_LOG_PATH", DEFAULT_EVENT_LOG_PATH)


def log_event(event, **fields):
    """Append one event record. Returns the record (useful for tests)."""
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
    record.update(fields)
    try:
        path = event_log_path()
        line = json.dumps(record, default=str, ensure_ascii=False)
        with _lock:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:  # pragma: no cover - best effort
        logging.warning(f"event_log: failed to write {event}: {e}")
    return record
