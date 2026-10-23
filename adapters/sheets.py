"""Google Sheets CRM log. Same two methods as CsvLog, so the agent nodes do not change.

Logs in with a service account: share the Sheet with the service account's email as Editor.
"""
import os

from adapters.csv_log import BOOKING_COLUMNS, LEAD_COLUMNS

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LEADS_TAB = "Leads"
BOOKINGS_TAB = "Bookings"
DEFAULT_KEY_FILE = "service_account.json"


def column_letter(number):
    """1 -> A, 12 -> L"""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def connect(spreadsheet_id, key_file=None):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if key_file is None:
        key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", DEFAULT_KEY_FILE)
    credentials = service_account.Credentials.from_service_account_file(key_file, scopes=SCOPES)
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    return SheetsLog(service, spreadsheet_id)


class SheetsLog:
    def __init__(self, service, spreadsheet_id):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.headers_checked = set()

    def _values(self):
        return self.service.spreadsheets().values()

    def _read(self, tab, columns):
        end = column_letter(len(columns))
        result = self._values().get(spreadsheetId=self.spreadsheet_id, range=tab + "!A1:" + end).execute()
        return result.get("values", [])

    def _ensure_header(self, tab, columns):
        """Write the column names into row 1 the first time, if the tab is empty."""
        if tab in self.headers_checked:
            return
        rows = self._read(tab, columns)
        if len(rows) == 0 or rows[0] != columns:
            end = column_letter(len(columns))
            self._values().update(
                spreadsheetId=self.spreadsheet_id,
                range=tab + "!A1:" + end + "1",
                valueInputOption="RAW",
                body={"values": [columns]},
            ).execute()
        self.headers_checked.add(tab)

    def _upsert(self, tab, columns, row):
        """Update the row whose first column matches, or add a new row at the bottom."""
        self._ensure_header(tab, columns)
        end = column_letter(len(columns))

        # RAW keeps customer text as plain text. Without it, a message starting with "=" would run as a formula.
        cells = []
        for column in columns:
            cells.append(str(row.get(column, "")))

        existing = self._read(tab, columns)
        for index in range(1, len(existing)):  # row 0 is the header
            if len(existing[index]) > 0 and existing[index][0] == cells[0]:
                sheet_row = index + 1
                self._values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=tab + "!A" + str(sheet_row) + ":" + end + str(sheet_row),
                    valueInputOption="RAW",
                    body={"values": [cells]},
                ).execute()
                return

        self._values().append(
            spreadsheetId=self.spreadsheet_id,
            range=tab + "!A1:" + end,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [cells]},
        ).execute()

    def upsert_lead(self, thread_id, row):
        full_row = {"thread_id": thread_id}
        full_row.update(row)
        self._upsert(LEADS_TAB, LEAD_COLUMNS, full_row)

    def add_booking(self, thread_id, booking):
        row = {"booking_id": booking["id"], "thread_id": thread_id}
        for column in BOOKING_COLUMNS:
            if column not in row:
                row[column] = booking.get(column, "")
        self._upsert(BOOKINGS_TAB, BOOKING_COLUMNS, row)
