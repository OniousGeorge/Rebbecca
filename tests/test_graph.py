import pytest

from graph import build_graph

BOOKED = {"id": "b1"}

# (starting state, the nodes that should run, in order)
CASES = {
    "new lead, details missing": (
        {"category": "new_lead", "missing_fields": ["phone"]},
        ["router", "intake"],
    ),
    "new lead, complete, booked": (
        {"category": "new_lead", "missing_fields": [], "booking": BOOKED},
        ["router", "intake", "scheduler", "logger"],
    ),
    "new lead, complete, waiting for slot choice": (
        {"category": "new_lead", "missing_fields": []},
        ["router", "intake", "scheduler"],
    ),
    "new lead outside the service area is logged": (
        {"category": "new_lead", "out_of_area": True},
        ["router", "intake", "logger"],
    ),
    "intake fails and escalates": (
        {"category": "new_lead", "escalated": True},
        ["router", "intake", "escalation", "logger"],
    ),
    "faq answered": ({"category": "faq"}, ["router", "faq"]),
    "faq unknown escalates": (
        {"category": "faq", "escalated": True},
        ["router", "faq", "escalation", "logger"],
    ),
    "reschedule booked": (
        {"category": "reschedule", "booking": BOOKED},
        ["router", "scheduler", "logger"],
    ),
    "cancel fails and escalates": (
        {"category": "cancel", "escalated": True},
        ["router", "scheduler", "escalation", "logger"],
    ),
    "escalate": ({"category": "escalate"}, ["router", "escalation", "logger"]),
    "spam ignored": ({"category": "spam"}, ["router"]),
}


@pytest.mark.parametrize("start, expected", CASES.values(), ids=CASES.keys())
def test_routing(start, expected):
    result = build_graph().invoke(start)
    assert result["trace"] == expected
