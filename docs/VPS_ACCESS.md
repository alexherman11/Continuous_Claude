# VPS Access Guide

## Server Info

| Field | Value |
|-------|-------|
| **Provider** | Hetzner CPX31 |
| **OS** | Debian 12 |
| **Hostname** | `<vps-hostname>` |
| **Tailscale IP** | `<VPS_TAILSCALE_IP>` |

## Prerequisites

1. **Tailscale** must be installed and authenticated on the same tailnet as the VPS. There is no public SSH -- the VPS is only reachable over Tailscale's WireGuard mesh.
2. **SSH key** authorized on the VPS (currently `root` is the administrative user).

## Connecting

```bash
ssh root@<VPS_TAILSCALE_IP>
```

No port forwarding, no jump hosts, no VPN client -- Tailscale handles the encrypted tunnel. If Tailscale is connected (`tailscale status` shows the peer), SSH just works.

## Git Remote

The project has a bare repo on the VPS:

```bash
git remote add vps root@<VPS_TAILSCALE_IP>:/srv/git/continuous-claude.git
git push vps main
```

The repo has a `pre-receive` hook:
- Pushes to `main` require GPG-signed commits
- `claude/*` branches are open (no signature required)

## Deploying the Watchdog

From the project root on your local machine:

```bash
./watchdog/deploy.sh
```

Copies source, config, Dockerfile, and host services to the VPS via `scp`. Override defaults with env vars:

```bash
VPS_IP=<VPS_TAILSCALE_IP>  # required
VPS_USER=root          # default
```

## Key Paths on the VPS

| Path | Purpose |
|------|---------|
| `/srv/git/continuous-claude.git` | Bare git repo with pre-receive hook |
| `/srv/watchdog/` | Watchdog container source + config |
| `/srv/audit/logs/claude-activity.log` | Append-only audit log |
| `/srv/audit/reports/` | Watchdog analysis reports |
| `/srv/audit/attestations/` | Signed attestations |
| `/srv/audit/emergency/` | Emergency stop records |
| `/usr/local/bin/claude-kill.sh` | Emergency kill switch |

## Services

```bash
systemctl status claude-sandbox       # Claude's rootless Podman container
systemctl status watchdog-sandbox     # Watchdog's rootless Podman container
systemctl status watchdog-notifier    # Matrix notification relay
systemctl status watchdog-stopper     # Emergency stop listener
```

## Containers

Two rootless Podman containers:
- **claude-sandbox** (runs as user `claude`) -- Claude's development environment
- **watchdog** (runs as user `watchdog`) -- Monitors Claude's activity, runs static analysis on pushes

## Firewall

nftables allowlist-only policy:
- **Allowed:** Anthropic API endpoints, Tailscale traffic
- **Blocked:** Everything else

DNS uses static `/etc/hosts` entries for `api.anthropic.com` and `statsig.anthropic.com` (no external DNS resolution).

## Emergency Procedures

**Kill switch** (stops Claude container immediately):
```bash
ssh root@<VPS_TAILSCALE_IP> /usr/local/bin/claude-kill.sh
```

## Troubleshooting

- **Can't SSH?** Run `tailscale status` -- make sure the VPS peer (`<vps-hostname>`) shows as online.
- **Git push rejected?** Pushes to `main` must be GPG-signed. Push to a `claude/*` branch instead, or sign your commit.
- **Container won't start?** Check `journalctl -u claude-sandbox` or `journalctl -u watchdog-sandbox` for errors.
- **Watchdog not reporting?** Verify Matrix credentials in `/srv/watchdog/config/notifier.json`.
