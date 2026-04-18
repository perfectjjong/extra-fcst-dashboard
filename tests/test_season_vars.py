import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db
from pipeline.build_season_vars import build_season_vars

def test_ramadan_2024_flagged(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    build_season_vars(db_path, years=[2024])
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT ramadan_flag FROM season_vars WHERE week='2024-W12'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1

def test_summer_flag(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    build_season_vars(db_path, years=[2026])
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT summer_flag FROM season_vars WHERE week='2026-W28'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1

def test_non_summer_not_flagged(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    build_season_vars(db_path, years=[2026])
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT summer_flag FROM season_vars WHERE week='2026-W1'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0

def test_week_key_format(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    build_season_vars(db_path, years=[2026])
    conn = sqlite3.connect(db_path)
    # 주차가 year-W{n} 형식으로 저장되는지 확인
    rows = conn.execute("SELECT week FROM season_vars WHERE week LIKE '2026-W%' LIMIT 5").fetchall()
    conn.close()
    assert len(rows) > 0
    for r in rows:
        assert r[0].startswith('2026-W')
