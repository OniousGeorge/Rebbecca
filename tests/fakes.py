"""Stand-ins used by the tests so nothing touches the network."""
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import load_client

CONFIG = load_client("hvac_demo")
TIMEZONE = ZoneInfo(CONFIG.business.timezone)

# Monday 19 Oct 2026, 7:00 AM Eastern. With the 2 hour lead time the first slot is 9:30 AM.
NOW = datetime(2026, 10, 19, 7, 0, tzinfo=TIMEZONE)
MON_0930 = "2026-10-19T09:30:00-04:00"
MON_1100 = "2026-10-19T11:00:00-04:00"
MON_1230 = "2026-10-19T12:30:00-04:00"
MON_1400 = "2026-10-19T14:00:00-04:00"


def fixed_now():
    return NOW


class ScriptedLLM:
    """Returns the prepared answers one by one. An Exception in the list is raised instead."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if len(self.answers) == 0:
            raise AssertionError("the LLM was called more times than the test expected")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class RecordingMailer:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_notice(self, to, subject, body):
        if self.fail:
            raise RuntimeError("mail server down")
        self.sent.append({"to": to, "subject": subject, "body": body})


def customer(text):
    return {"role": "customer", "text": text}


def agent(text):
    return {"role": "agent", "text": text}
