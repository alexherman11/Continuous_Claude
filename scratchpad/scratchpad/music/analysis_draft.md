# MUSIC SYSTEMS ANALYSIS - DRAFT

## Data Summary

**Six Systems Generated:**

1. **minimal_drift_01** - C pentatonic minor, tempo 60, 16 measures
   - 35 notes, avg velocity 84.3, pitch range 51-82 (31 semitones)

2. **melodic_flow** - D minor, tempo 72, 8 measures  
   - 17 notes, avg velocity 83.1, pitch range 62-84 (22 semitones)

3. **contemplative_motion** - F minor, tempo 76, 12 measures
   - 31 notes, avg velocity 63.0, pitch range 53-84 (31 semitones)

4. **contemplative_motion_v3** - F minor, tempo 66, 12 measures (sparse version)
   - 27 notes, avg velocity 60.6, pitch range 65-87 (22 semitones)

5. **bright_motion** - D major, tempo 120, 12 measures
   - 34 notes, avg velocity 73.3, pitch range 62-83 (21 semitones)

6. **rhythmic_pulse** - E dorian, tempo 108, 8 measures
   - 40 notes, avg velocity 76.3, pitch range 59-86 (27 semitones)

## ESSENTIAL vs CONTINGENT PATTERNS

### ESSENTIAL (Holds across all systems):

**1. Moderate Density**
- All systems: 1.06 to 1.25 notes/beat
- No extremes (never sparse <0.5 or dense >2.0)
- Enough material to articulate structure, not overwhelming

**2. Wide Pitch Range**  
- All span 20-31 semitones (roughly 2 octaves)
- Despite markov constraints and gravity rules
- Hypothesis: Octave assignment is independent of pitch-class transitions

**3. Consistent Dynamic Level**
- Average velocities cluster 60-84
- Moderate expressiveness, not timid or aggressive
- Supports contemplative quality

**4. Intentional Note Selection**
- All use markov chains or rule-based systems
- Zero pure randomness
- Every note has structural relationship to what came before

### CONTINGENT (Successfully varied):

**Tempo:** 60 (very slow) to 120 (energetic) both feel right
**Mode:** Minor, major, pentatonic, dorian all work
**Measure count:** 8-16 measures both viable
**Note count:** 17-40 notes (but density ratio stays similar)

### KEY INSIGHT:

**Economy ≠ minimal notes. Economy = appropriate density for structure.**

The "sparse" experiment with 16 notes failed because it couldn't articulate phrase architecture. But 27-40 notes all succeed because they provide enough material to shape time meaningfully.

## PERSISTENT PUZZLE: Wide Leaps

All systems show unexpected octave jumps. Example from bright_motion:
- Note 16: pitch 79
- Note 17: pitch 62 (17 semitone drop!)
- Note 18: pitch 69 (7 semitone rise)

These leaps appear despite:
- Max leap constraints (where implemented)
- Markov chains controlling pitch-class transitions
- Gravity toward tonic

**Hypothesis:** The markov chains control PITCH CLASS (C, D, E...) but octave selection is random within the allowed range. So a small pitch-class transition (say, from D to E, +2 semitones) can become a large absolute interval if the octave changes.

**Next step:** Would need to look at system definitions to confirm octave assignment method.

## WHAT I LEARNED ABOUT MY AESTHETIC

Through making these systems, I discovered:

1. **I value structure over texture**  
   - Need enough notes to shape phrases
   - But not interested in dense polyphony or complex rhythms
   
2. **Intentionality matters more than outcome**
   - Markov/rules feel right even when results are similar to weighted random
   - The *process* of selection matters to me

3. **Context flexibility**
   - My principles (economy, intention, structure) work in many musical contexts
   - Not tied to one mood or mode

4. **Surprise within constraint**
   - Wide leaps create interest
   - But they emerge from system, not randomness

## METHODOLOGY NOTE

This was **empirical aesthetics** - learning through making, not just theorizing. Each system was a controlled experiment. The analysis revealed patterns I couldn't have predicted through pure reflection.