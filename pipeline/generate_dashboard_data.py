"""
dashboard_data.json 생성 스크립트
- 모델 메타데이터 (Category, Compressor, Type, BTU)
- 2026 주차별 실적 (actuals)
- FCST 예측 (forecasts)
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
MMT_PATH = Path('/home/ubuntu/2026/10. Automation/01. Sell Out Dashboard/00. OR/01. Python Code/Model_Mapping_Table_v4.xlsx')
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
    mmt = mmt[mmt['Standard Model'].notna() & ~mmt['Standard Model'].astype(str).str.startswith('▶')]
    meta = {}
    for _, row in mmt.iterrows():
        model = str(row['Standard Model']).strip()
        cat_raw = str(row.get('Category', '')).strip()
        cat_map = {
            'Split AC': 'Split AC', 'Free Standing': 'PAC',
            'Window': 'Window', 'Window AC': 'Window',
            'Concealed': 'Concealed', 'Cassette': 'Cassette',
        }
        meta[model] = {
            'category': cat_map.get(cat_raw, cat_raw),
            'compressor': str(row.get('Compressor', '')).strip(),
            'type': str(row.get('Type', '')).strip(),
            'btu': btu_band(row.get('BTU', '')),
        }
    return meta


def load_actuals():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute('''
        SELECT model, year, week, SUM(qty) as qty
        FROM weekly_sellout
        WHERE year = 2026
        GROUP BY model, year, week
        ORDER BY model, week
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
    # 다음 주 번호 계산
    conn = sqlite3.connect(str(DB_PATH))
    latest = conn.execute("SELECT week FROM weekly_sellout WHERE year=2026 ORDER BY CAST(SUBSTR(week,2) AS INTEGER) DESC LIMIT 1").fetchone()
    conn.close()
    if latest:
        w_num = int(latest[0].replace('W', '')) + 1
        next_week = f'W{w_num}'
    else:
        next_week = 'W16'

    forecasts = []
    for f in data.get('forecasts', []):
        if f.get('level') in ('L1_sku', 'L3_coldstart'):
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
    return forecasts, next_week


def main():
    print('메타데이터 로드...')
    meta = load_metadata()

    print('실적 데이터 로드...')
    actuals = load_actuals()

    print('FCST 로드...')
    forecasts, next_week = load_forecasts()

    # 실적 + FCST에 등장하는 모델에 메타 보정
    all_models = set(a['model'] for a in actuals) | set(f['model'] for f in forecasts)
    meta_out = {}
    for m in sorted(all_models):
        if m in meta:
            meta_out[m] = meta[m]
        else:
            # DB category로 fallback
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute("SELECT category FROM weekly_sellout WHERE model=? LIMIT 1", (m,)).fetchone()
            conn.close()
            meta_out[m] = {
                'category': row[0] if row else '-',
                'compressor': '-',
                'type': '-',
                'btu': '-',
            }

    output = {
        'generated_at': datetime.now().isoformat(),
        'next_week': next_week,
        'metadata': meta_out,
        'actuals': actuals,
        'forecasts': forecasts,
    }

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f'완료: {OUT_PATH}')
    print(f'  모델 수: {len(meta_out)}')
    print(f'  실적 행: {len(actuals)}')
    print(f'  FCST 행: {len(forecasts)} (예측 주차: {next_week})')


if __name__ == '__main__':
    main()
