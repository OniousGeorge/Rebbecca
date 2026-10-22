"""Escalation node: tells a human, and tells the customer a person will follow up."""
from core.config import ClientConfig
from core.messages import transcript
from core.state import State, reply_update

STANDARD_REPLY = (
    "Thanks for reaching out. I have passed your message to our team and someone will follow up with you shortly."
)
EMERGENCY_REPLY = (
    "If you smell gas or suspect carbon monoxide, leave the building now and call 911 "
    "and your gas utility from outside. I have alerted our team and someone will contact you as soon as possible."
)


def make_escalation(config: ClientConfig, mailer):
    def escalation(state: State) -> dict:
        reason = state.get("escalation_reason") or "unspecified"
        lead = state.get("lead", {})

        subject = "[" + config.business.name + "] Needs a human: " + reason
        lines = [
            "Reason: " + reason,
            "Customer: " + state.get("sender", "unknown"),
            "Conversation id: " + state.get("thread_id", "unknown"),
            "",
            "Details collected so far: " + str(lead),
            "",
            "Conversation:",
            transcript(state.get("messages", [])),
        ]
        body = "\n".join(lines)

        errors = []
        try:
            mailer.send_notice(config.escalation.notify_email, subject, body)
        except Exception as error:
            # The customer must still get a reply even if the notice could not be sent.
            errors.append("escalation: " + str(error))

        text = STANDARD_REPLY
        if reason.startswith("emergency"):
            text = EMERGENCY_REPLY

        update = reply_update(text)
        update["trace"] = ["escalation"]
        update["escalated"] = True
        if len(errors) > 0:
            update["errors"] = errors
        return update

    return escalation
