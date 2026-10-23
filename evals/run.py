"""Runs the evaluation scenarios against the real LLMs and reports pass rate, speed and cost.

    python -m evals.run                      3 runs, primary from .env (default gemini)
    python -m evals.run --primary groq       force Groq as the primary model
    python -m evals.run --runs 1 --only spam run one scenario once

Results are saved to evals/results/ as JSON. Uses free-tier quota: about 2 LLM calls per email.
"""
import argparse
import csv
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

from adapters.calendar_json import JsonCalendar
from adapters.csv_log import CsvLog
from app import build_app
from core import metrics
from core.config import load_client
from evals.scenarios import SCENARIOS, SENDER

FIXED_NOW = datetime.fromisoformat("2026-10-19T07:00:00-04:00")  # Monday 7:00 AM Eastern
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Stay under the free tiers: Groq allows 8,000 tokens per minute on this model;
# Gemini's per-minute request limit is not published, so stay conservative.
TOKEN_BUDGET_PER_MINUTE = {"groq": 6000, "gemini": 200000}
CALL_BUDGET_PER_MINUTE = {"groq": 25, "gemini": 11}


def fixed_now():
    return FIXED_NOW


class RecordingMailer:
    def __init__(self):
        self.sent = []

    def send_notice(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})


class Run:
    """Everything one scenario produced, for its check function."""

    def __init__(self, folder):
        self.folder = folder
        self.calendar = None
        self.mailer = RecordingMailer()
        self.states = []
        self.replies = []
        self.seconds = []
        self.calls = []

    def _rows(self, name):
        path = self.folder / name
        if not path.exists():
            return []
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def leads(self):
        return self._rows("leads.csv")

    def notices(self):
        return self.mailer.sent


def pace(primary):
    """Wait until the last minute's usage leaves room for one more email."""
    while True:
        now = time.time()
        recent = [c for c in metrics.recorder.calls if now - c["ended_at"] < 60]
        tokens = sum(c["input_tokens"] + c["output_tokens"] for c in recent)
        if tokens < TOKEN_BUDGET_PER_MINUTE[primary] and len(recent) < CALL_BUDGET_PER_MINUTE[primary]:
            return
        time.sleep(2)


def run_scenario(config, scenario, primary):
    folder = Path(tempfile.mkdtemp())
    run = Run(folder)
    run.calendar = JsonCalendar(folder / "calendar.json", config.business, now_fn=fixed_now)
    graph = build_app(
        config,
        run.calendar,
        CsvLog(folder / "leads.csv", folder / "bookings.csv"),
        run.mailer,
        checkpointer=MemorySaver(),
        now_fn=fixed_now,
    )
    if scenario.get("setup"):
        scenario["setup"](run)

    calls_before, _ = metrics.recorder.mark()
    problems = []
    between = scenario.get("between", {})
    for index, text in enumerate(scenario["emails"]):
        if index in between:
            between[index](run)
        pace(primary)
        start = time.perf_counter()
        try:
            state = graph.invoke(
                {"thread_id": "eval", "sender": SENDER, "messages": [{"role": "customer", "text": text}]},
                {"configurable": {"thread_id": "eval"}},
            )
        except Exception as error:
            problems.append("crashed on email " + str(index + 1) + ": " + type(error).__name__ + ": " + str(error))
            break
        run.seconds.append(time.perf_counter() - start)
        run.states.append(state)
        run.replies.append(state.get("reply", ""))
    run.calls = metrics.recorder.calls[calls_before:]

    if len(problems) == 0:
        try:
            problems = scenario["check"](run)
        except Exception as error:
            problems = ["check failed: " + type(error).__name__ + ": " + str(error)]

    summary = metrics.summarize(run.calls)
    return {
        "scenario": scenario["name"],
        "passed": len(problems) == 0,
        "problems": problems,
        "seconds_per_email": run.seconds,
        "llm_calls": summary["llm_calls"],
        "failed_calls": summary["failed_calls"],
        "by_provider": summary["by_provider"],
        "tokens": summary["input_tokens"] + summary["output_tokens"],
        "cost_usd": summary["cost_usd"] if summary["cost_known"] else None,
        "replies": run.replies,
    }


def report(results, primary, runs):
    names = []
    for result in results:
        if result["scenario"] not in names:
            names.append(result["scenario"])

    print("\n=== " + primary + " as primary, " + str(runs) + " run(s) per scenario ===")
    for name in names:
        mine = [r for r in results if r["scenario"] == name]
        marks = "".join("." if r["passed"] else "F" for r in mine)
        print("  " + marks.ljust(4) + name)

    passed = sum(1 for r in results if r["passed"])
    email_times = [s for r in results for s in r["seconds_per_email"]]
    costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    calls = sum(r["llm_calls"] for r in results)
    failed = sum(r["failed_calls"] for r in results)
    providers = {}
    for r in results:
        for name, numbers in r["by_provider"].items():
            providers[name] = providers.get(name, 0) + numbers["calls"]

    print("\nPass rate: " + str(passed) + "/" + str(len(results)) + " (" + format(100 * passed / len(results), ".0f") + "%)")
    if email_times:
        email_times.sort()
        print(
            "Seconds per email: average "
            + format(sum(email_times) / len(email_times), ".1f")
            + ", slowest "
            + format(email_times[-1], ".1f")
        )
    if costs:
        print("Paid-tier cost per conversation: average $" + format(sum(costs) / len(costs), ".5f"))
    print("LLM calls: " + str(calls) + " (" + str(failed) + " failed and fell over), by provider: " + str(providers))

    failures = [r for r in results if not r["passed"]]
    if failures:
        print("\nFailures:")
        for r in failures:
            print("  - " + r["scenario"] + ": " + "; ".join(r["problems"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--primary", choices=["gemini", "groq"], default=None)
    parser.add_argument("--only", default=None, help="run only scenarios whose name contains this text")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    if args.primary:
        os.environ["LLM_PRIMARY"] = args.primary
    primary = os.environ.get("LLM_PRIMARY", "gemini").lower()
    config = load_client("hvac_demo")

    chosen = SCENARIOS
    if args.only:
        chosen = [s for s in SCENARIOS if args.only.lower() in s["name"].lower()]

    results = []
    for run_number in range(1, args.runs + 1):
        for scenario in chosen:
            result = run_scenario(config, scenario, primary)
            result["run"] = run_number
            results.append(result)
            mark = "pass" if result["passed"] else "FAIL"
            print("run " + str(run_number) + "  " + mark + "  " + scenario["name"], flush=True)

    report(results, primary, args.runs)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / (stamp + "-" + primary + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"primary": primary, "runs": args.runs, "results": results}, f, indent=2)
    print("\nSaved " + str(out))


if __name__ == "__main__":
    main()
