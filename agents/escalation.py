"""Escalation node: tells a human, and tells the customer a person will follow up.

After this runs, the conversation is handed off: the agent stops answering on that thread and
forwards any further customer emails to the owner instead (see the router).
"""
from core.config import ClientConfig
from core.failures import record_failure
from core.messages import transcript
from core.state import FOLLOW_UP_AFTER_HANDOFF, State, reply_update

STANDARD_REPLY = (
    "Thanks for reaching out. I have passed your message to our team and someone will follow up with you shortly."
)
EMERGENCY_REPLY = (
    "This may be a safety emergency. If you smell gas, see smoke or flames, or a carbon monoxide or smoke "
    "alarm is going off, leave the building now and call 911 from outside (and your gas utility for a gas smell). "
    "I have alerted our team and someone will contact you as soon as possible."
)


def make_escalation(config: ClientConfig, mailer):
    def escalation(state: State) -> dict:
        reason = state.get("escalation_reason") or "unspecified"
        lead = state.get("lead", {})

        if reason == FOLLOW_UP_AFTER_HANDOFF:
            subject = "[" + config.business.name + "] Customer wrote again in a conversation handed to you"
        else:
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
        if reason == FOLLOW_UP_AFTER_HANDOFF:
            lines.insert(0, "The agent did not reply. Please answer the customer yourself.\n")
        body = "\n".join(lines)

        errors = []
        try:
            mailer.send_notice(config.escalation.notify_email, subject, body)
        except Exception as error:
            # The customer must still get a reply even if the notice could not be sent.
            errors.append(record_failure("escalation", error))

        if reason == FOLLOW_UP_AFTER_HANDOFF:
            # A person owns this conversation: no automatic reply.
            update = {"reply": ""}
        elif reason.startswith("emergency"):
            update = reply_update(EMERGENCY_REPLY)
        else:
            update = reply_update(STANDARD_REPLY)

        update["trace"] = ["escalation"]
        update["escalated"] = True
        update["handed_off"] = True
        if len(errors) > 0:
            update["errors"] = errors
        return update

    return escalation
