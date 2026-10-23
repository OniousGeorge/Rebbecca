"""Records every LLM call and graph node: time, tokens, failures, and an estimated paid-tier cost.

The free tiers cost $0. The cost shown is what the same tokens would cost on the paid tier,
using PRICES below. Update PRICES when providers change their pricing.
"""
import time

from langchain_core.callbacks import BaseCallbackHandler

# US dollars per 1 million tokens: (input, output). None = price not confirmed yet.
# Checked 2026-10-22. Gemini: ai.google.dev/gemini-api/docs/pricing (official).
# Groq: GroqCloud model table as reported by third-party pricing pages (groq.com/pricing redirects).
PRICES = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "openai/gpt-oss-120b": (0.15, 0.60),
}


class Metrics(BaseCallbackHandler):
    """A LangChain callback that the LLM layer attaches to every model it builds."""

    def __init__(self):
        self.calls = []  # one dict per LLM call, in order
        self.node_times = []  # (node name, seconds), in order
        self._started = {}

    # --- LangChain callback hooks -------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        metadata = metadata or {}
        self._started[run_id] = {
            "start": time.perf_counter(),
            "provider": metadata.get("ls_provider", "unknown"),
            "model": metadata.get("ls_model_name", "unknown"),
        }

    def on_llm_end(self, response, *, run_id, **kwargs):
        call = self._finish(run_id, ok=True)
        if call is None:
            return
        usage = None
        if response.generations and response.generations[0]:
            message = getattr(response.generations[0][0], "message", None)
            if message is not None:
                usage = getattr(message, "usage_metadata", None)
        if usage:
            call["input_tokens"] = usage.get("input_tokens", 0)
            call["output_tokens"] = usage.get("output_tokens", 0)

    def on_llm_error(self, error, *, run_id, **kwargs):
        call = self._finish(run_id, ok=False)
        if call is not None:
            call["error"] = type(error).__name__ + ": " + str(error)[:200]

    def _finish(self, run_id, ok):
        started = self._started.pop(run_id, None)
        if started is None:
            return None
        call = {
            "provider": started["provider"],
            "model": started["model"],
            "ok": ok,
            "seconds": time.perf_counter() - started["start"],
            "ended_at": time.time(),  # wall clock, used to pace runs under per-minute token limits
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
        }
        self.calls.append(call)
        return call

    # --- node timing ----------------------------------------------------------
    def timed(self, name, node):
        """Wrap a graph node so its running time is recorded."""

        def run(state):
            start = time.perf_counter()
            try:
                return node(state)
            finally:
                self.node_times.append((name, time.perf_counter() - start))

        return run

    def mark(self):
        """A bookmark, so one email's numbers can be separated from the rest."""
        return len(self.calls), len(self.node_times)


def cost_of(call):
    """Estimated paid-tier cost of one call in dollars, or None if the price is unknown."""
    price = PRICES.get(call["model"])
    if price is None:
        return None
    input_price, output_price = price
    return (call["input_tokens"] * input_price + call["output_tokens"] * output_price) / 1_000_000


def summarize(calls, node_times=None):
    """Totals for a slice of calls (for example one email or one conversation)."""
    summary = {
        "llm_calls": len(calls),
        "failed_calls": 0,
        "llm_seconds": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "cost_known": True,
        "by_provider": {},
        "node_seconds": {},
    }
    for call in calls:
        summary["llm_seconds"] += call["seconds"]
        summary["input_tokens"] += call["input_tokens"]
        summary["output_tokens"] += call["output_tokens"]
        if not call["ok"]:
            summary["failed_calls"] += 1
        provider = summary["by_provider"].setdefault(call["provider"], {"calls": 0, "failed": 0, "seconds": 0.0})
        provider["calls"] += 1
        provider["seconds"] += call["seconds"]
        if not call["ok"]:
            provider["failed"] += 1
        cost = cost_of(call)
        if cost is None:
            summary["cost_known"] = False
        else:
            summary["cost_usd"] += cost
    for name, seconds in node_times or []:
        summary["node_seconds"][name] = summary["node_seconds"].get(name, 0.0) + seconds
    return summary


def describe(summary):
    """One log line, for example: 2 LLM calls (0 failed), 1.8 s LLM, 1,520 tokens, ~$0.0002"""
    text = (
        str(summary["llm_calls"])
        + " LLM calls ("
        + str(summary["failed_calls"])
        + " failed), "
        + format(summary["llm_seconds"], ".1f")
        + " s LLM, "
        + format(summary["input_tokens"] + summary["output_tokens"], ",")
        + " tokens"
    )
    if summary["cost_known"]:
        text = text + ", ~$" + format(summary["cost_usd"], ".5f")
    else:
        text = text + ", cost unknown (price not set)"
    return text


# One shared recorder for the running process. The LLM layer attaches it to every model.
recorder = Metrics()
