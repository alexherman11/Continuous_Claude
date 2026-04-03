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
su - claude -s /bin/bash -c 'podman start claude-sandbox' 2>&1 | tee -a "$AUDIT_LOG"
sleep 5

if su - claude -s /bin/bash -c 'podman ps --format "{{.Status}}" --filter name=claude-sandbox' | grep -q "Up"; then
    log "Claude container: OK"
else
    log "ERROR: Claude container failed to start"
    su - claude -s /bin/bash -c 'podman logs --tail 20 claude-sandbox' >> "$AUDIT_LOG" 2>&1
    exit 1
fi

# 4. Wait for Flask server inside container
log "Waiting for consciousness server..."
for i in $(seq 1 30); do
    if su - claude -s /bin/bash -c 'podman exec claude-sandbox curl -s -o /dev/null http://127.0.0.1:5000/api/state' 2>/dev/null; then
        log "Consciousness server: OK"
        break
    fi
    if [ $i -eq 30 ]; then
        log "WARNING: Consciousness server may not be ready"
    fi
    sleep 2
done

# 5. Health summary
log "=== SYSTEM READY ==="
log "Components:"
log "  API proxy:    $(systemctl is-active claude-api-proxy)"
log "  Claude:       $(su - claude -s /bin/bash -c 'podman ps --format "{{.Status}}" --filter name=claude-sandbox' 2>/dev/null || echo 'unknown')"
