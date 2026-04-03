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
su - claude -s /bin/bash -c 'podman stop -t 30 claude-sandbox' 2>&1 | tee -a "$AUDIT_LOG" || true
log "Claude container stopped"

# 2. Stop host services
log "Stopping host services..."
systemctl stop claude-api-proxy 2>/dev/null || true
log "Host services stopped"

log "=== SYSTEM STOPPED ==="
