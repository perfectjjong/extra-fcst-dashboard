import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.server import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestChatEndpoints:

    @patch("api.server._chat_bridge")
    def test_post_chat_returns_reply(self, mock_bridge, client):
        mock_bridge.chat.return_value = {
            "session_id": "abc123",
            "reply": "시뮬레이션 결과입니다.",
            "method": "claude",
        }
        resp = client.post("/api/chat", json={"message": "폭염 시나리오"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reply"] == "시뮬레이션 결과입니다."
        assert data["session_id"] == "abc123"

    @patch("api.server._chat_bridge")
    def test_post_chat_empty_message(self, mock_bridge, client):
        resp = client.post("/api/chat", json={"message": ""})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "입력" in data["reply"]

    @patch("api.server._chat_bridge")
    def test_post_chat_follow(self, mock_bridge, client):
        mock_bridge.chat.return_value = {
            "session_id": "abc123",
            "reply": "후속 답변입니다.",
            "method": "claude",
        }
        resp = client.post("/api/chat/follow", json={
            "session_id": "abc123",
            "message": "더 자세히",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reply"] == "후속 답변입니다."
        mock_bridge.chat.assert_called_once_with("더 자세히", "abc123")

    def test_post_chat_options_cors(self, client):
        resp = client.options("/api/chat")
        assert resp.status_code == 200
