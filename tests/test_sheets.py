import re

from adapters.csv_log import BOOKING_COLUMNS, LEAD_COLUMNS
from adapters.sheets import SheetsLog, column_letter

BOOKING = {
    "id": "bk-1",
    "start": "2026-10-22T09:30:00-04:00",
    "end": "2026-10-22T11:00:00-04:00",
    "name": "Sam",
    "contact": "sam@example.com",
    "address": "12 Oak St",
    "issue": "AC",
    "status": "booked",
}


class Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeSheetsService:
    """Keeps each tab as a list of rows and records every write."""

    def __init__(self):
        self.tabs = {"Leads": [], "Bookings": []}
        self.writes = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):
        rows = self.tabs[range.split("!")[0]]
        if len(rows) == 0:
            return Request({})  # the real API leaves out "values" for an empty range
        return Request({"values": [list(row) for row in rows]})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.writes.append(("update", range, valueInputOption))
        tab, cells = range.split("!")
        row_number = int(re.match(r"A(\d+):", cells).group(1))
        rows = self.tabs[tab]
        while len(rows) < row_number:
            rows.append([])
        rows[row_number - 1] = list(body["values"][0])
        return Request({})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.writes.append(("append", range, valueInputOption))
        self.tabs[range.split("!")[0]].append(list(body["values"][0]))
        return Request({})


def make_log():
    service = FakeSheetsService()
    return SheetsLog(service, "sheet-id"), service


def test_column_letters():
    assert [column_letter(n) for n in (1, 9, 12, 26, 27)] == ["A", "I", "L", "Z", "AA"]


def test_headers_are_written_once_into_an_empty_tab():
    log, service = make_log()
    log.upsert_lead("t1", {"name": "Sam"})
    log.upsert_lead("t2", {"name": "Bo"})
    assert service.tabs["Leads"][0] == LEAD_COLUMNS
    assert len(service.tabs["Leads"]) == 3  # header + two leads
    header_writes = [w for w in service.writes if w[0] == "update"]
    assert len(header_writes) == 1


def test_existing_headers_are_left_alone():
    log, service = make_log()
    service.tabs["Leads"] = [list(LEAD_COLUMNS)]
    log.upsert_lead("t1", {"name": "Sam"})
    assert [w for w in service.writes if w[0] == "update"] == []


def test_a_lead_is_added_as_a_row_in_column_order():
    log, service = make_log()
    log.upsert_lead("t1", {"name": "Sam Lee", "status": "booked", "escalated": False})
    row = dict(zip(LEAD_COLUMNS, service.tabs["Leads"][1]))
    assert row["thread_id"] == "t1"
    assert row["name"] == "Sam Lee"
    assert row["status"] == "booked"
    assert row["escalated"] == "False"
    assert row["phone"] == ""


def test_the_same_conversation_updates_its_row_instead_of_adding_one():
    log, service = make_log()
    log.upsert_lead("t1", {"name": "Sam", "status": "open"})
    log.upsert_lead("t2", {"name": "Bo", "status": "open"})
    log.upsert_lead("t1", {"name": "Sam", "status": "booked"})

    leads = service.tabs["Leads"]
    assert len(leads) == 3
    assert dict(zip(LEAD_COLUMNS, leads[1]))["status"] == "booked"
    assert dict(zip(LEAD_COLUMNS, leads[2]))["name"] == "Bo"  # the other row is untouched


def test_bookings_are_keyed_by_booking_id_and_can_be_updated():
    log, service = make_log()
    log.add_booking("t1", BOOKING)
    log.add_booking("t1", dict(BOOKING, status="cancelled"))

    bookings = service.tabs["Bookings"]
    assert len(bookings) == 2  # header + one booking
    row = dict(zip(BOOKING_COLUMNS, bookings[1]))
    assert row["booking_id"] == "bk-1"
    assert row["thread_id"] == "t1"
    assert row["status"] == "cancelled"


def test_customer_text_is_written_raw_so_it_cannot_run_as_a_formula():
    log, service = make_log()
    log.upsert_lead("t1", {"name": '=HYPERLINK("http://evil.example","click")'})
    assert service.tabs["Leads"][1][LEAD_COLUMNS.index("name")].startswith("=")
    assert {write[2] for write in service.writes} == {"RAW"}


def test_make_store_falls_back_to_csv_until_sheets_is_set_up(monkeypatch, tmp_path):
    from adapters.csv_log import CsvLog
    from app import make_store
    from tests.fakes import CONFIG

    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(tmp_path / "missing.json"))
    store, description = make_store(CONFIG)  # the demo config still says REPLACE_ME
    assert isinstance(store, CsvLog)
    assert "Google Sheets not set up" in description
