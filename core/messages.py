"""Small helpers for reading the conversation in the state."""


def latest_customer(messages):
    """Return (position, text) of the newest customer message, or None if there is none."""
    position = len(messages) - 1
    while position >= 0:
        message = messages[position]
        if message["role"] == "customer":
            return position, message["text"]
        position = position - 1
    return None


def transcript(messages):
    lines = []
    for message in messages:
        role = message["role"].upper()
        lines.append(role + ": " + message["text"])
    return "\n".join(lines)
