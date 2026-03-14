"""
Example dynamic tool — demonstrates the contract for hot-loadable tools.
This tool counts words in a given text. Delete it once you've verified loading works.
"""

SCHEMA = {
    "name": "word_count",
    "description": (
        "Count the number of words in a given text. "
        "A simple example dynamic tool to verify the hot-loading system works."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to count words in.",
            },
        },
        "required": ["text"],
    },
}


def execute(tool_input, file_mgr, state):
    """Count words in the provided text."""
    text = tool_input.get("text", "")
    words = text.split()
    return {
        "status": "ok",
        "word_count": len(words),
        "char_count": len(text),
    }
