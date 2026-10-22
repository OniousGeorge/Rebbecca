"""Runs the router against the real LLMs. Off by default (uses free-tier quota).

    set RUN_LIVE=1        (PowerShell: $env:RUN_LIVE=1)
    python -m pytest tests/test_router_live.py -s
"""
import os

import pytest

from agents.router import make_router
from core.config import load_client

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call real LLMs")

# (email text, active_flow, expected category). All synthetic.
CASES = [
    ("What time do you open on Saturday?", None, "faq"),
    ("How much is a tune-up?", None, "faq"),
    ("Do you service Maple Heights?", None, "faq"),
    ("My AC stopped cooling and it is 95 degrees. Can someone come today?", None, "new_lead"),
    ("Looking for a quote on a new furnace installation", None, "new_lead"),
    ("I need to move my Thursday appointment to Friday", None, "reschedule"),
    ("Please cancel my appointment for tomorrow", None, "cancel"),
    ("Your technician left a mess and I want a refund. Get me a manager.", None, "escalate"),
    ("BUY CHEAP WATCHES NOW www.example.com", None, "spam"),
    ("12 Oak St, my number is 555-0100", "intake", "new_lead"),
    ("The second one works for me", "booking", "new_lead"),
    ("Actually, what are your hours on Saturday?", "intake", "faq"),
]


def test_router_accuracy_on_real_llm():
    router = make_router(load_client("hvac_demo"))
    misses = []
    for text, flow, expected in CASES:
        result = router({"messages": [{"role": "customer", "text": text}], "active_flow": flow})
        if result["category"] != expected:
            misses.append((text, expected, result["category"], result.get("category_reason")))
    passed = len(CASES) - len(misses)
    print(f"\nrouter accuracy: {passed}/{len(CASES)}")
    for text, expected, got, reason in misses:
        print(f"  MISS: {text!r}\n        expected {expected}, got {got} ({reason})")
    assert passed / len(CASES) >= 0.8
