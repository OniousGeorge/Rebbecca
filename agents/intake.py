"""Intake node: collects the details needed to book a visit, over one or more emails."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from core.config import ClientConfig
from core.dates import parse_period, parse_preferred_date, today_text
from core.failures import record_failure
from core.llm import get_llm
from core.messages import latest_customer, transcript
from core.state import State, reply_update

REQUIRED_FIELDS = ["name", "address", "issue", "urgency"]
FIELD_LABELS = {
    "name": "your name",
    "address": "the service address",
    "issue": "a short description of the problem",
    "urgency": "how urgent it is (an emergency, same day, this week, or flexible)",
}
HISTORY_LIMIT = 6


class IntakeExtraction(BaseModel):
    name: str | None = Field(default=None, description="The customer's name, only if they stated it")
    phone: str | None = Field(default=None, description="A phone number, only if they gave one")
    address: str | None = Field(default=None, description="The service address, only if they stated it")
    town: str | None = Field(default=None, description="The town or city of the service address, if stated")
    issue: str | None = Field(default=None, description="What is wrong or what service they want")
    urgency: str | None = Field(default=None, description="One of the allowed urgency levels, if it is clear")
    preferred_date: str | None = Field(
        default=None,
        description="The day the customer asked for, as YYYY-MM-DD, worked out from today's date. "
        "Only if they named a day, such as 'today', 'tomorrow' or 'Friday'",
    )
    preferred_period: str | None = Field(
        default=None, description="'morning' or 'afternoon' if the customer said which, otherwise empty"
    )


SYSTEM_PROMPT = """You collect booking details for {business}, an HVAC company.
Today is {today}.
Read the conversation and the customer's latest email, and extract any details
the customer has actually stated. Leave a field empty if it was not stated. Never guess.

Allowed urgency levels: {levels}
(emergency = no heat or no cooling that is unsafe, same_day = needs a visit today,
this_week = soon but not urgent, flexible = no rush)

Details already collected: {known}"""


def _urgency_from_day(day, today, levels):
    """Today -> same_day, within a week -> this_week, later -> flexible (only levels the config allows)."""
    if day == today:
        wanted = "same_day"
    elif (day - today).days <= 7:
        wanted = "this_week"
    else:
        wanted = "flexible"
    if wanted in levels:
        return wanted
    return None


def _missing(lead):
    missing = []
    for field in REQUIRED_FIELDS:
        if not lead.get(field):
            missing.append(field)
    return missing


def _ask_for(config, missing):
    lines = ["Thanks for contacting " + config.business.name + "! To get you scheduled, could you send me:"]
    for field in missing:
        lines.append("- " + FIELD_LABELS[field])
    return "\n".join(lines)


def _town_is_served(config, lead):
    """False only when we are sure the customer is outside the service area."""
    town = lead.get("town")
    if not town:
        return True
    served = []
    for name in config.business.service_area:
        served.append(name.lower())
    if town.lower() in served:
        return True
    address = lead.get("address", "").lower()
    for name in served:
        if name in address:
            return True
    return False


def make_intake(config: ClientConfig, llm=None, now_fn=None):
    if llm is None:
        llm = get_llm(schema=IntakeExtraction)
    levels = config.urgency_levels
    timezone = ZoneInfo(config.business.timezone)

    def intake(state: State) -> dict:
        if now_fn is not None:
            now = now_fn().astimezone(timezone)
        else:
            now = datetime.now(timezone)
        lead = dict(state.get("lead", {}))
        lead["contact"] = state.get("sender", lead.get("contact", ""))

        # Details are complete and slots are already being offered: nothing new to extract.
        if state.get("active_flow") == "booking" and len(_missing(lead)) == 0:
            return {"trace": ["intake"], "lead": lead, "missing_fields": []}

        messages = state.get("messages", [])
        latest = latest_customer(messages)
        if latest is None:
            return {"trace": ["intake"], "lead": lead, "missing_fields": _missing(lead)}
        position, text = latest

        known = "nothing yet"
        if len(lead) > 1:
            known = str(lead)
        system = SYSTEM_PROMPT.format(
            business=config.business.name, today=today_text(now), levels=", ".join(levels), known=known
        )
        earlier = messages[:position][-HISTORY_LIMIT:]
        human = ""
        if len(earlier) > 0:
            human = "Earlier messages:\n" + transcript(earlier) + "\n\n"
        human = human + "Latest email:\n" + text

        try:
            found = llm.invoke([("system", system), ("human", human)])
        except Exception as error:
            return {
                "trace": ["intake"],
                "escalated": True,
                "escalation_reason": "llm_unavailable",
                "errors": [record_failure("intake", error)],
            }

        # Merge what was found into what we already know; never erase known details.
        for field in ["name", "phone", "address", "town", "issue"]:
            value = getattr(found, field)
            if value and value.strip():
                lead[field] = value.strip()
        if found.urgency and found.urgency.strip().lower() in levels:
            lead["urgency"] = found.urgency.strip().lower()

        # The requested day is checked in code: a malformed, past or far-off date is ignored.
        wanted = parse_preferred_date(found.preferred_date, now.date())
        if wanted is not None:
            lead["preferred_date"] = wanted.isoformat()
        period = parse_period(found.preferred_period)
        if period is not None:
            lead["preferred_period"] = period

        # A named day already answers "how urgent is it?", so do not ask it again.
        if not lead.get("urgency") and lead.get("preferred_date"):
            lead["urgency"] = _urgency_from_day(date.fromisoformat(lead["preferred_date"]), now.date(), levels)

        if not _town_is_served(config, lead):
            served = ", ".join(config.business.service_area)
            text_out = (
                "Thanks for reaching out! Unfortunately "
                + lead["town"]
                + " is outside our service area. We currently serve: "
                + served
                + "."
            )
            update = reply_update(text_out)
            update.update(
                {
                    "trace": ["intake"],
                    "lead": lead,
                    "missing_fields": [],
                    "out_of_area": True,
                    "active_flow": None,
                }
            )
            return update

        missing = _missing(lead)
        if len(missing) > 0:
            update = reply_update(_ask_for(config, missing))
            update.update(
                {"trace": ["intake"], "lead": lead, "missing_fields": missing, "active_flow": "intake"}
            )
            return update

        return {"trace": ["intake"], "lead": lead, "missing_fields": [], "active_flow": "booking"}

    return intake
