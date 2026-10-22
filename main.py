"""Runs the agent against the Gmail inbox.

    python main.py                 dry run: reads real mail, replies and labels nothing
    python main.py --live          really replies and labels
    python main.py --once          check the inbox one time and stop

By default anyone who emails the inbox gets an answer. To restrict it, set
ALLOWED_SENDERS=a@x.com,b@y.com in .env.

A dry run still writes to the local calendar and CSV files; it only holds back Gmail changes.
"""
import argparse
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

from adapters import gmail
from adapters.calendar_json import JsonCalendar
from adapters.csv_log import CsvLog
from app import build_app
from core.config import load_client
from core.ratelimit import SenderLimiter

POLL_SECONDS = 30
MAX_EMAILS_PER_SENDER_PER_HOUR = 10


def log(text):
    print(datetime.now().strftime("%H:%M:%S") + "  " + text, flush=True)


def parse_allowed_senders(value):
    """'a@x.com, B@y.com' -> ['a@x.com', 'b@y.com']"""
    allowed = []
    for item in (value or "").split(","):
        item = item.strip().lower()
        if item != "":
            allowed.append(item)
    return allowed


def notify_owner_of_failure(inbox, config, email, error):
    subject = "[" + config.business.name + "] The agent failed on an email"
    body = (
        "The agent could not finish handling this email and did not reply. Please answer it yourself.\n\n"
        "From: " + email["sender"] + "\n"
        "Subject: " + email["subject"] + "\n"
        "Error: " + str(error) + "\n\n"
        "Message:\n" + email["body"]
    )
    try:
        inbox.send_notice(config.escalation.notify_email, subject, body)
    except Exception as notice_error:
        log("  could not notify the owner either: " + str(notice_error))


def notify_owner_of_limit(inbox, config, sender, limiter):
    subject = "[" + config.business.name + "] A sender went over the email limit"
    body = (
        sender
        + " has sent more than "
        + str(limiter.max_per_window)
        + " emails within "
        + str(limiter.window_seconds // 60)
        + " minutes. The agent is ignoring further emails from this address until the window passes. "
        "If this is a real customer, please reply yourself; if it is a robot, no action is needed."
    )
    try:
        inbox.send_notice(config.escalation.notify_email, subject, body)
    except Exception as notice_error:
        log("  could not notify the owner: " + str(notice_error))


def process_inbox(inbox, graph, config, seen, limiter=None):
    """Handle every new email once. Returns a list of what happened to each, for the logs and tests."""
    outcomes = []
    for email in inbox.fetch_new():
        if email["id"] in seen:
            continue
        seen.add(email["id"])

        if email["skip"] is not None:
            log("skipped " + email["sender"] + " (" + email["skip"] + ")")
            inbox.mark_processed(email["id"])
            outcomes.append({"id": email["id"], "action": "skipped"})
            continue

        # Stops a runaway sender (for example two robots answering each other) from using up our quotas.
        if limiter is not None:
            if not limiter.allow(email["sender"]):
                log("rate limited " + email["sender"])
                inbox.mark_processed(email["id"])
                if limiter.first_block(email["sender"]):
                    notify_owner_of_limit(inbox, config, email["sender"], limiter)
                outcomes.append({"id": email["id"], "action": "rate_limited"})
                continue
            limiter.record(email["sender"])

        # Label first: if anything below crashes, the customer is never answered twice.
        inbox.mark_processed(email["id"])
        log("handling " + email["sender"] + ": " + email["subject"])

        try:
            result = graph.invoke(
                {
                    "thread_id": email["thread_id"],
                    "sender": email["sender"],
                    "messages": [{"role": "customer", "text": email["body"]}],
                },
                {"configurable": {"thread_id": email["thread_id"]}},
            )
            reply = result.get("reply", "")
            if reply != "":
                inbox.send_reply(email, reply)
                action = "replied"
            else:
                action = "no_reply"
            log("  " + action + " (category: " + str(result.get("category")) + ")")
            if result.get("errors"):
                log("  node errors: " + "; ".join(result["errors"]))
            outcomes.append({"id": email["id"], "action": action})
        except Exception as error:
            log("  FAILED: " + str(error))
            inbox.mark_error(email["id"])
            notify_owner_of_failure(inbox, config, email, error)
            outcomes.append({"id": email["id"], "action": "error"})
    return outcomes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="really reply and label (default is a dry run)")
    parser.add_argument("--once", action="store_true", help="check the inbox once and stop")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS, help="seconds between checks")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    allowed = parse_allowed_senders(os.environ.get("ALLOWED_SENDERS"))

    config = load_client("hvac_demo")
    inbox = gmail.connect(allowed_senders=allowed, dry_run=not args.live)
    calendar = JsonCalendar("data/calendar.json", config.business)
    store = CsvLog("data/leads.csv", "data/bookings.csv")
    graph = build_app(config, calendar, store, inbox, checkpointer=MemorySaver())

    mode = "LIVE" if args.live else "DRY RUN (nothing is sent or labelled)"
    log("Agent started for " + inbox.own_address + " - " + mode)
    if len(allowed) > 0:
        log("Answering only: " + ", ".join(allowed))
    else:
        log("Open to everyone: it answers anyone who emails this inbox (set ALLOWED_SENDERS to restrict)")

    seen = set()
    limiter = SenderLimiter(max_per_window=MAX_EMAILS_PER_SENDER_PER_HOUR, window_seconds=3600)
    log("Limit: " + str(MAX_EMAILS_PER_SENDER_PER_HOUR) + " emails per sender per hour")
    try:
        while True:
            try:
                process_inbox(inbox, graph, config, seen, limiter)
            except Exception as error:
                log("Inbox check failed, will try again: " + str(error))
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
