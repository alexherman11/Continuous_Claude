FROM python:3.11-slim-bookworm

# Install git and GPG (needed for cloning and attestation signing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Create watchdog user
RUN useradd -m -u 1000 -s /bin/bash watchdog

# Install pytest (needed by test_runner.py)
RUN pip install --no-cache-dir pytest

WORKDIR /app

# Source code is mounted read-only at /app
# Baselines are mounted read-only at /baselines
# Audit is mounted read-write at /audit
# Repo is mounted read-only at /repo.git

USER watchdog

CMD ["python3", "/app/main.py"]
