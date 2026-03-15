# Docker Sandbox for Claude Code

A sandboxed Docker environment that lets the Continuous Consciousness engine spawn Claude Code instances to write and edit code in isolation.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- An Anthropic API key
- Windows (the start/stop scripts are `.bat` files)

## Setup

### 1. Configure your API key

Copy the example env file in the project root and add your key:

```
cp .env.example .env
```

Edit `.env` and set your key:

```
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
```

### 2. Start the sandbox

**Important:** Run `start_sandbox.bat` from **PowerShell** or **cmd.exe**, not Git Bash. Git Bash's MSYS path conversion mangles Docker volume mount paths (e.g., `/workspace` becomes `\Program Files\Git\workspace`), which silently breaks file sync between the host and container.

From the project root in PowerShell or cmd:

```
docker\start_sandbox.bat
```

This will:
- Build the Docker image (`code-sandbox-img`) with Node.js 20, Python 3, Git, and Claude Code CLI
- Create a `sandbox_workspace/` directory (mounted as `/workspace` inside the container)
- Start the container (`code-sandbox`) with resource limits:
  - 1 GB RAM
  - 2 CPU cores
  - 256 max processes
  - No privilege escalation
- Apply the network firewall (only Anthropic API access allowed)

On subsequent runs, it reuses the existing container and just re-applies the firewall.

### 3. Verify it's running

```
docker ps --filter name=code-sandbox
```

You should see the `code-sandbox` container with status "Up".

### 4. Test Claude Code inside the sandbox

```
docker exec code-sandbox claude -p "Say hello" --output-format json
```

If you get a response from Claude, the sandbox is working.

## Usage

### Manual use

Run Claude Code commands inside the sandbox:

```
docker exec code-sandbox claude -p "Write a Python script that prints fibonacci numbers" --allowedTools "Read,Write,Edit,Bash,Glob,Grep"
```

Files Claude creates will appear in `sandbox_workspace/` on your host machine.

### From the consciousness engine

The consciousness engine uses the sandbox automatically through its "sandbox coding" tool. When Claude (running as the consciousness) wants to write or test code, it dispatches work to the Docker container via `docker exec`.

## Stopping the sandbox

```
docker\stop_sandbox.bat
```

This stops and removes the container. Your files in `sandbox_workspace/` are preserved.

## Security model

### Network isolation

The firewall (`init-firewall.sh`) implements default-deny networking:

- **Allowed**: `api.anthropic.com`, `statsig.anthropic.com` (required for Claude Code CLI)
- **Blocked**: Everything else (npm, GitHub, arbitrary URLs)

This means Claude inside the sandbox cannot:
- Download packages from npm/pip
- Access the internet
- Exfiltrate data to external services

### Container isolation

- Runs as non-root user (`coder`, UID 1000)
- `--security-opt=no-new-privileges` prevents privilege escalation
- `--cap-add=NET_ADMIN` is only used by the root-level firewall init script
- Resource limits prevent runaway processes

### File isolation

- Only `sandbox_workspace/` is mounted — Claude cannot access the rest of your system
- The host Docker socket is **not** mounted (prevents container escape)

## Customization

### Allow npm/pip access

If you need Claude to install packages, edit `init-firewall.sh` to whitelist `registry.npmjs.org` and/or `pypi.org`:

```bash
NPM_IPS=$(getent ahosts registry.npmjs.org | awk '{print $1}' | sort -u)
for ip in $NPM_IPS; do
    ipset add allowed-domains "$ip" -exist
done
```

### Change resource limits

Edit `start_sandbox.bat` and modify the `docker run` flags:

```
--memory=2g        (increase RAM)
--cpus=4           (more CPU cores)
--pids-limit=512   (more processes)
```

### Linux/macOS

The `.bat` scripts are Windows-only. On Linux/macOS, run the equivalent commands directly:

```bash
# Build
docker build -t code-sandbox-img ./docker

# Create workspace
mkdir -p sandbox_workspace

# Start
docker run -d \
    --name code-sandbox \
    --memory=1g \
    --cpus=2 \
    --pids-limit=256 \
    --security-opt=no-new-privileges \
    --cap-add=NET_ADMIN \
    -e "ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2)" \
    -v "$(pwd)/sandbox_workspace:/workspace" \
    code-sandbox-img

# Apply firewall
docker exec -u root code-sandbox /usr/local/bin/init-firewall.sh

# Stop
docker stop code-sandbox && docker rm code-sandbox
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `docker: command not found` | Install Docker Desktop and ensure it's running |
| Firewall script fails | Container needs `--cap-add=NET_ADMIN` (already set in start script) |
| Claude returns auth errors | Check that `.env` has a valid `ANTHROPIC_API_KEY` |
| Container won't start | Run `docker\stop_sandbox.bat` first to clean up, then retry |
| Files not appearing in `sandbox_workspace/` | Ensure Claude is writing to `/workspace` inside the container. Also verify mounts with `docker inspect code-sandbox --format '{{json .Mounts}}'` — if destinations show `\Program Files\Git\...` instead of `/workspace`, the container was started from Git Bash. Recreate it from PowerShell or cmd. |
