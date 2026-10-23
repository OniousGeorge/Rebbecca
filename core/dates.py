"""Checking and describing the day a customer asked for."""
from datetime import date, timedelta

MAX_DAYS_AHEAD = 60
PERIODS = ("morning", "afternoon")


def today_text(now):
    """'Monday, 2026-10-19' - given to the LLM so it can turn 'Friday' into a real date."""
    return now.strftime("%A") + ", " + now.date().isoformat()


def parse_preferred_date(text, today):
    """A YYYY-MM-DD string from the LLM -> a date, or None if missing, malformed, past, or too far ahead."""
    if not text:
        return None
    try:
        wanted = date.fromisoformat(text.strip())
    except ValueError:
        return None
    if wanted < today or wanted > today + timedelta(days=MAX_DAYS_AHEAD):
        return None
    return wanted


def parse_period(text):
    if not text:
        return None
    cleaned = text.strip().lower()
    if cleaned in PERIODS:
        return cleaned
    return None


def describe_day(day, today):
    """'today', 'tomorrow' or 'on Friday Oct 23'"""
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return "on " + day.strftime("%A %b ") + str(day.day)


def period_of(start):
    if start.hour < 12:
        return "morning"
    return "afternoon"
