import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))
from model.train_prophet import train_prophet_total

LGBM_WEIGHT = 0.7
PROPHET_WEIGHT = 0.3

# 신규 런칭 예정 모델 cold-start 예측
# 연간 목표 5,000대 ÷ 4개 모델, W19(5월 초) 런칭 기준 남은 34주 분배
# 주당: 5000 / 4 / 52 ≈ 24대 (연간 균등 배분)
COLDSTART_MODELS = [
    {'model': 'W181EC.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
    {'model': 'W181EH.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
    {'model': 'W242EC.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
    {'model': 'W242EH.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
]


def build_fcst_output(lgbm_results: List[Dict], db_path: str, output_path: str) -> None:
    models_dir = os.path.join(os.path.dirname(__file__), 'models')
    prophet_total = train_prophet_total(db_path, models_dir)

    lgbm_total = sum(r['predicted'] for r in lgbm_results if r.get('predicted') is not None)

    if lgbm_total > 0 and prophet_total is not None:
        scale = (LGBM_WEIGHT * lgbm_total + PROPHET_WEIGHT * prophet_total) / lgbm_total
    else:
        scale = 1.0

    forecasts = []
    for r in lgbm_results:
        forecasts.append({
            'model': r['model'],
            'category': r['category'],
            'level': r['level'],
            'week': r['week'],
            'predicted': round(r['predicted'] * scale, 1),
            'ci_low': round(r['ci_low'] * scale, 1),
            'ci_high': round(r['ci_high'] * scale, 1),
            'mape': r.get('mape'),
            'lgbm_raw': r['predicted'],
            'prophet_total_contribution': round(prophet_total, 1) if prophet_total else None,
        })

    # 신규 런칭 모델 cold-start 예측 추가
    existing_models = {f['model'] for f in forecasts}
    for cs in COLDSTART_MODELS:
        if cs['model'] not in existing_models:
            w = cs['weekly_fcst']
            forecasts.append({
                'model': cs['model'],
                'category': cs['category'],
                'level': 'L3_coldstart',
                'week': 'NEXT',
                'predicted': round(w, 1),
                'ci_low': round(w * 0.5, 1),
                'ci_high': round(w * 1.5, 1),
                'mape': None,
                'lgbm_raw': None,
                'prophet_total_contribution': None,
            })

    output = {
        'generated_at': datetime.now().isoformat(),
        'lgbm_total': round(lgbm_total, 1),
        'prophet_total': round(prophet_total, 1) if prophet_total else None,
        'ensemble_scale': round(scale, 4),
        'forecasts': forecasts,
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from model.train_lgbm import train_and_predict
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    models_dir = os.path.join(os.path.dirname(__file__), 'models')
    out = os.path.join(os.path.dirname(__file__), '..', 'dashboard', 'fcst_output.json')
    lgbm_results = train_and_predict(db, models_dir)
    build_fcst_output(lgbm_results, db, out)
    print(f"fcst_output.json written — {len(lgbm_results)} forecasts")
