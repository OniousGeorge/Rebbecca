"""Router node: decides how to handle the customer's latest email."""
import re

from pydantic import BaseModel, Field

from core.config import ClientConfig
from core.llm import get_llm
from core.messages import latest_customer, transcript
from core.state import Category, State

# If a task is already open, an email that continues it keeps that category.
FLOW_TO_CATEGORY: dict[str, Category] = {
    "intake": "new_lead",
    "booking": "new_lead",
    "reschedule": "reschedule",
    "cancel": "cancel",
}

HISTORY_LIMIT = 6  # earlier messages shown to the model for context


class RouterDecision(BaseModel):
    category: Category
    reason: str = Field(description="One short sentence explaining the choice")
    continues_active_flow: bool = Field(
        description="True if this email answers or continues the task already in progress"
    )


SYSTEM_PROMPT = """You are the message router for {business}, an HVAC company.
Read the customer's latest email and decide how to handle it.

Categories:
- new_lead: wants service, a repair, a quote, or an installation
- faq: asks a general question about hours, services, service area, prices or payment
- reschedule: wants to move an existing appointment
- cancel: wants to cancel an existing appointment
- escalate: angry, a complaint, a billing or legal dispute, a safety concern, or anything a bot should not handle alone
- spam: unrelated, advertising, or automated junk

{flow_section}"""

FLOW_ACTIVE = """A '{flow}' task is already in progress with this customer.
Set continues_active_flow to true if the email answers or continues that task
(for example giving a name, an address, or choosing a time).
Set it to false only if the customer clearly changes topic."""

FLOW_NONE = "No task is in progress. Set continues_active_flow to false."


def _emergency_pattern(keywords):
    """Build one regular expression that matches any emergency phrase as whole words."""
    if len(keywords) == 0:
        return None

    escaped_phrases = []
    for keyword in keywords:
        escaped_phrases.append(re.escape(keyword))

    any_phrase = "|".join(escaped_phrases)
    return re.compile(r"\b(" + any_phrase + r")\b", re.IGNORECASE)


def _escalate(reason, errors=None):
    result = {
        "trace": ["router"],
        "category": "escalate",
        "escalated": True,
        "escalation_reason": reason,
        "reply": "",
    }
    if errors is not None:
        result["errors"] = errors
    return result


def make_router(config: ClientConfig, llm=None):
    """Build the router node. Pass a fake llm in tests; otherwise Gemini with Groq backup."""
    if llm is None:
        llm = get_llm(schema=RouterDecision)
    emergency = _emergency_pattern(config.escalation.emergency_keywords)

    def router(state: State) -> dict:
        messages = state.get("messages", [])
        latest = latest_customer(messages)
        if latest is None:
            return _escalate("empty_message")

        position, text = latest
        if text.strip() == "":
            return _escalate("empty_message")

        # Safety first, in code: an emergency never depends on the LLM.
        if emergency is not None:
            match = emergency.search(text)
            if match is not None:
                return _escalate("emergency: " + match.group(0).lower())

        # Build the prompt.
        flow = state.get("active_flow")
        if flow:
            flow_section = FLOW_ACTIVE.format(flow=flow)
        else:
            flow_section = FLOW_NONE
        system = SYSTEM_PROMPT.format(business=config.business.name, flow_section=flow_section)

        earlier = messages[:position]
        history = earlier[-HISTORY_LIMIT:]
        human = ""
        if len(history) > 0:
            human = "Earlier messages:\n" + transcript(history) + "\n\n"
        human = human + "Latest email:\n" + text

        # Ask the model. If it fails, hand the conversation to a human.
        try:
            decision = llm.invoke([("system", system), ("human", human)])
        except Exception as error:
            return _escalate("llm_unavailable", errors=["router: " + str(error)])

        # Turn the decision into state changes. Per-run fields are reset here so a
        # value left over from an earlier email in the same thread cannot leak into this run.
        update = {
            "trace": ["router"],
            "category": decision.category,
            "category_reason": decision.reason,
            "reply": "",
            "escalated": False,
            "escalation_reason": None,
            "out_of_area": False,
        }

        if decision.category == "escalate":
            # An escalation wins, even in the middle of a task.
            update["escalated"] = True
            update["escalation_reason"] = "router: " + decision.reason
        elif flow and decision.continues_active_flow:
            # The email continues the open task, so keep that task's category.
            if flow in FLOW_TO_CATEGORY:
                update["category"] = FLOW_TO_CATEGORY[flow]
        elif flow:
            # The customer changed topic, so close the old task.
            update["active_flow"] = None

        return update

    return router
