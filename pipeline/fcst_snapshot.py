"""
FCST 스냅샷 저장 및 실적 비교 파이프라인.
- save_snapshot(): 예측 주차 전에 FCST를 fcst_snapshots에 저장
- compute_accuracy(): 실적 입력 후 MAPE 계산, fcst_accuracy_log에 기록
"""
import sqlite3
from typing import List, Dict


def save_snapshot(db_path: str, forecasts: List[Dict], week: str) -> int:
    """
    forecasts 리스트를 fcst_snapshots 테이블에 저장.
    이미 해당 week/model의 스냅샷이 있으면 무시(IGNORE).
    Returns: 저장된 row 수
    """
    conn = sqlite3.connect(db_path)
    count = 0
    for f in forecasts:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO fcst_snapshots (week, model, level, predicted, ci_low, ci_high) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (week, f['model'], f.get('level', 'L1_sku'),
                 f.get('predicted', 0), f.get('ci_low', 0), f.get('ci_high', 0))
            )
            count += conn.execute("SELECT changes()").fetchone()[0]
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


def compute_accuracy(db_path: str, week: str, mape_threshold: float = 0.30) -> bool:
    """
    해당 week의 fcst_snapshots vs weekly_sellout 실적 비교.
    MAPE를 fcst_accuracy_log에 기록.
    Returns True if any model exceeds mape_threshold (재학습 권고).
    """
    conn = sqlite3.connect(db_path)

    snaps = conn.execute(
        "SELECT model, level, predicted FROM fcst_snapshots WHERE week=?", (week,)
    ).fetchall()

    if not snaps:
        conn.close()
        return False

    actuals = dict(conn.execute(
        "SELECT model, SUM(qty) FROM weekly_sellout WHERE year=2026 AND week=? GROUP BY model",
        (week,)
    ).fetchall())

    retrain_needed = False
    for model, level, predicted in snaps:
        actual = actuals.get(model)
        if actual is None or actual <= 0:
            continue
        mape = abs(actual - predicted) / actual
        conn.execute(
            "INSERT OR REPLACE INTO fcst_accuracy_log (week, level, model, mape, retrained) "
            "VALUES (?, ?, ?, ?, ?)",
            (week, level, model, round(mape, 4), 0)
        )
        if mape > mape_threshold:
            retrain_needed = True

    conn.commit()
    conn.close()
    return retrain_needed


if __name__ == "__main__":
    import sys
    from pathlib import Path
    db = str(Path(__file__).parent.parent / 'data' / 'sellout.db')
    import sqlite3 as _sq
    conn = _sq.connect(db)
    latest = conn.execute(
        "SELECT week FROM weekly_sellout WHERE year=2026 "
        "ORDER BY CAST(SUBSTR(week,2) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if latest:
        w = latest[0]
        print(f"Checking accuracy for {w}...")
        retrain = compute_accuracy(db, week=w)
        print(f"  retrain_needed: {retrain}")
    else:
        print("No 2026 data found")
