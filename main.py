"""Runs the agent against the Gmail inbox.

    python main.py                 dry run: reads real mail, replies and labels nothing
    python main.py --live          really replies and labels
    python main.py --once          check the inbox one time and stop

By default anyone who emails the inbox gets an answer. To restrict it, set
ALLOWED_SENDERS=a@x.com,b@y.com in .env.

Conversations are remembered in data/conversations.sqlite, so a restart continues them.
A dry run still writes to the local calendar, CSV/Sheets log and conversation memory;
it only holds back Gmail changes.
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

from adapters import gmail
from adapters.calendar_json import JsonCalendar
from app import build_app, make_checkpointer, make_store
from core import metrics
from core.config import load_client
from core.ratelimit import SenderLimiter
from core.single_instance import AlreadyRunning, hold_lock

LOCK_FILE = "data/agent.lock"
POLL_SECONDS = 30
MAX_EMAILS_PER_SENDER_PER_HOUR = 10
MAX_EMAILS_PER_DAY = 200  # all senders together; Groq's free tier allows about 1,000 LLM calls a day
ALL_SENDERS = "*"  # the key the daily limiter counts under


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


def send_owner_notice(inbox, config, subject, body):
    try:
        inbox.send_notice(config.escalation.notify_email, "[" + config.business.name + "] " + subject, body)
    except Exception as notice_error:
        log("  could not notify the owner: " + str(notice_error))


def notify_owner_of_failure(inbox, config, email, error):
    body = (
        "The agent could not finish handling this email and did not reply. Please answer it yourself.\n\n"
        "From: " + email["sender"] + "\n"
        "Subject: " + email["subject"] + "\n"
        "Error: " + str(error) + "\n\n"
        "Message:\n" + email["body"]
    )
    send_owner_notice(inbox, config, "The agent failed on an email", body)


def notify_owner_of_limit(inbox, config, sender, limiter):
    body = (
        sender
        + " has sent more than "
        + str(limiter.max_per_window)
        + " emails within "
        + str(limiter.window_seconds // 60)
        + " minutes. The agent is ignoring further emails from this address until the window passes. "
        "If this is a real customer, please reply yourself; if it is a robot, no action is needed."
    )
    send_owner_notice(inbox, config, "A sender went over the email limit", body)


def notify_owner_of_daily_limit(inbox, config, daily_limiter):
    body = (
        "The agent has handled "
        + str(daily_limiter.max_per_window)
        + " emails in the last 24 hours, its daily limit. Emails arriving now are labelled "
        + gmail.PROCESSED_LABEL
        + " but NOT answered. Please check the inbox and answer them yourself. "
        "The agent starts answering again as the 24-hour window moves on."
    )
    send_owner_notice(inbox, config, "Daily email limit reached", body)


def process_inbox(inbox, graph, config, seen, limiter=None, daily_limiter=None):
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

        # Caps how much any number of senders together can make the agent spend in a day.
        if daily_limiter is not None and not daily_limiter.allow(ALL_SENDERS):
            log("daily limit reached, not answering " + email["sender"])
            inbox.mark_processed(email["id"])
            if daily_limiter.first_block(ALL_SENDERS):
                notify_owner_of_daily_limit(inbox, config, daily_limiter)
            outcomes.append({"id": email["id"], "action": "daily_limit"})
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
        if daily_limiter is not None:
            daily_limiter.record(ALL_SENDERS)

        # Label first: if anything below crashes, the customer is never answered twice.
        inbox.mark_processed(email["id"])
        log("handling " + email["sender"] + ": " + email["subject"])
        calls_before, nodes_before = metrics.recorder.mark()

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

        summary = metrics.summarize(
            metrics.recorder.calls[calls_before:], metrics.recorder.node_times[nodes_before:]
        )
        log("  " + metrics.describe(summary))
    return outcomes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="really reply and label (default is a dry run)")
    parser.add_argument("--once", action="store_true", help="check the inbox once and stop")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS, help="seconds between checks")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    # Node failures are logged with their full traceback (see core/failures.py).
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs("data", exist_ok=True)
    try:
        lock = hold_lock(LOCK_FILE)  # kept for the life of the process
    except AlreadyRunning as error:
        print(str(error) + ". Stop the other copy first.")
        sys.exit(1)
    load_dotenv()
    allowed = parse_allowed_senders(os.environ.get("ALLOWED_SENDERS"))

    config = load_client("hvac_demo")
    inbox = gmail.connect(allowed_senders=allowed, dry_run=not args.live)
    calendar = JsonCalendar("data/calendar.json", config.business)
    store, store_name = make_store(config)
    graph = build_app(config, calendar, store, inbox, checkpointer=make_checkpointer())

    mode = "LIVE" if args.live else "DRY RUN (nothing is sent or labelled)"
    log("Agent started for " + inbox.own_address + " - " + mode)
    log("Primary LLM: " + os.environ.get("LLM_PRIMARY", "gemini") + " (the other is the backup)")
    log("Logging leads to: " + store_name)
    if len(allowed) > 0:
        log("Answering only: " + ", ".join(allowed))
    else:
        log("Open to everyone: it answers anyone who emails this inbox (set ALLOWED_SENDERS to restrict)")

    seen = set()
    limiter = SenderLimiter(max_per_window=MAX_EMAILS_PER_SENDER_PER_HOUR, window_seconds=3600)
    daily_limiter = SenderLimiter(max_per_window=MAX_EMAILS_PER_DAY, window_seconds=24 * 3600)
    log(
        "Limits: "
        + str(MAX_EMAILS_PER_SENDER_PER_HOUR)
        + " emails per sender per hour, "
        + str(MAX_EMAILS_PER_DAY)
        + " emails per day in total"
    )
    try:
        while True:
            try:
                process_inbox(inbox, graph, config, seen, limiter, daily_limiter)
            except Exception as error:
                log("Inbox check failed, will try again: " + str(error))
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("Stopped.")
    finally:
        lock.close()


if __name__ == "__main__":
    main()
