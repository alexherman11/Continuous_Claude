#!/usr/bin/env python3
"""
Host-level service that monitors /srv/audit/reports/ for new report files
and sends summaries to Alex via Matrix.

Runs as root on the host — Matrix credentials never enter any container.
"""

import json
import re
import time
import sys
import logging
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip3 install requests")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [notifier] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

REPORTS_DIR = Path("/srv/audit/reports")
PROCESSED_DIR = Path("/srv/audit/reports/processed")
CONFIG_FILE = Path("/srv/watchdog/config/notifier.json")
AUDIT_LOG = Path("/srv/audit/logs/claude-activity.log")

POLL_INTERVAL = 10  # seconds


def load_config():
    """Load Matrix connection config."""
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def send_matrix_message(config, message):
    """Send a message to the configured Matrix room."""
    homeserver = config["homeserver"].rstrip("/")
    room_id = config["room_id"]
    token = config["access_token"]

    txn_id = int(time.time() * 1000)
    url = (f"{homeserver}/_matrix/client/v3/rooms/"
           f"{room_id}/send/m.room.message/{txn_id}")
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "msgtype": "m.notice",
        "body": message,
        "format": "org.matrix.custom.html",
        "formatted_body": _markdown_to_html(message),
    }

    try:
        resp = requests.put(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        logger.info("Matrix notification sent successfully")
    except requests.RequestException as e:
        logger.error(f"Failed to send Matrix notification: {e}")
        _audit_log(f"NOTIFICATION FAILED: {e}")


def _markdown_to_html(text):
    """Simple Markdown to HTML conversion for Matrix."""
    html = text
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'`(.+?)`', r'<code>\1</code>', html)
    html = html.replace("\n", "<br/>")
    return html


def _audit_log(message):
    """Write to audit log."""
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{datetime.utcnow().isoformat()}] [notifier] {message}\n")
    except Exception:
        pass


def process_reports():
    """Check for new report files and send notifications."""
    PROCESSED_DIR.mkdir(exist_ok=True)

    config = load_config()

    for report_file in sorted(REPORTS_DIR.glob("report_*.json")):
        try:
            report = json.loads(report_file.read_text())
            matrix_message = report.get("matrix_message", "")

            if matrix_message:
                header = "Watchdog Report\n" + "=" * 40 + "\n"
                send_matrix_message(config, header + matrix_message)

            _audit_log(f"Report processed: {report_file.name}, "
                       f"verdict: {report.get('verdict')}")

            # Move to processed directory
            report_file.rename(PROCESSED_DIR / report_file.name)

        except Exception as e:
            logger.error(f"Error processing {report_file.name}: {e}")
            _audit_log(f"Report processing error: {report_file.name}: {e}")


def main():
    """Main polling loop."""
    logger.info("Watchdog notifier started")
    _audit_log("Notifier service started")

    while True:
        try:
            process_reports()
        except Exception as e:
            logger.error(f"Polling error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
