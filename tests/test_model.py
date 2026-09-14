from unittest.mock import MagicMock, patch
import pytest
from agent.model import VertexGeminiModel
from agent.orchestrator import build_agent


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("GOOGLE_CLOUD_API_KEY", "test-cloud-api-key")
    monkeypatch.setenv("GEMINI_MODEL_ID", "gemini-2.0-flash")
    monkeypatch.setenv("SESSIONS_S3_BUCKET", "test-sessions-bucket")


def test_vertex_gemini_model_initialization():
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        model = VertexGeminiModel()

        mock_client_cls.assert_called_once_with(
            vertexai=True,
            api_key="test-cloud-api-key",
        )
        assert model.config["model_id"] == "gemini-2.0-flash"


def test_build_agent_configures_vertex_gemini():
    mock_session_mgr = MagicMock()
    with patch("google.genai.Client"), patch(
        "agent.orchestrator.S3SessionManager", return_value=mock_session_mgr
    ):
        agent = build_agent(session_id="sub-test-123")
        assert isinstance(agent.model, VertexGeminiModel)
        assert len(agent.tool_names) == 4


def test_no_forbidden_llm_libraries_in_agent():
    import importlib
    import agent.model
    import agent.orchestrator

    agent_modules = [agent.model, agent.orchestrator]
    forbidden_terms = ["bedrock", "openai", "anthropic"]

    for mod in agent_modules:
        source_code = inspect_source(mod).lower()
        for term in forbidden_terms:
            assert f"import {term}" not in source_code
            assert f"from {term}" not in source_code


def inspect_source(mod):
    import inspect
    return inspect.getsource(mod)
