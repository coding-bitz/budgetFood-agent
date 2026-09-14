import json
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def mock_server_env(monkeypatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_TestPool")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "testclientid12345")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


@pytest.fixture
def client():
    # Set environment variables before importing app
    import app
    app.COGNITO_USER_POOL_ID = "us-east-1_TestPool"
    app.COGNITO_APP_CLIENT_ID = "testclientid12345"
    app.AWS_REGION = "us-east-1"
    app.app.config["TESTING"] = True
    with app.app.test_client() as test_client:
        yield test_client


def test_index_route_injects_cognito_config(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "us-east-1_TestPool" in html
    assert "testclientid12345" in html
    assert "us-east-1" in html
    assert "{{COGNITO_USER_POOL_ID}}" not in html
    assert "{{COGNITO_APP_CLIENT_ID}}" not in html


def test_chat_missing_authorization_header(client):
    response = client.post("/chat", json={"prompt": "healthy lunch near 5th ave"})
    assert response.status_code == 401
    data = response.get_json()
    assert "Missing or invalid Authorization header" in data["error"]


def test_chat_invalid_authorization_header(client):
    response = client.post(
        "/chat",
        headers={"Authorization": "Basic invalid-token"},
        json={"prompt": "lunch"},
    )
    assert response.status_code == 401


def test_chat_token_validation_failure(client):
    with patch("app._validate_cognito_token", side_effect=ValueError("Token expired")):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer bad.token.here"},
            json={"prompt": "dinner"},
        )
        assert response.status_code == 401
        data = response.get_json()
        assert "Invalid token" in data["error"]


def test_chat_missing_prompt(client):
    with patch("app._validate_cognito_token", return_value={"sub": "user-test-sub"}):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer valid.token.here"},
            json={},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "Missing 'prompt'" in data["error"]


def test_chat_successful_invocation(client):
    mock_agent = MagicMock()
    mock_agent.return_value = "Proposal: 2 meals under $40"

    with patch("app._validate_cognito_token", return_value={"sub": "user-test-sub"}):
        with patch("app.build_agent", return_value=mock_agent) as mock_build_agent:
            response = client.post(
                "/chat",
                headers={"Authorization": "Bearer valid.token.here"},
                json={"prompt": "40 dollars lunch and dinner"},
            )

            assert response.status_code == 200
            data = response.get_json()
            assert "Proposal: 2 meals under $40" in data["response"]
            mock_build_agent.assert_called_once_with(session_id="user-test-sub")
            mock_agent.assert_called_once_with("40 dollars lunch and dinner")
