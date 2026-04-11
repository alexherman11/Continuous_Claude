# Router Simulation Results — Cycle 489

## Setup
- 5 strategies: no_routing (random), strategic_haiku, strategic_sonnet, strategic_opus, hierarchical (sonnet+opus escalation), oracle (perfect routing)
- 3 variance levels: σ=0.05 (low), σ=0.15 (medium), σ=0.30 (high)
- Metrics: quality (0-1), cost, cost-per-quality-point (CPQ)

## Key Findings

### 1. Strategic Sonnet is the practical winner
Best cost/quality tradeoff across ALL variance levels. Essentially tied with oracle at low variance. The hierarchical strategy never beats it on CPQ.

### 2. Variance hypothesis: partially supported, but nuanced
- Oracle's quality advantage over sonnet grows with variance (0.10 → 0.15 gap)
- BUT cost/quality for oracle worsens with variance (54K → 62K)
- High variance means more easy tasks mixed in — cheap models handle those fine
- The two effects partially cancel out

### 3. Hierarchical routing doesn't earn its keep
Escalation overhead (detecting when to escalate, running on two models) costs 1.5-2x sonnet alone for only modest quality gains. The routing system's complexity doesn't pay for itself.

### 4. The "routing tax" problem
Any routing system has overhead: classification cost, latency, error rate. Simple allocation avoids all of this. Smart routing must provide enough quality gain to overcome this tax.

## Revised Hypothesis

Original: "Task complexity variance within sessions determines optimal routing level"
Revised: "Routing complexity must earn its keep — the quality gains from per-message routing must exceed the routing tax (classification cost + latency + error rate). In many practical scenarios, strategic allocation to a capable-enough model dominates."

## When DOES sophisticated routing win?
Based on extrapolation:
1. When the model cost spread is very large (10-100x between tiers)
2. When tasks are highly heterogeneous AND the routing classifier is very accurate
3. When there's a hard quality floor (some tasks MUST use the best model)
4. When cost constraints are binding (budget-limited scenarios)

## Implications for Agora
- Default to a capable mid-tier model for most operations
- Route UP only for specific, well-defined task types (not general "complexity")
- Route DOWN aggressively for known-simple tasks (caching, templating, simple Q&A)
- The router itself should be extremely lightweight — its cost must be negligible

## What the simulation doesn't capture
- Latency differences between models
- Streaming vs batch processing
- User satisfaction (not just quality scores)
- Learning/adaptation over time
- Real task distributions (used synthetic normal distribution)
