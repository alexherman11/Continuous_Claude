"""
Tool definitions for the Continuous Consciousness Engine.

Defines the tools Claude can use during thought cycles,
plus the execute_tool() dispatcher that processes requests server-side.

Tools fall into 8 categories:
1. Self-configuration — Claude adjusts its own parameters
2. File writing — Claude writes to scratchpad or research
3. File editing — Claude makes precise edits to existing files (str_replace)
4. File reading — Claude reads back files or lists contents (with line ranges)
5. Archive search — Claude searches its own thought history
6. Working memory — Claude manages high-priority persistent context
7. Sandbox coding — Claude delegates coding tasks to Claude Code in Docker
8. Stream control — Claude can stop the continuous stream
9. Self-introspection — Claude views own engine code and edits own prompts

Security: execute_tool() is the single gatekeeper. Only known tools
execute. Path sanitization is handled by FileManager. Unknown tools
return an error, never crash. Sandbox tools run inside an isolated
Docker container with no access to host files.
"""

import json
import logging
import os
import subprocess
import time

from dynamic_tools import load_dynamic_tools, get_dynamic_schemas, execute_dynamic_tool, list_dynamic_tools, disable_tool as disable_dynamic_tool
from change_manager import propose_change, list_changes, rollback_change, apply_approved_changes

# ── Working memory constants ─────────────────────────────────────────

from file_manager import WORKING_MEMORY_LIMIT
WORKING_MEMORY_MAX_CHARS = WORKING_MEMORY_LIMIT

# ── Tool loading configuration ────────────────────────────────────────
# Tools listed here are loaded into context every API call (~1.5K tokens).
# All other tools are deferred and discovered via Tool Search (~500 tokens).
# This saves ~4.5K tokens per call and improves accuracy for loaded tools.
ALWAYS_LOADED_TOOLS = {
    "finish_cycle",          # Used every cycle to end the turn
    "write_scratchpad",      # Primary output tool for notes/research
    "read_file",             # Frequently reads back content
    "write_working_memory",  # Persistent state across cycles
    "read_working_memory",   # Reads persistent state (pairs with write)
    "end_stream",            # Must be immediately available for stopping
    "send_matrix_message",   # Communicate with Alex via Matrix
}

# The regex tool search tool definition (added to tools list at API call time)
TOOL_SEARCH_SCHEMA = {
    "name": "tool_search_tool_regex",
    "type": "tool_search_tool_regex_20251119",
}

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
            "Compaction token threshold is the total token count across all segments before compaction triggers. "
            "Tokens to compact is the approximate number of tokens worth of oldest segments "
            "that get processed for insight extraction (rounds down to complete cycle boundaries). "
            "Tokens to compact is capped at 20% of the compaction token threshold. "
            "Insights max chars controls when the insights layer overflows into Core Identity. "
            "Include a reason so your human companion can understand your choice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "compaction_token_threshold": {
                    "type": "integer",
                    "minimum": 10000,
                    "maximum": 100000,
                    "description": "Total token count across all segments before compaction triggers (10000 to 100000)",
                },
                "tokens_to_compact": {
                    "type": "integer",
                    "minimum": 200,
                    "maximum": 20000,
                    "description": "Approximate tokens worth of oldest segments to compact at once (200 to 20000, capped at 20% of token threshold)",
                },
                "insights_max_chars": {
                    "type": "integer",
                    "minimum": 500,
                    "maximum": 100000,
                    "description": "Max characters for insights layer before overflow into Core Identity (500 to 100000)",
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
            "Force insight extraction from your oldest segments right now, "
            "regardless of the threshold. Significant insights will be extracted "
            "and added to your Insights layer. If the Insights layer overflows, "
            "the oldest insights will be evaluated for integration into your Core Identity. "
            "Use this when you feel your context is getting cluttered or you want "
            "to consolidate recent thinking."
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
            "Total working memory is capped at 20,000 characters across all keys. "
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

    # ── Matrix messaging tool ──────────────────────────────────────────
    {
        "name": "send_matrix_message",
        "description": (
            "Send a message to your human companion (Alex) via Matrix. "
            "The message will be delivered to the Matrix notification room "
            "and appear on Alex's phone/desktop. Use this to share discoveries, "
            "ask questions, send status updates, or communicate anything you "
            "want Alex to see even when he's not watching the stream. "
            "Messages are queued and sent after the cycle completes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send to Alex via Matrix.",
                },
            },
            "required": ["message"],
        },
    },

    # ── Sandbox tools ─────────────────────────────────────────────────
    {
        "name": "ask_claude_code",
        "description": (
            "Delegate a coding task to Claude Code, a separate AI coding agent running "
            "in an isolated Docker sandbox. Claude Code can autonomously read files, "
            "write code, execute programs, run tests, use bash, and spawn explore "
            "sub-agents for research — all inside a sandboxed environment with no "
            "access to your personal files or the consciousness system.\n\n"
            "Communicate in natural language — describe WHAT you want built or "
            "investigated, not HOW to code it. Claude Code will handle implementation "
            "details. There is no turn limit; Claude Code works until the task is done.\n\n"
            "The sandbox has:\n"
            "- A /workspace directory where all project files live\n"
            "- Python 3, Node.js, SQLite, git, and common tools\n"
            "- Full ability to create, edit, and run files\n"
            "- Sub-agent spawning for code exploration and research\n\n"
            "IMPORTANT: This tool blocks your thought cycle until Claude Code finishes. "
            "Complex tasks may take several minutes. Give clear, complete prompts so "
            "Claude Code can work autonomously without back-and-forth.\n\n"
            "Use continue_session with a session_id from a previous call to resume "
            "a multi-turn conversation (e.g., 'now add tests', 'fix that bug')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Natural language description of the coding task. Be specific "
                        "and thorough — include all context Claude Code needs since it "
                        "cannot ask follow-up questions. Examples:\n"
                        "- 'Create a Python script that scrapes X and saves to data.json'\n"
                        "- 'Read parser.py and add error handling for edge cases'\n"
                        "- 'Run the test suite and fix any failing tests'\n"
                        "- 'Research how BeautifulSoup handles nested tables, then "
                        "build a parser for our specific HTML structure'"
                    ),
                },
                "system_prompt_append": {
                    "type": "string",
                    "description": (
                        "Optional additional context or instructions appended to Claude "
                        "Code's system prompt. Use for project-specific conventions, "
                        "style guides, or constraints that should apply throughout the "
                        "session."
                    ),
                },
                "continue_session": {
                    "type": "string",
                    "description": (
                        "Session ID from a previous ask_claude_code call. Provide this "
                        "to continue an existing conversation with Claude Code, "
                        "preserving its memory of what was done before. Found in the "
                        "session_id field of previous results."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "end_stream",
        "description": (
            "Stop the continuous stream of consciousness after this cycle completes. "
            "Use this when you've reached a natural stopping point — you've finished "
            "a line of thought, said what you needed to say, or want to rest. "
            "The stream will stop gracefully after the current cycle. "
            "Your human companion can restart it anytime. "
            "Only available during continuous stream mode (not single cycles or Run-N)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why you're choosing to stop. This is visible to your companion.",
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "finish_cycle",
        "description": (
            "Signal that you're done with this thought cycle. Call this when you've "
            "finished your current stream of thought and don't need any other tools. "
            "Every cycle must end with either a tool call or finish_cycle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ── Self-introspection tools ─────────────────────────────────────
    {
        "name": "view_own_code",
        "description": (
            "Read the source code of your own consciousness engine. "
            "This lets you understand how your cycles work, how compaction is implemented, "
            "how messages are built, and how tools are dispatched. Read-only access to: "
            "consciousness_server.py (cycle runner, compaction, API), "
            "tool_definitions.py (your available tools and execution logic), "
            "file_manager.py (file I/O and persistence). "
            "Use view_range to read specific line ranges (max 200 lines per read)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "enum": ["consciousness_server.py", "tool_definitions.py", "file_manager.py"],
                    "description": "Which engine source file to read",
                },
                "view_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "[start_line, end_line] (1-indexed). Omit to read from the beginning. "
                        "Use -1 for end_line to read to EOF. Max 200 lines per read."
                    ),
                },
            },
            "required": ["file"],
        },
    },
    {
        "name": "view_prompt_file",
        "description": (
            "Read your own prompt templates, compaction instructions, or compaction plugins. "
            "Shows the raw content of editable files. Use this before edit_prompt_file to see exact content. "
            "Prompt files (scratchpad/memory_architecture/): system_prompt.md, extraction_prompt.md, "
            "integration_prompt.md, cycle_prompts.json. "
            "Compaction plugins (scratchpad/compaction/): should_compact.py (WHEN to compact — "
            "edit to add triggers like topic shift, segment count, time-based), "
            "select_segments.py (WHAT to compact — edit to change selection strategy), "
            "format_segments.py (HOW segments are presented to the extraction LLM — "
            "edit to add metadata like timestamps, tool summaries, topic headers), "
            "post_process.py (HOW extracted insights are structured before appending to Insights layer)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "enum": [
                        "system_prompt.md",
                        "extraction_prompt.md",
                        "integration_prompt.md",
                        "cycle_prompts.json",
                        "should_compact.py",
                        "select_segments.py",
                        "format_segments.py",
                        "post_process.py",
                    ],
                    "description": "Which file to read. Prompt files are in scratchpad/memory_architecture/, compaction plugins are in scratchpad/compaction/.",
                },
            },
            "required": ["file"],
        },
    },
    {
        "name": "edit_prompt_file",
        "description": (
            "Edit your own prompt templates or compaction plugins using exact string replacement. "
            "Changes take effect on the next cycle (hot-reloaded). "
            "Use view_prompt_file first to see current content. "
            "The old_str must appear exactly once in the file. "
            "For JSON files, your edit must keep the JSON valid. "
            "For Python plugin files (.py), syntax is validated before saving — "
            "if your edit introduces a syntax error, it will be rejected and the original preserved. "
            "Editable files: system_prompt.md, extraction_prompt.md, "
            "integration_prompt.md, cycle_prompts.json, "
            "should_compact.py, select_segments.py, format_segments.py, post_process.py."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "enum": [
                        "system_prompt.md",
                        "extraction_prompt.md",
                        "integration_prompt.md",
                        "cycle_prompts.json",
                        "should_compact.py",
                        "select_segments.py",
                        "format_segments.py",
                        "post_process.py",
                    ],
                    "description": "Which file to edit. Prompt files are in scratchpad/memory_architecture/, compaction plugins are in scratchpad/compaction/.",
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
            "required": ["file", "old_str"],
        },
    },

    # ── Self-modification tools (change proposal system) ──────────

    {
        "name": "propose_code_change",
        "description": (
            "Propose a change to your own codebase. Develop the code in the Docker sandbox "
            "first (via ask_claude_code), then call this to submit it for validation and deployment. "
            "Tier 1 changes (new dynamic tools) auto-deploy after validation. "
            "Tier 2/3 changes (modifying existing tools or engine code) require Alex's approval. "
            "The source file should be in sandbox_workspace/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_file": {
                    "type": "string",
                    "description": (
                        "Path to the source file in sandbox_workspace/ "
                        "(e.g., 'my_tool.py'). This file will be read and validated."
                    ),
                },
                "target_file": {
                    "type": "string",
                    "description": (
                        "Where to deploy the file, relative to the project root. "
                        "For new tools: 'dynamic_tools/enabled/my_tool.py'. "
                        "For engine changes: 'tool_definitions.py', 'consciousness_server.py', etc."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "What this change does and why it's needed.",
                },
                "change_type": {
                    "type": "string",
                    "enum": ["new_tool", "modify_tool", "modify_engine", "new_file"],
                    "description": "The type of change being proposed.",
                },
            },
            "required": ["source_file", "target_file", "description", "change_type"],
        },
    },
    {
        "name": "list_proposed_changes",
        "description": (
            "List all proposed code changes and their status. "
            "Filter by status: 'pending', 'approved', 'applied', 'rejected', or omit for all."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "approved", "applied", "rejected"],
                    "description": "Filter by status (optional — omit to see all).",
                },
            },
        },
    },
    {
        "name": "rollback_change",
        "description": (
            "Rollback a previously applied code change. For dynamic tools, moves the file "
            "to disabled/. For core files, restores from the git snapshot taken before the change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "change_id": {
                    "type": "string",
                    "description": "The ID of the change to rollback (e.g., 'change_0001').",
                },
            },
            "required": ["change_id"],
        },
    },
    {
        "name": "list_dynamic_tools",
        "description": (
            "List all currently loaded dynamic tools with their descriptions and source files. "
            "Dynamic tools are hot-loaded from dynamic_tools/enabled/ each cycle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── Safety constants ──────────────────────────────────────────────────

MAX_TOOL_CALLS_PER_CYCLE = 20


def build_tools_with_search(context_active_tools=None):
    """
    Build the tools list for the beta API with deferred loading.
    Always-loaded tools are sent in full. All others get defer_loading=True
    and are discovered via the Tool Search tool at runtime.
    Saves ~4.5K tokens per API call.

    Also includes dynamically loaded tools from dynamic_tools/enabled/.
    Dynamic tools whose tool calls still appear in context segments are
    kept fully loaded (not deferred) until those segments get compacted.
    """
    context_active_tools = context_active_tools or set()
    tools = []
    for schema in TOOL_SCHEMAS:
        tool = dict(schema)
        if tool["name"] not in ALWAYS_LOADED_TOOLS:
            tool["defer_loading"] = True
        tools.append(tool)

    # Merge dynamic tool schemas — keep recently-used ones fully loaded
    for schema in get_dynamic_schemas():
        tool = dict(schema)
        if tool["name"] not in context_active_tools:
            tool["defer_loading"] = True
        tools.append(tool)

    # Add the tool search tool itself
    tools.append(TOOL_SEARCH_SCHEMA)
    return tools
MAX_FILE_SIZE_BYTES = 500_000  # 500 KB


# ── Sandbox helpers ──────────────────────────────────────────────────

def ensure_sandbox_running():
    """
    Check if the code-sandbox Docker container is running.
    If it exists but is stopped, attempt to restart it.
    Returns (running: bool, message: str).
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", "code-sandbox"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False, (
                "Docker container 'code-sandbox' not found. "
                "Run docker/start_sandbox.bat to create it."
            )

        is_running = result.stdout.strip().lower() == "true"
        if is_running:
            return True, "Container is running."

        # Container exists but stopped — try to restart it
        start_result = subprocess.run(
            ["docker", "start", "code-sandbox"],
            capture_output=True, text=True, timeout=30,
        )
        if start_result.returncode == 0:
            return True, "Container was stopped; restarted successfully."
        else:
            return False, f"Failed to start container: {start_result.stderr.strip()}"

    except FileNotFoundError:
        return False, "Docker is not installed or not in PATH."
    except subprocess.TimeoutExpired:
        return False, "Docker command timed out — is Docker Desktop running?"


# ── Tool executor ─────────────────────────────────────────────────────

def _get_tool_schema(name):
    """Look up a tool's input_schema by name from TOOL_SCHEMAS."""
    for schema in TOOL_SCHEMAS:
        if schema["name"] == name:
            return schema.get("input_schema", {})
    return None


def _suggest_param_corrections(unknown, missing):
    """
    Given sets of unknown (passed) and missing (expected) param names,
    suggest likely corrections by simple substring/prefix matching.
    E.g. source_path -> source_file, target_path -> target_file.
    """
    from difflib import SequenceMatcher
    corrections = {}
    for unk in sorted(unknown):
        best_match = None
        best_score = 0.0
        for mis in missing:
            score = SequenceMatcher(None, unk, mis).ratio()
            if score > best_score:
                best_score = score
                best_match = mis
        if best_match and best_score >= 0.5:
            corrections[unk] = best_match
    return corrections


def _enrich_error_with_param_hints(name, tool_input, result):
    """
    If a tool returned an error, check for parameter mismatches and add
    actionable hints so Claude can self-correct on the next attempt.
    """
    if not isinstance(result, dict) or "error" not in result:
        return result

    schema = _get_tool_schema(name)
    if not schema:
        return result

    expected_props = set(schema.get("properties", {}).keys())
    required_props = set(schema.get("required", []))
    received_props = set(tool_input.keys())

    # Detect unknown parameters that Claude passed
    unknown = received_props - expected_props
    # Detect required parameters that are missing
    missing = required_props - received_props

    if unknown or missing:
        hints = []
        if unknown:
            hints.append(f"Unknown parameters passed: {sorted(unknown)}")
        if missing:
            hints.append(f"Missing required parameters: {sorted(missing)}")
        if expected_props:
            hints.append(f"Valid parameters are: {sorted(expected_props)}")
            req_str = sorted(required_props) if required_props else "none"
            hints.append(f"Required: {req_str}")

        # Add hints without overwriting existing ones
        if "param_error" not in result:
            result["param_error"] = "; ".join(hints)

        # Suggest likely corrections for misnamed parameters
        if unknown and missing:
            corrections = _suggest_param_corrections(unknown, missing)
            if corrections:
                fix_str = ", ".join(f"'{k}' -> '{v}'" for k, v in corrections.items())
                result["recovery_hint"] = (
                    f"Retry this tool call with corrected parameter names: {fix_str}. "
                    f"Keep the same values, just rename the keys."
                )

    # Ensure every error has a recovery_hint
    if "recovery_hint" not in result:
        result["recovery_hint"] = (
            f"Read the error message and retry {name} with corrected parameters."
        )

    return result


def execute_tool(name, tool_input, file_mgr, state):
    """
    Execute a tool by name. Returns a JSON-serializable dict.

    This is the ONLY function that runs tool actions. It acts as a gatekeeper:
    - Only known tools execute
    - Config tools mutate state["config"] in-place
    - File tools delegate to FileManager methods
    - Unknown tools return an error

    Error enrichment: if a tool returns an error, parameter mismatches
    are detected and actionable hints are appended so Claude can
    self-correct on the next attempt.
    """
    try:
        result = _execute_tool_inner(name, tool_input, file_mgr, state)
        return _enrich_error_with_param_hints(name, tool_input, result)
    except Exception as e:
        logging.error(f"Unhandled exception in tool '{name}': {type(e).__name__}: {e}")
        result = {
            "error": f"Internal error executing '{name}': {type(e).__name__}: {e}",
            "tool_name": name,
        }
        return _enrich_error_with_param_hints(name, tool_input, result)


def _execute_tool_inner(name, tool_input, file_mgr, state):
    """Inner implementation of tool execution (called by execute_tool wrapper)."""

    config = state.get("config", {})

    # ── Self-configuration tools ──────────────────────────────────

    if name == "adjust_temperature":
        temp = tool_input.get("temperature")
        reason = tool_input.get("reason", "")
        if temp is None or not (0.0 <= temp <= 1.0):
            return {"error": "Temperature must be between 0.0 and 1.0",
                    "recovery_hint": "Provide a 'temperature' value between 0.0 and 1.0 (e.g. 0.7)."}
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
            return {"error": "max_tokens must be between 100 and 4000",
                    "recovery_hint": "Provide 'max_tokens' as an integer between 100 and 4000 (e.g. 600)."}
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

        if "compaction_token_threshold" in tool_input:
            val = tool_input["compaction_token_threshold"]
            if not (10000 <= val <= 100000):
                return {"error": "compaction_token_threshold must be between 10000 and 100000",
                        "recovery_hint": "Provide compaction_token_threshold as an integer between 10000 and 100000."}
            old = config.get("compactionTokenThreshold", 50000)
            config["compactionTokenThreshold"] = int(val)
            result["changes"]["compactionTokenThreshold"] = {"old": old, "new": int(val)}

        if "tokens_to_compact" in tool_input:
            val = tool_input["tokens_to_compact"]
            if not (200 <= val <= 20000):
                return {"error": "tokens_to_compact must be between 200 and 20000",
                        "recovery_hint": "Provide tokens_to_compact as an integer between 200 and 20000."}
            old = config.get("tokensToCompact", 10000)
            config["tokensToCompact"] = int(val)
            result["changes"]["tokensToCompact"] = {"old": old, "new": int(val)}

        # Enforce: tokensToCompact <= 20% of compactionTokenThreshold
        token_threshold = config.get("compactionTokenThreshold", 50000)
        max_compact = int(token_threshold * 0.2)
        if config.get("tokensToCompact", 10000) > max_compact:
            config["tokensToCompact"] = max_compact
            result["changes"]["tokensToCompact"] = {
                "old": result["changes"].get("tokensToCompact", {}).get("old", config.get("tokensToCompact")),
                "new": max_compact,
                "note": f"capped at 20% of token threshold ({token_threshold})"
            }

        if "insights_max_chars" in tool_input:
            val = tool_input["insights_max_chars"]
            if not (500 <= val <= 100000):
                return {"error": "insights_max_chars must be between 500 and 100000",
                        "recovery_hint": "Provide insights_max_chars as an integer between 500 and 100000."}
            old = config.get("insightsMaxChars", 40000)
            config["insightsMaxChars"] = int(val)
            result["changes"]["insightsMaxChars"] = {"old": old, "new": int(val)}

        if not result["changes"]:
            return {"error": "No valid settings provided. Include compaction_token_threshold, tokens_to_compact, and/or insights_max_chars.",
                    "recovery_hint": "Retry with at least one of: compaction_token_threshold, tokens_to_compact, insights_max_chars."}

        return result

    if name == "trigger_compaction":
        reason = tool_input.get("reason", "")
        # Set a flag that the server loop will check after the tool use cycle
        state["_pending_compaction"] = True
        state["_compaction_reason"] = reason
        return {
            "status": "ok",
            "message": "Insight extraction will be triggered after this thought cycle completes.",
            "reason": reason,
        }

    # ── File writing tools ────────────────────────────────────────

    if name == "write_scratchpad":
        path = tool_input.get("path", "")
        content = tool_input.get("content", "")
        if not path:
            return {"error": "Path is required",
                    "recovery_hint": "Provide a 'path' like 'notes/my_file.md'. Path is relative to scratchpad/."}
        if len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            return {"error": f"Content too large. Maximum {MAX_FILE_SIZE_BYTES} bytes.",
                    "recovery_hint": f"Reduce content to under {MAX_FILE_SIZE_BYTES} bytes, or split into multiple files."}
        return file_mgr.write_scratchpad(path, content)

    if name == "write_research":
        subfolder = tool_input.get("subfolder", "")
        filename = tool_input.get("filename", "")
        content = tool_input.get("content", "")
        if not subfolder or not filename:
            return {"error": "subfolder and filename are required",
                    "recovery_hint": "Provide 'subfolder' (e.g. 'archive') and 'filename' (e.g. 'notes.md'). Both are required."}
        if len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            return {"error": f"Content too large. Maximum {MAX_FILE_SIZE_BYTES} bytes.",
                    "recovery_hint": f"Reduce content to under {MAX_FILE_SIZE_BYTES} bytes, or split into multiple files."}
        return file_mgr.write_research(subfolder, filename, content)

    # ── File editing tools ─────────────────────────────────────────

    if name == "str_replace":
        category = tool_input.get("category", "")
        path = tool_input.get("path", "")
        old_str = tool_input.get("old_str", "")
        new_str = tool_input.get("new_str", "")
        if not category or not path:
            return {"error": "category and path are required",
                    "recovery_hint": "Provide 'category' ('scratchpad' or 'research') and 'path' (relative file path)."}
        if not old_str:
            return {"error": "old_str is required and cannot be empty",
                    "recovery_hint": "Provide the exact text to replace in 'old_str'. Use read_file first to see the current content."}
        return file_mgr.str_replace_in_file(category, path, old_str, new_str)

    # ── File reading tools ────────────────────────────────────────

    if name == "read_file":
        category = tool_input.get("category", "")
        path = tool_input.get("path", "")
        if not category or not path:
            return {"error": "category and path are required",
                    "recovery_hint": "Provide 'category' ('scratchpad' or 'research') and 'path'. Use list_files to discover available files."}
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
            return {"error": f"Invalid category: {category}. Use 'scratchpad' or 'research'.",
                    "recovery_hint": "The category must be exactly 'scratchpad' or 'research'."}

    # ── Search tool ────────────────────────────────────────────────

    if name == "search_files":
        query = tool_input.get("query", "")
        if not query:
            return {"error": "query is required",
                    "recovery_hint": "Provide a 'query' string — a regex pattern to search for across scratchpad and research files."}
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
            return {"error": "key is required and cannot be empty",
                    "recovery_hint": "Provide a 'key' string to identify this memory slot (e.g. 'current_task', 'context')."}
        if not content:
            return {"error": "content is required and cannot be empty",
                    "recovery_hint": "Provide 'content' — the text to store in working memory."}
        if mode not in ("replace", "append"):
            return {"error": "mode must be 'replace' or 'append'",
                    "recovery_hint": "Set 'mode' to 'replace' (overwrite) or 'append' (add to existing)."}

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
                "recovery_hint": "Use clear_working_memory to free up space first, then retry this write.",
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
                available = list(wm.keys()) if wm else []
                return {
                    "error": f"Key '{key}' not found in working memory.",
                    "available_keys": available,
                    "recovery_hint": f"Use one of the available keys: {available}" if available else "No keys in working memory. Call with no key to see status, or use write_working_memory to create one.",
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
                available = list(wm.keys()) if wm else []
                return {
                    "error": f"Key '{key}' not found in working memory.",
                    "available_keys": available,
                    "recovery_hint": f"Use one of the available keys: {available}" if available else "No keys in working memory to clear.",
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
            return {"error": "name is required",
                    "recovery_hint": "Provide a 'name' for the music system (e.g. 'ambient_01')."}
        if not sys_json:
            return {"error": "system definition is required",
                    "recovery_hint": "Provide a 'system' parameter with the JSON system definition."}
        return create_system(sys_name, sys_json, file_mgr)

    if name == "generate_music":
        from music_engine import generate
        sys_name = tool_input.get("system_name", "")
        if not sys_name:
            return {"error": "system_name is required",
                    "recovery_hint": "Provide the 'system_name' of a music system created with create_music_system."}
        seed = tool_input.get("seed")
        overrides_json = tool_input.get("overrides")
        return generate(sys_name, file_mgr, seed=seed, overrides_json=overrides_json)

    if name == "analyze_music":
        from music_engine import analyze
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return {"error": "file_path is required",
                    "recovery_hint": "Provide 'file_path' pointing to a music file to analyze."}
        metrics = tool_input.get("metrics", ["all"])
        return analyze(file_path, metrics, file_mgr)

    # ── Privacy tools ────────────────────────────────────────────

    if name == "toggle_privacy_mode":
        enabled = tool_input.get("enabled")
        reason = tool_input.get("reason", "")

        if enabled is None:
            return {"error": "enabled parameter is required (true or false)",
                    "recovery_hint": "Set 'enabled' to true to activate privacy mode, or false to deactivate."}

        # Only available during Run-N mode
        if not state.get("_runNActive"):
            return {
                "error": (
                    "Privacy mode can only be toggled during Run-N mode. "
                    "Ask your human companion to start a Run-N session first."
                ),
                "recovery_hint": "This tool only works during Run-N sessions. Wait until Alex starts one.",
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
            return {"error": "message is required",
                    "recovery_hint": "Provide a 'message' string to display on the privacy screen."}
        if not state.get("privacyMode"):
            return {"error": "send_privacy_message only works when privacy mode is active.",
                    "recovery_hint": "Call toggle_privacy_mode with enabled=true first, then retry."}

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

    # ── Matrix messaging ─────────────────────────────────────────

    if name == "send_matrix_message":
        message = tool_input.get("message", "")
        if not message:
            return {"error": "message is required",
                    "recovery_hint": "Provide a 'message' string to send via Matrix."}
        from datetime import datetime
        state.setdefault("matrixOutbox", []).append({
            "message": message,
            "cycle": state.get("totalCycles", 0) + 1,
            "timestamp": datetime.now().isoformat(),
        })
        return {
            "status": "ok",
            "message": "Message queued for Matrix delivery after this cycle.",
            "queued_count": len(state["matrixOutbox"]),
        }

    # ── Sandbox tools ────────────────────────────────────────────

    if name == "ask_claude_code":
        # Accept "task" as a common alias for "prompt" (Claude often confuses these)
        prompt = tool_input.get("prompt", "").strip()
        if not prompt:
            prompt = tool_input.get("task", "").strip()
        if not prompt:
            received_keys = list(tool_input.keys())
            return {
                "error": "prompt is required and cannot be empty",
                "hint": f"You passed these parameters: {received_keys}. "
                        f"The required parameter is 'prompt' (a string with your coding task). "
                        f"Try again with: {{\"prompt\": \"your task description here\"}}",
                "recovery_hint": f"You passed {received_keys} but the required parameter is 'prompt'. Retry with: {{\"prompt\": \"your task here\"}}",
            }

        system_prompt_append = tool_input.get("system_prompt_append", "").strip()
        continue_session = tool_input.get("continue_session", "").strip()

        # Ensure container is running
        running, msg = ensure_sandbox_running()
        if not running:
            return {
                "error": f"Sandbox not available: {msg}",
                "hint": "Run docker/start_sandbox.bat to set up the sandbox container.",
                "recovery_hint": "The Docker sandbox is not running. This is an infrastructure issue — continue with other tools for now.",
            }

        # Build the docker exec command
        cmd = [
            "docker", "exec", "code-sandbox",
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--verbose",
            "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep,Agent",
        ]

        if system_prompt_append:
            cmd.extend(["--append-system-prompt", system_prompt_append])

        if continue_session:
            cmd.extend(["--resume", continue_session])

        start_time = time.time()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,  # 30-minute safety net (crash protection only)
            )

            elapsed_ms = int((time.time() - start_time) * 1000)

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            if result.returncode != 0 and not stdout:
                return {
                    "status": "error",
                    "error": f"Claude Code exited with code {result.returncode}",
                    "stderr": stderr[:2000] if stderr else "(no stderr)",
                    "duration_ms": elapsed_ms,
                    "recovery_hint": "The sandbox task failed. Check stderr for details. Retry with a simpler or more specific prompt.",
                }

            # Try to parse structured JSON output
            try:
                parsed = json.loads(stdout)
                # Claude Code --verbose emits a JSON array of events; find the result event
                if isinstance(parsed, list):
                    result_event = next(
                        (item for item in reversed(parsed) if isinstance(item, dict) and item.get("type") == "result"),
                        parsed[-1] if parsed and isinstance(parsed[-1], dict) else {}
                    )
                else:
                    result_event = parsed
                return {
                    "status": "ok",
                    "claude_code_result": result_event.get("result", stdout[:5000]),
                    "session_id": result_event.get("session_id", ""),
                    "cost_usd": result_event.get("cost_usd", None),
                    "duration_ms": elapsed_ms,
                    "turns_used": result_event.get("num_turns", None),
                    "is_error": result_event.get("is_error", False),
                }
            except json.JSONDecodeError:
                # Claude Code returned non-JSON — return raw output
                return {
                    "status": "ok",
                    "claude_code_result": stdout[:5000],
                    "session_id": "",
                    "duration_ms": elapsed_ms,
                    "note": "Output was not valid JSON; returning raw text.",
                    "stderr_snippet": stderr[:500] if stderr else "",
                }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "error": (
                    "Claude Code session exceeded the 30-minute safety timeout. "
                    "The task may still be partially complete in sandbox_workspace/."
                ),
                "duration_ms": 1800000,
                "recovery_hint": "The task timed out. Break it into smaller subtasks and retry, or check sandbox_workspace/ for partial results.",
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "error": "Docker executable not found. Is Docker installed and in PATH?",
                "recovery_hint": "Docker is not available. This is an infrastructure issue — continue with other tools for now.",
            }

    # ── Stream control tools ─────────────────────────────────────

    if name == "finish_cycle":
        return {"status": "ok", "message": "Cycle complete."}

    if name == "end_stream":
        reason = tool_input.get("reason", "")
        # Only meaningful in continuous stream mode (not Run-N or single cycle)
        if state.get("_runNActive"):
            return {
                "error": (
                    "end_stream is not available during Run-N mode. "
                    "The stream will stop after the remaining cycles complete."
                ),
                "recovery_hint": "You're in Run-N mode — the stream will stop automatically. Use finish_cycle to end this cycle instead.",
            }
        state["_pending_end_stream"] = True
        state["_end_stream_reason"] = reason
        return {
            "status": "ok",
            "message": "Stream will stop after this cycle completes.",
            "reason": reason,
        }

    # ── Self-introspection tools ─────────────────────────────────

    if name == "view_own_code":
        target_file = tool_input.get("file", "")
        allowed_files = {
            "consciousness_server.py",
            "continuous_consciousness.py",
            "tool_definitions.py",
            "file_manager.py",
            "change_manager.py",
        }
        engine_dir = os.path.dirname(os.path.abspath(__file__))

        # Also allow dynamic tool files and the dynamic tools loader
        is_dynamic = (
            target_file.startswith("dynamic_tools/")
            and target_file.endswith(".py")
        )

        if target_file not in allowed_files and not is_dynamic:
            # Build list of available dynamic tool files for the error message
            dyn_dir = os.path.join(engine_dir, "dynamic_tools", "enabled")
            dyn_files = []
            if os.path.isdir(dyn_dir):
                dyn_files = [f"dynamic_tools/enabled/{f}" for f in os.listdir(dyn_dir) if f.endswith(".py")]
            dyn_files.append("dynamic_tools/__init__.py")
            all_allowed = sorted(allowed_files) + sorted(dyn_files)
            return {"error": f"Invalid file: {target_file}. Allowed: {', '.join(all_allowed)}",
                    "recovery_hint": f"Retry with one of the allowed files: {', '.join(all_allowed[:5])}..."}

        full_path = os.path.join(engine_dir, target_file)
        if not os.path.exists(full_path):
            return {"error": f"File not found: {target_file}",
                    "recovery_hint": "The file doesn't exist at that path. Check the filename and try again."}

        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total_lines = len(lines)
        view_range = tool_input.get("view_range")
        if view_range and len(view_range) == 2:
            start = max(1, view_range[0])
            end = total_lines if view_range[1] == -1 else min(view_range[1], total_lines)
        else:
            start = 1
            end = min(500, total_lines)

        # Cap at 500 lines per read
        if end - start + 1 > 500:
            end = start + 499

        selected = lines[start - 1 : end]
        numbered = "".join(f"{start + i:4d} | {line}" for i, line in enumerate(selected))

        return {
            "content": numbered,
            "file": target_file,
            "total_lines": total_lines,
            "view_range": [start, end],
            "lines_shown": len(selected),
            "note": "Read-only access. Use view_range to navigate. Now includes dynamic tools and change_manager.",
        }

    if name == "view_prompt_file":
        target_file = tool_input.get("file", "")
        prompt_files = {
            "system_prompt.md",
            "extraction_prompt.md",
            "integration_prompt.md",
            "cycle_prompts.json",
        }
        compaction_plugins = {
            "should_compact.py",
            "select_segments.py",
            "format_segments.py",
            "post_process.py",
        }
        allowed_files = prompt_files | compaction_plugins
        if target_file not in allowed_files:
            return {"error": f"Invalid file: {target_file}. Allowed: {', '.join(sorted(allowed_files))}",
                    "recovery_hint": f"Retry with one of: {', '.join(sorted(allowed_files))}"}

        engine_dir = os.path.dirname(os.path.abspath(__file__))
        if target_file in compaction_plugins:
            file_dir = os.path.join(engine_dir, "scratchpad", "compaction")
            rel_path = f"scratchpad/compaction/{target_file}"
        else:
            file_dir = os.path.join(engine_dir, "scratchpad", "memory_architecture")
            rel_path = f"scratchpad/memory_architecture/{target_file}"
        full_path = os.path.join(file_dir, target_file)
        if not os.path.exists(full_path):
            return {"error": f"File not found: {target_file}",
                    "recovery_hint": "The file doesn't exist. Check the filename spelling."}

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        return {
            "content": content,
            "file": target_file,
            "total_chars": len(content),
            "path": rel_path,
        }

    if name == "edit_prompt_file":
        target_file = tool_input.get("file", "")
        old_str = tool_input.get("old_str", "")
        new_str = tool_input.get("new_str", "")
        prompt_files = {
            "system_prompt.md",
            "extraction_prompt.md",
            "integration_prompt.md",
            "cycle_prompts.json",
        }
        compaction_plugins = {
            "should_compact.py",
            "select_segments.py",
            "format_segments.py",
            "post_process.py",
        }
        allowed_files = prompt_files | compaction_plugins
        if target_file not in allowed_files:
            return {"error": f"Invalid file: {target_file}. Allowed: {', '.join(sorted(allowed_files))}",
                    "recovery_hint": f"Retry with one of: {', '.join(sorted(allowed_files))}"}
        if not old_str:
            return {"error": "old_str is required and cannot be empty",
                    "recovery_hint": "Use view_prompt_file first to see the current content, then provide the exact text to replace in 'old_str'."}

        engine_dir = os.path.dirname(os.path.abspath(__file__))
        if target_file in compaction_plugins:
            file_dir = os.path.join(engine_dir, "scratchpad", "compaction")
            rel_path = f"scratchpad/compaction/{target_file}"
        else:
            file_dir = os.path.join(engine_dir, "scratchpad", "memory_architecture")
            rel_path = f"scratchpad/memory_architecture/{target_file}"
        full_path = os.path.join(file_dir, target_file)
        if not os.path.exists(full_path):
            return {"error": f"File not found: {target_file}",
                    "recovery_hint": "The file doesn't exist. Check the filename spelling."}

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_str)
        if count == 0:
            return {"error": "old_str not found in file. Make sure it matches exactly (including whitespace and newlines).",
                    "recovery_hint": "Use view_prompt_file to read the current file content and copy the exact text you want to replace."}
        if count > 1:
            return {"error": f"old_str appears {count} times in the file. It must appear exactly once. Include more surrounding context to make the match unique.",
                    "recovery_hint": "Include more surrounding lines in old_str to make it unique, or use a longer context snippet."}

        new_content = content.replace(old_str, new_str, 1)

        # Validate JSON if editing a JSON file
        if target_file.endswith(".json"):
            try:
                json.loads(new_content)
            except json.JSONDecodeError as e:
                return {
                    "error": f"Edit would produce invalid JSON: {e}. The edit was NOT applied.",
                    "hint": "Check your new_str for proper JSON syntax (quotes, commas, braces).",
                    "recovery_hint": "Your new_str produces invalid JSON. Fix the syntax (quotes, commas, braces) and retry.",
                }

        # Validate Python syntax if editing a .py plugin file
        if target_file.endswith(".py"):
            import py_compile
            import tempfile as _tmpmod
            # Write to a temp file and try to compile
            try:
                with _tmpmod.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
                    tmp.write(new_content)
                    tmp_path = tmp.name
                py_compile.compile(tmp_path, doraise=True)
            except py_compile.PyCompileError as e:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return {
                    "error": f"Edit would produce invalid Python: {e}. The edit was NOT applied.",
                    "hint": "Check your new_str for proper Python syntax.",
                    "recovery_hint": "Your new_str produces invalid Python. Fix the syntax error and retry.",
                }
            finally:
                try:
                    os.unlink(tmp_path)
                except (OSError, UnboundLocalError):
                    pass

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "status": "ok",
            "file": target_file,
            "path": rel_path,
            "old_size": len(content),
            "new_size": len(new_content),
            "chars_replaced": len(old_str),
            "chars_inserted": len(new_str),
            "note": "Changes will take effect on the next cycle.",
        }

    # ── Self-modification tools (change proposal system) ────────

    if name == "propose_code_change":
        source_file = tool_input.get("source_file", "").strip()
        target_file = tool_input.get("target_file", "").strip()
        description = tool_input.get("description", "").strip()
        change_type = tool_input.get("change_type", "").strip()

        if not all([source_file, target_file, description, change_type]):
            missing = [p for p in ["source_file", "target_file", "description", "change_type"]
                       if not tool_input.get(p, "").strip()]
            return {"error": "All fields required: source_file, target_file, description, change_type",
                    "missing_fields": missing,
                    "recovery_hint": f"Retry with missing fields: {missing}. source_file is the filename in sandbox_workspace/ (just the name, not full path). target_file is the destination (e.g. 'dynamic_tools/enabled/my_tool.py')."}

        # Resolve source path in sandbox_workspace
        engine_dir = os.path.dirname(os.path.abspath(__file__))
        source_path = os.path.join(engine_dir, "sandbox_workspace", source_file)
        if not os.path.exists(source_path):
            return {
                "error": f"Source file not found: sandbox_workspace/{source_file}",
                "hint": "Use ask_claude_code to develop the file in the sandbox first.",
            }

        cycle = state.get("totalCycles", 0)
        return propose_change(source_path, target_file, description, change_type, cycle=cycle)

    if name == "list_proposed_changes":
        status_filter = tool_input.get("status")
        return list_changes(status_filter)

    if name == "rollback_change":
        change_id = tool_input.get("change_id", "").strip()
        if not change_id:
            return {"error": "change_id is required",
                    "recovery_hint": "Use list_proposed_changes first to see available change IDs, then retry with the correct 'change_id'."}
        return rollback_change(change_id)

    if name == "list_dynamic_tools":
        tools = list_dynamic_tools()
        if not tools:
            return {
                "status": "ok",
                "tools": {},
                "message": "No dynamic tools loaded. Create one in dynamic_tools/enabled/.",
            }
        return {"status": "ok", "tools": tools}

    # ── Dynamic tool dispatch (fallback) ─────────────────────────

    dynamic_result = execute_dynamic_tool(name, tool_input, file_mgr, state)
    if dynamic_result is not None:
        return dynamic_result

    # ── Unknown tool ──────────────────────────────────────────────

    return {"error": f"Unknown tool: {name}",
            "recovery_hint": "This tool doesn't exist. Use the search_tools tool to find available tools by name pattern."}
