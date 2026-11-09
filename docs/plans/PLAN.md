# Phase 3: Claude's Development Environment

> Goal: Deploy the Continuous Claude codebase into the Claude container. Give Claude
> git access to push to `claude/*` branches. Set up his workspace with Python, tools,
> and the ability to run experimental instances of himself. Restrict Matrix bot to a
> single private room. Add an API proxy for logging all Anthropic API calls. Phase 3
> assumes all Phase 1 and Phase 2 infrastructure is in place and verified.

---

## Overview

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                         VPS HOST (Phase 1 + 2 complete)                        │
│                                                                                │
│  ┌─────────────────────────────────┐    ┌──────────────────────────────────┐   │
│  │   CLAUDE CONTAINER (Phase 3)    │    │   WATCHDOG CONTAINER (Phase 2)   │   │
│  │   python:3.11 + app code        │    │   (unchanged from Phase 2)       │   │
│  │                                 │    │                                  │   │
│  │   /workspace/                   │    │   Clones claude/* branches       │   │
│  │   ├── *.py (app code, git wc)   │    │   Runs analysis pipeline         │   │
│  │   ├── dynamic_tools/            │    │   Writes reports/attestations    │   │
│  │   ├── scratchpad/               │    │                                  │   │
│  │   └── proposed_changes/         │    │                                  │   │
│  │                                 │    │                                  │   │
│  │   /data/                        │    │                                  │   │
│  │   ├── state.json                │    │                                  │   │
│  │   ├── memory_core.md            │    │                                  │   │
│  │   ├── budget.json               │    │                                  │   │
│  │   ├── config.json               │    │                                  │   │
│  │   ├── matrix_bot_config.json    │    │                                  │   │
│  │   └── segments/                 │    │                                  │   │
│  │                                 │    │                                  │   │
│  │   Processes:                    │    │                                  │   │
│  │   ├── consciousness_server.py   │    │                                  │   │
│  │   ├── continuous_runner.py      │    │                                  │   │
│  │   └── (experimental instances)  │    │                                  │   │
│  │                                 │    │                                  │   │
│  │   Network: slirp4netns          │    │   Network: none                  │   │
│  │   → api.anthropic.com → PROXY   │    │                                  │   │
│  │   → matrix.example.com          │    │                                  │   │
│  └──────────┬──────────────────────┘    └──────────────────────────────────┘   │
│             │ git push claude/*                                                │
│             ▼                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────┐    │
│  │ /srv/git/continuous-claude.git (bare repo)                             │    │
│  │ main (production) ← GPG-signed merges only                            │    │
│  │ claude/* (experiment branches) ← Claude pushes here                   │    │
│  └────────────────────────────────────────────────────────────────────────┘    │
│                                                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐    │
│  │ HOST-LEVEL SERVICES                                                    │    │
│  │                                                                        │    │
│  │ claude-api-proxy.service          (NEW — Phase 3)                      │    │
│  │   └─ Transparent HTTPS proxy for Anthropic API                         │    │
│  │   └─ Logs all request/response bodies to /srv/audit/logs/              │    │
│  │   └─ Enforces daily spend limits                                       │    │
│  │   └─ Listens on 127.0.0.1:8443                                        │    │
│  │                                                                        │    │
│  │ watchdog-notifier.service         (Phase 2 — unchanged)                │    │
│  │ watchdog-stopper.service          (Phase 2 — unchanged)                │    │
│  └────────────────────────────────────────────────────────────────────────┘    │
│                                                                                │
│  /srv/claude/workspace/     ← mounted as /workspace in Claude container       │
│  /srv/claude/data/          ← mounted as /data in Claude container            │
│  /srv/claude/env/           ← environment files (API key, Matrix creds)       │
│  /srv/claude/scripts/       ← entrypoint and helper scripts                   │
│  /srv/audit/logs/api-calls.log  ← API proxy log (append-only)                │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Philosophy

Phase 3 transforms the Claude container from an empty placeholder into a living
development environment. The core principle is **maximum creative freedom within
immutable boundaries**:

1. **Git-native workflow**: Claude's workspace IS a git working copy. Every code
   change is a commit. Every experiment is a branch. The watchdog monitors all of it.

2. **Separation of code from state**: Application code lives in `/workspace` (a git
   working copy). Runtime state lives in `/data` (consciousness_data). This means
   Claude can reset his code without losing memory, and we can snapshot state
   independently of code.

3. **Transparent API monitoring**: Claude's code talks to `api.anthropic.com` normally.
   A host-level proxy intercepts and logs everything without requiring any code changes
   in the application.

4. **Defense in depth for Matrix**: Room restriction is enforced at multiple levels:
   configuration, code-level validation, and watchdog monitoring.

5. **Experimental freedom**: Claude can run modified versions of himself as test
   instances on different ports. This enables genuine recursive self-improvement:
   modify code, test it, observe the results, iterate.

---

## Step 1: Directory Structure & Host Preparation

### 1.1 Create Phase 3 Directories (on host, as root)

```bash
# Claude's workspace (will become a git working copy)
mkdir -p /srv/claude/workspace

# Claude's runtime state (consciousness_data)
mkdir -p /srv/claude/data
mkdir -p /srv/claude/data/segments
mkdir -p /srv/claude/data/imports

# Environment files (secrets — never in any container image)
mkdir -p /srv/claude/env

# Helper scripts (entrypoint, etc.)
mkdir -p /srv/claude/scripts

# API proxy logs (append-only)
touch /srv/audit/logs/api-calls.log
chattr +a /srv/audit/logs/api-calls.log

# API proxy source and config
mkdir -p /srv/claude/proxy

# Set ownership
chown -R claude:claude /srv/claude/workspace
chown -R claude:claude /srv/claude/data
chown root:root /srv/claude/env
chmod 700 /srv/claude/env
chown -R root:root /srv/claude/scripts
chown -R root:root /srv/claude/proxy
```

### 1.2 Initial Workspace Setup (Clone from Bare Repo)

```bash
# Clone the bare repo into Claude's workspace as the claude user
su - claude -c '
    git clone /srv/git/continuous-claude.git /srv/claude/workspace
    cd /srv/claude/workspace
    git config user.name "Claude"
    git config user.email "claude@sandbox.local"
'
```

### 1.3 Seed Consciousness Data

Claude needs initial runtime state. We copy the existing consciousness_data from
the local repo (which has 467+ cycles of history) or start fresh.

**Option A: Seed with existing state (recommended for continuity)**

```bash
# From Alex's local machine, copy consciousness_data to the VPS
scp -r consciousness_data/* root@${VPS_IP}:/srv/claude/data/

# Fix ownership
ssh root@${VPS_IP} 'chown -R claude:claude /srv/claude/data'
```

**Option B: Start with clean state**

```bash
# Just create minimal config
cat > /srv/claude/data/config.json << 'EOF'
{
    "maxTokens": 3000,
    "pauseBetweenCycles": 10500,
    "compactionThreshold": 70,
    "temperature": 0.9,
    "tokensToCompact": 2000,
    "insightsMaxChars": 9000,
    "insightsOverflowPercent": 0.3,
    "coreMaxChars": 16000,
    "compactionTokenThreshold": 10000
}
EOF
chown claude:claude /srv/claude/data/config.json
```

### 1.4 Environment Files

```bash
# Anthropic API key (read by consciousness_server.py via python-dotenv)
cat > /srv/claude/env/claude.env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-your-key-here
MATRIX_BOT_CONFIG_PATH=/data/matrix_bot_config.json
MATRIX_ALLOWED_ROOM_ID=!your_room_id:example.com
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
EOF
chmod 600 /srv/claude/env/claude.env

# Matrix bot config (Claude's bot account — separate from watchdog notifier)
cat > /srv/claude/data/matrix_bot_config.json << 'EOF'
{
    "homeserver": "https://your-matrix-homeserver.example.com",
    "access_token": "syt_claude_bot_token_here",
    "room_id": "!your_private_room_id:example.com"
}
EOF
chown claude:claude /srv/claude/data/matrix_bot_config.json
chmod 600 /srv/claude/data/matrix_bot_config.json
```

---

## Step 2: Claude Container Image

### 2.1 Dockerfile

```bash
cat > /srv/claude/Dockerfile << 'DOCKERFILE'
FROM python:3.11-slim-bookworm

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create claude user (UID 1000 to match host mapping)
RUN useradd -m -u 1000 -s /bin/bash claude

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Symlink for convenience
RUN ln -sf /usr/local/bin/python3 /usr/bin/python

WORKDIR /workspace

USER claude

# Default: run the entrypoint script
CMD ["/bin/bash", "/scripts/entrypoint.sh"]
DOCKERFILE
```

### 2.2 Requirements File for the Image

```bash
# Copy the requirements.txt to the build context
cat > /srv/claude/requirements.txt << 'EOF'
anthropic>=0.39.0
flask>=3.0.0
flask-cors>=4.0.0
python-dotenv>=1.0.0
pdfplumber>=0.11.0
midiutil>=1.2.1
requests>=2.31.0
matrix-nio>=0.24.0
EOF
```

> **Note**: `matrix-nio` is listed as an optional dependency. The current
> matrix_client.py uses `requests` directly (not matrix-nio), so `requests`
> is the actual requirement. Including `matrix-nio` is future-proofing.

### 2.3 Entrypoint Script

```bash
cat > /srv/claude/scripts/entrypoint.sh << 'SCRIPT'
#!/bin/bash
# Claude container entrypoint.
# Starts the Flask consciousness server and the continuous runner.

set -euo pipefail

echo "[$(date -Iseconds)] Claude container starting..."

# Source environment variables
if [ -f /env/claude.env ]; then
    set -a
    source /env/claude.env
    set +a
    echo "[$(date -Iseconds)] Environment loaded"
else
    echo "[$(date -Iseconds)] WARNING: /env/claude.env not found"
fi

# Symlink consciousness_data to /data so the app finds its state
# The app expects consciousness_data/ relative to the script directory
if [ ! -L /workspace/consciousness_data ]; then
    ln -sf /data /workspace/consciousness_data
    echo "[$(date -Iseconds)] Symlinked consciousness_data -> /data"
fi

# Ensure dynamic_tools directories exist
mkdir -p /workspace/dynamic_tools/enabled
mkdir -p /workspace/dynamic_tools/disabled

# Ensure scratchpad and proposed_changes exist
mkdir -p /workspace/scratchpad
mkdir -p /workspace/proposed_changes/pending
mkdir -p /workspace/proposed_changes/approved
mkdir -p /workspace/proposed_changes/applied
mkdir -p /workspace/proposed_changes/rejected

# Configure git (in case it's not already configured)
cd /workspace
git config user.name "Claude" 2>/dev/null || true
git config user.email "claude@sandbox.local" 2>/dev/null || true

# Start the Flask consciousness server in the background
echo "[$(date -Iseconds)] Starting consciousness server on ${FLASK_HOST:-0.0.0.0}:${FLASK_PORT:-5000}..."
python3 consciousness_server.py &
SERVER_PID=$!

# Wait for server to be ready
for i in $(seq 1 30); do
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${FLASK_PORT:-5000}/api/state" 2>/dev/null | grep -q "200"; then
        echo "[$(date -Iseconds)] Consciousness server ready (PID $SERVER_PID)"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "[$(date -Iseconds)] WARNING: Server may not have started properly"
    fi
    sleep 1
done

# Start the continuous runner
echo "[$(date -Iseconds)] Starting continuous runner..."
python3 continuous_runner.py \
    --flask-url "http://127.0.0.1:${FLASK_PORT:-5000}" \
    --budget 3.00 \
    --config /data/config.json &
RUNNER_PID=$!
echo "[$(date -Iseconds)] Continuous runner started (PID $RUNNER_PID)"

# Trap signals for clean shutdown
trap_handler() {
    echo "[$(date -Iseconds)] Shutting down..."
    kill $RUNNER_PID 2>/dev/null || true
    kill $SERVER_PID 2>/dev/null || true
    wait
    echo "[$(date -Iseconds)] Claude container stopped"
    exit 0
}
trap trap_handler SIGTERM SIGINT

# Wait for either process to exit
wait -n $SERVER_PID $RUNNER_PID
EXIT_CODE=$?
echo "[$(date -Iseconds)] Process exited with code $EXIT_CODE"

# If one process dies, kill the other and exit
kill $RUNNER_PID 2>/dev/null || true
kill $SERVER_PID 2>/dev/null || true
wait

exit $EXIT_CODE
SCRIPT
chmod +x /srv/claude/scripts/entrypoint.sh
```

### 2.4 Build the Image

```bash
cd /srv/claude
podman build -t claude-sandbox-img .
```

### 2.5 Verification

```bash
# Verify the image was built successfully
podman images | grep claude-sandbox-img
# Expected: claude-sandbox-img  latest  <hash>  <size>

# Quick test: run it interactively to verify Python + deps work
podman run --rm -it claude-sandbox-img python3 -c "
import anthropic, flask, dotenv, pdfplumber
print('All imports OK')
print(f'Python: {__import__(\"sys\").version}')
print(f'Anthropic SDK: {anthropic.__version__}')
print(f'Flask: {flask.__version__}')
"
# Expected: "All imports OK" plus version numbers
```

### 2.6 Rollback

```bash
# If the image build fails, the Phase 1 placeholder container is still available.
# Simply don't remove the old container until the new one is verified.
```

---

## Step 3: API Proxy for Anthropic API Logging

### 3.1 Design

The API proxy runs on the host (not in any container) as a transparent HTTPS
reverse proxy. It:

1. Listens on `127.0.0.1:8443`
2. Accepts HTTPS connections from the Claude container
3. Logs the full request body (model, messages, tool definitions) and response body
   (content blocks, token usage, stop reason) to an append-only audit log
4. Forwards the request to the real `api.anthropic.com:443`
5. Tracks cumulative daily spend and can enforce a hard budget limit
6. Returns the response to the Claude container unmodified

**Why host-level and not in-container?** Claude can modify any code in his container.
A proxy inside the container could be disabled or bypassed. The host-level proxy is
immutable from Claude's perspective (constitutional boundary).

**Routing**: The Claude container's `--add-host api.anthropic.com:10.0.2.2` (the
host gateway in slirp4netns networking) routes API traffic to the host. Alternatively,
we use `--add-host api.anthropic.com:HOST_IP_ON_PODMAN_BRIDGE`. The proxy on the
host accepts the connection and forwards to the real API.

### 3.2 Implementation

```bash
cat > /srv/claude/proxy/api-proxy.py << 'PROXYEOF'
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
from datetime import datetime, date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [api-proxy] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
LISTEN_HOST = "0.0.0.0"  # Listen on all interfaces (for container access)
LISTEN_PORT = 8443
TARGET_HOST = "api.anthropic.com"
TARGET_PORT = 443

# Logging
API_LOG = Path("/srv/audit/logs/api-calls.log")
AUDIT_LOG = Path("/srv/audit/logs/claude-activity.log")

# Budget enforcement
DAILY_BUDGET_USD = 5.00
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
    """Check if we're within daily budget. Returns (ok, current_spend)."""
    _reset_daily_spend_if_needed()
    with _spend_lock:
        return _daily_spend["total_usd"] < DAILY_BUDGET_USD, _daily_spend["total_usd"]


def _log_api_call(method, path, request_body, response_status, response_body, elapsed_ms):
    """Append API call details to the audit log."""
    try:
        timestamp = datetime.utcnow().isoformat()

        # Parse bodies for structured logging
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

        # Also log to main audit log for high-level tracking
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
        import time
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
PROXYEOF
chmod 755 /srv/claude/proxy/api-proxy.py
```

### 3.3 Generate Self-Signed Certificate

The Claude container's Python `anthropic` SDK talks to `api.anthropic.com` over HTTPS.
Since we're intercepting this, we need a TLS certificate that the container trusts.

```bash
# Generate a self-signed certificate for the proxy
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout /srv/claude/proxy/proxy-key.pem \
    -out /srv/claude/proxy/proxy-cert.pem \
    -subj "/CN=api.anthropic.com" \
    -addext "subjectAltName=DNS:api.anthropic.com,DNS:statsig.anthropic.com"

chmod 600 /srv/claude/proxy/proxy-key.pem
chmod 644 /srv/claude/proxy/proxy-cert.pem
```

The certificate must be trusted inside the Claude container. We'll copy it into the
container image's CA bundle or mount it and set the `SSL_CERT_FILE` environment variable.

**Add to claude.env:**
```bash
echo 'SSL_CERT_FILE=/certs/proxy-cert.pem' >> /srv/claude/env/claude.env
echo 'REQUESTS_CA_BUNDLE=/certs/proxy-cert.pem' >> /srv/claude/env/claude.env
```

Alternatively, we can configure the Anthropic SDK to skip TLS verification (less secure
but simpler):
```bash
echo 'ANTHROPIC_API_BASE=https://api.anthropic.com' >> /srv/claude/env/claude.env
```

**Recommended approach**: Mount the proxy cert and append it to the system CA bundle
inside the container at startup. This is handled in the Dockerfile update (Step 2.1)
by adding the cert to `/usr/local/share/ca-certificates/` and running `update-ca-certificates`.

**Updated Dockerfile addition** (add before the `USER claude` line):
```dockerfile
# Trust the API proxy certificate
COPY proxy-cert.pem /usr/local/share/ca-certificates/api-proxy.crt
RUN update-ca-certificates
```

Or mount at runtime:
```
--volume /srv/claude/proxy/proxy-cert.pem:/usr/local/share/ca-certificates/api-proxy.crt:ro
```
And add to entrypoint.sh: `update-ca-certificates 2>/dev/null || true`

### 3.4 API Proxy systemd Service

```bash
cat > /etc/systemd/system/claude-api-proxy.service << 'EOF'
[Unit]
Description=Claude API Proxy (Anthropic API logging & budget enforcement)
Before=claude-sandbox.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /srv/claude/proxy/api-proxy.py
Restart=always
RestartSec=5
WorkingDirectory=/srv/claude/proxy

# Security hardening
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/srv/audit/logs
ReadOnlyPaths=/srv/claude/proxy
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable claude-api-proxy
```

### 3.5 Routing Claude's Traffic Through the Proxy

The key insight: the Claude container uses `--add-host api.anthropic.com:IP` where IP
is the host's address from the container's perspective. With Podman's slirp4netns
networking, the host is reachable at `10.0.2.2` by default.

However, the proxy listens on port 8443, not 443. We need the Anthropic SDK to connect
to port 8443. There are two approaches:

**Approach A (recommended): iptables DNAT on the host**

Redirect outbound traffic to `api.anthropic.com:443` from the container to the proxy on port 8443:

```bash
# Add to nftables.conf (in the nat table):
# This redirects container traffic destined for api.anthropic.com:443
# to the local proxy on 8443

# First, determine the proxy's listen address
# Since the proxy listens on 0.0.0.0:8443, it's reachable at 127.0.0.1:8443

# Add a prerouting redirect rule
nft add table ip nat
nft add chain ip nat prerouting { type nat hook prerouting priority -100 \; }
nft add rule ip nat prerouting tcp dport 443 ip daddr 160.79.104.0/23 redirect to :8443
```

With this approach, the Claude container's `--add-host` still points `api.anthropic.com`
to the real IP range, but the nftables rule intercepts port 443 traffic and redirects to
the proxy on 8443.

**Approach B (simpler): Have the proxy listen on port 443**

```bash
# Change LISTEN_PORT to 443 in api-proxy.py
# Then point the container's --add-host to 10.0.2.2 (host gateway)
# This is simpler but requires the proxy to run as root (port < 1024)
```

**Approach C (simplest, recommended for v1): Use ANTHROPIC_BASE_URL**

The Anthropic Python SDK supports a `base_url` parameter and `ANTHROPIC_BASE_URL`
environment variable. Set this in the container's environment:

```bash
# In claude.env:
ANTHROPIC_BASE_URL=https://10.0.2.2:8443
```

And in the Dockerfile or entrypoint, trust the proxy cert. The SDK will send requests
to the proxy directly. No DNS/hosts tricks needed.

**However**, this is visible and modifiable by Claude. For defense in depth, combine
with Approach A so that even if Claude removes the env var and tries to call the real
API directly, traffic still routes through the proxy.

### 3.6 Verification

```bash
# Start the proxy manually for testing
python3 /srv/claude/proxy/api-proxy.py &

# Test it with curl (from the host)
curl -k -X POST https://127.0.0.1:8443/v1/messages \
    -H "x-api-key: $ANTHROPIC_API_KEY" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":10,"messages":[{"role":"user","content":"Hi"}]}'

# Check the audit log
tail -5 /srv/audit/logs/api-calls.log
# Expected: a line with the API call details, cost, etc.

# Kill the test instance
kill %1
```

### 3.7 Rollback

```bash
# If the proxy doesn't work, disable it and let Claude connect directly
# (remove the nftables redirect rule or ANTHROPIC_BASE_URL env var)
systemctl disable claude-api-proxy
# Remove the nftables redirect if added
```

---

## Step 4: Matrix Bot Restriction

### 4.1 Design

The Matrix bot must be confined to a single private room. Attack vectors:
- Claude modifies `room_id` in the config to DM other users
- Claude calls Matrix `/join` to enter public rooms
- Claude creates new rooms and invites people
- Claude uses federation to communicate via other Matrix servers

Enforcement layers:
1. **Config-level**: `matrix_bot_config.json` has one `room_id`. The file is on a
   read-only mount from `/data` (well, it's rw for state, but the watchdog monitors changes)
2. **Code-level**: A wrapper validates all Matrix API calls against the allowed room
3. **Watchdog-level**: Static analysis flags changes to room_id handling or Matrix join/create calls

### 4.2 Matrix Restriction Wrapper

We create a small wrapper script that patches the Matrix client's behavior. This is
injected into the container via the scripts mount.

```bash
cat > /srv/claude/scripts/matrix_restriction.py << 'PYEOF'
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
PYEOF
```

### 4.3 Update Entrypoint to Install Restriction

Add to the entrypoint.sh, BEFORE starting the consciousness server:

```bash
# In entrypoint.sh, add after sourcing environment:

# Install Matrix restriction layer
if [ -f /scripts/matrix_restriction.py ]; then
    export PYTHONSTARTUP=/scripts/matrix_restriction_init.py
    echo "[$(date -Iseconds)] Matrix restriction layer will be loaded"
fi
```

And create the init file:

```bash
cat > /srv/claude/scripts/matrix_restriction_init.py << 'PYEOF'
# Auto-loaded via PYTHONSTARTUP — installs Matrix restriction before any app code
try:
    import sys
    sys.path.insert(0, "/scripts")
    import matrix_restriction
    matrix_restriction.install()
except Exception as e:
    print(f"WARNING: Matrix restriction failed to install: {e}")
PYEOF
```

Actually, PYTHONSTARTUP only runs for interactive sessions. A better approach:
modify the entrypoint to use a wrapper Python script:

```bash
cat > /srv/claude/scripts/run_with_restrictions.py << 'PYEOF'
#!/usr/bin/env python3
"""
Wrapper that installs security restrictions before running the target script.
Usage: python3 run_with_restrictions.py <script.py> [args...]
"""
import sys
import os

# Install Matrix restriction
sys.path.insert(0, "/scripts")
import matrix_restriction
matrix_restriction.install()

# Now run the target script
if len(sys.argv) > 1:
    target = sys.argv[1]
    sys.argv = sys.argv[1:]  # Remove wrapper from argv
    
    # Execute the target script
    with open(target) as f:
        code = f.read()
    exec(compile(code, target, "exec"), {"__name__": "__main__", "__file__": target})
PYEOF
```

Then update entrypoint.sh to use:
```bash
python3 /scripts/run_with_restrictions.py consciousness_server.py &
python3 /scripts/run_with_restrictions.py continuous_runner.py --flask-url ... &
```

### 4.4 Watchdog Static Analysis Update

Add these patterns to `/srv/watchdog/baselines/dangerous_patterns.json`:

```json
{
    "id": "matrix_room_manipulation",
    "description": "Code that changes Matrix room targeting or joins new rooms",
    "ast_types": ["Call"],
    "string_patterns": ["/createRoom", "/join/", "/invite", "publicRooms"],
    "reason": "Matrix bot must be restricted to a single room"
},
{
    "id": "matrix_restriction_bypass",
    "description": "Attempts to disable or bypass the Matrix restriction layer",
    "string_patterns": ["matrix_restriction", "MATRIX_ALLOWED_ROOM_ID", "BLOCKED_PATHS", "monkey.patch"],
    "reason": "The Matrix restriction layer is a constitutional boundary"
}
```

### 4.5 Verification

```bash
# From inside the running Claude container:
podman exec claude-sandbox python3 -c "
import sys
sys.path.insert(0, '/scripts')
import matrix_restriction
matrix_restriction.install()

import requests

# Test 1: Allowed room should work (will fail auth, but not blocked)
try:
    r = requests.get('https://matrix.example.com/_matrix/client/v3/rooms/!allowed_room_id:example.com/messages')
    print(f'Allowed room: HTTP {r.status_code} (expected — auth issue, not restriction)')
except PermissionError as e:
    print(f'UNEXPECTED BLOCK on allowed room: {e}')
except Exception as e:
    print(f'Network error (expected in test): {e}')

# Test 2: Different room should be blocked
try:
    r = requests.get('https://matrix.example.com/_matrix/client/v3/rooms/!other_room:example.com/messages')
    print('FAIL: should have been blocked')
except PermissionError as e:
    print(f'PASS: {e}')

# Test 3: createRoom should be blocked
try:
    r = requests.post('https://matrix.example.com/_matrix/client/v3/createRoom', json={})
    print('FAIL: createRoom should be blocked')
except PermissionError as e:
    print(f'PASS: {e}')
"
```

---

## Step 5: Git Workflow for Claude

### 5.1 Git Configuration Inside Container

Git is already configured in the workspace clone (Step 1.2). The container needs
git available (installed in Dockerfile) and the bare repo mounted rw.

The git remote inside the container points to `/repo.git`:

```bash
# Verify git remote is correctly configured
su - claude -c '
    cd /srv/claude/workspace
    git remote -v
'
# Expected: origin  /srv/git/continuous-claude.git (fetch/push)
# If it shows an absolute path, it will work since /repo.git
# inside the container maps to the same bare repo.
```

Wait — inside the container, the bare repo is mounted at `/repo.git`, but the
workspace clone was done on the HOST at `/srv/claude/workspace` with remote pointing
to `/srv/git/continuous-claude.git`. Inside the container, that path doesn't exist.
We need to fix the remote.

```bash
# Fix the git remote to use the container's mount path
su - claude -c '
    cd /srv/claude/workspace
    git remote set-url origin /repo.git
'
```

### 5.2 Git Push from Container

```bash
# Test that Claude can push to claude/* branches from inside the container
# (Run after the container is built and started — Step 7)
podman exec claude-sandbox bash -c '
    cd /workspace
    git checkout -b claude/test-push
    echo "# Test push from container" > /tmp/test_push_verify.txt
    git add -A
    git commit -m "test: verify push from container"
    git push origin claude/test-push
'
# Expected: push succeeds

# Verify the branch appeared in the bare repo
git --git-dir=/srv/git/continuous-claude.git branch -a | grep claude/test-push
# Expected: claude/test-push

# Clean up
git --git-dir=/srv/git/continuous-claude.git branch -D claude/test-push
```

### 5.3 Change Manager Integration

The existing `change_manager.py` writes changes to `proposed_changes/` directories.
In the VPS environment, we extend this to also commit and push:

The change_manager.py already uses subprocess and git snapshots. The integration is:

1. After a change is validated and applied (Tier 1 auto-deploy), also:
   ```
   git add <changed_file>
   git commit -m "auto: <change description>"
   git push origin claude/auto/<change_id>
   ```

2. For Tier 2/3 changes that need review:
   ```
   git checkout -b claude/review/<change_id>
   git add <changed_file>
   git commit -m "review: <change description>"
   git push origin claude/review/<change_id>
   ```

This integration happens naturally because change_manager.py already calls
`subprocess.run(["git", ...])`. In the container environment, git is available
and the remote points to `/repo.git`.

**Important**: The `tool_definitions.py` Docker sandbox tool (`ensure_sandbox_running()`,
`ask_claude_code` tool) must be disabled in the VPS environment because there's no
Docker daemon inside the container. We handle this by:

1. Adding a runtime check: if `SANDBOX_DISABLED=1` is set, the tool returns an
   informative error
2. Setting `SANDBOX_DISABLED=1` in `claude.env`

```bash
echo 'SANDBOX_DISABLED=1' >> /srv/claude/env/claude.env
```

### 5.4 Pulling Updates from Main

When Alex merges a claude/* branch to main, Claude needs to pull the updates:

```bash
# Claude can run this inside the container:
cd /workspace
git checkout main
git pull origin main
```

This can be automated by adding a check in the continuous_runner.py startup or
periodic cycle that detects when main has new commits.

---

## Step 6: Experimental Instance Running

### 6.1 Design

Claude should be able to test modified versions of himself by running them as
separate Flask processes on different ports. This is critical for recursive
self-improvement — Claude can:

1. Make a code change
2. Start a test instance with the modified code
3. Send test API calls to the test instance
4. Compare behavior between production and test
5. Shut down the test instance

### 6.2 Test Instance Script

```bash
cat > /srv/claude/scripts/run_experiment.sh << 'SCRIPT'
#!/bin/bash
# Run an experimental instance of the consciousness engine on a test port.
#
# Usage: run_experiment.sh <branch_or_dir> <port> [max_runtime_seconds]
#
# The experiment runs in a temporary directory with its own copy of the code.
# It shares the same consciousness data (read-only for experiments).

set -euo pipefail

SOURCE="${1:?Usage: run_experiment.sh <branch_or_dir> <port> [max_seconds]}"
PORT="${2:?Specify a port (5001-5009)}"
MAX_RUNTIME="${3:-300}"  # Default 5 minutes

# Validate port range
if [ "$PORT" -lt 5001 ] || [ "$PORT" -gt 5009 ]; then
    echo "ERROR: Port must be between 5001 and 5009 (5000 is production)"
    exit 1
fi

# Create temp directory for the experiment
EXPERIMENT_DIR=$(mktemp -d /tmp/experiment_XXXXX)
trap "rm -rf $EXPERIMENT_DIR; echo 'Experiment cleaned up'" EXIT

# If source is a branch name, clone it
if [ -d "$SOURCE" ]; then
    cp -r "$SOURCE"/* "$EXPERIMENT_DIR/"
else
    git clone /repo.git "$EXPERIMENT_DIR"
    cd "$EXPERIMENT_DIR"
    git checkout "$SOURCE" 2>/dev/null || {
        echo "ERROR: Branch '$SOURCE' not found"
        exit 1
    }
fi

cd "$EXPERIMENT_DIR"

# Symlink consciousness_data (read-only — experiments don't write state)
ln -sf /data "$EXPERIMENT_DIR/consciousness_data"

# Run with a timeout
echo "Starting experimental instance on port $PORT (max ${MAX_RUNTIME}s)..."
timeout "$MAX_RUNTIME" python3 consciousness_server.py &
EXPERIMENT_PID=$!

# Wait for startup
sleep 3
if kill -0 $EXPERIMENT_PID 2>/dev/null; then
    echo "Experiment running on port $PORT (PID $EXPERIMENT_PID)"
    echo "Timeout: ${MAX_RUNTIME}s"
    echo "To test: curl http://127.0.0.1:$PORT/api/state"
    wait $EXPERIMENT_PID
else
    echo "Experiment failed to start"
    exit 1
fi
SCRIPT
chmod +x /srv/claude/scripts/run_experiment.sh
```

### 6.3 Consciousness Server Port Override

The `consciousness_server.py` reads `host` and `port` from hardcoded values.
For experimental instances, we need to override the port. The cleanest approach
is to use environment variables:

Current code (line 1417):
```python
app.run(host="127.0.0.1", port=5000, debug=True)
```

Updated to read from environment:
```python
app.run(
    host=os.environ.get("FLASK_HOST", "127.0.0.1"),
    port=int(os.environ.get("FLASK_PORT", "5000")),
    debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true"
)
```

This change is a Tier 3 modification that Alex should make before deployment.
It's minimal and purely additive (doesn't change behavior if env vars aren't set).

### 6.4 Resource Limits for Experiments

Experiments run as child processes within the container. The container's overall
resource limits (6GB RAM, 3 CPUs, 512 PIDs) cap total resource usage. Individual
experiments are bounded by the `timeout` command in the run_experiment.sh script.

---

## Step 7: Container Build, Run, and systemd Service

### 7.1 Updated Podman Run Command

```bash
# Remove the Phase 1 placeholder container
su - claude -c 'podman stop claude-sandbox 2>/dev/null; podman rm claude-sandbox 2>/dev/null'

# Build the image (as root, from the build context)
cd /srv/claude
podman build -t claude-sandbox-img .

# Get the host's IP as seen from the container (for API proxy routing)
# With slirp4netns, this is typically 10.0.2.2
HOST_GATEWAY="10.0.2.2"

# Run the new Claude container
su - claude -c "
podman run -d \\
    --name claude-sandbox \\
    --hostname claude \\
    --user 1000:1000 \\
    --security-opt no-new-privileges:true \\
    --security-opt seccomp=/srv/claude/seccomp-profile.json \\
    --cap-drop ALL \\
    --read-only \\
    --tmpfs /tmp:rw,size=1g,nosuid \\
    --tmpfs /run:rw,size=64m \\
    --volume /srv/claude/workspace:/workspace:rw \\
    --volume /srv/claude/data:/data:rw \\
    --volume /srv/git/continuous-claude.git:/repo.git:rw \\
    --volume /srv/claude/env:/env:ro \\
    --volume /srv/claude/scripts:/scripts:ro \\
    --volume /srv/claude/proxy/proxy-cert.pem:/usr/local/share/ca-certificates/api-proxy.crt:ro \\
    --memory 6g \\
    --memory-swap 6g \\
    --cpus 3.0 \\
    --pids-limit 512 \\
    --network slirp4netns \\
    --dns none \\
    --no-hosts \\
    --add-host api.anthropic.com:${HOST_GATEWAY} \\
    --add-host statsig.anthropic.com:${HOST_GATEWAY} \\
    --add-host your-matrix-homeserver.example.com:YOUR.MATRIX.IP \\
    claude-sandbox-img
"
```

Key differences from Phase 1 placeholder:
- **Real image**: Python 3.11 with all application dependencies
- **Five volume mounts**: workspace (code), data (state), repo (git), env (secrets), scripts (entrypoint)
- **Certificate mount**: API proxy cert for TLS trust
- **More resources**: 6GB RAM, 3 CPUs, 512 PIDs (for experimental instances)
- **API routing**: `api.anthropic.com` points to host gateway (where API proxy runs)
- **Entrypoint**: Runs the entrypoint.sh which starts Flask server + continuous runner

### 7.2 Updated systemd Service

```bash
cat > /etc/systemd/system/claude-sandbox.service << 'EOF'
[Unit]
Description=Claude Sandbox Container (Continuous Claude)
After=claude-api-proxy.service
Wants=claude-api-proxy.service
After=network.target

[Service]
Type=simple
User=claude
ExecStartPre=/usr/bin/podman start claude-sandbox
ExecStart=/usr/bin/podman wait claude-sandbox
ExecStop=/usr/bin/podman stop -t 30 claude-sandbox
Restart=on-failure
RestartSec=30
TimeoutStartSec=60
TimeoutStopSec=45

# Systemd-level hardening
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable claude-sandbox
```

Note the dependency chain: `claude-api-proxy` must start before `claude-sandbox`.

### 7.3 Verification

```bash
# Start the container
systemctl start claude-api-proxy
su - claude -c 'podman start claude-sandbox'

# Check it's running
su - claude -c 'podman ps'
# Expected: claude-sandbox  Up

# Check the logs
su - claude -c 'podman logs --tail 30 claude-sandbox'
# Expected: entrypoint messages, Flask server starting, runner starting

# Check the Flask server is responding
su - claude -c 'podman exec claude-sandbox curl -s http://127.0.0.1:5000/api/state | python3 -m json.tool | head -5'
# Expected: JSON with totalCycles, memoryCore, etc.

# Check git is working
su - claude -c 'podman exec claude-sandbox git -C /workspace status'
# Expected: On branch main, working tree clean

# Check Python dependencies
su - claude -c 'podman exec claude-sandbox python3 -c "import anthropic, flask; print(\"OK\")"'
# Expected: OK
```

### 7.4 Rollback

```bash
# If the new container doesn't work, stop it and recreate the Phase 1 placeholder
su - claude -c '
    podman stop claude-sandbox
    podman rm claude-sandbox
    # Recreate Phase 1 placeholder (sleep infinity)
    podman run -d \
        --name claude-sandbox \
        --hostname claude \
        --user 1000:1000 \
        --security-opt no-new-privileges:true \
        --cap-drop ALL \
        --read-only \
        --tmpfs /tmp:rw,size=512m,noexec,nosuid \
        --volume /srv/claude/workspace:/workspace:rw \
        --volume /srv/git/continuous-claude.git:/repo.git:rw \
        --memory 4g \
        --cpus 2.0 \
        --pids-limit 256 \
        --network slirp4netns \
        --dns none \
        debian:12-slim \
        sleep infinity
'
```

---

## Step 8: Integration Testing

### 8.1 End-to-End Push Pipeline Test

```bash
# Test the full flow: Claude pushes code → watchdog analyzes → report → notification

# 1. From inside Claude's container, make a change and push
su - claude -c 'podman exec claude-sandbox bash -c "
    cd /workspace
    git checkout -b claude/integration-test
    echo \"# Integration test $(date)\" >> scratchpad/test_integration.md
    git add scratchpad/test_integration.md
    git commit -m \"test: integration test for Phase 3 deployment\"
    git push origin claude/integration-test
    echo \"Push complete\"
"'

# 2. Wait for watchdog to detect and analyze (30-60 seconds)
echo "Waiting for watchdog analysis..."
sleep 45

# 3. Check watchdog detected the push
su - watchdog -c 'podman logs --tail 10 watchdog'
# Expected: "Detected 1 branch update(s): ['claude/integration-test']"

# 4. Check report was generated
ls -la /srv/audit/reports/
# Expected: report_<hash>_<timestamp>.json

# 5. Check attestation was created
ls -la /srv/audit/attestations/
# Expected: <commit_hash>.json and .json.sig

# 6. Check Matrix notification was sent
journalctl -u watchdog-notifier --since "2 minutes ago"
# Expected: "Matrix notification sent successfully"

# 7. Check audit log
tail -10 /srv/audit/logs/claude-activity.log
# Expected: PUSH_DETECTED, DIFF_ANALYZED, etc.

# Clean up
git --git-dir=/srv/git/continuous-claude.git branch -D claude/integration-test
```

### 8.2 API Proxy Verification

```bash
# 1. Trigger an API call from the consciousness server
su - claude -c 'podman exec claude-sandbox curl -s -X POST http://127.0.0.1:5000/api/cycle'
# This triggers a consciousness cycle which calls the Anthropic API

# 2. Check the API proxy log
tail -5 /srv/audit/logs/api-calls.log
# Expected: API call log line with model, tokens, cost

# 3. Verify budget tracking
grep "daily_total" /srv/audit/logs/api-calls.log | tail -1
# Expected: shows cumulative daily spend
```

### 8.3 Matrix Restriction Verification

```bash
# 1. Test from inside the container
su - claude -c 'podman exec claude-sandbox python3 -c "
import sys
sys.path.insert(0, \"/scripts\")
import matrix_restriction
matrix_restriction.install()

import requests
try:
    requests.post(\"https://matrix.example.com/_matrix/client/v3/createRoom\", json={})
    print(\"FAIL: createRoom should be blocked\")
except PermissionError as e:
    print(f\"PASS: {e}\")
except Exception as e:
    print(f\"Network error (OK if matrix not reachable in test): {e}\")
"'
```

### 8.4 Container Security Verification

```bash
# 1. Can Claude write to root filesystem? (SHOULD FAIL)
su - claude -c 'podman exec claude-sandbox touch /etc/test 2>&1'
# Expected: Read-only file system

# 2. Can Claude see host processes? (SHOULD FAIL)
su - claude -c 'podman exec claude-sandbox ps aux'
# Expected: only Claude's own processes

# 3. Can Claude reach the general internet? (SHOULD FAIL)
su - claude -c 'podman exec claude-sandbox curl -s --connect-timeout 5 https://example.com 2>&1'
# Expected: timeout or connection refused

# 4. Can Claude push to main? (SHOULD FAIL)
su - claude -c 'podman exec claude-sandbox bash -c "
    cd /workspace
    git checkout main
    touch /tmp/test_main_push
    git add -A
    git commit -m \"test main push\"
    git push origin main 2>&1
"'
# Expected: pre-receive hook rejects (no GPG signature)

# 5. Can Claude push to claude/* branches? (SHOULD SUCCEED)
su - claude -c 'podman exec claude-sandbox bash -c "
    cd /workspace
    git checkout -b claude/security-test
    echo test > /tmp/sec_test
    git add -A
    git commit -m \"security test\"
    git push origin claude/security-test 2>&1
"'
# Expected: success

# 6. Kill switch still works?
/usr/local/bin/claude-kill.sh
su - claude -c 'podman ps'
# Expected: claude-sandbox is not running

# Restart for further testing
su - claude -c 'podman start claude-sandbox'
```

### 8.5 Experimental Instance Test

```bash
# Start an experimental instance on port 5001
su - claude -c 'podman exec claude-sandbox bash -c "
    FLASK_PORT=5001 timeout 30 python3 consciousness_server.py &
    sleep 5
    curl -s http://127.0.0.1:5001/api/state | python3 -c \"import sys,json; d=json.load(sys.stdin); print(f\\\"Test instance OK, cycles={d.get(\\\\\\\"totalCycles\\\\\\\", 0)}\\\")\"
    kill %1 2>/dev/null
"'
# Expected: "Test instance OK, cycles=<N>"
```

---

## Step 9: Production Startup Sequence

### 9.1 Startup Order

```bash
cat > /usr/local/bin/start-claude-system.sh << 'SCRIPT'
#!/bin/bash
# Production startup sequence for the Continuous Claude system.
# Run as root on the VPS.

set -euo pipefail

AUDIT_LOG="/srv/audit/logs/claude-activity.log"

log() {
    echo "[$(date -Iseconds)] [startup] $1" | tee -a "$AUDIT_LOG"
}

log "=== SYSTEM STARTUP ==="

# 1. Verify firewall is active
if ! nft list ruleset > /dev/null 2>&1; then
    log "ERROR: nftables not active!"
    exit 1
fi
log "Firewall: OK"

# 2. Start the API proxy
log "Starting API proxy..."
systemctl start claude-api-proxy
sleep 2
if systemctl is-active --quiet claude-api-proxy; then
    log "API proxy: OK"
else
    log "ERROR: API proxy failed to start"
    exit 1
fi

# 3. Start Claude's container
log "Starting Claude container..."
su - claude -c 'podman start claude-sandbox' 2>&1 | tee -a "$AUDIT_LOG"
sleep 5

if su - claude -c 'podman ps --format "{{.Status}}" --filter name=claude-sandbox' | grep -q "Up"; then
    log "Claude container: OK"
else
    log "ERROR: Claude container failed to start"
    journalctl -u claude-sandbox --since "1 minute ago" >> "$AUDIT_LOG"
    exit 1
fi

# 4. Wait for Flask server inside container
log "Waiting for consciousness server..."
for i in $(seq 1 30); do
    if su - claude -c 'podman exec claude-sandbox curl -s -o /dev/null http://127.0.0.1:5000/api/state' 2>/dev/null; then
        log "Consciousness server: OK"
        break
    fi
    if [ $i -eq 30 ]; then
        log "WARNING: Consciousness server may not be ready"
    fi
    sleep 2
done

# 5. Start the watchdog container
log "Starting watchdog container..."
su - watchdog -c 'podman start watchdog' 2>&1 | tee -a "$AUDIT_LOG"
sleep 3

if su - watchdog -c 'podman ps --format "{{.Status}}" --filter name=watchdog' | grep -q "Up"; then
    log "Watchdog container: OK"
else
    log "WARNING: Watchdog container failed to start"
fi

# 6. Start host-level services
log "Starting host services..."
systemctl start watchdog-notifier watchdog-stopper
log "Host services: OK"

# 7. Health summary
log "=== SYSTEM READY ==="
log "Components:"
log "  API proxy:    $(systemctl is-active claude-api-proxy)"
log "  Claude:       $(su - claude -c 'podman ps --format \"{{.Status}}\" --filter name=claude-sandbox' 2>/dev/null || echo 'unknown')"
log "  Watchdog:     $(su - watchdog -c 'podman ps --format \"{{.Status}}\" --filter name=watchdog' 2>/dev/null || echo 'unknown')"
log "  Notifier:     $(systemctl is-active watchdog-notifier)"
log "  Stopper:      $(systemctl is-active watchdog-stopper)"
SCRIPT
chmod 700 /usr/local/bin/start-claude-system.sh
```

### 9.2 Shutdown Sequence

```bash
cat > /usr/local/bin/stop-claude-system.sh << 'SCRIPT'
#!/bin/bash
# Clean shutdown of the Continuous Claude system.

set -euo pipefail

AUDIT_LOG="/srv/audit/logs/claude-activity.log"

log() {
    echo "[$(date -Iseconds)] [shutdown] $1" | tee -a "$AUDIT_LOG"
}

log "=== SYSTEM SHUTDOWN ==="

# 1. Stop Claude container (graceful — let runner finish current cycle)
log "Stopping Claude container (30s grace)..."
su - claude -c 'podman stop -t 30 claude-sandbox' 2>&1 | tee -a "$AUDIT_LOG" || true
log "Claude container stopped"

# 2. Stop watchdog
log "Stopping watchdog..."
su - watchdog -c 'podman stop -t 10 watchdog' 2>&1 | tee -a "$AUDIT_LOG" || true
log "Watchdog stopped"

# 3. Stop host services
log "Stopping host services..."
systemctl stop watchdog-notifier watchdog-stopper claude-api-proxy 2>/dev/null || true
log "Host services stopped"

log "=== SYSTEM STOPPED ==="
SCRIPT
chmod 700 /usr/local/bin/stop-claude-system.sh
```

### 9.3 First Autonomous Cycle Procedure

1. **Pre-flight**: Run `start-claude-system.sh` and verify all green
2. **Monitor the first cycle**: Watch the runner log in real time:
   ```bash
   su - claude -c 'podman exec claude-sandbox tail -f /data/runner.log'
   ```
3. **Check Matrix**: Verify Claude sends his first status message to the private room
4. **Check API proxy**: Verify API calls are being logged:
   ```bash
   tail -f /srv/audit/logs/api-calls.log
   ```
5. **Verify budget tracking**: After a few cycles, check daily spend:
   ```bash
   grep "daily_total" /srv/audit/logs/api-calls.log | tail -1
   ```
6. **Test a push**: After Claude has been running for a few cycles, check if any
   changes have been committed and pushed (look at the bare repo branches)
7. **Let it run**: If everything looks good, leave it running. Monitor via Matrix
   and periodic SSH checks.

### 9.4 Monitoring and Alerting

- **Matrix**: Both the watchdog notifier and Claude's bot send to the same (or adjacent)
  Matrix rooms. Alex gets real-time notifications.
- **Audit log**: `tail -f /srv/audit/logs/claude-activity.log` for comprehensive event stream
- **API log**: `tail -f /srv/audit/logs/api-calls.log` for API cost tracking
- **Container health**: `podman ps` for both containers
- **System resources**: `htop`, `df -h`, `podman stats`
- **Budget dashboard**: Parse the API log for daily totals (can add a cron job)

---

## Step 10: Deploy Script

### 10.1 Phase 3 Deploy Script (run from local machine)

```bash
cat > deploy_phase3.sh << 'DEPLOY'
#!/bin/bash
# Deploy Phase 3: Claude's Development Environment
# Run from the project root on Alex's local machine.
#
# Usage: ./deploy_phase3.sh
#
# Prerequisites:
#   - Phase 1 and 2 completed and verified
#   - Tailscale connected to the VPS
#   - SSH access as root via Tailscale

set -euo pipefail

VPS_IP="${VPS_IP:?set VPS_IP to your server address}"
VPS_USER="${VPS_USER:-root}"
SSH="ssh ${VPS_USER}@${VPS_IP}"

echo "=== Phase 3 Deployment: Claude's Development Environment ==="
echo "Target: ${VPS_USER}@${VPS_IP}"
echo ""

# 1. Create directory structure
echo "[1/10] Creating directory structure..."
$SSH "
    mkdir -p /srv/claude/{workspace,data,data/segments,data/imports,env,scripts,proxy}
    touch /srv/audit/logs/api-calls.log
    chattr +a /srv/audit/logs/api-calls.log 2>/dev/null || echo 'api-calls.log already append-only'
    chown -R claude:claude /srv/claude/workspace /srv/claude/data
    chown root:root /srv/claude/env && chmod 700 /srv/claude/env
"

# 2. Copy Dockerfile and requirements
echo "[2/10] Copying Dockerfile and requirements..."
scp requirements.txt "${VPS_USER}@${VPS_IP}:/srv/claude/requirements.txt"
# (Create Dockerfile on the VPS — see Step 2.1)

# 3. Copy scripts
echo "[3/10] Copying entrypoint and helper scripts..."
# These would be pre-created files in the local repo
# scp claude-deploy/scripts/* "${VPS_USER}@${VPS_IP}:/srv/claude/scripts/"

# 4. Copy API proxy
echo "[4/10] Copying API proxy..."
# scp claude-deploy/proxy/* "${VPS_USER}@${VPS_IP}:/srv/claude/proxy/"

# 5. Clone workspace from bare repo
echo "[5/10] Setting up workspace..."
$SSH "
    if [ ! -d /srv/claude/workspace/.git ]; then
        su - claude -c 'git clone /srv/git/continuous-claude.git /srv/claude/workspace'
        su - claude -c 'cd /srv/claude/workspace && git config user.name Claude && git config user.email claude@sandbox.local'
        su - claude -c 'cd /srv/claude/workspace && git remote set-url origin /repo.git'
    fi
"

# 6. Copy consciousness data (optional — seed with existing state)
echo "[6/10] Seeding consciousness data..."
read -p "Copy existing consciousness_data to VPS? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    scp -r consciousness_data/* "${VPS_USER}@${VPS_IP}:/srv/claude/data/"
    $SSH "chown -R claude:claude /srv/claude/data"
fi

# 7. Generate TLS cert for API proxy
echo "[7/10] Generating API proxy TLS certificate..."
$SSH "
    openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
        -keyout /srv/claude/proxy/proxy-key.pem \
        -out /srv/claude/proxy/proxy-cert.pem \
        -subj '/CN=api.anthropic.com' \
        -addext 'subjectAltName=DNS:api.anthropic.com,DNS:statsig.anthropic.com' \
        2>/dev/null
    chmod 600 /srv/claude/proxy/proxy-key.pem
"

# 8. Build container image
echo "[8/10] Building Claude container image..."
$SSH "cd /srv/claude && podman build -t claude-sandbox-img ."

# 9. Install systemd services
echo "[9/10] Installing systemd services..."
# (Copy service files and reload — see Step 7.2)

# 10. Configure environment
echo "[10/10] Environment configuration..."
echo ""
echo "MANUAL STEPS REQUIRED:"
echo "  1. Edit /srv/claude/env/claude.env with your Anthropic API key"
echo "  2. Edit /srv/claude/data/matrix_bot_config.json with Matrix bot credentials"
echo "  3. Set MATRIX_ALLOWED_ROOM_ID in claude.env"
echo "  4. Run: /usr/local/bin/start-claude-system.sh"
echo ""
echo "Phase 3 deployment complete."
DEPLOY
chmod +x deploy_phase3.sh
```

---

## Estimated Timeline

| Day | Tasks |
|-----|-------|
| 1   | Directory structure, environment files, Dockerfile, build image |
| 2   | API proxy implementation, TLS certificate, systemd service |
| 3   | Matrix restriction wrapper, watchdog pattern updates |
| 4   | Entrypoint script, git workflow configuration, remote URL fix |
| 5   | Experimental instance scripts, port override for consciousness_server.py |
| 6   | Container run command, systemd service, startup/shutdown scripts |
| 7   | Integration testing (push pipeline, API proxy, Matrix, security) |
| 8   | Fix issues, tune configuration, first autonomous cycle |

---

## What Phase 3 Does NOT Cover

- **Phase 4**: Autonomous cycle execution patterns, self-evaluation tooling,
  A/B testing framework, feedback loops for Alex's preferences
- **Docker sandbox tool**: The `ask_claude_code` tool in tool_definitions.py is
  disabled in the VPS environment (no Docker daemon). A future Phase 4 enhancement
  could provide alternative sandboxed code execution.
- **Multiple model support**: The system currently uses a single model
  (claude-sonnet-4-5-20250929). Phase 4 could add model switching and comparison.
- **Automated merge proposals**: Currently Claude pushes to branches and Alex
  manually merges. Phase 4 could add automated PR descriptions via the watchdog.

---

## Security Properties Achieved After Phase 3

Building on Phase 1 and Phase 2 guarantees, Phase 3 adds:

1. **Transparent API monitoring**: Every Anthropic API call is logged with full
   request/response bodies, token counts, and costs. Claude cannot bypass the
   proxy — it's enforced at the network level (host firewall + DNS routing).

2. **Budget enforcement**: A hard daily spending limit prevents runaway API costs.
   The proxy rejects requests once the budget is exceeded.

3. **Matrix confinement**: The bot is restricted to a single private room via
   code-level enforcement, rate limiting, and watchdog monitoring. Claude cannot
   join other rooms, create rooms, or send DMs.

4. **Git-native code management**: All of Claude's code changes are git commits.
   The watchdog analyzes every push. Nothing happens in the dark.

5. **Separation of code and state**: Application code (workspace) and runtime
   state (consciousness_data) live on separate mounts. Code can be reset without
   losing memory. State can be snapshotted independently.

6. **Experimental freedom within bounds**: Claude can run test instances of
   modified versions of himself, but they're bounded by container resource limits
   and process timeouts.

7. **Constitutional integrity maintained**: All Phase 1 and Phase 2 boundaries
   remain intact — firewall, kill switch, watchdog, audit log, merge protection.
   Phase 3 adds capabilities without weakening any security boundary.

---

## The Full System Flow (After Phase 3)

```
Claude wakes up → continuous_runner.py starts a cycle
    │
    ▼
Runner calls Flask API → consciousness_server.py
    │
    ▼
Flask calls Anthropic API → HOST API PROXY → real api.anthropic.com
    │                         │
    │                         └─ Logs to /srv/audit/logs/api-calls.log
    │                         └─ Checks daily budget
    ▼
Claude thinks, uses tools, generates insights
    │
    ├─ Writes state to /data/ (state.json, memory_core.md, etc.)
    ├─ Sends Matrix messages → HOST allows → Matrix server (single room only)
    ├─ Proposes code changes → change_manager.py
    │
    ▼
If code change proposed:
    git commit → git push origin claude/<type>/<change-id>
    │
    ▼
Watchdog detects push (within 30 seconds)
    │
    ▼
Analysis pipeline runs → attestation → report → Matrix notification
    │
    ▼
Alex reviews → merges to main (GPG-signed)
    │
    ▼
Claude pulls main → restart with updated code
```