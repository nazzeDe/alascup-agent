from functools import partial

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agent.nodes import (
    act_node,
    observe_node,
    review_node,
    route_after_think,
    think_node,
)
from src.agent.state import AgentState


def build_graph(*, llm, executor, rule_engine, audit_logger, checkpointer) -> CompiledStateGraph:
    """Build ReAct graph: think → review → act → observe → END.

    After think: has tool_call → review, none → END.
    review_node uses interrupt() to pause on high-risk tools.
    checkpointer required for interrupt/Command(resume) state persistence.
    """
    if checkpointer is None:
        raise ValueError(
            "checkpointer is required. "
            "Use PostgresCheckpointer(db) for production or MemorySaver() for development only."
        )

    graph = StateGraph(AgentState)

    graph.add_node("think", partial(think_node, llm=llm, executor=executor))
    graph.add_node(
        "review",
        partial(review_node, executor=executor, rule_engine=rule_engine, audit_logger=audit_logger),
    )
    graph.add_node("act", partial(act_node, executor=executor, audit_logger=audit_logger))
    graph.add_node("observe", observe_node)

    graph.set_entry_point("think")
    graph.add_conditional_edges("think", route_after_think, {"review": "review", "observe": "observe", END: END})
    graph.add_edge("review", "act")
    graph.add_edge("act", "observe")
    graph.add_edge("observe", END)

    return graph.compile(checkpointer=checkpointer)
