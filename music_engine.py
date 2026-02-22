"""
Generative music engine for the Continuous Consciousness Engine.

Provides three tool entry points:
- create_system(): Define and save a generative music system as JSON
- generate_music(): Produce MIDI output from a saved system
- analyze_music(): Structural analysis of generated music

Music systems are JSON definitions specifying scale, tempo, note selection
rules, rhythm, dynamics, and phrase structure. MIDI files are written
directly (binary), while system definitions and analysis sidecars use
the FileManager's text-based write_scratchpad.

Dependencies: midiutil (for MIDI file generation only)
"""

import json
import os
import random
from datetime import datetime


# ── Constants ────────────────────────────────────────────────────────────

SCALE_DEFINITIONS = {
    "major":            [0, 2, 4, 5, 7, 9, 11],
    "minor":            [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor":   [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor":    [0, 2, 3, 5, 7, 9, 11],
    "dorian":           [0, 2, 3, 5, 7, 9, 10],
    "phrygian":         [0, 1, 3, 5, 7, 8, 10],
    "lydian":           [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":       [0, 2, 4, 5, 7, 9, 10],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "blues":            [0, 3, 5, 6, 7, 10],
    "whole_tone":       [0, 2, 4, 6, 8, 10],
    "chromatic":        list(range(12)),
}

NOTE_NAMES = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
    "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

PC_TO_NAME = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

REQUIRED_SYSTEM_FIELDS = {
    "global": ["tempo", "root", "scale", "measures"],
    "note_selection": ["method"],
    "rhythm": ["durations", "duration_weights"],
}


# ── Scale / Pitch Helpers ────────────────────────────────────────────────

def get_scale_pitches(root, scale_name, oct_low, oct_high):
    """Generate all MIDI note numbers for a scale across an octave range."""
    root_pc = NOTE_NAMES.get(root, 0)
    offsets = SCALE_DEFINITIONS.get(scale_name, SCALE_DEFINITIONS["major"])
    pitches = []
    for octave in range(oct_low, oct_high + 1):
        for offset in offsets:
            midi_num = (octave + 1) * 12 + root_pc + offset
            if 0 <= midi_num <= 127:
                pitches.append(midi_num)
    return sorted(set(pitches))


def pitch_to_name(midi_num):
    """Convert MIDI number to note name, e.g. 60 -> 'C4'."""
    octave = (midi_num // 12) - 1
    pc = midi_num % 12
    return f"{PC_TO_NAME[pc]}{octave}"


# ── Validation ───────────────────────────────────────────────────────────

def validate_system(system_def):
    """Validate a music system definition. Returns (ok: bool, message: str)."""
    for section, fields in REQUIRED_SYSTEM_FIELDS.items():
        if section not in system_def:
            return False, f"Missing required section: '{section}'"
        for field in fields:
            if field not in system_def[section]:
                return False, f"Missing required field: '{section}.{field}'"

    root = system_def["global"]["root"]
    if root not in NOTE_NAMES:
        return False, f"Unknown root note: '{root}'. Use: {list(NOTE_NAMES.keys())}"

    scale = system_def["global"]["scale"]
    if scale not in SCALE_DEFINITIONS:
        return False, f"Unknown scale: '{scale}'. Available: {list(SCALE_DEFINITIONS.keys())}"

    method = system_def["note_selection"]["method"]
    if method not in ("weighted_random", "markov_chain", "rule_based"):
        return False, f"Unknown method: '{method}'. Use: weighted_random, markov_chain, rule_based"

    durs = system_def["rhythm"]["durations"]
    weights = system_def["rhythm"]["duration_weights"]
    if len(durs) != len(weights):
        return False, f"durations ({len(durs)}) and duration_weights ({len(weights)}) must match in length"

    tempo = system_def["global"]["tempo"]
    if not (20 <= tempo <= 300):
        return False, f"Tempo {tempo} out of range (20-300 BPM)"

    measures = system_def["global"]["measures"]
    if not (1 <= measures <= 128):
        return False, f"Measures {measures} out of range (1-128)"

    return True, "ok"


# ── Note Selection ───────────────────────────────────────────────────────

def weighted_random_select(scale_pitches, root_pc, config, rng):
    """Select a note using weighted random by scale degree."""
    if rng.random() < config.get("rest_probability", 0.1):
        return None

    w_cfg = config.get("weights", {})
    w_root = w_cfg.get("root", 2.0)
    w_fifth = w_cfg.get("fifth", 1.5)
    w_other = w_cfg.get("other", 1.0)

    weights = []
    for p in scale_pitches:
        offset = (p % 12 - root_pc) % 12
        if offset == 0:
            weights.append(w_root)
        elif offset == 7:
            weights.append(w_fifth)
        else:
            weights.append(w_other)

    return rng.choices(scale_pitches, weights=weights, k=1)[0]


def markov_select(current_pc_offset, scale_pitches, prev_pitch, config, rng):
    """Select a note using Markov chain pitch-class transitions."""
    transitions = config.get("transitions", {})
    key = str(current_pc_offset)

    if key not in transitions:
        return rng.choice(scale_pitches)

    row = transitions[key]
    targets = list(row.keys())
    probs = list(row.values())

    next_pc_offset = int(rng.choices(targets, weights=probs, k=1)[0])

    # Find candidate pitches with this pitch class
    root_pc = scale_pitches[0] % 12 if scale_pitches else 0
    target_pc = (root_pc + next_pc_offset) % 12
    candidates = [p for p in scale_pitches if p % 12 == target_pc]

    if not candidates:
        return rng.choice(scale_pitches)

    if prev_pitch is not None:
        distances = [abs(c - prev_pitch) for c in candidates]
        inv_weights = [1.0 / (d + 1) for d in distances]
        return rng.choices(candidates, weights=inv_weights, k=1)[0]

    return rng.choice(candidates)


def rule_based_select(scale_pitches, prev_pitch, recent_pitches, rules, rng):
    """Select a note by filtering/weighting candidates through rules."""
    candidates = list(scale_pitches)
    weights = [1.0] * len(candidates)

    for rule in rules:
        rtype = rule.get("type", "")

        if rtype == "max_leap" and prev_pitch is not None:
            max_s = rule.get("semitones", 12)
            filtered = [(c, w) for c, w in zip(candidates, weights)
                        if abs(c - prev_pitch) <= max_s]
            if filtered:
                candidates, weights = zip(*filtered)
                candidates, weights = list(candidates), list(weights)

        elif rtype == "direction_bias" and prev_pitch is not None:
            bias = rule.get("bias", "upward")
            strength = rule.get("strength", 0.5)
            for i, c in enumerate(candidates):
                if bias == "upward" and c > prev_pitch:
                    weights[i] *= (1.0 + strength)
                elif bias == "downward" and c < prev_pitch:
                    weights[i] *= (1.0 + strength)

        elif rtype == "gravity":
            target_pc = rule.get("target_pc", 0)
            strength = rule.get("strength", 0.3)
            for i, c in enumerate(candidates):
                dist = min(abs((c % 12) - target_pc), 12 - abs((c % 12) - target_pc))
                weights[i] *= (1.0 + strength * (6 - dist) / 6)

        elif rtype == "avoid_repeat":
            max_consec = rule.get("max_consecutive", 2)
            if len(recent_pitches) >= max_consec:
                tail = recent_pitches[-max_consec:]
                if len(set(tail)) == 1:
                    repeat_pitch = tail[0]
                    filtered = [(c, w) for c, w in zip(candidates, weights)
                                if c != repeat_pitch]
                    if filtered:
                        candidates, weights = zip(*filtered)
                        candidates, weights = list(candidates), list(weights)

    if not candidates:
        return rng.choice(scale_pitches)

    return rng.choices(candidates, weights=weights, k=1)[0]


# ── Rhythm & Dynamics Helpers ────────────────────────────────────────────

def select_duration(rhythm_cfg, rng):
    """Select a note duration from the weighted duration list."""
    durations = rhythm_cfg.get("durations", [1.0])
    weights = rhythm_cfg.get("duration_weights", [1.0] * len(durations))
    return rng.choices(durations, weights=weights, k=1)[0]


def apply_swing(beat, swing, beats_per_measure):
    """Apply swing offset to off-beat positions."""
    if swing <= 0:
        return beat
    # Swing affects the "and" of each beat (the half-beat positions)
    beat_fraction = beat % 1.0
    if 0.4 < beat_fraction < 0.6:
        return beat + swing * 0.5
    return beat


def get_phrase_density(beat, beats_per_measure, structure_cfg):
    """Get the density value for the current beat position based on phrase structure."""
    phrases = structure_cfg.get("phrases", [])
    if not phrases:
        return 0.8  # default density

    current_measure = int(beat / beats_per_measure) + 1
    for phrase in phrases:
        m_range = phrase.get("measures", [1, 1])
        if len(m_range) == 2 and m_range[0] <= current_measure <= m_range[1]:
            return phrase.get("density", 0.8)

    return 0.8


def calculate_velocity(beat, raw_beat, total_beats, beats_per_measure, dyn_cfg, rng):
    """Calculate note velocity with accents and fades."""
    base = dyn_cfg.get("base_velocity", 80)
    v_range = dyn_cfg.get("velocity_range", [40, 120])

    # Random variation around base
    velocity = base + rng.randint(-10, 10)

    # Accent on strong beats
    accent_beats = dyn_cfg.get("accent_beats", [1])
    beat_in_measure = (int(raw_beat) % beats_per_measure) + 1
    if beat_in_measure in accent_beats:
        velocity += dyn_cfg.get("accent_boost", 15)

    # Fade in
    fade_in = dyn_cfg.get("fade_in_measures", 0)
    if fade_in > 0:
        fade_in_beats = fade_in * beats_per_measure
        if raw_beat < fade_in_beats:
            velocity = int(velocity * (raw_beat / fade_in_beats))

    # Fade out
    fade_out = dyn_cfg.get("fade_out_measures", 0)
    if fade_out > 0:
        fade_out_beats = fade_out * beats_per_measure
        remaining = total_beats - raw_beat
        if remaining < fade_out_beats:
            velocity = int(velocity * (remaining / fade_out_beats))

    return max(v_range[0], min(v_range[1], velocity))


# ── Generation Core ──────────────────────────────────────────────────────

def deep_merge(base, overrides):
    """Recursively merge overrides into base dict."""
    result = dict(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def generate_from_system(system_def, seed=None):
    """
    Generate a sequence of notes from a music system definition.
    Returns (notes: list[dict], meta: dict).
    """
    rng = random.Random(seed)

    g = system_def["global"]
    tempo = g["tempo"]
    time_sig = g.get("time_signature", [4, 4])
    beats_per_measure = time_sig[0]
    measures = g["measures"]
    total_beats = measures * beats_per_measure

    root = g["root"]
    scale = g["scale"]
    oct_range = g.get("octave_range", [3, 5])
    scale_pitches = get_scale_pitches(root, scale, oct_range[0], oct_range[1])

    if not scale_pitches:
        return [], {"tempo": tempo, "total_beats": total_beats,
                    "beats_per_measure": beats_per_measure}

    root_pc = NOTE_NAMES[root]
    note_method = system_def["note_selection"]["method"]
    note_config = system_def["note_selection"].get(note_method, {})

    rhythm_cfg = system_def.get("rhythm", {})
    dyn_cfg = system_def.get("dynamics", {})
    structure_cfg = system_def.get("structure", {})

    # Markov state
    if note_method == "markov_chain":
        start = note_config.get("start_on", "root")
        if start == "root":
            current_pc_offset = 0
        else:
            current_pc_offset = rng.choice(
                SCALE_DEFINITIONS.get(scale, [0])
            )
    else:
        current_pc_offset = 0

    notes = []
    current_beat = 0.0
    prev_pitch = None
    recent_pitches = []

    while current_beat < total_beats:
        density = get_phrase_density(current_beat, beats_per_measure, structure_cfg)
        dur = select_duration(rhythm_cfg, rng)

        swing = rhythm_cfg.get("swing", 0.0)
        adjusted_beat = apply_swing(current_beat, swing, beats_per_measure)

        # Density check — skip with probability (1 - density)
        if rng.random() > density:
            current_beat += dur
            continue

        # Pitch selection
        if note_method == "weighted_random":
            pitch = weighted_random_select(scale_pitches, root_pc, note_config, rng)
        elif note_method == "markov_chain":
            pitch = markov_select(
                current_pc_offset, scale_pitches, prev_pitch, note_config, rng
            )
            if pitch is not None:
                current_pc_offset = (pitch % 12 - root_pc) % 12
        elif note_method == "rule_based":
            pitch = rule_based_select(
                scale_pitches, prev_pitch, recent_pitches,
                note_config.get("rules", []), rng
            )
        else:
            pitch = rng.choice(scale_pitches)

        if pitch is not None:
            actual_dur = min(dur, total_beats - current_beat)
            velocity = calculate_velocity(
                adjusted_beat, current_beat, total_beats,
                beats_per_measure, dyn_cfg, rng
            )

            notes.append({
                "pitch": pitch,
                "start": round(adjusted_beat, 4),
                "duration": round(actual_dur, 4),
                "velocity": velocity,
            })

            prev_pitch = pitch
            recent_pitches.append(pitch)
            if len(recent_pitches) > 10:
                recent_pitches = recent_pitches[-10:]

        current_beat += dur

    meta = {
        "tempo": tempo,
        "total_beats": total_beats,
        "beats_per_measure": beats_per_measure,
    }
    return notes, meta


def write_midi_file(notes, meta, output_path):
    """Write notes to a MIDI file using midiutil."""
    from midiutil import MIDIFile

    midi = MIDIFile(1)
    track = 0
    channel = 0

    midi.addTempo(track, 0, meta["tempo"])

    for note in notes:
        midi.addNote(
            track, channel,
            note["pitch"], note["start"],
            note["duration"], note["velocity"],
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        midi.writeFile(f)


def get_next_output_version(output_dir, system_name):
    """Find the next available version number for output files."""
    prefix = f"{system_name}_v"
    existing = []
    if os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            if f.startswith(prefix) and f.endswith(".mid"):
                try:
                    version_str = f[len(prefix):-4]
                    existing.append(int(version_str))
                except ValueError:
                    pass
    return max(existing, default=0) + 1


# ── Analysis Functions ───────────────────────────────────────────────────

def pitch_class_histogram(notes):
    """Distribution of pitch classes as percentages."""
    counts = [0] * 12
    for n in notes:
        counts[n["pitch"] % 12] += 1
    total = sum(counts) or 1
    return {
        PC_TO_NAME[i]: round(counts[i] / total * 100, 1)
        for i in range(12) if counts[i] > 0
    }


def interval_analysis(notes):
    """Analysis of melodic intervals between consecutive notes."""
    if len(notes) < 2:
        return {"note": "Need at least 2 notes for interval analysis"}

    sorted_notes = sorted(notes, key=lambda n: n["start"])
    intervals = [
        sorted_notes[i]["pitch"] - sorted_notes[i - 1]["pitch"]
        for i in range(1, len(sorted_notes))
    ]

    categories = {"unison": 0, "step": 0, "skip": 0, "leap": 0, "wide_leap": 0}
    for iv in intervals:
        a = abs(iv)
        if a == 0:
            categories["unison"] += 1
        elif a <= 2:
            categories["step"] += 1
        elif a <= 4:
            categories["skip"] += 1
        elif a <= 7:
            categories["leap"] += 1
        else:
            categories["wide_leap"] += 1

    total = len(intervals) or 1
    direction = {
        "ascending": sum(1 for iv in intervals if iv > 0),
        "descending": sum(1 for iv in intervals if iv < 0),
        "unison": sum(1 for iv in intervals if iv == 0),
    }

    return {
        "categories": {k: round(v / total * 100, 1) for k, v in categories.items()},
        "direction": {k: round(v / total * 100, 1) for k, v in direction.items()},
        "average_interval": round(sum(abs(iv) for iv in intervals) / total, 2),
        "largest_leap": max(abs(iv) for iv in intervals),
    }


def rhythm_analysis(notes):
    """Analysis of note durations."""
    durations = [n["duration"] for n in notes]
    dur_counts = {}
    for d in durations:
        d_r = round(d, 2)
        dur_counts[d_r] = dur_counts.get(d_r, 0) + 1

    total = len(durations) or 1

    classifications = {}
    for d, count in dur_counts.items():
        if d <= 0.25:
            label = "sixteenth"
        elif d <= 0.5:
            label = "eighth"
        elif d <= 1.0:
            label = "quarter"
        elif d <= 2.0:
            label = "half"
        else:
            label = "whole_or_longer"
        classifications[label] = classifications.get(label, 0) + count

    return {
        "duration_distribution": {str(k): v for k, v in sorted(dur_counts.items())},
        "classifications": {k: round(v / total * 100, 1) for k, v in classifications.items()},
        "average_duration": round(sum(durations) / total, 3),
        "shortest": round(min(durations), 4),
        "longest": round(max(durations), 4),
    }


def density_analysis(notes, total_beats):
    """Density and range metrics."""
    note_count = len(notes)
    notes_per_beat = round(note_count / total_beats, 3) if total_beats > 0 else 0

    sounding_beats = sum(n["duration"] for n in notes)
    coverage = round(sounding_beats / total_beats * 100, 1) if total_beats > 0 else 0

    beats_per_measure = 4
    num_measures = max(1, int(total_beats / beats_per_measure))
    measure_counts = [0] * num_measures
    for n in notes:
        m = min(int(n["start"] / beats_per_measure), num_measures - 1)
        measure_counts[m] += 1

    pitches = [n["pitch"] for n in notes]
    return {
        "notes_per_beat": notes_per_beat,
        "coverage_percent": coverage,
        "total_notes": note_count,
        "pitch_range": {
            "lowest": pitch_to_name(min(pitches)),
            "highest": pitch_to_name(max(pitches)),
            "span_semitones": max(pitches) - min(pitches),
        },
        "unique_pitches": len(set(pitches)),
        "measure_note_counts": measure_counts,
    }


def velocity_analysis(notes):
    """Velocity (dynamics) metrics."""
    velocities = [n["velocity"] for n in notes]
    return {
        "min": min(velocities),
        "max": max(velocities),
        "average": round(sum(velocities) / len(velocities), 1),
        "dynamic_range": max(velocities) - min(velocities),
    }


# ── Tool Entry Points ───────────────────────────────────────────────────

def create_system(name, system_json, file_mgr):
    """Create and save a music system definition."""
    try:
        system_def = json.loads(system_json)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}

    valid, msg = validate_system(system_def)
    if not valid:
        return {"error": f"Invalid system: {msg}"}

    system_def["name"] = name
    now = datetime.now().isoformat()
    system_def.setdefault("version", 1)
    system_def.setdefault("created_at", now)
    system_def["updated_at"] = now

    path = f"music/systems/{name}.json"
    content = json.dumps(system_def, indent=2)
    result = file_mgr.write_scratchpad(path, content)

    if "error" in result:
        return result

    g = system_def["global"]
    oct_range = g.get("octave_range", [3, 5])
    pitches = get_scale_pitches(g["root"], g["scale"], oct_range[0], oct_range[1])

    return {
        "status": "ok",
        "path": result["path"],
        "system_name": name,
        "summary": {
            "tempo": g["tempo"],
            "key": f"{g['root']} {g['scale']}",
            "measures": g["measures"],
            "note_method": system_def["note_selection"]["method"],
            "available_pitches": len(pitches),
            "pitch_range": f"{pitch_to_name(min(pitches))} - {pitch_to_name(max(pitches))}" if pitches else "none",
        },
    }


def generate(system_name, file_mgr, seed=None, overrides_json=None):
    """Generate MIDI output from a named music system."""
    sys_path = f"music/systems/{system_name}.json"
    read_result = file_mgr.read_file("scratchpad", sys_path)
    if "error" in read_result:
        return {"error": f"System not found: {system_name}. Create it first with create_music_system."}

    try:
        system_def = json.loads(read_result["content"])
    except json.JSONDecodeError as e:
        return {"error": f"Corrupt system file: {e}"}

    if overrides_json:
        try:
            overrides = json.loads(overrides_json)
            system_def = deep_merge(system_def, overrides)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid overrides JSON: {e}"}

    try:
        notes, meta = generate_from_system(system_def, seed=seed)
    except Exception as e:
        return {"error": f"Generation failed: {e}"}

    if not notes:
        return {
            "status": "warning",
            "message": "Generation produced zero notes. Try lowering rest_probability or increasing density.",
        }

    # Determine output version
    output_dir = os.path.join(file_mgr.scratchpad_dir, "music", "output")
    version = get_next_output_version(output_dir, system_name)

    # Write MIDI (binary — direct file write)
    midi_filename = f"{system_name}_v{version:03d}.mid"
    midi_path = os.path.join(output_dir, midi_filename)
    write_midi_file(notes, meta, midi_path)

    # Write JSON sidecar (text — via file_mgr)
    sidecar = {
        "system_name": system_name,
        "system_version": system_def.get("version", 1),
        "output_version": version,
        "seed": seed,
        "generated_at": datetime.now().isoformat(),
        "global": {
            "tempo": meta["tempo"],
            "root": system_def["global"]["root"],
            "scale": system_def["global"]["scale"],
            "total_measures": system_def["global"]["measures"],
            "total_beats": meta["total_beats"],
        },
        "notes": notes,
        "summary": {
            "note_count": len(notes),
            "total_duration_beats": meta["total_beats"],
            "total_duration_seconds": round(meta["total_beats"] / meta["tempo"] * 60, 1),
            "pitch_range": [min(n["pitch"] for n in notes), max(n["pitch"] for n in notes)],
            "unique_pitches": len(set(n["pitch"] for n in notes)),
            "average_velocity": round(sum(n["velocity"] for n in notes) / len(notes), 1),
        },
    }
    sidecar_path = f"music/output/{system_name}_v{version:03d}.json"
    file_mgr.write_scratchpad(sidecar_path, json.dumps(sidecar, indent=2))

    return {
        "status": "ok",
        "midi_path": f"scratchpad/music/output/{midi_filename}",
        "sidecar_path": f"scratchpad/{sidecar_path}",
        "version": version,
        "summary": sidecar["summary"],
    }


def analyze(file_path, metrics, file_mgr):
    """Analyze a generated music file using its JSON sidecar."""
    json_path = file_path
    if json_path.endswith(".mid"):
        json_path = json_path[:-4] + ".json"
    elif not json_path.endswith(".json"):
        json_path += ".json"

    read_result = file_mgr.read_file("scratchpad", json_path, limit=500000)
    if "error" in read_result:
        return {"error": f"Note data not found at scratchpad/{json_path}. Generate music first."}

    try:
        sidecar = json.loads(read_result["content"])
    except json.JSONDecodeError as e:
        return {"error": f"Corrupt note data: {e}"}

    notes = sidecar.get("notes", [])
    if not notes:
        return {"error": "No notes found in the data file."}

    total_beats = sidecar.get("global", {}).get("total_beats", 0)
    want_all = "all" in metrics

    analysis = {
        "file": file_path,
        "system": sidecar.get("system_name"),
        "version": sidecar.get("output_version"),
        "note_count": len(notes),
    }

    if want_all or "pitch_classes" in metrics:
        analysis["pitch_classes"] = pitch_class_histogram(notes)
    if want_all or "intervals" in metrics:
        analysis["intervals"] = interval_analysis(notes)
    if want_all or "rhythm" in metrics:
        analysis["rhythm"] = rhythm_analysis(notes)
    if want_all or "density" in metrics:
        analysis["density"] = density_analysis(notes, total_beats)
    if want_all or "velocity" in metrics:
        analysis["velocity"] = velocity_analysis(notes)

    return {"status": "ok", "analysis": analysis}
