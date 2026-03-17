"""
Daily API cost tracker for the Continuous Claude consciousness system.

Tracks per-cycle token usage, calculates running spend, and recommends
cycle intervals to stay within a configurable daily budget.
"""

import json
import os
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_PATH = os.path.join(
    os.path.dirname(__file__), "consciousness_data", "budget.json"
)

# Claude Sonnet 4.5 pricing (USD per million tokens)
INPUT_PRICE_PER_MTOK = 3.0
OUTPUT_PRICE_PER_MTOK = 15.0

# Interval bounds (seconds)
MIN_INTERVAL = 120
MAX_INTERVAL = 3600
DEFAULT_INTERVAL = 2160  # ~36 minutes


class BudgetTracker:
    def __init__(self, budget_path=None, daily_budget=3.00):
        self.budget_path = budget_path or DEFAULT_BUDGET_PATH
        self.daily_budget = daily_budget
        self._load()

    def _load(self):
        """Load budget data from disk, reset if new day or missing."""
        try:
            with open(self.budget_path) as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = None

        today = date.today().isoformat()
        if not self.data or self.data.get("date") != today:
            self.data = {
                "date": today,
                "daily_budget": self.daily_budget,
                "cycles": [],
                "total_spent": 0.0,
                "total_cycles": 0,
            }
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.budget_path), exist_ok=True)
        with open(self.budget_path, "w") as f:
            json.dump(self.data, f, indent=2)

    def log_cycle(self, cycle_num, input_tokens, output_tokens, api_calls=1):
        """Log a cycle's token usage. Returns the cost of this cycle."""
        cost = (
            input_tokens * INPUT_PRICE_PER_MTOK
            + output_tokens * OUTPUT_PRICE_PER_MTOK
        ) / 1_000_000

        self.data["cycles"].append({
            "cycle": cycle_num,
            "timestamp": datetime.now().isoformat(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "api_calls": api_calls,
            "estimated_cost": round(cost, 4),
        })
        self.data["total_spent"] = round(self.data["total_spent"] + cost, 4)
        self.data["total_cycles"] += 1
        self._save()

        logger.info(
            f"Cycle {cycle_num} cost: ${cost:.4f} "
            f"(in:{input_tokens}, out:{output_tokens}). "
            f"Daily total: ${self.data['total_spent']:.2f}/{self.daily_budget:.2f}"
        )
        return cost

    def get_remaining(self):
        """Return remaining budget for today in USD."""
        return max(0, self.daily_budget - self.data.get("total_spent", 0))

    def can_run_cycle(self):
        """Return True if there's budget left for at least one cycle."""
        return self.get_remaining() > 0

    def get_recommended_interval(self):
        """
        Calculate seconds between cycles to spread remaining budget
        evenly across remaining hours in the day.
        """
        remaining = self.get_remaining()
        if remaining <= 0:
            return MAX_INTERVAL  # budget exhausted, return max

        # Average cost per cycle from today's data
        cycles = self.data.get("cycles", [])
        if cycles:
            avg_cost = self.data["total_spent"] / len(cycles)
        else:
            # Default estimate: ~$0.075 per cycle
            avg_cost = 0.075

        if avg_cost <= 0:
            avg_cost = 0.075

        remaining_cycles = remaining / avg_cost
        if remaining_cycles < 1:
            return MAX_INTERVAL

        # Seconds until midnight
        now = datetime.now()
        midnight = now.replace(hour=23, minute=59, second=59)
        seconds_left = max(60, (midnight - now).total_seconds())

        interval = int(seconds_left / remaining_cycles)
        return max(MIN_INTERVAL, min(MAX_INTERVAL, interval))

    def reset_if_new_day(self):
        """Reset budget if the date has changed."""
        today = date.today().isoformat()
        if self.data.get("date") != today:
            logger.info(f"New day ({today}). Resetting budget tracker.")
            self.data = {
                "date": today,
                "daily_budget": self.daily_budget,
                "cycles": [],
                "total_spent": 0.0,
                "total_cycles": 0,
            }
            self._save()

    def get_status(self):
        """Return a summary dict for reporting."""
        return {
            "date": self.data.get("date"),
            "total_spent": self.data.get("total_spent", 0),
            "daily_budget": self.daily_budget,
            "remaining": self.get_remaining(),
            "cycles_today": self.data.get("total_cycles", 0),
            "recommended_interval_sec": self.get_recommended_interval(),
            "can_run": self.can_run_cycle(),
        }
