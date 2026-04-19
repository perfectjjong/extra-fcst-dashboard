import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from model.train_lgbm import forecast_multistep

DB_PATH = str(Path(__file__).parent.parent / 'data' / 'sellout.db')
MODELS_DIR = str(Path(__file__).parent.parent / 'model' / 'models')


def test_forecast_multistep_returns_correct_count():
    """forecast_multistep은 SKU당 n_weeks개의 예측을 반환해야 한다."""
    results = forecast_multistep(DB_PATH, MODELS_DIR, start_week_num=17, n_weeks=5)
    assert len(results) > 0, "예측 결과가 있어야 함"
    from collections import Counter
    week_counts = Counter(r['model'] for r in results)
    assert max(week_counts.values()) == 5, f"각 모델은 5주 예측 필요, got {week_counts}"


def test_forecast_multistep_week_labels():
    """주차 레이블이 W17, W18... 형식이어야 한다."""
    results = forecast_multistep(DB_PATH, MODELS_DIR, start_week_num=17, n_weeks=3)
    weeks = sorted(set(r['week'] for r in results))
    assert weeks == ['W17', 'W18', 'W19'], f"주차 레이블 오류: {weeks}"


def test_forecast_multistep_non_negative():
    """예측값은 음수가 없어야 한다."""
    results = forecast_multistep(DB_PATH, MODELS_DIR, start_week_num=17, n_weeks=10)
    negatives = [r for r in results if r['predicted'] < 0]
    assert len(negatives) == 0, f"음수 예측: {negatives}"
