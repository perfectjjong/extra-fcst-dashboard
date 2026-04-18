import sqlite3
import glob
import re
import pandas as pd
from typing import List

PRICE_RAW_GLOB = "/home/ubuntu/2026/06. Price Tracking/00. eXtra/00. Raw/extra_ac_Prices_Tracking_Master_*.xlsx"
SHEET_NAME = "Prices DB"

# 가격 데이터 모델명 → 실적 데이터 모델명 수동 매핑 (자동 정규화 후 미매칭 모델)
MODEL_MANUAL_MAP = {
    # APNQ/APNW: 실적 데이터에 없는 모델 (Cassette/Portable 계열) — 현재 매핑 없음
    # 향후 실적 데이터에 등장하면 여기에 추가
    # 예: 'APNQ55GT3MA': 'AP55Q',
}

def _normalize_model(model_raw: str) -> str:
    """가격 데이터 모델명을 실적 데이터 모델코드 형식으로 정규화.
    예: 'AM182C0 NK2 SKU' -> 'AM182C', 'NS182C2 NK1 SKU' -> 'NS182C'
    """
    m = re.sub(r'\s+SKU.*$', '', model_raw.strip(), flags=re.IGNORECASE)
    m = re.sub(r'[\s\.]+.*$', '', m)
    m = re.sub(r'\d+$', '', m)  # 끝 숫자 제거 (0, 2, 3 등)
    return MODEL_MANUAL_MAP.get(m, m)


def parse_prices(xlsx_paths: List[str], db_path: str) -> int:
    frames = []
    for path in xlsx_paths:
        df = pd.read_excel(path, sheet_name=SHEET_NAME, engine='openpyxl')
        frames.append(df)
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)

    # Filter LG only
    df = df[df['Brand'].astype(str).str.upper() == 'LG']
    df = df.dropna(subset=['Scraped_At', 'Model_No'])

    df['Scraped_At'] = pd.to_datetime(df['Scraped_At'], errors='coerce')
    df = df.dropna(subset=['Scraped_At'])

    # ISO week aggregation
    df['year'] = df['Scraped_At'].dt.isocalendar().year.astype(int)
    df['week_num'] = df['Scraped_At'].dt.isocalendar().week.astype(int)
    df['week'] = df['week_num'].apply(lambda w: f'W{w}')  # W1, W8 형식 (앞에 0 없음)
    df['model'] = df['Model_No'].astype(str).apply(_normalize_model)

    df['Sale_Price'] = pd.to_numeric(df['Sale_Price'], errors='coerce')
    df['Discount_Rate'] = pd.to_numeric(df['Discount_Rate'], errors='coerce')

    agg = (
        df.groupby(['year', 'week', 'model'])
        .agg(avg_sale_price=('Sale_Price', 'mean'),
             avg_discount_rate=('Discount_Rate', 'mean'))
        .reset_index()
    )

    conn = sqlite3.connect(db_path)
    inserted = 0
    for _, row in agg.iterrows():
        conn.execute(
            "INSERT OR REPLACE INTO price_weekly (year, week, model, avg_sale_price, avg_discount_rate) "
            "VALUES (?, ?, ?, ?, ?)",
            (row['year'], row['week'], row['model'],
             row['avg_sale_price'], row['avg_discount_rate'])
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


if __name__ == "__main__":
    import os
    paths = glob.glob(PRICE_RAW_GLOB)
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    n = parse_prices(paths, db)
    print(f"Inserted {n} price_weekly rows from {len(paths)} files")
