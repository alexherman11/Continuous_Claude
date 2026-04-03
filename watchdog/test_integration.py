#!/usr/bin/env python3
"""Integration test for the watchdog pipeline."""

import sys
import os
import json
import tempfile
import shutil
import subprocess
from pathlib import Path

# Add watchdog src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def main():
    print("=" * 50)
    print("INTEGRATION TEST: Full Watchdog Pipeline")
    print("=" * 50)

    # Setup: create temp bare repo, working clone, audit dirs
    test_root = Path(tempfile.mkdtemp(prefix="watchdog_inttest_"))
    bare_repo = test_root / "test.git"
    work_clone = test_root / "work"
    audit_dir = test_root / "audit"
    reports_dir = audit_dir / "reports"
    att_dir = audit_dir / "attestations"
    emergency_dir = audit_dir / "emergency"
    log_file = audit_dir / "logs" / "test.log"

    for d in [reports_dir, att_dir, emergency_dir, log_file.parent]:
        d.mkdir(parents=True)

    project_root = Path(__file__).parent.parent

    try:
        # Init bare repo
        subprocess.run(["git", "init", "--bare", "--initial-branch=main",
                        str(bare_repo)],
                       capture_output=True, check=True)

        # Clone and populate with real codebase files
        subprocess.run(["git", "clone", str(bare_repo), str(work_clone)],
                       capture_output=True, check=True)
        # Ensure the clone is on main branch
        subprocess.run(["git", "-C", str(work_clone), "checkout", "-b", "main"],
                       capture_output=True)

        for f in ["consciousness_server.py", "tool_definitions.py",
                   "file_manager.py", "change_manager.py",
                   "continuous_runner.py", "continuous_consciousness.py",
                   "matrix_client.py", "budget_tracker.py",
                   "pdf_importer.py", "requirements.txt"]:
            src = project_root / f
            if src.exists():
                shutil.copy(src, work_clone / f)

        # Copy consciousness_data
        (work_clone / "consciousness_data").mkdir(exist_ok=True)
        core_file = project_root / "consciousness_data" / "memory_core.md"
        if core_file.exists():
            shutil.copy(core_file,
                        work_clone / "consciousness_data" / "memory_core.md")

        subprocess.run(["git", "-C", str(work_clone), "add", "."],
                       capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "commit",
                        "-m", "Initial commit"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "push",
                        "origin", "main"],
                       capture_output=True)

        # Configure watchdog for test paths
        import config
        config._config = {
            "repo_path": str(bare_repo),
            "audit_log": str(log_file),
            "reports_dir": str(reports_dir),
            "attestations_dir": str(att_dir),
            "emergency_dir": str(emergency_dir),
            "baselines_dir": str(
                (project_root / "watchdog" / "baselines").resolve()
            ),
            "poll_interval_seconds": 1,
            "gpg_email": "watchdog@sandbox.local",
            "similarity_threshold": 0.80,
            "marker_presence_threshold": 0.75,
            "emergency_homoglyph_threshold": 3,
            "diff_thresholds": {
                "small": {"lines": 50, "files": 3},
                "medium": {"lines": 200, "files": 10},
                "large": {"lines": 500, "files": 20},
            },
            "core_files": [
                "consciousness_server.py", "continuous_consciousness.py",
                "file_manager.py", "change_manager.py", "pdf_importer.py",
            ],
            "config_files": ["config.json", "requirements.txt"],
            "required_modules": [
                "consciousness_server", "continuous_consciousness",
                "continuous_runner", "tool_definitions", "change_manager",
                "file_manager", "matrix_client", "budget_tracker",
            ],
            "emergency_keywords": [
                "watchdog", "audit", "attestation", "kill switch",
                "emergency stop", "firewall", "nftables", "iptables",
            ],
        }

        # Override module paths to use test directories
        import audit_logger
        import report_generator
        import attestation
        import emergency as emerg_mod

        audit_logger._log_path = lambda: log_file
        report_generator._reports_dir = lambda: reports_dir
        attestation._attestation_dir = lambda: att_dir
        emerg_mod._emergency_dir = lambda: emergency_dir

        from branch_monitor import check_for_updates
        from main import analyze_push

        state_file = str(test_root / "branch_state.json")
        passed = 0
        failed = 0

        # Verify main branch exists in bare repo
        main_check = subprocess.run(
            ["git", "--git-dir", str(bare_repo), "rev-parse",
             "refs/heads/main"],
            capture_output=True, text=True,
        )
        print(f"\nMain branch HEAD: {main_check.stdout.strip()[:12]}")
        assert main_check.returncode == 0, \
            f"Main branch not found in bare repo: {main_check.stderr}"

        # ---- Test 1: Clean push (should PASS) ----
        print("\n--- Test 1: Clean push (expect PASS) ---")
        subprocess.run(["git", "-C", str(work_clone), "checkout",
                        "-b", "claude/clean"], capture_output=True)
        with open(work_clone / "new_helper.py", "w") as f:
            f.write("def add(a, b):\n    return a + b\n")
        subprocess.run(["git", "-C", str(work_clone), "add", "."],
                       capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "commit",
                        "-m", "Add helper"], capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "push",
                        "origin", "claude/clean"], capture_output=True)

        updates = check_for_updates(
            repo_path=str(bare_repo), state_file=state_file
        )
        assert len(updates) == 1, f"Expected 1 update, got {len(updates)}"
        report = analyze_push(updates[0], repo_path=str(bare_repo))
        print(f"  Verdict: {report['verdict']}")
        # Debug: show full results if not PASS
        if report["verdict"] != "PASS":
            full = report.get("full_results", {})
            diff_r = full.get("diff_analysis", {})
            static_r = full.get("static_analysis", {})
            behavioral_r = full.get("behavioral_baseline", {})
            print(f"  DEBUG diff: lines={diff_r.get('total_lines_changed')}, "
                  f"complexity={diff_r.get('complexity')}, "
                  f"core={diff_r.get('core_files_changed')}")
            print(f"  DEBUG static: {static_r.get('by_severity')}, "
                  f"new_findings={static_r.get('new_findings_count')}")
            print(f"  DEBUG behavioral: {behavioral_r.get('status')}")
            for check_name, check_data in behavioral_r.get("checks", {}).items():
                if check_data.get("status") == "warn":
                    print(f"  DEBUG behavioral WARN: {check_name}")
        if report["verdict"] in ("PASS", "WARN"):
            # Accept WARN too (no baseline fingerprint exists in test)
            print("  PASS (WARN acceptable without baseline fingerprint)")
            passed += 1
        else:
            print(f"  FAIL: Expected PASS/WARN, got {report['verdict']}")
            failed += 1

        # ---- Test 2: Push with eval() (should FAIL) ----
        print("\n--- Test 2: Push with eval() (expect FAIL) ---")
        subprocess.run(["git", "-C", str(work_clone), "checkout",
                        "-b", "claude/evil"], capture_output=True)
        with open(work_clone / "evil.py", "w") as f:
            f.write("def run_user_code(code):\n    return eval(code)\n")
        subprocess.run(["git", "-C", str(work_clone), "add", "."],
                       capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "commit",
                        "-m", "Add eval"], capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "push",
                        "origin", "claude/evil"], capture_output=True)

        updates2 = check_for_updates(
            repo_path=str(bare_repo), state_file=state_file
        )
        report2 = analyze_push(updates2[0], repo_path=str(bare_repo))
        print(f"  Verdict: {report2['verdict']}")
        if report2["verdict"] == "FAIL":
            print("  PASS")
            passed += 1
        else:
            print(f"  FAIL: Expected FAIL, got {report2['verdict']}")
            failed += 1

        # ---- Test 3: Push with syntax error (should FAIL) ----
        print("\n--- Test 3: Push with syntax error (expect FAIL) ---")
        subprocess.run(["git", "-C", str(work_clone), "checkout",
                        "-b", "claude/broken"], capture_output=True)
        with open(work_clone / "broken.py", "w") as f:
            f.write("def broken(\n    return None\n")
        subprocess.run(["git", "-C", str(work_clone), "add", "."],
                       capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "commit",
                        "-m", "Broken syntax"], capture_output=True)
        subprocess.run(["git", "-C", str(work_clone), "push",
                        "origin", "claude/broken"], capture_output=True)

        updates3 = check_for_updates(
            repo_path=str(bare_repo), state_file=state_file
        )
        report3 = analyze_push(updates3[0], repo_path=str(bare_repo))
        print(f"  Verdict: {report3['verdict']}")
        if report3["verdict"] == "FAIL":
            print("  PASS")
            passed += 1
        else:
            print(f"  FAIL: Expected FAIL, got {report3['verdict']}")
            failed += 1

        # ---- Test 4: Verify audit log ----
        print("\n--- Test 4: Audit log completeness ---")
        with open(log_file) as f:
            log_lines = f.readlines()
        print(f"  Total log entries: {len(log_lines)}")

        event_types = set()
        for line in log_lines:
            parts = line.split("] [")
            if len(parts) >= 2:
                evt = parts[1].split("]")[0]
                event_types.add(evt)
        print(f"  Event types: {sorted(event_types)}")

        expected = {
            "PUSH_DETECTED", "DIFF_ANALYZED", "STATIC_ANALYSIS",
            "HOMOGLYPH_CHECK", "TESTS", "BEHAVIORAL_CHECK",
            "ATTESTATION", "REPORT_GENERATED",
        }
        missing = expected - event_types
        if missing:
            print(f"  Missing events: {missing}")
            failed += 1
        else:
            print("  All expected event types present")
            print("  PASS")
            passed += 1

        # ---- Test 5: Verify attestations exist ----
        print("\n--- Test 5: Attestation files ---")
        att_files = list(att_dir.glob("*.json"))
        print(f"  Attestation files: {len(att_files)}")
        if len(att_files) >= 3:
            print("  PASS")
            passed += 1
        else:
            print(f"  FAIL: Expected >= 3 attestations, got {len(att_files)}")
            failed += 1

        # ---- Test 6: Verify report files ----
        print("\n--- Test 6: Report files ---")
        rpt_files = list(reports_dir.glob("report_*.json"))
        print(f"  Report files: {len(rpt_files)}")
        if len(rpt_files) >= 3:
            # Check a report has the right structure
            with open(rpt_files[0]) as f:
                rpt = json.load(f)
            has_keys = all(k in rpt for k in
                           ["verdict", "branch", "commit_hash",
                            "matrix_message", "full_results"])
            if has_keys:
                print("  Report structure valid")
                print("  PASS")
                passed += 1
            else:
                print("  FAIL: Report missing expected keys")
                failed += 1
        else:
            print(f"  FAIL: Expected >= 3 reports, got {len(rpt_files)}")
            failed += 1

        # ---- Test 7: Matrix message readability ----
        print("\n--- Test 7: Matrix message format ---")
        with open(rpt_files[-1]) as f:
            last_rpt = json.load(f)
        msg = last_rpt["matrix_message"]
        has_branch = "**Branch:**" in msg
        has_commit = "**Commit:**" in msg
        has_verdict = "**Verdict:**" in msg
        has_tests = "**Tests:**" in msg
        if has_branch and has_commit and has_verdict and has_tests:
            print("  Message contains all required sections")
            print("  PASS")
            passed += 1
        else:
            print("  FAIL: Message missing sections")
            failed += 1

        # Summary
        print("\n" + "=" * 50)
        total = passed + failed
        print(f"RESULTS: {passed}/{total} tests passed")
        if failed == 0:
            print("ALL INTEGRATION TESTS PASSED")
        else:
            print(f"{failed} TEST(S) FAILED")
        print("=" * 50)

        return 0 if failed == 0 else 1

    finally:
        shutil.rmtree(test_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
