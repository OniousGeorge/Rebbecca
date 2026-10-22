"""Assembles the real agent: config + adapters + nodes + graph."""
from agents.escalation import make_escalation
from agents.faq import make_faq
from agents.intake import make_intake
from agents.logger import make_logger
from agents.router import make_router
from agents.scheduler import make_scheduler
from graph import build_graph


def build_app(config, calendar, store, mailer, llms=None, checkpointer=None, now_fn=None):
    """`llms` can map "router", "intake", "faq", "scheduler" to fake models for tests."""
    if llms is None:
        llms = {}

    nodes = {
        "router": make_router(config, llms.get("router")),
        "intake": make_intake(config, llms.get("intake")),
        "faq": make_faq(config, llms.get("faq")),
        "scheduler": make_scheduler(config, calendar, llms.get("scheduler")),
        "logger": make_logger(store, config, now_fn),
        "escalation": make_escalation(config, mailer),
    }
    return build_graph(checkpointer=checkpointer, nodes=nodes)
