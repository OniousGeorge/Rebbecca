"""Assembles the real agent: config + adapters + nodes + graph."""
import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from adapters import sheets
from adapters.csv_log import CsvLog
from agents.escalation import make_escalation
from agents.faq import make_faq
from agents.intake import make_intake
from agents.logger import make_logger
from agents.router import make_router
from agents.scheduler import make_scheduler
from core import metrics
from graph import build_graph


def make_store(config):
    """The CRM log: Google Sheets if it is set up, otherwise local CSV files. Returns (store, description)."""
    sheet_id = config.sheets.spreadsheet_id
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", sheets.DEFAULT_KEY_FILE)
    if sheet_id != "REPLACE_ME" and os.path.exists(key_file):
        return sheets.connect(sheet_id, key_file), "Google Sheets"
    return CsvLog("data/leads.csv", "data/bookings.csv"), "local CSV files in data/ (Google Sheets not set up)"


def make_checkpointer(path="data/conversations.sqlite"):
    """Conversation memory on disk, so restarting the agent does not forget conversations in progress."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(connection)


def build_app(config, calendar, store, mailer, llms=None, checkpointer=None, now_fn=None):
    """`llms` can map "router", "intake", "faq", "scheduler" to fake models for tests."""
    if llms is None:
        llms = {}

    nodes = {
        "router": make_router(config, llms.get("router")),
        "intake": make_intake(config, llms.get("intake"), now_fn),
        "faq": make_faq(config, llms.get("faq")),
        "scheduler": make_scheduler(config, calendar, llms.get("scheduler")),
        "logger": make_logger(store, config, now_fn),
        "escalation": make_escalation(config, mailer),
    }
    # Record how long each node takes (see core/metrics.py).
    for name in nodes:
        nodes[name] = metrics.recorder.timed(name, nodes[name])
    return build_graph(checkpointer=checkpointer, nodes=nodes)
