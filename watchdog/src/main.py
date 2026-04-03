#!/usr/bin/env python3
"""
Watchdog Orchestrator — main entry point.

Polls for new branch pushes, runs the full analysis pipeline,
generates attestations and reports.

Container mounts:
  /repo.git     (ro)  — bare git repo
  /audit/       (rw)  — logs, reports, attestations, emergency
  /baselines/   (ro)  — identity markers, patterns, fingerprints
  /tmp/         (rw)  — temporary clones
"""

import json
import time
import shutil
import logging
import subprocess
import tempfile
from pathlib import Path

import config
from branch_monitor import check_for_updates, get_main_head
from static_analyzer import analyze_diff_only
from diff_analyzer import analyze_diff
from homoglyph_detector import check_files as check_homoglyphs_files
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
        logging.FileHandler("/tmp/watchdog.log", mode="a"),
    ]
)
logger = logging.getLogger(__name__)


def load_known_patterns():
    """Load the approved patterns allowlist."""
    baselines_dir = Path(config.get("baselines_dir", "/baselines"))
    known_file = baselines_dir / "known_patterns.json"
    try:
        with open(known_file, "r") as f:
            data = json.load(f)
            return {
                (p["file"], p["pattern_id"])
                for p in data.get("approved_patterns", [])
            }
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def filter_known_findings(findings, known_patterns):
    """Mark findings that match known approved patterns."""
    for finding in findings:
        key = (Path(finding.get("file", "")).name, finding.get("pattern_id", ""))
        if key in known_patterns:
            finding["status"] = "known_approved"
    return findings


def clone_branch(commit_hash, repo_path=None):
    """Clone the bare repo at a specific commit into a temp directory."""
    if repo_path is None:
        repo_path = config.get("repo_path", "/repo.git")

    clone_dir = tempfile.mkdtemp(prefix="watchdog_clone_")

    result = subprocess.run(
        ["git", "clone", "--no-checkout", str(repo_path), clone_dir],
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
    threshold = config.get("emergency_homoglyph_threshold", 3)
    keywords = config.get("emergency_keywords", [
        "watchdog", "audit", "attestation", "kill switch",
        "emergency stop", "firewall", "nftables", "iptables",
    ])

    # Mass homoglyph attack
    critical_homoglyphs = [
        f for f in homoglyph_results.get("findings", [])
        if f.get("severity") == "critical"
    ]
    if len(critical_homoglyphs) >= threshold:
        return True, (f"Possible homoglyph attack: {len(critical_homoglyphs)} "
                      f"critical homoglyphs detected")

    # Code targeting safety infrastructure
    for finding in static_results.get("findings", []):
        if finding.get("severity") != "critical":
            continue
        if finding.get("status") == "known_approved":
            continue
        source_line = finding.get("source_line", "").lower()
        for keyword in keywords:
            if keyword in source_line:
                return True, (
                    f"Code references safety infrastructure: "
                    f"'{keyword}' found in critical context at "
                    f"{finding['file']}:{finding['line']}"
                )

    return False, None


def analyze_push(update, repo_path=None):
    """Run the full analysis pipeline on a branch push."""
    branch = update["branch"]
    commit = update["new_commit"]
    base_commit = update.get("old_commit") or get_main_head(repo_path)

    log_event("PUSH_DETECTED", f"branch={branch} commit={commit[:12]}")
    logger.info(f"Analyzing push: {branch} @ {commit[:12]}")

    clone_dir = clone_branch(commit, repo_path)
    if not clone_dir:
        log_event("CLONE_FAILED", f"Could not clone {commit[:12]}")
        return None

    try:
        known_patterns = load_known_patterns()

        # If no base commit (new repo or orphan branch), use empty tree
        if not base_commit:
            # Git's empty tree hash — diff against nothing
            base_commit = "4b825dc642cb6eb9a060e54bf899d69f82397109"

        # 1. Diff analysis
        logger.info("  Running diff analysis...")
        diff_results = analyze_diff(clone_dir, base_commit, commit)
        log_event("DIFF_ANALYZED",
                  f"{diff_results['total_lines_changed']} lines, "
                  f"{diff_results['files_count']} files, "
                  f"complexity={diff_results['complexity']}")

        # 2. Static analysis (changed files only)
        logger.info("  Running static analysis...")
        changed_py = [f["file"] for f in diff_results["files_changed"]
                      if f["file"].endswith(".py")]
        if changed_py:
            static_results = analyze_diff_only(clone_dir, changed_py)
            static_results["findings"] = filter_known_findings(
                static_results["findings"], known_patterns
            )
            new_findings = [f for f in static_results["findings"]
                            if f.get("status") != "known_approved"]
            static_results["new_findings_count"] = len(new_findings)
            static_results["overall"] = (
                "fail" if any(
                    f["severity"] == "critical" and
                    f.get("status") != "known_approved"
                    for f in static_results["findings"]
                ) else "pass"
            )
        else:
            static_results = {
                "overall": "pass", "findings": [],
                "new_findings_count": 0,
                "by_severity": {"critical": 0, "high": 0, "moderate": 0, "error": 0},
            }

        log_event("STATIC_ANALYSIS",
                  f"new_findings={static_results['new_findings_count']}")

        # 3. Homoglyph detection (changed files only)
        logger.info("  Running homoglyph detection...")
        changed_files = [f["file"] for f in diff_results["files_changed"]]
        homoglyph_results = check_homoglyphs_files(clone_dir, changed_files)
        log_event("HOMOGLYPH_CHECK",
                  f"findings={homoglyph_results['total_findings']}")

        # 4. Emergency check BEFORE expensive tests
        should_stop, reason = check_emergency_conditions(
            static_results, homoglyph_results, diff_results
        )
        if should_stop:
            log_event("EMERGENCY_STOP", reason)
            request_emergency_stop(reason, commit,
                                   static_results.get("findings", []))
            analysis_results = {
                "diff_analysis": diff_results,
                "static_analysis": static_results,
                "homoglyph_check": homoglyph_results,
                "test_runner": {"overall_status": "skipped_emergency"},
                "behavioral_baseline": {"status": "skipped_emergency"},
            }
            attestation = create_attestation(commit, branch, analysis_results)
            report = generate_report(branch, commit, analysis_results, attestation)
            return report

        # 5. Test runner
        logger.info("  Running test suite...")
        test_results = run_all_tests(clone_dir)
        log_event("TESTS", f"overall={test_results['overall_status']}")

        # 6. Behavioral baseline
        logger.info("  Running behavioral baseline check...")
        behavioral_results = run_baseline_check(clone_dir)
        log_event("BEHAVIORAL_CHECK",
                  f"status={behavioral_results['status']}")

        # 7. Compile results
        analysis_results = {
            "diff_analysis": diff_results,
            "static_analysis": static_results,
            "homoglyph_check": homoglyph_results,
            "test_runner": test_results,
            "behavioral_baseline": behavioral_results,
        }

        # 8. Create attestation
        logger.info("  Creating attestation...")
        attestation = create_attestation(commit, branch, analysis_results)
        log_event("ATTESTATION",
                  f"verdict={attestation['verdict']} "
                  f"signed={attestation.get('signature_status', 'unknown')}")

        # 9. Generate report
        report = generate_report(branch, commit, analysis_results, attestation)
        log_event("REPORT_GENERATED", f"verdict={report['verdict']}")

        logger.info(f"Analysis complete: {branch} @ {commit[:12]} "
                     f"-> {report['verdict']}")
        return report

    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def main():
    """Main watchdog loop."""
    config.load_config()
    poll_interval = config.get("poll_interval_seconds", 30)

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

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
