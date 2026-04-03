#!/usr/bin/env python3
"""
Transparent API proxy for Anthropic API calls.

Runs on the host, intercepts API traffic from the Claude container,
logs everything to the audit log, and forwards to the real API.

Security: This runs outside all containers. Claude cannot modify it.
"""

import http.server
import http.client
import ssl
import json
import sys
import os
import threading
import logging
import time
from datetime import datetime, date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [api-proxy] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8443
TARGET_HOST = "api.anthropic.com"
TARGET_PORT = 443

# Logging
API_LOG = Path("/srv/audit/logs/api-calls.log")
AUDIT_LOG = Path("/srv/audit/logs/claude-activity.log")

# Budget enforcement
DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "5.00"))
INPUT_PRICE_PER_MTOK = 3.0   # Sonnet 4.5 input pricing
OUTPUT_PRICE_PER_MTOK = 15.0  # Sonnet 4.5 output pricing

# TLS certificate paths (self-signed, generated during setup)
CERT_FILE = "/srv/claude/proxy/proxy-cert.pem"
KEY_FILE = "/srv/claude/proxy/proxy-key.pem"

# Thread-safe daily spend tracker
_spend_lock = threading.Lock()
_daily_spend = {"date": None, "total_usd": 0.0, "call_count": 0}


def _reset_daily_spend_if_needed():
    """Reset spend tracker at the start of each day."""
    today = date.today().isoformat()
    with _spend_lock:
        if _daily_spend["date"] != today:
            _daily_spend["date"] = today
            _daily_spend["total_usd"] = 0.0
            _daily_spend["call_count"] = 0


def _record_spend(input_tokens, output_tokens):
    """Record token usage and return current daily total."""
    cost = (input_tokens / 1_000_000 * INPUT_PRICE_PER_MTOK +
            output_tokens / 1_000_000 * OUTPUT_PRICE_PER_MTOK)
    with _spend_lock:
        _daily_spend["total_usd"] += cost
        _daily_spend["call_count"] += 1
        return _daily_spend["total_usd"], _daily_spend["call_count"]


def _check_budget():
    """Check if we are within daily budget. Returns (ok, current_spend)."""
    _reset_daily_spend_if_needed()
    with _spend_lock:
        return _daily_spend["total_usd"] < DAILY_BUDGET_USD, _daily_spend["total_usd"]


def _log_api_call(method, path, request_body, response_status, response_body, elapsed_ms):
    """Append API call details to the audit log."""
    try:
        timestamp = datetime.utcnow().isoformat()

        req_summary = "non-JSON"
        resp_summary = "non-JSON"
        input_tokens = 0
        output_tokens = 0

        try:
            req_data = json.loads(request_body) if request_body else {}
            model = req_data.get("model", "unknown")
            num_messages = len(req_data.get("messages", []))
            num_tools = len(req_data.get("tools", []))
            max_tokens = req_data.get("max_tokens", "?")
            req_summary = (f"model={model} msgs={num_messages} "
                          f"tools={num_tools} max_tokens={max_tokens}")
        except (json.JSONDecodeError, AttributeError):
            pass

        try:
            resp_data = json.loads(response_body) if response_body else {}
            stop_reason = resp_data.get("stop_reason", "?")
            usage = resp_data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            content_blocks = len(resp_data.get("content", []))
            resp_summary = (f"stop={stop_reason} in={input_tokens} "
                           f"out={output_tokens} blocks={content_blocks}")
        except (json.JSONDecodeError, AttributeError):
            pass

        # Record spend
        daily_total, call_count = _record_spend(input_tokens, output_tokens)
        cost = (input_tokens / 1_000_000 * INPUT_PRICE_PER_MTOK +
                output_tokens / 1_000_000 * OUTPUT_PRICE_PER_MTOK)

        log_line = (
            f"[{timestamp}] {method} {path} -> {response_status} "
            f"({elapsed_ms}ms) | {req_summary} | {resp_summary} | "
            f"cost=${cost:.4f} daily_total=${daily_total:.4f} "
            f"call_count={call_count}\n"
        )

        with open(API_LOG, "a") as f:
            f.write(log_line)

        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{timestamp}] [api-proxy] API call #{call_count}: "
                    f"${cost:.4f} (daily: ${daily_total:.4f}/${DAILY_BUDGET_USD})\n")

    except Exception as e:
        logger.error(f"Failed to log API call: {e}")


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    """HTTPS proxy handler that forwards requests to Anthropic API."""

    def do_POST(self):
        self._proxy_request("POST")

    def do_GET(self):
        self._proxy_request("GET")

    def do_PUT(self):
        self._proxy_request("PUT")

    def do_DELETE(self):
        self._proxy_request("DELETE")

    def _proxy_request(self, method):
        start = time.time()

        # Budget check
        budget_ok, current_spend = _check_budget()
        if not budget_ok:
            self.send_error(429, f"Daily budget exceeded: ${current_spend:.2f}/${DAILY_BUDGET_USD}")
            logger.warning(f"Budget exceeded: ${current_spend:.2f}")
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        request_body = self.rfile.read(content_length) if content_length > 0 else b""

        # Forward to real API
        try:
            context = ssl.create_default_context()
            conn = http.client.HTTPSConnection(TARGET_HOST, TARGET_PORT,
                                                context=context, timeout=120)

            # Copy headers (except Host, which we override)
            forward_headers = {}
            for key, value in self.headers.items():
                if key.lower() not in ("host", "transfer-encoding"):
                    forward_headers[key] = value
            forward_headers["Host"] = TARGET_HOST

            conn.request(method, self.path, body=request_body,
                        headers=forward_headers)

            response = conn.getresponse()
            response_body = response.read()

            elapsed_ms = int((time.time() - start) * 1000)

            # Log the call
            _log_api_call(method, self.path, request_body.decode("utf-8", errors="replace"),
                         response.status, response_body.decode("utf-8", errors="replace"),
                         elapsed_ms)

            # Send response back to client
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response_body)

            conn.close()

        except Exception as e:
            logger.error(f"Proxy error: {e}")
            self.send_error(502, f"Proxy error: {e}")

    def log_message(self, format, *args):
        """Suppress default access log (we log our own way)."""
        pass


def main():
    """Start the HTTPS proxy server."""
    logger.info(f"Starting API proxy on {LISTEN_HOST}:{LISTEN_PORT}")
    logger.info(f"Forwarding to {TARGET_HOST}:{TARGET_PORT}")
    logger.info(f"Daily budget: ${DAILY_BUDGET_USD}")

    server = http.server.HTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)

    # TLS wrap
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    logger.info("API proxy ready")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
