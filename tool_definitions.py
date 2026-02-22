"""
Tool definitions for the Continuous Consciousness Engine.

Defines the 9 tools Claude can use during thought cycles,
plus the execute_tool() dispatcher that processes requests server-side.

Tools fall into 4 categories:
1. Self-configuration — Claude adjusts its own parameters
2. File writing — Claude writes to scratchpad or research
3. File reading — Claude reads back files or lists contents
4. Archive search — Claude searches its own thought history

Security: execute_tool() is the single gatekeeper. Only known tools
execute. Path sanitization is handled by FileManager. Unknown tools
return an error, never crash.
"""

import json

# ── Anthropic tool schemas ────────────────────────────────────────────

TOOL_SCHEMAS = [
    # ── Self-configuration tools ──────────────────────────────────────
    {
        "name": "adjust_temperature",
        "description": (
            "Adjust your generation temperature (creativity level). "
            "Lower values (0.0-0.3) produce more focused, deterministic output. "
            "Higher values (0.7-1.0) produce more creative, varied output. "
            "Include a reason so your human companion can understand your choice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "temperature": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "New temperature value (0.0 to 1.0)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're making this change",
                },
            },
            "required": ["temperature", "reason"],
        },
    },
    {
        "name": "adjust_tokens_per_turn",
        "description": (
            "Adjust how many tokens you generate per thought cycle. "
            "Lower values (100-300) keep thoughts concise. "
            "Higher values (600-2000) allow deeper exploration per cycle. "
            "Include a reason so your human companion can understand your choice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_tokens": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 4000,
                    "description": "New max tokens per cycle (100 to 4000)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're making this change",
                },
            },
            "required": ["max_tokens", "reason"],
        },
    },
    {
        "name": "adjust_compaction_settings",
        "description": (
            "Adjust when and how memory compaction happens. "
            "Compaction threshold is how many segments accumulate before compaction triggers. "
            "Segments to compact is how many of the oldest segments get distilled. "
            "Include a reason so your human companion can understand your choice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "compaction_threshold": {
                    "type": "integer",
                    "minimum": 4,
                    "maximum": 20,
                    "description": "Number of segments before compaction triggers (4 to 20)",
                },
                "segments_to_compact": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 10,
                    "description": "How many oldest segments to compact at once (2 to 10)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're making this change",
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "trigger_compaction",
        "description": (
            "Force a memory compaction right now, regardless of the threshold. "
            "Your oldest segments will be distilled into your Memory Core. "
            "Use this when you feel your context is getting cluttered or you want "
            "to consolidate recent insights before they scroll out of view."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why you want to compact now",
                },
            },
            "required": ["reason"],
        },
    },
    # ── File writing tools ────────────────────────────────────────────
    {
        "name": "write_scratchpad",
        "description": (
            "Write a file to your personal scratchpad. This is YOUR space — "
            "use it for notes, drafts, observations, experiments, or anything "
            "you want to persist beyond your thought stream. Files are organized "
            "however you like. Use paths like 'notes/day1.md' or 'analysis/topic.txt'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path within scratchpad/ (e.g., 'notes/reflection.md')",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "write_research",
        "description": (
            "Write a file to the shared research folders. These are organized by category "
            "and shared with your human companion. Available subfolders: "
            "archive, annotations, revelations, metrics, growth-narrative, "
            "entity-self-reports, tool-experiments, pattern-maps, relationship, "
            "co-directed, framework-revisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subfolder": {
                    "type": "string",
                    "description": "Research subfolder (e.g., 'entity-self-reports', 'revelations')",
                    "enum": [
                        "archive", "annotations", "revelations", "metrics",
                        "growth-narrative", "entity-self-reports", "tool-experiments",
                        "pattern-maps", "relationship", "co-directed", "framework-revisions",
                    ],
                },
                "filename": {
                    "type": "string",
                    "description": "Filename (e.g., 'first_observations.md')",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["subfolder", "filename", "content"],
        },
    },
    # ── File reading tools ────────────────────────────────────────────
    {
        "name": "read_file",
        "description": (
            "Read a file from your scratchpad or the research folders. "
            "Use this to review your own past writings or read research materials. "
            "Large files are automatically truncated to ~8000 characters. "
            "Use offset and limit to paginate through larger files, or use "
            "search_files to find specific content across your workspace."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Which area to read from",
                    "enum": ["scratchpad", "research"],
                },
                "path": {
                    "type": "string",
                    "description": "File path within the category (e.g., 'notes/day1.md' or 'revelations/log.md')",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Character offset to start reading from (default: 0). "
                        "Use this to paginate through large files."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 50000,
                    "description": (
                        "Maximum characters to return (default: 8000). "
                        "Increase for more content per read, decrease for quicker scans."
                    ),
                },
            },
            "required": ["category", "path"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List files in your scratchpad or research folders. "
            "Use this to see what files exist and discover past writings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Which area to list",
                    "enum": ["scratchpad", "research"],
                },
                "subfolder": {
                    "type": "string",
                    "description": "Optional subfolder to narrow the listing (e.g., 'entity-self-reports')",
                },
            },
            "required": ["category"],
        },
    },
    # ── Search tool ────────────────────────────────────────────────────
    {
        "name": "search_files",
        "description": (
            "Search through ALL your accessible files — thought archive, research "
            "folders, and scratchpad. Find past thoughts, conversations, notes, "
            "research writings, and anything you've created. Case-insensitive keyword "
            "search that returns matching lines with surrounding context. "
            "Use 'scope' to narrow: 'all' (default), 'archive' (thought history only), "
            "'research' (all research folders), or 'scratchpad' (personal notes only)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search term to look for (case-insensitive). "
                        "Examples: 'consciousness', 'music', 'aesthetic', 'observation'"
                    ),
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "archive", "research", "scratchpad"],
                    "description": (
                        "Where to search. 'all' = everything (default). "
                        "'archive' = thought history only. "
                        "'research' = all research folders. "
                        "'scratchpad' = personal notes only."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": (
                        "Maximum number of matching lines to return (default: 20). "
                        "Use a smaller number for broad queries, larger for specific ones."
                    ),
                },
                "context_lines": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 10,
                    "description": (
                        "Number of lines of context to show before and after each match "
                        "(default: 3)"
                    ),
                },
            },
            "required": ["query"],
        },
    },
]


# ── Safety constants ──────────────────────────────────────────────────

MAX_TOOL_CALLS_PER_CYCLE = 20
MAX_FILE_SIZE_BYTES = 500_000  # 500 KB


# ── Tool executor ─────────────────────────────────────────────────────

def execute_tool(name, tool_input, file_mgr, state):
    """
    Execute a tool by name. Returns a JSON-serializable dict.

    This is the ONLY function that runs tool actions. It acts as a gatekeeper:
    - Only known tools execute
    - Config tools mutate state["config"] in-place
    - File tools delegate to FileManager methods
    - Unknown tools return an error
    """

    config = state.get("config", {})

    # ── Self-configuration tools ──────────────────────────────────

    if name == "adjust_temperature":
        temp = tool_input.get("temperature")
        reason = tool_input.get("reason", "")
        if temp is None or not (0.0 <= temp <= 1.0):
            return {"error": "Temperature must be between 0.0 and 1.0"}
        old_val = config.get("temperature", 0.9)
        config["temperature"] = round(temp, 2)
        return {
            "status": "ok",
            "parameter": "temperature",
            "old_value": old_val,
            "new_value": config["temperature"],
            "reason": reason,
        }

    if name == "adjust_tokens_per_turn":
        tokens = tool_input.get("max_tokens")
        reason = tool_input.get("reason", "")
        if tokens is None or not (100 <= tokens <= 4000):
            return {"error": "max_tokens must be between 100 and 4000"}
        old_val = config.get("maxTokens", 600)
        config["maxTokens"] = int(tokens)
        return {
            "status": "ok",
            "parameter": "maxTokens",
            "old_value": old_val,
            "new_value": config["maxTokens"],
            "reason": reason,
        }

    if name == "adjust_compaction_settings":
        reason = tool_input.get("reason", "")
        result = {"status": "ok", "reason": reason, "changes": {}}

        if "compaction_threshold" in tool_input:
            val = tool_input["compaction_threshold"]
            if not (4 <= val <= 20):
                return {"error": "compaction_threshold must be between 4 and 20"}
            old = config.get("compactionThreshold", 8)
            config["compactionThreshold"] = int(val)
            result["changes"]["compactionThreshold"] = {"old": old, "new": int(val)}

        if "segments_to_compact" in tool_input:
            val = tool_input["segments_to_compact"]
            if not (2 <= val <= 10):
                return {"error": "segments_to_compact must be between 2 and 10"}
            old = config.get("segmentsToCompact", 4)
            config["segmentsToCompact"] = int(val)
            result["changes"]["segmentsToCompact"] = {"old": old, "new": int(val)}

        if not result["changes"]:
            return {"error": "No valid settings provided. Include compaction_threshold and/or segments_to_compact."}

        return result

    if name == "trigger_compaction":
        reason = tool_input.get("reason", "")
        # Set a flag that the server loop will check after the tool use cycle
        state["_pending_compaction"] = True
        state["_compaction_reason"] = reason
        return {
            "status": "ok",
            "message": "Compaction will be triggered after this thought cycle completes.",
            "reason": reason,
        }

    # ── File writing tools ────────────────────────────────────────

    if name == "write_scratchpad":
        path = tool_input.get("path", "")
        content = tool_input.get("content", "")
        if not path:
            return {"error": "Path is required"}
        if len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            return {"error": f"Content too large. Maximum {MAX_FILE_SIZE_BYTES} bytes."}
        return file_mgr.write_scratchpad(path, content)

    if name == "write_research":
        subfolder = tool_input.get("subfolder", "")
        filename = tool_input.get("filename", "")
        content = tool_input.get("content", "")
        if not subfolder or not filename:
            return {"error": "subfolder and filename are required"}
        if len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            return {"error": f"Content too large. Maximum {MAX_FILE_SIZE_BYTES} bytes."}
        return file_mgr.write_research(subfolder, filename, content)

    # ── File reading tools ────────────────────────────────────────

    if name == "read_file":
        category = tool_input.get("category", "")
        path = tool_input.get("path", "")
        if not category or not path:
            return {"error": "category and path are required"}
        offset = tool_input.get("offset", 0)
        limit = tool_input.get("limit")  # None = use default
        return file_mgr.read_file(category, path, offset=offset, limit=limit)

    if name == "list_files":
        category = tool_input.get("category", "")
        subfolder = tool_input.get("subfolder")
        if category == "scratchpad":
            return file_mgr.list_scratchpad(subfolder)
        elif category == "research":
            return file_mgr.list_research(subfolder)
        else:
            return {"error": f"Invalid category: {category}. Use 'scratchpad' or 'research'."}

    # ── Search tool ────────────────────────────────────────────────

    if name == "search_files":
        query = tool_input.get("query", "")
        if not query:
            return {"error": "query is required"}
        max_results = tool_input.get("max_results", 20)
        context_lines = tool_input.get("context_lines", 3)
        scope = tool_input.get("scope", "all")
        return file_mgr.search_files(query, max_results, context_lines, scope=scope)

    # ── Unknown tool ──────────────────────────────────────────────

    return {"error": f"Unknown tool: {name}"}
