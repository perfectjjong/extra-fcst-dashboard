import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.init_db import init_db
from pipeline.fcst_snapshot import save_snapshot, compute_accuracy


def make_test_db():
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    init_db(f.name)
    return f.name


def test_save_snapshot_creates_rows():
    """save_snapshot은 forecasts를 fcst_snapshots에 저장해야 한다."""
    import sqlite3
    db = make_test_db()
    try:
        forecasts = [
            {'model': 'ND182C', 'week': 'W17', 'level': 'L1_sku', 'predicted': 120.0, 'ci_low': 80.0, 'ci_high': 160.0},
            {'model': 'ND242C', 'week': 'W17', 'level': 'L1_sku', 'predicted': 50.0, 'ci_low': 30.0, 'ci_high': 70.0},
        ]
        save_snapshot(db, forecasts, week='W17')
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM fcst_snapshots WHERE week='W17'").fetchone()[0]
        conn.close()
        assert count == 2, f"Expected 2 rows, got {count}"
    finally:
        os.unlink(db)


def test_compute_accuracy_writes_accuracy_log():
    """compute_accuracy는 실적 vs 예측 비교 후 fcst_accuracy_log에 기록해야 한다."""
    import sqlite3
    db = make_test_db()
    try:
        forecasts = [
            {'model': 'ND182C', 'week': 'W17', 'level': 'L1_sku', 'predicted': 120.0, 'ci_low': 80.0, 'ci_high': 160.0},
        ]
        save_snapshot(db, forecasts, week='W17')

        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO weekly_sellout (channel, year, week, model, category, qty) VALUES (?,?,?,?,?,?)",
                     ('United Electronics Company', 2026, 'W17', 'ND182C', 'Split AC', 140.0))
        conn.commit()
        conn.close()

        retrain_needed = compute_accuracy(db, week='W17', mape_threshold=0.30)
        # MAPE = |140-120|/140 ≈ 0.143 < 0.30
        assert retrain_needed == False, "MAPE 14% < 30%, 재학습 불필요"

        conn = sqlite3.connect(db)
        log = conn.execute("SELECT mape FROM fcst_accuracy_log WHERE week='W17'").fetchall()
        conn.close()
        assert len(log) == 1, f"Expected 1 accuracy log, got {len(log)}"
        assert abs(log[0][0] - (20.0/140.0)) < 0.01, f"MAPE 계산 오류: {log[0][0]}"
    finally:
        os.unlink(db)


def test_compute_accuracy_triggers_retrain():
    """MAPE > threshold면 retrain_needed=True를 반환해야 한다."""
    import sqlite3
    db = make_test_db()
    try:
        forecasts = [
            {'model': 'ND182C', 'week': 'W17', 'level': 'L1_sku', 'predicted': 200.0, 'ci_low': 150.0, 'ci_high': 250.0},
        ]
        save_snapshot(db, forecasts, week='W17')

        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO weekly_sellout (channel, year, week, model, category, qty) VALUES (?,?,?,?,?,?)",
                     ('United Electronics Company', 2026, 'W17', 'ND182C', 'Split AC', 100.0))
        conn.commit()
        conn.close()

        # MAPE = |100-200|/100 = 1.0 = 100% > 30%
        retrain_needed = compute_accuracy(db, week='W17', mape_threshold=0.30)
        assert retrain_needed == True, "MAPE 100% > 30%, 재학습 필요"
    finally:
        os.unlink(db)
