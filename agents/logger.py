"""Logger node: records the lead and any booking in the CRM (Google Sheets or CSV)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import ClientConfig
from core.state import State


def _status(state):
    booking = state.get("booking")
    if state.get("escalated"):
        return "escalated"
    if state.get("out_of_area"):
        return "out_of_area"
    if booking:
        return booking["status"]
    return "open"


def make_logger(store, config: ClientConfig, now_fn=None):
    timezone = ZoneInfo(config.business.timezone)

    def logger(state: State) -> dict:
        if now_fn is not None:
            now = now_fn()
        else:
            now = datetime.now(timezone)

        lead = state.get("lead", {})
        row = {
            "updated_at": now.isoformat(timespec="seconds"),
            "name": lead.get("name", ""),
            "contact": lead.get("contact", state.get("sender", "")),
            "phone": lead.get("phone", ""),
            "address": lead.get("address", ""),
            "issue": lead.get("issue", ""),
            "urgency": lead.get("urgency", ""),
            "category": state.get("category", ""),
            "status": _status(state),
            "escalated": bool(state.get("escalated")),
            "reason": state.get("escalation_reason") or "",
        }

        update = {"trace": ["logger"]}
        try:
            store.upsert_lead(state.get("thread_id", ""), row)
            booking = state.get("booking")
            if booking:
                store.add_booking(state.get("thread_id", ""), booking)
        except Exception as error:
            # A logging failure must not stop the customer getting their reply.
            update["errors"] = ["logger: " + str(error)]
        return update

    return logger
