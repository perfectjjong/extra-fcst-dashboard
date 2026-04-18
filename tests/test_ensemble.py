import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db
from pipeline.build_season_vars import build_season_vars
from model.train_lgbm import train_and_predict
from model.ensemble import build_fcst_output


def seed_db_60w(db_path):
    import random
    random.seed(42)
    conn = sqlite3.connect(db_path)
    for w in range(1, 61):
        yr = 2025 if w <= 52 else 2026
        wn = w if w <= 52 else w - 52
        week = f'W{wn}'  # 패딩 없음 (실제 데이터 형식)
        for model, base in [('AM182C', 100), ('AM242C', 60)]:
            qty = base + random.randint(-10, 10)
            conn.execute(
                "INSERT OR REPLACE INTO weekly_sellout (channel,year,week,model,category,qty,sellthru) "
                "VALUES (?,?,?,?,?,?,?)",
                ('United Electronics', yr, week, model, 'Split Inverter', qty, 0.8)
            )
    conn.commit()
    conn.close()


def test_fcst_output_schema(tmp_path):
    db_path = str(tmp_path / "test.db")
    out_path = str(tmp_path / "fcst_output.json")
    init_db(db_path)
    seed_db_60w(db_path)
    build_season_vars(db_path)
    lgbm_results = train_and_predict(db_path, str(tmp_path / "models"))
    build_fcst_output(lgbm_results, db_path, out_path)
    assert os.path.exists(out_path)
    with open(out_path) as f:
        data = json.load(f)
    assert 'generated_at' in data
    assert 'forecasts' in data
    assert isinstance(data['forecasts'], list)
    assert len(data['forecasts']) > 0
    forecast = data['forecasts'][0]
    for key in ('model', 'category', 'level', 'predicted', 'ci_low', 'ci_high'):
        assert key in forecast
