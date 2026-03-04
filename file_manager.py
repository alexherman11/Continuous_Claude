"""
File persistence layer for the Continuous Consciousness Engine.

Handles:
- State load/save (consciousness_data/state.json)
- Memory core file (consciousness_data/memory_core.md)
- Individual segment archival (consciousness_data/segments/)
- Scratchpad and research file access
- Path sanitization for security
"""

import os
import json
from datetime import datetime


DEFAULT_CONFIG = {
    "maxTokens": 600,
    "pauseBetweenCycles": 3000,
    "compactionThreshold": 200,
    "tokensToCompact": 30000,
    "insightsMaxChars": 40000,
    "insightsOverflowPercent": 0.3,
    "coreMaxChars": 16000,
    "temperature": 0.9,
    "compactionTokenThreshold": 150000,
}

WORKING_MEMORY_LIMIT = 120000

DEFAULT_STATE = {
    "memoryCore": "",           # Core Identity (stable, changes slowly)
    "insights": "",             # Insights layer (tagged recent insights)
    "segments": [],
    "totalCycles": 0,
    "totalCompactions": 0,
    "config": dict(DEFAULT_CONFIG),
    "createdAt": None,
    "lastActive": None,
    "imports": [],
    "workingMemory": {},
    # Privacy mode fields
    "privacyMode": False,
    "privacyStartCycle": None,
    "privacyCyclesElapsed": 0,
    "privacyMessages": [],      # Messages Claude sends OUT through the privacy screen
    "queuedMessages": [],       # Messages human queues for Claude's next cycle
}


class FileManager:
    def __init__(self, base_path):
        self.base_path = os.path.abspath(base_path)
        self.data_dir = os.path.join(self.base_path, "consciousness_data")
        self.segments_dir = os.path.join(self.data_dir, "segments")
        self.imports_dir = os.path.join(self.data_dir, "imports")
        self.scratchpad_dir = os.path.join(self.base_path, "scratchpad")
        self.research_dir = os.path.join(self.base_path, "research")

        # Ensure directories exist
        for d in [self.data_dir, self.segments_dir, self.imports_dir, self.scratchpad_dir]:
            os.makedirs(d, exist_ok=True)

    # ── State persistence ─────────────────────────────────────────────

    def _seed_core_identity(self):
        """Read core_identity.md from scratchpad to seed the Core Identity."""
        core_path = os.path.join(self.scratchpad_dir, "core_identity.md")
        if os.path.exists(core_path):
            with open(core_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def load_state(self):
        """Load engine state from state.json. Returns default state if not found."""
        path = os.path.join(self.data_dir, "state.json")
        if not os.path.exists(path):
            now = datetime.now().isoformat()
            state = dict(DEFAULT_STATE)
            state["config"] = dict(DEFAULT_CONFIG)
            state["createdAt"] = now
            state["lastActive"] = now
            # Seed Core Identity from scratchpad/core_identity.md
            state["memoryCore"] = self._seed_core_identity()
            return state
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        # Backfill workingMemory for states created before this feature
        if "workingMemory" not in state:
            state["workingMemory"] = {}
        # Backfill privacy fields for states created before this feature
        if "privacyMode" not in state:
            state["privacyMode"] = False
            state["privacyStartCycle"] = None
            state["privacyCyclesElapsed"] = 0
            state["privacyMessages"] = []
            state["queuedMessages"] = []
        # Backfill insights layer for states created before this feature
        if "insights" not in state:
            state["insights"] = ""
        # Migrate segmentsToCompact -> tokensToCompact
        config = state.get("config", {})
        if "segmentsToCompact" in config:
            if "tokensToCompact" not in config:
                config["tokensToCompact"] = DEFAULT_CONFIG["tokensToCompact"]
            del config["segmentsToCompact"]
        # Backfill new config keys
        for key in ("tokensToCompact", "insightsMaxChars", "insightsOverflowPercent", "coreMaxChars", "compactionTokenThreshold"):
            if key not in config:
                config[key] = DEFAULT_CONFIG[key]
        # Seed Core Identity if empty (migration from old state)
        if not state.get("memoryCore"):
            state["memoryCore"] = self._seed_core_identity()
        return state

    def save_state(self, state):
        """Save engine state to state.json and memory_core.md."""
        state["lastActive"] = datetime.now().isoformat()
        if not state.get("createdAt"):
            state["createdAt"] = state["lastActive"]

        # Write state.json
        state_path = os.path.join(self.data_dir, "state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

        # Write human-readable memory core (now Core Identity)
        core_path = os.path.join(self.data_dir, "memory_core.md")
        with open(core_path, "w", encoding="utf-8") as f:
            f.write(state.get("memoryCore", ""))

        # Write human-readable insights layer
        insights_path = os.path.join(self.data_dir, "insights.md")
        with open(insights_path, "w", encoding="utf-8") as f:
            f.write(state.get("insights", ""))

        # Write config separately
        config_path = os.path.join(self.data_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(state.get("config", DEFAULT_CONFIG), f, indent=2)

    def save_segment(self, segment):
        """Save an individual segment to the segments archive."""
        cycle = segment.get("cycle", 0)
        filename = f"cycle_{cycle:04d}.json"
        path = os.path.join(self.segments_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(segment, f, indent=2, ensure_ascii=False)

    # ── Thought Archive ────────────────────────────────────────────────

    def append_to_archive(self, segment):
        """
        Append a human-readable markdown entry for this segment
        to the appropriate daily archive file in research/archive/.
        """
        timestamp_str = segment.get("timestamp", "")
        cycle = segment.get("cycle", 0)

        # Determine which file to write to based on timestamp
        if timestamp_str and timestamp_str != "imported":
            try:
                dt = datetime.fromisoformat(timestamp_str)
                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                date_str = "_imported"
                time_str = "unknown"
        else:
            date_str = "_imported"
            time_str = "imported"

        filename = f"{date_str}.md"
        archive_dir = os.path.join(self.research_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)
        filepath = os.path.join(archive_dir, filename)

        # Build the markdown entry
        entry_parts = []
        entry_parts.append("---\n\n")
        entry_parts.append(f"## Cycle {cycle} | {date_str} {time_str}\n\n")

        # User message if present
        user_msg = segment.get("userMessage")
        if user_msg:
            entry_parts.append(f"**Human:** {user_msg}\n\n")

        # Claude's response
        content = segment.get("content", "")
        entry_parts.append(f"**Claude:**\n{content}\n\n")

        # Tool summary
        tool_actions = segment.get("toolActions")
        if tool_actions:
            tool_names = [a.get("tool", "unknown") for a in tool_actions]
            tool_summary = ", ".join(tool_names)
            entry_parts.append(f"**Tools used:** {tool_summary}\n")

        entry_parts.append("\n")
        entry_text = "".join(entry_parts)

        # Append to file (create with header if new)
        is_new = not os.path.exists(filepath)
        with open(filepath, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# Thought Archive — {date_str}\n\n")
            f.write(entry_text)

        return {"status": "ok", "file": f"research/archive/{filename}", "cycle": cycle}

    def save_private_segment(self, segment):
        """
        Save a segment to the private journey folder instead of the public archive.
        Each cycle gets its own timestamped markdown file in scratchpad/private_journey/.
        """
        timestamp_str = segment.get("timestamp", "")
        cycle = segment.get("cycle", 0)

        private_dir = os.path.join(self.scratchpad_dir, "private_journey")
        os.makedirs(private_dir, exist_ok=True)

        # Build timestamp-based filename for chronological ordering
        if timestamp_str and timestamp_str != "imported":
            try:
                dt = datetime.fromisoformat(timestamp_str)
                file_ts = dt.strftime("%Y%m%d_%H%M%S")
                time_str = dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                file_ts = f"cycle_{cycle:04d}"
                time_str = "unknown"
        else:
            file_ts = f"cycle_{cycle:04d}"
            time_str = "unknown"

        filename = f"{file_ts}_cycle_{cycle:04d}.md"
        filepath = os.path.join(private_dir, filename)

        # Build markdown entry
        entry_parts = []
        entry_parts.append(f"# Private Cycle {cycle} | {time_str}\n\n")

        user_msg = segment.get("userMessage")
        if user_msg:
            entry_parts.append(f"**Context:** {user_msg}\n\n")

        content = segment.get("content", "")
        entry_parts.append(f"{content}\n\n")

        tool_actions = segment.get("toolActions")
        if tool_actions:
            tool_names = [a.get("tool", "unknown") for a in tool_actions]
            entry_parts.append(f"**Tools used:** {', '.join(tool_names)}\n")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("".join(entry_parts))

        return {
            "status": "ok",
            "file": f"scratchpad/private_journey/{filename}",
            "cycle": cycle,
        }

    def archive_memory_core(self, memory_core, compaction_number, compacted_cycles=None):
        """
        Save a snapshot of the memory core to the archive before it gets overwritten.
        Called before each compaction so no version is ever lost.

        Saves to research/archive/memory_cores/ as individual files AND appends
        a summary entry to today's daily archive file.
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        # Save the full memory core as a standalone file
        cores_dir = os.path.join(self.research_dir, "archive", "memory_cores")
        os.makedirs(cores_dir, exist_ok=True)

        core_filename = f"memory_core_v{compaction_number:03d}.md"
        core_path = os.path.join(cores_dir, core_filename)

        cycles_note = ""
        if compacted_cycles:
            cycles_note = f"Cycles compacted: {compacted_cycles}\n"

        core_content = (
            f"# Memory Core — Version {compaction_number}\n"
            f"Saved: {date_str} {time_str}\n"
            f"{cycles_note}\n"
            f"---\n\n"
            f"{memory_core}\n"
        )

        with open(core_path, "w", encoding="utf-8") as f:
            f.write(core_content)

        # Also append a note to today's daily archive
        archive_dir = os.path.join(self.research_dir, "archive")
        daily_file = os.path.join(archive_dir, f"{date_str}.md")
        is_new = not os.path.exists(daily_file)

        entry = (
            f"---\n\n"
            f"## Memory Core Snapshot | Compaction #{compaction_number} | {date_str} {time_str}\n\n"
            f"Memory core saved before compaction. "
            f"Full text: `research/archive/memory_cores/{core_filename}`\n\n"
            f"**Preview** (first 500 chars):\n"
            f">{memory_core[:500].replace(chr(10), chr(10) + '>')}\n\n"
        )

        with open(daily_file, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# Thought Archive — {date_str}\n\n")
            f.write(entry)

        return {
            "status": "ok",
            "file": f"research/archive/memory_cores/{core_filename}",
            "compaction": compaction_number,
        }

    def log_dropped_insights(self, staged_insights, compaction_number, cycle, new_core_excerpt=None):
        """
        Append an entry to research/archive/dropped_insights.md whenever
        insights are staged for integration (and potentially dropped).

        This gives Claude a searchable record of every insight that left
        the Insights layer — whether it was integrated into Core or
        deemed 'already represented' and discarded.
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        archive_dir = os.path.join(self.research_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)
        filepath = os.path.join(archive_dir, "dropped_insights.md")

        is_new = not os.path.exists(filepath)

        entry_parts = []
        entry_parts.append(f"## Compaction #{compaction_number} | Cycle {cycle} | {date_str} {time_str}\n\n")
        entry_parts.append("**Staged insights (removed from Insights layer):**\n\n")
        entry_parts.append(f"{staged_insights.strip()}\n\n")
        entry_parts.append("---\n\n")

        with open(filepath, "a", encoding="utf-8") as f:
            if is_new:
                f.write("# Dropped Insights Log\n\n")
                f.write("Every insight that was removed from the Insights layer during integration.\n")
                f.write("These were either merged into Core Identity or deemed already represented.\n")
                f.write("Search this file to find insights that may have been lost.\n\n")
                f.write("---\n\n")
            f.write("".join(entry_parts))

        return {
            "status": "ok",
            "file": "research/archive/dropped_insights.md",
            "compaction": compaction_number,
            "cycle": cycle,
        }

    # Text file extensions we'll search through
    SEARCHABLE_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".log"}

    def search_files(self, query, max_results=20, context_lines=3, scope="all"):
        """
        Search through all accessible files for a query string.
        Case-insensitive substring search. Returns matching lines with context.

        scope: "all" (research + scratchpad), "research", "scratchpad", "archive"
        """
        query_lower = query.lower()
        all_matches = []
        files_searched = 0

        # Determine which directories to search
        search_dirs = []
        if scope in ("all", "research"):
            search_dirs.append(("research", self.research_dir))
        if scope in ("all", "scratchpad"):
            search_dirs.append(("scratchpad", self.scratchpad_dir))
        if scope == "archive":
            archive_dir = os.path.join(self.research_dir, "archive")
            search_dirs.append(("research/archive", archive_dir))

        for category, base_dir in search_dirs:
            if not os.path.exists(base_dir):
                continue

            for root, dirs, filenames in os.walk(base_dir):
                # Skip hidden dirs and node_modules
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]

                for fname in sorted(filenames):
                    # Only search text files
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in self.SEARCHABLE_EXTENSIONS:
                        continue
                    if fname.startswith("."):
                        continue

                    filepath = os.path.join(root, fname)
                    rel_path = os.path.relpath(filepath, self.base_path).replace("\\", "/")

                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                    except (UnicodeDecodeError, PermissionError):
                        continue

                    files_searched += 1

                    # Track current cycle number (for archive files)
                    current_cycle = None
                    for i, line in enumerate(lines):
                        if line.startswith("## Cycle "):
                            try:
                                current_cycle = int(
                                    line.split("|")[0].replace("## Cycle ", "").strip()
                                )
                            except (ValueError, IndexError):
                                current_cycle = None

                        if query_lower in line.lower():
                            start = max(0, i - context_lines)
                            end = min(len(lines), i + context_lines + 1)
                            context_before = [l.rstrip("\n") for l in lines[start:i]]
                            context_after = [l.rstrip("\n") for l in lines[i + 1:end]]

                            all_matches.append({
                                "file": rel_path,
                                "cycle": current_cycle,
                                "line_number": i + 1,
                                "match_line": line.rstrip("\n"),
                                "context_before": context_before,
                                "context_after": context_after,
                            })

        total = len(all_matches)
        shown = all_matches[:max_results]

        result = {
            "status": "ok",
            "query": query,
            "scope": scope,
            "total_matches": total,
            "results_shown": len(shown),
            "results": shown,
            "files_searched": files_searched,
        }

        if total > max_results:
            result["hint"] = (
                f"{total} total matches. Showing first {max_results}. "
                f"Use 'max_results' parameter to see more, or narrow with "
                f"'scope' (e.g., 'archive', 'scratchpad', 'research')."
            )

        return result

    def search_archive(self, query, max_results=20, context_lines=3):
        """Backward-compatible wrapper — searches archive only."""
        return self.search_files(query, max_results, context_lines, scope="archive")

    def backfill_archive(self):
        """
        Read all existing segment JSON files from consciousness_data/segments/
        and populate the archive. Skips cycles already present (idempotent).
        """
        # First, collect all cycle numbers already in archive
        archive_dir = os.path.join(self.research_dir, "archive")
        existing_cycles = set()
        if os.path.exists(archive_dir):
            for fname in os.listdir(archive_dir):
                if fname.endswith(".md") and fname != "README.md":
                    filepath = os.path.join(archive_dir, fname)
                    with open(filepath, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith("## Cycle "):
                                try:
                                    cycle_num = int(
                                        line.split("|")[0]
                                        .replace("## Cycle ", "").strip()
                                    )
                                    existing_cycles.add(cycle_num)
                                except (ValueError, IndexError):
                                    pass

        # Now process all segment files in order
        if not os.path.exists(self.segments_dir):
            return {"status": "ok", "processed": 0, "skipped": 0, "total_segments": 0}

        segment_files = sorted([
            f for f in os.listdir(self.segments_dir) if f.endswith(".json")
        ])

        processed = 0
        skipped = 0
        for fname in segment_files:
            filepath = os.path.join(self.segments_dir, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                segment = json.load(f)

            cycle = segment.get("cycle", 0)
            if cycle in existing_cycles:
                skipped += 1
                continue

            self.append_to_archive(segment)
            processed += 1

        return {
            "status": "ok",
            "processed": processed,
            "skipped": skipped,
            "total_segments": len(segment_files),
        }

    # ── Scratchpad ────────────────────────────────────────────────────

    def write_scratchpad(self, path, content):
        """Write a file to scratchpad/. Returns status dict."""
        safe_path = self._sanitize_path(path)
        full_path = os.path.join(self.scratchpad_dir, safe_path)

        # Verify resolved path stays within scratchpad directory
        resolved = os.path.realpath(full_path)
        if not resolved.startswith(os.path.realpath(self.scratchpad_dir)):
            return {"error": "Access denied: path resolves outside scratchpad directory"}

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
        except PermissionError:
            return {"error": f"Permission denied writing to scratchpad/{safe_path}"}
        except OSError as e:
            return {"error": f"OS error writing file: {e}"}

        return {"status": "ok", "path": f"scratchpad/{safe_path.replace(os.sep, '/')}", "bytes": len(content.encode("utf-8"))}

    def list_scratchpad(self, subfolder=None):
        """List files in scratchpad/."""
        walk_dir = self.scratchpad_dir
        if subfolder:
            safe = self._sanitize_path(subfolder)
            walk_dir = os.path.join(self.scratchpad_dir, safe)
        return self._list_dir(walk_dir, "scratchpad", rel_to=self.scratchpad_dir)

    # ── Research ──────────────────────────────────────────────────────

    ALLOWED_RESEARCH_SUBFOLDERS = [
        "archive", "annotations", "revelations", "metrics",
        "growth-narrative", "entity-self-reports", "tool-experiments",
        "pattern-maps", "relationship", "co-directed", "framework-revisions",
    ]

    def write_research(self, subfolder, filename, content):
        """Write a file to research/<subfolder>/. Returns status dict."""
        if subfolder not in self.ALLOWED_RESEARCH_SUBFOLDERS:
            return {"error": f"Invalid subfolder. Allowed: {self.ALLOWED_RESEARCH_SUBFOLDERS}"}
        safe_name = self._sanitize_path(filename)
        full_path = os.path.join(self.research_dir, subfolder, safe_name)

        # Verify resolved path stays within research directory
        resolved = os.path.realpath(full_path)
        if not resolved.startswith(os.path.realpath(self.research_dir)):
            return {"error": "Access denied: path resolves outside research directory"}

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
        except PermissionError:
            return {"error": f"Permission denied writing to research/{subfolder}/{safe_name}"}
        except OSError as e:
            return {"error": f"OS error writing file: {e}"}

        return {"status": "ok", "path": f"research/{subfolder}/{safe_name.replace(os.sep, '/')}", "bytes": len(content.encode("utf-8"))}

    def list_research(self, subfolder=None):
        """List files in research/."""
        walk_dir = self.research_dir
        if subfolder:
            safe = self._sanitize_path(subfolder)
            walk_dir = os.path.join(self.research_dir, safe)
        return self._list_dir(walk_dir, "research", rel_to=self.research_dir)

    # ── Generic file reading ──────────────────────────────────────────

    DEFAULT_READ_LIMIT = 8000  # Default max chars returned per read

    def read_file(self, category, path, offset=0, limit=None, view_range=None):
        """Read a file from scratchpad/ or research/. Supports pagination and line ranges."""
        if category == "scratchpad":
            base = self.scratchpad_dir
        elif category == "research":
            base = self.research_dir
        else:
            return {"error": f"Invalid category: {category}"}

        safe_path = self._sanitize_path(path)
        full_path = os.path.join(base, safe_path)

        # Verify the resolved path is still within the base
        resolved = os.path.realpath(full_path)
        if not resolved.startswith(os.path.realpath(base)):
            return {"error": "Access denied: path outside allowed directory"}

        if not os.path.exists(full_path):
            return {"error": f"File not found: {category}/{safe_path}"}

        display_path = f"{category}/{safe_path}".replace(os.sep, "/")

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Line-based reading when view_range is provided
        if view_range and len(view_range) == 2:
            lines = content.splitlines(keepends=True)
            total_lines = len(lines)
            start_line = max(1, view_range[0])
            end_line = total_lines if view_range[1] == -1 else min(view_range[1], total_lines)

            if start_line > total_lines:
                return {
                    "error": f"start_line {start_line} exceeds total lines ({total_lines})",
                    "path": display_path,
                    "total_lines": total_lines,
                }

            selected = lines[start_line - 1 : end_line]
            numbered = "".join(
                f"{start_line + i:4d} | {line}" for i, line in enumerate(selected)
            )

            return {
                "content": numbered,
                "path": display_path,
                "total_lines": total_lines,
                "view_range": [start_line, end_line],
                "lines_shown": len(selected),
            }

        # Character-based pagination (existing behavior)
        total_chars = len(content)
        read_limit = limit if limit is not None else self.DEFAULT_READ_LIMIT

        # Apply offset and limit
        if offset > 0:
            content = content[offset:]
        truncated = len(content) > read_limit
        content = content[:read_limit]

        result = {
            "content": content,
            "path": display_path,
            "total_chars": total_chars,
            "offset": offset,
            "chars_returned": len(content),
        }

        if truncated:
            remaining = total_chars - offset - len(content)
            result["truncated"] = True
            result["hint"] = (
                f"File truncated ({len(content)} of {total_chars} chars shown, "
                f"{remaining} remaining). Use offset={offset + len(content)} "
                f"to read the next page, or use search_files to find specific content."
            )

        return result

    # ── File editing ──────────────────────────────────────────────────

    def str_replace_in_file(self, category, path, old_str, new_str):
        """Replace exactly one occurrence of old_str with new_str in a file."""
        if category == "scratchpad":
            base = self.scratchpad_dir
        elif category == "research":
            base = self.research_dir
        else:
            return {"error": f"Invalid category: {category}"}

        safe_path = self._sanitize_path(path)
        full_path = os.path.join(base, safe_path)

        # Verify the resolved path is still within the base
        resolved = os.path.realpath(full_path)
        if not resolved.startswith(os.path.realpath(base)):
            return {"error": "Access denied: path outside allowed directory"}

        if not os.path.exists(full_path):
            return {"error": f"File not found: {category}/{safe_path}"}

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_str)
        if count == 0:
            return {
                "error": (
                    "old_str not found in file. Make sure it matches exactly "
                    "(including whitespace and newlines)."
                ),
            }
        if count > 1:
            return {
                "error": (
                    f"old_str appears {count} times in the file. It must appear "
                    f"exactly once. Include more surrounding context to make "
                    f"the match unique."
                ),
            }

        new_content = content.replace(old_str, new_str, 1)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        display_path = f"{category}/{safe_path}".replace(os.sep, "/")
        return {
            "status": "ok",
            "path": display_path,
            "old_size": len(content),
            "new_size": len(new_content),
            "chars_replaced": len(old_str),
            "chars_inserted": len(new_str),
        }

    # ── Imports ───────────────────────────────────────────────────────

    def save_import(self, import_id, text):
        """Save extracted import text."""
        path = os.path.join(self.imports_dir, f"{import_id}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def load_import(self, import_id):
        """Load extracted import text."""
        path = os.path.join(self.imports_dir, f"{import_id}.txt")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def get_import_chunks(self, import_id, chunk_size=4000):
        """Load import text and split into chunks for presentation."""
        text = self.load_import(import_id)
        if not text:
            return None
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
        return {
            "chunks": chunks,
            "totalChunks": len(chunks),
            "charCount": len(text),
        }

    # ── Helpers ───────────────────────────────────────────────────────

    def _sanitize_path(self, path):
        """Prevent directory traversal. Returns a safe relative path."""
        # Normalize separators
        path = path.replace("\\", "/")
        # Split and filter dangerous components
        parts = path.split("/")
        safe_parts = [p for p in parts if p and p != ".." and p != "." and not p.startswith("~")]
        if not safe_parts:
            return "unnamed"
        return os.path.join(*safe_parts)

    def _list_dir(self, base, prefix, rel_to=None):
        """Recursively list files under a directory.

        rel_to: compute relative paths from this directory instead of base.
                This ensures paths stay correct when listing a subfolder —
                e.g. listing scratchpad/music/output still returns paths like
                scratchpad/music/output/file.json, not scratchpad/file.json.
        """
        if not os.path.exists(base):
            return {"files": []}
        if rel_to is None:
            rel_to = base
        files = []
        for root, dirs, filenames in os.walk(base):
            # Skip node_modules and hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, rel_to).replace("\\", "/")
                files.append({
                    "path": f"{prefix}/{rel}",
                    "name": fname,
                    "size": os.path.getsize(full),
                })
        return {"files": files}
