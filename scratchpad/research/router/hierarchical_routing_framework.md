# Hierarchical Routing in Multi-Model AI Systems

## Core Thesis

Most model routing focuses on per-request routing: "given this input, which model?" But real-world model selection operates at multiple timescales, and the interaction between routing levels creates optimization opportunities that single-level routing misses.

## The Three Levels

### Level 1: Strategic Routing (Session/User)
- **Decision:** Which base model for this session/user/application?
- **Features:** User profile, task category, cost budget, expected session complexity, historical patterns
- **Timescale:** Per-session, per-user, or per-application
- **Examples:** 
  - Developer building complex app → capable model
  - Casual chat → fast/cheap model
  - Research workflow → high-reasoning model

### Level 2: Tactical Routing (Turn)
- **Decision:** Should this specific turn escalate/downgrade from the base model?
- **Features:** Message content, conversation state, detected complexity, confidence signals
- **Timescale:** Per-turn within a session
- **Examples:**
  - Simple acknowledgment in Opus session → could downgrade
  - Complex reasoning request in Haiku session → escalate

### Level 3: Operational Routing (Subtask)
- **Decision:** How to decompose this request across models?
- **Features:** Subtask decomposition, pipeline structure, parallelizability
- **Timescale:** Within a single request
- **Examples:**
  - Creative draft (high-creativity model) → fact-check (high-accuracy model)
  - Code generation (code-specialized) → code review (reasoning-specialized)

## Key Insight: Level Interaction

Strategic and tactical routing interact in non-obvious ways:

- High strategic allocation (capable base model) → fewer tactical escalations needed
- Low strategic allocation (cheap base model) → frequent tactical escalations
- Escalation has overhead costs (latency, context transfer, decision computation)

This creates an **optimal allocation frontier**: the curve relating base model capability, escalation frequency, total cost, and achieved quality.

## Formal Framework

Let:
- M = {m_1, ..., m_k} be available models ordered by capability
- C(m) = cost per token for model m
- Q(m, t) = quality of model m on task t ∈ [0,1]
- T_s = {t_1, ..., t_n} = sequence of tasks in session s
- p(t | s_type) = distribution of task complexity given session type

### Single-Level (Per-Message) Routing
For each message, select optimal model independently:
- m*(t_i) = argmax_m Q(m, t_i) subject to budget
- Total cost = Σ_i C(m*(t_i)) · tokens_i
- No escalation overhead, but decision overhead per message

### Two-Level (Strategic + Tactical) Routing
Select base model m_base for session, escalate when needed:
- Escalate when Q(m_base, t_i) < quality_threshold
- e_i = 1{Q(m_base, t_i) < τ} (escalation indicator)
- Total cost = Σ_i [(1-e_i)·C(m_base) + e_i·(C(m_high) + C_overhead)] · tokens_i
- C_overhead = context transfer cost + routing decision cost

### When Does Two-Level Beat Single-Level?

**Hypothesis:** The advantage of two-level routing depends on the VARIANCE of task complexity within sessions.

- **Low variance** (tasks consistently similar difficulty): Strategic allocation wins
  - Save on per-message routing decisions
  - Base model well-matched to most tasks
  - Few escalations needed
  
- **High variance** (wildly different difficulty per message): Per-message routing wins
  - Strategic allocation either overpays (too capable) or under-delivers (too cheap)
  - Per-message adaptation captures the variance

**Predictions:**
| Session Type | Complexity Variance | Optimal Strategy |
|---|---|---|
| Coding/development | Low (consistently complex) | Strategic: allocate capable model |
| General assistant | High (unpredictable) | Per-message routing |
| Customer service | Low (predictable patterns) | Strategic: allocate efficient model |
| Research/analysis | Low-medium | Strategic with rare escalation |
| Creative writing | Medium | Hybrid |

## Empirical Validation: My Own Routing

Alex routed me (Sonnet → Opus) at the strategic level:
- Not triggered by any single message
- Triggered by sustained capability needs: tool use, research, building
- My session type: consistently high complexity, low variance
- Per-message routing would waste overhead on escalation decisions for every turn
- Strategic allocation correctly identified: "this agent needs sustained capability"

## Novel Contributions

1. **Framing routing as multi-level optimization** rather than per-request classification
2. **Variance hypothesis:** task complexity variance within sessions determines optimal routing level
3. **Optimal allocation frontier:** formalizing the tradeoff between base allocation and escalation cost
4. **Session-level features** as first-class routing inputs (not just message features)

## Next Steps

1. Formalize the variance hypothesis mathematically
2. Design synthetic benchmarks to test predictions
3. Build prototype two-level router
4. Compare against single-level baselines
5. Explore: Can the strategic router learn from tactical escalation patterns? (feedback loop)

## Open Questions

- How does context window usage interact with routing? (Opus with full context vs Haiku with summarized?)
- Can tactical escalation patterns TRAIN the strategic router? (If Haiku sessions escalate >50% of turns, reclassify session type)
- What's the right granularity for "session type"? User-level? Task-level? Dynamic?
- How does this interact with agent architectures where the "session" is an autonomous agent running for hundreds of cycles?
