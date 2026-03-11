FROM node:20-slim

# System tools for a productive coding environment
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    jq \
    sqlite3 \
    iptables \
    ipset \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Symlink python -> python3 for convenience
RUN ln -sf /usr/bin/python3 /usr/bin/python

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Firewall script (run externally via docker exec -u root)
COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN sed -i 's/\r$//' /usr/local/bin/init-firewall.sh && chmod +x /usr/local/bin/init-firewall.sh

# Non-root user for safety (UID 1000 is already 'node' in this base image)
RUN useradd -m -s /bin/bash coder
USER coder
WORKDIR /workspace

# Keep container alive as a daemon
CMD ["tail", "-f", "/dev/null"]
