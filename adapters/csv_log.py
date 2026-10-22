"""CRM log in two CSV files. Same interface the Google Sheets adapter will have."""
import csv
from pathlib import Path

LEAD_COLUMNS = [
    "thread_id",
    "updated_at",
    "name",
    "contact",
    "phone",
    "address",
    "issue",
    "urgency",
    "category",
    "status",
    "escalated",
    "reason",
]

BOOKING_COLUMNS = ["booking_id", "thread_id", "start", "end", "name", "contact", "address", "issue", "status"]


class CsvLog:
    def __init__(self, leads_path, bookings_path):
        self.leads_path = Path(leads_path)
        self.bookings_path = Path(bookings_path)

    def _read(self, path):
        if not path.exists():
            return []
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def _write(self, path, columns, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def _upsert(self, path, columns, key, new_row):
        rows = self._read(path)
        replaced = False
        for index in range(len(rows)):
            if rows[index][key] == new_row[key]:
                rows[index] = new_row
                replaced = True
        if not replaced:
            rows.append(new_row)
        self._write(path, columns, rows)

    def upsert_lead(self, thread_id, row):
        """One row per conversation: update it if the thread is already logged."""
        full_row = {"thread_id": thread_id}
        for column in LEAD_COLUMNS:
            if column != "thread_id":
                full_row[column] = row.get(column, "")
        self._upsert(self.leads_path, LEAD_COLUMNS, "thread_id", full_row)

    def add_booking(self, thread_id, booking):
        """One row per booking id: update it (for example to 'cancelled') if already logged."""
        row = {"booking_id": booking["id"], "thread_id": thread_id}
        for column in BOOKING_COLUMNS:
            if column not in row:
                row[column] = booking.get(column, "")
        self._upsert(self.bookings_path, BOOKING_COLUMNS, "booking_id", row)
