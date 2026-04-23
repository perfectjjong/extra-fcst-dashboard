import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestChatBridge:

    def test_build_prompt_first_message(self):
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        prompt = bridge._build_prompt("사우디 폭염이면?", session_id=None)
        assert "demand forecasting analyst" in prompt
        assert "사우디 폭염이면?" in prompt

    def test_build_prompt_with_history(self):
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        sid = bridge._new_session()
        bridge._sessions[sid]["history"].append(
            {"role": "user", "content": "유가 상승"}
        )
        bridge._sessions[sid]["history"].append(
            {"role": "assistant", "content": "유가 상승으로 수요 감소"}
        )
        prompt = bridge._build_prompt("더 자세히", session_id=sid)
        assert "유가 상승" in prompt
        assert "더 자세히" in prompt

    def test_session_management(self):
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        sid = bridge._new_session()
        assert sid in bridge._sessions
        assert len(bridge._sessions[sid]["history"]) == 0

    def test_parse_claude_response_success(self):
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        raw = json.dumps({
            "type": "result",
            "result": "시뮬레이션 결과입니다.",
            "is_error": False,
        })
        parsed = bridge._parse_response(raw)
        assert parsed["reply"] == "시뮬레이션 결과입니다."
        assert parsed["method"] == "claude"

    def test_parse_claude_response_error(self):
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        parsed = bridge._parse_response("")
        assert parsed["method"] == "error"

    def test_history_limit(self):
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        sid = bridge._new_session()
        for i in range(25):
            bridge._sessions[sid]["history"].append(
                {"role": "user", "content": f"msg {i}"}
            )
        bridge._trim_history(sid)
        assert len(bridge._sessions[sid]["history"]) <= 20
