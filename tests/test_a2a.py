"""Tests for the demo A2A reviewer and CodeCross A2A client."""

from fastapi.testclient import TestClient

from a2aproject.server import app
from codecross.a2a import _extract_reviewer_result


def test_a2a_agent_card_and_message_send():
    """The demo A2A reviewer should expose discovery metadata and artifacts."""

    client = TestClient(app)
    card = client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert card.json()["url"].endswith("/a2a")

    response = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "test-1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {
                                "reviewer": {
                                    "name": "a2a_logic_reviewer",
                                    "provider": "deepseek",
                                    "model": "deepseek-chat",
                                },
                                "llm": {},
                                "diff": (
                                    "diff --git a/pkg/math.py b/pkg/math.py\n"
                                    "+++ b/pkg/math.py\n"
                                    "+def safe_divide(a, b):\n"
                                    "+    return a / b\n"
                                ),
                                "review_plan": {},
                                "code_context": None,
                            },
                        }
                    ],
                }
            },
        },
    )

    assert response.status_code == 200
    result = _extract_reviewer_result(response.json())
    assert result.reviewer_name == "a2a_logic_reviewer"
    assert result.findings
    assert result.test_intents
