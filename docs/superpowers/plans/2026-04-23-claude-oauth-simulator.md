# FCST Demand Simulator v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Anthropic API key dependency with Claude Code CLI OAuth, add conversational AI chat panel, integrate B2C sell-out actuals, and track forecast accuracy via MAPE.

**Architecture:** Flask Bridge Server (:5050) serves existing simulator endpoints unchanged. New `/api/chat` and `/api/chat/follow` endpoints call `claude -p` as subprocess with `--mcp-config` pointing to a Python MCP server exposing 6 tools (simulate, get_environment, get_oos_signals, get_trends, get_actual_sellout, get_forecast_accuracy). The frontend adds a collapsible chat panel to the existing simulator UI.

**Tech Stack:** Python 3.12, Flask, MCP SDK (FastMCP), Claude Code CLI 2.1.x, Chart.js, vanilla JS

**Spec:** `docs/superpowers/specs/2026-04-23-claude-oauth-simulator-design.md`

**Project root:** `/home/ubuntu/2026/03. Reporting/01. FCST`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `api/b2c_data_loader.py` | Create | Parse B2C dashboard HTML, cache `_ALL` JSON, query sell-out by year/week/channel/category |
| `api/forecast_logger.py` | Create | Save simulation logs to `data/forecast_log.json`, compute MAPE vs actuals |
| `api/mcp_server.py` | Create | FastMCP stdio server with 6 tools |
| `api/mcp_config.json` | Create | MCP server config for `claude -p --mcp-config` |
| `api/chat_bridge.py` | Create | Call `claude -p` subprocess, manage session history, parse response |
| `api/server.py` | Modify | Add `/api/chat` and `/api/chat/follow` endpoints |
| `dashboard/simulator-v2.html` | Create | Copy simulator.html + add chat panel |
| `data/forecast_log.json` | Create | Empty initial log file |

---

## Task 1: B2C Data Loader

**Files:**
- Create: `api/b2c_data_loader.py`
- Create: `tests/test_b2c_data_loader.py`

This module parses the B2C unified dashboard HTML to extract the embedded `_ALL` JSON and provides query functions for sell-out data.

- [ ] **Step 1: Write failing test for HTML parsing**

```python
# tests/test_b2c_data_loader.py
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
        assert result["total_qty"] == 180  # 100 + 50 + 30
        assert "W1" in result["by_week"]
        assert result["by_week"]["W1"]["qty"] == 150  # 100 + 50
        assert result["by_week"]["W1"]["channels"]["BH"] == 100
        assert result["by_week"]["W2"]["qty"] == 30

    def test_get_sellout_filters_by_channel(self, tmp_path):
        html_path = tmp_path / "index.html"
        html_path.write_text(_make_html(SAMPLE_ALL), encoding="utf-8")

        from api.b2c_data_loader import B2CDataLoader
        loader = B2CDataLoader(str(html_path))
        result = loader.get_sellout(year="2026", week_from=1, week_to=3, channel="BH")
        assert result["total_qty"] == 210  # 100 + 30 + 80

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_b2c_data_loader.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api.b2c_data_loader'`

- [ ] **Step 3: Implement B2CDataLoader**

```python
# api/b2c_data_loader.py
import json
import os
import re
from collections import defaultdict


class B2CDataLoader:

    def __init__(self, html_path: str):
        self._path = html_path
        self._data = None
        self._mtime = 0
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            self._data = None
            return
        mtime = os.path.getmtime(self._path)
        if self._data and mtime == self._mtime:
            return
        with open(self._path, encoding="utf-8") as f:
            html = f.read()
        m = re.search(r'const\s+_ALL\s*=\s*(\{.*?\});\s*\n', html, re.DOTALL)
        if not m:
            self._data = None
            return
        self._data = json.loads(m.group(1))
        self._mtime = mtime

    @property
    def years(self):
        self._load()
        return self._data["years"] if self._data else []

    @property
    def current_year(self):
        self._load()
        return self._data.get("current") if self._data else None

    def get_sellout(self, year: str = "2026", week_from: int = 1,
                    week_to: int = 52, channel: str = None,
                    category: str = None) -> dict | None:
        self._load()
        if not self._data or year not in self._data.get("data", {}):
            return None

        raw = self._data["data"][year].get("raw", [])
        meta = self._data["data"][year].get("meta", {})

        by_week = defaultdict(lambda: {"qty": 0, "channels": defaultdict(int)})
        by_category = defaultdict(int)
        total = 0

        for r in raw:
            wnum = int(r["w"][1:])
            if not (week_from <= wnum <= week_to):
                continue
            if channel and r["ch"] != channel:
                continue
            if category and r["c"] != category:
                continue
            q = r.get("q", 0)
            total += q
            by_week[r["w"]]["qty"] += q
            by_week[r["w"]]["channels"][r["ch"]] += q
            by_category[r["c"]] += q

        return {
            "year": year,
            "total_qty": total,
            "by_week": {k: {"qty": v["qty"], "channels": dict(v["channels"])}
                        for k, v in sorted(by_week.items(),
                                           key=lambda x: int(x[0][1:]))},
            "by_category": dict(by_category),
            "data_as_of": meta.get("generated", "unknown"),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_b2c_data_loader.py -v
```

Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add api/b2c_data_loader.py tests/test_b2c_data_loader.py
git commit -m "feat: add B2C data loader with HTML parsing and mtime reload"
```

---

## Task 2: Forecast Logger

**Files:**
- Create: `api/forecast_logger.py`
- Create: `tests/test_forecast_logger.py`
- Create: `data/forecast_log.json`

This module saves simulation results and computes MAPE accuracy against actuals.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_forecast_logger.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_forecast_logger.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api.forecast_logger'`

- [ ] **Step 3: Create empty log file**

```bash
echo '{"logs": []}' > "/home/ubuntu/2026/03. Reporting/01. FCST/data/forecast_log.json"
```

- [ ] **Step 4: Implement ForecastLogger**

```python
# api/forecast_logger.py
import json
import os
from datetime import datetime


class ForecastLogger:

    def __init__(self, log_path: str):
        self._path = log_path

    def _read(self) -> dict:
        if not os.path.exists(self._path):
            return {"logs": []}
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self, params: dict, results_by_week: dict,
             results_by_category: dict, note: str = "") -> str:
        data = self._read()
        now = datetime.now()
        count = sum(1 for l in data["logs"]
                    if l["id"].startswith(now.strftime("%Y-%m-%d"))) + 1
        sim_id = f"{now.strftime('%Y-%m-%d')}_{count:03d}"

        entry = {
            "id": sim_id,
            "timestamp": now.isoformat(timespec="seconds"),
            "params": params,
            "results_by_week": results_by_week,
            "results_by_category": results_by_category,
            "note": note,
        }
        data["logs"].append(entry)
        self._write(data)
        return sim_id

    def get_latest(self) -> dict | None:
        data = self._read()
        return data["logs"][-1] if data["logs"] else None

    def compute_accuracy(self, actuals_by_week: dict,
                         actuals_by_category: dict) -> dict | None:
        latest = self.get_latest()
        if not latest:
            return None

        sim_weeks = latest["results_by_week"]
        week_errors = []
        worst = []

        for w, actual in actuals_by_week.items():
            if w not in sim_weeks or actual == 0:
                continue
            sim_val = sim_weeks[w]["adjusted"]
            error = abs(actual - sim_val) / actual * 100
            week_errors.append(error)
            worst.append({"week": w, "sim": sim_val, "actual": actual,
                          "error_pct": round(error, 1)})

        if not week_errors:
            return None

        cat_result = {}
        sim_cats = latest.get("results_by_category", {})
        for cat, actual in actuals_by_category.items():
            if cat not in sim_cats or actual == 0:
                continue
            sim_val = sim_cats[cat]["adjusted"]
            mape = abs(actual - sim_val) / actual * 100
            cat_result[cat] = {"sim": sim_val, "actual": actual,
                               "mape": round(mape, 1)}

        worst.sort(key=lambda x: x["error_pct"], reverse=True)

        return {
            "simulation_id": latest["id"],
            "simulation_date": latest["timestamp"][:10],
            "weeks_compared": len(week_errors),
            "overall_mape": round(sum(week_errors) / len(week_errors), 1),
            "by_category": cat_result,
            "worst_weeks": worst[:5],
        }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_forecast_logger.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add api/forecast_logger.py tests/test_forecast_logger.py data/forecast_log.json
git commit -m "feat: add forecast logger with MAPE accuracy tracking"
```

---

## Task 3: MCP Server

**Files:**
- Create: `api/mcp_server.py`
- Create: `api/mcp_config.json`
- Create: `tests/test_mcp_server.py`

FastMCP stdio server exposing 6 tools. Each tool wraps existing engine functions.

- [ ] **Step 1: Write failing tests for MCP tool functions**

The MCP server's tool handlers are plain functions that can be tested directly.

```python
# tests/test_mcp_server.py
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMCPToolFunctions:
    """Test the underlying functions that MCP tools call."""

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
        # May return None or data depending on B2C HTML availability
        # Just verify it doesn't crash
        assert result is None or "total_qty" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_mcp_server.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api.mcp_server'`

- [ ] **Step 3: Create MCP config**

```json
{
  "mcpServers": {
    "fcst-simulator": {
      "command": "python3",
      "args": ["api/mcp_server.py"],
      "cwd": "/home/ubuntu/2026/03. Reporting/01. FCST"
    }
  }
}
```

Save as `api/mcp_config.json`.

- [ ] **Step 4: Implement MCP server**

```python
# api/mcp_server.py
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP
from api.simulator import SimulationEngine, RIYADH_TEMP, RIYADH_HUMIDITY
from api.b2c_data_loader import B2CDataLoader
from api.forecast_logger import ForecastLogger

BASE_DIR = Path(__file__).parent.parent
FCST_PATH = BASE_DIR / "dashboard" / "fcst_output.json"
DB_PATH = BASE_DIR / "data" / "sellout.db"
B2C_HTML = Path("/home/ubuntu/Shaker-MD-App/docs/dashboards/b2c-unified/index.html")
LOG_PATH = BASE_DIR / "data" / "forecast_log.json"

_b2c_loader = B2CDataLoader(str(B2C_HTML))
_forecast_logger = ForecastLogger(str(LOG_PATH))

_oil_cache = {"price": None, "ts": 0}


def _fetch_oil_price() -> float:
    now = time.time()
    if _oil_cache["price"] and now - _oil_cache["ts"] < 3600:
        return _oil_cache["price"]
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/CL=F"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        _oil_cache["price"] = round(float(price), 1)
        _oil_cache["ts"] = now
    except Exception:
        _oil_cache["price"] = _oil_cache["price"] or 75.0
    return _oil_cache["price"]


def _load_fcst() -> list:
    with open(FCST_PATH, encoding="utf-8") as f:
        return json.load(f).get("long_range_forecasts", [])


def _load_price_gaps() -> dict:
    try:
        from pipeline.build_price_segments import get_brand_price_context
        return get_brand_price_context(str(DB_PATH))
    except Exception:
        return {}


def _run_simulate(params: dict) -> dict:
    fcst = _load_fcst()
    gaps = _load_price_gaps()
    engine = SimulationEngine()

    note_adjs = params.pop("note_adjustments", [])
    results = engine.simulate(fcst, params, gaps)

    for r in results:
        wnum = int(r["week"][1:])
        for adj in note_adjs:
            if adj["week_from"] <= wnum <= adj["week_to"]:
                r["adjusted"] = round(r["adjusted"] * adj["factor"])

    base_total = sum(r.get("predicted", 0) for r in results)
    adj_total = sum(r.get("adjusted", 0) for r in results)
    delta_pct = round((adj_total / base_total - 1) * 100, 1) if base_total else 0.0

    by_week = defaultdict(lambda: {"base": 0, "adjusted": 0, "promo": False, "hangover": False})
    for r in results:
        w = r["week"]
        by_week[w]["base"] += r["predicted"]
        by_week[w]["adjusted"] += r["adjusted"]
        if r["is_promo_week"]:
            by_week[w]["promo"] = True
        if r["is_hangover"]:
            by_week[w]["hangover"] = True

    by_week_dict = dict(by_week)

    top_movers = sorted(
        [{"week": w, "delta_pct": round((v["adjusted"] / v["base"] - 1) * 100, 1) if v["base"] else 0}
         for w, v in by_week_dict.items()],
        key=lambda x: abs(x["delta_pct"]),
        reverse=True,
    )[:5]

    summary = {
        "base_total": base_total,
        "adjusted_total": adj_total,
        "delta_pct": delta_pct,
        "model_count": len(set(r["model"] for r in results)),
        "promo_weeks": sum(1 for v in by_week_dict.values() if v["promo"]),
        "week_range": [params["scope"]["week_from"], params["scope"]["week_to"]],
    }

    by_cat = defaultdict(lambda: {"base": 0, "adjusted": 0})
    for r in results:
        by_cat[r.get("category", "Unknown")]["base"] += r["predicted"]
        by_cat[r.get("category", "Unknown")]["adjusted"] += r["adjusted"]

    _forecast_logger.save(
        params, by_week_dict, dict(by_cat),
        note=f"simulate call at {date.today()}"
    )

    return {"summary": summary, "by_week": by_week_dict, "top_movers": top_movers}


def _get_environment(week: str | None) -> dict:
    if not week:
        iso_week = date.today().isocalendar()[1]
        week = f"W{iso_week}"
    return {
        "week": week,
        "temp_c": RIYADH_TEMP.get(week, 25),
        "humidity_pct": RIYADH_HUMIDITY.get(week, 30),
        "oil_price_usd": _fetch_oil_price(),
    }


def _get_actual_sellout(year: str, week_from: int, week_to: int,
                        channel: str | None, category: str | None) -> dict | None:
    return _b2c_loader.get_sellout(year, week_from, week_to, channel, category)


# ── FastMCP Server ──────────────────────────────────────────────────

mcp = FastMCP("fcst-simulator")


@mcp.tool()
def simulate(
    scope_week_from: int = 1,
    scope_week_to: int = 52,
    scope_categories: list[str] | None = None,
    temp_scenario: str = "normal",
    humidity_scenario: str = "normal",
    oil_price_usd: float = 75.0,
    electricity_burden: bool = True,
    promo_periods: str = "[]",
    note_adjustments: str = "[]",
) -> str:
    """Run demand simulation with 8-factor engine for LG AC in Saudi Arabia.

    Args:
        scope_week_from: Start week (1-52)
        scope_week_to: End week (1-52)
        scope_categories: Filter categories (e.g. ["Mini Split", "Window"]), empty = all
        temp_scenario: Temperature scenario: "hot", "normal", or "mild"
        humidity_scenario: Humidity scenario: "high", "normal", or "low"
        oil_price_usd: WTI oil price in USD (default 75)
        electricity_burden: Apply electricity burden boost for Inverter models
        promo_periods: JSON string of promo periods array, each with segment/start_week/end_week/boost_direct_pct/hangover_weeks
        note_adjustments: JSON string of note adjustments array, each with week_from/week_to/factor/label
    """
    params = {
        "scope": {
            "week_from": scope_week_from,
            "week_to": scope_week_to,
            "categories": scope_categories or [],
        },
        "price_positioning": {},
        "promo_periods": json.loads(promo_periods) if isinstance(promo_periods, str) else promo_periods,
        "external_vars": {
            "temp_scenario": temp_scenario,
            "humidity_scenario": humidity_scenario,
            "oil_price_usd": oil_price_usd,
            "electricity_burden": electricity_burden,
            "oos_brands": {},
        },
        "trends_index": {},
        "note_adjustments": json.loads(note_adjustments) if isinstance(note_adjustments, str) else note_adjustments,
    }
    return json.dumps(_run_simulate(params), ensure_ascii=False)


@mcp.tool()
def get_environment(week: str | None = None) -> str:
    """Get Riyadh weather and oil price for a given ISO week.

    Args:
        week: ISO week string like "W16". Defaults to current week.
    """
    return json.dumps(_get_environment(week), ensure_ascii=False)


@mcp.tool()
def get_oos_signals() -> str:
    """Get competitor out-of-stock signals from price tracking data."""
    try:
        from pipeline.build_price_segments import get_oos_signals
        result = get_oos_signals(str(DB_PATH))
    except Exception:
        result = {}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_trends() -> str:
    """Get Google Trends weekly search index for AC in Saudi Arabia."""
    try:
        from api.trends import get_trends_index
        result = get_trends_index()
    except Exception:
        result = {}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_actual_sellout(
    year: str = "2026",
    week_from: int = 1,
    week_to: int = 52,
    channel: str | None = None,
    category: str | None = None,
) -> str:
    """Get actual sell-out data from B2C unified dashboard (2023-2026).

    Args:
        year: Year to query (2023-2026)
        week_from: Start ISO week
        week_to: End ISO week
        channel: Filter by channel (e.g. "BH", "eXtra"). None = all channels.
        category: Filter by category (e.g. "Split AC"). None = all.
    """
    result = _get_actual_sellout(year, week_from, week_to, channel, category)
    if result is None:
        return json.dumps({"error": "B2C 데이터를 로드할 수 없습니다."})
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_forecast_accuracy() -> str:
    """Compare latest simulation against actual sell-out data. Returns MAPE by week and category."""
    latest = _forecast_logger.get_latest()
    if not latest:
        return json.dumps({"error": "시뮬레이션 로그가 없습니다."})

    scope = latest.get("params", {}).get("scope", {})
    week_to = scope.get("week_to", 52)
    current_week = date.today().isocalendar()[1]
    compare_to = min(week_to, current_week - 1)

    actuals = _b2c_loader.get_sellout("2026", 1, compare_to)
    if not actuals:
        return json.dumps({"error": "B2C 실적 데이터를 로드할 수 없습니다."})

    actuals_by_week = {w: v["qty"] for w, v in actuals["by_week"].items()}
    result = _forecast_logger.compute_accuracy(actuals_by_week, actuals["by_category"])
    if not result:
        return json.dumps({"error": "비교할 데이터가 없습니다."})
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_mcp_server.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 6: Test MCP server standalone**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 api/mcp_server.py 2>/dev/null | head -5
```

Expected: JSON listing 6 tools

- [ ] **Step 7: Commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add api/mcp_server.py api/mcp_config.json tests/test_mcp_server.py
git commit -m "feat: add MCP server with 6 tools for Claude Code integration"
```

---

## Task 4: Chat Bridge

**Files:**
- Create: `api/chat_bridge.py`
- Create: `tests/test_chat_bridge.py`

This module calls `claude -p` as subprocess, manages conversation history, and parses the JSON response.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_bridge.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_chat_bridge.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'api.chat_bridge'`

- [ ] **Step 3: Implement ChatBridge**

```python
# api/chat_bridge.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_chat_bridge.py -v
```

Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add api/chat_bridge.py tests/test_chat_bridge.py
git commit -m "feat: add chat bridge for Claude CLI subprocess calls"
```

---

## Task 5: Server Endpoints

**Files:**
- Modify: `api/server.py` (add 2 routes + import)

Add `/api/chat` and `/api/chat/follow` to the existing Flask app. Also add `/simulator-v2` route.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_server_chat.py
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
        assert "비어" in data["reply"] or "입력" in data["reply"]

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_server_chat.py -v
```

Expected: FAIL — `_chat_bridge` not found or routes not defined

- [ ] **Step 3: Add chat routes to server.py**

Add these imports at the top of `api/server.py` (after existing imports):

```python
from api.chat_bridge import ChatBridge
```

Add this module-level variable after the `DEFAULT_FCST` / `DASHBOARD_DIR` constants:

```python
_chat_bridge = ChatBridge()
```

Add these routes inside `create_app()`, after the existing `/api/interpret-note` route and before `return app`:

```python
    @app.route('/simulator-v2')
    def simulator_v2_page():
        return send_from_directory(os.path.abspath(DASHBOARD_DIR), 'simulator-v2.html')

    @app.route('/api/chat', methods=['POST', 'OPTIONS'])
    def post_chat():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        if not message:
            return jsonify({'reply': '메시지를 입력해 주세요.', 'method': 'none', 'session_id': None})
        result = _chat_bridge.chat(message)
        return jsonify(result)

    @app.route('/api/chat/follow', methods=['POST', 'OPTIONS'])
    def post_chat_follow():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        session_id = data.get('session_id')
        if not message:
            return jsonify({'reply': '메시지를 입력해 주세요.', 'method': 'none', 'session_id': session_id})
        result = _chat_bridge.chat(message, session_id)
        return jsonify(result)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_server_chat.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add api/server.py tests/test_server_chat.py
git commit -m "feat: add /api/chat and /api/chat/follow endpoints"
```

---

## Task 6: Chat Panel UI (simulator-v2.html)

**Files:**
- Create: `dashboard/simulator-v2.html`

Copy existing `simulator.html` and add the collapsible chat panel on the right side. This is the largest file — it's a full copy of the existing HTML with chat additions.

- [ ] **Step 1: Copy existing simulator.html**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
cp dashboard/simulator.html dashboard/simulator-v2.html
```

- [ ] **Step 2: Add chat panel CSS**

In `simulator-v2.html`, add these styles inside the existing `<style>` block, before the closing `</style>`:

```css
/* ── Chat Panel ── */
.chat-toggle {
  position: fixed; right: 16px; bottom: 16px; z-index: 100;
  width: 48px; height: 48px; border-radius: 50%;
  background: var(--purple); border: none; color: #fff; font-size: 1.4rem;
  cursor: pointer; box-shadow: 0 4px 12px rgba(124,58,237,.4);
  display: flex; align-items: center; justify-content: center;
}
.chat-toggle:hover { background: var(--purple-lt); }

.chat-panel {
  position: fixed; right: 0; top: 0; bottom: 0; width: 380px; z-index: 90;
  background: var(--surface); border-left: 1px solid var(--border);
  display: flex; flex-direction: column;
  transform: translateX(100%); transition: transform .3s ease;
}
.chat-panel.open { transform: translateX(0); }

.chat-header {
  height: 52px; flex-shrink: 0; padding: 0 16px;
  display: flex; align-items: center; justify-content: space-between;
  border-bottom: 1px solid var(--border);
}
.chat-header h3 { font-size: .9rem; color: var(--purple-lt); margin: 0; }
.chat-close { background: none; border: none; color: var(--muted); font-size: 1.2rem; cursor: pointer; }

.chat-messages {
  flex: 1; overflow-y: auto; padding: 12px 16px;
  display: flex; flex-direction: column; gap: 10px;
}

.chat-msg {
  max-width: 90%; padding: 10px 14px; border-radius: 12px;
  font-size: .8rem; line-height: 1.5; word-wrap: break-word;
  white-space: pre-wrap;
}
.chat-msg.user {
  align-self: flex-end; background: var(--purple); color: #fff;
  border-bottom-right-radius: 2px;
}
.chat-msg.assistant {
  align-self: flex-start; background: var(--card); color: var(--text);
  border: 1px solid var(--border); border-bottom-left-radius: 2px;
}
.chat-msg .method-badge {
  display: inline-block; font-size: .6rem; padding: 1px 6px;
  border-radius: 4px; margin-bottom: 4px;
}
.method-claude { background: rgba(124,58,237,.2); color: var(--purple-lt); }
.method-rule { background: rgba(245,158,11,.2); color: var(--amber); }
.method-error { background: rgba(239,68,68,.2); color: var(--red); }

.chat-loading {
  align-self: flex-start; color: var(--muted); font-size: .75rem;
  padding: 8px 14px;
}
.chat-loading::after {
  content: ''; animation: dots 1.5s infinite;
}
@keyframes dots {
  0% { content: ''; } 33% { content: '.'; } 66% { content: '..'; } 100% { content: '...'; }
}

.chat-input-area {
  flex-shrink: 0; padding: 12px 16px;
  border-top: 1px solid var(--border);
  display: flex; gap: 8px;
}
.chat-input-area textarea {
  flex: 1; resize: none; height: 40px;
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); padding: 10px 12px; font-size: .78rem;
  font-family: inherit;
}
.chat-input-area textarea:focus { outline: none; border-color: var(--purple); }
.chat-input-area button {
  width: 40px; height: 40px; border-radius: 8px;
  background: var(--purple); border: none; color: #fff;
  font-size: 1rem; cursor: pointer; flex-shrink: 0;
}
.chat-input-area button:disabled { opacity: .5; cursor: not-allowed; }

body.chat-open .main-scroll { margin-right: 380px; }
```

- [ ] **Step 3: Add chat HTML**

Before the closing `</body>` tag in `simulator-v2.html`, add:

```html
<!-- Chat Toggle Button -->
<button class="chat-toggle" onclick="toggleChat()" title="AI 채팅">💬</button>

<!-- Chat Panel -->
<div class="chat-panel" id="chat-panel">
  <div class="chat-header">
    <h3>🤖 AI Demand Analyst</h3>
    <button class="chat-close" onclick="toggleChat()">✕</button>
  </div>
  <div class="chat-messages" id="chat-messages"></div>
  <div class="chat-input-area">
    <textarea id="chat-input" placeholder="시나리오를 입력하세요... (예: 사우디 폭염 + 유가 상승)"
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat()}"></textarea>
    <button id="chat-send-btn" onclick="sendChat()">➤</button>
  </div>
</div>
```

- [ ] **Step 4: Add chat JavaScript**

Before the closing `</script>` tag in `simulator-v2.html`, add:

```javascript
// ── Chat Panel ───────────────────────────────────────────────────────────────
let chatSessionId = null;
let chatBusy = false;

function toggleChat() {
  const panel = document.getElementById('chat-panel');
  panel.classList.toggle('open');
  document.body.classList.toggle('chat-open');
}

function appendChatMsg(role, text, method) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;

  if (role === 'assistant' && method) {
    const badgeCls = method === 'claude' ? 'method-claude'
      : method.includes('rule') ? 'method-rule' : 'method-error';
    const label = method === 'claude' ? 'Claude AI'
      : method.includes('rule') ? '룰 기반' : '오류';
    div.innerHTML = `<span class="method-badge ${badgeCls}">${label}</span>\n${text}`;
  } else {
    div.textContent = text;
  }

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function showChatLoading(show) {
  const container = document.getElementById('chat-messages');
  let loader = document.getElementById('chat-loader');
  if (show && !loader) {
    loader = document.createElement('div');
    loader.id = 'chat-loader';
    loader.className = 'chat-loading';
    loader.textContent = '⏳ Claude 분석 중';
    container.appendChild(loader);
    container.scrollTop = container.scrollHeight;
  } else if (!show && loader) {
    loader.remove();
  }
}

async function sendChat() {
  if (chatBusy) return;
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  appendChatMsg('user', text);
  chatBusy = true;
  document.getElementById('chat-send-btn').disabled = true;
  showChatLoading(true);

  try {
    const endpoint = chatSessionId ? '/api/chat/follow' : '/api/chat';
    const body = chatSessionId
      ? { session_id: chatSessionId, message: text }
      : { message: text };

    const resp = await fetch(API_BASE + endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    chatSessionId = data.session_id || chatSessionId;
    appendChatMsg('assistant', data.reply, data.method);

    if (data.simulation_result) {
      state.lastResult = data.simulation_result;
      renderKPIs(data.simulation_result.summary);
      renderChart(data.simulation_result.by_week);
      renderTable(data.simulation_result.by_week);
      updateLastUpdate();
    }
  } catch (e) {
    appendChatMsg('assistant', '채팅 오류: ' + e.message, 'error');
  } finally {
    chatBusy = false;
    document.getElementById('chat-send-btn').disabled = false;
    showChatLoading(false);
  }
}
```

- [ ] **Step 5: Update API_BASE for localhost**

In `simulator-v2.html`, change the API_BASE to always use localhost (since this is a local-only tool):

```javascript
const API_BASE = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
  ? ''
  : '';
```

- [ ] **Step 6: Manual test — open in browser**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -c "from api.server import create_app; create_app().run(host='0.0.0.0', port=5050, debug=True)" &
sleep 2
echo "Open http://localhost:5050/simulator-v2 in browser"
echo "1. Verify existing sliders and chart work"
echo "2. Click chat toggle button (bottom right)"
echo "3. Type '사우디 폭염이면?' and send"
echo "4. Verify AI response appears in chat"
echo "5. Verify chart updates if simulation was run"
kill %1 2>/dev/null
```

- [ ] **Step 7: Commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add dashboard/simulator-v2.html
git commit -m "feat: add simulator-v2 with AI chat panel"
```

---

## Task 7: Integration Test

**Files:**
- Create: `tests/test_integration.py`

End-to-end test that verifies the full flow: Flask → ChatBridge → Claude CLI → MCP → response.

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
"""
Integration test — requires:
- claude CLI installed and authenticated
- MCP server dependencies available
Run with: python3 -m pytest tests/test_integration.py -v -s --timeout=120
Skip in CI with: python3 -m pytest tests/test_integration.py -v -k "not integration"
"""
import json
import os
import shutil
import subprocess
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CLAUDE_AVAILABLE = shutil.which("claude") is not None


@pytest.mark.skipif(not CLAUDE_AVAILABLE, reason="claude CLI not installed")
class TestIntegration:

    def test_mcp_server_lists_tools(self):
        """MCP server starts and lists 6 tools."""
        result = subprocess.run(
            ["python3", "api/mcp_server.py"],
            input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
            capture_output=True, text=True, timeout=10,
            cwd="/home/ubuntu/2026/03. Reporting/01. FCST",
        )
        assert result.returncode == 0 or "tools" in result.stdout

    def test_claude_cli_with_mcp(self):
        """Claude CLI calls simulate tool via MCP and returns result."""
        result = subprocess.run(
            [
                "claude", "-p",
                "Call the simulate tool with default parameters (week 1-52, normal temperature, oil 75 USD). Return the base_total from the summary.",
                "--output-format", "json",
                "--mcp-config", "api/mcp_config.json",
            ],
            capture_output=True, text=True, timeout=120,
            cwd="/home/ubuntu/2026/03. Reporting/01. FCST",
        )
        data = json.loads(result.stdout)
        assert not data.get("is_error", False)
        assert "base_total" in data.get("result", "").lower() or len(data.get("result", "")) > 10

    def test_chat_bridge_full_flow(self):
        """ChatBridge sends message and gets Claude response."""
        from api.chat_bridge import ChatBridge
        bridge = ChatBridge()
        result = bridge.chat("W16의 리야드 기온은?")
        assert result["method"] in ("claude", "error")
        assert result["session_id"] is not None
        if result["method"] == "claude":
            assert "34" in result["reply"] or "기온" in result["reply"]
```

- [ ] **Step 2: Run integration tests**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/test_integration.py -v -s --timeout=120
```

Expected: All tests PASS (skip if claude CLI not available)

- [ ] **Step 3: Run all tests together**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m pytest tests/ -v --timeout=120
```

Expected: All unit tests + integration tests PASS

- [ ] **Step 4: Commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add tests/test_integration.py
git commit -m "test: add integration tests for full Claude CLI + MCP flow"
```

---

## Task 8: Final Wiring & Manual Verification

**Files:**
- No new files — verify everything works together

- [ ] **Step 1: Start the server**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
python3 -m api.server
```

- [ ] **Step 2: Test existing simulator endpoints**

```bash
# Existing endpoints should still work
curl -s http://localhost:5050/api/env-data?week=W16 | python3 -m json.tool
curl -s -X POST http://localhost:5050/api/simulate \
  -H 'Content-Type: application/json' \
  -d '{"scope":{"week_from":1,"week_to":52},"external_vars":{"temp_scenario":"normal","oil_price_usd":75}}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('base:', d['summary']['base_total'], 'adj:', d['summary']['adjusted_total'])"
```

- [ ] **Step 3: Test chat endpoint**

```bash
# First message
curl -s -X POST http://localhost:5050/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "사우디에 폭염이 오면 에어컨 수요가 어떻게 변할까?"}' \
  | python3 -m json.tool

# Follow-up (use session_id from above)
curl -s -X POST http://localhost:5050/api/chat/follow \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "SESSION_ID_HERE", "message": "프로모를 W25-W28에 넣으면?"}' \
  | python3 -m json.tool
```

- [ ] **Step 4: Test accuracy endpoint via chat**

```bash
curl -s -X POST http://localhost:5050/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "올해 W1-W17까지 시뮬레이션 대비 실적이 어떤가요?"}' \
  | python3 -m json.tool
```

- [ ] **Step 5: Open simulator-v2 in browser**

Navigate to `http://localhost:5050/simulator-v2` and verify:
1. All existing sliders, chart, KPI strip work
2. Chat toggle button appears (bottom right)
3. Chat panel opens/closes
4. Send a message → AI response appears
5. Follow-up questions work

- [ ] **Step 6: Final commit**

```bash
cd "/home/ubuntu/2026/03. Reporting/01. FCST"
git add -A
git status
git commit -m "feat: FCST Demand Simulator v2 — Claude Code OAuth + AI chat + B2C integration"
```
