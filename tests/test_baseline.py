import os
import sqlite3
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db
from model.baseline import compute_naive_mape, week_to_int

def seed_db(db_path):
    conn = sqlite3.connect(db_path)
    rows = [
        ('United Electronics', 2026, 'W1', 'AM182C', 'Inverter', 100, 0.8),
        ('United Electronics', 2026, 'W2', 'AM182C', 'Inverter', 120, 0.9),
        ('United Electronics', 2026, 'W3', 'AM182C', 'Inverter', 110, 0.85),
        ('United Electronics', 2026, 'W4', 'AM182C', 'Inverter', 130, 0.9),
    ]
    conn.executemany(
        "INSERT INTO weekly_sellout (channel,year,week,model,category,qty,sellthru) VALUES (?,?,?,?,?,?,?)",
        rows
    )
    conn.commit()
    conn.close()

def test_naive_mape_formula(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    seed_db(db_path)
    result = compute_naive_mape(db_path, lookback_weeks=3)
    assert 'AM182C' in result
    mape = result['AM182C']
    # W2→W3: |110-120|/110=0.0909, W3→W4: |130-110|/130=0.1538 → mean ~0.122
    assert 0.05 < mape < 0.25

def test_week_to_int():
    assert week_to_int(2026, 'W1') == 202601
    assert week_to_int(2026, 'W17') == 202617
    assert week_to_int(2025, 'W52') < week_to_int(2026, 'W1')
