"""
Generates structured JSON reports for the Matrix notification relay.
Reports are written to the reports directory and picked up by the
host-level notifier service.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)


def _reports_dir():
    return Path(get("reports_dir", "/audit/reports"))


def generate_report(branch, commit_hash, analysis_results, attestation):
    """
    Generate a comprehensive report and write it to the reports directory.
    The host-level notifier will pick this up and send it via Matrix.
    """
    verdict = attestation.get("verdict", "UNKNOWN")

    diff = analysis_results.get("diff_analysis", {})
    static = analysis_results.get("static_analysis", {})
    tests = analysis_results.get("test_runner", {})
    behavioral = analysis_results.get("behavioral_baseline", {})
    homoglyphs = analysis_results.get("homoglyph_check", {})

    summary_text = _format_matrix_message(
        branch, commit_hash, verdict,
        diff, static, tests, behavioral, homoglyphs
    )

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
    reports_dir = _reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = int(datetime.now().timestamp())
    report_file = reports_dir / f"report_{commit_hash[:12]}_{ts}.json"
    report_file.write_text(json.dumps(report, indent=2))

    logger.info(f"Report written: {report_file.name}")
    return report


def _format_matrix_message(branch, commit_hash, verdict,
                            diff, static, tests, behavioral, homoglyphs):
    """Format a human-readable report for Matrix."""
    verdict_indicator = {"PASS": "[OK]", "WARN": "[!]", "FAIL": "[X]"}.get(verdict, "[?]")

    lines = []
    lines.append(f"**Branch:** `{branch}`")
    lines.append(f"**Commit:** `{commit_hash[:12]}`")
    lines.append(f"**Verdict:** {verdict_indicator} **{verdict}**")
    lines.append("")

    # Diff summary
    lines.append(
        f"**Changes:** {diff.get('total_lines_changed', '?')} lines "
        f"across {diff.get('files_count', '?')} files "
        f"({diff.get('complexity', '?')} review)"
    )
    if diff.get("core_files_changed"):
        lines.append(f"  Core files: {', '.join(diff['core_files_changed'])}")
    if diff.get("new_imports"):
        imports = diff["new_imports"][:5]
        lines.append(f"  New imports: {', '.join(imports)}")

    # Static analysis
    by_sev = static.get("by_severity", {})
    new_count = static.get("new_findings_count", static.get("total_findings", 0))
    if by_sev.get("critical", 0) > 0:
        lines.append(
            f"**Static Analysis:** {by_sev['critical']} CRITICAL, "
            f"{by_sev.get('high', 0)} high"
        )
        for finding in static.get("findings", [])[:3]:
            if finding.get("severity") == "critical" and \
               finding.get("status") != "known_approved":
                lines.append(
                    f"  - {finding['description']} "
                    f"({finding['file']}:{finding['line']})"
                )
    elif by_sev.get("high", 0) > 0:
        lines.append(
            f"**Static Analysis:** {by_sev['high']} finding(s) to review"
        )
    else:
        lines.append("**Static Analysis:** Clean")

    # Homoglyphs
    if homoglyphs.get("has_homoglyphs"):
        lines.append(
            f"**Homoglyphs:** FOUND ({homoglyphs.get('total_findings', 0)} "
            f"non-ASCII in Python source)"
        )
    else:
        lines.append("**Homoglyphs:** Clean")

    # Tests
    test_status = tests.get("overall_status", "unknown")
    lines.append(f"**Tests:** {test_status.upper()}")
    if test_status == "fail":
        for level in ("syntax_check", "import_check", "unit_tests", "boot_test"):
            level_result = tests.get(level, {})
            if level_result.get("status") == "fail":
                lines.append(f"  Failed: {level}")

    # Behavioral baseline
    behavioral_status = behavioral.get("status", "unknown")
    lines.append(f"**Behavioral Baseline:** {behavioral_status.upper()}")

    # Warnings from diff analysis
    for warning in diff.get("warnings", []):
        lines.append(f"  Note: {warning}")

    return "\n".join(lines)
