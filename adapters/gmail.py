"""Gmail adapter: reads new customer emails, labels them, sends replies and escalation notices.

One-time login (opens your browser):   python -m adapters.gmail
"""
import os
from pathlib import Path

from adapters import email_rules

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_PATH = "token.json"
PROCESSED_LABEL = "agent-processed"
ERROR_LABEL = "agent-error"
MAX_PER_CHECK = 25


# --- login -------------------------------------------------------------------
def _client_config():
    client_id = os.environ.get("GMAIL_CLIENT")
    secret = os.environ.get("GMAIL_SECRET")
    if not client_id or not secret:
        raise RuntimeError("Missing GMAIL_CLIENT or GMAIL_SECRET in .env")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def login(token_path=TOKEN_PATH):
    """Return Google credentials, asking you to approve access in the browser the first time."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    credentials = None
    if Path(token_path).exists():
        credentials = Credentials.from_authorized_user_file(token_path, SCOPES)

    if credentials is not None and credentials.valid:
        return credentials

    if credentials is not None and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
        credentials = flow.run_local_server(port=0)

    Path(token_path).write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def connect(allowed_senders=None, dry_run=False, lookback="1d"):
    """Build a GmailInbox for the account in AGENT_EMAIL."""
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=login(), cache_discovery=False)
    inbox = GmailInbox(service, allowed_senders=allowed_senders, dry_run=dry_run, lookback=lookback)

    expected = os.environ.get("AGENT_EMAIL", "").lower()
    if expected and expected != inbox.own_address:
        raise RuntimeError(
            "Logged in as " + inbox.own_address + " but AGENT_EMAIL is " + expected + ". Delete token.json and log in again."
        )
    return inbox


# --- the adapter -------------------------------------------------------------
class GmailInbox:
    def __init__(self, service, allowed_senders=None, dry_run=False, lookback="1d"):
        self.service = service
        self.allowed_senders = allowed_senders or []
        self.dry_run = dry_run
        self.lookback = lookback
        self.label_ids = {}
        profile = self.service.users().getProfile(userId="me").execute()
        self.own_address = profile["emailAddress"].lower()

    def _label_id(self, name):
        """The id of a label, creating the label the first time."""
        if name in self.label_ids:
            return self.label_ids[name]
        existing = self.service.users().labels().list(userId="me").execute().get("labels", [])
        for label in existing:
            if label["name"] == name:
                self.label_ids[name] = label["id"]
                return label["id"]
        created = self.service.users().labels().create(userId="me", body={"name": name}).execute()
        self.label_ids[name] = created["id"]
        return created["id"]

    def fetch_new(self):
        """Unprocessed inbox emails, oldest first. Emails we must not answer carry a `skip` reason."""
        query = "in:inbox -label:" + PROCESSED_LABEL + " newer_than:" + self.lookback
        listing = self.service.users().messages().list(userId="me", q=query, maxResults=MAX_PER_CHECK).execute()

        emails = []
        for item in listing.get("messages", []):
            raw = self.service.users().messages().get(userId="me", id=item["id"], format="full").execute()
            payload = raw["payload"]
            headers = email_rules.headers_of(payload)
            name, address = email_rules.sender_of(payload)
            emails.append(
                {
                    "id": raw["id"],
                    "thread_id": raw["threadId"],
                    "received": int(raw.get("internalDate", "0")),
                    "sender": address,
                    "sender_name": name,
                    "subject": headers.get("subject", ""),
                    "body": email_rules.strip_quoted_reply(email_rules.extract_body(payload)),
                    "message_id_header": headers.get("message-id", ""),
                    "references": headers.get("references", ""),
                    "skip": email_rules.skip_reason(payload, self.own_address, self.allowed_senders),
                }
            )
        emails.sort(key=lambda e: e["received"])
        return emails

    def _add_label(self, message_id, label_name):
        if self.dry_run:
            print("[dry run] would label " + message_id + " as " + label_name)
            return
        body = {"addLabelIds": [self._label_id(label_name)]}
        self.service.users().messages().modify(userId="me", id=message_id, body=body).execute()

    def mark_processed(self, message_id):
        self._add_label(message_id, PROCESSED_LABEL)

    def mark_error(self, message_id):
        self._add_label(message_id, ERROR_LABEL)

    def send_reply(self, email, body):
        """Reply inside the customer's own thread."""
        message = email_rules.build_reply(
            self.own_address,
            email["sender"],
            email["subject"],
            body,
            email["message_id_header"],
            email["references"],
        )
        if self.dry_run:
            print("[dry run] would reply to " + email["sender"] + " (" + message["Subject"] + "):\n" + body)
            return
        request_body = {"raw": email_rules.encode_for_gmail(message), "threadId": email["thread_id"]}
        self.service.users().messages().send(userId="me", body=request_body).execute()

    def send_notice(self, to, subject, body):
        """Escalation notice to the owner. Same method name as ConsoleMail, so nodes need no change."""
        message = email_rules.build_notice(self.own_address, to, subject, body)
        if self.dry_run:
            print("[dry run] would send notice to " + to + ": " + subject)
            return
        self.service.users().messages().send(
            userId="me", body={"raw": email_rules.encode_for_gmail(message)}
        ).execute()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    inbox = connect(dry_run=True)
    print("Logged in as " + inbox.own_address + ". token.json saved. Nothing was sent or changed.")
