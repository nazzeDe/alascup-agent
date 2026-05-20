from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_request():
    req = MagicMock()
    return req


class TestServicesDataclass:
    def test_default_db_is_none(self):
        from src.services.container import Services

        svc = Services(
            llm_adapter=None,
            session_manager=MagicMock(),
            prompt_manager=None,
            context_manager=None,
            rule_engine=None,
            tool_executor=None,
            audit_logger=MagicMock(),
            classifier=None,
            approval_bridge=None,
            checkpointer=None,
            graph=None,
        )
        assert svc.db is None

    def test_db_can_be_set(self):
        from src.services.container import Services

        db = MagicMock()
        svc = Services(
            llm_adapter=None,
            session_manager=MagicMock(),
            prompt_manager=None,
            context_manager=None,
            rule_engine=None,
            tool_executor=None,
            audit_logger=MagicMock(),
            classifier=None,
            approval_bridge=None,
            checkpointer=None,
            graph=None,
            db=db,
        )
        assert svc.db is db


class TestDependencyFunctions:
    def test_session_manager_dep(self, mock_request):
        from src.services.container import Services, session_manager

        mock_sm = MagicMock()
        svc = Services(
            llm_adapter=None, session_manager=mock_sm, prompt_manager=None,
            context_manager=None, rule_engine=None, tool_executor=None,
            audit_logger=MagicMock(), classifier=None, approval_bridge=None,
            checkpointer=None, graph=None,
        )
        mock_request.app.state.services = svc

        result = session_manager(mock_request)
        assert result is mock_sm

    def test_audit_logger_dep(self, mock_request):
        from src.services.container import Services, audit_logger

        mock_al = MagicMock()
        svc = Services(
            llm_adapter=None, session_manager=MagicMock(), prompt_manager=None,
            context_manager=None, rule_engine=None, tool_executor=None,
            audit_logger=mock_al, classifier=None, approval_bridge=None,
            checkpointer=None, graph=None,
        )
        mock_request.app.state.services = svc

        result = audit_logger(mock_request)
        assert result is mock_al

    def test_approval_bridge_dep(self, mock_request):
        from src.services.container import Services, approval_bridge

        mock_ab = MagicMock()
        svc = Services(
            llm_adapter=None, session_manager=MagicMock(), prompt_manager=None,
            context_manager=None, rule_engine=None, tool_executor=None,
            audit_logger=MagicMock(), classifier=None, approval_bridge=mock_ab,
            checkpointer=None, graph=None,
        )
        mock_request.app.state.services = svc

        result = approval_bridge(mock_request)
        assert result is mock_ab

    def test_llm_adapter_dep(self, mock_request):
        from src.services.container import Services, llm_adapter

        mock_llm = MagicMock()
        svc = Services(
            llm_adapter=mock_llm, session_manager=MagicMock(), prompt_manager=None,
            context_manager=None, rule_engine=None, tool_executor=None,
            audit_logger=MagicMock(), classifier=None, approval_bridge=None,
            checkpointer=None, graph=None,
        )
        mock_request.app.state.services = svc

        result = llm_adapter(mock_request)
        assert result is mock_llm

    def test_rule_engine_dep(self, mock_request):
        from src.services.container import Services, rule_engine

        mock_re = MagicMock()
        svc = Services(
            llm_adapter=None, session_manager=MagicMock(), prompt_manager=None,
            context_manager=None, rule_engine=mock_re, tool_executor=None,
            audit_logger=MagicMock(), classifier=None, approval_bridge=None,
            checkpointer=None, graph=None,
        )
        mock_request.app.state.services = svc

        result = rule_engine(mock_request)
        assert result is mock_re

    def test_classifier_dep(self, mock_request):
        from src.services.container import Services, classifier

        mock_cl = MagicMock()
        svc = Services(
            llm_adapter=None, session_manager=MagicMock(), prompt_manager=None,
            context_manager=None, rule_engine=None, tool_executor=None,
            audit_logger=MagicMock(), classifier=mock_cl, approval_bridge=None,
            checkpointer=None, graph=None,
        )
        mock_request.app.state.services = svc

        result = classifier(mock_request)
        assert result is mock_cl

    def test_graph_dep(self, mock_request):
        from src.services.container import Services, graph

        mock_g = MagicMock()
        svc = Services(
            llm_adapter=None, session_manager=MagicMock(), prompt_manager=None,
            context_manager=None, rule_engine=None, tool_executor=None,
            audit_logger=MagicMock(), classifier=None, approval_bridge=None,
            checkpointer=None, graph=mock_g,
        )
        mock_request.app.state.services = svc

        result = graph(mock_request)
        assert result is mock_g
