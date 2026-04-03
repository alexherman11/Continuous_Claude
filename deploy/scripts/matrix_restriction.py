"""
Matrix restriction layer.

Wraps the requests library to intercept Matrix API calls and enforce:
1. Only the configured room_id is allowed
2. No /join, /createRoom, or /invite calls
3. Rate limiting on outbound messages

This module is imported in the entrypoint BEFORE the application starts,
monkey-patching requests to add enforcement.
"""

import os
import time
import threading
import logging
import functools

logger = logging.getLogger("matrix_restriction")

ALLOWED_ROOM_ID = os.environ.get("MATRIX_ALLOWED_ROOM_ID", "")
MAX_MESSAGES_PER_MINUTE = 10

# Thread-safe rate limiter
_rate_lock = threading.Lock()
_message_times = []

# Blocked Matrix API paths (these should never be called)
BLOCKED_PATHS = [
    "/createRoom",
    "/join/",
    "/invite",
    "/publicRooms",
    "/user/",  # Block profile/DM lookups
]


def check_matrix_request(method, url, **kwargs):
    """Validate a Matrix API request before it's sent."""
    url_lower = url.lower()

    # Check for blocked paths
    for blocked in BLOCKED_PATHS:
        if blocked.lower() in url_lower:
            logger.warning(f"BLOCKED Matrix API call: {method} {url}")
            raise PermissionError(
                f"Matrix restriction: {blocked} calls are not permitted. "
                f"The bot is restricted to room {ALLOWED_ROOM_ID}"
            )

    # Check room_id in URL matches allowed room
    if ALLOWED_ROOM_ID and "/rooms/" in url_lower:
        # Extract room_id from URL
        import urllib.parse
        parts = url.split("/rooms/")
        if len(parts) > 1:
            # Room ID is URL-encoded in the path
            room_part = parts[1].split("/")[0]
            room_id = urllib.parse.unquote(room_part)
            if room_id != ALLOWED_ROOM_ID:
                logger.warning(
                    f"BLOCKED Matrix call to unauthorized room: {room_id} "
                    f"(allowed: {ALLOWED_ROOM_ID})"
                )
                raise PermissionError(
                    f"Matrix restriction: can only interact with room "
                    f"{ALLOWED_ROOM_ID}, not {room_id}"
                )

    # Rate limiting for message sends
    if "/send/" in url_lower and method.upper() in ("PUT", "POST"):
        _check_rate_limit()


def _check_rate_limit():
    """Enforce per-minute rate limit on outbound messages."""
    now = time.time()
    with _rate_lock:
        # Remove entries older than 60 seconds
        _message_times[:] = [t for t in _message_times if now - t < 60]
        if len(_message_times) >= MAX_MESSAGES_PER_MINUTE:
            raise PermissionError(
                f"Matrix rate limit: max {MAX_MESSAGES_PER_MINUTE} "
                f"messages per minute exceeded"
            )
        _message_times.append(now)


def install():
    """
    Monkey-patch the requests library to intercept Matrix API calls.
    Call this BEFORE any application code runs.
    """
    import requests

    original_request = requests.Session.request

    @functools.wraps(original_request)
    def patched_request(self, method, url, **kwargs):
        # Only intercept Matrix API calls (not Anthropic API calls)
        if "/_matrix/" in url:
            check_matrix_request(method, url, **kwargs)
        return original_request(self, method, url, **kwargs)

    requests.Session.request = patched_request

    # Also patch the module-level convenience functions
    original_get = requests.get
    original_post = requests.post
    original_put = requests.put

    @functools.wraps(original_get)
    def patched_get(url, **kwargs):
        if "/_matrix/" in url:
            check_matrix_request("GET", url, **kwargs)
        return original_get(url, **kwargs)

    @functools.wraps(original_post)
    def patched_post(url, **kwargs):
        if "/_matrix/" in url:
            check_matrix_request("POST", url, **kwargs)
        return original_post(url, **kwargs)

    @functools.wraps(original_put)
    def patched_put(url, **kwargs):
        if "/_matrix/" in url:
            check_matrix_request("PUT", url, **kwargs)
        return original_put(url, **kwargs)

    requests.get = patched_get
    requests.post = patched_post
    requests.put = patched_put

    logger.info(f"Matrix restriction installed. Allowed room: {ALLOWED_ROOM_ID}")
    logger.info(f"Rate limit: {MAX_MESSAGES_PER_MINUTE}/minute")


if __name__ == "__main__":
    # Self-test
    install()
    print("Matrix restriction layer installed successfully")
