"""Tests the Gmail wrapper against a fake service that records every call."""
import base64
import email

from adapters import email_rules
from adapters.gmail import ERROR_LABEL, PROCESSED_LABEL, GmailInbox
from tests.test_email_rules import payload


class Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeGmail:
    def __init__(self, messages=None, labels=None):
        self.messages_by_id = messages or {}
        self.labels_list = labels or []
        self.calls = []

    def users(self):
        return self

    def getProfile(self, userId):
        return Request({"emailAddress": "Agent@Gmail.com"})

    def messages(self):
        return FakeMessages(self)

    def labels(self):
        return FakeLabels(self)


class FakeMessages:
    def __init__(self, gmail):
        self.gmail = gmail

    def list(self, userId, q, maxResults):
        self.gmail.calls.append(("list", q))
        return Request({"messages": [{"id": message_id} for message_id in self.gmail.messages_by_id]})

    def get(self, userId, id, format):
        return Request(self.gmail.messages_by_id[id])

    def modify(self, userId, id, body):
        self.gmail.calls.append(("modify", id, body))
        return Request({})

    def send(self, userId, body):
        self.gmail.calls.append(("send", body))
        return Request({"id": "sent-1"})


class FakeLabels:
    def __init__(self, gmail):
        self.gmail = gmail

    def list(self, userId):
        return Request({"labels": self.gmail.labels_list})

    def create(self, userId, body):
        self.gmail.calls.append(("create_label", body["name"]))
        created = {"id": "LBL_" + body["name"], "name": body["name"]}
        self.gmail.labels_list.append(created)
        return Request(created)


def raw_message(message_id, thread_id, received, headers=None, plain="hello"):
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(received),
        "payload": payload(headers, plain=plain),
    }


def calls_named(gmail, name):
    return [call for call in gmail.calls if call[0] == name]


def test_own_address_comes_from_the_logged_in_account_in_lower_case():
    assert GmailInbox(FakeGmail()).own_address == "agent@gmail.com"


def test_fetch_new_returns_oldest_first_with_clean_bodies():
    quoted = "Second one\n\nOn Tue, Oct 21, 2026 at 3:04 PM X <a@b.com> wrote:\n> old"
    gmail = FakeGmail(
        {
            "m2": raw_message("m2", "t1", 2000, {"Message-ID": "<m2@mail>"}, plain=quoted),
            "m1": raw_message("m1", "t1", 1000, {"Message-ID": "<m1@mail>"}),
        }
    )
    emails = GmailInbox(gmail).fetch_new()

    assert [e["id"] for e in emails] == ["m1", "m2"]
    assert emails[1]["body"] == "Second one"
    assert emails[1]["thread_id"] == "t1"
    assert emails[1]["sender"] == "dana@example.com"
    assert emails[1]["message_id_header"] == "<m2@mail>"
    assert emails[0]["skip"] is None


def test_query_excludes_processed_mail_and_limits_how_far_back_it_looks():
    gmail = FakeGmail()
    GmailInbox(gmail, lookback="2d").fetch_new()
    query = calls_named(gmail, "list")[0][1]
    assert "in:inbox" in query
    assert "-label:" + PROCESSED_LABEL in query
    assert "newer_than:2d" in query


def test_automatic_mail_is_returned_with_a_skip_reason_so_it_can_still_be_labelled():
    gmail = FakeGmail({"m1": raw_message("m1", "t1", 1, {"From": "noreply@shop.com"})})
    assert GmailInbox(gmail).fetch_new()[0]["skip"] == "no_reply_sender"


def test_allowlist_is_applied_when_fetching():
    gmail = FakeGmail({"m1": raw_message("m1", "t1", 1, {"From": "stranger@spam.com"})})
    emails = GmailInbox(gmail, allowed_senders=["dana@example.com"]).fetch_new()
    assert emails[0]["skip"] == "sender_not_allowed"


def test_labels_are_created_once_and_reused():
    gmail = FakeGmail()
    inbox = GmailInbox(gmail)
    inbox.mark_processed("m1")
    inbox.mark_processed("m2")
    inbox.mark_error("m3")

    assert calls_named(gmail, "create_label") == [
        ("create_label", PROCESSED_LABEL),
        ("create_label", ERROR_LABEL),
    ]
    assert calls_named(gmail, "modify")[0] == ("modify", "m1", {"addLabelIds": ["LBL_" + PROCESSED_LABEL]})
    assert calls_named(gmail, "modify")[2][2] == {"addLabelIds": ["LBL_" + ERROR_LABEL]}


def test_an_existing_label_is_reused_not_recreated():
    gmail = FakeGmail(labels=[{"id": "Label_7", "name": PROCESSED_LABEL}])
    GmailInbox(gmail).mark_processed("m1")
    assert calls_named(gmail, "create_label") == []
    assert calls_named(gmail, "modify")[0][2] == {"addLabelIds": ["Label_7"]}


def test_reply_is_sent_in_the_customers_thread():
    gmail = FakeGmail()
    inbox = GmailInbox(gmail)
    customer_email = {
        "sender": "dana@example.com",
        "subject": "AC problem",
        "thread_id": "t1",
        "message_id_header": "<m1@mail>",
        "references": "",
    }
    inbox.send_reply(customer_email, "Here are your times")

    body = calls_named(gmail, "send")[0][1]
    assert body["threadId"] == "t1"
    sent = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
    assert sent["To"] == "dana@example.com"
    assert sent["From"] == "agent@gmail.com"
    assert sent["Subject"] == "Re: AC problem"
    assert sent["In-Reply-To"] == "<m1@mail>"


def test_notice_is_a_new_message_not_part_of_a_thread():
    gmail = FakeGmail()
    GmailInbox(gmail).send_notice("owner@example.com", "Needs a human", "details")
    body = calls_named(gmail, "send")[0][1]
    assert "threadId" not in body
    sent = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]))
    assert sent["To"] == "owner@example.com"
    assert sent["Subject"] == "Needs a human"


def test_dry_run_changes_and_sends_nothing(capsys):
    gmail = FakeGmail()
    inbox = GmailInbox(gmail, dry_run=True)
    inbox.mark_processed("m1")
    inbox.send_reply({"sender": "d@example.com", "subject": "x", "thread_id": "t", "message_id_header": "", "references": ""}, "hi")
    inbox.send_notice("owner@example.com", "s", "b")

    assert calls_named(gmail, "modify") == []
    assert calls_named(gmail, "send") == []
    assert "[dry run]" in capsys.readouterr().out


def test_gmail_adapter_satisfies_the_mailer_interface_used_by_escalation():
    assert callable(GmailInbox(FakeGmail()).send_notice)
    assert email_rules  # the pure rules module is what the adapter builds on
