import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMCPToolFunctions:

    def test_tool_simulate_returns_summary(self):
        from api.mcp_server import _run_simulate
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
        assert "summary" in result
        assert "by_week" in result
        assert result["summary"]["base_total"] > 0

    def test_tool_simulate_with_promo(self):
        from api.mcp_server import _run_simulate
        params = {
            "scope": {"week_from": 1, "week_to": 52, "categories": []},
            "price_positioning": {},
            "promo_periods": [{
                "segment": "ALL",
                "start_week": 25,
                "end_week": 28,
                "boost_direct_pct": 15,
                "hangover_weeks": 2,
            }],
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
        assert result["summary"]["promo_weeks"] > 0
        assert result["summary"]["adjusted_total"] > result["summary"]["base_total"]

    def test_tool_get_environment(self):
        from api.mcp_server import _get_environment
        result = _get_environment("W16")
        assert result["temp_c"] == 34
        assert result["humidity_pct"] == 16
        assert "oil_price_usd" in result

    def test_tool_get_environment_default_week(self):
        from api.mcp_server import _get_environment
        result = _get_environment(None)
        assert "week" in result
        assert "temp_c" in result

    def test_tool_get_actual_sellout_no_data(self):
        from api.mcp_server import _get_actual_sellout
        result = _get_actual_sellout("2026", 1, 17, None, None)
        assert result is None or "total_qty" in result
