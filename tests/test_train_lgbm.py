import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db
from pipeline.build_season_vars import build_season_vars
from model.train_lgbm import train_and_predict, MIN_WEEKS_LEVEL1


def seed_full_db(db_path):
    """Seed 60 weeks of data for 2 models."""
    import random
    random.seed(42)
    conn = sqlite3.connect(db_path)
    for w in range(1, 61):
        year = 2025 if w <= 52 else 2026
        week_num = w if w <= 52 else w - 52
        week = f'W{week_num}'  # 패딩 없음 (실제 데이터 형식)
        for model, base in [('AM182C', 100), ('AM242C', 60)]:
            qty = base + random.randint(-10, 10)
            conn.execute(
                "INSERT OR REPLACE INTO weekly_sellout (channel,year,week,model,category,qty,sellthru) "
                "VALUES (?,?,?,?,?,?,?)",
                ('United Electronics', year, week, model, 'Split Inverter', qty, 0.8)
            )
    conn.commit()
    conn.close()


def test_train_returns_predictions(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    seed_full_db(db_path)
    build_season_vars(db_path)
    result = train_and_predict(db_path, models_dir=str(tmp_path / "models"))
    assert isinstance(result, list)
    assert len(result) > 0
    assert 'model' in result[0]
    assert 'week' in result[0]
    assert 'predicted' in result[0]
    assert 'level' in result[0]


def test_level1_requires_min_weeks(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    # Only 10 weeks — should fall to Level 2 or 3
    conn = sqlite3.connect(db_path)
    for w in range(1, 11):
        conn.execute(
            "INSERT OR REPLACE INTO weekly_sellout (channel,year,week,model,category,qty,sellthru) "
            "VALUES (?,?,?,?,?,?,?)",
            ('United Electronics', 2026, f'W{w}', 'AM182C', 'Split Inverter', 100, 0.8)
        )
    conn.commit()
    conn.close()
    build_season_vars(db_path)
    result = train_and_predict(db_path, models_dir=str(tmp_path / "models"))
    for r in result:
        if r['model'] == 'AM182C':
            assert r['level'] in ('L2_category', 'L3_total')
