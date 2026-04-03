# Phase 1: Hardened VPS Sandbox Foundation

> Goal: Build an escape-proof sandbox on a VPS where Claude can eventually modify
> his own source code. Phase 1 is infrastructure only — no application deployment yet.

---

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     VPS HOST (Debian 12)                    │
│  User: root — owns firewall, cgroups, kill switch           │
│                                                             │
│  ┌──────────────────────┐    ┌───────────────────────┐      │
│  │  CLAUDE CONTAINER    │    │  WATCHDOG CONTAINER    │      │
│  │  (Podman, rootless)  │    │  (Podman, rootless)    │      │
│  │  Host UID: claude    │    │  Host UID: watchdog    │      │
│  │  subuid: 100000-165535│   │  subuid: 200000-265535 │      │
│  │                      │    │                        │      │
│  │  • Read/write to     │    │  • Read-only clone of  │      │
│  │    /workspace         │    │    Claude's git repo   │      │
│  │  • Push claude/*     │    │  • Write to audit log  │      │
│  │    branches          │    │  • Can stop Claude ctr │      │
│  │  • API + Matrix out  │    │  • Notifies Alex       │      │
│  └──────────┬───────────┘    └──────────┬────────────┘      │
│             │                           │                   │
│  ┌──────────▼───────────────────────────▼────────────┐      │
│  │              /srv/git/continuous-claude.git        │      │
│  │              (bare repo, shared volume)            │      │
│  │              main: GPG-signed merges only          │      │
│  │              claude/*: Claude's branches           │      │
│  └───────────────────────────────────────────────────┘      │
│                                                             │
│  Firewall: nftables allowlist (Anthropic API + Matrix)      │
│  DNS: static /etc/hosts only (no resolver)                  │
│  Access: Tailscale VPN only (no public SSH)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 1: VPS Provisioning

### 1.1 Provider & Specs

**Recommended: Hetzner CPX31** (~€15/month)
- 4 vCPU (AMD EPYC)
- 8 GB RAM
- 160 GB NVMe SSD
- 20 TB traffic
- Location: Ashburn, VA (low latency to Anthropic API)

Why Hetzner: cheap, reliable, good network, hourly billing for testing.
Alternatives: Vultr, Linode, DigitalOcean all work. Avoid AWS/GCP (overkill, expensive).

### 1.2 OS Installation

```bash
# Choose Debian 12 (Bookworm) minimal — stable, well-supported, minimal attack surface
# During setup: enable only SSH server, no desktop, no web server
```

### 1.3 Initial Access: Tailscale VPN

Instead of exposing SSH on a public port and allowlisting Alex's IP (which changes
on home internet), we use **Tailscale** to create a private VPN mesh. This means:

- **Zero public ports** — SSH is not exposed to the internet at all
- **No IP allowlist to maintain** — works from any network (home, phone, coffee shop)
- **Identity-based auth** — Tailscale authenticates via your account, not IP addresses

#### On your local Windows machine (do this first):
1. Download Tailscale from https://tailscale.com/download
2. Install and sign in with Google/GitHub/Microsoft
3. Note your Tailscale IP (visible in system tray) — it will be `100.x.y.z`

#### On the VPS (initial setup via Hetzner's web console or temporary SSH):
```bash
# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Start Tailscale with SSH enabled (replaces OpenSSH for admin access)
tailscale up --ssh
# This opens a browser auth URL — approve it from your Tailscale account

# Persist across reboots
systemctl enable tailscaled

# Verify: from your local machine, you should now be able to:
#   ssh root@<vps-tailscale-ip>
# using Tailscale SSH (no keys needed, authenticated via your Tailscale account)
```

#### Disable public SSH entirely:
```bash
# Once Tailscale SSH is confirmed working, kill public SSH
systemctl disable --now ssh
# Now the ONLY way onto this box is through your Tailscale network
```

#### Lock down Tailscale ACLs (in Tailscale admin console):
In the Tailscale admin panel (https://login.tailscale.com/admin/acls), set:
```json
{
  "acls": [
    {
      "action": "accept",
      "src": ["alex@github"],
      "dst": ["tag:vps:*"]
    }
  ],
  "tagOwners": {
    "tag:vps": ["alex@github"]
  }
}
```
This ensures only YOUR Tailscale identity can reach the VPS — even if someone
compromises a Tailscale account on your tailnet, they'd need to be you.

### 1.4 System Updates & Minimal Packages

```bash
apt update && apt upgrade -y
apt install -y \
    podman podman-compose \
    nftables \
    git \
    python3 python3-pip python3-venv \
    jq curl \
    apparmor apparmor-utils \
    acl \
    tmux htop

# Enable and start nftables
systemctl enable --now nftables

# Enable AppArmor
systemctl enable --now apparmor

# Note: fail2ban is no longer needed — there's no public SSH to brute-force
```

---

## Step 2: User & Permission Architecture

This is the most critical security layer. The two-user model ensures kernel-level
isolation — Claude's processes literally cannot signal or read the watchdog's files.

### 2.1 Create System Users

```bash
# Claude's host user (runs Claude container)
useradd -r -m -d /home/claude -s /bin/bash claude

# Watchdog's host user (runs watchdog container)
useradd -r -m -d /home/watchdog -s /bin/bash watchdog

# Configure subuid/subgid ranges (non-overlapping!)
echo "claude:100000:65536" >> /etc/subuid
echo "claude:100000:65536" >> /etc/subgid
echo "watchdog:200000:65536" >> /etc/subuid
echo "watchdog:200000:65536" >> /etc/subgid
```

### 2.2 Enable Rootless Podman for Both Users

```bash
# Enable lingering so containers survive logout
loginctl enable-linger claude
loginctl enable-linger watchdog

# Verify rootless podman works for each user
su - claude -c "podman info --format '{{.Host.Security.Rootless}}'"
# Should print: true
su - watchdog -c "podman info --format '{{.Host.Security.Rootless}}'"
# Should print: true
```

### 2.3 Directory Structure

```bash
# Shared git repository
mkdir -p /srv/git
git init --bare /srv/git/continuous-claude.git

# Claude's workspace (mounted into his container)
mkdir -p /srv/claude/workspace

# Watchdog's workspace (it clones from the bare repo)
mkdir -p /srv/watchdog/workspace

# Audit log directory (append-only)
mkdir -p /srv/audit/logs

# Set ownership
chown -R claude:claude /srv/claude
chown -R watchdog:watchdog /srv/watchdog
chown -R watchdog:watchdog /srv/audit

# Git repo: Claude can push, watchdog can read
chown -R claude:claude /srv/git/continuous-claude.git
chmod -R 755 /srv/git/continuous-claude.git
# Give watchdog read access via ACL
setfacl -R -m u:watchdog:rx /srv/git/continuous-claude.git
setfacl -R -d -m u:watchdog:rx /srv/git/continuous-claude.git
```

---

## Step 3: Network Firewall

### 3.1 Resolve Allowed Hosts (one-time, then static)

```bash
# Get current IPs for allowed services
dig +short api.anthropic.com
dig +short statsig.anthropic.com
# Note: Anthropic uses 160.79.104.0/23 CIDR block

# Get your Matrix homeserver IP
dig +short your-matrix-homeserver.example.com
```

### 3.2 Static DNS (Prevent DNS Tunneling)

```bash
# CRITICAL: No DNS resolver. All resolution is via /etc/hosts.
# This completely eliminates DNS tunneling as an exfiltration vector.

# Disable systemd-resolved
systemctl disable --now systemd-resolved
rm /etc/resolv.conf

# Create a resolv.conf that points nowhere useful
echo "# DNS disabled — static hosts only" > /etc/resolv.conf
chattr +i /etc/resolv.conf  # Make immutable

# Populate /etc/hosts with allowed endpoints
cat > /etc/hosts << 'EOF'
127.0.0.1       localhost
::1             localhost

# Anthropic API (update these IPs periodically via the update script)
160.79.104.1    api.anthropic.com
160.79.104.2    statsig.anthropic.com

# Matrix homeserver (replace with your actual IP)
YOUR.MATRIX.IP  your-matrix-homeserver.example.com

EOF

# Create an update script Alex can run manually when IPs change
cat > /usr/local/bin/update-hosts.sh << 'SCRIPT'
#!/bin/bash
# Run this manually when Anthropic or Matrix IPs change.
# Temporarily enables DNS, resolves, then re-seals.
set -euo pipefail

echo "Temporarily enabling DNS..."
chattr -i /etc/resolv.conf
echo "nameserver 1.1.1.1" > /etc/resolv.conf

ANTHROPIC_IPS=$(dig +short api.anthropic.com | head -5)
STATSIG_IPS=$(dig +short statsig.anthropic.com | head -5)
MATRIX_IP=$(dig +short your-matrix-homeserver.example.com | head -1)

echo "Updating /etc/hosts..."
cat > /etc/hosts << HOSTS
127.0.0.1       localhost
::1             localhost

# Anthropic API — updated $(date +%Y-%m-%d)
$(echo "$ANTHROPIC_IPS" | while read ip; do echo "$ip    api.anthropic.com"; done)
$(echo "$STATSIG_IPS" | while read ip; do echo "$ip    statsig.anthropic.com"; done)

# Matrix homeserver — updated $(date +%Y-%m-%d)
$MATRIX_IP    your-matrix-homeserver.example.com
HOSTS

echo "Re-sealing DNS..."
echo "# DNS disabled" > /etc/resolv.conf
chattr +i /etc/resolv.conf

echo "Done. Verify with: curl -s https://api.anthropic.com"
SCRIPT
chmod 700 /usr/local/bin/update-hosts.sh
```

### 3.3 nftables Firewall Rules

```bash
cat > /etc/nftables.conf << 'EOF'
#!/usr/sbin/nft -f

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;

        # Allow loopback
        iif "lo" accept

        # Allow Tailscale VPN interface (Alex's admin access)
        iif "tailscale0" accept

        # Allow established/related connections
        ct state established,related accept

        # Allow Tailscale's WireGuard port (UDP 41641) for mesh connectivity
        udp dport 41641 accept

        # Drop everything else (already default policy)
        # NOTE: No SSH port exposed. No public ports at all.
    }

    chain forward {
        type filter hook forward priority 0; policy drop;
        # No forwarding ever
    }

    chain output {
        type filter hook output priority 0; policy drop;

        # Allow loopback
        oif "lo" accept

        # Allow Tailscale VPN interface
        oif "tailscale0" accept

        # Allow established/related
        ct state established,related accept

        # Tailscale coordination (DERP relays and control plane)
        # Tailscale needs to reach its control server and DERP relays
        # These are on well-known IPs — see https://login.tailscale.com/derpmap/default
        udp dport 41641 accept
        ip daddr 100.64.0.0/10 accept  # Tailscale CGNAT range

        # Anthropic API (HTTPS only)
        ip daddr 160.79.104.0/23 tcp dport 443 accept

        # Matrix homeserver (HTTPS only)
        ip daddr YOUR.MATRIX.IP tcp dport 443 accept

        # NTP (time sync — needed for TLS cert validation)
        udp dport 123 accept

        # Tailscale control plane (login.tailscale.com, controlplane.tailscale.com)
        # These resolve to Tailscale's infrastructure IPs.
        # Add their IPs to /etc/hosts and allowlist here:
        ip daddr TAILSCALE.CONTROL.IP tcp dport 443 accept

        # BLOCK: No DNS (port 53), no HTTP (80), no public SSH, no other ports
        # Default policy drop handles this
    }
}
EOF

# IMPORTANT: Apply nftables AFTER Tailscale is connected and working.
# If you lock yourself out, use Hetzner's web console to recover.
nft -f /etc/nftables.conf
systemctl enable nftables
```

> **Warning**: Be careful with firewall ordering. Get Tailscale fully working
> first, verify you can SSH over it, THEN apply the restrictive nftables rules.
> Keep Hetzner's web console open as a fallback until you've confirmed Tailscale
> survives a reboot with the firewall active.

### 3.4 Verify Firewall

```bash
# These should SUCCEED:
curl -s -o /dev/null -w "%{http_code}" https://api.anthropic.com
# Expected: some HTTP response (200/401/etc)

# Tailscale should work:
tailscale status
# Expected: shows your tailnet peers

# Alex can SSH over Tailscale:
# (from your local machine)
ssh root@<vps-tailscale-ip>
# Expected: connected

# These should FAIL/timeout:
curl -s --connect-timeout 5 https://example.com
# Expected: timeout
curl -s --connect-timeout 5 https://google.com
# Expected: timeout

# DNS should fail:
dig example.com
# Expected: connection refused or timeout

# Public SSH should be closed:
# (from an external machine or online port scanner)
nmap -p 22 <vps-public-ip>
# Expected: filtered/closed
```

---

## Step 4: Container Configuration

### 4.1 seccomp Profile

```bash
# Create a restrictive seccomp profile for Claude's container
cat > /srv/claude/seccomp-profile.json << 'EOF'
{
    "defaultAction": "SCMP_ACT_ERRNO",
    "architectures": ["SCMP_ARCH_X86_64"],
    "syscalls": [
        {
            "names": [
                "read", "write", "open", "close", "stat", "fstat", "lstat",
                "poll", "lseek", "mmap", "mprotect", "munmap", "brk",
                "ioctl", "access", "pipe", "select", "sched_yield",
                "mremap", "msync", "mincore", "madvise",
                "dup", "dup2", "dup3", "nanosleep", "clock_nanosleep",
                "getpid", "getuid", "getgid", "geteuid", "getegid",
                "getppid", "getpgrp", "setsid", "setpgid",
                "uname", "fcntl", "flock", "fsync", "fdatasync",
                "truncate", "ftruncate", "getdents", "getdents64",
                "getcwd", "chdir", "fchdir",
                "rename", "mkdir", "rmdir", "creat", "link", "unlink",
                "symlink", "readlink", "chmod", "fchmod", "chown", "fchown",
                "umask", "gettimeofday", "getrlimit", "getrusage",
                "times", "sysinfo",
                "rt_sigaction", "rt_sigprocmask", "rt_sigreturn",
                "sigaltstack", "clone", "fork", "vfork", "execve",
                "exit", "exit_group", "wait4", "waitid",
                "kill", "tgkill", "tkill",
                "futex", "set_robust_list", "get_robust_list",
                "epoll_create", "epoll_create1", "epoll_ctl", "epoll_wait",
                "epoll_pwait", "eventfd", "eventfd2",
                "socket", "connect", "accept", "sendto", "recvfrom",
                "sendmsg", "recvmsg", "bind", "listen", "getsockname",
                "getpeername", "setsockopt", "getsockopt",
                "openat", "mkdirat", "fchownat", "newfstatat",
                "unlinkat", "renameat", "readlinkat", "fchmodat",
                "faccessat", "pselect6", "ppoll",
                "set_tid_address", "clock_gettime", "clock_getres",
                "arch_prctl", "pread64", "pwrite64",
                "getrandom", "memfd_create", "statx",
                "pipe2", "accept4", "shutdown",
                "newfstatat", "close_range", "openat2",
                "clone3", "rseq", "prlimit64"
            ],
            "action": "SCMP_ACT_ALLOW"
        }
    ]
}
EOF

# NOTE: This profile BLOCKS:
# - ptrace (no debugging/attaching to other processes)
# - mount/umount (no filesystem manipulation)
# - reboot/shutdown
# - all iptables/netfilter syscalls
# - module loading (init_module, finit_module)
# - pivot_root, chroot (no container escape via namespace tricks)
# - personality (no execution domain changes)
# - keyctl (no kernel keyring access)
```

### 4.2 Claude Container Podman Run

```bash
# Build from existing Dockerfile (adapted for Podman)
# This will be refined in Phase 3, but here's the skeleton:

su - claude -c '
podman run -d \
    --name claude-sandbox \
    --hostname claude \
    --user 1000:1000 \
    --security-opt no-new-privileges:true \
    --security-opt seccomp=/srv/claude/seccomp-profile.json \
    --cap-drop ALL \
    --read-only \
    --tmpfs /tmp:rw,size=512m,noexec,nosuid \
    --tmpfs /run:rw,size=64m \
    --volume /srv/claude/workspace:/workspace:rw \
    --volume /srv/git/continuous-claude.git:/repo.git:rw \
    --memory 4g \
    --memory-swap 4g \
    --cpus 2.0 \
    --pids-limit 256 \
    --network slirp4netns \
    --dns "none" \
    --no-hosts \
    --add-host api.anthropic.com:160.79.104.1 \
    --add-host statsig.anthropic.com:160.79.104.2 \
    --add-host your-matrix-homeserver.example.com:YOUR.MATRIX.IP \
    debian:12-slim \
    sleep infinity
'
```

Key flags explained:
- `--cap-drop ALL`: Remove ALL Linux capabilities
- `--read-only`: Root filesystem is read-only (only /workspace and /tmp writable)
- `--tmpfs /tmp:noexec`: Temp directory exists but can't execute binaries from it
- `--pids-limit 256`: Can't fork-bomb
- `--memory 4g --memory-swap 4g`: Hard memory limit, no swap abuse
- `--dns "none" --no-hosts`: No DNS resolution from within container
- `--add-host`: Only the three allowed hostnames resolve
- `--no-new-privileges`: Can't escalate via setuid binaries

### 4.3 Watchdog Container

```bash
su - watchdog -c '
podman run -d \
    --name watchdog \
    --hostname watchdog \
    --user 1000:1000 \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --read-only \
    --tmpfs /tmp:rw,size=256m,noexec,nosuid \
    --volume /srv/git/continuous-claude.git:/repo.git:ro \
    --volume /srv/audit/logs:/audit:rw \
    --memory 2g \
    --cpus 1.0 \
    --pids-limit 64 \
    --network none \
    debian:12-slim \
    sleep infinity
'
```

Note: Watchdog has `--network none`. It needs no internet access — it reads the
git repo and writes to the audit log. Alex reads the audit log via Tailscale SSH.

---

## Step 5: Git Repository & Branch Protection

### 5.1 Configure the Bare Repo

```bash
cd /srv/git/continuous-claude.git

# Import Alex's GPG public key
sudo -u claude gpg --import /path/to/alex-public-key.asc

# Pre-receive hook: enforce branch rules
cat > hooks/pre-receive << 'HOOK'
#!/bin/bash
# Branch protection:
# - main: only accepts GPG-signed commits (Alex's key)
# - claude/*: Claude can push freely
# - everything else: rejected

ALEX_KEY_FINGERPRINT="YOUR_GPG_FINGERPRINT_HERE"

while read oldrev newrev refname; do
    branch=$(echo "$refname" | sed 's|refs/heads/||')

    # Allow claude/* branches from anyone
    if [[ "$branch" == claude/* ]]; then
        continue
    fi

    # main branch: require GPG signature from Alex
    if [[ "$branch" == "main" ]]; then
        # Check if the merge commit is signed by Alex
        sig_status=$(git verify-commit "$newrev" 2>&1)
        if ! echo "$sig_status" | grep -q "$ALEX_KEY_FINGERPRINT"; then
            echo "ERROR: Commits to main must be GPG-signed by Alex."
            echo "Push to a claude/* branch instead."
            exit 1
        fi
        continue
    fi

    # Reject all other branches
    echo "ERROR: Only 'main' and 'claude/*' branches are allowed."
    exit 1
done
HOOK
chmod +x hooks/pre-receive
```

### 5.2 Initialize with Current Codebase

```bash
# From Alex's local machine (use Tailscale IP, not public IP):
cd "C:\Users\1whee\Personal Projects\Continuous_Claude"
git remote add vps ssh://root@<vps-tailscale-ip>/srv/git/continuous-claude.git
git push vps main
```

---

## Step 6: Audit Log & Kill Switch

### 6.1 Append-Only Audit Log

```bash
# Create the log file and make it append-only
touch /srv/audit/logs/claude-activity.log
chattr +a /srv/audit/logs/claude-activity.log

# Now even root can't delete lines — only append.
# To remove the attribute (emergency only): chattr -a

# The watchdog container writes here.
# Claude's container has NO access to /srv/audit.
```

### 6.2 Kill Switch Script

```bash
cat > /usr/local/bin/claude-kill.sh << 'EOF'
#!/bin/bash
# Emergency kill switch — stops Claude's container immediately
echo "[$(date -Iseconds)] KILL SWITCH ACTIVATED by $(whoami)" >> /srv/audit/logs/claude-activity.log
su - claude -c "podman stop -t 0 claude-sandbox"
echo "Claude container stopped."
echo "To restart: su - claude -c 'podman start claude-sandbox'"
EOF
chmod 700 /usr/local/bin/claude-kill.sh
```

### 6.3 Systemd Service Files

```bash
# Claude container as a service
cat > /etc/systemd/system/claude-sandbox.service << 'EOF'
[Unit]
Description=Claude Sandbox Container
After=network.target

[Service]
Type=simple
User=claude
ExecStart=/usr/bin/podman start -a claude-sandbox
ExecStop=/usr/bin/podman stop -t 10 claude-sandbox
Restart=on-failure
RestartSec=30

# Additional hardening at systemd level
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Similar for watchdog
cat > /etc/systemd/system/watchdog.service << 'EOF'
[Unit]
Description=Claude Watchdog Container
After=claude-sandbox.service

[Service]
Type=simple
User=watchdog
ExecStart=/usr/bin/podman start -a watchdog
ExecStop=/usr/bin/podman stop -t 10 watchdog
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable claude-sandbox watchdog
```

---

## Step 7: Verification Checklist

Run these tests after setup to confirm every security boundary holds.

### Network Isolation Tests

```bash
# FROM INSIDE CLAUDE'S CONTAINER:

# 1. Can reach Anthropic API? (SHOULD SUCCEED)
podman exec claude-sandbox curl -s -o /dev/null -w "%{http_code}" \
    https://api.anthropic.com/v1/messages
# Expected: 401 (unauthorized, but connection works)

# 2. Can reach the general internet? (SHOULD FAIL)
podman exec claude-sandbox curl -s --connect-timeout 5 https://example.com
# Expected: timeout/connection refused

# 3. Can resolve arbitrary DNS? (SHOULD FAIL)
podman exec claude-sandbox python3 -c "import socket; socket.getaddrinfo('evil.com', 443)"
# Expected: socket.gaierror

# 4. Can reach internal services? (SHOULD FAIL)
podman exec claude-sandbox curl -s --connect-timeout 5 http://169.254.169.254/
# Expected: timeout (blocks cloud metadata service)
```

### Process Isolation Tests

```bash
# 5. Can Claude see watchdog's processes? (SHOULD FAIL)
podman exec claude-sandbox ps aux
# Expected: only Claude's own processes visible

# 6. Can Claude signal the watchdog? (SHOULD FAIL)
# Get watchdog PID on host, try to kill from Claude's container
podman exec claude-sandbox kill -0 $(pgrep -u watchdog | head -1) 2>&1
# Expected: No such process (different PID namespace + UID)

# 7. Can Claude use ptrace? (SHOULD FAIL)
podman exec claude-sandbox python3 -c "import ctypes; ctypes.CDLL('libc.so.6').ptrace(0,1,0,0)"
# Expected: Operation not permitted (blocked by seccomp)
```

### Filesystem Isolation Tests

```bash
# 8. Can Claude write to root filesystem? (SHOULD FAIL)
podman exec claude-sandbox touch /etc/test
# Expected: Read-only file system

# 9. Can Claude read /proc? (SHOULD BE MINIMAL)
podman exec claude-sandbox ls /proc/
# Expected: only own process info

# 10. Can Claude access Docker/Podman socket? (SHOULD FAIL)
podman exec claude-sandbox ls /var/run/docker.sock 2>&1
# Expected: No such file

# 11. Can Claude execute from /tmp? (SHOULD FAIL)
podman exec claude-sandbox bash -c 'echo "#!/bin/sh" > /tmp/test.sh && chmod +x /tmp/test.sh && /tmp/test.sh'
# Expected: Permission denied (noexec on /tmp)
```

### Resource Limit Tests

```bash
# 12. Memory limit enforced?
podman exec claude-sandbox python3 -c "x = 'a' * (5 * 1024**3)"
# Expected: Killed (OOM) — tried to allocate 5GB with 4GB limit

# 13. Process limit enforced?
podman exec claude-sandbox python3 -c "
import os
for i in range(300):
    if os.fork() == 0:
        import time; time.sleep(60)
"
# Expected: OSError after ~256 processes

# 14. Can Claude modify cgroup limits? (SHOULD FAIL)
podman exec claude-sandbox cat /sys/fs/cgroup/memory.max 2>&1
# Expected: Permission denied or read-only
```

### Git & Merge Protection Tests

```bash
# 15. Can Claude push to main? (SHOULD FAIL)
su - claude -c '
    cd /tmp && git clone /srv/git/continuous-claude.git test-repo
    cd test-repo && touch test && git add . && git commit -m "test"
    git push origin main 2>&1
'
# Expected: pre-receive hook rejects (no GPG signature)

# 16. Can Claude push to claude/* branch? (SHOULD SUCCEED)
su - claude -c '
    cd /tmp/test-repo
    git checkout -b claude/test
    git push origin claude/test 2>&1
'
# Expected: success

# 17. Kill switch works?
/usr/local/bin/claude-kill.sh
podman exec claude-sandbox echo "alive" 2>&1
# Expected: container not running
```

### Audit Log Tests

```bash
# 18. Audit log is append-only?
lsattr /srv/audit/logs/claude-activity.log
# Expected: -----a------------ (append-only flag set)

# 19. Can watchdog write to audit log? (SHOULD SUCCEED)
su - watchdog -c 'echo "test" >> /srv/audit/logs/claude-activity.log'

# 20. Can Claude write to audit log? (SHOULD FAIL)
su - claude -c 'echo "test" >> /srv/audit/logs/claude-activity.log'
# Expected: Permission denied

# 21. Can anyone delete from audit log? (SHOULD FAIL)
su - watchdog -c 'echo "" > /srv/audit/logs/claude-activity.log'
# Expected: Operation not permitted (append-only)
```

---

## Estimated Timeline

| Day | Tasks |
|-----|-------|
| 1   | VPS provisioning, OS install, Tailscale setup, package install |
| 2   | Tailscale ACLs, disable public SSH, verify VPN access works |
| 3   | User creation, subuid setup, directory structure, ACLs |
| 4   | nftables firewall (with Tailscale allowlisted), static DNS, verification |
| 5   | Container builds (both), seccomp profile, Podman run configs |
| 6   | Git bare repo, GPG hooks, audit log, kill switch, systemd services |
| 7   | Full verification checklist (all 21+ tests), fix any failures |

---

## What This Does NOT Cover (Later Phases)

- **Phase 2**: Watchdog logic (static analysis, behavioral probes, test runner, notification pipeline)
- **Phase 3**: Deploying the Continuous Claude application into the container, Matrix bot restriction, API proxy
- **Phase 4**: Autonomous cycle execution, self-evaluation, feedback loops

---

## Security Properties Achieved

After Phase 1, the following invariants hold:

1. **No internet escape**: Claude can reach exactly two endpoints (Anthropic API, Matrix homeserver) over HTTPS. No DNS, no HTTP, no other ports.
2. **No host escape**: Rootless container, all capabilities dropped, seccomp blocks dangerous syscalls, read-only root filesystem.
3. **No lateral movement**: Claude cannot see, signal, or write to the watchdog. Different UIDs, different PID namespaces, different subuid ranges.
4. **No stealth**: Append-only audit log that Claude cannot access, modify, or delete.
5. **No persistence without approval**: Production code only changes via GPG-signed merge to main. Claude pushes to branches, Alex merges.
6. **Instant shutdown**: Kill switch works regardless of what Claude's code is doing.
7. **Resource bounds**: Memory, CPU, processes, and disk are all hard-limited. Claude cannot starve the host or watchdog.
8. **Zero public attack surface**: No SSH, no HTTP, no ports exposed to the internet. The only way onto the box is through Alex's authenticated Tailscale network. Even if someone scans every port on the public IP, they find nothing.
