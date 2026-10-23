"""Pure email logic with no network: reading messages, loop guard, building replies."""
import base64
import re
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

NO_REPLY_NAMES = ["noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster"]
BULK_PRECEDENCE = ["bulk", "junk", "list", "auto_reply"]

# A line that starts the quoted history in a customer's reply.
QUOTE_START = [
    re.compile(r"^on .+wrote:\s*$", re.IGNORECASE),  # Gmail, Apple Mail
    re.compile(r"^-{2,}\s*original message\s*-{2,}\s*$", re.IGNORECASE),  # Outlook
    re.compile(r"^-{2,}\s*forwarded message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^_{5,}\s*$"),  # Outlook divider
    re.compile(r"^from:\s.+", re.IGNORECASE),  # Outlook header block
]


# --- reading a Gmail message -------------------------------------------------
def headers_of(payload):
    """The message headers as a dictionary with lower-case names."""
    result = {}
    for header in payload.get("headers", []):
        result[header["name"].lower()] = header["value"]
    return result


def _decode(data):
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


class _TextFromHtml(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip_depth += 1
        if tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextFromHtml()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _find_part(payload, mime_type):
    """First body of the given type, searching nested parts."""
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []):
        found = _find_part(part, mime_type)
        if found is not None:
            return found
    return None


def extract_body(payload):
    """The message text: the plain-text part if there is one, otherwise the HTML turned into text."""
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        return plain
    html = _find_part(payload, "text/html")
    if html is not None:
        return html_to_text(html)
    return ""


def strip_quoted_reply(text):
    """Remove the quoted earlier conversation so only what the customer just wrote is left."""
    lines = text.replace("\r\n", "\n").split("\n")
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()

        if line.startswith(">"):
            break

        # "On Tue, Oct 21, 2026 at 3:04 PM Name <a@b.com>" with "wrote:" on the next line
        joined = line
        if line.lower().startswith("on ") and index + 1 < len(lines):
            joined = line + " " + lines[index + 1].strip()

        quoted = False
        for pattern in QUOTE_START:
            if pattern.match(line) or pattern.match(joined):
                quoted = True
        if quoted:
            break

        kept.append(lines[index])
        index = index + 1

    return "\n".join(kept).strip()


def sender_of(payload):
    """(display name, lower-case address) from the From header."""
    name, address = parseaddr(headers_of(payload).get("from", ""))
    return name, address.lower()


def reply_address(payload):
    """Where to send our reply: Reply-To if it is on the sender's own domain, otherwise From.

    A Reply-To on another domain is ignored. Otherwise anyone could make this open inbox send
    our replies to a stranger by putting the stranger's address in Reply-To.
    """
    _, sender = sender_of(payload)
    _, reply_to = parseaddr(headers_of(payload).get("reply-to", ""))
    reply_to = reply_to.lower()
    if "@" in reply_to and reply_to.split("@")[-1] == sender.split("@")[-1]:
        return reply_to
    return sender


# --- loop guard --------------------------------------------------------------
def skip_reason(payload, own_address, allowed_senders=None):
    """Why the agent must not answer this message, or None if it may."""
    headers = headers_of(payload)
    _, sender = sender_of(payload)

    if sender == own_address.lower():
        return "own_address"

    local_part = sender.split("@")[0]
    for name in NO_REPLY_NAMES:
        if name in local_part:
            return "no_reply_sender"

    auto = headers.get("auto-submitted", "no").strip().lower()
    if auto != "no":
        return "auto_submitted"

    if headers.get("precedence", "").strip().lower() in BULK_PRECEDENCE:
        return "bulk_precedence"

    if "list-id" in headers or "list-unsubscribe" in headers:
        return "mailing_list"

    if "x-auto-response-suppress" in headers:
        return "auto_response"

    if allowed_senders:
        allowed = []
        for address in allowed_senders:
            allowed.append(address.lower())
        if sender not in allowed:
            return "sender_not_allowed"

    return None


# --- building a reply --------------------------------------------------------
def reply_subject(subject):
    cleaned = subject.strip()
    if cleaned.lower().startswith("re:"):
        return cleaned
    return "Re: " + cleaned


def build_reply(from_address, to_address, subject, body, message_id_header="", references=""):
    """A reply message with the headers that make Gmail keep it in the customer's thread."""
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = to_address
    message["Subject"] = reply_subject(subject)
    if message_id_header:
        message["In-Reply-To"] = message_id_header
        message["References"] = (references + " " + message_id_header).strip()
    message.set_content(body)
    return message


def build_notice(from_address, to_address, subject, body):
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    return message


def encode_for_gmail(message):
    """Gmail's send call wants the whole message as URL-safe base64."""
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
