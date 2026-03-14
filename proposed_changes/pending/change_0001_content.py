"""Test tool proposed through the change system."""

SCHEMA = {
    "name": "char_frequency",
    "description": "Count character frequency in text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to analyze"},
        },
        "required": ["text"],
    },
}

def execute(tool_input, file_mgr, state):
    text = tool_input.get("text", "")
    freq = {}
    for c in text.lower():
        if c.isalpha():
            freq[c] = freq.get(c, 0) + 1
    top_5 = sorted(freq.items(), key=lambda x: -x[1])[:5]
    return {"status": "ok", "frequencies": dict(top_5), "total_chars": len(text)}
