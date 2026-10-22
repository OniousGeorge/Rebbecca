"""Local calendar stored in a JSON file. Same interface a Google Calendar adapter would have."""
import json
import uuid
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

from core.config import DAYS, Business, format_time

MIN_LEAD_HOURS = 2  # no slots sooner than this from now
SEARCH_DAYS = 14  # how far ahead to look for open slots


def describe_slot(iso: str) -> str:
    """'2026-10-19T09:30:00-04:00' -> 'Mon Oct 19 at 9:30 AM'"""
    start = datetime.fromisoformat(iso)
    return start.strftime("%a %b ") + str(start.day) + " at " + format_time(start.time())


class JsonCalendar:
    def __init__(self, path, business: Business, now_fn=None):
        self.path = Path(path)
        self.business = business
        self.timezone = ZoneInfo(business.timezone)
        self.now_fn = now_fn

    # --- storage -------------------------------------------------------------
    def _load(self):
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, bookings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(bookings, f, indent=2)

    def _now(self):
        if self.now_fn is not None:
            return self.now_fn()
        return datetime.now(self.timezone)

    # --- slot grid -----------------------------------------------------------
    def _slots_on(self, day):
        """Every appointment start time on one calendar day, from the business hours."""
        key = DAYS[day.weekday()]
        if key not in self.business.hours:
            return []
        opens, closes = self.business.hours[key]
        step = timedelta(minutes=self.business.appointment_minutes)
        start = datetime.combine(day, time.fromisoformat(opens), tzinfo=self.timezone)
        close = datetime.combine(day, time.fromisoformat(closes), tzinfo=self.timezone)

        slots = []
        while start + step <= close:
            slots.append(start)
            start = start + step
        return slots

    def _is_free(self, start, bookings):
        end = start + timedelta(minutes=self.business.appointment_minutes)
        for booking in bookings:
            if booking["status"] != "booked":
                continue
            booked_start = datetime.fromisoformat(booking["start"])
            booked_end = datetime.fromisoformat(booking["end"])
            if start < booked_end and booked_start < end:
                return False
        return True

    # --- public interface ----------------------------------------------------
    def get_open_slots(self, count=3, after=None):
        """Next open appointment starts as ISO strings, optionally only those after a given slot."""
        bookings = self._load()
        earliest = self._now() + timedelta(hours=MIN_LEAD_HOURS)
        after_dt = None
        if after is not None:
            after_dt = datetime.fromisoformat(after)

        found = []
        day = earliest.astimezone(self.timezone).date()
        for _ in range(SEARCH_DAYS):
            for slot in self._slots_on(day):
                if slot < earliest:
                    continue
                if after_dt is not None and slot <= after_dt:
                    continue
                if not self._is_free(slot, bookings):
                    continue
                found.append(slot.isoformat())
                if len(found) == count:
                    return found
            day = day + timedelta(days=1)
        return found

    def book_slot(self, slot_iso, lead):
        """Book a slot. Re-checks everything first; returns None if it is not bookable."""
        start = datetime.fromisoformat(slot_iso).astimezone(self.timezone)
        earliest = self._now() + timedelta(hours=MIN_LEAD_HOURS)
        if start < earliest:
            return None
        if start not in self._slots_on(start.date()):
            return None  # outside business hours or not on the appointment grid

        bookings = self._load()
        if not self._is_free(start, bookings):
            return None

        end = start + timedelta(minutes=self.business.appointment_minutes)
        booking = {
            "id": "bk-" + uuid.uuid4().hex[:8],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "name": lead.get("name", ""),
            "contact": lead.get("contact", ""),
            "address": lead.get("address", ""),
            "issue": lead.get("issue", ""),
            "status": "booked",
        }
        bookings.append(booking)
        self._save(bookings)
        return booking

    def cancel_booking(self, booking_id):
        """Mark a booking cancelled and return it, or None if there is no such booked appointment."""
        bookings = self._load()
        for booking in bookings:
            if booking["id"] == booking_id and booking["status"] == "booked":
                booking["status"] = "cancelled"
                self._save(bookings)
                return booking
        return None

    def find_booking(self, contact):
        """The customer's next upcoming booked appointment, or None."""
        now = self._now()
        matches = []
        for booking in self._load():
            if booking["status"] != "booked":
                continue
            if booking["contact"].lower() != contact.lower():
                continue
            if datetime.fromisoformat(booking["end"]) <= now:
                continue
            matches.append(booking)
        if len(matches) == 0:
            return None
        matches.sort(key=lambda b: b["start"])
        return matches[0]
