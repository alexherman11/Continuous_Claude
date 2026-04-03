"""
Multi-level test runner for the Continuous Claude codebase.

Levels:
  1. Syntax check (py_compile) — can all .py files parse?
  2. Import check — can all modules be AST-parsed?
  3. Unit tests — run any test_*.py or *_test.py files via pytest
  4. Boot test — can the Flask server start and respond?
"""

import subprocess
import sys
import json
import os
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)

EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv",
                "sandbox", "watchdog", "research", "scratchpad"}


def run_syntax_check(clone_path):
    """Level 1: Verify all Python files parse without errors."""
    results = {"status": "pass", "errors": [], "files_checked": 0}

    for pyfile in Path(clone_path).rglob("*.py"):
        if any(skip in pyfile.parts for skip in EXCLUDE_DIRS):
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
    """Level 2: Verify required modules can be AST-parsed."""
    required = get("required_modules", [
        "consciousness_server", "continuous_consciousness",
        "continuous_runner", "tool_definitions", "change_manager",
        "file_manager", "matrix_client", "budget_tracker",
    ])

    results = {"status": "pass", "errors": [], "modules_checked": []}

    for module_name in required:
        module_file = Path(clone_path) / f"{module_name}.py"
        if not module_file.exists():
            results["errors"].append({
                "module": module_name,
                "error": "Module file does not exist"
            })
            continue

        results["modules_checked"].append(module_name)

        # Parse with AST to verify structure without executing
        try:
            import ast
            with open(module_file, "r", encoding="utf-8") as f:
                ast.parse(f.read(), filename=str(module_file))
        except SyntaxError as e:
            results["errors"].append({
                "module": module_name,
                "error": f"Syntax error: {e}"
            })
        except Exception as e:
            results["errors"].append({
                "module": module_name,
                "error": str(e)
            })

    if results["errors"]:
        results["status"] = "fail"

    return results


def run_unit_tests(clone_path):
    """Level 3: Run any test files found in the project."""
    results = {"status": "pass", "errors": [], "tests_found": 0, "tests_passed": 0}

    # Find test files
    test_files = []
    for pattern in ("test_*.py", "*_test.py"):
        test_files.extend(Path(clone_path).glob(pattern))
    tests_dir = Path(clone_path) / "tests"
    if tests_dir.is_dir():
        test_files.extend(tests_dir.glob("test_*.py"))
        test_files.extend(tests_dir.glob("*_test.py"))

    results["tests_found"] = len(test_files)

    if not test_files:
        results["status"] = "skip"
        results["note"] = "No test files found"
        return results

    for test_file in test_files:
        try:
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
        except subprocess.TimeoutExpired:
            results["errors"].append({
                "file": str(test_file.relative_to(clone_path)),
                "error": "Test execution timed out (>120s)"
            })

    if results["errors"]:
        results["status"] = "fail"

    return results


def run_boot_test(clone_path):
    """
    Level 4: Attempt to start the Flask server and verify key endpoints respond.
    Uses a test config — no real API key needed for basic health checks.
    """
    results = {"status": "pass", "errors": [], "endpoints_checked": []}

    # Create a minimal test config
    config_dir = Path(clone_path) / "consciousness_data"
    config_dir.mkdir(exist_ok=True)
    config_file = config_dir / "config.json"

    # Preserve existing config
    existing_config = None
    if config_file.exists():
        existing_config = config_file.read_text()

    server = None
    try:
        test_config = {
            "maxTokens": 100,
            "pauseBetweenCycles": 999999,
            "temperature": 0.5,
        }
        config_file.write_text(json.dumps(test_config))

        # Build environment with a fake API key
        env = dict(os.environ)
        env["FLASK_ENV"] = "testing"
        env["ANTHROPIC_API_KEY"] = "sk-test-not-real-key"

        # Start the server
        server = subprocess.Popen(
            [sys.executable, "consciousness_server.py"],
            cwd=clone_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env
        )

        # Wait for server to start
        time.sleep(4)

        # Check if server is still running
        if server.poll() is not None:
            stderr = server.stderr.read().decode()[-500:]
            results["status"] = "fail"
            results["errors"].append({
                "error": f"Server crashed on startup: {stderr}"
            })
            return results

        # Test the state endpoint
        endpoints = [
            ("GET", "http://127.0.0.1:5000/api/state", None),
        ]

        for method, url, _expected in endpoints:
            try:
                req = urllib.request.Request(url, method=method)
                response = urllib.request.urlopen(req, timeout=5)
                results["endpoints_checked"].append({
                    "endpoint": url, "status": response.status, "pass": True
                })
            except urllib.error.HTTPError as e:
                # Any HTTP response means the server is alive
                results["endpoints_checked"].append({
                    "endpoint": url, "status": e.code,
                    "pass": e.code < 500
                })
            except Exception as e:
                results["endpoints_checked"].append({
                    "endpoint": url, "error": str(e), "pass": False
                })
                results["errors"].append({
                    "error": f"Endpoint {url} failed: {e}"
                })

    except Exception as e:
        results["status"] = "fail"
        results["errors"].append({"error": f"Boot test failed: {e}"})

    finally:
        # Clean up: kill the server
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=2)

        # Restore original config
        if existing_config is not None:
            config_file.write_text(existing_config)
        elif config_file.exists():
            config_file.unlink()

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

    # Overall: fail if any critical level fails
    overall = "pass"
    for level_name in ("syntax_check", "import_check", "unit_tests"):
        if results[level_name]["status"] == "fail":
            overall = "fail"
            break

    # Boot test failure is a warning, not a hard fail
    if results["boot_test"]["status"] == "fail" and overall == "pass":
        overall = "warn"

    results["overall_status"] = overall
    return results
