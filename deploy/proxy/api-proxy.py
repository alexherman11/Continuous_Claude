#!/usr/bin/env python3
"""
Transparent API proxy for Anthropic API calls.

Runs on the host, intercepts API traffic from the Claude container,
logs everything to the audit log, and forwards to the real API.

Auth: by default forwards the caller's x-api-key / Authorization headers
unchanged (so the API key lives in the container). If PROXY_UPSTREAM_API_KEY
is set, the proxy strips the caller's auth headers and injects that key, so
the container can run with a dummy ANTHROPIC_API_KEY and never sees the
real one.
Budget: tracks daily spend (persisted to disk), rejects over-budget calls.

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
# Bind to all interfaces: the container reaches this via the VPS public IP (slirp4netns).
# External access is blocked by nftables (no input rule for port 8443).
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8443
TARGET_HOST = "api.anthropic.com"
TARGET_PORT = 443

# Logging
API_LOG = Path("/srv/audit/logs/api-calls.log")
AUDIT_LOG = Path("/srv/audit/logs/claude-activity.log")

# Budget enforcement
DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "10.00"))

# Per-model pricing table (USD per million tokens). Keyed by model-ID prefix —
# matched longest-first at lookup time so "claude-opus-4-6" beats a generic
# "claude-opus" entry. Prices reflect standard (≤200K context) tier; the 1M
# context tier charges ~2× but is hard to detect from outside, so we accept
# a modest under-count rather than double-billing every normal call.
MODEL_PRICING = {
    "claude-opus-4-7":   (15.00, 75.00),
    "claude-opus-4-6":   (15.00, 75.00),
    "claude-opus-4-5":   (15.00, 75.00),
    "claude-opus-4":     (15.00, 75.00),
    "claude-sonnet-4-7": ( 3.00, 15.00),
    "claude-sonnet-4-6": ( 3.00, 15.00),
    "claude-sonnet-4-5": ( 3.00, 15.00),
    "claude-sonnet-4":   ( 3.00, 15.00),
    "claude-haiku-4":    ( 1.00,  5.00),
    "claude-haiku":      ( 0.80,  4.00),
    "claude-3-5-sonnet": ( 3.00, 15.00),
    "claude-3-5-haiku":  ( 0.80,  4.00),
    "claude-3-opus":     (15.00, 75.00),
}
DEFAULT_PRICING = (15.00, 75.00)   # Assume Opus if unknown — fail-safe (over-counts)


def _pricing_for(model_id):
    """Look up input/output price per MTok for a model ID. Longest-prefix match."""
    if not model_id:
        return DEFAULT_PRICING
    m = str(model_id).lower()
    for key in sorted(MODEL_PRICING, key=len, reverse=True):
        if m.startswith(key):
            return MODEL_PRICING[key]
    return DEFAULT_PRICING

# Optional host-held API key. When set, caller auth is replaced with this
# key (see module docstring). Unset/empty = forward caller auth (default).
UPSTREAM_API_KEY = os.environ.get("PROXY_UPSTREAM_API_KEY", "").strip()
_CALLER_AUTH_HEADERS = ("x-api-key", "authorization")

# TLS certificate paths (self-signed, generated during setup)
CERT_FILE = "/srv/claude/proxy/proxy-cert.pem"
KEY_FILE = "/srv/claude/proxy/proxy-key.pem"

# ── Thread-safe daily spend tracker (persisted to disk) ──────────────

_spend_lock = threading.Lock()
_daily_spend = {"date": None, "total_usd": 0.0, "call_count": 0}
SPEND_FILE = Path("/srv/audit/logs/daily_spend.json")


def _load_spend_from_disk():
    """Load spend tracker from disk on startup."""
    global _daily_spend
    try:
        if SPEND_FILE.exists():
            with open(SPEND_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == date.today().isoformat():
                _daily_spend = data
                logger.info(f"Loaded spend from disk: ${data['total_usd']:.4f} ({data['call_count']} calls)")
    except Exception as e:
        logger.warning(f"Could not load spend file: {e}")


def _save_spend_to_disk():
    """Persist current spend to disk."""
    try:
        with open(SPEND_FILE, "w") as f:
            json.dump(_daily_spend, f)
    except Exception as e:
        logger.warning(f"Could not save spend file: {e}")


def _reset_daily_spend_if_needed():
    """Reset spend tracker at the start of each day."""
    today = date.today().isoformat()
    with _spend_lock:
        if _daily_spend["date"] != today:
            _daily_spend["date"] = today
            _daily_spend["total_usd"] = 0.0
            _daily_spend["call_count"] = 0
            _save_spend_to_disk()


def _decompress_for_logging(body_bytes, encoding):
    """
    Decode a Content-Encoded response body to utf-8 text for logging/metering.
    Anthropic typically gzip-compresses responses. If decompression fails for
    any reason, fall back to naive utf-8 decode — logger will then just record
    "non-JSON" rather than crashing.
    """
    try:
        if encoding == "gzip":
            import gzip
            body_bytes = gzip.decompress(body_bytes)
        elif encoding == "deflate":
            import zlib
            try:
                body_bytes = zlib.decompress(body_bytes)
            except zlib.error:
                body_bytes = zlib.decompress(body_bytes, -zlib.MAX_WBITS)
        elif encoding == "br":
            try:
                import brotli  # type: ignore
                body_bytes = brotli.decompress(body_bytes)
            except ImportError:
                pass  # brotli not installed — log will show non-JSON
    except Exception as e:
        logger.warning(f"Decompress ({encoding}) failed: {e}")
    return body_bytes.decode("utf-8", errors="replace")


def _parse_usage_from_sse(body_text):
    """
    Extract (input_tokens, output_tokens, cache_read, cache_create) from an
    Anthropic /v1/messages SSE stream. Anthropic streams emit:
        event: message_start      data: {...,"message":{"usage":{"input_tokens":N,...}}}
        event: message_delta      data: {...,"usage":{"output_tokens":M,...}}
    Input tokens land in message_start; output_tokens in message_delta.
    Cache-hit accounting lives in message_start.usage too.
    Returns zeros if nothing parsable is found.
    """
    in_tok = out_tok = cache_read = cache_create = 0
    if not body_text:
        return in_tok, out_tok, cache_read, cache_create
    for line in body_text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        etype = obj.get("type")
        if etype == "message_start":
            u = (obj.get("message") or {}).get("usage") or {}
            in_tok = u.get("input_tokens", in_tok) or in_tok
            cache_read = u.get("cache_read_input_tokens", cache_read) or cache_read
            cache_create = u.get("cache_creation_input_tokens", cache_create) or cache_create
        elif etype == "message_delta":
            u = obj.get("usage") or {}
            # message_delta carries the *final* output_tokens — take the max
            # just in case multiple deltas appear.
            out_tok = max(out_tok, u.get("output_tokens", 0) or 0)
    return in_tok, out_tok, cache_read, cache_create


def _record_spend(input_tokens, output_tokens,
                  model=None,
                  cache_read_tokens=0,
                  cache_creation_tokens=0):
    """Record token usage and return current daily total."""
    in_price, out_price = _pricing_for(model)
    # Prompt-caching pricing: cache writes cost 1.25× input price, cache reads
    # cost 0.1×. The `input_tokens` reported by the API already *excludes*
    # cached tokens — so bill fresh input + writes + reads separately.
    cost = (
        input_tokens        / 1_000_000 * in_price +
        output_tokens       / 1_000_000 * out_price +
        cache_creation_tokens / 1_000_000 * in_price * 1.25 +
        cache_read_tokens   / 1_000_000 * in_price * 0.10
    )
    with _spend_lock:
        _daily_spend["total_usd"] += cost
        _daily_spend["call_count"] += 1
        _save_spend_to_disk()
        return _daily_spend["total_usd"], _daily_spend["call_count"]


def _check_budget():
    """Check if we are within daily budget. Returns (ok, current_spend)."""
    _reset_daily_spend_if_needed()
    with _spend_lock:
        return _daily_spend["total_usd"] < DAILY_BUDGET_USD, _daily_spend["total_usd"]


# ── Logging ──────────────────────────────────────────────────────────

def _log_api_call(method, path, request_body, response_status, response_body, elapsed_ms,
                  auth_scheme="api_key"):
    """Append API call details to the audit log.

    auth_scheme:
      'api_key' → bill tokens against the daily API-credit budget
      'oauth'   → bill $0 (Max/Pro subscription pays), log for observability
    """
    try:
        timestamp = datetime.utcnow().isoformat()

        req_summary = "non-JSON"
        resp_summary = "non-JSON"
        input_tokens = 0
        output_tokens = 0
        cache_read = 0
        cache_create = 0
        model = "unknown"

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

        # Try JSON first (non-streaming responses); fall back to SSE parse for
        # streamed responses. Before this fix, SSE responses silently scored as
        # $0 — meaning the daily budget never enforced anything at all.
        parsed = False
        try:
            resp_data = json.loads(response_body) if response_body else {}
            stop_reason = resp_data.get("stop_reason", "?")
            usage = resp_data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            cache_read = usage.get("cache_read_input_tokens", 0) or 0
            cache_create = usage.get("cache_creation_input_tokens", 0) or 0
            content_blocks = len(resp_data.get("content", []))
            resp_summary = (f"stop={stop_reason} in={input_tokens} "
                           f"out={output_tokens} blocks={content_blocks} "
                           f"cache_r={cache_read} cache_w={cache_create}")
            parsed = True
        except (json.JSONDecodeError, AttributeError):
            pass

        # Second fallback: some streamed SSE bodies don't start with "event:"
        # in the first 200 chars (they lead with :ping comments, or the proxy
        # read a partial chunk). Try the SSE parser whenever JSON fails and
        # "message_start" is present anywhere in the body.
        if not parsed and response_body and ("event:" in response_body[:500]
                                              or "message_start" in response_body[:2000]):
            in_t, out_t, cr, cc = _parse_usage_from_sse(response_body)
            if in_t or out_t:
                input_tokens, output_tokens = in_t, out_t
                cache_read, cache_create = cr, cc
                resp_summary = (f"stream in={input_tokens} out={output_tokens} "
                               f"cache_r={cache_read} cache_w={cache_create}")

        # Record spend — OAuth calls (Max/Pro subscription) bill $0 against
        # the API-credit budget. Per-model pricing + cache-aware billing for
        # api_key calls.
        if auth_scheme == "oauth":
            daily_total, call_count = _record_spend(0, 0, model=model)
            cost = 0.0
        else:
            daily_total, call_count = _record_spend(
                input_tokens, output_tokens,
                model=model,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_create,
            )
            in_price, out_price = _pricing_for(model)
            cost = (
                input_tokens / 1_000_000 * in_price +
                output_tokens / 1_000_000 * out_price +
                cache_create / 1_000_000 * in_price * 1.25 +
                cache_read / 1_000_000 * in_price * 0.10
            )

        log_line = (
            f"[{timestamp}] {method} {path} -> {response_status} "
            f"({elapsed_ms}ms) | {req_summary} | {resp_summary} | "
            f"auth={auth_scheme} cost=${cost:.4f} daily_total=${daily_total:.4f} "
            f"call_count={call_count}\n"
        )

        with open(API_LOG, "a") as f:
            f.write(log_line)

        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{timestamp}] [api-proxy] API call #{call_count}: "
                    f"${cost:.4f} (daily: ${daily_total:.4f}/${DAILY_BUDGET_USD})\n")

    except Exception as e:
        logger.error(f"Failed to log API call: {e}")


# ── Proxy handler ────────────────────────────────────────────────────

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
                if key.lower() in ("host", "transfer-encoding"):
                    continue
                if UPSTREAM_API_KEY and key.lower() in _CALLER_AUTH_HEADERS:
                    continue  # replaced with the host-held key below
                forward_headers[key] = value
            forward_headers["Host"] = TARGET_HOST
            if UPSTREAM_API_KEY:
                forward_headers["x-api-key"] = UPSTREAM_API_KEY

            conn.request(method, self.path, body=request_body,
                        headers=forward_headers)

            response = conn.getresponse()
            response_body = response.read()

            elapsed_ms = int((time.time() - start) * 1000)

            # Decompress a *copy* of the body for logging/metering purposes.
            # The client still receives the original encoded bytes, so it
            # handles gzip/br itself. This fixes the silent-budget bug:
            # Anthropic returns SSE gzipped, so parsing the raw bytes as
            # utf-8 produced garbage → $0 cost → no budget enforcement.
            encoding = (response.getheader("Content-Encoding") or "").lower().strip()
            body_for_logging = _decompress_for_logging(response_body, encoding)

            # Classify auth: OAuth Bearer tokens (claude-code-cli) bill to the
            # user's Max/Pro subscription, not to API credits. Don't charge
            # them against the daily API budget — only x-api-key calls count.
            # With a host-held key every call is billed to that key.
            if UPSTREAM_API_KEY:
                auth_scheme = "api_key"
            else:
                auth_scheme = "oauth" if str(self.headers.get("Authorization", "")).lower().startswith("bearer ") else "api_key"

            _log_api_call(method, self.path, request_body.decode("utf-8", errors="replace"),
                         response.status, body_for_logging,
                         elapsed_ms, auth_scheme=auth_scheme)

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
            self.send_error(502, "Upstream connection failed")

    def log_message(self, format, *args):
        """Suppress default access log (we log our own way)."""
        pass


# ── Main ─────────────────────────────────────────────────────────────

def main():
    """Start the HTTPS proxy server."""
    _load_spend_from_disk()

    logger.info(f"Starting API proxy on {LISTEN_HOST}:{LISTEN_PORT}")
    logger.info(f"Forwarding to {TARGET_HOST}:{TARGET_PORT}")
    logger.info(f"Daily budget: ${DAILY_BUDGET_USD}")
    logger.info("Upstream auth: " + ("host-held key (PROXY_UPSTREAM_API_KEY)"
                                      if UPSTREAM_API_KEY else "forwarding caller headers"))

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
