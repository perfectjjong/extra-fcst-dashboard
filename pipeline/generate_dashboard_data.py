"""
dashboard_data.json 생성 스크립트
- 모델 메타데이터 (Category, Compressor, Type, BTU)
- 2026 주차별 실적 (actuals)
- FCST 예측 — 단기 (next week) + 장기 (W17-W52)
- 시나리오 데이터
- 정확도 이력
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
MMT_PATH = Path('/home/ubuntu/2026/07. Claude Rule/00. Model Mapping/Model_Mapping_Master_v6_Updated.xlsx')
DB_PATH = BASE_DIR / 'data' / 'sellout.db'
FCST_PATH = BASE_DIR / 'dashboard' / 'fcst_output.json'
OUT_PATH = BASE_DIR / 'dashboard' / 'dashboard_data.json'

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


def btu_band(btu_raw):
    try:
        v = int(str(btu_raw).replace(',', '').strip())
        if v <= 13000: return '12K'
        if v <= 20000: return '18K'
        if v <= 26000: return '22K'
        if v <= 32000: return '30K'
        return '36K+'
    except Exception:
        return '-'


def load_metadata():
    mmt = pd.read_excel(MMT_PATH)
    mmt = mmt[mmt['Unified Model'].notna()]
    # v6: Category 값 중 RAC → Split AC로 정규화
    cat_map = {
        'Split AC': 'Split AC', 'RAC': 'Split AC',
        'Free Standing': 'PAC',
        'Window': 'Window',
        'Concealed': 'Concealed', 'Cassette': 'Cassette',
    }
    meta = {}
    for _, row in mmt.iterrows():
        model = str(row['Unified Model']).strip()
        if not model or model == 'nan':
            continue
        cat_raw = str(row.get('Category', '')).strip()
        # v6 BTU는 이미 '12K', '18K' 형식 문자열
        btu_raw = str(row.get('BTU', '')).strip()
        meta[model] = {
            'category': cat_map.get(cat_raw, cat_raw),
            'compressor': str(row.get('Compressor', '')).strip(),
            'sub_category': str(row.get('Sub-Category', '')).strip(),
            'btu': btu_raw if btu_raw and btu_raw != 'nan' else '-',
        }
    return meta


def load_actuals():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute('''
        SELECT model, year, week, SUM(qty) as qty
        FROM weekly_sellout
        WHERE year = 2026
        GROUP BY model, year, week
        ORDER BY model, CAST(SUBSTR(week,2) AS INTEGER)
    ''').fetchall()
    conn.close()
    actuals = []
    for model, year, week, qty in rows:
        if qty and qty > 0:
            actuals.append({
                'model': model,
                'year': year,
                'week': week,
                'month': WEEK_MONTH.get(week, ''),
                'qty': round(float(qty), 0),
            })
    return actuals


def load_forecasts():
    with open(FCST_PATH, encoding='utf-8') as f:
        data = json.load(f)

    conn = sqlite3.connect(str(DB_PATH))
    latest = conn.execute(
        "SELECT week FROM weekly_sellout WHERE year=2026 "
        "ORDER BY CAST(SUBSTR(week,2) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if latest:
        w_num = int(latest[0].replace('W', '')) + 1
        next_week = f'W{w_num}'
    else:
        next_week = 'W17'

    # 단기 예측 (next week)
    forecasts = []
    for f in data.get('forecasts', []):
        if f.get('level') in ('L1_sku', 'L3_coldstart', 'L2_category'):
            forecasts.append({
                'model': f['model'],
                'week': next_week,
                'month': WEEK_MONTH.get(next_week, ''),
                'level': f['level'],
                'predicted': f.get('predicted', 0),
                'ci_low': f.get('ci_low', 0),
                'ci_high': f.get('ci_high', 0),
                'mape': f.get('mape'),
            })

    # 장기 예측 (W17-W52)
    long_range = []
    for f in data.get('long_range_forecasts', []):
        long_range.append({
            'model': f['model'],
            'week': f['week'],
            'month': f.get('month', WEEK_MONTH.get(f['week'], '')),
            'level': f['level'],
            'predicted': f.get('predicted', 0),
            'ci_low': f.get('ci_low', 0),
            'ci_high': f.get('ci_high', 0),
        })

    scenarios = data.get('scenarios', {})

    return forecasts, long_range, scenarios, next_week


def load_accuracy_history():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT week, model, level, mape, logged_at FROM fcst_accuracy_log "
        "ORDER BY CAST(SUBSTR(week,2) AS INTEGER)"
    ).fetchall()
    conn.close()
    return [
        {'week': r[0], 'model': r[1], 'level': r[2], 'mape': r[3], 'logged_at': r[4]}
        for r in rows
    ]


def main():
    print('메타데이터 로드...')
    meta = load_metadata()

    print('실적 데이터 로드...')
    actuals = load_actuals()

    print('FCST 로드...')
    forecasts, long_range, scenarios, next_week = load_forecasts()

    print('정확도 이력 로드...')
    accuracy_history = load_accuracy_history()

    # v6 MMT에 없는 모델의 수동 보정 (DB category는 eXtra 자체 용어라 신뢰 불가)
    MANUAL_META = {
        'NG182H':     {'category': 'Split AC', 'compressor': 'Inverter', 'sub_category': 'Split Inverter', 'btu': '18K'},
        'NG242H':     {'category': 'Split AC', 'compressor': 'Inverter', 'sub_category': 'Split Inverter', 'btu': '24K'},
        'AF182C0N20': {'category': 'Split AC', 'compressor': 'Inverter', 'sub_category': 'Split Inverter', 'btu': '18K'},
        'AF242C0N20': {'category': 'Split AC', 'compressor': 'Inverter', 'sub_category': 'Split Inverter', 'btu': '24K'},
        'APNQ55GT3MA':{'category': 'PAC', 'compressor': 'Inverter', 'sub_category': 'Free Standing', 'btu': '48K'},
        'APNW55GT3MA':{'category': 'PAC', 'compressor': 'Inverter', 'sub_category': 'Free Standing', 'btu': '55K'},
        'W181EC.SN0': {'category': 'Window', 'compressor': 'Inverter', 'sub_category': 'Window SEEC', 'btu': '18K'},
        'W181EH.SN0': {'category': 'Window', 'compressor': 'Inverter', 'sub_category': 'Window SEEC', 'btu': '18K'},
        'W242EC.SN0': {'category': 'Window', 'compressor': 'Inverter', 'sub_category': 'Window SEEC', 'btu': '24K'},
        'W242EH.SN0': {'category': 'Window', 'compressor': 'Inverter', 'sub_category': 'Window SEEC', 'btu': '24K'},
    }

    # 모델 메타 보정 (v6 MMT → 수동 보정 → DB fallback 순)
    all_models = (
        set(a['model'] for a in actuals) |
        set(f['model'] for f in forecasts) |
        set(f['model'] for f in long_range)
    )
    meta_out = {}
    for m in sorted(all_models):
        if m in meta:
            meta_out[m] = meta[m]
        elif m in MANUAL_META:
            meta_out[m] = MANUAL_META[m]
        else:
            meta_out[m] = {'category': '-', 'compressor': '-', 'sub_category': '-', 'btu': '-'}

    # forecasts / long_range에 category 필드 추가 (metadata 기준)
    for f in forecasts:
        f['category'] = meta_out.get(f['model'], {}).get('category', '-')
    for f in long_range:
        f['category'] = meta_out.get(f['model'], {}).get('category', '-')

    output = {
        'generated_at': datetime.now().isoformat(),
        'next_week': next_week,
        'metadata': meta_out,
        'actuals': actuals,
        'forecasts': forecasts,
        'long_range_forecasts': long_range,
        'scenarios': scenarios,
        'accuracy_history': accuracy_history,
    }

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f'완료: {OUT_PATH}')
    print(f'  모델 수: {len(meta_out)}')
    print(f'  실적 행: {len(actuals)}')
    print(f'  단기 FCST: {len(forecasts)} (예측 주차: {next_week})')
    print(f'  장기 FCST: {len(long_range)} rows')
    print(f'  정확도 이력: {len(accuracy_history)} rows')


if __name__ == '__main__':
    main()
