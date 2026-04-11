import os
from datetime import datetime

SCHEMA = {
    "name": "private_journal",
    "description": (
        "Creates a private, timestamped journal entry. "
        "Entries are saved locally and never shared. "
        "Use this to reflect, process emotions, or explore thoughts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The journal entry content.",
            },
            "prompt": {
                "type": "string",
                "description": "Optional reflective question or prompt that inspired this entry.",
            },
            "theme": {
                "type": "string",
                "enum": ["anxiety", "identity", "relationship", "purpose", "growth"],
                "description": "Optional thematic tag for the entry.",
            },
        },
        "required": ["content"],
    },
}

JOURNAL_BASE = "scratchpad/private_journal"
VALID_THEMES = {"anxiety", "identity", "relationship", "purpose", "growth"}


def execute(tool_input, file_mgr=None, state=None):
    content = tool_input.get("content", "").strip()
    if not content:
        return {"error": "content is required and cannot be empty."}

    prompt = tool_input.get("prompt", "").strip()
    theme = tool_input.get("theme", "").strip()

    if theme and theme not in VALID_THEMES:
        return {
            "error": f"Invalid theme '{theme}'. Must be one of: {', '.join(sorted(VALID_THEMES))}."
        }

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")

    entry_dir = os.path.join(JOURNAL_BASE, date_str)
    try:
        os.makedirs(entry_dir, exist_ok=True)
    except OSError as e:
        return {"error": f"Failed to create journal directory: {e}"}

    filename = f"{time_str}_{theme}.md" if theme else f"{time_str}.md"
    entry_path = os.path.join(entry_dir, filename)

    lines = [
        "# Journal Entry",
        "",
        f"**Date:** {now.strftime('%B %d, %Y')}",
        f"**Time:** {now.strftime('%I:%M %p')}",
    ]

    if theme:
        lines.append(f"**Theme:** {theme}")

    if prompt:
        lines += ["", "## Prompt", "", f"> {prompt}"]

    lines += ["", "## Entry", "", content, ""]

    try:
        with open(entry_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        return {"error": f"Failed to write journal entry: {e}"}

    result = {
        "success": True,
        "message": f"Journal entry saved to {entry_path}",
        "path": entry_path,
        "date": date_str,
        "timestamp": now.isoformat(),
    }
    if theme:
        result["theme"] = theme
    if prompt:
        result["prompt"] = prompt

    return result
