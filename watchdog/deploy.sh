#!/bin/bash
# Deploy watchdog to the VPS.
# Run from the project root on your local machine.
#
# Usage: ./watchdog/deploy.sh
#
# Prerequisites:
#   - Tailscale connected to the VPS
#   - SSH access as root via Tailscale

set -euo pipefail

VPS_IP="${VPS_IP:?Set VPS_IP to the VPS address (e.g. its Tailscale IP)}"
VPS_USER="${VPS_USER:-root}"

echo "Deploying watchdog to ${VPS_USER}@${VPS_IP}..."

# 1. Copy watchdog source code
echo "  Copying source code..."
scp -r watchdog/src/* "${VPS_USER}@${VPS_IP}:/srv/watchdog/src/"

# 2. Copy baselines
echo "  Copying baselines..."
scp -r watchdog/baselines/* "${VPS_USER}@${VPS_IP}:/srv/watchdog/baselines/"

# 3. Copy config (don't overwrite existing watchdog.json if it has local customizations)
echo "  Copying config template..."
scp watchdog/config/watchdog.json "${VPS_USER}@${VPS_IP}:/srv/watchdog/config/watchdog.json.template"

# 4. Copy Dockerfile
echo "  Copying Dockerfile..."
scp watchdog/Dockerfile "${VPS_USER}@${VPS_IP}:/srv/watchdog/Dockerfile"

# 5. Copy host services
echo "  Copying host services..."
scp watchdog/host_services/watchdog-notifier.py "${VPS_USER}@${VPS_IP}:/usr/local/bin/watchdog-notifier.py"
scp watchdog/host_services/watchdog-stopper.sh "${VPS_USER}@${VPS_IP}:/usr/local/bin/watchdog-stopper.sh"
ssh "${VPS_USER}@${VPS_IP}" "chmod +x /usr/local/bin/watchdog-notifier.py /usr/local/bin/watchdog-stopper.sh"

scp watchdog/host_services/watchdog-notifier.service "${VPS_USER}@${VPS_IP}:/etc/systemd/system/"
scp watchdog/host_services/watchdog-stopper.service "${VPS_USER}@${VPS_IP}:/etc/systemd/system/"

# 6. Copy pre-receive hook
echo "  Copying pre-receive hook..."
scp watchdog/hooks/pre-receive "${VPS_USER}@${VPS_IP}:/srv/git/continuous-claude.git/hooks/pre-receive"
ssh "${VPS_USER}@${VPS_IP}" "chmod +x /srv/git/continuous-claude.git/hooks/pre-receive"

# 7. Create audit subdirectories if they don't exist
echo "  Ensuring audit directories..."
ssh "${VPS_USER}@${VPS_IP}" "
    mkdir -p /srv/audit/reports/processed
    mkdir -p /srv/audit/attestations
    mkdir -p /srv/audit/emergency
    chown -R watchdog:watchdog /srv/audit/reports /srv/audit/attestations /srv/audit/emergency
"

# 8. Reload systemd
echo "  Reloading systemd..."
ssh "${VPS_USER}@${VPS_IP}" "systemctl daemon-reload"

echo ""
echo "Deployment complete. Next steps:"
echo "  1. Build the watchdog container image:"
echo "     ssh ${VPS_USER}@${VPS_IP} 'cd /srv/watchdog && podman build -t watchdog-img .'"
echo "  2. Start the watchdog container (see docs/plans/PLAN.md Step 14.2)"
echo "  3. Enable host services:"
echo "     ssh ${VPS_USER}@${VPS_IP} 'systemctl enable --now watchdog-notifier watchdog-stopper'"
echo "  4. Configure notifier Matrix credentials:"
echo "     ssh ${VPS_USER}@${VPS_IP} 'vi /srv/watchdog/config/notifier.json'"
