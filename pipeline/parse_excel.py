import sqlite3
import pandas as pd

EXTRA_CHANNEL_PATTERN = 'United Electronics'
SHEET_NAME = 'for Bi RAW_Weekly Sell out'

def parse_excel(excel_path: str, db_path: str) -> int:
    df = pd.read_excel(excel_path, sheet_name=SHEET_NAME, engine='openpyxl')

    # Column B(index 1)가 실제 채널명. Column A(index 0)는 =B2 수식 참조라 무시
    # Real file cols: Channel, Channel_1, ..., QTY, ..., Sell Thru, ...
    # Test fixture cols: Channel_Formula, Channel, ..., Sell out Qty, Sell Thru Qty
    col_map = {
        df.columns[1]: 'channel',
        'Year': 'year',
        'Week': 'week',
        'Dealer Channel Models': 'model',
        'Category': 'category',
        # Real Excel uses 'QTY'; test fixture uses 'Sell out Qty'
        'QTY': 'qty',
        'Sell out Qty': 'qty',
        # Real Excel uses 'Sell Thru'; test fixture uses 'Sell Thru Qty'
        'Sell Thru': 'sellthru',
        'Sell Thru Qty': 'sellthru',
    }
    df = df.rename(columns=col_map)

    # eXtra 필터
    df = df[df['channel'].astype(str).str.contains(EXTRA_CHANNEL_PATTERN, na=False)]

    df = df.dropna(subset=['year', 'week', 'model', 'qty'])
    df['year'] = df['year'].astype(int)
    df['qty'] = pd.to_numeric(df['qty'], errors='coerce').fillna(0)
    df['sellthru'] = pd.to_numeric(df.get('sellthru', 0), errors='coerce').fillna(0)
    df['category'] = df['category'].astype(str).str.strip()

    # 같은 channel+year+week+model → qty 합산, sellthru 평균
    agg = (
        df.groupby(['channel', 'year', 'week', 'model', 'category'])
        .agg(qty=('qty', 'sum'), sellthru=('sellthru', 'mean'))
        .reset_index()
    )

    conn = sqlite3.connect(db_path)
    inserted = 0
    for _, row in agg.iterrows():
        try:
            conn.execute(
                "INSERT OR REPLACE INTO weekly_sellout (channel, year, week, model, category, qty, sellthru) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row['channel'], row['year'], row['week'], row['model'],
                 row['category'], row['qty'], row['sellthru'])
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return inserted

if __name__ == "__main__":
    import os
    excel = "/home/ubuntu/2026/B2C Dealer Sell out FCST_2025_Actual_W17_rev_재작업.xlsx"
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    os.makedirs(os.path.dirname(db), exist_ok=True)
    # init db if not exists
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from pipeline.init_db import init_db
    init_db(db)
    n = parse_excel(excel, db)
    print(f"Inserted {n} rows into weekly_sellout")
