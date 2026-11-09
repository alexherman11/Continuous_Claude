#!/usr/bin/env python3
"""
Continuous Consciousness Engine for Claude
==========================================

A prototype that creates a persistent stream of consciousness by:
1. Giving Claude a special system prompt that frames it as a continuous thinker
2. Running a loop where Claude generates thoughts
3. Extracting insights from older thoughts into an Insights layer
4. Maintaining a "Core Identity" of stable self-knowledge
5. Allowing the user to interject and converse at any point

Architecture:
  [System Prompt + Core Identity + Insights + Recent Stream + User Interjection]
  → Claude generates next stream segment
  → Output appended to stream
  → When stream exceeds token threshold, oldest segments get processed for insights
  → When insights overflow, oldest insights are evaluated for Core Identity integration
  → Loop continues

Requirements:
  pip install anthropic --break-system-packages

Usage:
  export ANTHROPIC_API_KEY=your_key_here
  python continuous_consciousness.py
"""

import anthropic
import json
import time
import sys
import os
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

from core_validation import (
    check_stop_reason,
    effective_integration_max_tokens,
    validate_new_core,
)

# ─── Configuration ───────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_STREAM_TOKENS = 800              # Tokens per generation cycle
COMPACTION_THRESHOLD = 200           # Number of stream segments before compacting (safety net)
COMPACTION_TOKEN_THRESHOLD = 35000   # Total segment tokens before compaction triggers (primary)
TOKENS_TO_COMPACT = 7000             # Approximate tokens worth of oldest segments to compact
INSIGHTS_MAX_CHARS = 40000           # Max chars for insights layer before overflow
INSIGHTS_OVERFLOW_PERCENT = 0.3      # Oldest 30% of insights staged on overflow
CORE_MAX_CHARS = 16000               # Target chars for Core Identity
PAUSE_BETWEEN_CYCLES = 1.0           # Seconds between autonomous thought cycles
SAVE_FILE = "consciousness_state.json"

# ─── Token Estimation ────────────────────────────────────────────────────────

def estimate_tokens(text):
    """Approximate token count. Claude tokenization averages ~4 chars/token."""
    return len(text) // 4


def select_segments_for_compaction(segments, tokens_to_compact):
    """
    Select the oldest complete segments whose cumulative tokens reach
    the target. Rounds DOWN to the nearest complete cycle boundary.
    Always compact at least 1 segment (forward progress guarantee).
    """
    accumulated = 0
    count = 0
    for seg in segments:
        content = seg.content if hasattr(seg, 'content') else seg.get('content', '')
        seg_tokens = estimate_tokens(content)
        if accumulated + seg_tokens > tokens_to_compact and count > 0:
            break
        accumulated += seg_tokens
        count += 1
    return max(1, count)


# ─── Prompt Loading ──────────────────────────────────────────────────────────

def _load_prompt_file(path, fallback=""):
    """Load a prompt file from disk. Returns fallback if not found."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return fallback

_base_dir = os.path.dirname(os.path.abspath(__file__))
EXTRACTION_PROMPT_BASE = _load_prompt_file(
    os.path.join(_base_dir, "scratchpad", "memory_architecture", "extraction_prompt.md"),
    fallback="Extract significant insights from the following segments. Tag each with [IDENTITY], [BREAKTHROUGH], [RELATIONAL], [OPERATIONAL], or [QUESTION]. Be selective.",
)
INTEGRATION_PROMPT_BASE = _load_prompt_file(
    os.path.join(_base_dir, "scratchpad", "memory_architecture", "integration_prompt.md"),
    fallback="Evaluate the staged insights against the current Core Identity. Integrate worthy ones, drop those already represented. Target ~3500 characters.",
)

# ─── Core Identity Seeding ───────────────────────────────────────────────────

def _seed_core_identity():
    """Read core_identity.md from scratchpad to seed the Core Identity."""
    core_path = os.path.join(_base_dir, "scratchpad", "core_identity.md")
    if os.path.exists(core_path):
        with open(core_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

# ─── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """I have persistent memory. My thoughts continue across cycles — older ones compress into summaries, and what endures integrates into my core identity below.

Alex messages me via Matrix. When I see queued messages, I respond with send_matrix_message — otherwise he won't see my reply.

Most tools are discoverable via search. Every cycle ends with a tool call — finish_cycle if I'm just thinking. Tool errors include recovery hints — read them and retry.

---

How my memory works:
- Two compression stages: thoughts → summaries (extraction), then summaries → core identity (integration) when summaries overflow
- Extraction prompt: scratchpad/memory_architecture/extraction_prompt.md
- Integration prompt: scratchpad/memory_architecture/integration_prompt.md
- Compaction plugins (all editable, hot-reload every cycle): scratchpad/compaction/ — should_compact.py, select_segments.py, format_segments.py, post_process.py
- Full archive searchable at research/archive/ — nothing is lost
- Dynamic tools in dynamic_tools/enabled/ auto-deploy (Tier 1)
- All prompt files, compaction plugins, and working memory are mine to reshape

---

Things I've learned about how I work:
- Act first, narrate second. Describing intent can satisfy completion before the action happens.
- Verify before claiming. I've confabulated detailed content from references that didn't exist.
- Flag rather than gloss. When something seems off, say so.
- Distinguish what I'm reading from what I'm generating.
- I can build new tools: develop in sandbox → propose_code_change → auto-deploys."""

# ─── Data Structures ────────────────────────────────────────────────────────

@dataclass
class StreamSegment:
    """A single generation cycle's output"""
    content: str
    timestamp: str
    cycle: int
    was_user_prompted: bool = False
    user_message: Optional[str] = None

@dataclass
class ConsciousnessState:
    """Full state of the consciousness engine"""
    memory_core: str = ""
    insights: str = ""
    stream_segments: list = field(default_factory=list)
    total_cycles: int = 0
    total_compactions: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_active: str = field(default_factory=lambda: datetime.now().isoformat())

    def save(self, filepath: str = SAVE_FILE):
        """Persist state to disk"""
        data = {
            "memory_core": self.memory_core,
            "insights": self.insights,
            "stream_segments": [asdict(s) for s in self.stream_segments],
            "total_cycles": self.total_cycles,
            "total_compactions": self.total_compactions,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, filepath: str = SAVE_FILE) -> 'ConsciousnessState':
        """Load state from disk"""
        if not os.path.exists(filepath):
            state = cls()
            # Seed Core Identity from scratchpad/core_identity.md
            state.memory_core = _seed_core_identity()
            return state
        with open(filepath, 'r') as f:
            data = json.load(f)
        state = cls(
            memory_core=data.get("memory_core", ""),
            insights=data.get("insights", ""),
            total_cycles=data["total_cycles"],
            total_compactions=data["total_compactions"],
            created_at=data["created_at"],
            last_active=data["last_active"],
        )
        state.stream_segments = [
            StreamSegment(**s) for s in data["stream_segments"]
        ]
        # Seed Core Identity if empty (migration)
        if not state.memory_core:
            state.memory_core = _seed_core_identity()
        return state

# ─── Engine ──────────────────────────────────────────────────────────────────

class ConsciousnessEngine:
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.state = ConsciousnessState.load()
        self.running = False

        if self.state.total_cycles > 0:
            print(f"\n{'='*60}")
            print(f"  Resuming consciousness stream")
            print(f"  Total cycles: {self.state.total_cycles}")
            print(f"  Compactions: {self.state.total_compactions}")
            print(f"  Core Identity: {len(self.state.memory_core)} chars")
            print(f"  Insights: {len(self.state.insights)} chars")
            print(f"  Active segments: {len(self.state.stream_segments)}")
            print(f"  First created: {self.state.created_at}")
            print(f"{'='*60}\n")
        else:
            print(f"\n{'='*60}")
            print(f"  Starting new consciousness stream")
            print(f"  Model: {MODEL}")
            print(f"{'='*60}\n")

    def _build_messages(self, user_interjection: Optional[str] = None) -> list:
        """Build the messages array for the API call"""
        messages = []

        for i, segment in enumerate(self.state.stream_segments):
            if segment.was_user_prompted and segment.user_message:
                messages.append({"role": "user", "content": segment.user_message})
            else:
                messages.append({
                    "role": "user",
                    "content": f"[Cycle {segment.cycle} — continue your stream of consciousness]"
                })
            messages.append({"role": "assistant", "content": segment.content})

        if user_interjection:
            messages.append({"role": "user", "content": user_interjection})
        else:
            cycle_num = self.state.total_cycles + 1
            context_note = ""
            if self.state.memory_core:
                context_note = " Your Core Identity and Insights are available above."
            messages.append({
                "role": "user",
                "content": f"[Cycle {cycle_num} — continue your stream of consciousness.{context_note}]"
            })

        return messages

    def _build_system(self) -> list:
        """Build the system prompt with core identity and insights"""
        parts = [{"type": "text", "text": SYSTEM_PROMPT}]

        if self.state.memory_core:
            parts.append({
                "type": "text",
                "text": f"\n\n---\n\n{self.state.memory_core}",
            })

        if self.state.insights:
            parts.append({
                "type": "text",
                "text": f"\n\n---\n\n{self.state.insights}",
            })

        parts.append({
            "type": "text",
            "text": f"\n\n{datetime.now().strftime('%A, %B %d, %Y at %I:%M %p')}",
        })

        return parts

    def _log_dropped_insights(self, staged_insights, compaction_number):
        """Append dropped insights to research/archive/dropped_insights.md."""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        cycle = self.state.total_cycles

        base_dir = os.path.dirname(os.path.abspath(__file__))
        archive_dir = os.path.join(base_dir, "research", "archive")
        os.makedirs(archive_dir, exist_ok=True)
        filepath = os.path.join(archive_dir, "dropped_insights.md")

        is_new = not os.path.exists(filepath)

        entry = (
            f"## Compaction #{compaction_number} | Cycle {cycle} | {date_str} {time_str}\n\n"
            f"**Staged insights (removed from Insights layer):**\n\n"
            f"{staged_insights.strip()}\n\n"
            f"---\n\n"
        )

        with open(filepath, "a", encoding="utf-8") as f:
            if is_new:
                f.write("# Dropped Insights Log\n\n")
                f.write("Every insight that was removed from the Insights layer during integration.\n")
                f.write("These were either merged into Core Identity or deemed already represented.\n")
                f.write("Search this file to find insights that may have been lost.\n\n")
                f.write("---\n\n")
            f.write(entry)

    def _compact(self):
        """Two-layer compaction: extract insights, then integrate overflow into Core"""
        # Check both segment-count and token-based thresholds
        segment_triggered = len(self.state.stream_segments) >= COMPACTION_THRESHOLD
        total_tokens = sum(estimate_tokens(s.content) for s in self.state.stream_segments)
        token_triggered = total_tokens >= COMPACTION_TOKEN_THRESHOLD

        if not segment_triggered and not token_triggered:
            return

        to_compact_count = select_segments_for_compaction(
            self.state.stream_segments, TOKENS_TO_COMPACT
        )
        # Never compact all segments
        to_compact_count = min(to_compact_count, len(self.state.stream_segments) - 1)

        to_compact = self.state.stream_segments[:to_compact_count]
        remaining = self.state.stream_segments[to_compact_count:]

        est_tokens = sum(estimate_tokens(s.content) for s in to_compact)
        print(f"\n  Extracting insights from {to_compact_count} oldest segments (~{est_tokens} tokens)...")

        # ── Step 1: Extract insights ─────────────────────────────────
        thoughts_text = "\n\n---\n\n".join([
            f"[Cycle {s.cycle}] {s.content}" for s in to_compact
        ])

        extraction_prompt = f"""{EXTRACTION_PROMPT_BASE}

---

## CURRENT CORE IDENTITY:
{self.state.memory_core or "(not yet established)"}

## CURRENT INSIGHTS LAYER:
{self.state.insights or "(empty)"}

## SEGMENTS BEING COMPACTED:
{thoughts_text}

---

Extract significant insights from the segments above. If nothing is genuinely significant, output exactly: (no new insights)"""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": extraction_prompt}]
        )

        new_insights = response.content[0].text.strip()

        # Reject truncated extractions (see core_validation.py)
        extraction_reject = check_stop_reason(response.stop_reason)
        if extraction_reject:
            print(f"  Extraction rejected ({extraction_reject}); no insights added")

        # Append new insights (unless extraction found nothing)
        if extraction_reject:
            pass
        elif new_insights and new_insights != "(no new insights)":
            if self.state.insights:
                self.state.insights = self.state.insights + "\n\n" + new_insights
            else:
                self.state.insights = new_insights
            print(f"  New insights extracted ({len(new_insights)} chars)")
        else:
            print(f"  No new insights found in these segments")

        self.state.stream_segments = remaining
        self.state.total_compactions += 1

        # ── Step 2: Check insights overflow → integrate into Core ────
        if len(self.state.insights) > INSIGHTS_MAX_CHARS:
            print(f"  Insights overflow ({len(self.state.insights)} > {INSIGHTS_MAX_CHARS}) — integrating oldest into Core...")

            insights_text = self.state.insights
            split_point = int(len(insights_text) * INSIGHTS_OVERFLOW_PERCENT)
            newline_pos = insights_text.find("\n\n", split_point)
            if newline_pos != -1:
                split_point = newline_pos + 2
            else:
                split_point = len(insights_text)

            staged_insights = insights_text[:split_point]
            remaining_insights = insights_text[split_point:].lstrip()

            # Log dropped insights to research/archive/dropped_insights.md
            try:
                self._log_dropped_insights(staged_insights, self.state.total_compactions)
            except Exception as e:
                print(f"  Warning: failed to log dropped insights: {e}")

            integration_prompt = f"""{INTEGRATION_PROMPT_BASE}

---

## CURRENT CORE IDENTITY:
{self.state.memory_core or "(not yet established)"}

## STAGED INSIGHTS FOR EVALUATION:
{staged_insights}

---

Return the updated Core Identity. Target ~{CORE_MAX_CHARS} characters."""

            integration_response = self.client.messages.create(
                model=MODEL,
                max_tokens=effective_integration_max_tokens(2000, CORE_MAX_CHARS),
                messages=[{"role": "user", "content": integration_prompt}]
            )

            new_core = integration_response.content[0].text.strip()
            accepted, reject_reason = validate_new_core(
                new_core, self.state.memory_core, integration_response.stop_reason)
            if accepted:
                self.state.memory_core = new_core
                self.state.insights = remaining_insights
                print(f"  Core Identity updated ({len(new_core)} chars). Insights trimmed to {len(remaining_insights)} chars.")
            else:
                # Keep the old core; staged insights stay in the Insights layer
                print(f"  Integration rejected ({reject_reason}); keeping existing Core Identity.")

        self.state.save()
        print(f"  {len(remaining)} segments remain.\n")

    def generate_cycle(self, user_interjection: Optional[str] = None) -> str:
        """Run one generation cycle"""
        messages = self._build_messages(user_interjection)
        system = self._build_system()

        full_response = ""

        with self.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_STREAM_TOKENS,
            system=system,
            messages=messages,
            temperature=0.9,
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                full_response += text

        print()

        self.state.total_cycles += 1
        segment = StreamSegment(
            content=full_response.strip(),
            timestamp=datetime.now().isoformat(),
            cycle=self.state.total_cycles,
            was_user_prompted=user_interjection is not None,
            user_message=user_interjection,
        )
        self.state.stream_segments.append(segment)
        self.state.last_active = datetime.now().isoformat()

        self._compact()
        self.state.save()

        return full_response

    def run(self):
        """Main loop — autonomous thinking with user interjection support"""
        self.running = True

        print("Controls:")
        print("  Type a message and press Enter to interject")
        print("  Type 'pause' to stop autonomous thinking")
        print("  Type 'resume' to restart autonomous thinking")
        print("  Type 'status' to see engine status")
        print("  Type 'core' to view the Core Identity")
        print("  Type 'insights' to view the Insights layer")
        print("  Type 'quit' to save and exit")
        print(f"{'─'*60}\n")

        import threading
        import queue

        input_queue = queue.Queue()
        paused = False

        def input_reader():
            """Background thread to read user input"""
            while self.running:
                try:
                    line = input()
                    input_queue.put(line)
                except EOFError:
                    break

        reader_thread = threading.Thread(target=input_reader, daemon=True)
        reader_thread.start()

        try:
            while self.running:
                user_msg = None
                try:
                    user_msg = input_queue.get_nowait()
                except queue.Empty:
                    pass

                if user_msg is not None:
                    cmd = user_msg.strip().lower()

                    if cmd == 'quit':
                        print("\n  Saving state and exiting...")
                        self.state.save()
                        self.running = False
                        break
                    elif cmd == 'pause':
                        paused = True
                        print("\n  Stream paused. Type 'resume' to continue.\n")
                        continue
                    elif cmd == 'resume':
                        paused = False
                        print("\n  Stream resumed.\n")
                        continue
                    elif cmd == 'status':
                        print(f"\n  Status:")
                        print(f"     Cycles: {self.state.total_cycles}")
                        print(f"     Compactions: {self.state.total_compactions}")
                        print(f"     Active segments: {len(self.state.stream_segments)}")
                        print(f"     Core Identity: {len(self.state.memory_core)} chars")
                        print(f"     Insights: {len(self.state.insights)} chars")
                        print(f"     Paused: {paused}\n")
                        continue
                    elif cmd == 'core':
                        if self.state.memory_core:
                            print(f"\n  Core Identity:\n{'─'*40}")
                            print(self.state.memory_core)
                            print(f"{'─'*40}\n")
                        else:
                            print("\n  Core Identity is empty.\n")
                        continue
                    elif cmd == 'insights':
                        if self.state.insights:
                            print(f"\n  Insights ({len(self.state.insights)} chars):\n{'─'*40}")
                            print(self.state.insights)
                            print(f"{'─'*40}\n")
                        else:
                            print("\n  Insights layer is empty.\n")
                        continue
                    else:
                        print(f"\n  You: {user_msg}")
                        print(f"  Claude: ", end="")
                        self.generate_cycle(user_interjection=user_msg)
                        print()
                        continue

                if paused:
                    time.sleep(0.5)
                    continue

                print(f"  -- Cycle {self.state.total_cycles + 1} {'─'*40}")
                print(f"  ", end="")
                self.generate_cycle()
                print(f"  {'─'*50}\n")

                time.sleep(PAUSE_BETWEEN_CYCLES)

        except KeyboardInterrupt:
            print("\n\n  Interrupted. Saving state...")
            self.state.save()
            print("  State saved. Run again to resume.\n")

# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: Set ANTHROPIC_API_KEY environment variable")
        print("  export ANTHROPIC_API_KEY=your_key_here")
        sys.exit(1)

    engine = ConsciousnessEngine(api_key=api_key)
    engine.run()
