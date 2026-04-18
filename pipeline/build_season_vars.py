import sqlite3
from datetime import date, timedelta
from typing import List

# 라마단 기간: (year, start_date, end_date)
RAMADAN = [
    (2024, date(2024, 3, 11), date(2024, 4, 9)),
    (2025, date(2025, 3, 1),  date(2025, 3, 29)),
    (2026, date(2026, 2, 18), date(2026, 3, 19)),
]

# 사우디 공휴일 (±1주 플래그)
HOLIDAY_DATES = [
    date(2024, 9, 23), date(2025, 9, 23), date(2026, 9, 23),  # 국경일
    date(2024, 2, 22), date(2025, 2, 22), date(2026, 2, 22),  # 건국기념일
    date(2024, 4, 10), date(2025, 3, 30), date(2026, 3, 20),  # 이드 알피트르
    date(2024, 6, 16), date(2025, 6, 6),  date(2026, 5, 27),  # 이드 알아드하
]

def _week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w}"  # 예: 2026-W8 (앞에 0 없음, weekly_sellout 형식과 통일)

def build_season_vars(db_path: str, years: List[int] = None) -> int:
    if years is None:
        years = [2024, 2025, 2026]

    ramadan_weeks = set()
    for yr, start, end in RAMADAN:
        if yr in years:
            d = start
            while d <= end:
                ramadan_weeks.add(_week_key(d))
                d += timedelta(days=1)

    holiday_weeks = set()
    for hd in HOLIDAY_DATES:
        if hd.year in years:
            for delta in [-7, 0, 7]:
                holiday_weeks.add(_week_key(hd + timedelta(days=delta)))

    all_weeks: dict = {}
    for yr in years:
        d = date(yr, 1, 1)
        end = date(yr, 12, 31)
        while d <= end:
            key = _week_key(d)
            if key not in all_weeks:
                all_weeks[key] = {
                    'ramadan': int(key in ramadan_weeks),
                    'summer':  int(d.month in (6, 7, 8)),
                    'holiday': int(key in holiday_weeks),
                }
            d += timedelta(days=1)

    conn = sqlite3.connect(db_path)
    for week_key, flags in all_weeks.items():
        conn.execute(
            "INSERT OR REPLACE INTO season_vars (week, ramadan_flag, summer_flag, holiday_flag) "
            "VALUES (?, ?, ?, ?)",
            (week_key, flags['ramadan'], flags['summer'], flags['holiday'])
        )
    conn.commit()
    conn.close()
    return len(all_weeks)

if __name__ == "__main__":
    import os
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    n = build_season_vars(db)
    print(f"Inserted {n} season_vars rows")
