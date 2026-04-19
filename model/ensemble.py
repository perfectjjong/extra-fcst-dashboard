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

WINDOW_SEEC_MODELS = ['W181EC.SN0', 'W181EH.SN0', 'W242EC.SN0', 'W242EH.SN0']
WINDOW_PREDECESSORS = {
    'W181EC.SN0': 'C182EC.SN2',   # 18K Cooling Only
    'W181EH.SN0': 'C182EH.SN2',   # 18K Heat Pump
    'W242EC.SN0': 'C242EC.SN2',   # 24K Cooling Only
    'W242EH.SN0': None,            # 24K Heat Pump — no meaningful predecessor
}
WINDOW_COLDSTART_START_WEEK = 22   # W22 = June launch
WINDOW_COLDSTART_TARGET = 5000.0   # 4-model annual target

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


def _compute_window_coldstart(db_path: str) -> List[Dict]:
    """
    Window SEEC 신제품(W22-W52) cold-start 예측.
    - 과거 전세대 Window AC(C182EC.SN2 등) 주차별 판매 트렌드로 seasonality 도출
    - 전체 판매 비중으로 BTU/H-C 모델별 allocation
    - 4모델 합산 WINDOW_COLDSTART_TARGET 기준 스케일링
    """
    target_weeks = list(range(WINDOW_COLDSTART_START_WEEK, 53))
    predecessor_models = [m for m in WINDOW_PREDECESSORS.values() if m]

    conn = sqlite3.connect(db_path)

    # 2024 W22-W52: 월별 합계로 seasonal 가중치 도출
    rows = conn.execute('''
        SELECT week, SUM(qty) as qty
        FROM weekly_sellout
        WHERE model IN ({})
          AND year = 2024
          AND CAST(SUBSTR(week,2) AS INTEGER) >= ?
        GROUP BY week
    '''.format(','.join('?' * len(predecessor_models))),
        predecessor_models + [WINDOW_COLDSTART_START_WEEK]
    ).fetchall()

    # 주차 → 월 매핑 (W22-W52)
    def week_to_month(w: int) -> int:
        if w <= 25: return 6
        if w <= 29: return 7
        if w <= 33: return 8
        if w <= 37: return 9
        if w <= 41: return 10
        if w <= 45: return 11
        return 12

    monthly_totals: Dict[int, float] = {m: 0.0 for m in range(6, 13)}
    for week_str, qty in rows:
        if qty and qty > 0:
            w = int(week_str.replace('W', ''))
            monthly_totals[week_to_month(w)] += float(qty)

    total_hist = sum(monthly_totals.values())

    weeks_per_month = {m: sum(1 for w in target_weeks if week_to_month(w) == m) for m in range(6, 13)}

    if total_hist > 0:
        seasonal_profile = {}
        for w in target_weeks:
            m = week_to_month(w)
            month_share = monthly_totals[m] / total_hist
            seasonal_profile[f'W{w}'] = month_share / weeks_per_month[m] if weeks_per_month[m] else 0.0
    else:
        # 과거 W22-W52 데이터 없음 → 동일 분포
        seasonal_profile = {f'W{w}': 1.0 / len(target_weeks) for w in target_weeks}

    # BTU/HC 모델 비중: 2024+2025 전체 기간 기준
    mix_rows = conn.execute('''
        SELECT model, SUM(qty) as total
        FROM weekly_sellout
        WHERE model IN ({})
          AND year IN (2024, 2025)
        GROUP BY model
    '''.format(','.join('?' * len(predecessor_models))),
        predecessor_models
    ).fetchall()
    conn.close()

    mix = {r[0]: max(0.0, float(r[1])) for r in mix_rows}

    co_18k = mix.get('C182EC.SN2', 0)
    hp_18k = mix.get('C182EH.SN2', 0)
    co_24k = mix.get('C242EC.SN2', 0)

    # 24K HP 전세대(C242EH.SN2) 실적 없음 → 18K CO:HP 비율로 24K HP 추정
    # 전세대 유통/라인업 문제로 미판매된 것이지 신제품도 같다고 볼 수 없음
    hp_co_ratio = (hp_18k / co_18k) if co_18k > 0 else 0.246
    hp_24k_est = co_24k * hp_co_ratio

    adjusted_total = co_18k + hp_18k + co_24k + hp_24k_est

    if adjusted_total > 0:
        model_shares = {
            'W181EC.SN0': co_18k / adjusted_total,
            'W181EH.SN0': hp_18k / adjusted_total,
            'W242EC.SN0': co_24k / adjusted_total,
            'W242EH.SN0': hp_24k_est / adjusted_total,
        }
    else:
        model_shares = {'W181EC.SN0': 0.583, 'W181EH.SN0': 0.144, 'W242EC.SN0': 0.219, 'W242EH.SN0': 0.054}

    results = []
    for new_model in WINDOW_SEEC_MODELS:
        share = model_shares.get(new_model, 0.0)
        for w in target_weeks:
            week_str = f'W{w}'
            season_wt = seasonal_profile.get(week_str, 0.0)
            predicted = round(WINDOW_COLDSTART_TARGET * share * season_wt)
            results.append({
                'model': new_model,
                'category': 'Window',
                'level': 'L3_coldstart',
                'week': week_str,
                'predicted': predicted,
                'ci_low': round(predicted * 0.6),
                'ci_high': round(predicted * 1.4),
            })

    # 합계를 정확히 WINDOW_COLDSTART_TARGET(5,000)에 맞춤 — 가장 큰 모델의 마지막 주차 조정
    actual_total = sum(r['predicted'] for r in results)
    diff = int(WINDOW_COLDSTART_TARGET) - actual_total
    if diff != 0:
        # W181EC.SN0 마지막 예측 주차에 차이 반영
        for r in reversed(results):
            if r['model'] == 'W181EC.SN0' and r['predicted'] > 0:
                r['predicted'] += diff
                r['ci_low'] = round(r['predicted'] * 0.6)
                r['ci_high'] = round(r['predicted'] * 1.4)
                break

    return results


def _compute_scenarios(forecasts: List[Dict]) -> Dict:
    scenarios = {'promo_10': {}, 'promo_20': {}, 'ramadan': {}}
    for f in forecasts:
        model = f['model']
        week = f.get('week', 'NEXT')
        base = f['predicted']
        key = f'{model}|{week}'
        scenarios['promo_10'][key] = round(base * (1 + PROMO_ELASTICITY * 0.10))
        scenarios['promo_20'][key] = round(base * (1 + PROMO_ELASTICITY * 0.20))
        is_ramadan = week in RAMADAN_WEEKS_2026
        scenarios['ramadan'][key] = round(base * (RAMADAN_BOOST if is_ramadan else 1.0))
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
            'predicted': round(r['predicted'] * scale),
            'ci_low': round(r['ci_low'] * scale),
            'ci_high': round(r['ci_high'] * scale),
            'mape': r.get('mape'),
            'lgbm_raw': r['predicted'],
            'prophet_total_contribution': round(prophet_total) if prophet_total else None,
        })

    # Window SEEC 신제품은 단기(NEXT week) 예측에서 제외 (W22 이후에만 예측)
    existing_models = {f['model'] for f in forecasts}

    long_range = []
    if multistep_results:
        for r in multistep_results:
            long_range.append({
                'model': r['model'],
                'category': r['category'],
                'level': r['level'],
                'week': r['week'],
                'month': WEEK_MONTH.get(r['week'], ''),
                'predicted': round(r['predicted'] * scale),
                'ci_low': round(r['ci_low'] * scale),
                'ci_high': round(r['ci_high'] * scale),
            })

    # Window SEEC cold-start: 과거 전세대 트렌드 기반 W22-W52 장기 예측 추가
    cs_models_existing = {r['model'] for r in long_range}
    if any(m not in cs_models_existing for m in WINDOW_SEEC_MODELS):
        window_cs = _compute_window_coldstart(db_path)
        for entry in window_cs:
            if entry['model'] not in cs_models_existing:
                entry['month'] = WEEK_MONTH.get(entry['week'], '')
                long_range.append(entry)

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
