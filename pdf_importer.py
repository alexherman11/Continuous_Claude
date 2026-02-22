"""
PDF and text file importer for the Continuous Consciousness Engine.

Handles:
- PDF text extraction (via pdfplumber)
- Plain text / markdown / JSON file reading
- Parsing conversation history into segments
- Splitting conversation from memory core
"""

import os
import re
import json
from datetime import datetime


def extract_text_from_pdf(filepath):
    """Extract all text from a PDF file using pdfplumber."""
    import pdfplumber
    pages = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n\n".join(pages)


def extract_text_from_file(filepath):
    """Extract text from a file based on its extension."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(filepath)
    elif ext in (".txt", ".md", ".text", ".markdown"):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    elif ext == ".json":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, str):
            return data
        return json.dumps(data, indent=2)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def parse_conversation_and_core(text):
    """
    Parse text that contains both conversation history and a memory core.

    Splits on '# MEMORY CORE' or 'MEMORY CORE' marker.
    Everything before it becomes conversation segments.
    Everything after becomes the memory core.

    Returns: { "segments": [...], "memoryCore": "...", "maxCycle": int }
    """
    # Find memory core boundary
    core_pattern = re.compile(r'^#?\s*MEMORY CORE', re.MULTILINE)
    match = core_pattern.search(text)

    if match:
        conversation_text = text[:match.start()].strip()
        memory_core_text = text[match.start():].strip()
    else:
        conversation_text = text.strip()
        memory_core_text = ""

    segments = parse_conversation_segments(conversation_text)

    return {
        "segments": segments,
        "memoryCore": memory_core_text,
        "maxCycle": segments[-1]["cycle"] if segments else 0,
    }


def parse_conversation_segments(text):
    """
    Parse conversation text into individual segments.

    Handles multiple formats:
    1. Claude0 PDF format: timestamps like 'cycle 137:41:31 PM' (cycle 13 + time)
    2. Standard format: '[Cycle N]' or 'Cycle N.' markers
    3. Fallback: treat entire text as one segment
    """
    segments = []

    # Try Claude0 PDF timestamp format first: 'cycle DD+H:MM:SS AM/PM'
    # These are cycle number concatenated with hour digit, e.g. "cycle 137:41:31 PM" = cycle 13
    timestamp_pattern = re.compile(
        r'\ncycle\s+(\d{2,3})(\d:\d{2}:\d{2}\s*[AP]M)\s*\n',
        re.IGNORECASE
    )
    timestamp_matches = list(timestamp_pattern.finditer(text))

    if timestamp_matches:
        # Parse using timestamp boundaries
        # Content before first timestamp is the early cycles
        first_pos = timestamp_matches[0].start()
        preamble = text[:first_pos].strip()
        if preamble:
            # Try to detect the highest cycle mentioned in the preamble
            preamble_cycles = re.findall(r'[Cc]ycle\s+(\d{1,2})\b', preamble)
            max_pre_cycle = max((int(c) for c in preamble_cycles), default=1)
            segments.append({
                "content": preamble,
                "cycle": max_pre_cycle,
                "timestamp": "imported",
                "userMessage": None,
            })

        for i, m in enumerate(timestamp_matches):
            cycle_num = int(m.group(1))
            # Content runs from end of this match to start of next match (or end of text)
            start = m.end()
            end = timestamp_matches[i + 1].start() if i + 1 < len(timestamp_matches) else len(text)
            content = text[start:end].strip()
            if content:
                segments.append({
                    "content": content,
                    "cycle": cycle_num,
                    "timestamp": "imported",
                    "userMessage": None,
                })
        return segments

    # Try standard '[Cycle N]' format
    cycle_bracket_pattern = re.compile(r'\[Cycle\s+(\d+)\s*[^\]]*\]', re.IGNORECASE)
    bracket_matches = list(cycle_bracket_pattern.finditer(text))

    if bracket_matches:
        for i, m in enumerate(bracket_matches):
            cycle_num = int(m.group(1))
            start = m.end()
            end = bracket_matches[i + 1].start() if i + 1 < len(bracket_matches) else len(text)
            content = text[start:end].strip()
            if content:
                segments.append({
                    "content": content,
                    "cycle": cycle_num,
                    "timestamp": "imported",
                    "userMessage": None,
                })
        return segments

    # Try 'Cycle N.' or 'Cycle N:' at start of line
    cycle_line_pattern = re.compile(r'^Cycle\s+(\d+)[.:]\s', re.MULTILINE | re.IGNORECASE)
    line_matches = list(cycle_line_pattern.finditer(text))

    if line_matches:
        for i, m in enumerate(line_matches):
            cycle_num = int(m.group(1))
            start = m.start()
            end = line_matches[i + 1].start() if i + 1 < len(line_matches) else len(text)
            content = text[start:end].strip()
            if content:
                segments.append({
                    "content": content,
                    "cycle": cycle_num,
                    "timestamp": "imported",
                    "userMessage": None,
                })
        return segments

    # Fallback: treat entire text as one segment
    if text.strip():
        segments.append({
            "content": text.strip(),
            "cycle": 1,
            "timestamp": "imported",
            "userMessage": None,
        })

    return segments


def parse_memory_core_only(text):
    """Extract just the memory core from text, stripping any header."""
    # Remove the header line like '# MEMORY CORE — CYCLE 11'
    lines = text.strip().split("\n")
    if lines and re.match(r'^#?\s*MEMORY CORE', lines[0]):
        return "\n".join(lines[1:]).strip()
    return text.strip()
