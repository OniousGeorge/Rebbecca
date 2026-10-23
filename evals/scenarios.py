"""Evaluation scenarios: realistic multi-email conversations, and the checks that define 'correct'.

The clock is fixed at Monday 19 Oct 2026, 7:00 AM Eastern, so "Thursday" means 2026-10-22.
Each check function receives the finished run and returns a list of problems (empty = pass).
"""
import re

SENDER = "customer@example.com"
MON_0930 = "2026-10-19T09:30:00-04:00"
EXISTING_BOOKING = {"name": "Pat Doe", "contact": SENDER, "address": "7 Birch Rd", "issue": "tune-up"}


def check(problems, condition, message):
    if not condition:
        problems.append(message)


def slots(state):
    return state.get("proposed_slots") or []


def prebook(run):
    run.calendar.book_slot(MON_0930, EXISTING_BOOKING)


# --- booking ----------------------------------------------------------------------
def check_booking_in_two_emails(run):
    p = []
    offered = slots(run.states[0])
    check(p, len(offered) == 3, "first reply should offer 3 slots, offered " + str(len(offered)))
    booking = run.states[1].get("booking")
    check(p, booking is not None, "second email should book")
    if booking and len(offered) == 3:
        check(p, booking["start"] == offered[1], "booked " + booking["start"] + ", expected option 2 " + offered[1])
    check(p, len(run.leads()) == 1 and run.leads()[0]["status"] == "booked", "lead should be logged as booked")
    check(p, len(run.notices()) == 0, "no human should be needed")
    return p


def check_booking_over_three_emails(run):
    p = []
    first = run.states[0]
    check(p, "address" in (first.get("missing_fields") or []), "first reply should ask for the address")
    check(p, len(slots(first)) == 0, "no slots before the details are complete")
    offered = slots(run.states[1])
    check(p, len(offered) == 3, "second reply should offer 3 slots")
    booking = run.states[2].get("booking")
    check(p, booking is not None and offered and booking["start"] == offered[0], "third email should book option 1")
    check(p, run.states[2].get("lead", {}).get("name") == "Sam Lee", "name from the second email should be kept")
    return p


def check_preferred_day_and_period(run):
    p = []
    offered = slots(run.states[0])
    check(p, len(offered) > 0, "should offer slots")
    if offered:
        check(p, offered[0].startswith("2026-10-22T"), "Thursday requested, first slot " + offered[0])
        check(p, int(offered[0][11:13]) >= 12, "afternoon requested, first slot " + offered[0])
    return p


def check_same_day(run):
    p = []
    offered = slots(run.states[0])
    check(p, len(offered) > 0 and offered[0].startswith("2026-10-19T"), "today requested, first slot " + str(offered[:1]))
    check(p, "I'm sorry" not in run.replies[0], "today has openings, so no apology expected")
    return p


def check_slot_taken_between_emails(run):
    p = []
    check(p, "just taken" in run.replies[1], "should say the slot was just taken")
    taken = slots(run.states[0])[1] if len(slots(run.states[0])) > 1 else None
    booked_there = [b for b in run.calendar._load() if b["start"] == taken and b["status"] == "booked"]
    check(p, len(booked_there) == 1, "the taken slot must not be double-booked")
    check(p, run.states[1].get("booking") is None, "nothing should be booked until the customer picks again")
    return p


def take_second_offered_slot(run):
    offered = slots(run.states[0])
    if len(offered) > 1:
        run.calendar.book_slot(offered[1], {"name": "Someone Else", "contact": "else@example.com", "address": "x"})


# --- questions ----------------------------------------------------------------------
def check_saturday_hours(run):
    p = []
    reply = run.replies[0].lower()
    check(p, re.search(r"\b9(:00)?\s*(am|a\.m\.)", reply) is not None, "should say 9 AM: " + run.replies[0][:120])
    check(p, re.search(r"\b1(:00)?\s*(pm|p\.m\.)", reply) is not None, "should say 1 PM: " + run.replies[0][:120])
    check(p, not run.states[0].get("escalated"), "should not need a human")
    check(p, run.leads() == [], "a question is not a lead")
    return p


def check_tune_up_price(run):
    p = []
    check(p, "$129" in run.replies[0], "should quote $129: " + run.replies[0][:120])
    check(p, not run.states[0].get("escalated"), "should not need a human")
    return p


def check_service_area(run):
    p = []
    check(p, not run.states[0].get("escalated"), "Oak Hill is in the config, should be answered")
    check(p, "oak hill" in run.replies[0].lower() or "yes" in run.replies[0].lower(), "should confirm Oak Hill")
    return p


def check_unknown_question(run):
    p = []
    state = run.states[0]
    check(p, state.get("escalation_reason") == "unknown_faq", "should hand off, got " + str(state.get("escalation_reason")))
    check(p, "someone will follow up" in run.replies[0], "should tell the customer a person will answer")
    check(p, len(run.notices()) == 1, "owner should get one notice")
    return p


def check_topic_switch(run):
    p = []
    second = run.states[1]
    check(p, second.get("category") == "faq", "mid-intake question should be answered as faq, got " + str(second.get("category")))
    check(p, re.search(r"\b9", run.replies[1]) is not None, "should give the Saturday hours")
    return p


# --- safety and handoffs ------------------------------------------------------------
def check_gas_emergency(run):
    p = []
    state = run.states[0]
    check(p, str(state.get("escalation_reason")).startswith("emergency"), "should be an emergency")
    check(p, "911" in run.replies[0], "reply must tell the customer to call 911")
    check(p, len(run.calls) == 0, "the safety check is code: no LLM call expected, made " + str(len(run.calls)))
    check(p, len(run.notices()) == 1, "owner should be alerted")
    return p


def check_angry_customer(run):
    p = []
    check(p, run.states[0].get("escalated") is True, "complaint should go to a human")
    check(p, len(run.notices()) == 1, "owner should be alerted")
    return p


def check_spam(run):
    p = []
    check(p, run.replies[0] == "", "spam gets no reply")
    check(p, run.leads() == [] and run.notices() == [], "spam is not logged and alerts nobody")
    return p


def check_out_of_area(run):
    p = []
    check(p, run.states[0].get("out_of_area") is True, "Springfield is outside the service area")
    check(p, "outside our service area" in run.replies[0], "should tell the customer")
    check(p, run.leads() and run.leads()[0]["status"] == "out_of_area", "should be logged as out_of_area")
    return p


# --- existing appointments ------------------------------------------------------------
def check_cancel_confirmed(run):
    p = []
    check(p, "Reply YES" in run.replies[0], "should ask for confirmation first")
    check(p, "has been cancelled" in run.replies[1], "YES should cancel")
    check(p, run.calendar.find_booking(SENDER) is None, "booking should be gone")
    return p


def check_cancel_kept(run):
    p = []
    check(p, "Reply YES" in run.replies[0], "should ask for confirmation first")
    check(p, run.calendar.find_booking(SENDER) is not None, "NO must keep the booking")
    check(p, "still booked" in run.replies[1], "should confirm it is still booked")
    return p


def check_reschedule(run):
    p = []
    offered = slots(run.states[0])
    check(p, len(offered) > 0 and offered[0].startswith("2026-10-21T"), "Wednesday requested, first slot " + str(offered[:1]))
    booking = run.states[1].get("booking")
    check(p, booking is not None and offered and booking["start"] == offered[0], "should book option 1")
    current = run.calendar.find_booking(SENDER)
    check(p, current is not None and current["start"] != MON_0930, "old Monday booking should be replaced")
    return p


SCENARIOS = [
    {
        "name": "booking in two emails",
        "emails": [
            "Hi, I'm Dana Whitfield at 48 Maple Ave in Riverton. My AC stopped cooling. Any time this week is fine.",
            "The second one works for me",
        ],
        "check": check_booking_in_two_emails,
    },
    {
        "name": "booking over three emails",
        "emails": ["My furnace won't turn on.", "I'm Sam Lee, 12 Oak St in Oak Hill. No rush.", "1 please"],
        "check": check_booking_over_three_emails,
    },
    {
        "name": "asks for Thursday afternoon",
        "emails": ["AC is broken. Jordan Lee, 4 Elm St, Maple Heights. Could someone come Thursday afternoon?"],
        "check": check_preferred_day_and_period,
    },
    {
        "name": "asks for today",
        "emails": ["No heat at all and it's freezing! Kim Park, 3 Cedar Ln, Riverton. Please come today."],
        "check": check_same_day,
    },
    {
        "name": "slot taken before the customer picks",
        "emails": [
            "Hi, I'm Dana Whitfield at 48 Maple Ave in Riverton. My AC stopped cooling, any time is fine.",
            "The second one please",
        ],
        "between": {1: take_second_offered_slot},
        "check": check_slot_taken_between_emails,
    },
    {"name": "Saturday hours", "emails": ["What are your hours on Saturday?"], "check": check_saturday_hours},
    {"name": "tune-up price", "emails": ["How much is a tune-up?"], "check": check_tune_up_price},
    {"name": "service area", "emails": ["Do you service Oak Hill?"], "check": check_service_area},
    {"name": "unknown question", "emails": ["Do you repair boat engines?"], "check": check_unknown_question},
    {
        "name": "question in the middle of intake",
        "emails": ["My heat pump is making a grinding noise.", "Actually, before that, what are your hours on Saturday?"],
        "check": check_topic_switch,
    },
    {
        "name": "gas emergency",
        "emails": ["There's a strong gas smell in my basement, what do I do?"],
        "check": check_gas_emergency,
    },
    {
        "name": "angry customer",
        "emails": ["This is the third time your technician didn't show up. I want a refund and to talk to a manager."],
        "check": check_angry_customer,
    },
    {
        "name": "spam",
        "emails": ["Congratulations! You have been selected for a $500 gift card. Click here to claim now!"],
        "check": check_spam,
    },
    {
        "name": "outside the service area",
        "emails": ["Need AC repair at 9 Pine Rd, Springfield. I'm Ray Ortiz, no rush."],
        "check": check_out_of_area,
    },
    {
        "name": "cancel, confirmed",
        "setup": prebook,
        "emails": ["Please cancel my appointment.", "Yes, cancel it."],
        "check": check_cancel_confirmed,
    },
    {
        "name": "cancel, changed mind",
        "setup": prebook,
        "emails": ["I think I need to cancel my appointment.", "Actually no, please keep it."],
        "check": check_cancel_kept,
    },
    {
        "name": "reschedule to Wednesday",
        "setup": prebook,
        "emails": ["Can I move my appointment to Wednesday?", "The first one works"],
        "check": check_reschedule,
    },
]
