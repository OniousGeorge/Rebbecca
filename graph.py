"""Builds the agent graph. Any node not supplied is a placeholder that only records that it ran."""
from langgraph.graph import END, START, StateGraph

from core.state import State

NODE_NAMES = ("router", "intake", "faq", "scheduler", "logger", "escalation")


def _placeholder(name: str):
    """A stand-in node that only records that it ran."""

    def node(state: State) -> dict:
        return {"trace": [name]}

    return node


def route_after_router(state: State) -> str:
    return state["category"]


def route_after_intake(state: State) -> str:
    if state.get("out_of_area"):
        return "logged"  # told the customer we do not serve them; just record the lead
    if state.get("escalated"):
        return "escalation"
    if state.get("missing_fields"):
        return "ask"  # the reply already asks the customer for the missing details
    return "scheduler"


def route_after_faq(state: State) -> str:
    if state.get("escalated"):
        return "escalation"
    return "done"


def route_after_scheduler(state: State) -> str:
    if state.get("escalated"):
        return "escalation"
    if state.get("booking"):
        return "logger"
    return "waiting"  # slots offered; wait for the customer's reply


def build_graph(checkpointer=None, nodes=None):
    """Compile the graph. `nodes` maps a node name to the function that should run there."""
    if nodes is None:
        nodes = {}

    builder = StateGraph(State)
    for name in NODE_NAMES:
        node = nodes.get(name)
        if node is None:
            node = _placeholder(name)
        builder.add_node(name, node)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {
            "new_lead": "intake",
            "faq": "faq",
            "reschedule": "scheduler",
            "cancel": "scheduler",
            "escalate": "escalation",
            "spam": END,
        },
    )
    builder.add_conditional_edges(
        "intake",
        route_after_intake,
        {"scheduler": "scheduler", "ask": END, "logged": "logger", "escalation": "escalation"},
    )
    builder.add_conditional_edges("faq", route_after_faq, {"escalation": "escalation", "done": END})
    builder.add_conditional_edges(
        "scheduler",
        route_after_scheduler,
        {"logger": "logger", "escalation": "escalation", "waiting": END},
    )
    builder.add_edge("escalation", "logger")  # every escalated conversation is recorded
    builder.add_edge("logger", END)

    return builder.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    # Print the graph as a Mermaid diagram: python graph.py
    print(build_graph().get_graph().draw_mermaid())
