from langgraph.checkpoint.memory import MemorySaver

from adapters.calendar_json import JsonCalendar
from adapters.csv_log import CsvLog
from agents.faq import FAQAnswer
from agents.router import RouterDecision
from app import build_app
from main import parse_allowed_senders, process_inbox
from tests.fakes import CONFIG, RecordingMailer, ScriptedLLM, fixed_now


class FakeInbox:
    """Records every Gmail action in order, so tests can check what happened and when."""

    def __init__(self, emails, fail_send=False):
        self.emails = emails
        self.fail_send = fail_send
        self.events = []

    def fetch_new(self):
        return self.emails

    def mark_processed(self, message_id):
        self.events.append(("processed", message_id))

    def mark_error(self, message_id):
        self.events.append(("error", message_id))

    def send_reply(self, email, body):
        if self.fail_send:
            raise RuntimeError("gmail send failed")
        self.events.append(("reply", email["id"], body))

    def send_notice(self, to, subject, body):
        self.events.append(("notice", to, subject, body))


def incoming(message_id="m1", thread_id="t1", body="What are your hours?", skip=None):
    return {
        "id": message_id,
        "thread_id": thread_id,
        "sender": "dana@example.com",
        "subject": "Question",
        "body": body,
        "skip": skip,
        "message_id_header": "<" + message_id + "@mail>",
        "references": "",
    }


class StubGraph:
    def __init__(self, result=None, error=None):
        self.result = result or {"reply": "", "category": "spam"}
        self.error = error
        self.inputs = []

    def invoke(self, state, run_config):
        self.inputs.append((state, run_config))
        if self.error is not None:
            raise self.error
        return self.result


def names(inbox):
    return [event[0] for event in inbox.events]


def test_email_is_labelled_before_the_graph_runs_and_then_answered():
    inbox = FakeInbox([incoming()])
    graph = StubGraph({"reply": "We open at 8.", "category": "faq"})
    outcomes = process_inbox(inbox, graph, CONFIG, set())

    assert names(inbox) == ["processed", "reply"]
    assert inbox.events[1] == ("reply", "m1", "We open at 8.")
    assert outcomes == [{"id": "m1", "action": "replied"}]


def test_graph_gets_the_gmail_thread_as_conversation_id():
    inbox = FakeInbox([incoming(thread_id="thread-9", body="Hello there")])
    graph = StubGraph()
    process_inbox(inbox, graph, CONFIG, set())

    state, run_config = graph.inputs[0]
    assert state["thread_id"] == "thread-9"
    assert state["sender"] == "dana@example.com"
    assert state["messages"] == [{"role": "customer", "text": "Hello there"}]
    assert run_config == {"configurable": {"thread_id": "thread-9"}}


def test_automatic_mail_is_labelled_but_never_answered_or_sent_to_the_graph():
    inbox = FakeInbox([incoming(skip="no_reply_sender")])
    graph = StubGraph()
    outcomes = process_inbox(inbox, graph, CONFIG, set())

    assert names(inbox) == ["processed"]
    assert graph.inputs == []
    assert outcomes[0]["action"] == "skipped"


def test_no_reply_from_the_graph_means_nothing_is_sent():
    inbox = FakeInbox([incoming()])
    outcomes = process_inbox(inbox, StubGraph({"reply": "", "category": "spam"}), CONFIG, set())
    assert names(inbox) == ["processed"]
    assert outcomes[0]["action"] == "no_reply"


def test_a_crash_labels_the_email_as_an_error_and_tells_the_owner_and_does_not_reply():
    inbox = FakeInbox([incoming("m1"), incoming("m2", thread_id="t2")])
    graph = StubGraph(error=RuntimeError("boom"))
    outcomes = process_inbox(inbox, graph, CONFIG, set())

    assert [o["action"] for o in outcomes] == ["error", "error"]  # the loop kept going after the first crash
    assert ("error", "m1") in inbox.events
    notice = [event for event in inbox.events if event[0] == "notice"][0]
    assert notice[1] == "owner@example.com"
    assert "boom" in notice[3]
    assert "reply" not in names(inbox)


def test_a_failed_send_is_treated_like_any_other_failure():
    inbox = FakeInbox([incoming()], fail_send=True)
    outcomes = process_inbox(inbox, StubGraph({"reply": "hi", "category": "faq"}), CONFIG, set())
    assert outcomes[0]["action"] == "error"
    assert "error" in names(inbox)
    assert "notice" in names(inbox)


def test_an_email_seen_before_is_not_handled_twice():
    # In a dry run the label is never really applied, so Gmail keeps listing the same email.
    inbox = FakeInbox([incoming()])
    graph = StubGraph({"reply": "hi", "category": "faq"})
    seen = set()
    process_inbox(inbox, graph, CONFIG, seen)
    process_inbox(inbox, graph, CONFIG, seen)
    assert len(graph.inputs) == 1
    assert names(inbox).count("reply") == 1


def test_works_with_the_real_graph(tmp_path):
    router = ScriptedLLM(RouterDecision(category="faq", reason="hours", continues_active_flow=False))
    faq = ScriptedLLM(FAQAnswer(answerable=True, answer="We open at 8 AM on weekdays."))
    graph = build_app(
        CONFIG,
        JsonCalendar(tmp_path / "calendar.json", CONFIG.business, now_fn=fixed_now),
        CsvLog(tmp_path / "leads.csv", tmp_path / "bookings.csv"),
        RecordingMailer(),
        llms={"router": router, "faq": faq},
        checkpointer=MemorySaver(),
    )
    inbox = FakeInbox([incoming(body="When do you open?")])
    process_inbox(inbox, graph, CONFIG, set())
    assert inbox.events[-1] == ("reply", "m1", "We open at 8 AM on weekdays.")


# --- start-up rules ------------------------------------------------------------
def test_allowed_senders_are_cleaned_up():
    assert parse_allowed_senders(" Dana@Example.com, ,bob@x.com ") == ["dana@example.com", "bob@x.com"]
    assert parse_allowed_senders("") == []
    assert parse_allowed_senders(None) == []
