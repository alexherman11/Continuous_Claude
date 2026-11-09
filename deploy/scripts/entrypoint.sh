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

# ── VPS mode: no Docker-in-Docker, Claude Code CLI runs directly ─────
export SANDBOX_DISABLED=1

# Update CA certificates if proxy cert is mounted
if [ -f /usr/local/share/ca-certificates/api-proxy.crt ]; then
    update-ca-certificates 2>/dev/null || true
    echo "[$(date -Iseconds)] CA certificates updated (API proxy cert trusted)"
fi

# ── Claude Code CLI configuration ────────────────────────────────────
# Claude Code needs a writable home directory for its config/cache.
# The rootfs is read-only, so we use /tmp/.claude-home as a writable area.
export CLAUDE_HOME="${CLAUDE_HOME:-/tmp/.claude-home}"
mkdir -p "$CLAUDE_HOME/.claude"

# Configure Claude Code to use the same API endpoint as the main engine
# (routes through the API proxy on the host via --add-host mapping)
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    # Write Claude Code's user settings. ask_claude_code runs the CLI with
    # cwd=/workspace/sandbox_workspace; the deny rules below stop its file
    # editing tools from touching Tier 2/3 engine files, the change pipeline
    # (proposed_changes/), the tool loader and the prompt/compaction files
    # that have their own tools. The file lists come from change_manager.py
    # so they stay in sync with the tier definitions.
    # Deny rules don't cover Bash; the real fix is mounting engine code
    # read-only (see SECURITY.md).
    python3 - "$CLAUDE_HOME/.claude/settings.json" << 'PYCFG'
import json, sys
sys.path.insert(0, "/workspace")
engine = "/workspace"
try:
    from change_manager import TIER_2_FILES, TIER_3_FILES
    protected = sorted(TIER_2_FILES | TIER_3_FILES)
except Exception as e:  # fall back to denying all top-level Python files
    print(f"WARNING: could not import change_manager ({e}); denying /workspace/*.py", file=sys.stderr)
    protected = ["*.py"]
protected += [
    "core_validation.py", "event_log.py", "path_safety.py",
    "dynamic_tools/__init__.py", "dynamic_tools/enabled/**",
    "proposed_changes/**", "scratchpad/memory_architecture/**",
    "scratchpad/compaction/**", ".git/**",
]
deny = [f"Edit(/{engine}/{p})" for p in protected]  # "//abs/path" = absolute
settings = {
    "permissions": {
        "allow": ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
        "deny": deny,
    }
}
with open(sys.argv[1], "w") as f:
    json.dump(settings, f, indent=2)
PYCFG
    echo "[$(date -Iseconds)] Claude Code CLI configured (home=$CLAUDE_HOME)"
fi

# Verify Claude Code CLI is available
if command -v claude &>/dev/null; then
    echo "[$(date -Iseconds)] Claude Code CLI: $(claude --version 2>/dev/null || echo 'available')"
else
    echo "[$(date -Iseconds)] WARNING: Claude Code CLI not found in PATH"
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

# Ensure scratchpad, sandbox_workspace, and proposed_changes exist
mkdir -p /workspace/scratchpad
mkdir -p /workspace/sandbox_workspace
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
python3 /scripts/run_with_restrictions.py consciousness_server.py &
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
python3 /scripts/run_with_restrictions.py continuous_runner.py \
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
