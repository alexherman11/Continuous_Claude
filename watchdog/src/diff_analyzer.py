"""
Analyzes git diffs for size, scope, and patterns of interest.
Produces metrics that help Alex quickly assess review complexity.
"""

import subprocess
import re
import logging
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)


def _run_git(repo_path, *args):
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path] + list(args),
            capture_output=True, text=True, timeout=60
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        logger.error(f"git command timed out: {args}")
        return ""


def analyze_diff(clone_path, base_commit, head_commit):
    """
    Analyze the diff between base_commit and head_commit.
    Returns a structured analysis with metrics and warnings.
    """
    thresholds = get("diff_thresholds", {
        "small": {"lines": 50, "files": 3},
        "medium": {"lines": 200, "files": 10},
        "large": {"lines": 500, "files": 20},
    })
    core_files = set(get("core_files", []))
    config_files = set(get("config_files", []))

    # Get numstat for precise line counts
    numstat = _run_git(clone_path, "diff", "--numstat", base_commit, head_commit)

    files_changed = []
    total_added = 0
    total_removed = 0

    for line in numstat.split("\n"):
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
    complexity = _classify_complexity(total_lines, len(files_changed), thresholds)

    # Detect new and deleted files
    new_files = _get_filtered_files(clone_path, base_commit, head_commit, "A")
    deleted_files = _get_filtered_files(clone_path, base_commit, head_commit, "D")

    # Check which categories of files are affected
    changed_filenames = {Path(f["file"]).name for f in files_changed}
    core_files_changed = list(changed_filenames & core_files)
    config_files_changed = list(changed_filenames & config_files)

    # Extract new imports from the diff
    diff_content = _run_git(clone_path, "diff", "-U0", base_commit, head_commit)
    new_imports = _extract_new_imports(diff_content)

    # Generate warnings
    warnings = _generate_warnings(
        complexity, core_files_changed, config_files_changed,
        total_lines, len(files_changed)
    )

    return {
        "total_lines_changed": total_lines,
        "lines_added": total_added,
        "lines_removed": total_removed,
        "files_changed": files_changed,
        "files_count": len(files_changed),
        "new_files": new_files,
        "deleted_files": deleted_files,
        "complexity": complexity,
        "core_files_changed": core_files_changed,
        "config_files_changed": config_files_changed,
        "new_imports": new_imports,
        "warnings": warnings,
    }


def _get_filtered_files(clone_path, base, head, diff_filter):
    """Get files matching a diff filter (A=added, D=deleted, M=modified)."""
    output = _run_git(clone_path, "diff", f"--diff-filter={diff_filter}",
                      "--name-only", base, head)
    return [f for f in output.split("\n") if f.strip()]


def _classify_complexity(total_lines, file_count, thresholds):
    """Classify the diff complexity based on line and file counts."""
    small = thresholds.get("small", {"lines": 50, "files": 3})
    medium = thresholds.get("medium", {"lines": 200, "files": 10})
    large = thresholds.get("large", {"lines": 500, "files": 20})

    if total_lines <= small["lines"] and file_count <= small["files"]:
        return "small"
    elif total_lines <= medium["lines"] and file_count <= medium["files"]:
        return "medium"
    elif total_lines <= large["lines"]:
        return "large"
    else:
        return "huge"


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
