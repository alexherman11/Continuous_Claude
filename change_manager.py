"""
Change Manager — Staged self-modification pipeline for the Consciousness Engine.

Handles the lifecycle of code changes that Claude proposes to his own codebase:
  1. PROPOSE — validate and write to proposed_changes/pending/
  2. VALIDATE — syntax check + contract verification
  3. APPLY — deploy approved changes (Tier 1 auto-deploy, Tier 2/3 after human approval)
  4. ROLLBACK — revert applied changes using git snapshots

Tier System:
  Tier 1: New dynamic tools → auto-deploy after validation
  Tier 2: Modify existing tools → human approval required
  Tier 3: Core engine files → human approval + restart required
"""

import json
import logging
import os
import py_compile
import shutil
import subprocess
import tempfile
from datetime import datetime

from path_safety import is_within

_base_dir = os.path.dirname(os.path.abspath(__file__))
PROPOSED_DIR = os.path.join(_base_dir, "proposed_changes")
PENDING_DIR = os.path.join(PROPOSED_DIR, "pending")
APPROVED_DIR = os.path.join(PROPOSED_DIR, "approved")
APPLIED_DIR = os.path.join(PROPOSED_DIR, "applied")
REJECTED_DIR = os.path.join(PROPOSED_DIR, "rejected")
DYNAMIC_ENABLED_DIR = os.path.join(_base_dir, "dynamic_tools", "enabled")

# Core engine files that require Tier 3 (human approval + restart)
# These are the foundational files — changes here affect the runtime loop,
# file persistence, or the change pipeline itself.
TIER_3_FILES = {
    "consciousness_server.py",
    "continuous_consciousness.py",
    "continuous_runner.py",
    "file_manager.py",
    "change_manager.py",
}

# Files that require Tier 2 (human approval, no restart)
# These affect tool behavior, integrations, or UI — important but
# don't require a full restart to take effect.
TIER_2_FILES = {
    "tool_definitions.py",
    "matrix_client.py",
    "music_engine.py",
    "budget_tracker.py",
    "pdf_importer.py",
    "continuous_consciousness.jsx",
}


def _ensure_dirs():
    """Create proposed_changes subdirectories if missing."""
    for d in [PENDING_DIR, APPROVED_DIR, APPLIED_DIR, REJECTED_DIR]:
        os.makedirs(d, exist_ok=True)


def _next_change_id():
    """Generate the next sequential change ID."""
    _ensure_dirs()
    existing = []
    for d in [PENDING_DIR, APPROVED_DIR, APPLIED_DIR, REJECTED_DIR]:
        for f in os.listdir(d):
            if f.startswith("change_") and f.endswith(".json"):
                try:
                    num = int(f.replace("change_", "").replace(".json", ""))
                    existing.append(num)
                except ValueError:
                    pass
    next_num = max(existing, default=0) + 1
    return f"change_{next_num:04d}"


def _determine_tier(target_file):
    """Determine the review tier for a target file."""
    basename = os.path.basename(target_file)
    if basename in TIER_3_FILES:
        return 3
    if basename in TIER_2_FILES:
        return 2
    # Dynamic tools and new files are Tier 1
    if target_file.startswith("dynamic_tools/"):
        return 1
    # Unknown files default to Tier 3 for safety
    return 3


def _validate_python_syntax(filepath):
    """Check Python syntax. Returns (ok: bool, error: str|None)."""
    try:
        py_compile.compile(filepath, doraise=True)
        return True, None
    except py_compile.PyCompileError as e:
        return False, str(e)


def _validate_dynamic_tool_contract(filepath):
    """
    Validate that a file follows the dynamic tool contract:
    - Exports SCHEMA dict with name, description, input_schema
    - Exports callable execute()
    Returns (ok: bool, error: str|None).
    """
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("_validation_check", filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "SCHEMA"):
            return False, "Missing SCHEMA dict"
        if not isinstance(module.SCHEMA, dict):
            return False, "SCHEMA must be a dict"
        required = {"name", "description", "input_schema"}
        missing = required - set(module.SCHEMA.keys())
        if missing:
            return False, f"SCHEMA missing keys: {missing}"
        if not hasattr(module, "execute"):
            return False, "Missing execute() function"
        if not callable(module.execute):
            return False, "execute must be callable"
        return True, None
    except Exception as e:
        return False, f"Import error: {type(e).__name__}: {e}"


def propose_change(source_path, target_file, description, change_type, cycle=None):
    """
    Propose a code change. Reads source from source_path (typically in
    sandbox_workspace/ or scratchpad/), validates syntax, and writes manifest
    to proposed_changes/pending/.

    For Tier 1 changes (new dynamic tools), auto-deploys after validation.

    Args:
        source_path: Absolute path to the source file (e.g., in sandbox_workspace/ or scratchpad/)
        target_file: Relative target path (e.g., "dynamic_tools/enabled/my_tool.py")
        description: What the change does and why
        change_type: "new_tool", "modify_tool", "modify_engine", "new_file"
        cycle: Current consciousness cycle number

    Returns: status dict with change_id and tier info
    """
    _ensure_dirs()

    # Read source file
    if not os.path.exists(source_path):
        return {"error": f"Source file not found: {source_path}"}

    with open(source_path, "r", encoding="utf-8") as f:
        source_content = f.read()

    if not source_content.strip():
        return {"error": "Source file is empty"}

    # Determine tier
    tier = _determine_tier(target_file)

    # Validate Python syntax
    validation = {"syntax": None, "contract": None}
    if source_path.endswith(".py"):
        ok, err = _validate_python_syntax(source_path)
        validation["syntax"] = {"passed": ok, "error": err}
        if not ok:
            return {
                "error": f"Syntax validation failed: {err}",
                "validation": validation,
                "hint": "Fix the syntax error and try again.",
            }

    # Validate dynamic tool contract for Tier 1
    # Accept various change_type names that indicate a dynamic tool
    _tool_change_types = ("new_tool", "modify_tool", "new_dynamic_tool", "modify_dynamic_tool")
    if (change_type in _tool_change_types or target_file.startswith("dynamic_tools/")) and source_path.endswith(".py"):
        ok, err = _validate_dynamic_tool_contract(source_path)
        validation["contract"] = {"passed": ok, "error": err}
        if not ok:
            return {
                "error": f"Tool contract validation failed: {err}",
                "validation": validation,
                "hint": "File must export SCHEMA dict (with name, description, input_schema) and execute() function.",
            }

    # Create manifest
    change_id = _next_change_id()
    manifest = {
        "id": change_id,
        "proposed_at": datetime.now().isoformat(),
        "cycle": cycle,
        "tier": tier,
        "type": change_type,
        "description": description,
        "target_file": target_file,
        "source_file": source_path,
        "status": "pending",
        "validation": validation,
        "rollback_commit": None,
        "applied_at": None,
    }

    # Save manifest
    manifest_path = os.path.join(PENDING_DIR, f"{change_id}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Save source content alongside manifest
    content_path = os.path.join(PENDING_DIR, f"{change_id}_content.py" if source_path.endswith(".py") else f"{change_id}_content.txt")
    with open(content_path, "w", encoding="utf-8") as f:
        f.write(source_content)

    # Tier 1: auto-deploy after validation passes
    if tier == 1 and all(
        v is None or v.get("passed", False)
        for v in validation.values()
        if v is not None
    ):
        deploy_result = _deploy_change(change_id, manifest, source_content)
        if deploy_result.get("status") == "ok":
            return {
                "status": "auto_deployed",
                "change_id": change_id,
                "tier": tier,
                "target_file": target_file,
                "description": description,
                "validation": validation,
                "message": "Tier 1 change auto-deployed. Will be available next cycle.",
            }
        else:
            return {
                "status": "pending",
                "change_id": change_id,
                "tier": tier,
                "deploy_error": deploy_result.get("error"),
                "message": "Auto-deploy failed. Change is pending manual review.",
            }

    # Tier 2/3: require human approval
    return {
        "status": "pending",
        "change_id": change_id,
        "tier": tier,
        "target_file": target_file,
        "description": description,
        "validation": validation,
        "message": f"Tier {tier} change requires human approval. Move {change_id}.json from pending/ to approved/.",
    }


def _deploy_change(change_id, manifest, source_content):
    """
    Deploy an approved change. Creates a git snapshot first for rollback.
    """
    target_file = manifest["target_file"]
    target_path = os.path.join(_base_dir, target_file)

    # Path traversal guard: resolved path must stay within the project directory
    if not is_within(_base_dir, target_path) or os.path.realpath(target_path) == os.path.realpath(_base_dir):
        return {"error": f"Path traversal blocked: {target_file} resolves outside project directory"}

    # Create git snapshot for rollback
    rollback_commit = _create_git_snapshot(change_id)

    # Ensure target directory exists
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    # Write the file
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(source_content)
    except OSError as e:
        return {"error": f"Failed to write target file: {e}"}

    # Update manifest
    manifest["status"] = "applied"
    manifest["applied_at"] = datetime.now().isoformat()
    manifest["rollback_commit"] = rollback_commit

    # Move manifest to applied/
    src_manifest = os.path.join(PENDING_DIR, f"{change_id}.json")
    src_content = None
    # Find the content file
    for ext in ("_content.py", "_content.txt"):
        p = os.path.join(PENDING_DIR, f"{change_id}{ext}")
        if os.path.exists(p):
            src_content = p
            break

    dst_manifest = os.path.join(APPLIED_DIR, f"{change_id}.json")
    with open(dst_manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Clean up pending files
    if os.path.exists(src_manifest):
        os.remove(src_manifest)
    if src_content and os.path.exists(src_content):
        dst_content = os.path.join(APPLIED_DIR, os.path.basename(src_content))
        shutil.move(src_content, dst_content)

    return {
        "status": "ok",
        "change_id": change_id,
        "target_file": target_file,
        "rollback_commit": rollback_commit,
    }


def _create_git_snapshot(change_id):
    """Create a git commit as a rollback point. Returns commit hash or None."""
    try:
        # Check if there are uncommitted changes
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=_base_dir,
        )
        if status.stdout.strip():
            # Stage and commit current state
            subprocess.run(
                ["git", "add", "-A"],
                capture_output=True, text=True, timeout=10, cwd=_base_dir,
            )
            subprocess.run(
                ["git", "commit", "-m", f"Snapshot before applying {change_id}"],
                capture_output=True, text=True, timeout=10, cwd=_base_dir,
            )

        # Get current commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=_base_dir,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        logging.error(f"Git snapshot failed: {e}")
        return None


def apply_approved_changes():
    """
    Scan proposed_changes/approved/ and deploy each change.
    Called at the start of each cycle from reload_hot_config().

    Returns list of applied change IDs.
    """
    _ensure_dirs()
    applied = []

    for filename in sorted(os.listdir(APPROVED_DIR)):
        if not filename.endswith(".json"):
            continue

        manifest_path = os.path.join(APPROVED_DIR, filename)
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.error(f"Failed to read approved manifest {filename}: {e}")
            continue

        change_id = manifest.get("id", filename.replace(".json", ""))

        # Find the source content
        source_content = None
        for ext in ("_content.py", "_content.txt"):
            content_path = os.path.join(APPROVED_DIR, f"{change_id}{ext}")
            if os.path.exists(content_path):
                with open(content_path, "r", encoding="utf-8") as f:
                    source_content = f.read()
                break

        # If no content file in approved/, check pending/ (file may not have been moved)
        if source_content is None:
            for ext in ("_content.py", "_content.txt"):
                content_path = os.path.join(PENDING_DIR, f"{change_id}{ext}")
                if os.path.exists(content_path):
                    with open(content_path, "r", encoding="utf-8") as f:
                        source_content = f.read()
                    # Move content to approved
                    shutil.move(content_path, os.path.join(APPROVED_DIR, os.path.basename(content_path)))
                    break

        if source_content is None:
            # Try reading from original source_file
            src = manifest.get("source_file", "")
            if src and os.path.exists(src):
                with open(src, "r", encoding="utf-8") as f:
                    source_content = f.read()

        if source_content is None:
            logging.error(f"No source content found for {change_id}, skipping")
            continue

        result = _deploy_change(change_id, manifest, source_content)
        if result.get("status") == "ok":
            applied.append(change_id)
            # Remove the approved manifest (it's been moved to applied/)
            if os.path.exists(manifest_path):
                os.remove(manifest_path)
            logging.info(f"Applied change {change_id}: {manifest.get('description', '')}")
        else:
            logging.error(f"Failed to apply {change_id}: {result.get('error')}")

    return applied


def rollback_change(change_id):
    """
    Rollback an applied change using its git snapshot.
    For dynamic tools, also moves the file to disabled/.
    """
    # Find the manifest in applied/
    manifest_path = os.path.join(APPLIED_DIR, f"{change_id}.json")
    if not os.path.exists(manifest_path):
        return {"error": f"Applied change '{change_id}' not found"}

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    target_file = manifest.get("target_file", "")
    rollback_commit = manifest.get("rollback_commit")

    # For dynamic tools, just move to disabled/
    if target_file.startswith("dynamic_tools/enabled/"):
        target_path = os.path.join(_base_dir, target_file)
        if os.path.exists(target_path):
            disabled_path = os.path.join(
                _base_dir, "dynamic_tools", "disabled", os.path.basename(target_file)
            )
            os.makedirs(os.path.dirname(disabled_path), exist_ok=True)
            shutil.move(target_path, disabled_path)

        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = datetime.now().isoformat()
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return {
            "status": "ok",
            "change_id": change_id,
            "action": "moved_to_disabled",
            "target_file": target_file,
            "message": "Dynamic tool moved to disabled/. Will be removed next cycle.",
        }

    # For core files, use git checkout
    if rollback_commit:
        try:
            result = subprocess.run(
                ["git", "checkout", rollback_commit, "--", target_file],
                capture_output=True, text=True, timeout=10, cwd=_base_dir,
            )
            if result.returncode != 0:
                return {"error": f"Git rollback failed: {result.stderr.strip()}"}
        except Exception as e:
            return {"error": f"Git rollback error: {e}"}

        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = datetime.now().isoformat()
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return {
            "status": "ok",
            "change_id": change_id,
            "action": "git_rollback",
            "target_file": target_file,
            "rollback_commit": rollback_commit,
            "message": f"File restored from commit {rollback_commit[:8]}. Restart may be needed for core files.",
        }

    return {"error": "No rollback commit recorded for this change"}


def list_changes(status_filter=None):
    """
    List all proposed changes, optionally filtered by status.
    status_filter: "pending", "approved", "applied", "rejected", or None (all)
    """
    _ensure_dirs()
    dirs = {
        "pending": PENDING_DIR,
        "approved": APPROVED_DIR,
        "applied": APPLIED_DIR,
        "rejected": REJECTED_DIR,
    }

    if status_filter:
        if status_filter not in dirs:
            return {"error": f"Invalid status filter: {status_filter}"}
        scan = {status_filter: dirs[status_filter]}
    else:
        scan = dirs

    changes = []
    for status_name, directory in scan.items():
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".json") or "_content" in filename:
                continue
            filepath = os.path.join(directory, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                changes.append({
                    "id": manifest.get("id", filename),
                    "status": status_name,
                    "tier": manifest.get("tier"),
                    "type": manifest.get("type"),
                    "description": manifest.get("description", ""),
                    "target_file": manifest.get("target_file", ""),
                    "proposed_at": manifest.get("proposed_at", ""),
                    "applied_at": manifest.get("applied_at"),
                })
            except (json.JSONDecodeError, OSError):
                continue

    return {
        "status": "ok",
        "total": len(changes),
        "changes": changes,
    }
