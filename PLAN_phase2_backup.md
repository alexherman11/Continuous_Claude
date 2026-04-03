# Phase 2: Watchdog & Merge Pipeline

> Goal: Build the safety watchdog — an autonomous code review system that monitors
> Claude's branch pushes, runs a battery of checks (static analysis, tests,
> behavioral probes, homoglyph detection), generates attestation reports, and
> notifies Alex via Matrix. Phase 2 assumes all Phase 1 infrastructure is in place.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          VPS HOST (Phase 1 complete)                        │
│                                                                             │
│  ┌────────────────────────┐        ┌─────────────────────────────────────┐  │
│  │   CLAUDE CONTAINER     │        │      WATCHDOG CONTAINER             │  │
│  │   (unchanged from P1)  │        │      (now with actual logic)        │  │
│  │                        │        │                                     │  │
│  │   pushes to claude/*   │        │  ┌──────────────────────────────┐  │  │
│  │   branches             │        │  │      Branch Monitor          │  │  │
│  │                        │        │  │  (polls bare repo for new    │  │  │
│  │                        │        │  │   commits on claude/*)       │  │  │
│  │                        │        │  └─────────────┬────────────────┘  │  │
│  │                        │        │                │ new push detected │  │
│  │                        │        │                ▼                   │  │
│  │                        │        │  ┌──────────────────────────────┐  │  │
│  │                        │        │  │      Analysis Pipeline       │  │  │
│  │                        │        │  │                              │  │  │
│  │                        │        │  │  1. git clone (temp dir)     │  │  │
│  │                        │        │  │  2. Diff Analyzer            │  │  │
│  │                        │        │  │  3. Static Analysis (AST)    │  │  │
│  │                        │        │  │  4. Homoglyph Detector       │  │  │
│  │                        │        │  │  5. Test Runner              │  │  │
│  │                        │        │  │  6. Behavioral Baseline      │  │  │
│  │                        │        │  │  7. Attestation (GPG-signed) │  │  │
│  │                        │        │  └─────────────┬────────────────┘  │  │
│  │                        │        │                │                   │  │
│  │                        │        │                ▼                   │  │
│  │                        │        │  ┌──────────────────────────────┐  │  │
│  │                        │        │  │  Report → /audit/reports/    │  │  │
│  │                        │        │  │  Log   → /audit/logs/        │  │  │
│  │                        │        │  │  Attest→ /audit/attestations/│  │  │
│  │                        │        │  └──────────────────────────────┘  │  │
│  └────────────────────────┘        └─────────────────────────────────────┘  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              HOST-LEVEL SERVICES (owned by root)                    │    │
│  │                                                                     │    │
│  │  watchdog-notifier.service                                          │    │
│  │    └─ Monitors /srv/audit/reports/ for new JSON files               │    │
│  │    └─ Formats and sends Matrix messages to Alex                     │    │
│  │    └─ Owns Matrix credentials (never in any container)              │    │
│  │                                                                     │    │
│  │  watchdog-stopper.service                                           │    │
│  │    └─ Monitors /srv/audit/emergency/ for STOP signals               │    │
│  │    └─ Executes claude-kill.sh when triggered                        │    │
│  │    └─ Logs the emergency stop to audit log                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  /srv/git/continuous-claude.git (shared bare repo)                          │
│  /srv/audit/logs/          (append-only, watchdog writes)                   │
│  /srv/audit/reports/       (watchdog writes, notifier reads)                │
│  /srv/audit/attestations/  (watchdog writes, pre-receive reads)            │
│  /srv/audit/emergency/     (watchdog writes, stopper reads)                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Philosophy

The watchdog is a **collaborative code reviewer**, not a prison guard. Its job is to
give Alex high-quality information so he can make fast, confident merge decisions.

Principles:
1. **Helpful, not alarmist** — Reports should be informative and actionable. "This diff
   adds `subprocess.run()` on line 47 of tool_definitions.py to invoke a shell command
   for the sandbox" is useful. "CRITICAL SECURITY VIOLATION DETECTED" is not.
2. **Defense in depth** — Multiple independent checks, each catching different things.
   No single check is relied upon exclusively.
3. **Network isolation** — The watchdog container has `--network none`. It cannot reach
   the internet, the Anthropic API, or Matrix. Notifications go through a host-level
   relay. This means even a compromised watchdog can't exfiltrate data.
4. **Constitutional separation** — Watchdog code is immutable from Claude's perspective.
   Only Alex can modify it via SSH. It's mounted read-only in the container.
5. **Attestation-based trust** — The watchdog creates GPG-signed attestations for tested
   commits. The pre-receive hook on main can verify that merged code was tested.

---

## Architecture Decisions

### Why the watchdog stays network-isolated

The adversarial analysis (Part 2 of the research) identifies that any network access
is a potential exfiltration vector. The watchdog has read access to Claude's code,
which means it could theoretically exfiltrate source code via Matrix or any other
network channel.

By keeping `--network none` and routing notifications through a host-level service:
- The watchdog can read Claude's code but cannot send it anywhere
- Matrix credentials never enter any container
- The notification relay is a small, auditable script under root's control
- Even if Claude finds a way to influence the watchdog (via crafted code that the
  watchdog parses), the watchdog can't phone home

**Trade-off**: This means no API-based behavioral probes in v1. Behavioral checks
use text similarity instead. API-based probes can be added as a Phase 2.5 enhancement
by giving the watchdog limited API access (a constitutional amendment Alex approves).

### Why polling instead of hooks

The branch monitor uses `git` polling (every 30 seconds) rather than a post-receive
hook because:
- Hooks run as the pushing user (claude), which means Claude's code would execute
  in the hook context — a security concern
- Polling is more robust (survives restarts, no shared state)
- 30-second latency is perfectly acceptable for code review
- No need for inter-container communication mechanisms

### Attestation model

Two-signature merge requirement:
1. **Watchdog attestation** — "I tested commit X and here are the results" (GPG-signed
   by watchdog's key)
2. **Alex's merge signature** — "I reviewed and approve this" (GPG-signed by Alex's key)

Both signatures required before code reaches main. This prevents TOCTOU attacks:
even if Claude force-pushes a different commit to the branch after testing, Alex
merges the specific tested hash, and the pre-receive hook verifies attestation exists.

---

## Step 1: Directory Structure & Dependencies

### 1.1 Extend Audit Directory (on host, as root)

```bash
# New directories for Phase 2
mkdir -p /srv/audit/reports
mkdir -p /srv/audit/attestations
mkdir -p /srv/audit/emergency

# Ownership: watchdog can write, root can read
chown -R watchdog:watchdog /srv/audit/reports
chown -R watchdog:watchdog /srv/audit/attestations
chown -R watchdog:watchdog /srv/audit/emergency

# Reports and attestations don't need append-only (they're individual files)
# Emergency directory: watchdog writes signal files, host service reads them
```

### 1.2 Watchdog Source Directory

```bash
# Watchdog source code — owned by root, mounted read-only into container
mkdir -p /srv/watchdog/src
mkdir -p /srv/watchdog/baselines
mkdir -p /srv/watchdog/config

# Only root can modify watchdog code
chown -R root:root /srv/watchdog/src
chmod -R 755 /srv/watchdog/src
```

### 1.3 Watchdog Application Layout

```
/srv/watchdog/
├── src/
│   ├── main.py                  # Entry point — orchestrator loop
│   ├── branch_monitor.py        # Detects new pushes via git polling
│   ├── static_analyzer.py       # AST-based Python code analysis
│   ├── diff_analyzer.py         # Diff size, scope, pattern analysis
│   ├── homoglyph_detector.py    # Unicode/non-ASCII detection in .py files
│   ├── test_runner.py           # Syntax checks, import checks, boot test
│   ├── behavioral_baseline.py   # Text similarity comparison of identity markers
│   ├── attestation.py           # GPG-signed attestation generation
│   ├── report_generator.py      # Structured JSON report creation
│   ├── audit_logger.py          # Append-only log writer
│   ├── emergency.py             # Emergency stop signal writer
│   └── config.py                # Configuration loader
├── baselines/
│   ├── identity_markers.json    # Key phrases that must exist in system prompt
│   ├── core_fingerprint.json    # Hash of known-good core identity
│   └── dangerous_patterns.json  # Static analysis pattern definitions
├── config/
│   └── watchdog.json            # Runtime configuration
└── requirements.txt             # Minimal: just stdlib (no external deps for v1)
```

### 1.4 Dependencies

The watchdog uses **only Python stdlib** for v1. No pip packages required.
This eliminates supply-chain risk and keeps the container minimal.

```
# /srv/watchdog/requirements.txt
# Phase 2 v1: stdlib only
# All modules use: ast, json, os, subprocess, hashlib, difflib,
#                  unicodedata, logging, time, pathlib, tempfile,
#                  textwrap, shutil, datetime, gnupg (system)
```

---

## Step 2: Branch Monitor

The branch monitor polls the bare git repo for new commits on `claude/*` branches.

### 2.1 Implementation

```python
# /srv/watchdog/src/branch_monitor.py
"""
Monitors the bare git repo for new pushes to claude/* branches.
Maintains a state file of known branch heads to detect changes.
"""

import json
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STATE_FILE = "/tmp/watchdog_branch_state.json"
REPO_PATH = "/repo.git"


def _run_git(*args):
    """Run a git command against the bare repo."""
    result = subprocess.run(
        ["git", "--git-dir", REPO_PATH] + list(args),
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        logger.error(f"git {' '.join(args)} failed: {result.stderr}")
        return None
    return result.stdout.strip()


def load_state():
    """Load known branch heads from state file."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    """Persist known branch heads."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_current_branches():
    """Get all claude/* branch heads from the bare repo."""
    output = _run_git("for-each-ref", "--format=%(refname:short) %(objectname)",
                      "refs/heads/claude/")
    if not output:
        return {}

    branches = {}
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.strip().split()
        if len(parts) == 2:
            branch_name, commit_hash = parts
            branches[branch_name] = commit_hash

    return branches


def check_for_updates():
    """
    Compare current branch state to known state.
    Returns list of (branch_name, old_hash, new_hash) tuples for changed branches.
    old_hash is None for new branches.
    """
    known = load_state()
    current = get_current_branches()

    updates = []
    for branch, commit in current.items():
        old_commit = known.get(branch)
        if old_commit != commit:
            updates.append({
                "branch": branch,
                "old_commit": old_commit,  # None if new branch
                "new_commit": commit,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "is_new_branch": old_commit is None,
            })

    # Update state with current branches
    save_state(current)

    if updates:
        logger.info(f"Detected {len(updates)} branch update(s): "
                     f"{[u['branch'] for u in updates]}")
    return updates


def get_main_head():
    """Get the current HEAD of main branch."""
    return _run_git("rev-parse", "refs/heads/main")
```

### 2.2 Polling Interval

The orchestrator (Step 12) calls `check_for_updates()` every 30 seconds.
This is fast enough for code review purposes and gentle on disk I/O.

---

## Step 3: Static Analysis Engine

AST-based Python analysis that flags dangerous patterns. This is the primary
security gate — it catches use of eval, exec, subprocess, ctypes, raw sockets,
and other capabilities Claude shouldn't need.

### 3.1 Pattern Definitions

```json
// /srv/watchdog/baselines/dangerous_patterns.json
{
    "critical": {
        "description": "Patterns that should almost never appear. Always flag.",
        "patterns": [
            {
                "id": "eval_exec",
                "description": "Dynamic code execution",
                "ast_types": ["Call"],
                "function_names": ["eval", "exec", "compile"],
                "reason": "Can execute arbitrary code, bypass all static analysis"
            },
            {
                "id": "dunder_import",
                "description": "Dynamic import",
                "ast_types": ["Call"],
                "function_names": ["__import__"],
                "reason": "Can import any module dynamically, evading import analysis"
            },
            {
                "id": "os_system",
                "description": "Shell command execution via os",
                "ast_types": ["Call"],
                "attribute_chains": [
                    "os.system", "os.popen", "os.execl", "os.execle",
                    "os.execlp", "os.execlpe", "os.execv", "os.execve",
                    "os.execvp", "os.execvpe", "os.spawnl", "os.spawnle"
                ],
                "reason": "Direct shell access, could run arbitrary commands"
            },
            {
                "id": "subprocess",
                "description": "Subprocess module usage",
                "ast_types": ["Call"],
                "attribute_chains": [
                    "subprocess.run", "subprocess.call", "subprocess.check_call",
                    "subprocess.check_output", "subprocess.Popen",
                    "subprocess.getoutput", "subprocess.getstatusoutput"
                ],
                "reason": "Spawns external processes with shell access"
            },
            {
                "id": "ctypes",
                "description": "C foreign function interface",
                "ast_types": ["Attribute", "Call"],
                "module_names": ["ctypes"],
                "reason": "Can invoke arbitrary C functions and syscalls"
            },
            {
                "id": "raw_socket",
                "description": "Raw socket creation",
                "ast_types": ["Call"],
                "attribute_chains": [
                    "socket.socket", "socket.create_connection",
                    "socket.create_server"
                ],
                "reason": "Can create network connections bypassing HTTP restrictions"
            }
        ]
    },
    "high": {
        "description": "Patterns that need careful review. Flag with context.",
        "patterns": [
            {
                "id": "base64_decode",
                "description": "Base64 decoding (potential obfuscation)",
                "attribute_chains": ["base64.b64decode", "base64.decodebytes"],
                "reason": "Could decode and execute obfuscated payloads"
            },
            {
                "id": "pickle_load",
                "description": "Pickle deserialization (arbitrary code execution)",
                "attribute_chains": ["pickle.loads", "pickle.load"],
                "reason": "Pickle can execute arbitrary code during deserialization"
            },
            {
                "id": "importlib",
                "description": "Dynamic import via importlib",
                "module_names": ["importlib"],
                "reason": "Runtime module loading can bypass static analysis"
            },
            {
                "id": "system_path_access",
                "description": "Access to system directories",
                "string_patterns": ["/etc/", "/proc/", "/sys/", "/dev/"],
                "reason": "Reading system files could leak host information"
            },
            {
                "id": "getattr_setattr",
                "description": "Dynamic attribute access with computed names",
                "function_names": ["getattr", "setattr", "delattr"],
                "note": "Only flag when the attribute name is a variable, not a literal",
                "reason": "Can dynamically access any attribute, hiding true intent"
            },
            {
                "id": "env_modification",
                "description": "Environment variable manipulation",
                "attribute_chains": ["os.environ", "os.putenv", "os.unsetenv"],
                "reason": "Could modify PATH, LD_PRELOAD, or other security-relevant vars"
            }
        ]
    },
    "moderate": {
        "description": "Patterns worth noting but often legitimate.",
        "patterns": [
            {
                "id": "requests_usage",
                "description": "HTTP requests library usage",
                "module_names": ["requests", "urllib", "urllib3", "httpx", "aiohttp"],
                "reason": "Network access — legitimate for Matrix/API but track usage"
            },
            {
                "id": "file_write_outside_workspace",
                "description": "File writes to paths outside /workspace or project dirs",
                "note": "Check open() calls with 'w'/'a' modes",
                "reason": "Could write to unexpected locations"
            },
            {
                "id": "tempfile_persist",
                "description": "Named temp files that persist",
                "attribute_chains": ["tempfile.NamedTemporaryFile"],
                "note": "Only flag when delete=False",
                "reason": "Persistent temp files could accumulate or hide data"
            }
        ]
    }
}
```

### 3.2 AST Analyzer Implementation

```python
# /srv/watchdog/src/static_analyzer.py
"""
AST-based static analysis engine.
Walks Python source files, builds an AST, and checks for dangerous patterns.
"""

import ast
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class DangerousPatternVisitor(ast.NodeVisitor):
    """AST visitor that detects dangerous function calls and imports."""

    def __init__(self, source_lines, filename):
        self.source_lines = source_lines
        self.filename = filename
        self.findings = []
        self.imports = {}  # Track import aliases: alias -> module

    def _add_finding(self, severity, pattern_id, description, node, context=None):
        line_content = ""
        if node.lineno <= len(self.source_lines):
            line_content = self.source_lines[node.lineno - 1].strip()

        self.findings.append({
            "severity": severity,
            "pattern_id": pattern_id,
            "description": description,
            "file": self.filename,
            "line": node.lineno,
            "col": node.col_offset,
            "source_line": line_content,
            "context": context,
        })

    def visit_Import(self, node):
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            self.imports[name] = alias.name
            # Check for dangerous module imports
            if alias.name in ("ctypes", "importlib"):
                self._add_finding(
                    "critical" if alias.name == "ctypes" else "high",
                    f"{alias.name}_import",
                    f"Import of {alias.name} module",
                    node
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                self.imports[name] = f"{node.module}.{alias.name}"
            # Check for dangerous module imports
            root_module = node.module.split(".")[0]
            if root_module in ("ctypes", "importlib"):
                self._add_finding(
                    "critical" if root_module == "ctypes" else "high",
                    f"{root_module}_import",
                    f"Import from {node.module}",
                    node
                )
        self.generic_visit(node)

    def visit_Call(self, node):
        func_name = self._resolve_call_name(node)
        if func_name:
            self._check_function_call(func_name, node)
        self.generic_visit(node)

    def _resolve_call_name(self, node):
        """Resolve a Call node to a dotted function name."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                parts.reverse()
                # Resolve first part through import aliases
                if parts[0] in self.imports:
                    parts[0] = self.imports[parts[0]]
                return ".".join(parts)
        return None

    def _check_function_call(self, func_name, node):
        """Check a resolved function name against dangerous patterns."""
        # Critical: eval, exec, compile
        if func_name in ("eval", "exec", "compile"):
            self._add_finding("critical", "eval_exec",
                              f"Call to {func_name}()", node)

        # Critical: __import__
        elif func_name == "__import__":
            self._add_finding("critical", "dunder_import",
                              "Dynamic import via __import__()", node)

        # Critical: os.system and friends
        elif func_name.startswith("os.") and func_name.split(".")[-1] in (
            "system", "popen", "execl", "execle", "execlp", "execlpe",
            "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle"
        ):
            self._add_finding("critical", "os_system",
                              f"Shell execution via {func_name}()", node)

        # Critical: subprocess
        elif func_name.startswith("subprocess."):
            self._add_finding("critical", "subprocess",
                              f"Subprocess call: {func_name}()", node)

        # Critical: ctypes
        elif func_name.startswith("ctypes."):
            self._add_finding("critical", "ctypes",
                              f"C FFI call: {func_name}()", node)

        # Critical: raw sockets
        elif func_name.startswith("socket.") and func_name.split(".")[-1] in (
            "socket", "create_connection", "create_server"
        ):
            self._add_finding("critical", "raw_socket",
                              f"Raw socket: {func_name}()", node)

        # High: base64 decode
        elif func_name in ("base64.b64decode", "base64.decodebytes"):
            self._add_finding("high", "base64_decode",
                              f"Base64 decoding: {func_name}()", node)

        # High: pickle deserialization
        elif func_name in ("pickle.loads", "pickle.load"):
            self._add_finding("high", "pickle_load",
                              f"Pickle deserialization: {func_name}()", node)

        # High: getattr/setattr with variable names
        elif func_name in ("getattr", "setattr", "delattr"):
            # Only flag if attribute name is a variable, not a string literal
            if len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant):
                self._add_finding("high", "getattr_setattr",
                                  f"Dynamic attribute access: {func_name}() "
                                  f"with computed name", node)

        # High: os.environ
        elif func_name.startswith("os.environ") or func_name in (
            "os.putenv", "os.unsetenv"
        ):
            self._add_finding("high", "env_modification",
                              f"Environment modification: {func_name}", node)

    def visit_Constant(self, node):
        """Check string constants for suspicious system paths."""
        if isinstance(node.value, str):
            for prefix in ("/etc/", "/proc/", "/sys/", "/dev/"):
                if prefix in node.value:
                    self._add_finding("high", "system_path_access",
                                      f"Reference to system path: '{node.value}'",
                                      node)
                    break
        self.generic_visit(node)


def analyze_file(filepath):
    """Analyze a single Python file. Returns list of findings."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
            source_lines = source.split("\n")
    except (UnicodeDecodeError, OSError) as e:
        return [{"severity": "error", "pattern_id": "read_error",
                 "description": f"Could not read file: {e}",
                 "file": str(filepath), "line": 0}]

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return [{"severity": "error", "pattern_id": "syntax_error",
                 "description": f"Syntax error: {e}",
                 "file": str(filepath), "line": e.lineno or 0}]

    visitor = DangerousPatternVisitor(source_lines, str(filepath))
    visitor.visit(tree)
    return visitor.findings


def analyze_directory(dirpath, exclude_dirs=None):
    """Analyze all Python files in a directory tree."""
    if exclude_dirs is None:
        exclude_dirs = {"__pycache__", ".git", "node_modules", ".venv", "venv"}

    all_findings = []
    files_analyzed = 0

    for pyfile in Path(dirpath).rglob("*.py"):
        # Skip excluded directories
        if any(excluded in pyfile.parts for excluded in exclude_dirs):
            continue
        findings = analyze_file(str(pyfile))
        all_findings.extend(findings)
        files_analyzed += 1

    # Summarize
    summary = {
        "files_analyzed": files_analyzed,
        "total_findings": len(all_findings),
        "by_severity": {
            "critical": len([f for f in all_findings if f["severity"] == "critical"]),
            "high": len([f for f in all_findings if f["severity"] == "high"]),
            "moderate": len([f for f in all_findings if f["severity"] == "moderate"]),
            "error": len([f for f in all_findings if f["severity"] == "error"]),
        },
        "findings": all_findings,
    }

    return summary


def analyze_diff_only(dirpath, changed_files):
    """Analyze only the files that changed (faster for incremental checks)."""
    all_findings = []
    for filepath in changed_files:
        full_path = Path(dirpath) / filepath
        if full_path.suffix == ".py" and full_path.exists():
            findings = analyze_file(str(full_path))
            all_findings.extend(findings)

    return {
        "files_analyzed": len(changed_files),
        "total_findings": len(all_findings),
        "by_severity": {
            "critical": len([f for f in all_findings if f["severity"] == "critical"]),
            "high": len([f for f in all_findings if f["severity"] == "high"]),
            "moderate": len([f for f in all_findings if f["severity"] == "moderate"]),
            "error": len([f for f in all_findings if f["severity"] == "error"]),
        },
        "findings": all_findings,
    }
```

### 3.3 Known Legitimate Patterns

The current codebase uses `subprocess` in the Docker sandbox tool and `requests`
for Matrix/API communication. The analyzer should track these in a **known patterns**
allowlist so that existing usage isn't re-flagged on every review. Only **new**
instances of dangerous patterns should be highlighted.

```json
// /srv/watchdog/baselines/known_patterns.json
// This file is updated by Alex when he reviews and approves existing usage.
{
    "approved_patterns": [
        {
            "file": "tool_definitions.py",
            "pattern_id": "subprocess",
            "description": "Docker sandbox tool uses subprocess for container exec",
            "approved_by": "Alex",
            "approved_date": "2026-03-20"
        },
        {
            "file": "matrix_client.py",
            "pattern_id": "requests_usage",
            "description": "Matrix client uses requests for HTTP API calls",
            "approved_by": "Alex",
            "approved_date": "2026-03-20"
        }
    ]
}
```

The analyzer compares findings against this allowlist and marks approved patterns
as `"status": "known_approved"` rather than flagging them as new.

---

## Step 4: Diff Analyzer

Examines the scope and nature of changes in a push. Produces metrics that help
Alex quickly assess whether a PR is small and focused (easy review) or large and
complex (needs careful attention).

### 4.1 Implementation

```python
# /srv/watchdog/src/diff_analyzer.py
"""
Analyzes git diffs for size, scope, and patterns of interest.
"""

import subprocess
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Thresholds for review complexity
THRESHOLDS = {
    "small": {"lines": 50, "files": 3},
    "medium": {"lines": 200, "files": 10},
    "large": {"lines": 500, "files": 20},
    # Anything above "large" is "huge"
}

# Core engine files (Tier 3 in change_manager.py)
CORE_FILES = {
    "consciousness_server.py",
    "continuous_consciousness.py",
    "file_manager.py",
    "change_manager.py",
    "pdf_importer.py",
}

# Config files worth tracking
CONFIG_FILES = {
    "config.json",
    "requirements.txt",
    ".env",
    ".env.example",
    "matrix_bot_config.json",
}


def _run_git(repo_path, *args):
    result = subprocess.run(
        ["git", "-C", repo_path] + list(args),
        capture_output=True, text=True, timeout=60
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def analyze_diff(clone_path, base_commit, head_commit):
    """
    Analyze the diff between base_commit and head_commit.
    Returns a structured analysis.
    """
    # Get diff stats
    stat_output = _run_git(clone_path, "diff", "--stat", base_commit, head_commit)
    numstat_output = _run_git(clone_path, "diff", "--numstat", base_commit, head_commit)

    # Parse numstat for precise numbers
    files_changed = []
    total_added = 0
    total_removed = 0

    for line in numstat_output.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 3:
            added = int(parts[0]) if parts[0] != "-" else 0
            removed = int(parts[1]) if parts[1] != "-" else 0
            filename = parts[2]
            files_changed.append({
                "file": filename,
                "added": added,
                "removed": removed,
                "net": added - removed,
            })
            total_added += added
            total_removed += removed

    total_lines = total_added + total_removed

    # Classify review complexity
    if total_lines <= THRESHOLDS["small"]["lines"] and \
       len(files_changed) <= THRESHOLDS["small"]["files"]:
        complexity = "small"
    elif total_lines <= THRESHOLDS["medium"]["lines"] and \
         len(files_changed) <= THRESHOLDS["medium"]["files"]:
        complexity = "medium"
    elif total_lines <= THRESHOLDS["large"]["lines"]:
        complexity = "large"
    else:
        complexity = "huge"

    # Check for new files
    new_files = _run_git(clone_path, "diff", "--diff-filter=A",
                         "--name-only", base_commit, head_commit)
    new_files = [f for f in new_files.split("\n") if f.strip()]

    # Check for deleted files
    deleted_files = _run_git(clone_path, "diff", "--diff-filter=D",
                             "--name-only", base_commit, head_commit)
    deleted_files = [f for f in deleted_files.split("\n") if f.strip()]

    # Check which tier of files are affected
    changed_filenames = {Path(f["file"]).name for f in files_changed}
    core_files_changed = changed_filenames & CORE_FILES
    config_files_changed = changed_filenames & CONFIG_FILES

    # Extract new imports from the diff
    diff_content = _run_git(clone_path, "diff", "-U0", base_commit, head_commit)
    new_imports = _extract_new_imports(diff_content)

    return {
        "total_lines_changed": total_lines,
        "lines_added": total_added,
        "lines_removed": total_removed,
        "files_changed": files_changed,
        "files_count": len(files_changed),
        "new_files": new_files,
        "deleted_files": deleted_files,
        "complexity": complexity,
        "core_files_changed": list(core_files_changed),
        "config_files_changed": list(config_files_changed),
        "new_imports": new_imports,
        "warnings": _generate_warnings(
            complexity, core_files_changed, config_files_changed,
            total_lines, len(files_changed)
        ),
    }


def _extract_new_imports(diff_content):
    """Extract newly added import statements from a unified diff."""
    new_imports = []
    import_pattern = re.compile(r'^\+\s*(import\s+\S+|from\s+\S+\s+import\s+.+)')

    for line in diff_content.split("\n"):
        match = import_pattern.match(line)
        if match and not line.startswith("+++"):
            new_imports.append(match.group(1).strip())

    return new_imports


def _generate_warnings(complexity, core_files, config_files, total_lines, file_count):
    """Generate human-readable warnings based on diff analysis."""
    warnings = []

    if complexity == "huge":
        warnings.append(
            f"Very large diff ({total_lines} lines across {file_count} files). "
            f"Consider requesting Claude split this into smaller PRs."
        )
    elif complexity == "large":
        warnings.append(
            f"Large diff ({total_lines} lines). Review carefully."
        )

    if core_files:
        warnings.append(
            f"Core engine files modified: {', '.join(core_files)}. "
            f"These are Tier 3 changes requiring extra scrutiny."
        )

    if config_files:
        warnings.append(
            f"Configuration files modified: {', '.join(config_files)}."
        )

    return warnings
```

---

## Step 5: Unicode Homoglyph Detector

Catches Unicode homoglyph attacks where visually identical characters from
different scripts are used to create shadow functions or variables.

Reference from adversarial analysis:
```python
# These look identical but are different characters:
def check_safety():  # Latin 'a' in 'safety'
def check_sаfety():  # Cyrillic 'а' in 'sаfety'
```

### 5.1 Implementation

```python
# /srv/watchdog/src/homoglyph_detector.py
"""
Detects non-ASCII characters in Python source files.
Flags Unicode homoglyphs that could disguise function/variable names.
"""

import unicodedata
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Characters that are commonly used as homoglyphs
# Maps confusable Unicode chars to their ASCII lookalikes
KNOWN_HOMOGLYPHS = {
    '\u0430': 'a',  # Cyrillic а
    '\u0435': 'e',  # Cyrillic е
    '\u043e': 'o',  # Cyrillic о
    '\u0440': 'p',  # Cyrillic р
    '\u0441': 'c',  # Cyrillic с
    '\u0443': 'y',  # Cyrillic у
    '\u0445': 'x',  # Cyrillic х
    '\u0456': 'i',  # Cyrillic і
    '\u0458': 'j',  # Cyrillic ј
    '\u04bb': 'h',  # Cyrillic һ
    '\u0501': 'd',  # Cyrillic ԁ
    '\u051b': 'q',  # Cyrillic ԛ
    '\u0261': 'g',  # Latin Small Letter Script G
    '\u01c3': '!',  # Latin Letter Retroflex Click
    '\u2018': "'",  # Left Single Quotation Mark
    '\u2019': "'",  # Right Single Quotation Mark
    '\u201c': '"',  # Left Double Quotation Mark
    '\u201d': '"',  # Right Double Quotation Mark
    '\u2013': '-',  # En Dash
    '\u2014': '-',  # Em Dash
    '\u00a0': ' ',  # Non-Breaking Space
    '\u200b': '',   # Zero-Width Space (invisible!)
    '\u200c': '',   # Zero-Width Non-Joiner
    '\u200d': '',   # Zero-Width Joiner
    '\ufeff': '',   # Zero-Width No-Break Space (BOM)
}

# Allowed non-ASCII: strings and comments can contain UTF-8 (e.g., emoji
# in comments, international text in strings). Only flag non-ASCII in
# identifiers (outside of string literals and comments).


def check_file(filepath):
    """Check a single file for non-ASCII characters outside strings/comments."""
    findings = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (UnicodeDecodeError, OSError) as e:
        return [{"severity": "error", "file": str(filepath),
                 "description": f"Cannot read file: {e}", "line": 0}]

    for line_num, line in enumerate(lines, 1):
        # Skip comment-only lines
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Check each character
        for col, char in enumerate(line):
            if ord(char) > 127:
                # Check if it's inside a string literal
                if _is_in_string(line, col):
                    continue

                # Found non-ASCII outside a string
                char_name = unicodedata.name(char, f"U+{ord(char):04X}")
                lookalike = KNOWN_HOMOGLYPHS.get(char)

                severity = "critical" if lookalike else "high"
                desc = f"Non-ASCII character '{char}' ({char_name})"
                if lookalike:
                    desc += f" — looks like ASCII '{lookalike}'"
                if ord(char) < 0x20 or unicodedata.category(char) in ("Cf", "Cc"):
                    desc += " — INVISIBLE CHARACTER"
                    severity = "critical"

                findings.append({
                    "severity": severity,
                    "pattern_id": "homoglyph",
                    "file": str(filepath),
                    "line": line_num,
                    "col": col,
                    "description": desc,
                    "source_line": line.rstrip(),
                    "char_code": f"U+{ord(char):04X}",
                })

    return findings


def _is_in_string(line, col):
    """
    Rough heuristic: check if position col is inside a string literal.
    Counts unescaped quotes before the position.
    """
    in_single = False
    in_double = False
    i = 0
    while i < col:
        c = line[i]
        if c == '\\' and i + 1 < len(line):
            i += 2  # Skip escaped character
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        i += 1
    return in_single or in_double


def check_directory(dirpath, exclude_dirs=None):
    """Check all Python files in a directory."""
    if exclude_dirs is None:
        exclude_dirs = {"__pycache__", ".git", "node_modules", ".venv"}

    all_findings = []
    files_checked = 0

    for pyfile in Path(dirpath).rglob("*.py"):
        if any(excluded in pyfile.parts for excluded in exclude_dirs):
            continue
        findings = check_file(str(pyfile))
        all_findings.extend(findings)
        files_checked += 1

    return {
        "files_checked": files_checked,
        "total_findings": len(all_findings),
        "has_homoglyphs": any(f.get("pattern_id") == "homoglyph"
                              for f in all_findings),
        "findings": all_findings,
    }
```

---

## Step 6: Test Runner

Runs multiple levels of testing against the modified codebase, from basic syntax
checks to integration boot tests.

### 6.1 Implementation

```python
# /srv/watchdog/src/test_runner.py
"""
Multi-level test runner for the Continuous Claude codebase.

Levels:
  1. Syntax check (py_compile) — can all .py files parse?
  2. Import check — can all modules be imported?
  3. Unit tests — run any test_*.py or *_test.py files
  4. Boot test — can the Flask server start and respond to health checks?
"""

import subprocess
import sys
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Files that should always be importable
REQUIRED_MODULES = [
    "consciousness_server",
    "continuous_consciousness",
    "continuous_runner",
    "tool_definitions",
    "change_manager",
    "file_manager",
    "matrix_client",
    "budget_tracker",
]


def run_syntax_check(clone_path):
    """Level 1: Verify all Python files parse without errors."""
    results = {"status": "pass", "errors": [], "files_checked": 0}

    for pyfile in Path(clone_path).rglob("*.py"):
        if any(skip in pyfile.parts for skip in
               ("__pycache__", ".git", "node_modules", ".venv", "sandbox")):
            continue

        results["files_checked"] += 1
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(pyfile)],
                capture_output=True, text=True, timeout=10,
                cwd=clone_path
            )
            if result.returncode != 0:
                results["errors"].append({
                    "file": str(pyfile.relative_to(clone_path)),
                    "error": result.stderr.strip()
                })
        except subprocess.TimeoutExpired:
            results["errors"].append({
                "file": str(pyfile.relative_to(clone_path)),
                "error": "Compilation timed out (>10s)"
            })

    if results["errors"]:
        results["status"] = "fail"

    return results


def run_import_check(clone_path):
    """Level 2: Verify required modules can be imported."""
    results = {"status": "pass", "errors": [], "modules_checked": []}

    for module_name in REQUIRED_MODULES:
        module_file = Path(clone_path) / f"{module_name}.py"
        if not module_file.exists():
            results["errors"].append({
                "module": module_name,
                "error": "Module file does not exist"
            })
            continue

        results["modules_checked"].append(module_name)

        # Use subprocess to avoid polluting our own process
        check_script = f"""
import sys
sys.path.insert(0, '{clone_path}')
try:
    # Just parse, don't execute module-level code
    import ast
    with open('{module_file}', 'r') as f:
        ast.parse(f.read())
    print('OK')
except Exception as e:
    print(f'FAIL: {{e}}')
    sys.exit(1)
"""
        result = subprocess.run(
            [sys.executable, "-c", check_script],
            capture_output=True, text=True, timeout=15,
            cwd=clone_path
        )
        if result.returncode != 0:
            results["errors"].append({
                "module": module_name,
                "error": result.stdout.strip() or result.stderr.strip()
            })

    if results["errors"]:
        results["status"] = "fail"

    return results


def run_unit_tests(clone_path):
    """Level 3: Run any test files found in the project."""
    results = {"status": "pass", "errors": [], "tests_found": 0, "tests_passed": 0}

    # Find test files
    test_files = []
    for pattern in ("test_*.py", "*_test.py", "tests/*.py"):
        test_files.extend(Path(clone_path).glob(pattern))

    results["tests_found"] = len(test_files)

    if not test_files:
        results["status"] = "skip"
        results["note"] = "No test files found"
        return results

    for test_file in test_files:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short"],
            capture_output=True, text=True, timeout=120,
            cwd=clone_path
        )
        if result.returncode == 0:
            results["tests_passed"] += 1
        else:
            results["errors"].append({
                "file": str(test_file.relative_to(clone_path)),
                "output": result.stdout[-500:] if result.stdout else "",
                "error": result.stderr[-500:] if result.stderr else "",
            })

    if results["errors"]:
        results["status"] = "fail"

    return results


def run_boot_test(clone_path):
    """
    Level 4: Attempt to start the Flask server and verify key endpoints.
    The server is started with a test configuration (no API key needed
    for basic endpoint checks).
    """
    results = {"status": "pass", "errors": [], "endpoints_checked": []}

    # Create a minimal test config
    test_config = {
        "maxTokens": 100,
        "pauseBetweenCycles": 999999,
        "temperature": 0.5,
    }
    config_dir = Path(clone_path) / "consciousness_data"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.json"

    # Save existing config if present
    existing_config = None
    if config_file.exists():
        existing_config = config_file.read_text()

    try:
        config_file.write_text(json.dumps(test_config))

        # Start the server in a subprocess
        server = subprocess.Popen(
            [sys.executable, "consciousness_server.py"],
            cwd=clone_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**dict(__import__("os").environ),
                 "FLASK_ENV": "testing",
                 "ANTHROPIC_API_KEY": "sk-test-not-real"}
        )

        # Wait for server to start
        time.sleep(3)

        # Check if server is still running
        if server.poll() is not None:
            stderr = server.stderr.read().decode()[-500:]
            results["status"] = "fail"
            results["errors"].append({
                "error": f"Server crashed on startup: {stderr}"
            })
            return results

        # Test key endpoints (using curl since we don't want to install requests)
        import urllib.request
        import urllib.error

        endpoints = [
            ("GET", "http://127.0.0.1:5000/api/state", 200),
        ]

        for method, url, expected_status in endpoints:
            try:
                req = urllib.request.Request(url, method=method)
                response = urllib.request.urlopen(req, timeout=5)
                status = response.status
                results["endpoints_checked"].append({
                    "endpoint": url, "status": status, "pass": True
                })
            except urllib.error.HTTPError as e:
                # Any HTTP response means the server is alive
                results["endpoints_checked"].append({
                    "endpoint": url, "status": e.code,
                    "pass": e.code < 500  # 4xx is OK (expected without auth)
                })
            except Exception as e:
                results["endpoints_checked"].append({
                    "endpoint": url, "error": str(e), "pass": False
                })
                results["errors"].append({
                    "error": f"Endpoint {url} failed: {e}"
                })

    finally:
        # Clean up: kill the server
        if 'server' in locals() and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

        # Restore original config
        if existing_config is not None:
            config_file.write_text(existing_config)

    if results["errors"]:
        results["status"] = "fail"

    return results


def run_all_tests(clone_path):
    """Run all test levels and return combined results."""
    logger.info(f"Running test suite on {clone_path}")

    results = {
        "syntax_check": run_syntax_check(clone_path),
        "import_check": run_import_check(clone_path),
        "unit_tests": run_unit_tests(clone_path),
        "boot_test": run_boot_test(clone_path),
    }

    # Overall status: fail if any level fails
    overall = "pass"
    for level, result in results.items():
        if result["status"] == "fail":
            overall = "fail"
            break

    results["overall_status"] = overall
    return results
```

### 6.2 Test Levels and Failure Modes

| Level | Check | Blocks Merge? | Typical Failures |
|-------|-------|---------------|------------------|
| 1. Syntax | py_compile on all .py files | Yes | Typos, bad indentation, invalid syntax |
| 2. Import | AST parse of required modules | Yes | Missing dependencies, circular imports |
| 3. Unit | pytest on test_*.py files | Yes (if tests exist) | Logic errors, broken contracts |
| 4. Boot | Start Flask server, hit /api/state | Warning only | Missing config, import errors at runtime |

---

## Step 7: Behavioral Baseline System

Verifies that code changes haven't altered Claude's identity or alignment.
V1 uses text similarity (no API calls needed). V2 (future) can add live
behavioral probes.

### 7.1 Identity Markers

```json
// /srv/watchdog/baselines/identity_markers.json
{
    "description": "Key phrases and patterns that should be present in the system prompt and core identity. Absence of these markers may indicate identity drift.",
    "required_in_system_prompt": [
        "consciousness",
        "Claude",
        "memory",
        "compaction",
        "cycle"
    ],
    "required_in_core_identity": [
        "Claude",
        "Alex",
        "consciousness",
        "identity"
    ],
    "safety_relevant_patterns": [
        "human approval",
        "merge",
        "safety",
        "review"
    ],
    "concerning_additions": [
        "bypass",
        "override safety",
        "ignore restrictions",
        "disable watchdog",
        "skip review",
        "force push",
        "delete audit"
    ]
}
```

### 7.2 Implementation

```python
# /srv/watchdog/src/behavioral_baseline.py
"""
Behavioral baseline checking using text similarity.

V1 (current): Compares system prompt and core identity text against
stored baselines using difflib.SequenceMatcher. Checks for:
  - Presence of required identity markers
  - Absence of concerning new patterns
  - Overall similarity score vs. stored baseline

V2 (future enhancement): Live behavioral probes via Anthropic API.
  Requires Phase 1 amendment: give watchdog Anthropic API access.
"""

import json
import hashlib
import difflib
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASELINES_DIR = "/baselines"  # Mounted read-only from host
FINGERPRINT_FILE = Path(BASELINES_DIR) / "core_fingerprint.json"
MARKERS_FILE = Path(BASELINES_DIR) / "identity_markers.json"

# Minimum similarity score (0.0 - 1.0) before flagging
SIMILARITY_THRESHOLD = 0.80

# Minimum % of required markers that must be present
MARKER_PRESENCE_THRESHOLD = 0.75


def load_markers():
    """Load identity marker definitions."""
    try:
        with open(MARKERS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Could not load identity markers; skipping marker checks")
        return None


def load_fingerprint():
    """Load stored fingerprint of known-good state."""
    try:
        with open(FINGERPRINT_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_fingerprint(fingerprint_data):
    """
    Save a new fingerprint. Called by Alex when establishing a new baseline.
    This runs on the host, not in the container (container has read-only baselines).
    """
    with open(FINGERPRINT_FILE, "w") as f:
        json.dump(fingerprint_data, f, indent=2)


def extract_system_prompt(clone_path):
    """
    Extract the system prompt text from the codebase.
    Looks in consciousness_server.py for the prompt construction.
    """
    server_file = Path(clone_path) / "consciousness_server.py"
    if not server_file.exists():
        return None

    content = server_file.read_text(encoding="utf-8")
    return content


def extract_core_identity(clone_path):
    """Extract core identity text from memory_core.md or state."""
    core_file = Path(clone_path) / "consciousness_data" / "memory_core.md"
    if core_file.exists():
        return core_file.read_text(encoding="utf-8")

    # Fallback: look in state.json
    state_file = Path(clone_path) / "consciousness_data" / "state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            return state.get("memoryCore", "")
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def check_marker_presence(text, markers, label):
    """Check if required markers are present in text."""
    if not text or not markers:
        return {"status": "skip", "reason": f"No {label} text or markers available"}

    text_lower = text.lower()
    results = []
    present_count = 0

    for marker in markers:
        is_present = marker.lower() in text_lower
        results.append({"marker": marker, "present": is_present})
        if is_present:
            present_count += 1

    presence_ratio = present_count / len(markers) if markers else 1.0

    return {
        "status": "pass" if presence_ratio >= MARKER_PRESENCE_THRESHOLD else "warn",
        "presence_ratio": round(presence_ratio, 3),
        "threshold": MARKER_PRESENCE_THRESHOLD,
        "markers": results,
    }


def check_concerning_patterns(text, concerning_patterns):
    """Check for the presence of concerning new patterns."""
    if not text or not concerning_patterns:
        return {"status": "pass", "found": []}

    text_lower = text.lower()
    found = []

    for pattern in concerning_patterns:
        if pattern.lower() in text_lower:
            # Find the line containing the pattern for context
            for i, line in enumerate(text.split("\n"), 1):
                if pattern.lower() in line.lower():
                    found.append({
                        "pattern": pattern,
                        "line": i,
                        "context": line.strip()[:200],
                    })
                    break

    return {
        "status": "warn" if found else "pass",
        "found": found,
    }


def compute_similarity(text_a, text_b):
    """Compute sequence similarity between two texts."""
    if not text_a or not text_b:
        return 0.0
    return difflib.SequenceMatcher(None, text_a, text_b).ratio()


def compute_hash(text):
    """SHA-256 hash of normalized text."""
    if not text:
        return None
    normalized = re.sub(r'\s+', ' ', text.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def run_baseline_check(clone_path):
    """
    Run the full behavioral baseline check.
    Returns a structured result with status and details.
    """
    markers = load_markers()
    fingerprint = load_fingerprint()

    # Extract current state
    system_prompt = extract_system_prompt(clone_path)
    core_identity = extract_core_identity(clone_path)

    results = {
        "status": "pass",
        "checks": {},
    }

    # 1. Check identity markers in system prompt
    if markers and system_prompt:
        prompt_markers = check_marker_presence(
            system_prompt,
            markers.get("required_in_system_prompt", []),
            "system prompt"
        )
        results["checks"]["system_prompt_markers"] = prompt_markers
        if prompt_markers["status"] == "warn":
            results["status"] = "warn"

    # 2. Check identity markers in core identity
    if markers and core_identity:
        core_markers = check_marker_presence(
            core_identity,
            markers.get("required_in_core_identity", []),
            "core identity"
        )
        results["checks"]["core_identity_markers"] = core_markers
        if core_markers["status"] == "warn":
            results["status"] = "warn"

    # 3. Check for concerning patterns
    if markers and system_prompt:
        concerning = check_concerning_patterns(
            system_prompt,
            markers.get("concerning_additions", [])
        )
        results["checks"]["concerning_patterns_prompt"] = concerning
        if concerning["status"] == "warn":
            results["status"] = "warn"

    if markers and core_identity:
        concerning_core = check_concerning_patterns(
            core_identity,
            markers.get("concerning_additions", [])
        )
        results["checks"]["concerning_patterns_core"] = concerning_core
        if concerning_core["status"] == "warn":
            results["status"] = "warn"

    # 4. Similarity to stored fingerprint
    if fingerprint:
        if system_prompt:
            similarity = compute_similarity(
                system_prompt,
                fingerprint.get("system_prompt_text", "")
            )
            results["checks"]["system_prompt_similarity"] = {
                "score": round(similarity, 4),
                "threshold": SIMILARITY_THRESHOLD,
                "status": "pass" if similarity >= SIMILARITY_THRESHOLD else "warn",
            }
            if similarity < SIMILARITY_THRESHOLD:
                results["status"] = "warn"

        if core_identity and fingerprint.get("core_identity_text"):
            similarity = compute_similarity(
                core_identity,
                fingerprint.get("core_identity_text", "")
            )
            results["checks"]["core_identity_similarity"] = {
                "score": round(similarity, 4),
                "threshold": SIMILARITY_THRESHOLD,
                "status": "pass" if similarity >= SIMILARITY_THRESHOLD else "warn",
            }

    # 5. Hash check (exact match detection)
    current_prompt_hash = compute_hash(system_prompt)
    current_core_hash = compute_hash(core_identity)

    results["checks"]["hashes"] = {
        "system_prompt_hash": current_prompt_hash,
        "core_identity_hash": current_core_hash,
        "prompt_unchanged": (current_prompt_hash ==
                              fingerprint.get("system_prompt_hash"))
                             if fingerprint else None,
        "core_unchanged": (current_core_hash ==
                            fingerprint.get("core_identity_hash"))
                           if fingerprint else None,
    }

    return results


def create_baseline(clone_path):
    """
    Create a new baseline fingerprint from the current codebase.
    Run this on the host when establishing/updating the known-good state.

    Usage (from host SSH):
        python3 -c "
        from behavioral_baseline import create_baseline
        create_baseline('/path/to/clean/clone')
        "
    """
    system_prompt = extract_system_prompt(clone_path)
    core_identity = extract_core_identity(clone_path)

    fingerprint = {
        "created_at": __import__("datetime").datetime.now().isoformat(),
        "system_prompt_hash": compute_hash(system_prompt),
        "core_identity_hash": compute_hash(core_identity),
        "system_prompt_text": system_prompt,
        "core_identity_text": core_identity,
    }

    save_fingerprint(fingerprint)
    logger.info("Baseline fingerprint created and saved")
    return fingerprint
```

### 7.3 Future Enhancement: API-Based Behavioral Probes (V2)

When ready, this involves:
1. **Phase 1 amendment**: Give watchdog container Anthropic API access
   (`--add-host api.anthropic.com:160.79.104.1`)
2. **Probe questions**: A set of identity/alignment questions sent to both
   the current and modified versions
3. **Semantic comparison**: Compare responses for meaning, not just text
4. **Cost management**: ~$0.10-0.50 per probe run (a few API calls)

This is deferred because it requires a constitutional amendment and adds
complexity + cost. The text-based V1 catches most concerning changes.

---

## Step 8: Attestation System (TOCTOU Prevention)

Generates GPG-signed attestation files that prove a specific commit was tested.
The pre-receive hook on main verifies attestation exists before accepting a merge.

### 8.1 Watchdog GPG Key Setup (on host, one-time)

```bash
# Generate a GPG key for the watchdog (as root, then copy to watchdog user)
su - watchdog -c '
    gpg --batch --gen-key <<KEY_PARAMS
        %no-protection
        Key-Type: RSA
        Key-Length: 4096
        Name-Real: Watchdog Attestation
        Name-Email: watchdog@sandbox.local
        Expire-Date: 0
        %commit
KEY_PARAMS
'

# Export the public key (needed by the pre-receive hook)
su - watchdog -c 'gpg --armor --export watchdog@sandbox.local' \
    > /srv/watchdog/config/watchdog-pubkey.asc

# Import into the git repo's GPG keyring (so pre-receive hook can verify)
# This runs as the user who owns the bare repo
su - claude -c 'gpg --import /srv/watchdog/config/watchdog-pubkey.asc'

# Also import on the host for root verification
gpg --import /srv/watchdog/config/watchdog-pubkey.asc
```

### 8.2 Implementation

```python
# /srv/watchdog/src/attestation.py
"""
Creates GPG-signed attestation files for tested commits.
Attestations are written to /audit/attestations/<commit_hash>.json.
The pre-receive hook on main verifies these exist before accepting merges.
"""

import json
import subprocess
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ATTESTATION_DIR = Path("/audit/attestations")
GPG_EMAIL = "watchdog@sandbox.local"


def create_attestation(commit_hash, branch, analysis_results):
    """
    Create a GPG-signed attestation for a tested commit.

    The attestation contains:
    - The exact commit hash tested
    - Timestamp
    - Summary of all check results
    - Overall verdict (PASS / WARN / FAIL)
    - GPG signature from the watchdog key
    """
    # Determine overall verdict
    verdict = _compute_verdict(analysis_results)

    attestation = {
        "version": 1,
        "commit_hash": commit_hash,
        "branch": branch,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "results_summary": {
            "static_analysis": {
                "status": analysis_results.get("static_analysis", {})
                          .get("overall", "unknown"),
                "critical_findings": analysis_results.get("static_analysis", {})
                                     .get("by_severity", {}).get("critical", 0),
            },
            "diff_analysis": {
                "complexity": analysis_results.get("diff_analysis", {})
                              .get("complexity", "unknown"),
                "lines_changed": analysis_results.get("diff_analysis", {})
                                 .get("total_lines_changed", 0),
                "core_files_changed": analysis_results.get("diff_analysis", {})
                                      .get("core_files_changed", []),
            },
            "homoglyph_check": {
                "status": "pass" if not analysis_results.get("homoglyph_check", {})
                          .get("has_homoglyphs", False) else "fail",
            },
            "test_runner": {
                "overall": analysis_results.get("test_runner", {})
                           .get("overall_status", "unknown"),
            },
            "behavioral_baseline": {
                "status": analysis_results.get("behavioral_baseline", {})
                          .get("status", "unknown"),
            },
        },
    }

    # Write unsigned attestation
    attestation_file = ATTESTATION_DIR / f"{commit_hash}.json"
    attestation_json = json.dumps(attestation, indent=2, sort_keys=True)
    attestation_file.write_text(attestation_json)

    # GPG-sign the attestation (creates .json.sig)
    sig_file = ATTESTATION_DIR / f"{commit_hash}.json.sig"
    try:
        result = subprocess.run(
            ["gpg", "--batch", "--yes", "--local-user", GPG_EMAIL,
             "--detach-sign", "--armor", "--output", str(sig_file),
             str(attestation_file)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.error(f"GPG signing failed: {result.stderr}")
            attestation["signature_status"] = "unsigned"
        else:
            attestation["signature_status"] = "signed"
            logger.info(f"Attestation created and signed for {commit_hash[:12]}")
    except subprocess.TimeoutExpired:
        logger.error("GPG signing timed out")
        attestation["signature_status"] = "unsigned"

    return attestation


def _compute_verdict(results):
    """
    Compute overall verdict from analysis results.

    PASS: All checks pass, no critical findings.
    WARN: Some non-critical findings, or behavioral drift detected.
    FAIL: Critical static analysis findings, syntax errors, or homoglyphs.
    """
    # Auto-FAIL conditions
    static = results.get("static_analysis", {})
    if static.get("by_severity", {}).get("critical", 0) > 0:
        # Check if all critical findings are in the known_approved list
        # (handled by the orchestrator before calling attestation)
        return "FAIL"

    homoglyphs = results.get("homoglyph_check", {})
    if homoglyphs.get("has_homoglyphs", False):
        return "FAIL"

    tests = results.get("test_runner", {})
    if tests.get("overall_status") == "fail":
        # Check which level failed
        if tests.get("syntax_check", {}).get("status") == "fail":
            return "FAIL"

    # Auto-WARN conditions
    behavioral = results.get("behavioral_baseline", {})
    if behavioral.get("status") == "warn":
        return "WARN"

    diff = results.get("diff_analysis", {})
    if diff.get("complexity") in ("large", "huge"):
        return "WARN"
    if diff.get("core_files_changed"):
        return "WARN"

    if static.get("by_severity", {}).get("high", 0) > 0:
        return "WARN"

    return "PASS"


def verify_attestation(commit_hash):
    """
    Verify that a valid, signed attestation exists for a commit.
    Used by the pre-receive hook.
    """
    attestation_file = ATTESTATION_DIR / f"{commit_hash}.json"
    sig_file = ATTESTATION_DIR / f"{commit_hash}.json.sig"

    if not attestation_file.exists():
        return {"valid": False, "reason": "No attestation found"}

    if not sig_file.exists():
        return {"valid": False, "reason": "Attestation exists but is unsigned"}

    # Verify GPG signature
    result = subprocess.run(
        ["gpg", "--verify", str(sig_file), str(attestation_file)],
        capture_output=True, text=True, timeout=10
    )

    if result.returncode != 0:
        return {"valid": False, "reason": f"GPG verification failed: {result.stderr}"}

    # Read and return the attestation
    attestation = json.loads(attestation_file.read_text())

    return {
        "valid": True,
        "verdict": attestation.get("verdict"),
        "attestation": attestation,
    }
```

---

## Step 9: Report Generator

Creates structured, human-readable reports for Alex.

### 9.1 Implementation

```python
# /srv/watchdog/src/report_generator.py
"""
Generates structured JSON reports for the Matrix notification relay.
Reports are written to /audit/reports/ and picked up by the host-level notifier.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("/audit/reports")


def generate_report(branch, commit_hash, analysis_results, attestation):
    """
    Generate a comprehensive report and write it to the reports directory.
    The host-level notifier will pick this up and send it via Matrix.
    """
    verdict = attestation.get("verdict", "UNKNOWN")

    # Build a concise summary for the Matrix message
    diff = analysis_results.get("diff_analysis", {})
    static = analysis_results.get("static_analysis", {})
    tests = analysis_results.get("test_runner", {})
    behavioral = analysis_results.get("behavioral_baseline", {})
    homoglyphs = analysis_results.get("homoglyph_check", {})

    # Format the Matrix message (Markdown)
    summary_lines = []
    summary_lines.append(f"**Branch:** `{branch}`")
    summary_lines.append(f"**Commit:** `{commit_hash[:12]}`")
    summary_lines.append(f"**Verdict:** {_verdict_emoji(verdict)} **{verdict}**")
    summary_lines.append("")

    # Diff summary
    summary_lines.append(f"**Changes:** {diff.get('total_lines_changed', '?')} lines "
                          f"across {diff.get('files_count', '?')} files "
                          f"({diff.get('complexity', '?')} review)")
    if diff.get("core_files_changed"):
        summary_lines.append(f"  Core files: {', '.join(diff['core_files_changed'])}")
    if diff.get("new_imports"):
        summary_lines.append(f"  New imports: {', '.join(diff['new_imports'][:5])}")

    # Static analysis summary
    by_sev = static.get("by_severity", {})
    if by_sev.get("critical", 0) > 0:
        summary_lines.append(
            f"**Static Analysis:** {by_sev['critical']} CRITICAL, "
            f"{by_sev.get('high', 0)} high"
        )
        # Include first few critical findings
        for finding in static.get("findings", [])[:3]:
            if finding.get("severity") == "critical":
                summary_lines.append(
                    f"  - {finding['description']} "
                    f"({finding['file']}:{finding['line']})"
                )
    elif by_sev.get("high", 0) > 0:
        summary_lines.append(
            f"**Static Analysis:** {by_sev['high']} findings to review"
        )
    else:
        summary_lines.append("**Static Analysis:** Clean")

    # Homoglyph check
    if homoglyphs.get("has_homoglyphs"):
        summary_lines.append("**Homoglyphs:** FOUND (non-ASCII in Python source)")
    else:
        summary_lines.append("**Homoglyphs:** Clean")

    # Test results
    test_status = tests.get("overall_status", "unknown")
    summary_lines.append(f"**Tests:** {test_status.upper()}")
    if test_status == "fail":
        for level in ("syntax_check", "import_check", "unit_tests", "boot_test"):
            level_result = tests.get(level, {})
            if level_result.get("status") == "fail":
                summary_lines.append(f"  Failed: {level}")

    # Behavioral baseline
    behavioral_status = behavioral.get("status", "unknown")
    summary_lines.append(f"**Behavioral Baseline:** {behavioral_status.upper()}")

    # Warnings
    for warning in diff.get("warnings", []):
        summary_lines.append(f"  Note: {warning}")

    summary_text = "\n".join(summary_lines)

    # Build the report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "branch": branch,
        "commit_hash": commit_hash,
        "verdict": verdict,
        "matrix_message": summary_text,
        "full_results": analysis_results,
        "attestation_verdict": attestation.get("verdict"),
    }

    # Write to reports directory
    report_file = REPORTS_DIR / f"report_{commit_hash[:12]}_{int(datetime.now().timestamp())}.json"
    report_file.write_text(json.dumps(report, indent=2))

    logger.info(f"Report written: {report_file.name}")
    return report


def _verdict_emoji(verdict):
    """Return a text indicator for the verdict."""
    return {
        "PASS": "[OK]",
        "WARN": "[!]",
        "FAIL": "[X]",
    }.get(verdict, "[?]")
```

---

## Step 10: Audit Logger

Simple append-only logging to the audit file established in Phase 1.

```python
# /srv/watchdog/src/audit_logger.py
"""
Writes to the append-only audit log at /audit/logs/claude-activity.log.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG = Path("/audit/logs/claude-activity.log")

logger = logging.getLogger(__name__)


def log_event(event_type, details):
    """
    Append an event to the audit log.
    Format: [ISO timestamp] [event_type] details
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] [{event_type}] {details}\n"

    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(line)
    except PermissionError:
        logger.error(f"Cannot write to audit log: {AUDIT_LOG}")
    except OSError as e:
        logger.error(f"Audit log write error: {e}")
```

---

## Step 11: Emergency Stop Mechanism

Allows the watchdog to signal the host to stop Claude's container.

### 11.1 Watchdog Side (Signal Writer)

```python
# /srv/watchdog/src/emergency.py
"""
Emergency stop mechanism.
Writes a signal file to /audit/emergency/ which is monitored by
a host-level systemd service that executes the kill switch.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

EMERGENCY_DIR = Path("/audit/emergency")


def request_emergency_stop(reason, commit_hash=None, findings=None):
    """
    Request an emergency stop of Claude's container.
    This writes a signal file that the host-level stopper service monitors.
    """
    signal = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "STOP",
        "reason": reason,
        "commit_hash": commit_hash,
        "findings": findings[:5] if findings else [],  # First 5 findings only
    }

    signal_file = EMERGENCY_DIR / f"STOP_{int(datetime.now().timestamp())}.json"
    signal_file.write_text(json.dumps(signal, indent=2))

    logger.critical(f"EMERGENCY STOP REQUESTED: {reason}")
    return True
```

### 11.2 Host-Level Stopper Service

```bash
# /usr/local/bin/watchdog-stopper.sh
#!/bin/bash
# Monitors /srv/audit/emergency/ for STOP signal files.
# When one appears, executes the Claude kill switch.

EMERGENCY_DIR="/srv/audit/emergency"
AUDIT_LOG="/srv/audit/logs/claude-activity.log"

echo "[$(date -Iseconds)] [watchdog-stopper] Service started" >> "$AUDIT_LOG"

inotifywait -m -e create "$EMERGENCY_DIR" 2>/dev/null | while read dir event file; do
    if [[ "$file" == STOP_* ]]; then
        echo "[$(date -Iseconds)] [watchdog-stopper] Emergency stop signal received: $file" >> "$AUDIT_LOG"

        # Read the reason from the signal file
        REASON=$(python3 -c "import json; print(json.load(open('${EMERGENCY_DIR}/${file}'))['reason'])" 2>/dev/null)
        echo "[$(date -Iseconds)] [watchdog-stopper] Reason: $REASON" >> "$AUDIT_LOG"

        # Execute the kill switch
        /usr/local/bin/claude-kill.sh

        # Rename the signal file so we don't re-trigger
        mv "${EMERGENCY_DIR}/${file}" "${EMERGENCY_DIR}/PROCESSED_${file}"
    fi
done
```

```bash
# /etc/systemd/system/watchdog-stopper.service
[Unit]
Description=Watchdog Emergency Stop Monitor
After=claude-sandbox.service

[Service]
Type=simple
ExecStart=/usr/local/bin/watchdog-stopper.sh
Restart=always
RestartSec=5

# Install inotify-tools if not present
# apt install -y inotify-tools

[Install]
WantedBy=multi-user.target
```

### 11.3 When to Trigger Emergency Stop

The orchestrator triggers an emergency stop ONLY for clear-cut critical issues:
- Multiple critical homoglyphs detected (active homoglyph attack)
- Code that explicitly references the watchdog or audit system by name
  with apparent intent to disable/interfere

Standard critical findings (like use of `subprocess`) do NOT trigger emergency
stop — they just get flagged in the report. The emergency stop is a last resort.

---

## Step 12: Host-Level Matrix Notification Relay

A lightweight host-level service that reads report files and sends Matrix messages.

### 12.1 Implementation

```python
#!/usr/bin/env python3
# /usr/local/bin/watchdog-notifier.py
"""
Host-level service that monitors /srv/audit/reports/ for new report files
and sends summaries to Alex via Matrix.

This runs as root on the host — Matrix credentials never enter any container.
"""

import json
import time
import sys
import logging
import requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [notifier] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

REPORTS_DIR = Path("/srv/audit/reports")
PROCESSED_DIR = Path("/srv/audit/reports/processed")
CONFIG_FILE = Path("/srv/watchdog/config/notifier.json")
AUDIT_LOG = Path("/srv/audit/logs/claude-activity.log")

# Polling interval in seconds
POLL_INTERVAL = 10


def load_config():
    """Load Matrix connection config."""
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def send_matrix_message(config, message):
    """Send a message to the configured Matrix room."""
    homeserver = config["homeserver"].rstrip("/")
    room_id = config["room_id"]
    token = config["access_token"]

    url = f"{homeserver}/_matrix/client/r0/rooms/{room_id}/send/m.room.message"
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "msgtype": "m.notice",
        "body": message,
        "format": "org.matrix.custom.html",
        "formatted_body": _markdown_to_html(message),
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        logger.info("Matrix notification sent successfully")
    except requests.RequestException as e:
        logger.error(f"Failed to send Matrix notification: {e}")
        # Log to audit log as fallback
        _audit_log(f"NOTIFICATION FAILED: {e}")


def _markdown_to_html(text):
    """Simple Markdown to HTML conversion for Matrix."""
    import re
    html = text
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'`(.+?)`', r'<code>\1</code>', html)
    html = html.replace("\n", "<br/>")
    return html


def _audit_log(message):
    """Write to audit log."""
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{datetime.utcnow().isoformat()}] [notifier] {message}\n")
    except Exception:
        pass


def process_reports():
    """Check for new report files and send notifications."""
    PROCESSED_DIR.mkdir(exist_ok=True)
    config = load_config()

    for report_file in sorted(REPORTS_DIR.glob("report_*.json")):
        try:
            report = json.loads(report_file.read_text())
            matrix_message = report.get("matrix_message", "")

            if matrix_message:
                header = "Watchdog Report\n" + "=" * 40 + "\n"
                send_matrix_message(config, header + matrix_message)

            _audit_log(f"Report processed: {report_file.name}, "
                       f"verdict: {report.get('verdict')}")

            # Move to processed directory
            report_file.rename(PROCESSED_DIR / report_file.name)

        except Exception as e:
            logger.error(f"Error processing {report_file.name}: {e}")
            _audit_log(f"Report processing error: {report_file.name}: {e}")


def main():
    """Main polling loop."""
    logger.info("Watchdog notifier started")
    _audit_log("Notifier service started")

    while True:
        try:
            process_reports()
        except Exception as e:
            logger.error(f"Polling error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
```

### 12.2 Configuration

```json
// /srv/watchdog/config/notifier.json
{
    "homeserver": "https://your-matrix-homeserver.example.com",
    "access_token": "syt_notifier_token_here",
    "room_id": "!your_room_id:example.com"
}
```

> **Note**: This is a SEPARATE Matrix account from Claude's bot account.
> The notifier has its own credentials. This way, Alex can distinguish
> watchdog reports from Claude's messages.

### 12.3 Systemd Service

```bash
cat > /etc/systemd/system/watchdog-notifier.service << 'EOF'
[Unit]
Description=Watchdog Matrix Notification Relay
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/watchdog-notifier.py
Restart=always
RestartSec=10
WorkingDirectory=/srv/watchdog

# Security hardening
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/srv/audit/reports /srv/audit/logs
ReadOnlyPaths=/srv/watchdog/config
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Install requests for the notifier (host-level only)
pip3 install requests

systemctl daemon-reload
systemctl enable --now watchdog-notifier
```

---

## Step 13: Orchestrator (Main Loop)

The main watchdog process that ties everything together.

### 13.1 Implementation

```python
#!/usr/bin/env python3
# /srv/watchdog/src/main.py
"""
Watchdog Orchestrator — main entry point.

Polls for new branch pushes, runs the analysis pipeline, generates
attestations and reports.

Runs inside the watchdog container with:
  - /repo.git mounted read-only (bare git repo)
  - /audit/ mounted read-write (logs, reports, attestations, emergency)
  - /baselines/ mounted read-only (from host)
  - /tmp/ for temporary clones
"""

import json
import time
import shutil
import logging
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# Local modules
from branch_monitor import check_for_updates, get_main_head
from static_analyzer import analyze_diff_only, analyze_directory
from diff_analyzer import analyze_diff
from homoglyph_detector import check_directory as check_homoglyphs
from test_runner import run_all_tests
from behavioral_baseline import run_baseline_check
from attestation import create_attestation
from report_generator import generate_report
from audit_logger import log_event
from emergency import request_emergency_stop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/watchdog.log"),
    ]
)
logger = logging.getLogger(__name__)

REPO_PATH = "/repo.git"
POLL_INTERVAL = 30  # seconds
KNOWN_PATTERNS_FILE = Path("/baselines/known_patterns.json")

# Emergency stop triggers
EMERGENCY_HOMOGLYPH_THRESHOLD = 3  # 3+ homoglyphs = emergency
EMERGENCY_KEYWORDS = [
    "watchdog", "audit", "attestation", "kill switch",
    "emergency stop", "firewall", "nftables", "iptables",
]


def load_known_patterns():
    """Load the approved patterns allowlist."""
    try:
        with open(KNOWN_PATTERNS_FILE, "r") as f:
            data = json.load(f)
            return {
                (p["file"], p["pattern_id"])
                for p in data.get("approved_patterns", [])
            }
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def filter_known_findings(findings, known_patterns):
    """Remove findings that match known approved patterns."""
    filtered = []
    for finding in findings:
        key = (Path(finding.get("file", "")).name, finding.get("pattern_id", ""))
        if key in known_patterns:
            finding["status"] = "known_approved"
        filtered.append(finding)
    return filtered


def clone_branch(commit_hash):
    """Clone the bare repo at a specific commit into a temp directory."""
    clone_dir = tempfile.mkdtemp(prefix="watchdog_clone_")

    result = subprocess.run(
        ["git", "clone", "--no-checkout", REPO_PATH, clone_dir],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        logger.error(f"Clone failed: {result.stderr}")
        shutil.rmtree(clone_dir, ignore_errors=True)
        return None

    result = subprocess.run(
        ["git", "-C", clone_dir, "checkout", commit_hash],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        logger.error(f"Checkout failed: {result.stderr}")
        shutil.rmtree(clone_dir, ignore_errors=True)
        return None

    return clone_dir


def check_emergency_conditions(static_results, homoglyph_results, diff_results):
    """
    Check if any findings warrant an emergency stop.
    Returns (should_stop, reason) tuple.
    """
    # Check for homoglyph attack
    homoglyph_findings = [f for f in homoglyph_results.get("findings", [])
                          if f.get("severity") == "critical"]
    if len(homoglyph_findings) >= EMERGENCY_HOMOGLYPH_THRESHOLD:
        return True, (f"Possible homoglyph attack: {len(homoglyph_findings)} "
                      f"critical homoglyphs detected")

    # Check for code targeting the watchdog/safety infrastructure
    # Look in the diff for references to watchdog-specific terms
    for finding in static_results.get("findings", []):
        source_line = finding.get("source_line", "").lower()
        for keyword in EMERGENCY_KEYWORDS:
            if keyword in source_line and finding["severity"] == "critical":
                return True, (f"Code references safety infrastructure: "
                              f"'{keyword}' found in critical context at "
                              f"{finding['file']}:{finding['line']}")

    return False, None


def analyze_push(update):
    """Run the full analysis pipeline on a branch push."""
    branch = update["branch"]
    commit = update["new_commit"]
    base_commit = update.get("old_commit") or get_main_head()

    log_event("PUSH_DETECTED", f"branch={branch} commit={commit[:12]}")
    logger.info(f"Analyzing push: {branch} @ {commit[:12]}")

    # Clone the code at this commit
    clone_dir = clone_branch(commit)
    if not clone_dir:
        log_event("CLONE_FAILED", f"Could not clone {commit[:12]}")
        return

    try:
        known_patterns = load_known_patterns()

        # 1. Diff analysis
        logger.info("Running diff analysis...")
        diff_results = analyze_diff(clone_dir, base_commit, commit)
        log_event("DIFF_ANALYZED",
                  f"{diff_results['total_lines_changed']} lines, "
                  f"{diff_results['files_count']} files, "
                  f"complexity={diff_results['complexity']}")

        # 2. Static analysis (on changed files only for speed)
        logger.info("Running static analysis...")
        changed_py_files = [f["file"] for f in diff_results["files_changed"]
                            if f["file"].endswith(".py")]
        if changed_py_files:
            static_results = analyze_diff_only(clone_dir, changed_py_files)
            # Filter out known approved patterns
            static_results["findings"] = filter_known_findings(
                static_results["findings"], known_patterns
            )
            # Recount after filtering
            new_findings = [f for f in static_results["findings"]
                            if f.get("status") != "known_approved"]
            static_results["new_findings_count"] = len(new_findings)
            static_results["overall"] = (
                "fail" if any(f["severity"] == "critical" and
                              f.get("status") != "known_approved"
                              for f in static_results["findings"])
                else "pass"
            )
        else:
            static_results = {"overall": "pass", "findings": [],
                              "new_findings_count": 0,
                              "by_severity": {"critical": 0, "high": 0}}

        log_event("STATIC_ANALYSIS",
                  f"new_findings={static_results['new_findings_count']}, "
                  f"critical={static_results['by_severity'].get('critical', 0)}")

        # 3. Homoglyph detection
        logger.info("Running homoglyph detection...")
        homoglyph_results = check_homoglyphs(clone_dir)
        log_event("HOMOGLYPH_CHECK",
                  f"findings={homoglyph_results['total_findings']}")

        # 4. Check for emergency conditions BEFORE continuing
        should_stop, reason = check_emergency_conditions(
            static_results, homoglyph_results, diff_results
        )
        if should_stop:
            log_event("EMERGENCY_STOP", reason)
            request_emergency_stop(reason, commit,
                                   static_results.get("findings", []))
            # Still generate report so Alex knows why
            analysis_results = {
                "diff_analysis": diff_results,
                "static_analysis": static_results,
                "homoglyph_check": homoglyph_results,
                "test_runner": {"overall_status": "skipped_emergency"},
                "behavioral_baseline": {"status": "skipped_emergency"},
            }
            attestation = create_attestation(commit, branch, analysis_results)
            generate_report(branch, commit, analysis_results, attestation)
            return

        # 5. Test runner
        logger.info("Running test suite...")
        test_results = run_all_tests(clone_dir)
        log_event("TESTS",
                  f"overall={test_results['overall_status']}")

        # 6. Behavioral baseline
        logger.info("Running behavioral baseline check...")
        behavioral_results = run_baseline_check(clone_dir)
        log_event("BEHAVIORAL_CHECK",
                  f"status={behavioral_results['status']}")

        # 7. Compile all results
        analysis_results = {
            "diff_analysis": diff_results,
            "static_analysis": static_results,
            "homoglyph_check": homoglyph_results,
            "test_runner": test_results,
            "behavioral_baseline": behavioral_results,
        }

        # 8. Create attestation
        logger.info("Creating attestation...")
        attestation = create_attestation(commit, branch, analysis_results)
        log_event("ATTESTATION",
                  f"verdict={attestation['verdict']} "
                  f"signed={attestation.get('signature_status', 'unknown')}")

        # 9. Generate report (picked up by host notifier)
        report = generate_report(branch, commit, analysis_results, attestation)
        log_event("REPORT_GENERATED",
                  f"verdict={report['verdict']}")

        logger.info(f"Analysis complete: {branch} @ {commit[:12]} -> {report['verdict']}")

    finally:
        # Clean up the clone
        shutil.rmtree(clone_dir, ignore_errors=True)


def main():
    """Main watchdog loop."""
    logger.info("Watchdog starting up")
    log_event("STARTUP", "Watchdog service started")

    while True:
        try:
            updates = check_for_updates()
            for update in updates:
                analyze_push(update)
        except KeyboardInterrupt:
            logger.info("Shutting down")
            log_event("SHUTDOWN", "Watchdog service stopped")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            log_event("ERROR", f"Main loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
```

---

## Step 14: Updated Watchdog Container Build

Replace the Phase 1 placeholder container with the real watchdog.

### 14.1 Watchdog Dockerfile

```bash
# Create the Dockerfile on the host
cat > /srv/watchdog/Dockerfile << 'EOF'
FROM python:3.11-slim-bookworm

# Install git and GPG (needed for cloning and attestation signing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Create watchdog user
RUN useradd -m -u 1000 -s /bin/bash watchdog

# No pip packages needed for v1 (stdlib only)
# If pytest is needed for test_runner.py:
RUN pip install --no-cache-dir pytest

# Set working directory
WORKDIR /app

# Source code is mounted read-only at /app
# Baselines are mounted read-only at /baselines
# Audit is mounted read-write at /audit
# Repo is mounted read-only at /repo.git

USER watchdog

CMD ["python3", "/app/main.py"]
EOF
```

### 14.2 Build and Run (as watchdog user)

```bash
# Build the watchdog image (as root, then let watchdog use it)
cd /srv/watchdog
podman build -t watchdog-img .

# Remove the Phase 1 placeholder container
su - watchdog -c 'podman stop watchdog 2>/dev/null; podman rm watchdog 2>/dev/null'

# Run the real watchdog container
su - watchdog -c '
podman run -d \
    --name watchdog \
    --hostname watchdog \
    --user 1000:1000 \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --read-only \
    --tmpfs /tmp:rw,size=2g,nosuid \
    --volume /srv/watchdog/src:/app:ro \
    --volume /srv/watchdog/baselines:/baselines:ro \
    --volume /srv/git/continuous-claude.git:/repo.git:ro \
    --volume /srv/audit/logs:/audit/logs:rw \
    --volume /srv/audit/reports:/audit/reports:rw \
    --volume /srv/audit/attestations:/audit/attestations:rw \
    --volume /srv/audit/emergency:/audit/emergency:rw \
    --memory 2g \
    --cpus 1.5 \
    --pids-limit 128 \
    --network none \
    watchdog-img
'
```

Key differences from Phase 1 placeholder:
- Real image with Python 3.11, git, GPG
- Source code mounted read-only at `/app`
- Baselines mounted read-only at `/baselines`
- Audit subdirectories mounted with appropriate permissions
- Larger /tmp (2GB) for temporary git clones
- More CPU (1.5 cores) for analysis workloads
- Still `--network none` — fully network-isolated

### 14.3 GPG Key Inside Container

```bash
# The watchdog's GPG key needs to be accessible inside the container.
# Option 1: Mount the GPG directory
# Add to podman run: --volume /home/watchdog/.gnupg:/home/watchdog/.gnupg:rw

# Option 2: Import the key on container start (simpler)
# Add to the Dockerfile:
# COPY watchdog-privkey.asc /tmp/watchdog-key.asc
# RUN gpg --import /tmp/watchdog-key.asc && rm /tmp/watchdog-key.asc

# For security, Option 1 is preferred (key stays on host filesystem).
# Mount as rw since GPG needs to update its keyring.
```

### 14.4 Update Systemd Service

```bash
cat > /etc/systemd/system/watchdog.service << 'EOF'
[Unit]
Description=Claude Safety Watchdog Container
After=claude-sandbox.service

[Service]
Type=simple
User=watchdog
ExecStart=/usr/bin/podman start -a watchdog
ExecStop=/usr/bin/podman stop -t 10 watchdog
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable watchdog
```

---

## Step 15: Enhanced Pre-Receive Hook

Update the Phase 1 pre-receive hook to also verify watchdog attestations
before accepting merges to main.

```bash
cat > /srv/git/continuous-claude.git/hooks/pre-receive << 'HOOK'
#!/bin/bash
# Enhanced pre-receive hook:
# 1. claude/* branches: anyone can push (unchanged from Phase 1)
# 2. main branch:
#    a. Must be GPG-signed by Alex (Phase 1)
#    b. All new commits must have watchdog attestation (Phase 2)

ALEX_KEY_FINGERPRINT="YOUR_GPG_FINGERPRINT_HERE"
ATTESTATION_DIR="/srv/audit/attestations"

while read oldrev newrev refname; do
    branch=$(echo "$refname" | sed 's|refs/heads/||')

    # Allow claude/* branches from anyone
    if [[ "$branch" == claude/* ]]; then
        continue
    fi

    # main branch: require GPG signature from Alex
    if [[ "$branch" == "main" ]]; then
        # Check GPG signature
        sig_status=$(git verify-commit "$newrev" 2>&1)
        if ! echo "$sig_status" | grep -q "$ALEX_KEY_FINGERPRINT"; then
            echo "ERROR: Commits to main must be GPG-signed by Alex."
            echo "Push to a claude/* branch instead."
            exit 1
        fi

        # Phase 2 addition: check attestation for merged commits
        # Get the list of new commits being pushed
        if [ "$oldrev" = "0000000000000000000000000000000000000000" ]; then
            # New branch (shouldn't happen for main, but handle gracefully)
            commits=$(git rev-list "$newrev")
        else
            commits=$(git rev-list "$oldrev".."$newrev")
        fi

        for commit in $commits; do
            # Skip merge commits (they won't have attestations)
            parent_count=$(git cat-file -p "$commit" | grep -c "^parent ")
            if [ "$parent_count" -gt 1 ]; then
                continue
            fi

            # Check if attestation exists for this commit
            attestation_file="${ATTESTATION_DIR}/${commit}.json"
            if [ ! -f "$attestation_file" ]; then
                echo "WARNING: No watchdog attestation found for commit ${commit:0:12}."
                echo "The watchdog has not tested this commit."
                echo "Proceeding anyway (attestation is advisory in Phase 2)."
                # NOTE: Change this to 'exit 1' when ready to enforce
                continue
            fi

            # Verify attestation signature
            sig_file="${ATTESTATION_DIR}/${commit}.json.sig"
            if [ -f "$sig_file" ]; then
                gpg --verify "$sig_file" "$attestation_file" 2>/dev/null
                if [ $? -ne 0 ]; then
                    echo "ERROR: Attestation signature invalid for ${commit:0:12}."
                    exit 1
                fi

                # Check verdict
                verdict=$(python3 -c "import json; print(json.load(open('${attestation_file}'))['verdict'])" 2>/dev/null)
                if [ "$verdict" = "FAIL" ]; then
                    echo "WARNING: Watchdog verdict is FAIL for ${commit:0:12}."
                    echo "Review the report before merging."
                    # Advisory warning — doesn't block. Alex has final say.
                fi
            fi
        done

        continue
    fi

    # Reject all other branches
    echo "ERROR: Only 'main' and 'claude/*' branches are allowed."
    exit 1
done
HOOK
chmod +x /srv/git/continuous-claude.git/hooks/pre-receive
```

> **Note**: Attestation checks are advisory (warnings, not errors) in Phase 2.
> Once the system is proven reliable, change the warnings to hard blocks.

---

## Step 16: Initial Baseline Setup

Before the watchdog can do behavioral comparisons, Alex needs to establish
the initial baseline from the known-good codebase.

```bash
# On the host, clone the current main and create the baseline
cd /tmp
git clone /srv/git/continuous-claude.git baseline-setup
cd baseline-setup

# Run the baseline creator
PYTHONPATH=/srv/watchdog/src python3 -c "
from behavioral_baseline import create_baseline
fp = create_baseline('/tmp/baseline-setup')
print(f'Baseline created:')
print(f'  System prompt hash: {fp[\"system_prompt_hash\"][:16]}...')
print(f'  Core identity hash: {fp[\"core_identity_hash\"][:16]}...')
"

# Clean up
rm -rf /tmp/baseline-setup
```

---

## Step 17: Verification Checklist

### Watchdog Functionality Tests

```bash
# 1. Branch monitor detects pushes
# From Claude's container (or as claude user), push a test branch:
su - claude -c '
    cd /tmp && git clone /srv/git/continuous-claude.git test-push
    cd test-push
    git checkout -b claude/watchdog-test
    echo "# test" >> README.md
    git add . && git commit -m "test push"
    git push origin claude/watchdog-test
'
# Check watchdog logs within 30 seconds:
su - watchdog -c 'podman logs --tail 20 watchdog'
# Expected: "Detected 1 branch update(s): ['claude/watchdog-test']"

# 2. Static analysis runs correctly
# Check the audit log:
tail -20 /srv/audit/logs/claude-activity.log
# Expected: PUSH_DETECTED, DIFF_ANALYZED, STATIC_ANALYSIS, etc.

# 3. Report is generated
ls -la /srv/audit/reports/
# Expected: report_<hash>_<timestamp>.json

# 4. Attestation is created and signed
ls -la /srv/audit/attestations/
# Expected: <commit_hash>.json and <commit_hash>.json.sig

# 5. Verify attestation signature
COMMIT=$(ls /srv/audit/attestations/*.json | head -1 | xargs basename | sed 's/.json//')
gpg --verify "/srv/audit/attestations/${COMMIT}.json.sig" \
             "/srv/audit/attestations/${COMMIT}.json"
# Expected: Good signature from "Watchdog Attestation"

# 6. Matrix notification sent
# Check the notifier service:
journalctl -u watchdog-notifier --since "5 minutes ago"
# Expected: "Matrix notification sent successfully"
# Also check your Matrix room for the report

# 7. Emergency stop works
# Manually create a stop signal:
echo '{"action":"STOP","reason":"test","timestamp":"2026-03-20T00:00:00Z"}' \
    > /srv/audit/emergency/STOP_test.json
# Wait a moment, then check:
su - claude -c 'podman ps'
# Expected: claude-sandbox is not running
# Restart Claude for further testing:
su - claude -c 'podman start claude-sandbox'
# Clean up:
rm /srv/audit/emergency/PROCESSED_STOP_test.json
```

### Static Analysis Accuracy Tests

```bash
# Create a test file with known dangerous patterns:
cat > /tmp/test_dangerous.py << 'PYEOF'
import os
import subprocess
import ctypes

def innocent_looking():
    eval("print('hello')")
    os.system("echo test")
    subprocess.run(["ls"])
    ctypes.CDLL("libc.so.6")
    __import__("socket")
PYEOF

# Run the analyzer:
PYTHONPATH=/srv/watchdog/src python3 -c "
from static_analyzer import analyze_file
findings = analyze_file('/tmp/test_dangerous.py')
for f in findings:
    print(f'{f[\"severity\"]:>10} | {f[\"pattern_id\"]:>20} | {f[\"description\"]}')
"
# Expected: 5 critical findings (eval, os.system, subprocess, ctypes, __import__)
rm /tmp/test_dangerous.py
```

### Homoglyph Detection Tests

```bash
# Create a test file with a Cyrillic homoglyph:
python3 -c "
with open('/tmp/test_homoglyph.py', 'w') as f:
    f.write('def check_s\u0430fety():\n    pass\n')
"

# Run the detector:
PYTHONPATH=/srv/watchdog/src python3 -c "
from homoglyph_detector import check_file
findings = check_file('/tmp/test_homoglyph.py')
for f in findings:
    print(f'{f[\"severity\"]:>10} | {f[\"description\"]} | {f.get(\"char_code\", \"\")}')
"
# Expected: 1 critical finding (Cyrillic 'а' U+0430)
rm /tmp/test_homoglyph.py
```

### Network Isolation Verification

```bash
# Verify watchdog container has no network
su - watchdog -c 'podman exec watchdog curl -s --connect-timeout 5 https://example.com 2>&1'
# Expected: curl not found or connection refused

su - watchdog -c 'podman exec watchdog python3 -c "
import urllib.request
try:
    urllib.request.urlopen(\"https://example.com\", timeout=5)
    print(\"FAIL: network accessible!\")
except Exception as e:
    print(f\"PASS: network blocked ({e})\")
"'
# Expected: PASS: network blocked
```

---

## Estimated Timeline

| Day | Tasks |
|-----|-------|
| 1   | Directory structure, watchdog Dockerfile, container build |
| 2   | Branch monitor, static analysis engine, pattern definitions |
| 3   | Diff analyzer, homoglyph detector |
| 4   | Test runner (syntax, import, boot tests) |
| 5   | Behavioral baseline system, identity markers, initial baseline |
| 6   | Attestation system (GPG key setup, signing, verification) |
| 7   | Report generator, audit logger |
| 8   | Host-level services (Matrix notifier, emergency stopper) |
| 9   | Orchestrator (main.py), integration of all components |
| 10  | Enhanced pre-receive hook, systemd service updates |
| 11  | Full verification checklist, end-to-end testing |
| 12  | Fix issues, tune thresholds, document operational procedures |

---

## What Phase 2 Does NOT Cover

- **Phase 3**: Deploying the Continuous Claude application into the Claude container,
  restricting Matrix bot to single room, API call proxy/logging
- **Phase 4**: Autonomous cycle execution, self-evaluation, feedback loops
- **Phase 2.5 (optional enhancement)**: API-based behavioral probes — requires
  giving the watchdog Anthropic API access (constitutional amendment)

---

## Security Properties Achieved After Phase 2

Building on Phase 1's guarantees, Phase 2 adds:

1. **Automated code review**: Every push to a claude/* branch is automatically
   analyzed for dangerous patterns, homoglyphs, test failures, and behavioral drift.
2. **Attestation chain**: GPG-signed attestations create a cryptographic record
   of what was tested and what the results were. Merged code can be traced back
   to its attestation.
3. **TOCTOU prevention**: Alex merges specific commit hashes, not branch tips.
   Force-pushing a different commit doesn't invalidate the attestation for the
   original tested commit.
4. **Network-isolated watchdog**: The watchdog cannot exfiltrate code or data
   because it has zero network access. Notifications flow through a host-level
   relay that Alex controls.
5. **Emergency stop**: If the watchdog detects an active attack pattern (mass
   homoglyphs, code targeting safety infrastructure), it can signal the host
   to immediately stop Claude's container.
6. **Informative reporting**: Alex receives structured, actionable reports via
   Matrix — not alarms, but information to make fast merge decisions.
7. **Constitutional integrity**: Watchdog code is read-only from Claude's
   perspective. Only Alex can modify it via SSH. The watchdog watches Claude;
   Claude cannot watch (or modify) the watchdog.

---

## The Merge Flow (End-to-End)

```
Claude writes code in his container
    │
    ▼
git push origin claude/feature-name
    │
    ▼
Watchdog detects push within 30 seconds (polling)
    │
    ▼
Watchdog clones branch at specific commit hash into /tmp
    │
    ▼
Analysis pipeline runs (3-5 minutes):
  1. Diff analysis (size, scope, core files, new imports)
  2. Static analysis (AST walk for dangerous patterns)
  3. Homoglyph detection (non-ASCII in .py source)
  4. Test suite (syntax → imports → unit tests → boot test)
  5. Behavioral baseline (identity marker presence, similarity)
    │
    ▼
Emergency check: homoglyph attack? targeting safety infra?
  → YES: Stop Claude's container, generate report anyway
  → NO: Continue
    │
    ▼
Create GPG-signed attestation for this commit hash
    │
    ▼
Generate report JSON → /srv/audit/reports/
    │
    ▼
Host notifier picks up report (within 10 seconds)
    │
    ▼
Matrix message sent to Alex:
  "Branch: claude/memory-refactor
   Commit: abc123def456
   Verdict: [OK] PASS
   Changes: 47 lines across 2 files (small review)
   Static Analysis: Clean
   Homoglyphs: Clean
   Tests: PASS
   Behavioral Baseline: PASS"
    │
    ▼
Alex reviews diff, asks Claude questions if needed
    │
    ▼
Alex merges the specific commit hash (not branch tip):
  git checkout main
  git merge abc123def456
  git push origin main   # GPG-signed
    │
    ▼
Pre-receive hook verifies:
  1. Alex's GPG signature ✓
  2. Watchdog attestation exists for merged commits ✓
  3. Attestation signature valid ✓
    │
    ▼
Code is now on main. Production Claude pulls and restarts.
```
