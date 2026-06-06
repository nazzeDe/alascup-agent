from functools import partial

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agent.nodes import (
    act_node,
    observe_node,
    review_node,
    route_after_review,
    route_after_think,
    think_node,
)
from src.agent.state import AgentState


def build_graph(*, llm, executor, rule_engine, audit_logger, lifecycle=None) -> CompiledStateGraph:
    """Build ReAct graph: think → review → act → observe → END.

    After think: has tool_call → review, has approved_tool_calls → act, none → END.
    The orchestrator owns the approval loop externally via _drain_approval_loop().
    """
    graph = StateGraph(AgentState)

    graph.add_node("think", partial(think_node, llm=llm, executor=executor, lifecycle=lifecycle))
    graph.add_node(
        "review",
        partial(review_node, executor=executor, rule_engine=rule_engine, audit_logger=audit_logger, lifecycle=lifecycle),
    )
    graph.add_node("act", partial(act_node, executor=executor, audit_logger=audit_logger, lifecycle=lifecycle))
    graph.add_node("observe", observe_node)

    graph.set_entry_point("think")
    graph.add_conditional_edges("think", route_after_think, {
        "review": "review",
        "act": "act",
        "observe": "observe",
        END: END,
    })
    graph.add_conditional_edges("review", route_after_review, {
        "act": "act",
        END: END,
    })
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "think")

    return graph.compile()
