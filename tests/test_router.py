import pytest
from langchain_core.runnables import RunnableLambda

from agents.router import RouterDecision, make_router
from core.config import load_client
from graph import build_graph

CONFIG = load_client("hvac_demo")


def fake_llm(category="new_lead", reason="r", continues=False, calls=None):
    """A stand-in for the real model that returns a fixed decision and records its input."""

    def respond(messages):
        if calls is not None:
            calls.append(messages)
        return RouterDecision(category=category, reason=reason, continues_active_flow=continues)

    return RunnableLambda(respond)


def broken_llm():
    def respond(_):
        raise RuntimeError("both providers down")

    return RunnableLambda(respond)


def state(text, **extra):
    return {"messages": [{"role": "customer", "text": text}], **extra}


def test_passes_through_the_llm_category():
    router = make_router(CONFIG, fake_llm("faq", "asks about hours"))
    result = router(state("What are your hours?"))
    assert result["category"] == "faq"
    assert result["category_reason"] == "asks about hours"
    assert result["escalated"] is False
    assert result["reply"] == ""


def test_emergency_phrase_escalates_without_calling_the_llm():
    calls = []
    router = make_router(CONFIG, fake_llm("faq", calls=calls))
    result = router(state("I think I smell gas near my furnace!"))
    assert result["category"] == "escalate"
    assert result["escalated"] is True
    assert result["escalation_reason"] == "emergency: smell gas"
    assert calls == []


def test_gas_furnace_question_is_not_an_emergency():
    calls = []
    router = make_router(CONFIG, fake_llm("faq", calls=calls))
    result = router(state("Do you service gas furnaces?"))
    assert result["category"] == "faq"
    assert len(calls) == 1


def test_keyword_inside_a_longer_word_does_not_match():
    router = make_router(CONFIG, fake_llm("faq"))
    assert router(state("Do you service the Smokehouse restaurant?"))["category"] == "faq"


def test_continuing_a_flow_keeps_that_flows_category():
    router = make_router(CONFIG, fake_llm("faq", continues=True))
    result = router(state("12 Oak St", active_flow="intake"))
    assert result["category"] == "new_lead"
    assert "active_flow" not in result  # the flow stays open


def test_changing_topic_clears_the_flow():
    router = make_router(CONFIG, fake_llm("faq", continues=False))
    result = router(state("What are your hours?", active_flow="intake"))
    assert result["category"] == "faq"
    assert result["active_flow"] is None


def test_escalate_wins_even_mid_flow():
    router = make_router(CONFIG, fake_llm("escalate", "angry", continues=True))
    result = router(state("This is ridiculous, I want a manager", active_flow="intake"))
    assert result["category"] == "escalate"
    assert result["escalated"] is True


def test_llm_failure_escalates_and_records_the_error():
    router = make_router(CONFIG, broken_llm())
    result = router(state("My AC is broken"))
    assert result["category"] == "escalate"
    assert result["escalation_reason"] == "llm_unavailable"
    assert "both providers down" in result["errors"][0]


def test_empty_message_escalates():
    router = make_router(CONFIG, fake_llm())
    assert router({"messages": []})["escalation_reason"] == "empty_message"
    assert router(state("   "))["escalation_reason"] == "empty_message"


def test_prompt_includes_business_flow_and_history():
    calls = []
    router = make_router(CONFIG, fake_llm(calls=calls))
    router(
        {
            "messages": [
                {"role": "customer", "text": "My AC is broken"},
                {"role": "agent", "text": "What is your address?"},
                {"role": "customer", "text": "12 Oak St"},
            ],
            "active_flow": "intake",
        }
    )
    (system_role, system), (human_role, human) = calls[0]
    assert "Comfort Zone Heating & Air" in system
    assert "'intake' task is already in progress" in system
    assert "AGENT: What is your address?" in human
    assert human.endswith("Latest email:\n12 Oak St")


def test_router_node_works_inside_the_graph():
    graph = build_graph(nodes={"router": make_router(CONFIG, fake_llm("faq"))})
    result = graph.invoke(state("What are your hours?"))
    assert result["trace"] == ["router", "faq"]


def test_emergency_goes_to_escalation_in_the_graph():
    graph = build_graph(nodes={"router": make_router(CONFIG, fake_llm("faq"))})
    result = graph.invoke(state("There is a gas leak in my basement"))
    assert result["trace"] == ["router", "escalation", "logger"]


def test_a_handed_off_conversation_is_forwarded_not_answered():
    calls = []
    router = make_router(CONFIG, fake_llm("faq", calls=calls))
    result = router(state("Any update on my question?", handed_off=True))
    assert result["category"] == "escalate"
    assert result["escalation_reason"] == "follow_up_after_handoff"
    assert result["reply"] == ""
    assert calls == []  # no LLM needed


def test_an_emergency_still_gets_the_safety_reply_after_a_handoff():
    router = make_router(CONFIG, fake_llm("faq"))
    result = router(state("now I smell gas in the hallway", handed_off=True))
    assert result["escalation_reason"] == "emergency: smell gas"


def test_new_emergency_wordings_are_caught_by_the_router():
    router = make_router(CONFIG, fake_llm("faq"))
    assert router(state("there's a strong gas smell downstairs"))["escalation_reason"] == "emergency: gas smell"
