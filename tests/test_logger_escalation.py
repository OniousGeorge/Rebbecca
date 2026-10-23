import csv

from adapters.csv_log import CsvLog
from agents.escalation import make_escalation
from agents.logger import make_logger
from tests.fakes import CONFIG, NOW, RecordingMailer, agent, customer, fixed_now

LEAD = {"name": "Sam Lee", "contact": "sam@example.com", "address": "12 Oak St", "issue": "AC broken", "urgency": "same_day"}
BOOKING = {
    "id": "bk-1",
    "start": "2026-10-19T09:30:00-04:00",
    "end": "2026-10-19T11:00:00-04:00",
    "name": "Sam Lee",
    "contact": "sam@example.com",
    "address": "12 Oak St",
    "issue": "AC broken",
    "status": "booked",
}


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def make_store(tmp_path):
    return CsvLog(tmp_path / "leads.csv", tmp_path / "bookings.csv")


# --- logger ----------------------------------------------------------------
def test_logger_records_the_lead_and_the_booking(tmp_path):
    store = make_store(tmp_path)
    state = {"thread_id": "t1", "sender": "sam@example.com", "category": "new_lead", "lead": LEAD, "booking": BOOKING}
    result = make_logger(store, CONFIG, fixed_now)(state)

    assert result == {"trace": ["logger"]}
    lead_row = read(tmp_path / "leads.csv")[0]
    assert lead_row["name"] == "Sam Lee"
    assert lead_row["status"] == "booked"
    assert lead_row["urgency"] == "same_day"
    assert lead_row["updated_at"] == NOW.isoformat(timespec="seconds")
    assert read(tmp_path / "bookings.csv")[0]["booking_id"] == "bk-1"


def test_logger_updates_the_same_conversation_instead_of_adding_rows(tmp_path):
    store = make_store(tmp_path)
    logger = make_logger(store, CONFIG, fixed_now)
    logger({"thread_id": "t1", "category": "new_lead", "lead": LEAD})
    logger({"thread_id": "t1", "category": "new_lead", "lead": LEAD, "booking": BOOKING})
    logger({"thread_id": "t1", "category": "cancel", "lead": LEAD, "booking": dict(BOOKING, status="cancelled")})

    leads = read(tmp_path / "leads.csv")
    assert len(leads) == 1
    assert leads[0]["status"] == "cancelled"
    bookings = read(tmp_path / "bookings.csv")
    assert len(bookings) == 1
    assert bookings[0]["status"] == "cancelled"


def test_logger_status_for_escalated_and_out_of_area_and_open(tmp_path):
    store = make_store(tmp_path)
    logger = make_logger(store, CONFIG, fixed_now)
    logger({"thread_id": "a", "escalated": True, "escalation_reason": "unknown_faq"})
    logger({"thread_id": "b", "out_of_area": True, "lead": LEAD})
    logger({"thread_id": "c", "lead": LEAD})
    rows = {row["thread_id"]: row for row in read(tmp_path / "leads.csv")}
    assert rows["a"]["status"] == "escalated"
    assert rows["a"]["reason"] == "unknown_faq"
    assert rows["b"]["status"] == "out_of_area"
    assert rows["c"]["status"] == "open"


def test_logger_failure_is_recorded_and_does_not_raise():
    class BrokenStore:
        def upsert_lead(self, thread_id, row):
            raise RuntimeError("sheet unreachable")

    result = make_logger(BrokenStore(), CONFIG, fixed_now)({"thread_id": "t1", "lead": LEAD})
    assert "sheet unreachable" in result["errors"][0]


# --- escalation ------------------------------------------------------------
def test_escalation_notifies_the_owner_with_the_conversation():
    mailer = RecordingMailer()
    state = {
        "thread_id": "t1",
        "sender": "sam@example.com",
        "escalation_reason": "unknown_faq",
        "lead": {"name": "Sam"},
        "messages": [customer("Do you repair boats?"), agent("hold on")],
    }
    result = make_escalation(CONFIG, mailer)(state)

    notice = mailer.sent[0]
    assert notice["to"] == CONFIG.escalation.notify_email
    assert "unknown_faq" in notice["subject"]
    assert "Comfort Zone Heating & Air" in notice["subject"]
    assert "CUSTOMER: Do you repair boats?" in notice["body"]
    assert "sam@example.com" in notice["body"]
    assert "someone will follow up" in result["reply"]
    assert result["escalated"] is True
    assert result["messages"][0]["role"] == "agent"


def test_emergency_reply_tells_the_customer_to_call_911():
    mailer = RecordingMailer()
    state = {"escalation_reason": "emergency: smell gas", "messages": [customer("I smell gas")]}
    result = make_escalation(CONFIG, mailer)(state)
    assert "911" in result["reply"]


def test_customer_still_gets_a_reply_when_the_notice_cannot_be_sent():
    mailer = RecordingMailer(fail=True)
    result = make_escalation(CONFIG, mailer)({"escalation_reason": "x", "messages": [customer("hi")]})
    assert "someone will follow up" in result["reply"]
    assert "mail server down" in result["errors"][0]


def test_after_escalating_the_conversation_is_handed_off():
    result = make_escalation(CONFIG, RecordingMailer())({"escalation_reason": "unknown_faq", "messages": [customer("x")]})
    assert result["handed_off"] is True


def test_a_follow_up_after_handoff_is_forwarded_without_a_reply():
    mailer = RecordingMailer()
    state = {"escalation_reason": "follow_up_after_handoff", "messages": [customer("hello? any news?")]}
    result = make_escalation(CONFIG, mailer)(state)
    assert result["reply"] == ""
    assert "messages" not in result  # nothing was said to the customer
    assert "wrote again" in mailer.sent[0]["subject"]
    assert "The agent did not reply" in mailer.sent[0]["body"]
    assert "hello? any news?" in mailer.sent[0]["body"]


def test_the_emergency_reply_covers_smoke_and_fire_too():
    result = make_escalation(CONFIG, RecordingMailer())({"escalation_reason": "emergency: smell smoke", "messages": []})
    assert "smoke or flames" in result["reply"]
    assert "911" in result["reply"]
