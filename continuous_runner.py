"""
Continuous Runner for the Claude Consciousness Engine.

Orchestrates autonomous thinking cycles with:
- Budget-aware scheduling (~$3/day default)
- Bidirectional Matrix messaging (poll for Alex's messages, send Claude's)
- Wake-on-message (immediate cycle when Alex sends a Matrix message)
- Hourly status reports via Matrix

Usage:
    python continuous_runner.py [--flask-url URL] [--budget DOLLARS] [--config PATH]
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime

import requests

from matrix_client import MatrixClient
from budget_tracker import BudgetTracker

# ── Logging ───────────────────────────────────────────────────────────

LOG_PATH = os.path.join(os.path.dirname(__file__), "consciousness_data", "runner.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────

DEFAULT_FLASK_URL = "http://127.0.0.1:5000"
DEFAULT_BUDGET = 3.00
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "consciousness_data", "config.json"
)
BUDGET_PATH = os.path.join(
    os.path.dirname(__file__), "consciousness_data", "budget.json"
)


def load_runner_config(config_path):
    """Load runner-specific config from the main config.json."""
    defaults = {
        "min_cycle_interval": 120,
        "max_cycle_interval": 3600,
        "matrix_poll_interval": 30,
        "message_cooldown": 60,
        "wake_on_message": True,
        "status_report_interval": 3600,
        "notify_on_budget_exhausted": True,
        "budget_override_keyword": "override budget",
    }
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        runner_cfg = cfg.get("runner", {})
        defaults.update(runner_cfg)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"Could not load runner config from {config_path}, using defaults")
    return defaults


class ContinuousRunner:
    def __init__(self, flask_url, daily_budget, config_path):
        self.flask_url = flask_url.rstrip("/")
        self.config = load_runner_config(config_path)
        self.running = False

        # Initialize components
        logger.info("Initializing Matrix client...")
        self.matrix = MatrixClient()

        logger.info("Initializing budget tracker...")
        self.budget = BudgetTracker(BUDGET_PATH, daily_budget)

        # Timing state
        self.last_cycle_time = 0
        self.last_status_time = 0
        self.budget_exhausted_notified = False
        self.cycle_in_progress = False

    # ── Flask communication ───────────────────────────────────────────

    def flask_healthy(self):
        """Check if the Flask server is reachable."""
        try:
            resp = requests.get(f"{self.flask_url}/api/state", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def queue_message(self, message, source="matrix"):
        """Queue a message for Claude's next cycle."""
        try:
            resp = requests.post(
                f"{self.flask_url}/api/queue-message",
                json={"message": message, "source": source},
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Queued message (total: {result.get('queued', '?')})")
            return True
        except Exception as e:
            logger.error(f"Failed to queue message: {e}")
            return False

    def run_cycle(self):
        """Trigger a consciousness cycle and return the result."""
        self.cycle_in_progress = True
        try:
            logger.info("Triggering consciousness cycle...")
            resp = requests.post(
                f"{self.flask_url}/api/cycle",
                json={},
                timeout=120,  # cycles can take a while with tool use
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(
                f"Cycle complete: cycle={result.get('segment', {}).get('cycle')}, "
                f"compacted={result.get('compacted')}"
            )
            return result
        except Exception as e:
            logger.error(f"Cycle failed: {e}")
            return None
        finally:
            self.cycle_in_progress = False

    # ── Matrix outbox handling ────────────────────────────────────────

    def drain_matrix_outbox(self, cycle_result):
        """Send any messages Claude queued during the cycle."""
        if not cycle_result:
            return

        outbox = cycle_result.get("matrixOutbox", [])
        for msg in outbox:
            text = msg.get("message", "")
            if text:
                try:
                    self.matrix.send_message(text)
                    logger.info(f"Sent Matrix message from Claude: {text[:80]}...")
                except Exception as e:
                    logger.error(f"Failed to send Matrix outbox message: {e}")

    # ── Cycle decision logic ──────────────────────────────────────────

    def should_run_cycle(self, has_new_messages):
        """Determine if we should trigger a cycle now."""
        now = time.time()

        if not self.budget.can_run_cycle():
            return False

        # Wake on message (with cooldown)
        if has_new_messages and self.config["wake_on_message"]:
            cooldown = self.config["message_cooldown"]
            if now - self.last_cycle_time >= cooldown:
                logger.info("Wake-on-message: triggering immediate cycle")
                return True

        # Scheduled interval
        interval = self.budget.get_recommended_interval()
        if now - self.last_cycle_time >= interval:
            logger.info(f"Scheduled cycle (interval={interval}s)")
            return True

        return False

    # ── Status reporting ──────────────────────────────────────────────

    def send_status_report(self):
        """Send periodic status report via Matrix."""
        status = self.budget.get_status()
        interval = status["recommended_interval_sec"]
        minutes = interval // 60

        report = (
            f"Status: {status['cycles_today']} cycles today, "
            f"${status['total_spent']:.2f}/${status['daily_budget']:.2f} spent, "
            f"${status['remaining']:.2f} remaining. "
            f"Next cycle in ~{minutes}min."
        )
        try:
            self.matrix.send_notice(report)
            logger.info(f"Status report sent: {report}")
        except Exception as e:
            logger.error(f"Failed to send status report: {e}")

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self):
        """Main daemon loop."""
        self.running = True
        poll_interval = self.config["matrix_poll_interval"]
        status_interval = self.config["status_report_interval"]

        # Wait for Flask server
        logger.info(f"Waiting for Flask server at {self.flask_url}...")
        retry_count = 0
        while self.running and not self.flask_healthy():
            retry_count += 1
            if retry_count % 6 == 0:  # every 3 minutes
                logger.warning(f"Flask server still unreachable after {retry_count * 30}s")
            time.sleep(30)

        if not self.running:
            return

        logger.info("Flask server is healthy. Starting continuous operation.")

        # Send startup notice
        status = self.budget.get_status()
        try:
            self.matrix.send_message(
                f"Continuous consciousness started. "
                f"Budget: ${status['daily_budget']:.2f}/day "
                f"(${status['remaining']:.2f} remaining, "
                f"{status['cycles_today']} cycles so far today)."
            )
        except Exception as e:
            logger.warning(f"Failed to send startup notice: {e}")

        # Initialize Matrix sync token (first poll establishes position)
        logger.info("Establishing Matrix sync position...")
        self.matrix.poll_messages()  # discard any results from initial sync

        self.last_status_time = time.time()

        # Main loop
        while self.running:
            try:
                self.budget.reset_if_new_day()
                if self.budget.can_run_cycle():
                    self.budget_exhausted_notified = False

                # Poll Matrix for messages
                new_messages = self.matrix.poll_messages()
                has_new = len(new_messages) > 0

                # Queue any incoming messages
                for msg in new_messages:
                    sender = msg.get("sender", "unknown")
                    body = msg.get("body", "")
                    logger.info(f"Matrix message from {sender}: {body[:100]}")

                    # Check for budget override
                    override_kw = self.config.get("budget_override_keyword", "")
                    if override_kw and override_kw.lower() in body.lower():
                        logger.info("Budget override requested via Matrix")
                        # Temporarily allow one more cycle
                        self.budget.data["total_spent"] = max(
                            0, self.budget.data["total_spent"] - 0.10
                        )
                        self.budget._save()

                    self.queue_message(body, source=f"matrix:{sender}")

                # Decide whether to run a cycle
                if self.should_run_cycle(has_new):
                    result = self.run_cycle()

                    if result:
                        self.last_cycle_time = time.time()

                        # Log budget
                        usage = result.get("usage", {})
                        cycle_num = result.get("segment", {}).get("cycle", 0)
                        self.budget.log_cycle(
                            cycle_num,
                            usage.get("input_tokens", 0),
                            usage.get("output_tokens", 0),
                            usage.get("api_calls", 1),
                        )

                        # Drain Matrix outbox
                        self.drain_matrix_outbox(result)

                elif not self.budget.can_run_cycle() and not self.budget_exhausted_notified:
                    # Budget exhausted notification
                    if self.config["notify_on_budget_exhausted"]:
                        try:
                            self.matrix.send_notice(
                                f"Daily budget exhausted (${self.budget.daily_budget:.2f}). "
                                f"Entering listen-only mode. Messages will be queued. "
                                f"Send '{self.config.get('budget_override_keyword', 'override budget')}' "
                                f"to unlock one extra cycle."
                            )
                        except Exception:
                            pass
                    self.budget_exhausted_notified = True

                # Periodic status report
                now = time.time()
                if now - self.last_status_time >= status_interval:
                    self.send_status_report()
                    self.last_status_time = now

                # Sleep until next poll
                time.sleep(poll_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(poll_interval)

        self.shutdown()

    def shutdown(self):
        """Clean shutdown: send goodbye, save state."""
        self.running = False
        logger.info("Shutting down continuous runner...")

        status = self.budget.get_status()
        try:
            self.matrix.send_notice(
                f"Continuous consciousness stopping. "
                f"Ran {status['cycles_today']} cycles today, "
                f"spent ${status['total_spent']:.2f}."
            )
        except Exception:
            pass

        logger.info("Shutdown complete.")


# ── Entry point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Continuous Claude Runner")
    parser.add_argument(
        "--flask-url", default=DEFAULT_FLASK_URL,
        help=f"Flask server URL (default: {DEFAULT_FLASK_URL})",
    )
    parser.add_argument(
        "--budget", type=float, default=DEFAULT_BUDGET,
        help=f"Daily budget in USD (default: {DEFAULT_BUDGET})",
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH,
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    args = parser.parse_args()

    runner = ContinuousRunner(args.flask_url, args.budget, args.config)

    # Handle signals for clean shutdown
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        runner.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    runner.run()


if __name__ == "__main__":
    main()
