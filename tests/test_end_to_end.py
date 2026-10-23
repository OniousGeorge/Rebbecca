"""Whole-agent tests: real graph, real nodes and adapters, scripted LLM answers."""
import csv

import pytest
from langgraph.checkpoint.memory import MemorySaver

from adapters.calendar_json import JsonCalendar
from adapters.csv_log import CsvLog
from agents.faq import FAQAnswer
from agents.intake import IntakeExtraction
from agents.router import RouterDecision
from agents.scheduler import SlotChoice
from app import build_app, make_checkpointer
from tests.fakes import CONFIG, MON_1100, RecordingMailer, ScriptedLLM, customer, fixed_now

RUN = {"configurable": {"thread_id": "t1"}}


def route(category, continues=False):
    return RouterDecision(category=category, reason="test", continues_active_flow=continues)


class Agent:
    """The assembled app plus handles on everything it touches."""

    def __init__(self, tmp_path, router=(), intake=(), faq=(), scheduler=(), checkpointer=None):
        self.calendar = JsonCalendar(tmp_path / "calendar.json", CONFIG.business, now_fn=fixed_now)
        self.leads_path = tmp_path / "leads.csv"
        self.bookings_path = tmp_path / "bookings.csv"
        self.mailer = RecordingMailer()
        self.llms = {
            "router": ScriptedLLM(*router),
            "intake": ScriptedLLM(*intake),
            "faq": ScriptedLLM(*faq),
            "scheduler": ScriptedLLM(*scheduler),
        }
        self.app = build_app(
            CONFIG,
            self.calendar,
            CsvLog(self.leads_path, self.bookings_path),
            self.mailer,
            llms=self.llms,
            checkpointer=checkpointer or MemorySaver(),
            now_fn=fixed_now,
        )

    def send(self, text):
        return self.app.invoke(
            {"thread_id": "t1", "sender": "sam@example.com", "messages": [customer(text)]}, RUN
        )

    def leads(self):
        if not self.leads_path.exists():
            return []
        with open(self.leads_path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))


def test_new_lead_is_taken_from_first_email_to_confirmed_booking(tmp_path):
    agent = Agent(
        tmp_path,
        router=[route("new_lead"), route("new_lead", continues=True), route("new_lead", continues=True)],
        intake=[
            IntakeExtraction(name="Sam Lee", issue="AC not cooling"),
            IntakeExtraction(address="12 Oak St", town="Riverton", urgency="same_day"),
        ],
        scheduler=[SlotChoice(choice=2)],
    )

    first = agent.send("Hi, my AC stopped cooling. I'm Sam Lee.")
    assert "the service address" in first["reply"]
    assert first["trace"] == ["router", "intake"]
    assert first["active_flow"] == "intake"

    second = agent.send("12 Oak St in Riverton, I need someone today")
    assert "1. Mon Oct 19 at 9:30 AM" in second["reply"]
    assert second["active_flow"] == "booking"
    assert second["lead"]["name"] == "Sam Lee"  # remembered from the first email

    third = agent.send("the second one please")
    assert "You are booked for Mon Oct 19 at 11:00 AM at 12 Oak St" in third["reply"]
    assert third["booking"]["start"] == MON_1100
    assert third["active_flow"] is None

    assert agent.calendar.find_booking("sam@example.com")["start"] == MON_1100
    lead = agent.leads()[0]
    assert lead["status"] == "booked"
    assert lead["name"] == "Sam Lee"
    assert lead["urgency"] == "same_day"
    assert len(agent.leads()) == 1  # one row for the whole conversation
    assert agent.mailer.sent == []  # nothing needed a human
    assert agent.llms["intake"].answers == []  # the third email did not call the intake LLM


def test_an_emergency_alerts_the_owner_and_tells_the_customer_to_call_911(tmp_path):
    agent = Agent(tmp_path)  # no LLM answers prepared: any LLM call would fail the test
    result = agent.send("I think I smell gas near my furnace")

    assert "911" in result["reply"]
    assert result["trace"] == ["router", "escalation", "logger"]
    assert len(agent.mailer.sent) == 1
    assert "emergency: smell gas" in agent.mailer.sent[0]["subject"]
    assert agent.leads()[0]["status"] == "escalated"


def test_a_general_question_is_answered_and_not_logged_as_a_lead(tmp_path):
    agent = Agent(
        tmp_path,
        router=[route("faq")],
        faq=[FAQAnswer(answerable=True, answer="We are open Saturdays 9 to 1.")],
    )
    result = agent.send("Are you open on Saturday?")
    assert result["reply"] == "We are open Saturdays 9 to 1."
    assert result["trace"] == ["router", "faq"]
    assert agent.leads() == []


def test_a_question_the_config_cannot_answer_goes_to_a_human(tmp_path):
    agent = Agent(
        tmp_path,
        router=[route("faq")],
        faq=[FAQAnswer(answerable=False, answer="")],
    )
    result = agent.send("Do you repair boats?")
    assert result["trace"] == ["router", "faq", "escalation", "logger"]
    assert "someone will follow up" in result["reply"]
    assert agent.mailer.sent[0]["subject"].endswith("unknown_faq")
    assert agent.leads()[0]["reason"] == "unknown_faq"


def test_spam_gets_no_reply_and_is_not_logged(tmp_path):
    agent = Agent(tmp_path, router=[route("spam")])
    result = agent.send("BUY CHEAP WATCHES")
    assert result["reply"] == ""
    assert result["trace"] == ["router"]
    assert agent.leads() == []


def test_after_a_handoff_the_agent_stops_replying_and_forwards_new_emails(tmp_path):
    agent = Agent(
        tmp_path,
        router=[route("faq")],  # only the first email reaches the LLM
        faq=[FAQAnswer(answerable=False, answer="")],
    )
    first = agent.send("Do you repair boats?")
    assert first["handed_off"] is True
    assert "someone will follow up" in first["reply"]

    second = agent.send("Hello? Any news about the boat?")
    assert second["reply"] == ""  # a person owns the conversation now
    assert len(agent.mailer.sent) == 2
    assert "wrote again" in agent.mailer.sent[1]["subject"]
    assert "Any news about the boat?" in agent.mailer.sent[1]["body"]


def test_an_old_reply_is_never_sent_again(tmp_path):
    # main.py sends whatever `reply` holds after a run, so a stale reply would be re-sent.
    agent = Agent(
        tmp_path,
        router=[route("faq"), route("spam")],
        faq=[FAQAnswer(answerable=True, answer="We open at 8 AM.")],
    )
    assert agent.send("When do you open?")["reply"] == "We open at 8 AM."
    assert agent.send("BUY CHEAP WATCHES")["reply"] == ""


def test_customer_outside_the_service_area_is_told_and_logged(tmp_path):
    agent = Agent(
        tmp_path,
        router=[route("new_lead")],
        intake=[IntakeExtraction(name="Sam", address="9 Pine Rd", town="Springfield", issue="AC", urgency="flexible")],
    )
    result = agent.send("AC broken at 9 Pine Rd, Springfield. I'm Sam, no rush")
    assert "outside our service area" in result["reply"]
    assert result["trace"] == ["router", "intake", "logger"]
    assert agent.leads()[0]["status"] == "out_of_area"


def test_cancelling_a_booking_through_the_whole_agent(tmp_path):
    agent = Agent(tmp_path, router=[route("cancel"), route("cancel", continues=True)])
    agent.calendar.book_slot(MON_1100, {"name": "Sam Lee", "contact": "sam@example.com", "address": "12 Oak St"})

    first = agent.send("Please cancel my appointment")
    assert "Reply YES to cancel it" in first["reply"]
    assert agent.calendar.find_booking("sam@example.com") is not None  # nothing cancelled yet

    second = agent.send("Yes, cancel it")
    assert "has been cancelled" in second["reply"]
    assert agent.calendar.find_booking("sam@example.com") is None
    assert agent.leads()[0]["status"] == "cancelled"


def test_a_customer_who_asks_for_today_late_in_the_day_is_told_plainly(tmp_path):
    agent = Agent(
        tmp_path,
        router=[route("new_lead")],
        intake=[
            IntakeExtraction(
                name="Sam Lee", address="12 Oak St", town="Riverton", issue="AC out", urgency="same_day",
                preferred_date="2026-10-19",
            )
        ],
    )
    agent.calendar.now_fn = lambda: fixed_now().replace(hour=19)  # 7 PM: no visits left today
    result = agent.send("AC is out, I'm Sam Lee at 12 Oak St Riverton, today if possible")
    assert "I'm sorry, we don't have an opening today." in result["reply"]
    assert "1. Tue Oct 20 at 8:00 AM" in result["reply"]


@pytest.mark.parametrize("text", ["", "   "])
def test_an_empty_email_goes_to_a_human(tmp_path, text):
    agent = Agent(tmp_path)
    result = agent.send(text)
    assert result["escalation_reason"] == "empty_message"
    assert len(agent.mailer.sent) == 1


def test_a_restart_does_not_forget_a_conversation(tmp_path):
    memory = tmp_path / "conversations.sqlite"
    before = Agent(
        tmp_path,
        router=[route("faq")],
        faq=[FAQAnswer(answerable=False, answer="")],
        checkpointer=make_checkpointer(str(memory)),
    )
    assert before.send("Do you repair boats?")["handed_off"] is True

    after_restart = Agent(tmp_path, checkpointer=make_checkpointer(str(memory)))  # a fresh process, same file
    result = after_restart.send("Hello? Any news?")
    assert result["reply"] == ""  # still remembers the conversation was handed to a person
    assert "wrote again" in after_restart.mailer.sent[0]["subject"]
