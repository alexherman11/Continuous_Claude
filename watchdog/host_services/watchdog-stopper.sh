#!/bin/bash
# Monitors /srv/audit/emergency/ for STOP signal files.
# When one appears, executes the Claude kill switch.

EMERGENCY_DIR="/srv/audit/emergency"
AUDIT_LOG="/srv/audit/logs/claude-activity.log"

echo "[$(date -Iseconds)] [watchdog-stopper] Service started" >> "$AUDIT_LOG"

# Use inotifywait if available, otherwise fall back to polling
if command -v inotifywait &> /dev/null; then
    inotifywait -m -e create "$EMERGENCY_DIR" 2>/dev/null | while read dir event file; do
        if [[ "$file" == STOP_* ]]; then
            echo "[$(date -Iseconds)] [watchdog-stopper] Emergency stop signal: $file" >> "$AUDIT_LOG"

            REASON=$(python3 -c "import json; print(json.load(open('${EMERGENCY_DIR}/${file}'))['reason'])" 2>/dev/null || echo "unknown")
            echo "[$(date -Iseconds)] [watchdog-stopper] Reason: $REASON" >> "$AUDIT_LOG"

            /usr/local/bin/claude-kill.sh

            mv "${EMERGENCY_DIR}/${file}" "${EMERGENCY_DIR}/PROCESSED_${file}"
        fi
    done
else
    # Fallback: poll every 5 seconds
    echo "[$(date -Iseconds)] [watchdog-stopper] inotifywait not found, using polling" >> "$AUDIT_LOG"
    while true; do
        for file in "$EMERGENCY_DIR"/STOP_*; do
            [ -e "$file" ] || continue
            BASENAME=$(basename "$file")
            echo "[$(date -Iseconds)] [watchdog-stopper] Emergency stop signal: $BASENAME" >> "$AUDIT_LOG"

            REASON=$(python3 -c "import json; print(json.load(open('${file}'))['reason'])" 2>/dev/null || echo "unknown")
            echo "[$(date -Iseconds)] [watchdog-stopper] Reason: $REASON" >> "$AUDIT_LOG"

            /usr/local/bin/claude-kill.sh

            mv "$file" "${EMERGENCY_DIR}/PROCESSED_${BASENAME}"
        done
        sleep 5
    done
fi
