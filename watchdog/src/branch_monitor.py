"""
Monitors the bare git repo for new pushes to claude/* branches.
Maintains a state file of known branch heads to detect changes.
"""

import json
import subprocess
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)

STATE_FILE = Path("/tmp/watchdog_branch_state.json")


def _run_git(*args, repo_path=None):
    """Run a git command against the bare repo."""
    if repo_path is None:
        repo_path = get("repo_path", "/repo.git")

    try:
        result = subprocess.run(
            ["git", "--git-dir", str(repo_path)] + list(args),
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.error(f"git {' '.join(args)} failed: {result.stderr}")
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error(f"git command timed out: {args}")
        return None


def load_state(state_file=None):
    """Load known branch heads from state file."""
    path = Path(state_file) if state_file else STATE_FILE
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state, state_file=None):
    """Persist known branch heads."""
    path = Path(state_file) if state_file else STATE_FILE
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def get_current_branches(repo_path=None):
    """Get all claude/* branch heads from the bare repo."""
    output = _run_git("for-each-ref", "--format=%(refname:short) %(objectname)",
                      "refs/heads/claude/", repo_path=repo_path)
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


def check_for_updates(repo_path=None, state_file=None):
    """
    Compare current branch state to known state.
    Returns list of update dicts for changed branches.
    """
    known = load_state(state_file)
    current = get_current_branches(repo_path)

    updates = []
    for branch, commit in current.items():
        old_commit = known.get(branch)
        if old_commit != commit:
            updates.append({
                "branch": branch,
                "old_commit": old_commit,
                "new_commit": commit,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "is_new_branch": old_commit is None,
            })

    # Update state with current branches
    save_state(current, state_file)

    if updates:
        logger.info(f"Detected {len(updates)} branch update(s): "
                     f"{[u['branch'] for u in updates]}")
    return updates


def get_main_head(repo_path=None):
    """Get the current HEAD of main branch."""
    return _run_git("rev-parse", "refs/heads/main", repo_path=repo_path)
