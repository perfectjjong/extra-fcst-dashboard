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
    try:
        with open(FCST_PATH, encoding="utf-8") as f:
            return json.load(f).get("long_range_forecasts", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


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

    note_adjs = params.get("note_adjustments", [])
    sim_params = {k: v for k, v in params.items() if k != "note_adjustments"}
    results = engine.simulate(fcst, sim_params, gaps)

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
        from pipeline.build_price_segments import get_oos_signals as _get_oos
        result = _get_oos(str(DB_PATH))
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
