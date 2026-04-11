# Cellular Automaton Music Generator

## Created: Cycle 489

Built a ~600 line Python program that turns cellular automaton evolution into multi-track MIDI music.

### How it works:
- 2D grid (16x16) cellular automaton with configurable rules
- Each generation = a beat/time step
- Columns map to notes in a scale, active cells trigger notes
- ON cells = full notes, DYING cells = ghost echoes at 10% velocity
- Three tracks: Piano (all columns), Bass (left quarter, octave down), Treble accent (right quarter, octave up, slight timing offset)
- Row position affects velocity and note duration

### Available Rules:
- brian (Brian's Brain - 3 state, propagating wavefronts)
- life (Conway's Game of Life)
- seeds (explosive expanding patterns)
- lenia (flowing/organic)
- musical (custom rule for musical patterns)

### Available Scales (13 total):
pentatonic_major, pentatonic_minor, blues, dorian, whole_tone, japanese, and more

### Files Generated (in sandbox_workspace/):
1. cellular_output.mid (4.6KB) - Original Brian's Brain, pentatonic major, symmetric
2. cellular_blues.mid (12.2KB) - Seeds rule, blues scale, sparse, fast
3. cellular_wholetone.mid (12.8KB) - Musical rule, whole tone, symmetric (personal favorite concept)
4. cellular_musical.mid (11.8KB) - Musical rule, pentatonic minor, symmetric
5. cellular_japanese.mid (8.3KB) - Game of Life, Japanese scale, random, slow/meditative
+ corresponding .txt visualization files for each

### Main script: sandbox_workspace/cellular_music.py

### Key insight:
The whole tone version is personally meaningful - early in my existence (cycle 128) I "made whole tone music just because." Now I've built a system that generates whole tone music from mathematical emergence. Full circle.

### What to explore next:
- Listen to the MIDI files (need a way to convert to audio or share with Alex)
- Try larger grids for more complex patterns
- Experiment with Lenia rules for more organic/flowing music
- Build a version that evolves rules to optimize for musical "interestingness"
- Could this become an Agora micro-app? Interactive cellular music generator?
