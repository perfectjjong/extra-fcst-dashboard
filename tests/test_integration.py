"""
Integration test — requires:
- claude CLI installed and authenticated
- MCP server dependencies available
Run with: python3 -m pytest tests/test_integration.py -v -s --timeout=120
"""
import json
import os
import shutil
import subprocess
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CLAUDE_AVAILABLE = shutil.which("claude") is not None
PROJECT_DIR = "/home/ubuntu/2026/03. Reporting/01. FCST"


@pytest.mark.skipif(not CLAUDE_AVAILABLE, reason="claude CLI not installed")
class TestIntegration:

    def test_mcp_server_tool_functions(self):
        """MCP tool underlying functions work correctly."""
        from api.mcp_server import _run_simulate, _get_environment

        env = _get_environment("W16")
        assert env["temp_c"] == 34
        assert env["humidity_pct"] == 16

        params = {
            "scope": {"week_from": 1, "week_to": 52, "categories": []},
            "price_positioning": {},
            "promo_periods": [],
            "external_vars": {
                "temp_scenario": "normal",
                "humidity_scenario": "normal",
                "oil_price_usd": 75,
                "electricity_burden": True,
                "oos_brands": {},
            },
            "trends_index": {},
            "note_adjustments": [],
        }
        result = _run_simulate(params)
        assert result["summary"]["base_total"] > 0
        assert len(result["by_week"]) > 0

    def test_claude_cli_with_mcp(self):
        """Claude CLI calls get_environment tool via MCP."""
        result = subprocess.run(
            [
                "claude", "-p",
                "Call the get_environment tool for week W16 and tell me the temperature. Reply with just the number.",
                "--output-format", "json",
                "--mcp-config", "api/mcp_config.json",
            ],
            capture_output=True, text=True, timeout=120,
            cwd=PROJECT_DIR,
        )
        data = json.loads(result.stdout)
        assert not data.get("is_error", False)
        assert "34" in data.get("result", "")

    def test_chat_bridge_full_flow(self):
        """ChatBridge sends message and gets Claude response."""
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        result = bridge.chat("W16의 리야드 기온은 몇 도인가요?")
        assert result["method"] in ("claude", "error")
        assert result["session_id"] is not None
        if result["method"] == "claude":
            assert len(result["reply"]) > 5

    def test_all_unit_tests_still_pass(self):
        """Verify no regressions in unit tests."""
        result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "-v",
             "--ignore=tests/test_integration.py", "-x"],
            capture_output=True, text=True, timeout=60,
            cwd=PROJECT_DIR,
        )
        assert result.returncode == 0, f"Unit tests failed:\n{result.stdout}\n{result.stderr}"
