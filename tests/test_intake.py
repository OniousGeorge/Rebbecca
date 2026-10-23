import pytest

from agents.intake import IntakeExtraction, make_intake
from tests.fakes import CONFIG, ScriptedLLM, customer, fixed_now


def state(text, **extra):
    base = {"sender": "sam@example.com", "messages": [customer(text)]}
    base.update(extra)
    return base


def found(**fields):
    return IntakeExtraction(**fields)


def test_complete_details_move_on_to_booking():
    llm = ScriptedLLM(
        found(name="Sam Lee", address="12 Oak St", town="Riverton", issue="AC not cooling", urgency="same_day")
    )
    result = make_intake(CONFIG, llm)(state("I'm Sam Lee at 12 Oak St Riverton, AC is out, need today"))
    assert result["missing_fields"] == []
    assert result["active_flow"] == "booking"
    assert result["lead"]["contact"] == "sam@example.com"
    assert result["lead"]["urgency"] == "same_day"
    assert "reply" not in result


def test_missing_details_are_requested():
    llm = ScriptedLLM(found(name="Sam Lee", issue="AC not cooling"))
    result = make_intake(CONFIG, llm)(state("Hi, Sam here, my AC stopped"))
    assert result["missing_fields"] == ["address", "urgency"]
    assert result["active_flow"] == "intake"
    assert "the service address" in result["reply"]
    assert "your name" not in result["reply"]
    assert result["messages"][0]["role"] == "agent"


def test_details_accumulate_across_emails():
    llm = ScriptedLLM(found(address="12 Oak St", urgency="flexible"))
    existing = {"name": "Sam Lee", "issue": "AC not cooling", "contact": "sam@example.com"}
    result = make_intake(CONFIG, llm)(state("12 Oak St, no rush", lead=existing, active_flow="intake"))
    assert result["missing_fields"] == []
    assert result["lead"]["name"] == "Sam Lee"  # kept from the earlier email
    assert result["lead"]["address"] == "12 Oak St"


def test_an_unknown_urgency_is_ignored():
    llm = ScriptedLLM(found(name="Sam", address="1 Elm", issue="x", urgency="super duper urgent"))
    result = make_intake(CONFIG, llm)(state("help"))
    assert result["missing_fields"] == ["urgency"]


def test_town_outside_the_service_area_is_told_and_flagged():
    llm = ScriptedLLM(found(name="Sam", address="9 Pine Rd, Springfield", town="Springfield", issue="x", urgency="flexible"))
    result = make_intake(CONFIG, llm)(state("9 Pine Rd Springfield"))
    assert result["out_of_area"] is True
    assert "outside our service area" in result["reply"]
    assert "Riverton" in result["reply"]
    assert result["active_flow"] is None


def test_town_matching_is_case_insensitive():
    llm = ScriptedLLM(found(name="Sam", address="1 Elm", town="riverton", issue="x", urgency="flexible"))
    result = make_intake(CONFIG, llm)(state("1 Elm riverton"))
    assert "out_of_area" not in result


def test_no_llm_call_once_details_are_complete_and_slots_are_being_offered():
    complete = {"name": "Sam", "address": "1 Elm", "issue": "x", "urgency": "flexible"}
    llm = ScriptedLLM()  # raises if called
    result = make_intake(CONFIG, llm)(state("the second one", lead=complete, active_flow="booking"))
    assert result["missing_fields"] == []
    assert llm.calls == []


def test_llm_failure_escalates():
    llm = ScriptedLLM(RuntimeError("both providers down"))
    result = make_intake(CONFIG, llm)(state("My AC is out"))
    assert result["escalated"] is True
    assert result["escalation_reason"] == "llm_unavailable"
    assert "both providers down" in result["errors"][0]


def test_the_prompt_lists_allowed_urgency_levels_and_what_is_known():
    llm = ScriptedLLM(found())
    make_intake(CONFIG, llm)(state("hello", lead={"name": "Sam"}))
    system = llm.calls[0][0][1]
    assert "emergency, same_day, this_week, flexible" in system
    assert "Sam" in system


# --- the day the customer asked for ------------------------------------------------
def test_requested_day_and_part_of_day_are_kept():
    llm = ScriptedLLM(found(preferred_date="2026-10-23", preferred_period="Afternoon"))
    result = make_intake(CONFIG, llm, now_fn=fixed_now)(state("Friday afternoon works best"))
    assert result["lead"]["preferred_date"] == "2026-10-23"
    assert result["lead"]["preferred_period"] == "afternoon"


@pytest.mark.parametrize("bad_date", ["2026-10-01", "Friday", "2027-06-01", ""])
def test_past_malformed_or_far_off_days_are_ignored(bad_date):
    llm = ScriptedLLM(found(preferred_date=bad_date, preferred_period="evening"))
    result = make_intake(CONFIG, llm, now_fn=fixed_now)(state("some day"))
    assert "preferred_date" not in result["lead"]
    assert "preferred_period" not in result["lead"]  # only morning or afternoon are accepted


def test_the_prompt_tells_the_model_what_today_is():
    llm = ScriptedLLM(found())
    make_intake(CONFIG, llm, now_fn=fixed_now)(state("hello"))
    assert "Today is Monday, 2026-10-19" in llm.calls[0][0][1]


@pytest.mark.parametrize(
    "day, urgency",
    [("2026-10-19", "same_day"), ("2026-10-22", "this_week"), ("2026-11-05", "flexible")],
)
def test_a_named_day_answers_the_urgency_question(day, urgency):
    llm = ScriptedLLM(found(name="Jordan Lee", address="4 Elm St", issue="AC broken", preferred_date=day))
    result = make_intake(CONFIG, llm, now_fn=fixed_now)(state("Could someone come then?"))
    assert result["lead"]["urgency"] == urgency
    assert result["missing_fields"] == []


def test_a_stated_urgency_is_not_overwritten_by_the_day():
    llm = ScriptedLLM(found(urgency="emergency", preferred_date="2026-10-22"))
    result = make_intake(CONFIG, llm, now_fn=fixed_now)(state("no heat, Thursday at the latest"))
    assert result["lead"]["urgency"] == "emergency"
