import json
import os
import subprocess
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
MCP_CONFIG = BASE_DIR / "api" / "mcp_config.json"

SYSTEM_PROMPT = """You are a demand forecasting analyst for LG air conditioners in Saudi Arabia.
You have access to simulation tools and actual sell-out data (2023-2026).

When the user describes a scenario:
1. Translate it into simulation parameters
2. Call the simulate tool
3. Explain the results in Korean with key insights

When asked about accuracy:
1. Call get_actual_sellout for real data
2. Call get_forecast_accuracy for MAPE comparison
3. Highlight which weeks/categories had the largest errors and why

Always respond in Korean. Use specific numbers and week references.
Factor range: 0.85-1.20. Week range: W1-W52 (ISO weeks, 2026).
Categories: Mini Split, Window, Free Standing, Cassette, Packaged.
Channels: BH, BM, Tamkeen, Zagzoog, Dhamin, Star Appliance, Al Ghanem, Al Shathri, Al Manea, SWS, Black Box, Al Khunizan, eXtra."""

MAX_HISTORY = 20
CLI_TIMEOUT = 60


class ChatBridge:

    def __init__(self):
        self._sessions: dict[str, dict] = {}

    def _new_session(self) -> str:
        sid = uuid.uuid4().hex[:12]
        self._sessions[sid] = {"history": []}
        return sid

    def _trim_history(self, sid: str):
        hist = self._sessions[sid]["history"]
        if len(hist) > MAX_HISTORY:
            self._sessions[sid]["history"] = hist[-MAX_HISTORY:]

    def _build_prompt(self, message: str, session_id: str | None) -> str:
        parts = [SYSTEM_PROMPT, ""]

        if session_id and session_id in self._sessions:
            for turn in self._sessions[session_id]["history"]:
                role = "사용자" if turn["role"] == "user" else "어시스턴트"
                parts.append(f"[{role}]: {turn['content']}")
            parts.append("")

        parts.append(f"[사용자]: {message}")
        return "\n".join(parts)

    def _parse_response(self, raw: str) -> dict:
        if not raw.strip():
            return {"reply": "Claude 응답을 받지 못했습니다.", "method": "error"}
        try:
            data = json.loads(raw)
            if data.get("is_error"):
                return {
                    "reply": f"Claude 오류: {data.get('result', 'unknown')}",
                    "method": "error",
                }
            return {
                "reply": data.get("result", ""),
                "method": "claude",
                "cost_usd": data.get("total_cost_usd"),
                "duration_ms": data.get("duration_ms"),
            }
        except json.JSONDecodeError:
            return {"reply": raw.strip(), "method": "claude"}

    def chat(self, message: str, session_id: str | None = None) -> dict:
        if not session_id or session_id not in self._sessions:
            session_id = self._new_session()

        prompt = self._build_prompt(message, session_id)

        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "json",
                    "--mcp-config", str(MCP_CONFIG),
                ],
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT,
                cwd=str(BASE_DIR),
            )
            parsed = self._parse_response(result.stdout)
        except subprocess.TimeoutExpired:
            parsed = {"reply": "Claude 응답 시간 초과 (60초)", "method": "error"}
        except FileNotFoundError:
            parsed = {"reply": "Claude CLI를 찾을 수 없습니다. 설치 확인 필요.", "method": "error"}

        self._sessions[session_id]["history"].append(
            {"role": "user", "content": message}
        )
        self._sessions[session_id]["history"].append(
            {"role": "assistant", "content": parsed["reply"]}
        )
        self._trim_history(session_id)

        parsed["session_id"] = session_id
        return parsed
