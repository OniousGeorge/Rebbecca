"""Scheduler node: offers open times, books the one the customer picks, handles cancel/reschedule."""
import re
from datetime import datetime

from pydantic import BaseModel, Field

from adapters.calendar_json import describe_slot
from core.config import ClientConfig
from core.dates import describe_day, parse_period, parse_preferred_date, period_of, today_text
from core.failures import record_failure
from core.llm import get_llm
from core.messages import latest_customer
from core.state import State, reply_update

SLOTS_PER_OFFER = 3
URGENT_LEVELS = ("emergency", "same_day")  # with no day named, these customers are offered today first

# Read by code, never by the LLM: a misunderstood reply must not cancel an appointment.
YES_PHRASES = ["yes", "yeah", "yep", "yup", "sure", "confirm", "confirmed", "ok", "okay", "please do", "go ahead", "cancel it", "do it"]
NO_PHRASES = ["no", "nope", "don't", "dont", "do not", "keep", "keep it", "never mind", "nevermind"]


class SlotChoice(BaseModel):
    choice: int | None = Field(
        default=None, description="The number of the option the customer chose, or null if they did not clearly choose"
    )
    wants_other_times: bool = Field(default=False, description="True if the customer asks for different times")
    preferred_date: str | None = Field(
        default=None, description="A day the customer asks for instead, as YYYY-MM-DD, worked out from today's date"
    )
    preferred_period: str | None = Field(
        default=None, description="'morning' or 'afternoon' if the customer says which, otherwise empty"
    )


CHOICE_PROMPT = """Today is {today}.
A customer was offered these appointment times:
{options}

Decide what the customer wants in their latest email.
- choice: the option number they picked, or empty if they did not clearly pick one.
- wants_other_times: true if they ask for different times.
- preferred_date: if they name a day they would prefer, that day as YYYY-MM-DD, otherwise empty.
- preferred_period: 'morning' or 'afternoon' if they say which, otherwise empty."""

RESCHEDULE_PROMPT = """Today is {today}.
A customer wants to move their existing appointment. From their latest email, fill in only:
- preferred_date: the day they want instead, as YYYY-MM-DD, if they named one, otherwise empty.
- preferred_period: 'morning' or 'afternoon' if they said which, otherwise empty.
Leave choice empty and wants_other_times false."""


def _escalation(reason, errors=None):
    result = {
        "trace": ["scheduler"],
        "booking": None,
        "escalated": True,
        "escalation_reason": reason,
    }
    if errors is not None:
        result["errors"] = errors
    return result


def yes_or_no(text):
    """'yes', 'no', or None when the reply is unclear (or says both)."""
    cleaned = text.lower().replace("’", "'")
    padded = " " + " ".join(re.findall(r"[a-z']+", cleaned)) + " "
    said_yes = False
    for phrase in YES_PHRASES:
        if " " + phrase + " " in padded:
            said_yes = True
    said_no = False
    for phrase in NO_PHRASES:
        if " " + phrase + " " in padded:
            said_no = True
    if said_yes and not said_no:
        return "yes"
    if said_no and not said_yes:
        return "no"
    return None


def _unavailable_note(first_slot, wanted_date, wanted_period, today):
    """'I'm sorry, we don't have an opening today. ' when the offer cannot match what was asked."""
    if wanted_date is None and wanted_period is None:
        return ""
    first = datetime.fromisoformat(first_slot)
    date_ok = wanted_date is None or first.date() == wanted_date
    period_ok = wanted_period is None or period_of(first) == wanted_period
    if date_ok and period_ok:
        return ""
    if wanted_period is not None:
        text = "I'm sorry, we don't have a " + wanted_period + " opening"
    else:
        text = "I'm sorry, we don't have an opening"
    if wanted_date is not None:
        text = text + " " + describe_day(wanted_date, today)
    return text + ". "


def _latest_text(state):
    latest = latest_customer(state.get("messages", []))
    if latest is None:
        return ""
    return latest[1]


def make_scheduler(config: ClientConfig, calendar, llm=None):
    if llm is None:
        llm = get_llm(schema=SlotChoice)

    def offer(state, intro, after=None, reschedule_of=None, wanted_date=None, wanted_period=None):
        """Offer open slots (near the day the customer wants, if any) and remember them for the reply."""
        slots = calendar.get_open_slots(
            count=SLOTS_PER_OFFER, after=after, start_date=wanted_date, period=wanted_period
        )
        if len(slots) == 0 and wanted_period is not None:
            # Nothing in that part of the day: offer any time instead.
            slots = calendar.get_open_slots(count=SLOTS_PER_OFFER, after=after, start_date=wanted_date)
        if len(slots) == 0:
            return _escalation("no_open_slots")

        note = _unavailable_note(slots[0], wanted_date, wanted_period, calendar.now().date())
        lines = [intro + note + "Here are the next available appointment times:"]
        for number, slot in enumerate(slots, start=1):
            lines.append(str(number) + ". " + describe_slot(slot))
        lines.append("Reply with the number that works for you, or tell me if you would like other times.")

        if reschedule_of is None:
            reschedule_of = state.get("reschedule_of")
        flow = "booking"
        if reschedule_of is not None:
            flow = "reschedule"

        update = reply_update("\n".join(lines))
        update.update(
            {
                "trace": ["scheduler"],
                "booking": None,
                "proposed_slots": slots,
                "active_flow": flow,
                "reschedule_of": reschedule_of,
                "cancel_of": None,
            }
        )
        return update

    # --- cancel: ask first, then act on a clear YES -----------------------------
    def ask_cancel(state):
        existing = calendar.find_booking(state.get("sender", ""))
        if existing is None:
            return _escalation("booking_not_found")
        update = reply_update(
            "I found your appointment on "
            + describe_slot(existing["start"])
            + " at "
            + existing["address"]
            + ". Reply YES to cancel it, or NO to keep it. If you would rather move it, just tell me a day that works."
        )
        update.update(
            {
                "trace": ["scheduler"],
                "booking": None,
                "proposed_slots": [],
                "active_flow": "cancel",
                "cancel_of": existing["id"],
            }
        )
        return update

    def confirm_cancel(state):
        answer = yes_or_no(_latest_text(state))
        if answer is None:
            update = reply_update("Just to confirm: reply YES to cancel your appointment, or NO to keep it.")
            update.update({"trace": ["scheduler"], "booking": None})
            return update

        if answer == "no":
            update = reply_update("No problem, your appointment is still booked. See you then!")
            update.update({"trace": ["scheduler"], "booking": None, "active_flow": None, "cancel_of": None})
            return update

        cancelled = calendar.cancel_booking(state["cancel_of"])
        if cancelled is None:
            return _escalation("booking_not_found")
        update = reply_update("Your appointment on " + describe_slot(cancelled["start"]) + " has been cancelled.")
        update.update(
            {
                "trace": ["scheduler"],
                "booking": cancelled,
                "proposed_slots": [],
                "active_flow": None,
                "reschedule_of": None,
                "cancel_of": None,
            }
        )
        return update

    # --- reschedule -------------------------------------------------------------
    def start_reschedule(state):
        existing = calendar.find_booking(state.get("sender", ""))
        if existing is None:
            return _escalation("booking_not_found")

        today = calendar.now().date()
        wanted_date = None
        wanted_period = None
        system = RESCHEDULE_PROMPT.format(today=today_text(calendar.now()))
        try:
            wish = llm.invoke([("system", system), ("human", "Latest email:\n" + _latest_text(state))])
            wanted_date = parse_preferred_date(wish.preferred_date, today)
            wanted_period = parse_period(wish.preferred_period)
        except Exception as error:
            # Not knowing the preferred day is not worth a handoff: offer the earliest times instead.
            record_failure("scheduler", error)

        intro = "I found your appointment on " + describe_slot(existing["start"]) + ". "
        return offer(
            state, intro, reschedule_of=existing["id"], wanted_date=wanted_date, wanted_period=wanted_period
        )

    # --- the customer answers an offer ------------------------------------------
    def handle_choice(state):
        slots = state.get("proposed_slots", [])
        options = []
        for number, slot in enumerate(slots, start=1):
            options.append(str(number) + ". " + describe_slot(slot))
        system = CHOICE_PROMPT.format(today=today_text(calendar.now()), options="\n".join(options))

        try:
            decision = llm.invoke([("system", system), ("human", "Latest email:\n" + _latest_text(state))])
        except Exception as error:
            return _escalation("llm_unavailable", errors=[record_failure("scheduler", error)])

        if decision.choice is not None and 1 <= decision.choice <= len(slots):
            lead = dict(state.get("lead", {}))
            lead.setdefault("contact", state.get("sender", ""))
            booking = calendar.book_slot(slots[decision.choice - 1], lead)
            if booking is None:
                # Someone else got it first, or it is no longer bookable.
                return offer(state, "Sorry, that time was just taken. ")

            old_id = state.get("reschedule_of")
            if old_id is not None:
                calendar.cancel_booking(old_id)

            update = reply_update(
                "You are booked for " + describe_slot(booking["start"]) + " at " + booking["address"] + ". See you then!"
            )
            update.update(
                {
                    "trace": ["scheduler"],
                    "booking": booking,
                    "proposed_slots": [],
                    "active_flow": None,
                    "reschedule_of": None,
                }
            )
            return update

        today = calendar.now().date()
        wanted_date = parse_preferred_date(decision.preferred_date, today)
        wanted_period = parse_period(decision.preferred_period)
        if wanted_date is not None or wanted_period is not None:
            return offer(state, "No problem. ", wanted_date=wanted_date, wanted_period=wanted_period)
        if decision.wants_other_times:
            return offer(state, "No problem. ", after=slots[-1])

        update = reply_update(
            "Sorry, I did not catch which time you want. Please reply with 1, 2 or 3, or ask for other times."
        )
        update.update({"trace": ["scheduler"], "booking": None})
        return update

    def scheduler(state: State) -> dict:
        category = state.get("category")
        flow = state.get("active_flow")
        slots = state.get("proposed_slots", [])

        if category == "cancel":
            if flow == "cancel" and state.get("cancel_of"):
                return confirm_cancel(state)
            return ask_cancel(state)
        if category == "reschedule" and not (flow == "reschedule" and len(slots) > 0):
            return start_reschedule(state)
        if len(slots) == 0:
            lead = state.get("lead", {})
            today = calendar.now().date()
            wanted_date = parse_preferred_date(lead.get("preferred_date"), today)
            if wanted_date is None and lead.get("urgency") in URGENT_LEVELS:
                wanted_date = today
            wanted_period = parse_period(lead.get("preferred_period"))
            return offer(
                state,
                "Thanks, " + lead.get("name", "there") + "! ",
                wanted_date=wanted_date,
                wanted_period=wanted_period,
            )
        return handle_choice(state)

    return scheduler
