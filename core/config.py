"""Load and validate a per-client YAML config."""
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, field_validator

CLIENTS_DIR = Path(__file__).resolve().parent.parent / "config" / "clients"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# YAML Business Shape
class Business(BaseModel):
    name: str
    tone: str
    timezone: str
    hours: dict[str, tuple[str, str]]
    service_area: list[str]
    services: list[str]
    appointment_minutes: int = 90

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError(f"unknown timezone: {value}")
        return value

    @field_validator("hours")
    @classmethod
    def _valid_hours(cls, value: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
        for day, (opens, closes) in value.items(): # splits the times between open and closing
            if day not in DAYS:
                raise ValueError(f"unknown day: {day}")
            if time.fromisoformat(opens) >= time.fromisoformat(closes): #converts values into real time to ensure they make sense
                raise ValueError(f"{day}: opening time must be before closing time")
        return value


class FAQ(BaseModel):
    q: str
    a: str


class Escalation(BaseModel):
    notify_email: str
    emergency_keywords: list[str]


class Sheets(BaseModel):
    spreadsheet_id: str


class ClientConfig(BaseModel):
    business: Business
    faqs: list[FAQ]
    pricing: dict[str, str]
    urgency_levels: list[str]
    escalation: Escalation
    sheets: Sheets


DAY_NAMES = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


def format_time(clock: time) -> str:
    """08:00 -> '8:00 AM'"""
    hour = clock.hour % 12
    if hour == 0:
        hour = 12
    suffix = "AM"
    if clock.hour >= 12:
        suffix = "PM"
    return str(hour) + ":" + str(clock.minute).zfill(2) + " " + suffix


def describe_hours(business: Business) -> str:
    lines = []
    for day in DAYS:
        name = DAY_NAMES[day]
        if day in business.hours:
            opens, closes = business.hours[day]
            lines.append(
                name + ": " + format_time(time.fromisoformat(opens)) + " to " + format_time(time.fromisoformat(closes))
            )
        else:
            lines.append(name + ": closed")
    return "\n".join(lines)


def load_config(path: Path) -> ClientConfig:
    with open(path, encoding="utf-8") as f:
        return ClientConfig.model_validate(yaml.safe_load(f))


def load_client(name: str) -> ClientConfig:
    return load_config(CLIENTS_DIR / f"{name}.yaml")
