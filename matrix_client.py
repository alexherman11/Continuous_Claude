"""
Bidirectional Matrix client for the Continuous Claude consciousness system.

Uses a dedicated bot account for sending messages, so Claude's messages
appear under a separate identity from Alex. Receives messages from the
room and filters out the bot's own messages by sender ID.
"""

import json
import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

BOT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "consciousness_data", "matrix_bot_config.json"
)
SYNC_TOKEN_PATH = os.path.join(
    os.path.dirname(__file__), "consciousness_data", "matrix_sync_token.txt"
)


class MatrixClient:
    def __init__(self, config_path=None):
        config_path = config_path or os.environ.get("MATRIX_BOT_CONFIG_PATH", BOT_CONFIG_PATH)
        with open(config_path) as f:
            cfg = json.load(f)

        self.homeserver = cfg["homeserver"]
        self.access_token = cfg["access_token"]
        self.room_id = cfg["room_id"]
        self.headers = {"Authorization": f"Bearer {self.access_token}"}
        self._backoff = 1  # current backoff in seconds

        # Resolve bot's own user ID for sender-based filtering
        self.user_id = self._whoami()
        self._sync_token = self._load_sync_token()

    # ── Identity ──────────────────────────────────────────────────────

    def _whoami(self):
        """Get the bot's own user ID."""
        try:
            resp = requests.get(
                f"{self.homeserver}/_matrix/client/v3/account/whoami",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            user_id = resp.json()["user_id"]
            logger.info(f"Matrix identity: {user_id}")
            return user_id
        except Exception as e:
            logger.warning(f"Failed to resolve Matrix identity: {e}")
            return None

    # ── Sending ───────────────────────────────────────────────────────

    def send_message(self, text):
        """Send a text message to the configured room. Returns event_id."""
        return self._send(text, msgtype="m.text")

    def send_notice(self, text):
        """Send a notice (less intrusive) to the configured room."""
        return self._send(text, msgtype="m.notice")

    def send_formatted(self, plain_text, html):
        """Send an HTML-formatted message."""
        return self._send(plain_text, msgtype="m.text", formatted_body=html)

    def _send(self, body, msgtype="m.text", formatted_body=None):
        txn_id = int(time.time() * 1000)
        url = (
            f"{self.homeserver}/_matrix/client/v3/rooms/"
            f"{self.room_id}/send/m.room.message/{txn_id}"
        )
        payload = {"msgtype": msgtype, "body": body}
        if formatted_body:
            payload["format"] = "org.matrix.custom.html"
            payload["formatted_body"] = formatted_body

        resp = requests.put(url, json=payload, headers=self.headers, timeout=15)
        resp.raise_for_status()
        self._backoff = 1  # reset on success
        return resp.json().get("event_id")

    # ── Receiving ─────────────────────────────────────────────────────

    def poll_messages(self):
        """
        Poll for new messages since last sync.
        Returns list of dicts: [{sender, body, timestamp, event_id}, ...]
        """
        try:
            data = self._sync(timeout_ms=0)
        except Exception as e:
            logger.warning(f"Matrix sync failed: {e}")
            self._backoff = min(self._backoff * 2, 30)
            return []

        self._backoff = 1  # reset on success
        messages = []

        rooms = data.get("rooms", {}).get("join", {})
        room_data = rooms.get(self.room_id, {})
        timeline = room_data.get("timeline", {})

        for event in timeline.get("events", []):
            # Only process message events
            if event.get("type") != "m.room.message":
                continue
            # Skip messages sent by the bot itself
            if event.get("sender") == self.user_id:
                continue
            content = event.get("content", {})
            # Skip notices (bot-generated: status reports, etc.)
            if content.get("msgtype") == "m.notice":
                continue
            messages.append({
                "sender": event.get("sender"),
                "body": content.get("body", ""),
                "timestamp": event.get("origin_server_ts", 0),
                "event_id": event.get("event_id"),
            })

        # Save the new sync token
        next_batch = data.get("next_batch")
        if next_batch:
            self._sync_token = next_batch
            self._save_sync_token(next_batch)

        return messages

    def _sync(self, timeout_ms=0):
        """Raw /sync call with optional since token."""
        params = {"timeout": str(timeout_ms)}

        if self._sync_token:
            params["since"] = self._sync_token
        else:
            # First sync: use a filter to avoid downloading full history
            params["filter"] = json.dumps({
                "room": {
                    "rooms": [self.room_id],
                    "timeline": {"limit": 0},
                }
            })

        resp = requests.get(
            f"{self.homeserver}/_matrix/client/v3/sync",
            headers=self.headers,
            params=params,
            timeout=max(30, timeout_ms // 1000 + 10),
        )
        resp.raise_for_status()
        return resp.json()

    # ── Sync token persistence ────────────────────────────────────────

    def _load_sync_token(self):
        try:
            with open(SYNC_TOKEN_PATH) as f:
                token = f.read().strip()
                if token:
                    logger.info("Loaded existing Matrix sync token")
                    return token
        except FileNotFoundError:
            pass
        return None

    def _save_sync_token(self, token):
        os.makedirs(os.path.dirname(SYNC_TOKEN_PATH), exist_ok=True)
        with open(SYNC_TOKEN_PATH, "w") as f:
            f.write(token)
