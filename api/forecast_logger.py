import json
import os
from datetime import datetime

# B2C 대시보드 카테고리 → 시뮬레이터 카테고리 매핑
CAT_NORMALIZE = {
    "Split AC": "Inverter",
    "Mini Split AC": "Inverter",
    "Mini Split": "Inverter",
    "Window AC": "Window AC",
    "Window": "Window AC",
    "Floor Standing AC": "Floor Standing AC",
    "Free Standing AC": "Floor Standing AC",
    "Free Standing": "Floor Standing AC",
    "Cassette AC": "Cassette AC",
    "Cassette": "Cassette AC",
    "Packaged AC": "Packaged AC",
    "Packaged": "Packaged AC",
    "Inverter": "Inverter",
    "Concealed Set": "Concealed Set",
}


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
            norm = CAT_NORMALIZE.get(cat, cat)
            sim_entry = sim_cats.get(norm) or sim_cats.get(cat)
            if not sim_entry or actual == 0:
                continue
            sim_val = sim_entry["adjusted"]
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
