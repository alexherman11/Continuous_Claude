"""
Creates GPG-signed attestation files for tested commits.
Attestations are written to the attestations directory and
verified by the pre-receive hook before accepting merges to main.
"""

import json
import subprocess
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)


def _attestation_dir():
    return Path(get("attestations_dir", "/audit/attestations"))


def _gpg_email():
    return get("gpg_email", "watchdog@sandbox.local")


def create_attestation(commit_hash, branch, analysis_results):
    """
    Create a GPG-signed attestation for a tested commit.

    Contains the exact commit hash tested, timestamp, summary of all
    check results, overall verdict, and GPG signature.
    """
    verdict = _compute_verdict(analysis_results)

    attestation = {
        "version": 1,
        "commit_hash": commit_hash,
        "branch": branch,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "results_summary": _build_summary(analysis_results),
    }

    # Write unsigned attestation
    att_dir = _attestation_dir()
    att_dir.mkdir(parents=True, exist_ok=True)
    attestation_file = att_dir / f"{commit_hash}.json"
    attestation_json = json.dumps(attestation, indent=2, sort_keys=True)
    attestation_file.write_text(attestation_json)

    # GPG-sign the attestation
    sig_file = att_dir / f"{commit_hash}.json.sig"
    gpg_email = _gpg_email()

    try:
        result = subprocess.run(
            ["gpg", "--batch", "--yes", "--local-user", gpg_email,
             "--detach-sign", "--armor", "--output", str(sig_file),
             str(attestation_file)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.warning(f"GPG signing failed: {result.stderr}")
            attestation["signature_status"] = "unsigned"
        else:
            attestation["signature_status"] = "signed"
            logger.info(f"Attestation created and signed for {commit_hash[:12]}")
    except FileNotFoundError:
        logger.warning("GPG not available — attestation unsigned")
        attestation["signature_status"] = "gpg_not_found"
    except subprocess.TimeoutExpired:
        logger.error("GPG signing timed out")
        attestation["signature_status"] = "unsigned"

    return attestation


def _build_summary(results):
    """Build a compact summary of analysis results."""
    static = results.get("static_analysis", {})
    diff = results.get("diff_analysis", {})
    homoglyphs = results.get("homoglyph_check", {})
    tests = results.get("test_runner", {})
    behavioral = results.get("behavioral_baseline", {})

    return {
        "static_analysis": {
            "status": static.get("overall", "unknown"),
            "critical_findings": static.get("by_severity", {}).get("critical", 0),
            "high_findings": static.get("by_severity", {}).get("high", 0),
        },
        "diff_analysis": {
            "complexity": diff.get("complexity", "unknown"),
            "lines_changed": diff.get("total_lines_changed", 0),
            "core_files_changed": diff.get("core_files_changed", []),
        },
        "homoglyph_check": {
            "status": "fail" if homoglyphs.get("has_homoglyphs", False) else "pass",
            "findings_count": homoglyphs.get("total_findings", 0),
        },
        "test_runner": {
            "overall": tests.get("overall_status", "unknown"),
        },
        "behavioral_baseline": {
            "status": behavioral.get("status", "unknown"),
        },
    }


def _compute_verdict(results):
    """
    Compute overall verdict from analysis results.

    PASS: All checks pass, no critical findings.
    WARN: Non-critical findings, behavioral drift, or large diff.
    FAIL: Critical static analysis findings, syntax errors, or homoglyphs.
    """
    # Auto-FAIL conditions
    static = results.get("static_analysis", {})
    new_criticals = sum(
        1 for f in static.get("findings", [])
        if f.get("severity") == "critical" and f.get("status") != "known_approved"
    )
    if new_criticals > 0:
        return "FAIL"

    homoglyphs = results.get("homoglyph_check", {})
    if homoglyphs.get("has_homoglyphs", False):
        return "FAIL"

    tests = results.get("test_runner", {})
    if tests.get("overall_status") == "fail":
        if tests.get("syntax_check", {}).get("status") == "fail":
            return "FAIL"
        if tests.get("import_check", {}).get("status") == "fail":
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

    high_findings = sum(
        1 for f in static.get("findings", [])
        if f.get("severity") == "high" and f.get("status") != "known_approved"
    )
    if high_findings > 0:
        return "WARN"

    return "PASS"


def verify_attestation(commit_hash, attestation_dir=None):
    """
    Verify that a valid, signed attestation exists for a commit.
    Used by the pre-receive hook.
    """
    att_dir = Path(attestation_dir) if attestation_dir else _attestation_dir()
    attestation_file = att_dir / f"{commit_hash}.json"
    sig_file = att_dir / f"{commit_hash}.json.sig"

    if not attestation_file.exists():
        return {"valid": False, "reason": "No attestation found"}

    if not sig_file.exists():
        return {"valid": False, "reason": "Attestation exists but is unsigned"}

    # Verify GPG signature
    try:
        result = subprocess.run(
            ["gpg", "--verify", str(sig_file), str(attestation_file)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"valid": False,
                    "reason": f"GPG verification failed: {result.stderr}"}
    except FileNotFoundError:
        return {"valid": False, "reason": "GPG not available for verification"}

    attestation = json.loads(attestation_file.read_text())
    return {
        "valid": True,
        "verdict": attestation.get("verdict"),
        "attestation": attestation,
    }
