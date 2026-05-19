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


def build_graph(*, llm, executor, classifier, rule_engine, audit_logger, checkpointer) -> CompiledStateGraph:
    """构建 ReAct graph: think → review → act → observe → END。

    think 后路由：有 tool_call → review，无 → END。
    review 节点使用 interrupt() 在高风险操作时暂停等审批。
    checkpointer 为 interrupt/Command(resume) 提供状态持久化，必须显式传入。
    """
    if checkpointer is None:
        raise ValueError(
            "checkpointer is required. "
            "Use PostgresCheckpointer(db) for production or MemorySaver() for development only."
        )

    graph = StateGraph(AgentState)

    graph.add_node("think", partial(think_node, llm=llm, executor=executor, classifier=classifier))
    graph.add_node(
        "review",
        partial(review_node, classifier=classifier, rule_engine=rule_engine, audit_logger=audit_logger),
    )
    graph.add_node("act", partial(act_node, executor=executor, audit_logger=audit_logger))
    graph.add_node("observe", observe_node)

    graph.set_entry_point("think")
    graph.add_conditional_edges("think", route_after_think, {"review": "review", "observe": "observe", END: END})
    graph.add_edge("review", "act")
    graph.add_edge("act", "observe")
    graph.add_edge("observe", END)

    return graph.compile(checkpointer=checkpointer)
