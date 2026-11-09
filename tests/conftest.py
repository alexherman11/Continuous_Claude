"""
Test setup: run fully offline.

consciousness_server creates an Anthropic client at import time, so a dummy
API key must be in the environment before it is imported. No test makes a
real API call: the client is replaced with a fake per test.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

os.environ["ANTHROPIC_API_KEY"] = "test-dummy-key"
os.environ.setdefault(
    "CC_EVENT_LOG_PATH",
    os.path.join(tempfile.mkdtemp(prefix="cc-events-"), "events.jsonl"),
)
