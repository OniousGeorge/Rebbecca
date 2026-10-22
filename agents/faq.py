"""FAQ node: answers general questions using only the facts in the client config."""
from pydantic import BaseModel, Field

from core.config import ClientConfig, describe_hours
from core.llm import get_llm
from core.messages import latest_customer, transcript
from core.state import State, reply_update

HISTORY_LIMIT = 4


class FAQAnswer(BaseModel):
    answerable: bool = Field(description="True only if the facts fully answer the question")
    answer: str = Field(description="A short, friendly answer using only the facts. Empty if not answerable")


SYSTEM_PROMPT = """You answer customer questions for {business}, an HVAC company.
Tone: {tone}.
Use ONLY the facts below. Never invent prices, hours, policies or availability.
If the facts do not fully answer the question, set answerable to false.

FACTS
Opening hours:
{hours}

Services: {services}
Service area: {area}

Pricing:
{pricing}

Common questions:
{faqs}"""


def facts_prompt(config: ClientConfig) -> str:
    pricing_lines = []
    for item, price in config.pricing.items():
        pricing_lines.append("- " + item.replace("_", " ") + ": " + price)

    faq_lines = []
    for faq in config.faqs:
        faq_lines.append("Q: " + faq.q + "\nA: " + faq.a)

    return SYSTEM_PROMPT.format(
        business=config.business.name,
        tone=config.business.tone,
        hours=describe_hours(config.business),
        services=", ".join(config.business.services),
        area=", ".join(config.business.service_area),
        pricing="\n".join(pricing_lines),
        faqs="\n\n".join(faq_lines),
    )


def make_faq(config: ClientConfig, llm=None):
    if llm is None:
        llm = get_llm(schema=FAQAnswer)
    system = facts_prompt(config)

    def faq(state: State) -> dict:
        messages = state.get("messages", [])
        latest = latest_customer(messages)
        if latest is None:
            return {"trace": ["faq"], "escalated": True, "escalation_reason": "unknown_faq"}
        position, text = latest

        earlier = messages[:position][-HISTORY_LIMIT:]
        human = ""
        if len(earlier) > 0:
            human = "Earlier messages:\n" + transcript(earlier) + "\n\n"
        human = human + "Latest email:\n" + text

        try:
            result = llm.invoke([("system", system), ("human", human)])
        except Exception as error:
            return {
                "trace": ["faq"],
                "escalated": True,
                "escalation_reason": "llm_unavailable",
                "errors": ["faq: " + str(error)],
            }

        # If the facts do not cover it, a person answers. We never improvise.
        if not result.answerable or result.answer.strip() == "":
            return {"trace": ["faq"], "escalated": True, "escalation_reason": "unknown_faq"}

        update = reply_update(result.answer.strip())
        update["trace"] = ["faq"]
        return update

    return faq
