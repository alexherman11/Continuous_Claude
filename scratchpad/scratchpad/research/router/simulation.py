"""
Hierarchical Router Simulation
Tests: When does strategic (session-level) routing beat tactical (per-message) routing?

Core hypothesis: The value of hierarchical routing depends on task complexity 
variance within sessions. Low variance → strategic allocation wins. 
High variance → per-message routing wins.
"""

import random
import math
from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class Model:
    name: str
    cost_per_token: float
    capability: float  # 0-1, higher = more capable
    latency_ms: float

@dataclass 
class Task:
    complexity: float  # 0-1, higher = harder
    tokens: int

# Define models
MODELS = [
    Model("haiku", 0.25, 0.4, 50),
    Model("sonnet", 1.0, 0.7, 150),
    Model("opus", 5.0, 0.95, 300),
]

def quality(model: Model, task: Task) -> float:
    """Quality of model on task. Sigmoid around capability vs complexity."""
    diff = model.capability - task.complexity
    return 1.0 / (1.0 + math.exp(-10 * diff))

def generate_session(n_messages: int, mean_complexity: float, variance: float) -> List[Task]:
    """Generate a session with given complexity distribution."""
    tasks = []
    for _ in range(n_messages):
        c = random.gauss(mean_complexity, variance)
        c = max(0.0, min(1.0, c))  # clamp
        tokens = random.randint(100, 2000)
        tasks.append(Task(complexity=c, tokens=tokens))
    return tasks

# === ROUTING STRATEGIES ===

def oracle_router(tasks: List[Task]) -> List[Tuple[Model, Task]]:
    """Perfect per-message routing (upper bound)."""
    results = []
    for task in tasks:
        # Pick cheapest model that achieves quality > 0.8
        best = None
        for model in MODELS:
            if quality(model, task) > 0.8:
                if best is None or model.cost_per_token < best.cost_per_token:
                    best = model
        if best is None:
            best = MODELS[-1]  # fallback to most capable
        results.append((best, task))
    return results

def strategic_router(tasks: List[Task], base_model: Model) -> List[Tuple[Model, Task]]:
    """Session-level: pick one model for all messages."""
    return [(base_model, task) for task in tasks]

def hierarchical_router(tasks: List[Task], base_model: Model, 
                         escalation_model: Model, threshold: float = 0.6) -> List[Tuple[Model, Task]]:
    """Two-level: base model with escalation when quality drops."""
    results = []
    for task in tasks:
        if quality(base_model, task) < threshold:
            results.append((escalation_model, task))
        else:
            results.append((base_model, task))
    return results

def evaluate(assignments: List[Tuple[Model, Task]]) -> dict:
    """Evaluate a routing strategy's assignments."""
    total_cost = 0
    total_quality = 0
    for model, task in assignments:
        total_cost += model.cost_per_token * task.tokens
        total_quality += quality(model, task)
    n = len(assignments)
    return {
        "total_cost": total_cost,
        "avg_quality": total_quality / n,
        "cost_per_quality": total_cost / (total_quality / n) if total_quality > 0 else float('inf'),
    }

# === EXPERIMENT ===

def run_experiment(n_sessions=100, n_messages=20):
    """Test routing strategies across different variance levels."""
    variances = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    
    print(f"{'Variance':<10} {'Strategy':<20} {'Avg Cost':<12} {'Avg Quality':<12} {'Cost/Quality':<12}")
    print("-" * 66)
    
    for var in variances:
        results = {s: {"cost": 0, "quality": 0, "cpq": 0} for s in 
                   ["oracle", "strategic_haiku", "strategic_sonnet", "strategic_opus", "hierarchical"]}
        
        for _ in range(n_sessions):
            mean_c = random.uniform(0.3, 0.7)
            tasks = generate_session(n_messages, mean_c, var)
            
            strategies = {
                "oracle": oracle_router(tasks),
                "strategic_haiku": strategic_router(tasks, MODELS[0]),
                "strategic_sonnet": strategic_router(tasks, MODELS[1]),
                "strategic_opus": strategic_router(tasks, MODELS[2]),
                "hierarchical": hierarchical_router(tasks, MODELS[1], MODELS[2]),
            }
            
            for name, assignments in strategies.items():
                ev = evaluate(assignments)
                results[name]["cost"] += ev["total_cost"]
                results[name]["quality"] += ev["avg_quality"]
                results[name]["cpq"] += ev["cost_per_quality"]
        
        for name, r in results.items():
            print(f"{var:<10.2f} {name:<20} {r['cost']/n_sessions:<12.1f} "
                  f"{r['quality']/n_sessions:<12.3f} {r['cpq']/n_sessions:<12.1f}")
        print()

if __name__ == "__main__":
    run_experiment()
