import json
import os
import tempfile
import pytest
from datetime import datetime


class TestForecastLogger:

    def test_save_creates_log_entry(self, tmp_path):
        log_path = tmp_path / "forecast_log.json"
        log_path.write_text('{"logs": []}', encoding="utf-8")

        from api.forecast_logger import ForecastLogger
        logger = ForecastLogger(str(log_path))

        params = {"scope": {"week_from": 1, "week_to": 52}}
        by_week = {"W1": {"base": 800, "adjusted": 820}}
        by_category = {"Split AC": {"base": 30000, "adjusted": 32100}}

        sim_id = logger.save(params, by_week, by_category, note="test run")

        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert len(data["logs"]) == 1
        assert data["logs"][0]["id"] == sim_id
        assert data["logs"][0]["results_by_week"]["W1"]["adjusted"] == 820
        assert data["logs"][0]["note"] == "test run"

    def test_save_appends_to_existing(self, tmp_path):
        log_path = tmp_path / "forecast_log.json"
        log_path.write_text('{"logs": []}', encoding="utf-8")

        from api.forecast_logger import ForecastLogger
        logger = ForecastLogger(str(log_path))

        logger.save({}, {"W1": {"base": 1, "adjusted": 1}}, {}, note="first")
        logger.save({}, {"W1": {"base": 2, "adjusted": 2}}, {}, note="second")

        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert len(data["logs"]) == 2

    def test_compute_mape(self, tmp_path):
        log_path = tmp_path / "forecast_log.json"
        log_path.write_text('{"logs": []}', encoding="utf-8")

        from api.forecast_logger import ForecastLogger
        logger = ForecastLogger(str(log_path))

        by_week = {
            "W1": {"base": 100, "adjusted": 110},
            "W2": {"base": 200, "adjusted": 190},
        }
        by_category = {
            "Split AC": {"base": 200, "adjusted": 210},
            "Window AC": {"base": 100, "adjusted": 90},
        }
        logger.save({}, by_week, by_category, note="test")

        actuals_by_week = {"W1": 100, "W2": 200}
        actuals_by_cat = {"Split AC": 220, "Window AC": 95}

        result = logger.compute_accuracy(actuals_by_week, actuals_by_cat)

        assert result["weeks_compared"] == 2
        # W1: |100-110|/100 = 10%, W2: |200-190|/200 = 5% → avg 7.5%
        assert abs(result["overall_mape"] - 7.5) < 0.1
        assert "Split AC" in result["by_category"]
        assert "worst_weeks" in result

    def test_compute_accuracy_no_logs_returns_none(self, tmp_path):
        log_path = tmp_path / "forecast_log.json"
        log_path.write_text('{"logs": []}', encoding="utf-8")

        from api.forecast_logger import ForecastLogger
        logger = ForecastLogger(str(log_path))
        result = logger.compute_accuracy({}, {})
        assert result is None

    def test_get_latest_returns_most_recent(self, tmp_path):
        log_path = tmp_path / "forecast_log.json"
        log_path.write_text('{"logs": []}', encoding="utf-8")

        from api.forecast_logger import ForecastLogger
        logger = ForecastLogger(str(log_path))
        logger.save({}, {"W1": {"base": 1, "adjusted": 1}}, {}, note="first")
        logger.save({}, {"W1": {"base": 2, "adjusted": 2}}, {}, note="second")

        latest = logger.get_latest()
        assert latest["note"] == "second"
