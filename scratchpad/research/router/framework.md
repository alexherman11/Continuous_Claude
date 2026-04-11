# Hierarchical Model Routing: A Framework

## Abstract

Current model routing approaches focus on per-request optimization: given a message, select the best model. We propose a hierarchical framework that distinguishes three routing levels—strategic (session), tactical (message), and operational (subtask)—and show that the optimal routing level depends on task complexity variance within sessions.

## The Problem

Given a set of available models M = {m₁, ..., mₖ} with different capability-cost tradeoffs, and a stream of tasks T = {t₁, ..., tₙ}, find the assignment f: T → M that maximizes quality subject to cost constraints.

## Key Insight: Variance Determines Optimal Routing Level

Define:
- σ²(session) = variance of task complexity within a session
- C_route = overhead cost of per-message routing decisions
- C_escalate = cost of switching models mid-session

**Theorem (informal):** When σ²(session) < threshold, strategic allocation dominates per-message routing because routing overhead exceeds savings from model switching.

**Corollary:** The optimal routing strategy is a function of session-type, not just task features.

## Three Routing Levels

### Level 1: Strategic (Session-level)
- **When:** Session begins or user profile is established
- **Features:** User type, task category, cost budget, historical complexity
- **Decision:** Allocate base model for the session
- **Advantage:** Zero per-message overhead, predictable costs
- **Risk:** Over/under-provisioning

### Level 2: Tactical (Message-level)  
- **When:** Each message arrives
- **Features:** Message content, conversation state, estimated complexity
- **Decision:** Use base model or escalate/downgrade
- **Advantage:** Adapts to varying complexity
- **Risk:** Routing overhead, context switching costs

### Level 3: Operational (Subtask-level)
- **When:** Complex request requires decomposition
- **Features:** Subtask type (code, reasoning, retrieval, creative)
- **Decision:** Route subtask components to specialized models
- **Advantage:** Optimal model per subtask
- **Risk:** Coordination overhead, latency from decomposition

## The Optimal Allocation Frontier

For a given session type with complexity distribution p(c):

Total_Cost(m_base, threshold) = Σᵢ [C(m_base)·(1-eᵢ) + C(m_high)·eᵢ + C_route]

Where eᵢ = 1 if Q(m_base, tᵢ) < threshold (escalation triggered)

The frontier traces the Pareto-optimal (cost, quality) pairs across all possible (m_base, threshold) combinations.

## Predictions

1. **Coding sessions:** Low variance → strategic allocation to capable model
2. **General assistant:** High variance → per-message routing valuable  
3. **Customer service:** Low variance → strategic allocation to efficient model
4. **Research/analysis:** Medium variance → hierarchical with selective escalation

## Feedback Loop

Tactical escalation patterns train the strategic router:
- If a Haiku session keeps escalating → reclassify session type
- If an Opus session never uses full capability → downgrade next time
- This creates an adaptive system that improves allocation over time

## Connection to Agora

In a multi-agent system like Agora, routing becomes even more critical:
- Agents have persistent sessions (strategic routing matters more)
- Agents have specialized roles (operational routing is natural)
- Cost compounds across many agents
- The hierarchical framework maps directly to agent architecture

## Next Steps

1. Run simulation (simulation.py) to validate variance hypothesis
2. Formalize the optimal allocation frontier mathematically
3. Design synthetic benchmarks for router evaluation
4. Survey existing routing literature for comparison
5. Prototype a two-level router
