FROM python:3.11-slim-bookworm

# ── System dependencies ──────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 20 LTS (required for Claude Code CLI) ───────────────────
# Using NodeSource's official setup to get Node 20 on Debian Bookworm.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ─────────────────────────────────────────────
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# ── Claude Code CLI ─────────────────────────────────────────────────
# Install globally so it's available to the claude user.
# npm cache is cleaned to keep image size down.
RUN npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

# ── Claude user (UID/GID must match host claude user for volume perms) ──
RUN groupadd -g 996 claude && useradd -m -u 999 -g 996 -s /bin/bash claude

# Symlink for convenience
RUN ln -sf /usr/local/bin/python3 /usr/bin/python

# ── Runtime directories ─────────────────────────────────────────────
# Create mount-point directories and set ownership.
# The read-only rootfs means these must exist in the image.
RUN mkdir -p /workspace /data /env /scripts /repo.git /certs \
    && chown claude:claude /workspace /data

# ── Claude Code configuration ───────────────────────────────────────
# Create the .claude directory structure so the read-only rootfs doesn't
# block Claude Code from initializing. The actual settings will be
# populated at runtime via tmpfs or volume mount if needed.
RUN mkdir -p /home/claude/.claude \
    && chown -R claude:claude /home/claude/.claude

WORKDIR /workspace

USER claude

# Default: run the entrypoint script (mounted at /scripts/)
CMD ["/bin/bash", "/scripts/entrypoint.sh"]
