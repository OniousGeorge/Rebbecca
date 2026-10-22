from agents.faq import FAQAnswer, facts_prompt, make_faq
from tests.fakes import CONFIG, ScriptedLLM, customer


def state(text):
    return {"messages": [customer(text)]}


def test_answerable_question_gets_a_reply():
    llm = ScriptedLLM(FAQAnswer(answerable=True, answer="We open at 8 AM on weekdays."))
    result = make_faq(CONFIG, llm)(state("When do you open?"))
    assert result["reply"] == "We open at 8 AM on weekdays."
    assert result["messages"][0] == {"role": "agent", "text": "We open at 8 AM on weekdays."}
    assert "escalated" not in result


def test_unanswerable_question_escalates_without_a_reply():
    llm = ScriptedLLM(FAQAnswer(answerable=False, answer="I think maybe yes?"))
    result = make_faq(CONFIG, llm)(state("Do you repair boats?"))
    assert result["escalated"] is True
    assert result["escalation_reason"] == "unknown_faq"
    assert "reply" not in result


def test_empty_answer_counts_as_unanswerable():
    llm = ScriptedLLM(FAQAnswer(answerable=True, answer="   "))
    result = make_faq(CONFIG, llm)(state("Hmm?"))
    assert result["escalation_reason"] == "unknown_faq"


def test_llm_failure_escalates():
    llm = ScriptedLLM(RuntimeError("down"))
    result = make_faq(CONFIG, llm)(state("When do you open?"))
    assert result["escalation_reason"] == "llm_unavailable"


def test_prompt_contains_only_the_clients_facts():
    prompt = facts_prompt(CONFIG)
    assert "Saturday: 9:00 AM to 1:00 PM" in prompt
    assert "Sunday: closed" in prompt
    assert "diagnostic fee: $89" in prompt
    assert "Do you give free quotes?" in prompt
    assert "Riverton" in prompt
    assert "Never invent" in prompt
