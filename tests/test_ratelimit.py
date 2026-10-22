from core.ratelimit import SenderLimiter
from main import process_inbox
from tests.fakes import CONFIG
from tests.test_main import FakeInbox, StubGraph, incoming, names


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_limiter(max_per_window=2):
    clock = Clock()
    return SenderLimiter(max_per_window=max_per_window, window_seconds=3600, clock=clock), clock


# --- the limiter itself ---------------------------------------------------------
def test_a_sender_is_allowed_up_to_the_limit_and_then_blocked():
    limiter, _ = make_limiter(2)
    assert limiter.allow("a@x.com")
    limiter.record("a@x.com")
    assert limiter.allow("a@x.com")
    limiter.record("a@x.com")
    assert not limiter.allow("a@x.com")


def test_each_sender_has_their_own_count():
    limiter, _ = make_limiter(1)
    limiter.record("a@x.com")
    assert not limiter.allow("a@x.com")
    assert limiter.allow("b@x.com")


def test_the_count_resets_after_the_window():
    limiter, clock = make_limiter(1)
    limiter.record("a@x.com")
    assert not limiter.allow("a@x.com")
    clock.now += 3601
    assert limiter.allow("a@x.com")


def test_the_owner_is_told_only_once_per_window():
    limiter, clock = make_limiter(1)
    assert limiter.first_block("a@x.com") is True
    assert limiter.first_block("a@x.com") is False
    clock.now += 3601
    assert limiter.first_block("a@x.com") is True


# --- inside the polling loop ----------------------------------------------------
def three_emails_from_one_sender():
    return [incoming("m1", "t1"), incoming("m2", "t2"), incoming("m3", "t3")]


def test_emails_over_the_limit_are_labelled_but_not_answered():
    inbox = FakeInbox(three_emails_from_one_sender())
    graph = StubGraph({"reply": "hi", "category": "faq"})
    limiter, _ = make_limiter(2)
    outcomes = process_inbox(inbox, graph, CONFIG, set(), limiter)

    assert [o["action"] for o in outcomes] == ["replied", "replied", "rate_limited"]
    assert len(graph.inputs) == 2  # the third never reached the LLMs
    assert ("processed", "m3") in inbox.events


def test_the_owner_gets_one_notice_however_many_emails_are_blocked():
    inbox = FakeInbox(three_emails_from_one_sender() + [incoming("m4", "t4"), incoming("m5", "t5")])
    limiter, _ = make_limiter(2)
    process_inbox(inbox, StubGraph({"reply": "hi", "category": "faq"}), CONFIG, set(), limiter)

    notices = [event for event in inbox.events if event[0] == "notice"]
    assert len(notices) == 1
    assert "dana@example.com" in notices[0][3]
    assert "ignoring further emails" in notices[0][3]


def test_other_senders_are_not_affected():
    other = incoming("m9", "t9")
    other["sender"] = "bob@example.com"
    inbox = FakeInbox(three_emails_from_one_sender() + [other])
    limiter, _ = make_limiter(2)
    outcomes = process_inbox(inbox, StubGraph({"reply": "hi", "category": "faq"}), CONFIG, set(), limiter)
    assert outcomes[-1] == {"id": "m9", "action": "replied"}


def test_skipped_automatic_mail_does_not_count_toward_the_limit():
    auto = [incoming("a" + str(n), "ta" + str(n), skip="auto_submitted") for n in range(5)]
    inbox = FakeInbox(auto + [incoming("m1", "t1")])
    limiter, _ = make_limiter(1)
    outcomes = process_inbox(inbox, StubGraph({"reply": "hi", "category": "faq"}), CONFIG, set(), limiter)
    assert outcomes[-1]["action"] == "replied"


def test_without_a_limiter_nothing_is_limited():
    inbox = FakeInbox(three_emails_from_one_sender())
    outcomes = process_inbox(inbox, StubGraph({"reply": "hi", "category": "faq"}), CONFIG, set())
    assert [o["action"] for o in outcomes] == ["replied", "replied", "replied"]
    assert names(inbox).count("notice") == 0
