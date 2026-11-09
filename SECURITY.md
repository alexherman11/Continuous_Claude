# Security model

## Threat model

Everything the model produces is untrusted input: text, tool calls and tool
arguments, files it writes, code it proposes, and anything a Claude Code
sub-agent (`ask_claude_code`) does on its behalf. The engine runs the model in
an autonomous loop with no human reviewing each step, so controls must hold
even if the model is confused, prompt-injected (e.g. via Matrix messages or
imported files), or deliberately probing its limits.

Goals, in order: keep the model inside its sandbox; cap spend; keep a record
of what happened; let a human stop everything quickly.

The controls below fall into two groups. **Policy** is code in the engine
process that the model talks to. It shapes behavior and catches mistakes, but
it runs inside the same container as the model's code, so a determined escape
from the engine process defeats it. **Enforcement** is applied by the
container runtime or the host and holds even if the engine process is fully
compromised.

## Layers

### 1. Engine (policy)

- **Tool gatekeeper.** Every tool call goes through `execute_tool` in
  `tool_definitions.py`. Unknown tools return an error; tool calls per cycle
  are capped (`MAX_TOOL_CALLS_PER_CYCLE`).
- **Path checks.** File tools are limited to `scratchpad/`, `research/` and
  (read-only) the engine directory. Containment uses
  `path_safety.is_within()`, which resolves symlinks and compares whole path
  components (`os.path.commonpath`), so `/x/scratchpad_evil` is not inside
  `/x/scratchpad`.
- **Change tiers** (`change_manager.py`). Code changes are proposed, not
  written directly:
  - Tier 1: a new/updated tool at exactly `dynamic_tools/enabled/<name>.py`.
    Auto-deployed after syntax and contract checks.
  - Tier 2: tool/integration files. Needs human approval.
  - Tier 3: core engine files and anything unrecognized. Needs human approval
    and a restart.

  Target paths are normalized first; absolute paths and `..` are rejected.
  The tool contract (`SCHEMA` literal + `execute()`) is checked with `ast`;
  proposed code is never imported or executed during validation. Every
  deploy takes a git snapshot first, for rollback.
- **Compaction safety.** Compaction plugins are model-editable; invalid
  output falls back to built-in defaults. Core Identity rewrites that are
  truncated, empty, or much shorter than the current core are rejected
  (`core_validation.py`).
- **Observability.** `api_calls.log` records every model call (including
  compaction calls). `consciousness_data/events.jsonl` records structured
  events: compaction outcomes, plugin fallbacks, tool errors.
- **`ask_claude_code`** runs the Claude Code CLI with its working directory
  set to `sandbox_workspace/`, with permission deny rules on editing engine
  files, `proposed_changes/`, the tool loader and `.git/` (written by
  `deploy/scripts/entrypoint.sh`).
- **Matrix.** The budget-override keyword is honored only from allowlisted
  Matrix user IDs (`budget_override_allowed_senders` /
  `MATRIX_BUDGET_OVERRIDE_USERS`; empty = disabled).

### 2. Container (enforcement)

The model and engine run in a rootless Podman container with:

- a non-root user inside a rootless runtime (no host root mapping)
- `--cap-drop ALL`, `--security-opt no-new-privileges`, and a seccomp profile
- `--read-only` root filesystem, with tmpfs for `/tmp` and `/run`
- memory, CPU and PID limits
- `--dns none` with no resolver; the only resolvable names are the ones
  pinned with `--add-host` (the Anthropic API names point at the host-side
  proxy)

### 3. Host (enforcement)

- **API proxy** (`deploy/proxy/api-proxy.py`) runs outside the container. All
  model traffic goes through it. It enforces a daily USD budget (cache-aware,
  per-model pricing; spend persisted to disk) by returning 429, and writes an
  audit log. With `PROXY_UPSTREAM_API_KEY` set it injects the real API key
  itself, so the container only needs a dummy key.
- **Firewall.** Default-drop nftables policy for input and output, with
  egress allowlisted by destination. SSH and admin access only over
  Tailscale; the proxy port is loopback-only. See
  `deploy/nftables.conf.example`. Match interfaces by name, not index.
- **Kill switch.** A root-only script on the host stops the container
  immediately. The watchdog (`watchdog/`) monitors pushes and activity and
  can trigger an emergency stop.

## Known limitations

| Issue | Status |
| --- | --- |
| Engine code is mounted read-write in the container. The model's own code paths (and Bash inside `ask_claude_code`) can edit Tier 2/3 files directly, bypassing the tiers. | **Open.** Mitigated by policy only: `ask_claude_code` runs in `sandbox_workspace/` with Claude Code deny rules, which don't cover Bash. Real fix: mount the engine code read-only and deploy approved changes from the host. |
| Tier 1 tools run in-process with engine privileges once deployed (`dynamic_tools/__init__.py` imports them every cycle). | **By design.** Tier 1 is auto-deployed, so a Tier 1 tool can do anything the engine can. Validation is static only. |
| The API key lives in the container by default (env file mounted into the container). | **Mitigation available:** set `PROXY_UPSTREAM_API_KEY` on the host and give the container a dummy key. Not on by default. |
| Requests sent with an OAuth bearer token are logged but billed $0 by the proxy (assumed to be on a subscription). | **Open** when `PROXY_UPSTREAM_API_KEY` is unset. With it set, caller auth is replaced and every call counts against the budget. |
| The API proxy has no client authentication; it relies on the firewall to stay loopback-only. It is also single-threaded. | **Open.** Keep 8443 closed to all non-loopback interfaces. |
| Any member of the Matrix room can queue messages for the model (prompt-injection surface). | **Open.** Only the budget override is allowlisted. |
| The engine's Flask API has no authentication. | **Accepted** as long as port 5000 is published only to host loopback (or not at all). Never expose it beyond that. |
| Proposed source files may come from `scratchpad/` or `sandbox_workspace/` only. | **Fixed** (it used to accept `../` and absolute paths). |
