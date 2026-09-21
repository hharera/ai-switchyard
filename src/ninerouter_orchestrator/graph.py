from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .models import Plan, ReviewReport


class WorkflowState(TypedDict, total=False):
    request: str
    plan: Plan
    current_ticket: int
    merged_tickets: list[str]
    review: ReviewReport
    status: str


def build_workflow(nodes=None):
    """Compile topology. Execution requires explicit stage implementations."""

    def unbound(state):
        raise RuntimeError("Graph topology only: bind runtime stages before invocation")

    graph = StateGraph(WorkflowState)
    for stage in (
        "plan",
        "execute",
        "validate",
        "synthesize",
        "merge",
        "integration_test",
        "review",
        "repair",
    ):
        graph.add_node(stage, (nodes or {}).get(stage, unbound))
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "validate")
    graph.add_edge("validate", "synthesize")
    graph.add_edge("synthesize", "merge")
    graph.add_edge("merge", "integration_test")
    graph.add_edge("integration_test", "review")
    graph.add_conditional_edges(
        "review",
        lambda state: "done" if state.get("review") and state["review"].approved else "repair",
        {"done": END, "repair": "repair"},
    )
    graph.add_edge("repair", "execute")
    return graph.compile()
