import os
import sqlite3
from collections import defaultdict
from typing import Dict

import numpy as np


def week_to_int(year: int, week: str) -> int:
    w = int(week.replace('W', ''))
    return year * 100 + w

def compute_naive_mape(db_path: str, lookback_weeks: int = 12) -> Dict[str, float]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT model, year, week, qty FROM weekly_sellout WHERE channel LIKE '%United%' ORDER BY year, week"
    ).fetchall()
    conn.close()

    series = defaultdict(list)
    for model, year, week, qty in rows:
        series[model].append((week_to_int(year, week), qty))

    mape_by_model = {}
    for model, pts in series.items():
        pts.sort()
        if len(pts) < lookback_weeks + 1:
            continue
        recent = pts[-(lookback_weeks + 1):]
        errors = []
        for i in range(1, len(recent)):
            _, actual = recent[i]
            _, predicted = recent[i - 1]
            if actual > 0:
                errors.append(abs(actual - predicted) / actual)
        if errors:
            mape_by_model[model] = float(np.mean(errors))

    return mape_by_model

if __name__ == "__main__":
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    results = compute_naive_mape(db)
    sorted_results = sorted(results.items(), key=lambda x: x[1])
    overall = np.mean(list(results.values())) if results else float('nan')
    print(f"\nNaive MAPE Report ({len(results)} models)")
    print(f"Overall mean MAPE: {overall:.1%}")
    print(f"\nBest models (lowest MAPE):")
    for m, mape in sorted_results[:10]:
        print(f"  {m}: {mape:.1%}")
    print(f"\nWorst models (highest MAPE):")
    for m, mape in sorted_results[-10:]:
        print(f"  {m}: {mape:.1%}")
