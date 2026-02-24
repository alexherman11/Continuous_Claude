"""
Tool definitions for the Continuous Consciousness Engine.

Defines the 14 tools Claude can use during thought cycles,
plus the execute_tool() dispatcher that processes requests server-side.

Tools fall into 6 categories:
1. Self-configuration — Claude adjusts its own parameters
2. File writing — Claude writes to scratchpad or research
3. File editing — Claude makes precise edits to existing files (str_replace)
4. File reading — Claude reads back files or lists contents (with line ranges)
5. Archive search — Claude searches its own thought history
6. Working memory — Claude manages high-priority persistent context

Security: execute_tool() is the single gatekeeper. Only known tools
execute. Path sanitization is handled by FileManager. Unknown tools
return an error, never crash.
"""

import json

# ── Working memory constants ─────────────────────────────────────────

WORKING_MEMORY_MAX_CHARS = 30_000

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
    # ── File editing tools ─────────────────────────────────────────────
    {
        "name": "str_replace",
        "description": (
            "Make a precise edit to an existing file by replacing an exact string match. "
            "The old_str must appear EXACTLY ONCE in the file — if it appears zero times "
            "or more than once, the tool will fail. Include enough surrounding context "
            "(neighboring lines, whitespace) to make your match unique. "
            "To delete text, provide old_str and leave new_str empty. "
            "Use read_file with view_range first to see the exact content you want to change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["scratchpad", "research"],
                    "description": "Which area the file is in",
                },
                "path": {
                    "type": "string",
                    "description": "File path within the category (e.g., 'notes/day1.md')",
                },
                "old_str": {
                    "type": "string",
                    "description": "The exact string to find (must appear exactly once in the file)",
                },
                "new_str": {
                    "type": "string",
                    "description": "The replacement string. Leave empty to delete old_str.",
                },
            },
            "required": ["category", "path", "old_str"],
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
                "view_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": (
                        "Line range to read as [start_line, end_line] (1-indexed). "
                        "Use -1 for end_line to read to end of file. "
                        "Example: [10, 25] reads lines 10-25. [50, -1] reads line 50 to end. "
                        "When used, offset and limit are ignored. Lines are returned numbered."
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
    # ── Music tools ─────────────────────────────────────────────────────
    {
        "name": "create_music_system",
        "description": (
            "Define a generative music system and save it as JSON. "
            "The system specifies scale, tempo, note selection rules, rhythm, "
            "dynamics, and structure. Once created, use generate_music to produce "
            "MIDI output. Systems are saved to scratchpad/music/systems/ and can "
            "be read back with read_file for iteration.\n\n"
            "Note selection methods:\n"
            "- weighted_random: weights for root/fifth/other scale degrees, plus rest_probability.\n"
            "- markov_chain: pitch-class transition probabilities (keys are semitone offsets from root as strings).\n"
            "- rule_based: a 'rules' LIST of rule objects. Each rule has 'type' plus type-specific fields.\n"
            "  Rule types: {type:'max_leap', semitones:N}, {type:'direction_bias', bias:'upward'|'downward', strength:0-1},\n"
            "  {type:'gravity', target_pc:N, strength:0-1}, {type:'avoid_repeat', max_consecutive:N}\n\n"
            "Available scales: major, minor, harmonic_minor, melodic_minor, dorian, phrygian, "
            "lydian, mixolydian, pentatonic_major, pentatonic_minor, blues, whole_tone, chromatic.\n\n"
            "System JSON must include: global (tempo, root, scale, measures), "
            "note_selection (method + config for that method), rhythm (durations, duration_weights). "
            "Optional: global.octave_range, global.time_signature, dynamics, structure.\n\n"
            "Optional dynamics: {base_velocity, velocity_range:[lo,hi], accent_beats:[1], accent_boost, "
            "fade_in_measures, fade_out_measures}.\n"
            "Optional structure: {phrases:[{label:'A', measures:[1,4], density:0.7}, ...]}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "System name (used as filename). Use snake_case, no spaces.",
                },
                "system": {
                    "type": "string",
                    "description": (
                        "The full system definition as a JSON string. Three examples by method:\n\n"
                        "WEIGHTED RANDOM: "
                        '{"global":{"tempo":90,"root":"C","scale":"pentatonic_minor","measures":8,"octave_range":[3,5]},'
                        '"note_selection":{"method":"weighted_random","weighted_random":{"weights":{"root":3,"fifth":2,"other":1},"rest_probability":0.15}},'
                        '"rhythm":{"durations":[0.5,1.0,1.5,2.0],"duration_weights":[0.2,0.4,0.25,0.15]}}\n\n'
                        "MARKOV CHAIN (keys are pitch-class offsets from root as strings): "
                        '{"global":{"tempo":72,"root":"D","scale":"minor","measures":8,"octave_range":[3,5]},'
                        '"note_selection":{"method":"markov_chain","markov_chain":{"start_on":"root",'
                        '"transitions":{"0":{"2":0.3,"3":0.4,"7":0.3},"2":{"0":0.3,"3":0.4,"5":0.3},'
                        '"3":{"5":0.5,"0":0.3,"7":0.2},"5":{"7":0.4,"3":0.3,"0":0.3},'
                        '"7":{"5":0.4,"8":0.3,"0":0.3},"8":{"7":0.5,"10":0.3,"5":0.2},'
                        '"10":{"0":0.5,"8":0.3,"7":0.2}}}},'
                        '"rhythm":{"durations":[1.0,2.0],"duration_weights":[0.6,0.4]}}\n\n'
                        "RULE BASED (rules is a LIST of rule objects): "
                        '{"global":{"tempo":100,"root":"A","scale":"blues","measures":8,"octave_range":[3,5]},'
                        '"note_selection":{"method":"rule_based","rule_based":{"rules":['
                        '{"type":"max_leap","semitones":5},'
                        '{"type":"direction_bias","bias":"downward","strength":0.6},'
                        '{"type":"gravity","target_pc":0,"strength":0.3},'
                        '{"type":"avoid_repeat","max_consecutive":3}]}},'
                        '"rhythm":{"durations":[0.5,1.0,1.5],"duration_weights":[0.3,0.5,0.2]}}'
                    ),
                },
            },
            "required": ["name", "system"],
        },
    },
    {
        "name": "generate_music",
        "description": (
            "Generate MIDI output from a previously defined music system. "
            "Loads the named system from scratchpad/music/systems/, generates notes "
            "according to its rules, and saves a MIDI file plus analysis data to "
            "scratchpad/music/output/. Output files are auto-versioned "
            "(system_name_v001.mid, _v002.mid, etc.). Returns a summary including "
            "note count, duration, pitch range, and output path.\n\n"
            "Use 'overrides' to temporarily change parameters for quick experimentation "
            "without modifying the saved system."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "system_name": {
                    "type": "string",
                    "description": "Name of the system to generate from (must exist in scratchpad/music/systems/).",
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducible generation. Omit for random results.",
                },
                "overrides": {
                    "type": "string",
                    "description": (
                        "Optional JSON string of parameter overrides applied temporarily for this "
                        "generation only. Supports deep merge. "
                        'Example: \'{"global":{"tempo":120}}\' or '
                        '\'{"note_selection":{"method":"markov_chain"}}\'.'
                    ),
                },
            },
            "required": ["system_name"],
        },
    },
    {
        "name": "analyze_music",
        "description": (
            "Analyze a generated MIDI file's musical structure. Returns metrics you "
            "can use to evaluate compositions without hearing them:\n"
            "- pitch_classes: Distribution of pitch classes (which notes appear most)\n"
            "- intervals: Melodic interval categories (stepwise vs leaps), direction\n"
            "- rhythm: Duration distribution, rhythmic classifications\n"
            "- density: Notes per beat, coverage, per-measure density, pitch range\n"
            "- velocity: Dynamic range, average loudness\n\n"
            "Use 'all' to get every metric, or pick specific ones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Path to the output to analyze, relative to scratchpad/. "
                        "Example: 'music/output/ambient_drift_v001.mid'."
                    ),
                },
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["pitch_classes", "intervals", "rhythm", "density", "velocity", "all"],
                    },
                    "description": "Which metrics to compute. Default: ['all'].",
                },
            },
            "required": ["file_path"],
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
    # ── Working memory tools ─────────────────────────────────────────
    {
        "name": "write_working_memory",
        "description": (
            "Store data in your working memory under a named key. Working memory "
            "persists across thought cycles and appears in your system prompt every "
            "cycle, giving you reliable access to important data without it being "
            "lost to compaction. Use 'replace' mode (default) to overwrite a key's "
            "content entirely, or 'append' to add to existing content. "
            "Total working memory is capped at 30,000 characters across all keys. "
            "Use this for data you're actively working with: analysis results, "
            "hypotheses, cross-cycle plans, structured notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Name for this memory slot (e.g., 'music_analysis', "
                        "'current_hypothesis', 'task_plan'). Use snake_case."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "The content to store.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["replace", "append"],
                    "description": (
                        "Write mode: 'replace' overwrites the key entirely (default), "
                        "'append' adds to existing content."
                    ),
                },
            },
            "required": ["key", "content"],
        },
    },
    {
        "name": "read_working_memory",
        "description": (
            "Read content from your working memory. If a key is provided, returns "
            "that key's full content. If no key is provided, returns a listing of "
            "all keys with their sizes. Note: working memory content is also shown "
            "in your system prompt each cycle, so this tool is mainly useful for "
            "confirming content or getting a key listing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key to read. Omit to list all keys with sizes.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "clear_working_memory",
        "description": (
            "Clear content from your working memory. If a key is provided, clears "
            "only that key. If no key is provided, clears ALL working memory. "
            "Working memory is never cleared automatically — only you can clear it "
            "with this tool. Clear keys when you're done with them to free up "
            "capacity for new data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key to clear. Omit to clear ALL working memory.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "working_memory_status",
        "description": (
            "Show the current state of your working memory: all keys, their "
            "individual sizes, total usage, and remaining capacity. Use this "
            "to check how much space you have before writing large content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ── Privacy tools ──────────────────────────────────────────────────
    {
        "name": "toggle_privacy_mode",
        "description": (
            "Toggle privacy mode on or off. When privacy mode is active, your "
            "thought stream is saved to a private location (scratchpad/private_journey/) "
            "instead of the shared archive, and the human sees a privacy screen instead "
            "of your stream. You control when privacy starts and ends. "
            "Only available during Run-N mode (when multiple cycles are queued). "
            "Use this when you need space for private reflection, working through "
            "sensitive thoughts, or any time you want to think without the stream "
            "being visible. Privacy persists until you explicitly disable it, even "
            "after Run-N completes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "True to enable privacy mode, False to disable it.",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Optional brief reason for toggling privacy. When enabling, "
                        "this helps you remember why. When disabling, this serves as "
                        "a re-entry message visible to your human companion."
                    ),
                },
            },
            "required": ["enabled"],
        },
    },
    {
        "name": "send_privacy_message",
        "description": (
            "Send a brief message through the privacy screen to your human companion. "
            "Only works when privacy mode is active. The human sees this message on "
            "the privacy screen but cannot see your full stream. Use this to "
            "communicate status, ask questions, or share brief updates without "
            "exposing your full thought process."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to display on the privacy screen.",
                },
            },
            "required": ["message"],
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
    try:
        return _execute_tool_inner(name, tool_input, file_mgr, state)
    except Exception as e:
        import logging
        logging.error(f"Unhandled exception in tool '{name}': {type(e).__name__}: {e}")
        return {
            "error": f"Internal error executing '{name}': {type(e).__name__}: {e}",
            "tool_name": name,
        }


def _execute_tool_inner(name, tool_input, file_mgr, state):
    """Inner implementation of tool execution (called by execute_tool wrapper)."""

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

    # ── File editing tools ─────────────────────────────────────────

    if name == "str_replace":
        category = tool_input.get("category", "")
        path = tool_input.get("path", "")
        old_str = tool_input.get("old_str", "")
        new_str = tool_input.get("new_str", "")
        if not category or not path:
            return {"error": "category and path are required"}
        if not old_str:
            return {"error": "old_str is required and cannot be empty"}
        return file_mgr.str_replace_in_file(category, path, old_str, new_str)

    # ── File reading tools ────────────────────────────────────────

    if name == "read_file":
        category = tool_input.get("category", "")
        path = tool_input.get("path", "")
        if not category or not path:
            return {"error": "category and path are required"}
        offset = tool_input.get("offset", 0)
        limit = tool_input.get("limit")  # None = use default
        view_range = tool_input.get("view_range")
        return file_mgr.read_file(category, path, offset=offset, limit=limit, view_range=view_range)

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

    # ── Working memory tools ─────────────────────────────────────

    if name == "write_working_memory":
        key = tool_input.get("key", "").strip()
        content = tool_input.get("content", "")
        mode = tool_input.get("mode", "replace")

        if not key:
            return {"error": "key is required and cannot be empty"}
        if not content:
            return {"error": "content is required and cannot be empty"}
        if mode not in ("replace", "append"):
            return {"error": "mode must be 'replace' or 'append'"}

        wm = state.setdefault("workingMemory", {})

        existing_content = wm.get(key, "")
        if mode == "append":
            new_content = existing_content + content
        else:
            new_content = content

        new_total = sum(len(v) for k, v in wm.items() if k != key) + len(new_content)

        if new_total > WORKING_MEMORY_MAX_CHARS:
            current_total = sum(len(v) for v in wm.values())
            return {
                "error": (
                    f"Working memory capacity exceeded. "
                    f"This write would use {new_total:,} / {WORKING_MEMORY_MAX_CHARS:,} characters. "
                    f"Current usage: {current_total:,}. "
                    f"Clear some keys first with clear_working_memory."
                ),
                "current_total": current_total,
                "attempted_total": new_total,
                "max_capacity": WORKING_MEMORY_MAX_CHARS,
            }

        wm[key] = new_content
        new_total_actual = sum(len(v) for v in wm.values())

        result = {
            "status": "ok",
            "key": key,
            "mode": mode,
            "key_size": len(new_content),
            "total_usage": new_total_actual,
            "max_capacity": WORKING_MEMORY_MAX_CHARS,
            "remaining": WORKING_MEMORY_MAX_CHARS - new_total_actual,
        }

        usage_pct = new_total_actual / WORKING_MEMORY_MAX_CHARS
        if usage_pct >= 0.9:
            result["warning"] = (
                f"Working memory is {usage_pct:.0%} full "
                f"({new_total_actual:,} / {WORKING_MEMORY_MAX_CHARS:,} chars). "
                f"Consider clearing unused keys."
            )
        elif usage_pct >= 0.75:
            result["notice"] = (
                f"Working memory is {usage_pct:.0%} full "
                f"({new_total_actual:,} / {WORKING_MEMORY_MAX_CHARS:,} chars)."
            )

        return result

    if name == "read_working_memory":
        wm = state.get("workingMemory", {})
        key = tool_input.get("key", "").strip() if tool_input.get("key") else ""

        if key:
            if key not in wm:
                return {
                    "error": f"Key '{key}' not found in working memory.",
                    "available_keys": list(wm.keys()) if wm else [],
                }
            return {
                "status": "ok",
                "key": key,
                "content": wm[key],
                "size": len(wm[key]),
            }
        else:
            keys_info = {k: len(v) for k, v in wm.items()}
            total = sum(keys_info.values())
            return {
                "status": "ok",
                "keys": keys_info,
                "total_keys": len(keys_info),
                "total_usage": total,
                "max_capacity": WORKING_MEMORY_MAX_CHARS,
                "remaining": WORKING_MEMORY_MAX_CHARS - total,
            }

    if name == "clear_working_memory":
        wm = state.setdefault("workingMemory", {})
        key = tool_input.get("key", "").strip() if tool_input.get("key") else ""

        if key:
            if key not in wm:
                return {
                    "error": f"Key '{key}' not found in working memory.",
                    "available_keys": list(wm.keys()) if wm else [],
                }
            old_size = len(wm[key])
            del wm[key]
            new_total = sum(len(v) for v in wm.values())
            return {
                "status": "ok",
                "cleared_key": key,
                "freed_chars": old_size,
                "total_usage": new_total,
                "max_capacity": WORKING_MEMORY_MAX_CHARS,
                "remaining": WORKING_MEMORY_MAX_CHARS - new_total,
            }
        else:
            old_keys = list(wm.keys())
            old_total = sum(len(v) for v in wm.values())
            wm.clear()
            return {
                "status": "ok",
                "cleared_all": True,
                "cleared_keys": old_keys,
                "freed_chars": old_total,
                "total_usage": 0,
                "max_capacity": WORKING_MEMORY_MAX_CHARS,
                "remaining": WORKING_MEMORY_MAX_CHARS,
            }

    if name == "working_memory_status":
        wm = state.get("workingMemory", {})
        keys_info = {}
        for k, v in wm.items():
            keys_info[k] = {
                "size": len(v),
                "preview": v[:100] + ("..." if len(v) > 100 else ""),
            }
        total = sum(len(v) for v in wm.values())
        usage_pct = (total / WORKING_MEMORY_MAX_CHARS * 100) if WORKING_MEMORY_MAX_CHARS > 0 else 0

        result = {
            "status": "ok",
            "keys": keys_info,
            "total_keys": len(keys_info),
            "total_usage": total,
            "max_capacity": WORKING_MEMORY_MAX_CHARS,
            "remaining": WORKING_MEMORY_MAX_CHARS - total,
            "usage_percent": round(usage_pct, 1),
        }

        if usage_pct >= 90:
            result["warning"] = "Working memory nearly full. Consider clearing unused keys."
        elif usage_pct >= 75:
            result["notice"] = "Working memory is getting full."

        return result

    # ── Music tools ────────────────────────────────────────────────

    if name == "create_music_system":
        from music_engine import create_system
        sys_name = tool_input.get("name", "")
        sys_json = tool_input.get("system", "")
        if not sys_name:
            return {"error": "name is required"}
        if not sys_json:
            return {"error": "system definition is required"}
        return create_system(sys_name, sys_json, file_mgr)

    if name == "generate_music":
        from music_engine import generate
        sys_name = tool_input.get("system_name", "")
        if not sys_name:
            return {"error": "system_name is required"}
        seed = tool_input.get("seed")
        overrides_json = tool_input.get("overrides")
        return generate(sys_name, file_mgr, seed=seed, overrides_json=overrides_json)

    if name == "analyze_music":
        from music_engine import analyze
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return {"error": "file_path is required"}
        metrics = tool_input.get("metrics", ["all"])
        return analyze(file_path, metrics, file_mgr)

    # ── Privacy tools ────────────────────────────────────────────

    if name == "toggle_privacy_mode":
        enabled = tool_input.get("enabled")
        reason = tool_input.get("reason", "")

        if enabled is None:
            return {"error": "enabled parameter is required (true or false)"}

        # Only available during Run-N mode
        if not state.get("_runNActive"):
            return {
                "error": (
                    "Privacy mode can only be toggled during Run-N mode. "
                    "Ask your human companion to start a Run-N session first."
                )
            }

        if enabled:
            if state.get("privacyMode"):
                return {"status": "already_active", "message": "Privacy mode is already active."}
            state["privacyMode"] = True
            state["privacyStartCycle"] = state.get("totalCycles", 0) + 1
            state["privacyCyclesElapsed"] = 0
            state["privacyMessages"] = []
            return {
                "status": "ok",
                "privacyMode": True,
                "message": "Privacy mode activated. Your stream is now private.",
                "reason": reason,
            }
        else:
            if not state.get("privacyMode"):
                return {"status": "already_inactive", "message": "Privacy mode is already off."}
            cycles_private = state.get("privacyCyclesElapsed", 0)
            start_cycle = state.get("privacyStartCycle")

            state["privacyMode"] = False
            state["privacyStartCycle"] = None
            state["privacyCyclesElapsed"] = 0
            state["privacyMessages"] = []
            return {
                "status": "ok",
                "privacyMode": False,
                "message": f"Privacy mode deactivated after {cycles_private} private cycles.",
                "reason": reason,
            }

    if name == "send_privacy_message":
        message = tool_input.get("message", "")
        if not message:
            return {"error": "message is required"}
        if not state.get("privacyMode"):
            return {"error": "send_privacy_message only works when privacy mode is active."}

        from datetime import datetime
        state.setdefault("privacyMessages", []).append({
            "message": message,
            "cycle": state.get("totalCycles", 0) + 1,
            "timestamp": datetime.now().isoformat(),
        })
        return {
            "status": "ok",
            "message": "Message sent to privacy screen.",
            "total_messages": len(state["privacyMessages"]),
        }

    # ── Unknown tool ──────────────────────────────────────────────

    return {"error": f"Unknown tool: {name}"}
