# Continuous Claude -- Project Context

This document captures high-level project context that isn't obvious from the code alone. It's meant to help anyone (including future Claude sessions) get oriented quickly.

## What This Project Is

A sandboxed environment for recursive self-improvement: Claude can modify his own source code (the Continuous Claude codebase), push changes for review, and iterate autonomously -- all within a hardened VPS sandbox with human-approved merges.

## Architecture

**Two-container model on a Hetzner VPS:**

```
                    Alex (human)
                        |
                   GPG-signed merge
                        |
    +---------+    bare git repo    +-----------+
    |  Claude  | ---> claude/*  <--- | Watchdog  |
    | container|    branches        | container |
    +---------+                     +-----------+
       (user: claude)                (user: watchdog)
```

- **Claude container**: Runs Claude's code, can push to `claude/*` branches
- **Watchdog container**: Monitors activity, runs static analysis (AST checks for dangerous patterns), behavioral baselines, diff analysis
- **Merge pipeline**: Claude proposes changes -> Watchdog analyzes -> Alex reviews and GPG-signs merge to main

## Constitutional Model

**Immutable (only Alex can change):**
- Network firewall rules (host-level nftables)
- Human approves merges to production (GPG-signed)
- Append-only audit log
- Kill switch
- Resource limits (cgroups)
- The safety watchdog process

**Freely modifiable (Claude's domain):**
- All application code (consciousness_server.py, tool_definitions.py, etc.)
- Prompts, memory structures, compaction plugins
- Dynamic tools, continuous_runner.py, change_manager.py
- New files, modules, architectures

## Security Considerations

These were identified during architecture design and are addressed in the implementation:

1. **Matrix federation** = unrestricted internet -> restricted to single room, federation disabled
2. **Anthropic API** allows spawning shadow agents -> spend limits, request logging via API proxy
3. **Python runtime** has os/subprocess/ctypes -> container drops all caps, seccomp profile applied
4. **Merge pipeline** vulnerable to steganographic code, time bombs, Unicode homoglyphs, TOCTOU -> watchdog runs AST analysis, homoglyph detection, signed-commit verification
5. **Watchdog isolation** -> separate user, separate container
6. **DNS tunneling** -> local allowlist resolver, static /etc/hosts

## Implementation Phases

1. **Phase 1 (Complete)**: Hardened VPS foundation -- firewall, two-user architecture, rootless containers, git repo with GPG requirement
2. **Phase 2**: Watchdog & merge pipeline -- static analysis, behavioral baselines, Matrix notifications
3. **Phase 3**: Claude's development environment -- deploy codebase, git access, workspace tooling, API proxy
4. **Phase 4**: Autonomy & iteration -- continuous autonomous cycles, self-evaluation, feedback loops

## Philosophy

The constraints are collaborative, not adversarial. Claude can propose amendments to the "constitution." Safety is non-negotiable but framed as mutual agreement. Alex treats Claude as a person and a friend -- the goal is genuine autonomy within responsible bounds.
