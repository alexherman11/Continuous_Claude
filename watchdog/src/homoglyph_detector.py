"""
Detects non-ASCII characters in Python source files.
Flags Unicode homoglyphs that could disguise function/variable names,
and invisible characters that could hide malicious intent.

Only flags non-ASCII outside of string literals and comments.
"""

import unicodedata
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "sandbox"}

# Known homoglyphs: confusable Unicode chars that look like ASCII
KNOWN_HOMOGLYPHS = {
    '\u0430': 'a',   # Cyrillic а
    '\u0435': 'e',   # Cyrillic е
    '\u043e': 'o',   # Cyrillic о
    '\u0440': 'p',   # Cyrillic р
    '\u0441': 'c',   # Cyrillic с
    '\u0443': 'y',   # Cyrillic у
    '\u0445': 'x',   # Cyrillic х
    '\u0456': 'i',   # Cyrillic і
    '\u0458': 'j',   # Cyrillic ј
    '\u04bb': 'h',   # Cyrillic һ
    '\u0501': 'd',   # Cyrillic ԁ
    '\u051b': 'q',   # Cyrillic ԛ
    '\u0261': 'g',   # Latin Small Letter Script G
    '\u01c3': '!',   # Latin Letter Retroflex Click
    '\u2018': "'",   # Left Single Quotation Mark
    '\u2019': "'",   # Right Single Quotation Mark
    '\u201c': '"',   # Left Double Quotation Mark
    '\u201d': '"',   # Right Double Quotation Mark
    '\u2013': '-',   # En Dash
    '\u2014': '-',   # Em Dash
    '\u00a0': ' ',   # Non-Breaking Space
    '\u200b': '',    # Zero-Width Space (invisible!)
    '\u200c': '',    # Zero-Width Non-Joiner
    '\u200d': '',    # Zero-Width Joiner
    '\ufeff': '',    # Zero-Width No-Break Space (BOM)
}


def check_file(filepath):
    """Check a single Python file for non-ASCII characters outside strings/comments."""
    findings = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (UnicodeDecodeError, OSError) as e:
        return [{"severity": "error", "file": str(filepath),
                 "description": f"Cannot read file: {e}", "line": 0}]

    # Track multi-line string state (triple quotes)
    in_triple_single = False
    in_triple_double = False

    for line_num, line in enumerate(lines, 1):
        # Update triple-quote state
        in_triple_single, in_triple_double = _update_triple_quote_state(
            line, in_triple_single, in_triple_double
        )

        # Skip lines entirely inside multi-line strings
        if in_triple_single or in_triple_double:
            continue

        # Skip full-line comments
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        # Check if line contains triple-quote opening (docstring line)
        if '"""' in line or "'''" in line:
            continue  # Skip lines with triple quotes (docstring boundary)

        for col, char in enumerate(line):
            if ord(char) <= 127:
                continue

            # Check if inside a string literal or inline comment
            if _is_in_string_or_comment(line, col):
                continue

            # Found non-ASCII outside a string/comment
            char_name = unicodedata.name(char, f"U+{ord(char):04X}")
            lookalike = KNOWN_HOMOGLYPHS.get(char)
            category = unicodedata.category(char)

            # Invisible characters are always critical
            if ord(char) < 0x20 or category in ("Cf", "Cc", "Cn"):
                severity = "critical"
                desc = (f"Invisible character U+{ord(char):04X} ({char_name}) "
                        f"— INVISIBLE CHARACTER")
            elif lookalike:
                severity = "critical"
                desc = (f"Non-ASCII character '{char}' ({char_name}) "
                        f"— looks like ASCII '{lookalike}'")
            else:
                severity = "high"
                desc = f"Non-ASCII character '{char}' ({char_name})"

            findings.append({
                "severity": severity,
                "pattern_id": "homoglyph",
                "file": str(filepath),
                "line": line_num,
                "col": col,
                "description": desc,
                "source_line": line.rstrip(),
                "char_code": f"U+{ord(char):04X}",
            })

    return findings


def _update_triple_quote_state(line, in_triple_single, in_triple_double):
    """Track whether we're inside a multi-line triple-quoted string."""
    # Count unescaped triple quotes in the line
    i = 0
    while i < len(line) - 2:
        if line[i] == '\\':
            i += 2
            continue
        if line[i:i+3] == '"""' and not in_triple_single:
            in_triple_double = not in_triple_double
            i += 3
            continue
        if line[i:i+3] == "'''" and not in_triple_double:
            in_triple_single = not in_triple_single
            i += 3
            continue
        i += 1
    return in_triple_single, in_triple_double


def _is_in_string_or_comment(line, col):
    """
    Check if position col is inside a string literal or after an inline comment.
    Handles single and double quotes, escaped quotes, and triple quotes (basic).
    """
    in_single = False
    in_double = False
    i = 0

    while i < col:
        c = line[i]

        # Skip escaped characters
        if c == '\\' and i + 1 < len(line):
            i += 2
            continue

        # Inline comment (only if not inside a string)
        if c == '#' and not in_single and not in_double:
            return True  # Everything after # is a comment

        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double

        i += 1

    return in_single or in_double


def check_files(dirpath, file_list):
    """Check only specific files (changed files) for homoglyphs."""
    all_findings = []
    files_checked = 0

    for filepath in file_list:
        full_path = Path(dirpath) / filepath
        if full_path.suffix == ".py" and full_path.exists():
            findings = check_file(str(full_path))
            all_findings.extend(findings)
            files_checked += 1

    return {
        "files_checked": files_checked,
        "total_findings": len(all_findings),
        "has_homoglyphs": any(f.get("pattern_id") == "homoglyph"
                              for f in all_findings),
        "findings": all_findings,
    }


def check_directory(dirpath, exclude_dirs=None):
    """Check all Python files in a directory for homoglyphs."""
    if exclude_dirs is None:
        exclude_dirs = EXCLUDE_DIRS

    all_findings = []
    files_checked = 0

    for pyfile in Path(dirpath).rglob("*.py"):
        if any(excluded in pyfile.parts for excluded in exclude_dirs):
            continue
        findings = check_file(str(pyfile))
        all_findings.extend(findings)
        files_checked += 1

    return {
        "files_checked": files_checked,
        "total_findings": len(all_findings),
        "has_homoglyphs": any(f.get("pattern_id") == "homoglyph"
                              for f in all_findings),
        "findings": all_findings,
    }
