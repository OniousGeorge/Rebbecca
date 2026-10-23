"""Try the agent in the terminal, playing the customer. Uses the real LLMs.

    python chat.py

Bookings go to data/calendar.json, leads to data/leads.csv, escalation notices print to the screen.
Type "quit" to stop.
"""
import sys

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

from adapters.calendar_json import JsonCalendar
from adapters.console_mail import ConsoleMail
from app import build_app, make_store
from core.config import load_client

CUSTOMER_EMAIL = "customer@example.com"
THREAD_ID = "terminal-demo"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    config = load_client("hvac_demo")
    calendar = JsonCalendar("data/calendar.json", config.business)
    store, store_name = make_store(config)
    print("Logging leads to: " + store_name)
    graph = build_app(config, calendar, store, ConsoleMail(), checkpointer=MemorySaver())
    run_config = {"configurable": {"thread_id": THREAD_ID}}

    print("You are a customer of " + config.business.name + ". Type an email, or 'quit'.\n")
    while True:
        text = input("Customer> ").strip()
        if text.lower() == "quit":
            break
        if text == "":
            continue
        # trace keeps the whole conversation's history, so remember where this email starts.
        earlier_steps = len(graph.get_state(run_config).values.get("trace", []))
        result = graph.invoke(
            {
                "thread_id": THREAD_ID,
                "sender": CUSTOMER_EMAIL,
                "messages": [{"role": "customer", "text": text}],
            },
            run_config,
        )
        print("Path: " + " -> ".join(result["trace"][earlier_steps:]))
        if result.get("reply"):
            print("\nAgent> " + result["reply"] + "\n")
        else:
            print("\n(no reply sent)\n")


if __name__ == "__main__":
    main()
