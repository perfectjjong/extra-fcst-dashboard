import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from model.train_prophet import train_prophet_total

LGBM_WEIGHT = 0.7
PROPHET_WEIGHT = 0.3
PROMO_ELASTICITY = 0.7   # 할인 +10% → 수요 +7%
RAMADAN_BOOST = 1.20     # 라마단 기간 +20%

COLDSTART_MODELS = [
    {'model': 'W181EC.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
    {'model': 'W181EH.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
    {'model': 'W242EC.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
    {'model': 'W242EH.SN0', 'category': 'Window', 'weekly_fcst': 24.0},
]

WEEK_MONTH = {
    'W1': 'Jan', 'W2': 'Jan', 'W3': 'Jan', 'W4': 'Jan',
    'W5': 'Feb', 'W6': 'Feb', 'W7': 'Feb', 'W8': 'Feb',
    'W9': 'Mar', 'W10': 'Mar', 'W11': 'Mar', 'W12': 'Mar',
    'W13': 'Mar', 'W14': 'Apr', 'W15': 'Apr', 'W16': 'Apr',
    'W17': 'Apr', 'W18': 'May', 'W19': 'May', 'W20': 'May',
    'W21': 'May', 'W22': 'Jun', 'W23': 'Jun', 'W24': 'Jun',
    'W25': 'Jun', 'W26': 'Jul', 'W27': 'Jul', 'W28': 'Jul',
    'W29': 'Jul', 'W30': 'Aug', 'W31': 'Aug', 'W32': 'Aug',
    'W33': 'Aug', 'W34': 'Sep', 'W35': 'Sep', 'W36': 'Sep',
    'W37': 'Sep', 'W38': 'Oct', 'W39': 'Oct', 'W40': 'Oct',
    'W41': 'Oct', 'W42': 'Nov', 'W43': 'Nov', 'W44': 'Nov',
    'W45': 'Nov', 'W46': 'Dec', 'W47': 'Dec', 'W48': 'Dec',
    'W49': 'Dec', 'W50': 'Dec', 'W51': 'Dec', 'W52': 'Dec',
}

RAMADAN_WEEKS_2026 = {'W5', 'W6', 'W7', 'W8', 'W9'}


def _compute_scenarios(forecasts: List[Dict]) -> Dict:
    scenarios = {'promo_10': {}, 'promo_20': {}, 'ramadan': {}}
    for f in forecasts:
        model = f['model']
        week = f.get('week', 'NEXT')
        base = f['predicted']
        key = f'{model}|{week}'
        scenarios['promo_10'][key] = round(base * (1 + PROMO_ELASTICITY * 0.10), 1)
        scenarios['promo_20'][key] = round(base * (1 + PROMO_ELASTICITY * 0.20), 1)
        is_ramadan = week in RAMADAN_WEEKS_2026
        scenarios['ramadan'][key] = round(base * (RAMADAN_BOOST if is_ramadan else 1.0), 1)
    return scenarios


def build_fcst_output(
    lgbm_results: List[Dict],
    db_path: str,
    output_path: str,
    multistep_results: Optional[List[Dict]] = None,
) -> None:
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

    long_range = []
    if multistep_results:
        for r in multistep_results:
            long_range.append({
                'model': r['model'],
                'category': r['category'],
                'level': r['level'],
                'week': r['week'],
                'month': WEEK_MONTH.get(r['week'], ''),
                'predicted': round(r['predicted'] * scale, 1),
                'ci_low': round(r['ci_low'] * scale, 1),
                'ci_high': round(r['ci_high'] * scale, 1),
            })
        cs_models_existing = {r['model'] for r in long_range}
        weeks_in_long_range = sorted(set(r['week'] for r in long_range))
        for cs in COLDSTART_MODELS:
            if cs['model'] not in cs_models_existing:
                for wk in weeks_in_long_range:
                    w = cs['weekly_fcst']
                    long_range.append({
                        'model': cs['model'],
                        'category': cs['category'],
                        'level': 'L3_coldstart',
                        'week': wk,
                        'month': WEEK_MONTH.get(wk, ''),
                        'predicted': round(w, 1),
                        'ci_low': round(w * 0.5, 1),
                        'ci_high': round(w * 1.5, 1),
                    })

    all_for_scenario = forecasts + long_range
    scenarios = _compute_scenarios(all_for_scenario)

    output = {
        'generated_at': datetime.now().isoformat(),
        'lgbm_total': round(lgbm_total, 1),
        'prophet_total': round(prophet_total, 1) if prophet_total else None,
        'ensemble_scale': round(scale, 4),
        'forecasts': forecasts,
        'long_range_forecasts': long_range,
        'scenarios': scenarios,
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from model.train_lgbm import train_and_predict, forecast_multistep
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    models_dir = os.path.join(os.path.dirname(__file__), 'models')
    out = os.path.join(os.path.dirname(__file__), '..', 'dashboard', 'fcst_output.json')

    conn = sqlite3.connect(db)
    latest = conn.execute(
        "SELECT week FROM weekly_sellout WHERE year=2026 "
        "ORDER BY CAST(SUBSTR(week,2) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    conn.close()
    start_w = int(latest[0].replace('W', '')) + 1 if latest else 17
    remaining = max(1, 52 - start_w + 1)

    lgbm_results = train_and_predict(db, models_dir)
    multistep = forecast_multistep(db, models_dir, start_week_num=start_w, n_weeks=remaining)
    build_fcst_output(lgbm_results, db, out, multistep_results=multistep)
    print(f"fcst_output.json written — {len(lgbm_results)} short-range, {len(multistep)} long-range forecasts")
