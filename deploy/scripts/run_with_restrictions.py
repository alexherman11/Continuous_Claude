#!/usr/bin/env python3
"""
Wrapper that installs security restrictions before running the target script.
Usage: python3 run_with_restrictions.py <script.py> [args...]
"""
import sys
import os

# Install Matrix restriction
sys.path.insert(0, "/scripts")
import matrix_restriction
matrix_restriction.install()

# Now run the target script
if len(sys.argv) > 1:
    target = sys.argv[1]
    sys.argv = sys.argv[1:]  # Remove wrapper from argv

    # Add the target script's directory to sys.path so imports work
    target_dir = os.path.dirname(os.path.abspath(target))
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)
    os.chdir(target_dir)

    # Execute the target script
    with open(target) as f:
        code = f.read()
    exec(compile(code, target, "exec"), {"__name__": "__main__", "__file__": os.path.abspath(target)})
