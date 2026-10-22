from datetime import datetime

import pytest

from adapters.calendar_json import JsonCalendar, describe_slot
from tests.fakes import CONFIG, MON_0930, MON_1100, MON_1230, MON_1400, TIMEZONE, fixed_now

LEAD = {"name": "Sam Lee", "contact": "sam@example.com", "address": "12 Oak St", "issue": "AC broken"}


@pytest.fixture
def calendar(tmp_path):
    return JsonCalendar(tmp_path / "calendar.json", CONFIG.business, now_fn=fixed_now)


def test_open_slots_respect_hours_and_lead_time(calendar):
    # Now is 7:00 AM, so 8:00 is too soon (2 hour lead time) and the first slot is 9:30.
    assert calendar.get_open_slots(3) == [MON_0930, MON_1100, MON_1230]


def test_booked_slot_is_no_longer_offered(calendar):
    calendar.book_slot(MON_0930, LEAD)
    assert calendar.get_open_slots(3) == [MON_1100, MON_1230, MON_1400]


def test_cannot_book_the_same_slot_twice(calendar):
    first = calendar.book_slot(MON_0930, LEAD)
    second = calendar.book_slot(MON_0930, LEAD)
    assert first is not None
    assert second is None


@pytest.mark.parametrize(
    "slot",
    [
        "2026-10-19T19:00:00-04:00",  # after closing
        "2026-10-19T09:45:00-04:00",  # not on the appointment grid
        "2026-10-19T08:00:00-04:00",  # too soon after now
        "2026-10-25T10:00:00-04:00",  # Sunday, closed
        "2026-10-24T12:00:00-04:00",  # Saturday slot would end after 1 PM close
    ],
)
def test_rejects_slots_outside_the_rules(calendar, slot):
    assert calendar.book_slot(slot, LEAD) is None


def test_saturday_has_short_hours_and_sunday_is_skipped():
    friday_afternoon = datetime(2026, 10, 23, 16, 0, tzinfo=TIMEZONE)
    calendar = JsonCalendar("unused.json", CONFIG.business, now_fn=lambda: friday_afternoon)
    slots = calendar.get_open_slots(3)
    assert slots == [
        "2026-10-24T09:00:00-04:00",
        "2026-10-24T10:30:00-04:00",
        "2026-10-26T08:00:00-04:00",  # Monday: Sunday was skipped
    ]


def test_after_skips_earlier_slots(calendar):
    assert calendar.get_open_slots(2, after=MON_1100) == [MON_1230, MON_1400]


def test_cancel_frees_the_slot_and_removes_the_booking(calendar):
    booking = calendar.book_slot(MON_0930, LEAD)
    assert calendar.cancel_booking(booking["id"])["status"] == "cancelled"
    assert calendar.cancel_booking(booking["id"]) is None  # already cancelled
    assert calendar.find_booking("sam@example.com") is None
    assert MON_0930 in calendar.get_open_slots(1)


def test_find_booking_ignores_case_and_returns_the_next_one(calendar):
    calendar.book_slot(MON_1100, LEAD)
    calendar.book_slot(MON_0930, LEAD)
    found = calendar.find_booking("SAM@Example.com")
    assert found["start"] == MON_0930


def test_bookings_survive_a_restart(tmp_path):
    path = tmp_path / "calendar.json"
    JsonCalendar(path, CONFIG.business, now_fn=fixed_now).book_slot(MON_0930, LEAD)
    reopened = JsonCalendar(path, CONFIG.business, now_fn=fixed_now)
    assert reopened.find_booking("sam@example.com") is not None


def test_describe_slot():
    assert describe_slot(MON_0930) == "Mon Oct 19 at 9:30 AM"
    assert describe_slot(MON_1400) == "Mon Oct 19 at 2:00 PM"
