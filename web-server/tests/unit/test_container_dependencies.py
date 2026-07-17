import inspect

from src.services import container


def test_fastapi_dependency_providers_are_async():
    providers = [
        container.llm_adapter,
        container.session_manager,
        container.prompt_manager,
        container.context_manager,
        container.input_safety_gate,
        container.rule_engine,
        container.tool_executor,
        container.audit_logger,
        container.approval_bridge,
        container.error_recovery,
        container.db,
        container.lifecycle,
        container.agent_max_iterations,
        container.agent_token_ceiling_ratio,
    ]

    assert all(inspect.iscoroutinefunction(provider) for provider in providers)
