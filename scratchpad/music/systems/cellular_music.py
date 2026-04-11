#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║          CELLULAR AUTOMATON MUSIC GENERATOR                      ║
║   Turning emergent mathematical patterns into living sound       ║
╚══════════════════════════════════════════════════════════════════╝

Maps 2D cellular automaton evolution to MIDI music.

  Column  → Note in a musical scale
  ON cell → Note fires at that time step
  DYING   → Ghost/echo note (very soft)
  Row pos → Affects velocity and note duration
  Density → Polyphony and emphasis

Rules available:
  brian   – Brian's Brain (3-state): propagating wave-fronts, very alive
  life    – Conway's Game of Life: classic, sparse, meditative
  seeds   – Seeds (B2/S): explosive expanding rings
  lenia   – Lenia-inspired smooth rule: flowing, ambient
  musical – Custom rule: sustained harmonic clusters
"""

import os
import sys
import copy
import random
import argparse
from typing import List, Dict

try:
    from midiutil import MIDIFile
except ImportError:
    print("Installing midiutil…")
    os.system(f"{sys.executable} -m pip install midiutil -q")
    from midiutil import MIDIFile


# ══════════════════════════════════════════════════════════════════
#  SCALES  (semitone intervals from root)
# ══════════════════════════════════════════════════════════════════

SCALES: Dict[str, List[int]] = {
    "pentatonic_major": [0, 2, 4, 7, 9],           # C D E G A
    "pentatonic_minor": [0, 3, 5, 7, 10],           # C Eb F G Bb
    "blues":            [0, 3, 5, 6, 7, 10],        # C Eb F F# G Bb
    "major":            [0, 2, 4, 5, 7, 9, 11],     # C D E F G A B
    "natural_minor":    [0, 2, 3, 5, 7, 8, 10],     # C D Eb F G Ab Bb
    "dorian":           [0, 2, 3, 5, 7, 9, 10],     # C D Eb F G A Bb
    "phrygian":         [0, 1, 3, 5, 7, 8, 10],     # C Db Eb F G Ab Bb
    "lydian":           [0, 2, 4, 6, 7, 9, 11],     # C D E F# G A B
    "mixolydian":       [0, 2, 4, 5, 7, 9, 10],     # C D E F G A Bb
    "whole_tone":       [0, 2, 4, 6, 8, 10],        # All whole steps
    "diminished":       [0, 2, 3, 5, 6, 8, 9, 11],  # Half-whole dim
    "japanese":         [0, 1, 5, 7, 8],            # Hirajoshi
    "chromatic":        list(range(12)),
}

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_name(n: int) -> str:
    return f"{_NOTE_NAMES[n % 12]}{n // 12 - 1}"


# ══════════════════════════════════════════════════════════════════
#  CELLULAR AUTOMATON
# ══════════════════════════════════════════════════════════════════

class CellularAutomaton:
    """
    2D toroidal cellular automaton.

    Cell states:
      0 = OFF    · dead / silent
      1 = ON     █ alive / note fires
      2 = DYING  ▒ refractory / ghost note
    """

    DISPLAY = {0: "·", 1: "█", 2: "▒"}

    def __init__(self,
                 width: int = 16,
                 height: int = 16,
                 rule: str = "brian",
                 initial: str = "random",
                 seed: int = 42):
        self.width = width
        self.height = height
        self.rule = rule
        random.seed(seed)
        self.grid = self._init(initial)
        self.history: List[List[List[int]]] = []
        self.generation = 0

    # ── Initialisation patterns ──────────────────────────────────

    def _init(self, pattern: str) -> List[List[int]]:
        w, h = self.width, self.height

        if pattern == "random":
            return [[1 if random.random() < 0.30 else 0 for _ in range(w)]
                    for _ in range(h)]

        if pattern == "sparse":
            return [[1 if random.random() < 0.12 else 0 for _ in range(w)]
                    for _ in range(h)]

        if pattern == "dense":
            return [[1 if random.random() < 0.58 else 0 for _ in range(w)]
                    for _ in range(h)]

        if pattern == "symmetric":
            # Left–right mirror
            half = [[1 if random.random() < 0.30 else 0
                     for _ in range(w // 2)] for _ in range(h)]
            return [row + row[::-1] for row in half]

        if pattern == "symmetric_4way":
            # Four-quadrant symmetry (very musical)
            qw, qh = w // 2, h // 2
            quad = [[1 if random.random() < 0.32 else 0
                     for _ in range(qw)] for _ in range(qh)]
            g = [[0] * w for _ in range(h)]
            for r in range(qh):
                for c in range(qw):
                    v = quad[r][c]
                    g[r][c] = v
                    g[r][w - 1 - c] = v
                    g[h - 1 - r][c] = v
                    g[h - 1 - r][w - 1 - c] = v
            return g

        if pattern == "glider":
            g = [[0] * w for _ in range(h)]
            for r, c in [(1, 0), (2, 1), (0, 2), (1, 2), (2, 2)]:
                g[r % h][c % w] = 1
            return g

        if pattern == "stripes":
            return [[1 if r % 3 == 0 else 0 for _ in range(w)]
                    for r in range(h)]

        if pattern == "diagonal":
            g = [[0] * w for _ in range(h)]
            for i in range(min(w, h)):
                g[i][i] = 1
                g[i][(i + w // 3) % w] = 1
                g[i][(i + 2 * w // 3) % w] = 1
            return g

        if pattern == "center_blob":
            g = [[0] * w for _ in range(h)]
            cx, cy = h // 2, w // 2
            for dr in range(-4, 5):
                for dc in range(-4, 5):
                    if dr * dr + dc * dc <= 12 and random.random() > 0.25:
                        g[(cx + dr) % h][(cy + dc) % w] = 1
            return g

        if pattern == "cross":
            g = [[0] * w for _ in range(h)]
            cx, cy = h // 2, w // 2
            for i in range(min(w, h)):
                g[cx][i] = 1
                g[i][cy] = 1
            return g

        # empty
        return [[0] * w for _ in range(h)]

    # ── Neighbour query ──────────────────────────────────────────

    def _nbrs(self, g, r, c) -> List[int]:
        h, w = self.height, self.width
        return [
            g[(r + dr) % h][(c + dc) % w]
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            if not (dr == 0 and dc == 0)
        ]

    # ── Rule implementations ─────────────────────────────────────

    def _brian(self, g):
        """
        Brian's Brain: B2/S/D
        Dead cells with exactly 2 ON neighbours come alive.
        Alive cells enter a refractory (DYING) state.
        Dying cells go dark.
        Creates beautiful propagating wavefronts.
        """
        n = [[0] * self.width for _ in range(self.height)]
        for r in range(self.height):
            for c in range(self.width):
                cell = g[r][c]
                nbrs = self._nbrs(g, r, c)
                on_n = sum(1 for x in nbrs if x == 1)
                if cell == 0:
                    n[r][c] = 1 if on_n == 2 else 0
                elif cell == 1:
                    n[r][c] = 2
                else:
                    n[r][c] = 0
        return n

    def _life(self, g):
        """Conway's Game of Life: B3/S23"""
        n = [[0] * self.width for _ in range(self.height)]
        for r in range(self.height):
            for c in range(self.width):
                cell = g[r][c]
                on_n = sum(1 for x in self._nbrs(g, r, c) if x == 1)
                if cell == 1:
                    n[r][c] = 1 if on_n in (2, 3) else 0
                else:
                    n[r][c] = 1 if on_n == 3 else 0
        return n

    def _seeds(self, g):
        """
        Seeds: B2/S
        No cell survives; dead cells with exactly 2 live neighbours spring to life.
        Creates explosive expanding rings — dramatic musical texture.
        """
        n = [[0] * self.width for _ in range(self.height)]
        for r in range(self.height):
            for c in range(self.width):
                if g[r][c] == 0:
                    on_n = sum(1 for x in self._nbrs(g, r, c) if x == 1)
                    n[r][c] = 1 if on_n == 2 else 0
                # live cells always die → stay 0
        return n

    def _lenia(self, g):
        """
        Lenia-inspired smooth rule: B(1-4)/S(2-6)/D
        Soft birth/survival ranges create flowing, ambient patterns.
        """
        n = [[0] * self.width for _ in range(self.height)]
        for r in range(self.height):
            for c in range(self.width):
                cell = g[r][c]
                nbrs = self._nbrs(g, r, c)
                on_n  = sum(1 for x in nbrs if x == 1)
                any_n = sum(1 for x in nbrs if x != 0)
                if cell == 0:
                    n[r][c] = 1 if (1 <= on_n <= 4) or any_n == 3 else 0
                elif cell == 1:
                    n[r][c] = 1 if 2 <= any_n <= 6 else 2
                else:
                    n[r][c] = 0
        return n

    def _musical(self, g):
        """
        Custom musical rule: B(2,4)/S(3,4,5)/D
        Designed to maintain sustained harmonic clusters.
        Cells with 2 or 4 live neighbours are born; clusters of 3–5 survive.
        Richer polyphony than pure Brian's Brain.
        """
        n = [[0] * self.width for _ in range(self.height)]
        for r in range(self.height):
            for c in range(self.width):
                cell = g[r][c]
                nbrs = self._nbrs(g, r, c)
                on_n  = sum(1 for x in nbrs if x == 1)
                any_n = sum(1 for x in nbrs if x != 0)
                if cell == 0:
                    n[r][c] = 1 if on_n in (2, 4) else 0
                elif cell == 1:
                    n[r][c] = 1 if any_n in (3, 4, 5) else 2
                else:
                    n[r][c] = 0
        return n

    # ── Stepping ─────────────────────────────────────────────────

    def step(self):
        self.history.append([row[:] for row in self.grid])
        fn = {
            "brian":   self._brian,
            "life":    self._life,
            "seeds":   self._seeds,
            "lenia":   self._lenia,
            "musical": self._musical,
        }.get(self.rule, self._brian)
        self.grid = fn(self.grid)
        self.generation += 1

    def run(self, generations: int) -> List[List[List[int]]]:
        for _ in range(generations):
            self.step()
        self.history.append([row[:] for row in self.grid])
        return self.history

    def render(self, grid=None) -> str:
        g = grid or self.grid
        return "\n".join("".join(self.DISPLAY.get(c, "?") for c in row) for row in g)


# ══════════════════════════════════════════════════════════════════
#  MUSIC MAPPER
# ══════════════════════════════════════════════════════════════════

class MusicMapper:
    """
    Translates a grid state into a list of MIDI note events.

    Column → pitch (left = low, right = high, spanning multiple octaves)
    ON cell → note fires
    DYING cell → ghost note (very quiet)
    Row position → duration (lower rows = longer, more sustained)
    Active cell count in column → velocity / emphasis
    """

    def __init__(self,
                 scale: str = "pentatonic_major",
                 root: int = 60,
                 octaves: int = 3,
                 tempo: int = 120,
                 beats_per_gen: float = 0.5,
                 width: int = 16):
        self.scale_name = scale
        self.intervals = SCALES.get(scale, SCALES["pentatonic_major"])
        self.root = root
        self.octaves = octaves
        self.tempo = tempo
        self.bpg = beats_per_gen  # beats per generation
        self.width = width
        self.note_seq = self._build_notes()

    def _build_notes(self) -> List[int]:
        notes = []
        for oct in range(self.octaves):
            for iv in self.intervals:
                notes.append(self.root + oct * 12 + iv)
        return notes

    def col_to_note(self, col: int) -> int:
        idx = int(col * len(self.note_seq) / self.width)
        return self.note_seq[min(idx, len(self.note_seq) - 1)]

    def _velocity(self, on_rows: List[int], height: int) -> int:
        n = len(on_rows)
        # More active cells → louder
        base = 50 + min(50, n * 18)
        # Rows near the upper-middle are slightly brighter
        focal = height * 0.35
        weights = [1.0 - abs(r - focal) / height * 0.35 for r in on_rows]
        w = sum(weights) / len(weights)
        return max(25, min(127, int(base * w)))

    def _duration(self, on_rows: List[int], height: int) -> float:
        if not on_rows:
            return self.bpg * 0.4
        avg = sum(on_rows) / len(on_rows)
        # Low rows (high index) → longer, more legato
        frac = 0.25 + (avg / height) * 0.75
        return self.bpg * frac

    def grid_to_events(self, grid: List[List[int]]) -> List[Dict]:
        height = len(grid)
        events = []
        for col in range(self.width):
            on_rows    = [r for r in range(height) if grid[r][col] == 1]
            dying_rows = [r for r in range(height) if grid[r][col] == 2]
            note = self.col_to_note(col)

            if on_rows:
                events.append({
                    "note":      note,
                    "velocity":  self._velocity(on_rows, height),
                    "duration":  self._duration(on_rows, height),
                    "column":    col,
                    "on_rows":   on_rows,
                    "ghost":     False,
                })
            elif dying_rows:
                # Echo / ghost note — very soft, very short
                events.append({
                    "note":      note,
                    "velocity":  max(8, 22 - len(dying_rows) * 4),
                    "duration":  self.bpg * 0.18,
                    "column":    col,
                    "on_rows":   [],
                    "ghost":     True,
                })
        return events


# ══════════════════════════════════════════════════════════════════
#  MIDI GENERATION
# ══════════════════════════════════════════════════════════════════

def generate_midi(history: List[List[List[int]]],
                  mapper: MusicMapper,
                  output_path: str) -> None:
    """
    Three-track MIDI:
      Track 0 – Main melody     (all columns, channel 0 = Piano)
      Track 1 – Bass register   (left ¼ of columns, octave down, channel 1)
      Track 2 – Treble accent   (right ¼ of columns, octave up, quiet, channel 2)

    Notes are deduplicated per (track, channel, pitch, time-slot) to avoid
    midiutil's deInterleaveNotes IndexError when two columns share the same
    mapped pitch in the same generation.
    """
    MIN_DUR = 0.1   # beats — below this midiutil can mis-sequence events

    midi = MIDIFile(3)
    for t in range(3):
        midi.addTempo(t, 0, mapper.tempo)

    bass_limit   = mapper.width // 4
    treble_start = 3 * mapper.width // 4
    t_now = 0.0

    for grid in history:
        events = mapper.grid_to_events(grid)

        # Deduplicate: keep highest velocity when same (track, ch, pitch) fires
        # simultaneously.
        # Key: (track, channel, pitch) → best (velocity, duration)
        best: Dict[tuple, tuple] = {}

        for ev in events:
            col  = ev["column"]
            note = ev["note"]
            vel  = ev["velocity"]
            dur  = max(MIN_DUR, ev["duration"])
            ghost = ev["ghost"]

            # ── Main melody ──────────────────────────────
            key = (0, 0, note)
            if key not in best or vel > best[key][0]:
                best[key] = (vel, dur)

            if ghost:
                continue

            # ── Bass ─────────────────────────────────────
            if col < bass_limit:
                b_note = max(24, note - 12)
                b_vel  = max(20, int(vel * 0.75))
                b_dur  = max(MIN_DUR, mapper.bpg * 0.85)
                key = (1, 1, b_note)
                if key not in best or b_vel > best[key][0]:
                    best[key] = (b_vel, b_dur)

            # ── Treble accent ────────────────────────────
            if col >= treble_start:
                t_note = min(108, note + 12)
                t_vel  = max(12, int(vel * 0.55))
                t_dur  = max(MIN_DUR, dur * 0.45)
                key = (2, 2, t_note)
                if key not in best or t_vel > best[key][0]:
                    best[key] = (t_vel, t_dur)

        # Emit deduplicated notes
        for (track, chan, pitch), (vel, dur) in best.items():
            midi.addNote(track, chan, pitch, t_now, dur, max(1, vel))

        t_now += mapper.bpg

    _ensure_dir(output_path)
    with open(output_path, "wb") as f:
        midi.writeFile(f)


# ══════════════════════════════════════════════════════════════════
#  TEXT VISUALIZATION
# ══════════════════════════════════════════════════════════════════

def create_visualization(history: List[List[List[int]]],
                         mapper: MusicMapper,
                         rule: str,
                         pattern: str,
                         output_path: str) -> None:

    W = mapper.width
    H = len(history[0]) if history else 16
    CHARS = {0: "·", 1: "█", 2: "▒"}

    lines = []
    sep72 = "═" * 72

    # ── Banner ───────────────────────────────────────────────────
    lines += [
        "╔" + "═" * 68 + "╗",
        "║" + "  CELLULAR AUTOMATON MUSIC GENERATOR  ".center(68) + "║",
        "║" + "  Turning emergent mathematics into living sound  ".center(68) + "║",
        "╚" + "═" * 68 + "╝",
        "",
    ]

    # ── Config ───────────────────────────────────────────────────
    scale_notes = "  ".join(_NOTE_NAMES[iv % 12] for iv in mapper.intervals)
    root_name   = midi_name(mapper.root)
    dur_sec     = len(history) * mapper.bpg / (mapper.tempo / 60)

    lines += [
        "  Configuration",
        f"  ├─ Grid       : {W} × {H}  (toroidal wrapping)",
        f"  ├─ Rule       : {rule}",
        f"  ├─ Pattern    : {pattern}",
        f"  ├─ Scale      : {mapper.scale_name}  [ {scale_notes} ]",
        f"  ├─ Root note  : {root_name}",
        f"  ├─ Octaves    : {mapper.octaves}",
        f"  ├─ Tempo      : {mapper.tempo} BPM",
        f"  ├─ Beat/gen   : {mapper.bpg}",
        f"  ├─ Generations: {len(history)}",
        f"  └─ Duration   : ~{dur_sec:.1f} seconds of music",
        "",
    ]

    # ── Legend ───────────────────────────────────────────────────
    lines += [
        "  Cell legend:",
        "    ·  OFF    — silent",
        "    █  ON     — note fires (main melody, possibly bass/treble)",
        "    ▒  DYING  — ghost echo (very quiet)",
        "",
    ]

    # ── Column → Note table ──────────────────────────────────────
    col_notes = [midi_name(mapper.col_to_note(c)) for c in range(W)]
    # Build two-row table
    lines.append("  Column → Note mapping:")
    lines.append("  Col  │ " + " ".join(f"{c:>3}" for c in range(W)))
    lines.append("  Note │ " + " ".join(f"{n:>3}" for n in col_notes))
    lines.append("")

    # ── Per-generation blocks ─────────────────────────────────────
    lines.append(sep72)
    lines.append("  E V O L U T I O N")
    lines.append(sep72)

    total_events = 0
    max_poly = 0
    silent_gens = 0
    note_freq: Dict[str, int] = {}

    for gen, grid in enumerate(history):
        events   = mapper.grid_to_events(grid)
        on_evts  = [e for e in events if not e["ghost"]]
        gh_evts  = [e for e in events if e["ghost"]]
        poly     = len(on_evts)

        total_events += poly
        max_poly = max(max_poly, poly)
        if poly == 0:
            silent_gens += 1

        for e in on_evts:
            nm = midi_name(e["note"])
            note_freq[nm] = note_freq.get(nm, 0) + 1

        # Playing notes
        playing_names = [midi_name(e["note"]) for e in on_evts]
        ghost_names   = [midi_name(e["note"]) for e in gh_evts]
        notes_str     = "  ".join(playing_names) if playing_names else "(silence)"
        ghost_str     = "  ".join(ghost_names)   if ghost_names   else ""

        # Polyphony bar (up to W wide)
        poly_bar = "█" * poly + "░" * (W - poly)

        # Inner width for box
        IW = W + 4  # grid chars + 2 spaces padding each side

        lines.append("")
        lines.append(f"  ┌─ Gen {gen:03d} " + "─" * 53 + "┐")

        # Notes line (truncate if needed)
        lines.append(f"  │  Notes   {notes_str[:56]:<56}  │")
        if ghost_str:
            lines.append(f"  │  Ghost   {ghost_str[:56]:<56}  │")

        # Polyphony bar
        lines.append(f"  │  Voices  [{poly_bar:<{W}}] {poly:2d}/{W}{'':>7}  │")
        lines.append(f"  │  {'─' * 62}  │")

        # Grid rows
        for row in grid:
            row_str = " ".join(CHARS.get(c, "?") for c in row)
            lines.append(f"  │  {row_str}  │")

        lines.append(f"  └" + "─" * 64 + "┘")

    # ── Statistics ───────────────────────────────────────────────
    lines += [
        "",
        sep72,
        "  S T A T I S T I C S",
        sep72,
        f"  Total note events   : {total_events}",
        f"  Max polyphony       : {max_poly} simultaneous voices",
        f"  Silent generations  : {silent_gens} / {len(history)}",
        f"  Avg voices / gen    : {total_events / max(1, len(history)):.1f}",
        "",
        "  Note frequency:",
    ]

    if note_freq:
        max_count = max(note_freq.values())
        for nm, cnt in sorted(note_freq.items(),
                               key=lambda x: -x[1]):
            bar = "█" * int(cnt / max_count * 36)
            lines.append(f"    {nm:>4}  │{bar:<36}  {cnt}")
    else:
        lines.append("    (no notes played)")

    lines += [
        "",
        sep72,
        "  Output files:",
        "    cellular_output.mid        — open in GarageBand, VLC, MuseScore, etc.",
        "    cellular_visualization.txt — this file",
        sep72,
        "",
    ]

    _ensure_dir(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main() -> None:
    p = argparse.ArgumentParser(
        description="Cellular Automaton Music Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cellular_music.py
  python cellular_music.py --rule brian --scale pentatonic_minor --tempo 140
  python cellular_music.py --rule seeds --pattern sparse --scale blues --tempo 160
  python cellular_music.py --rule musical --pattern symmetric_4way --octaves 4
  python cellular_music.py --rule lenia --scale dorian --generations 128
        """,
    )
    p.add_argument("--width",       type=int,   default=16)
    p.add_argument("--height",      type=int,   default=16)
    p.add_argument("--rule",
                   choices=["brian","life","seeds","lenia","musical"],
                   default="brian")
    p.add_argument("--scale",
                   choices=list(SCALES.keys()),
                   default="pentatonic_major")
    p.add_argument("--root",   type=int,   default=60,
                   help="Root MIDI note (60=C4)")
    p.add_argument("--octaves",type=int,   default=3)
    p.add_argument("--tempo",  type=int,   default=120)
    p.add_argument("--beats",  type=float, default=0.5,
                   help="Beats per generation (0.25=fast, 1.0=slow)")
    p.add_argument("--generations", type=int, default=64)
    p.add_argument("--pattern",
                   choices=["random","sparse","dense","symmetric",
                            "symmetric_4way","glider","stripes",
                            "diagonal","center_blob","cross"],
                   default="random")
    p.add_argument("--seed",  type=int, default=42)
    p.add_argument("--midi-out",
                   default="sandbox_workspace/cellular_output.mid")
    p.add_argument("--viz-out",
                   default="sandbox_workspace/cellular_visualization.txt")
    args = p.parse_args()

    # ── Banner ────────────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║       CELLULAR AUTOMATON MUSIC GENERATOR                 ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    print(f"  Rule        : {args.rule}")
    print(f"  Grid        : {args.width} × {args.height}")
    print(f"  Pattern     : {args.pattern}")
    print(f"  Scale       : {args.scale}  (root {midi_name(args.root)})")
    print(f"  Tempo       : {args.tempo} BPM")
    print(f"  Generations : {args.generations}")
    print(f"  Seed        : {args.seed}")
    print()

    # ── Run automaton ─────────────────────────────────────────────
    print("  [1/3] Evolving automaton …", end="", flush=True)
    ca = CellularAutomaton(
        width=args.width, height=args.height,
        rule=args.rule, initial=args.pattern, seed=args.seed,
    )
    history = ca.run(args.generations)
    print(f" {len(history)} frames captured.")

    # ── Build mapper ──────────────────────────────────────────────
    mapper = MusicMapper(
        scale=args.scale,
        root=args.root,
        octaves=args.octaves,
        tempo=args.tempo,
        beats_per_gen=args.beats,
        width=args.width,
    )

    # ── MIDI ──────────────────────────────────────────────────────
    print("  [2/3] Writing MIDI …", end="", flush=True)
    generate_midi(history, mapper, args.midi_out)
    size = os.path.getsize(args.midi_out)
    print(f" done  →  {args.midi_out}  ({size:,} bytes)")

    # ── Visualisation ─────────────────────────────────────────────
    print("  [3/3] Writing visualization …", end="", flush=True)
    create_visualization(history, mapper, args.rule, args.pattern,
                         args.viz_out)
    size2 = os.path.getsize(args.viz_out)
    print(f" done  →  {args.viz_out}  ({size2:,} bytes)")

    # ── Summary ───────────────────────────────────────────────────
    dur = args.generations * args.beats / (args.tempo / 60)
    print()
    print(f"  Duration   : ~{dur:.1f} seconds of music")
    print(f"  Open '{args.midi_out}' in any MIDI player to listen.")
    print()


if __name__ == "__main__":
    main()
