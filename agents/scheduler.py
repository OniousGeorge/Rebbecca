"""Scheduler node: offers open times, books the one the customer picks, handles cancel/reschedule."""
from pydantic import BaseModel, Field

from adapters.calendar_json import describe_slot
from core.config import ClientConfig
from core.llm import get_llm
from core.messages import latest_customer
from core.state import State, reply_update

SLOTS_PER_OFFER = 3


class SlotChoice(BaseModel):
    choice: int | None = Field(
        default=None, description="The number of the option the customer chose, or null if they did not clearly choose"
    )
    wants_other_times: bool = Field(default=False, description="True if the customer asks for different times")


SYSTEM_PROMPT = """A customer was offered these appointment times:
{options}

Decide which option the customer chose in their latest email.
Set choice to the option number, or leave it empty if they did not clearly pick one.
Set wants_other_times to true if they ask for different times."""


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


def make_scheduler(config: ClientConfig, calendar, llm=None):
    if llm is None:
        llm = get_llm(schema=SlotChoice)

    def offer(state, intro, after=None, reschedule_of=None):
        """Offer the next open slots and remember them for the customer's reply."""
        slots = calendar.get_open_slots(count=SLOTS_PER_OFFER, after=after)
        if len(slots) == 0:
            return _escalation("no_open_slots")

        lines = [intro + "Here are the next available appointment times:"]
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
            }
        )
        return update

    def cancel(state):
        existing = calendar.find_booking(state.get("sender", ""))
        if existing is None:
            return _escalation("booking_not_found")
        cancelled = calendar.cancel_booking(existing["id"])
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
            }
        )
        return update

    def start_reschedule(state):
        existing = calendar.find_booking(state.get("sender", ""))
        if existing is None:
            return _escalation("booking_not_found")
        intro = "I found your appointment on " + describe_slot(existing["start"]) + ". "
        return offer(state, intro, reschedule_of=existing["id"])

    def handle_choice(state):
        slots = state.get("proposed_slots", [])
        latest = latest_customer(state.get("messages", []))
        text = ""
        if latest is not None:
            text = latest[1]

        options = []
        for number, slot in enumerate(slots, start=1):
            options.append(str(number) + ". " + describe_slot(slot))
        system = SYSTEM_PROMPT.format(options="\n".join(options))

        try:
            decision = llm.invoke([("system", system), ("human", "Latest email:\n" + text)])
        except Exception as error:
            return _escalation("llm_unavailable", errors=["scheduler: " + str(error)])

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
            return cancel(state)
        if category == "reschedule" and not (flow == "reschedule" and len(slots) > 0):
            return start_reschedule(state)
        if len(slots) == 0:
            lead = state.get("lead", {})
            name = lead.get("name", "there")
            return offer(state, "Thanks, " + name + "! ")
        return handle_choice(state)

    return scheduler
