import pytest

from adapters.calendar_json import JsonCalendar
from agents.scheduler import SlotChoice, make_scheduler, yes_or_no
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


def test_cancel_asks_for_confirmation_before_doing_anything(calendar):
    booked = calendar.book_slot(MON_0930, LEAD)
    result = make_scheduler(CONFIG, calendar, ScriptedLLM())(state("cancel please", category="cancel"))
    assert "Reply YES to cancel it" in result["reply"]
    assert "Mon Oct 19 at 9:30 AM" in result["reply"]
    assert result["active_flow"] == "cancel"
    assert result["cancel_of"] == booked["id"]
    assert result["booking"] is None
    assert calendar.find_booking("sam@example.com") is not None  # still booked


def confirming(calendar, text):
    booked = calendar.book_slot(MON_0930, LEAD)
    scheduler = make_scheduler(CONFIG, calendar, ScriptedLLM())  # no LLM call: YES/NO is read by code
    return scheduler(state(text, category="cancel", active_flow="cancel", cancel_of=booked["id"]))


def test_yes_cancels_the_booking(calendar):
    result = confirming(calendar, "Yes please, cancel it")
    assert result["booking"]["status"] == "cancelled"
    assert "has been cancelled" in result["reply"]
    assert result["active_flow"] is None
    assert calendar.find_booking("sam@example.com") is None


def test_no_keeps_the_booking(calendar):
    result = confirming(calendar, "No, keep it")
    assert "still booked" in result["reply"]
    assert result["active_flow"] is None
    assert result["booking"] is None
    assert calendar.find_booking("sam@example.com") is not None


def test_an_unclear_answer_asks_again_and_cancels_nothing(calendar):
    result = confirming(calendar, "hmm, what time was it again?")
    assert "reply YES to cancel" in result["reply"]
    assert "active_flow" not in result  # still waiting for YES or NO
    assert calendar.find_booking("sam@example.com") is not None


@pytest.mark.parametrize(
    "text, answer",
    [
        ("Yes", "yes"),
        ("yes please", "yes"),
        ("Yep, go ahead", "yes"),
        ("OK cancel it", "yes"),
        ("No", "no"),
        ("no thanks, keep it", "no"),
        ("Don’t cancel", "no"),
        ("never mind", "no"),
        ("yes... actually no", None),
        ("what time was it?", None),
        ("I know the place", None),  # 'know' is not 'no'
    ],
)
def test_yes_or_no_reading(text, answer):
    assert yes_or_no(text) == answer


def test_cancel_without_a_booking_escalates(calendar):
    result = make_scheduler(CONFIG, calendar, ScriptedLLM())(state("cancel please", category="cancel"))
    assert result["escalated"] is True
    assert result["escalation_reason"] == "booking_not_found"


def test_reschedule_offers_slots_then_swaps_the_booking(calendar):
    old = calendar.book_slot(MON_0930, LEAD)
    # First answer: no preferred day in the request. Second answer: the customer picks option 1.
    scheduler = make_scheduler(CONFIG, calendar, ScriptedLLM(SlotChoice(), SlotChoice(choice=1)))

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


# --- the day the customer asked for ------------------------------------------------
def first_offer(calendar, **lead_extra):
    lead = dict(LEAD)
    lead.update(lead_extra)
    scheduler = make_scheduler(CONFIG, calendar, ScriptedLLM())
    return scheduler(state(category="new_lead", active_flow="booking", lead=lead))


def test_slots_start_on_the_requested_day(calendar):
    result = first_offer(calendar, preferred_date="2026-10-21")  # Wednesday
    assert result["proposed_slots"][0] == "2026-10-21T08:00:00-04:00"
    assert "I'm sorry" not in result["reply"]


def test_requested_afternoon_is_honoured(calendar):
    result = first_offer(calendar, preferred_date="2026-10-21", preferred_period="afternoon")
    assert result["proposed_slots"] == [
        "2026-10-21T12:30:00-04:00",
        "2026-10-21T14:00:00-04:00",
        "2026-10-21T15:30:00-04:00",
    ]


def test_a_closed_day_is_explained(calendar):
    result = first_offer(calendar, preferred_date="2026-10-25")  # Sunday, closed
    assert "I'm sorry, we don't have an opening on Sunday Oct 25." in result["reply"]
    assert result["proposed_slots"][0] == "2026-10-26T08:00:00-04:00"


def test_same_day_request_late_in_the_day_says_today_is_not_possible(tmp_path):
    evening = fixed_now().replace(hour=19)
    calendar = JsonCalendar(tmp_path / "c.json", CONFIG.business, now_fn=lambda: evening)
    result = first_offer(calendar, urgency="same_day")
    assert "I'm sorry, we don't have an opening today." in result["reply"]
    assert result["proposed_slots"][0] == "2026-10-20T08:00:00-04:00"


def test_same_day_urgency_offers_today_first(calendar):
    result = first_offer(calendar, urgency="same_day")
    assert result["proposed_slots"][0] == MON_0930
    assert "I'm sorry" not in result["reply"]


def test_a_past_or_malformed_requested_day_is_ignored(calendar):
    assert first_offer(calendar, preferred_date="2026-10-01")["proposed_slots"][0] == MON_0930
    assert first_offer(calendar, preferred_date="next friday")["proposed_slots"][0] == MON_0930


def test_asking_for_another_day_while_choosing_offers_that_day(calendar):
    llm = ScriptedLLM(SlotChoice(wants_other_times=True, preferred_date="2026-10-23", preferred_period="morning"))
    result = make_scheduler(CONFIG, calendar, llm)(
        state("Anything Friday morning?", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert result["proposed_slots"][0] == "2026-10-23T08:00:00-04:00"
    for slot in result["proposed_slots"]:
        assert slot.startswith("2026-10-23T") and int(slot[11:13]) < 12


def test_reschedule_request_with_a_day_offers_that_day(calendar):
    calendar.book_slot(MON_0930, LEAD)
    llm = ScriptedLLM(SlotChoice(preferred_date="2026-10-22"))
    result = make_scheduler(CONFIG, calendar, llm)(state("Can we move it to Thursday?", category="reschedule"))
    assert result["proposed_slots"][0].startswith("2026-10-22T")


def test_reschedule_still_works_when_reading_the_preferred_day_fails(calendar):
    calendar.book_slot(MON_0930, LEAD)
    llm = ScriptedLLM(RuntimeError("down"))
    result = make_scheduler(CONFIG, calendar, llm)(state("move it please", category="reschedule"))
    assert result["active_flow"] == "reschedule"
    assert len(result["proposed_slots"]) == 3


def test_today_is_given_to_the_model(calendar):
    llm = ScriptedLLM(SlotChoice(choice=1))
    make_scheduler(CONFIG, calendar, llm)(
        state("1", category="new_lead", active_flow="booking", proposed_slots=OFFERED)
    )
    assert "Today is Monday, 2026-10-19" in llm.calls[0][0][1]
