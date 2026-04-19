import sqlite3, sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db

def test_all_tables_created(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
              if not r[0].startswith('sqlite_')}
    conn.close()
    assert tables == {'weekly_sellout', 'price_weekly', 'season_vars', 'requests', 'fcst_feedback', 'fcst_accuracy_log', 'fcst_snapshots'}

def test_unique_constraints(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO weekly_sellout (channel,year,week,model,category,qty,sellthru) VALUES ('eXtra',2026,'W01','AM182C','Inverter',100,0.8)")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO weekly_sellout (channel,year,week,model,category,qty,sellthru) VALUES ('eXtra',2026,'W01','AM182C','Inverter',200,0.9)")
        conn.commit()
    conn.close()
