"""The shared state every node in the graph reads from and writes to."""
from operator import add
from typing import Annotated, Literal, TypedDict

Category = Literal["new_lead", "faq", "reschedule", "cancel", "escalate", "spam"]

# Escalation reason used when a customer writes again after the conversation was handed to a person.
FOLLOW_UP_AFTER_HANDOFF = "follow_up_after_handoff"


class Message(TypedDict):
    role: str  # "customer" or "agent"
    text: str


class State(TypedDict, total=False):
    # Identity of the email conversation
    thread_id: str
    message_id: str
    sender: str

    # Annotated[..., add] is a reducer: a node's returned list is appended to the
    # existing one instead of replacing it.
    messages: Annotated[list[Message], add]
    errors: Annotated[list[str], add]
    trace: Annotated[list[str], add]  # node names in the order they ran

    # Router output and flow tracking
    category: Category
    category_reason: str  # the router's one-line explanation, for debugging
    active_flow: str | None  # "intake" | "booking" | "reschedule" | "cancel" | None

    # Intake
    lead: dict  # name, contact, phone, address, town, issue, urgency, preferred_date, preferred_period
    missing_fields: list[str]
    out_of_area: bool

    # Scheduling
    proposed_slots: list[str]  # ISO 8601 strings
    booking: dict | None  # {id, start, end, status}; set only by the run that booked/cancelled
    reschedule_of: str | None  # id of the booking being replaced
    cancel_of: str | None  # id of the booking waiting for the customer's YES to cancel

    # Escalation
    escalated: bool  # this run needs a person
    escalation_reason: str | None
    handed_off: bool  # a person owns this conversation now: the agent stops replying on it

    # What to send back to the customer (empty when this run has nothing to say)
    reply: str


def reply_update(text: str) -> dict:
    """State update for a node that speaks: sets the reply and records it in the conversation."""
    return {"reply": text, "messages": [{"role": "agent", "text": text}]}
