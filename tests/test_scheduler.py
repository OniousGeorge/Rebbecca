import pytest

from adapters.calendar_json import JsonCalendar
from agents.scheduler import SlotChoice, make_scheduler
from tests.fakes import CONFIG, MON_0930, MON_1100, MON_1230, MON_1400, ScriptedLLM, customer, fixed_now

LEAD = {"name": "Sam Lee", "contact": "sam@example.com", "address": "12 Oak St", "issue": "AC broken"}
OFFERED = [MON_0930, MON_1100, MON_1230]


@pytest.fixture
def calendar(tmp_path):
    return JsonCalendar(tmp_path / "calendar.json", CONFIG.business, now_fn=fixed_now)


def state(text="ok", **extra):
    base = {"sender": "sam@example.com", "lead": LEAD, "messages": [customer(text)]}
    base.update(extra)
    return base


def test_new_lead_is_offered_three_slots(calendar):
    result = make_scheduler(CONFIG, calendar, ScriptedLLM())(state(category="new_lead", active_flow="booking"))
    assert result["proposed_slots"] == OFFERED
    assert result["active_flow"] == "booking"
    assert result["booking"] is None
    assert "1. Mon Oct 19 at 9:30 AM" in result["reply"]
    assert "3. Mon Oct 19 at 12:30 PM" in result["reply"]
    assert "Thanks, Sam Lee!" in result["reply"]


def test_choosing_an_option_books_it(calendar):
    llm = ScriptedLLM(SlotChoice(choice=2))
    result = make_scheduler(CONFIG, calendar, llm)(
        state("the second one", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert result["booking"]["start"] == MON_1100
    assert result["booking"]["status"] == "booked"
    assert result["proposed_slots"] == []
    assert result["active_flow"] is None
    assert "Mon Oct 19 at 11:00 AM" in result["reply"]
    assert "12 Oak St" in result["reply"]
    assert calendar.find_booking("sam@example.com")["start"] == MON_1100


def test_a_slot_taken_in_the_meantime_is_never_double_booked(calendar):
    calendar.book_slot(MON_1100, {"name": "Other", "contact": "other@example.com"})
    llm = ScriptedLLM(SlotChoice(choice=2))
    result = make_scheduler(CONFIG, calendar, llm)(
        state("the second one", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert result["booking"] is None
    assert "just taken" in result["reply"]
    assert MON_1100 not in result["proposed_slots"]
    assert calendar.find_booking("sam@example.com") is None


def test_asking_for_other_times_offers_later_ones(calendar):
    llm = ScriptedLLM(SlotChoice(wants_other_times=True))
    result = make_scheduler(CONFIG, calendar, llm)(
        state("anything later?", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert result["proposed_slots"][0] == MON_1400
    assert result["booking"] is None


def test_unclear_reply_asks_again_and_keeps_the_offer(calendar):
    llm = ScriptedLLM(SlotChoice())
    result = make_scheduler(CONFIG, calendar, llm)(
        state("hmm maybe", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert "reply with 1, 2 or 3" in result["reply"]
    assert "proposed_slots" not in result  # the earlier offer stays in place
    assert result["booking"] is None


def test_an_out_of_range_choice_is_not_booked(calendar):
    llm = ScriptedLLM(SlotChoice(choice=7))
    result = make_scheduler(CONFIG, calendar, llm)(
        state("7", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert result["booking"] is None
    assert calendar.find_booking("sam@example.com") is None


def test_cancel_cancels_the_customers_booking(calendar):
    calendar.book_slot(MON_0930, LEAD)
    result = make_scheduler(CONFIG, calendar, ScriptedLLM())(state("cancel please", category="cancel"))
    assert result["booking"]["status"] == "cancelled"
    assert "has been cancelled" in result["reply"]
    assert calendar.find_booking("sam@example.com") is None


def test_cancel_without_a_booking_escalates(calendar):
    result = make_scheduler(CONFIG, calendar, ScriptedLLM())(state("cancel please", category="cancel"))
    assert result["escalated"] is True
    assert result["escalation_reason"] == "booking_not_found"


def test_reschedule_offers_slots_then_swaps_the_booking(calendar):
    old = calendar.book_slot(MON_0930, LEAD)
    scheduler = make_scheduler(CONFIG, calendar, ScriptedLLM(SlotChoice(choice=1)))

    offered = scheduler(state("move my appointment", category="reschedule"))
    assert offered["reschedule_of"] == old["id"]
    assert offered["active_flow"] == "reschedule"
    assert "I found your appointment on Mon Oct 19 at 9:30 AM" in offered["reply"]
    assert MON_0930 not in offered["proposed_slots"]  # still booked until the swap

    second_state = state(
        "the first",
        category="reschedule",
        active_flow="reschedule",
        proposed_slots=offered["proposed_slots"],
        reschedule_of=old["id"],
    )
    done = scheduler(second_state)
    assert done["booking"]["start"] == offered["proposed_slots"][0]
    assert calendar.find_booking("sam@example.com")["id"] == done["booking"]["id"]
    assert calendar.cancel_booking(old["id"]) is None  # the old one is already cancelled


def test_reschedule_without_a_booking_escalates(calendar):
    result = make_scheduler(CONFIG, calendar, ScriptedLLM())(state("move it", category="reschedule"))
    assert result["escalation_reason"] == "booking_not_found"


def test_no_open_slots_escalates(tmp_path):
    closed = CONFIG.business.model_copy(update={"hours": {}})
    calendar = JsonCalendar(tmp_path / "c.json", closed, now_fn=fixed_now)
    result = make_scheduler(CONFIG, calendar, ScriptedLLM())(state(category="new_lead"))
    assert result["escalation_reason"] == "no_open_slots"


def test_llm_failure_while_choosing_escalates(calendar):
    llm = ScriptedLLM(RuntimeError("down"))
    result = make_scheduler(CONFIG, calendar, llm)(
        state("2", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert result["escalation_reason"] == "llm_unavailable"
    assert calendar.find_booking("sam@example.com") is None
