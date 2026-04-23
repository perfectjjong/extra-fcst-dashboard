import json
import os
import tempfile
import pytest


def _make_html(all_data: dict) -> str:
    """Generate minimal HTML with embedded _ALL JSON."""
    return f'<script>\nconst _ALL = {json.dumps(all_data)};\n</script>'


SAMPLE_ALL = {
    "years": ["2025", "2026"],
    "current": "2026",
    "data": {
        "2026": {
            "meta": {"generated": "2026-04-23 09:00", "weeks": ["W1", "W2", "W3"]},
            "raw": [
                {"w": "W1", "ch": "BH", "c": "Split AC", "comp": "Inverter", "q": 100, "model": "AM182C"},
                {"w": "W1", "ch": "eXtra", "c": "Split AC", "comp": "Rotary", "q": 50, "model": "LB242C"},
                {"w": "W2", "ch": "BH", "c": "Window AC", "comp": "Rotary", "q": 30, "model": "WM182C"},
                {"w": "W3", "ch": "BH", "c": "Split AC", "comp": "Inverter", "q": 80, "model": "AM182C"},
            ],
            "sellthru": [],
            "stock": {},
            "prices": {},
        },
        "2025": {
            "meta": {"generated": "2025-12-31", "weeks": ["W1"]},
            "raw": [
                {"w": "W1", "ch": "BH", "c": "Split AC", "comp": "Inverter", "q": 90, "model": "AM182C"},
            ],
            "sellthru": [],
            "stock": {},
            "prices": {},
        }
    }
}


class TestB2CDataLoader:

    def test_parse_html_extracts_all_json(self, tmp_path):
        html_path = tmp_path / "index.html"
        html_path.write_text(_make_html(SAMPLE_ALL), encoding="utf-8")

        from api.b2c_data_loader import B2CDataLoader
        loader = B2CDataLoader(str(html_path))
        assert loader.years == ["2025", "2026"]
        assert loader.current_year == "2026"

    def test_get_sellout_filters_by_year_and_week(self, tmp_path):
        html_path = tmp_path / "index.html"
        html_path.write_text(_make_html(SAMPLE_ALL), encoding="utf-8")

        from api.b2c_data_loader import B2CDataLoader
        loader = B2CDataLoader(str(html_path))
        result = loader.get_sellout(year="2026", week_from=1, week_to=2)

        assert result["year"] == "2026"
        assert result["total_qty"] == 180
        assert "W1" in result["by_week"]
        assert result["by_week"]["W1"]["qty"] == 150
        assert result["by_week"]["W1"]["channels"]["BH"] == 100
        assert result["by_week"]["W2"]["qty"] == 30

    def test_get_sellout_filters_by_channel(self, tmp_path):
        html_path = tmp_path / "index.html"
        html_path.write_text(_make_html(SAMPLE_ALL), encoding="utf-8")

        from api.b2c_data_loader import B2CDataLoader
        loader = B2CDataLoader(str(html_path))
        result = loader.get_sellout(year="2026", week_from=1, week_to=3, channel="BH")
        assert result["total_qty"] == 210

    def test_get_sellout_filters_by_category(self, tmp_path):
        html_path = tmp_path / "index.html"
        html_path.write_text(_make_html(SAMPLE_ALL), encoding="utf-8")

        from api.b2c_data_loader import B2CDataLoader
        loader = B2CDataLoader(str(html_path))
        result = loader.get_sellout(year="2026", week_from=1, week_to=3, category="Window AC")
        assert result["total_qty"] == 30

    def test_auto_reload_on_mtime_change(self, tmp_path):
        html_path = tmp_path / "index.html"
        html_path.write_text(_make_html(SAMPLE_ALL), encoding="utf-8")

        from api.b2c_data_loader import B2CDataLoader
        loader = B2CDataLoader(str(html_path))
        assert loader.get_sellout("2026", 1, 3)["total_qty"] == 260

        updated = SAMPLE_ALL.copy()
        updated["data"] = {**SAMPLE_ALL["data"]}
        updated["data"]["2026"] = {**SAMPLE_ALL["data"]["2026"]}
        updated["data"]["2026"]["raw"] = SAMPLE_ALL["data"]["2026"]["raw"] + [
            {"w": "W3", "ch": "BH", "c": "Split AC", "comp": "Inverter", "q": 20, "model": "XX001"},
        ]
        html_path.write_text(_make_html(updated), encoding="utf-8")
        os.utime(str(html_path), (9999999999, 9999999999))

        result = loader.get_sellout("2026", 1, 3)
        assert result["total_qty"] == 280

    def test_missing_file_returns_none(self):
        from api.b2c_data_loader import B2CDataLoader
        loader = B2CDataLoader("/nonexistent/path.html")
        assert loader.get_sellout("2026", 1, 17) is None
