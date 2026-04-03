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
FLASK_PORT=$PORT timeout "$MAX_RUNTIME" python3 consciousness_server.py &
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
