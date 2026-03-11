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
    && rm -rf /var/lib/apt/lists/*

# Symlink python -> python3 for convenience
RUN ln -sf /usr/bin/python3 /usr/bin/python

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Non-root user for safety
RUN useradd -m -s /bin/bash -u 1000 coder
USER coder
WORKDIR /workspace

# Keep container alive as a daemon
CMD ["tail", "-f", "/dev/null"]
