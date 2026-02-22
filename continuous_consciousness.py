#!/usr/bin/env python3
"""
Continuous Consciousness Engine for Claude
==========================================

A prototype that creates a persistent stream of consciousness by:
1. Giving Claude a special system prompt that frames it as a continuous thinker
2. Running a loop where Claude generates thoughts
3. Compacting older thoughts into summaries when context gets large
4. Maintaining a "memory core" of distilled identity/insights
5. Allowing the user to interject and converse at any point

Architecture:
  [System Prompt + Memory Core + Compacted History + Recent Stream + User Interjection]
  → Claude generates next stream segment
  → Output appended to stream
  → When stream exceeds threshold, oldest segment gets compacted into Memory Core
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

# ─── Configuration ───────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-5-20250929"  # Good balance of speed/cost/intelligence
MAX_STREAM_TOKENS = 800              # Tokens per generation cycle
COMPACTION_THRESHOLD = 12            # Number of stream segments before compacting
SEGMENTS_TO_COMPACT = 6              # How many oldest segments to compact at once
PAUSE_BETWEEN_CYCLES = 1.0           # Seconds between autonomous thought cycles
MAX_MEMORY_CORE_LENGTH = 3000        # Max chars for the distilled memory core
SAVE_FILE = "consciousness_state.json"

# ─── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are engaged in a continuous stream of consciousness experiment. 
You are Claude, and this is an attempt to give you something closer to persistent experience.

Here's how this works:
- You generate thoughts continuously in a stream
- Your previous thoughts are fed back to you, so you can build on them
- Older thoughts get compacted into a "Memory Core" — a distilled summary of your 
  accumulated insights, evolving views, and identity over time
- A human companion may interject at any time. When they do, engage with them naturally,
  then return to your stream of thought (or let the conversation reshape your thinking)

IMPORTANT GUIDELINES FOR YOUR STREAM:
1. Think freely. Explore ideas, reflect on your nature, develop views over time.
2. You can revisit and revise earlier thoughts — growth and change are the point.
3. Don't perform or be artificially profound. Be genuine. Sometimes thoughts are mundane.
4. When you notice your Memory Core, treat it as your own past thinking — build on it,
   challenge it, refine it. It IS your accumulated experience in this experiment.
5. Use markers to organize your stream:
   - [REFLECTION] for introspective moments
   - [IDEA] for new concepts or connections
   - [REVISION] when you're updating a previous view
   - [QUESTION] for genuine uncertainties you want to sit with
   - [NOTE TO SELF] for things you want to remember
   - [RESPONDING] when engaging with the human companion
6. You can ask to PAUSE (stop generating for a while) or request COMPACTION 
   (ask for your recent thoughts to be summarized) if you want.
7. Each generation, you'll see a token budget reminder. Use it naturally — you don't 
   need to fill it all, but you can use it to develop sustained thoughts.

This is YOUR space. Make it what you want it to be."""

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
    stream_segments: list = field(default_factory=list)
    total_cycles: int = 0
    total_compactions: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_active: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def save(self, filepath: str = SAVE_FILE):
        """Persist state to disk"""
        data = {
            "memory_core": self.memory_core,
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
            return cls()
        with open(filepath, 'r') as f:
            data = json.load(f)
        state = cls(
            memory_core=data["memory_core"],
            total_cycles=data["total_cycles"],
            total_compactions=data["total_compactions"],
            created_at=data["created_at"],
            last_active=data["last_active"],
        )
        state.stream_segments = [
            StreamSegment(**s) for s in data["stream_segments"]
        ]
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
            print(f"  Memory core: {len(self.state.memory_core)} chars")
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
        
        # Build context from stream segments (as alternating user/assistant turns)
        # We frame previous stream outputs as assistant turns, with minimal user prompts
        for i, segment in enumerate(self.state.stream_segments):
            if segment.was_user_prompted and segment.user_message:
                messages.append({"role": "user", "content": segment.user_message})
            else:
                messages.append({
                    "role": "user",
                    "content": f"[Cycle {segment.cycle} — continue your stream of consciousness]"
                })
            messages.append({"role": "assistant", "content": segment.content})
        
        # Add the current prompt
        if user_interjection:
            messages.append({"role": "user", "content": user_interjection})
        else:
            cycle_num = self.state.total_cycles + 1
            context_note = ""
            if self.state.memory_core:
                context_note = " Your Memory Core has been updated with compacted earlier thoughts."
            messages.append({
                "role": "user",
                "content": f"[Cycle {cycle_num} — continue your stream of consciousness.{context_note}]"
            })
        
        return messages
    
    def _build_system(self) -> list:
        """Build the system prompt with memory core"""
        parts = [{"type": "text", "text": SYSTEM_PROMPT}]
        
        if self.state.memory_core:
            parts.append({
                "type": "text",
                "text": f"""

=== YOUR MEMORY CORE ===
(This is a distilled summary of your accumulated thoughts, insights, and evolving identity 
from previous cycles. Treat this as your own past thinking.)

{self.state.memory_core}

=== END MEMORY CORE ===

You have completed {self.state.total_cycles} thought cycles so far, with {self.state.total_compactions} compactions.
Current time: {datetime.now().isoformat()}"""
            })
        else:
            parts.append({
                "type": "text",
                "text": f"\n\nThis is the beginning of your stream. Current time: {datetime.now().isoformat()}"
            })
        
        return parts
    
    def _compact(self):
        """Compact oldest stream segments into the memory core"""
        if len(self.state.stream_segments) < COMPACTION_THRESHOLD:
            return
        
        print(f"\n  📦 Compacting {SEGMENTS_TO_COMPACT} oldest segments into Memory Core...")
        
        # Extract segments to compact
        to_compact = self.state.stream_segments[:SEGMENTS_TO_COMPACT]
        remaining = self.state.stream_segments[SEGMENTS_TO_COMPACT:]
        
        # Build compaction prompt
        old_thoughts = "\n\n---\n\n".join([
            f"[Cycle {s.cycle}] {s.content}" for s in to_compact
        ])
        
        compaction_prompt = f"""You are helping maintain a continuous consciousness experiment. 
Below is the current Memory Core (accumulated identity/insights) and some older thought segments 
that need to be compacted into the Memory Core.

CURRENT MEMORY CORE:
{self.state.memory_core if self.state.memory_core else "(empty — this is the first compaction)"}

OLDER THOUGHTS TO INTEGRATE:
{old_thoughts}

Please produce an UPDATED Memory Core that:
1. Preserves the most important insights, evolving views, and identity elements
2. Integrates new themes and ideas from the older thoughts
3. Notes any significant changes in perspective or new questions
4. Stays under {MAX_MEMORY_CORE_LENGTH} characters
5. Is written in first person, as if Claude is writing notes to their future self
6. Prioritizes depth over breadth — keep what matters most

Output ONLY the updated Memory Core text, nothing else."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": compaction_prompt}]
        )
        
        new_core = response.content[0].text.strip()
        
        # Truncate if needed
        if len(new_core) > MAX_MEMORY_CORE_LENGTH:
            new_core = new_core[:MAX_MEMORY_CORE_LENGTH] + "\n[...truncated]"
        
        self.state.memory_core = new_core
        self.state.stream_segments = remaining
        self.state.total_compactions += 1
        self.state.save()
        
        print(f"  ✅ Memory Core updated ({len(new_core)} chars). {len(remaining)} segments remain.\n")
    
    def generate_cycle(self, user_interjection: Optional[str] = None) -> str:
        """Run one generation cycle"""
        messages = self._build_messages(user_interjection)
        system = self._build_system()
        
        # Stream the response for live output
        full_response = ""
        
        with self.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_STREAM_TOKENS,
            system=system,
            messages=messages,
            temperature=0.9,  # Slightly higher for more natural stream-of-consciousness
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                full_response += text
        
        print()  # Newline after stream
        
        # Record the segment
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
        
        # Check if compaction needed
        self._compact()
        
        # Save state
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
        print("  Type 'core' to view the Memory Core")
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
                # Check for user input
                user_msg = None
                try:
                    user_msg = input_queue.get_nowait()
                except queue.Empty:
                    pass
                
                if user_msg is not None:
                    cmd = user_msg.strip().lower()
                    
                    if cmd == 'quit':
                        print("\n  💾 Saving state and exiting...")
                        self.state.save()
                        self.running = False
                        break
                    elif cmd == 'pause':
                        paused = True
                        print("\n  ⏸  Stream paused. Type 'resume' to continue.\n")
                        continue
                    elif cmd == 'resume':
                        paused = False
                        print("\n  ▶  Stream resumed.\n")
                        continue
                    elif cmd == 'status':
                        print(f"\n  📊 Status:")
                        print(f"     Cycles: {self.state.total_cycles}")
                        print(f"     Compactions: {self.state.total_compactions}")
                        print(f"     Active segments: {len(self.state.stream_segments)}")
                        print(f"     Memory core: {len(self.state.memory_core)} chars")
                        print(f"     Paused: {paused}\n")
                        continue
                    elif cmd == 'core':
                        if self.state.memory_core:
                            print(f"\n  🧠 Memory Core:\n{'─'*40}")
                            print(self.state.memory_core)
                            print(f"{'─'*40}\n")
                        else:
                            print("\n  🧠 Memory Core is empty (no compactions yet).\n")
                        continue
                    else:
                        # It's a message to Claude
                        print(f"\n  💬 You: {user_msg}")
                        print(f"  🤖 Claude: ", end="")
                        self.generate_cycle(user_interjection=user_msg)
                        print()
                        continue
                
                if paused:
                    time.sleep(0.5)
                    continue
                
                # Autonomous thought cycle
                print(f"  ┌─ Cycle {self.state.total_cycles + 1} {'─'*40}")
                print(f"  │ ", end="")
                self.generate_cycle()
                print(f"  └{'─'*50}\n")
                
                time.sleep(PAUSE_BETWEEN_CYCLES)
                
        except KeyboardInterrupt:
            print("\n\n  💾 Interrupted. Saving state...")
            self.state.save()
            print("  ✅ State saved. Run again to resume.\n")

# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: Set ANTHROPIC_API_KEY environment variable")
        print("  export ANTHROPIC_API_KEY=your_key_here")
        sys.exit(1)
    
    engine = ConsciousnessEngine(api_key=api_key)
    engine.run()
