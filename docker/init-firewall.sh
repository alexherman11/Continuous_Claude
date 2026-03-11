#!/bin/bash
# Network firewall: default-deny with Anthropic API allowlist
# Run as: docker exec -u root code-sandbox /usr/local/bin/init-firewall.sh

set -e

echo "[firewall] Configuring network firewall..."

# Resolve api.anthropic.com to current IPs
ANTHROPIC_IPS=$(getent ahosts api.anthropic.com | awk '{print $1}' | sort -u)
# Known Anthropic CIDR range
ANTHROPIC_CIDR="160.79.104.0/23"

# Statsig (feature flags, required by Claude Code CLI)
STATSIG_IPS=$(getent ahosts statsig.anthropic.com | awk '{print $1}' | sort -u 2>/dev/null || true)

# Create ipset for allowed individual IPs
ipset create allowed-domains hash:ip -exist
ipset flush allowed-domains

for ip in $ANTHROPIC_IPS; do
    ipset add allowed-domains "$ip" -exist
done

for ip in $STATSIG_IPS; do
    ipset add allowed-domains "$ip" -exist
done

# Create ipset for allowed CIDR ranges
ipset create allowed-cidrs hash:net -exist
ipset flush allowed-cidrs
ipset add allowed-cidrs "$ANTHROPIC_CIDR" -exist

# Flush existing rules
iptables -F OUTPUT
iptables -F INPUT

# Allow loopback (localhost)
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT -i lo -j ACCEPT

# Allow DNS (needed to resolve api.anthropic.com)
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

# Allow established/related connections
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow traffic to Anthropic API IPs
iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT
iptables -A OUTPUT -m set --match-set allowed-cidrs dst -j ACCEPT

# Default deny everything else
iptables -P OUTPUT DROP
iptables -P INPUT DROP

# Verify: blocked site should be unreachable
if curl -s --connect-timeout 2 http://example.com > /dev/null 2>&1; then
    echo "[firewall] WARNING: example.com is reachable — firewall may not be working!"
    exit 1
else
    echo "[firewall] Verified: unauthorized outbound traffic is blocked."
fi

echo "[firewall] Done. Only api.anthropic.com and statsig.anthropic.com are reachable."
