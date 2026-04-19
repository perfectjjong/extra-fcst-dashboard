"""
competitor_prices 테이블 구축 파이프라인
3개 데이터 소스를 통합:
  1. eXtra Price Tracking Raw (2026, 일별, VAT-포함) → ÷1.15 → week 집계
  2. extra_2025.xlsx (2025, 월별, VAT-제외, 전 브랜드)
  3. week01-16.xlsx (2026, 일별, VAT-제외, Sale Value/Qty → 단가) → week 집계
"""
import glob
import os
import sqlite3
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent

PRICE_TRACKING_GLOB = "/home/ubuntu/2026/06. Price Tracking/00. eXtra/00. Raw/extra_ac_Prices_Tracking_Master_*.xlsx"
EXTRA_2025_PATH = "/home/ubuntu/2026/07. Claude Rule/extra_2025.xlsx"
WEEKLY_SELLOUT_GLOB = "/home/ubuntu/2026/10. Automation/01. Sell Out Dashboard/00. OR/00. Raw/00. eXtra/00. Weekly Sell out/week*.xlsx"

VAT_RATE = 1.15

# Category / Sub Family → 표준 이름
SUB_FAMILY_NORM = {
    # Price Tracking Category 값
    'Split Air Conditioner': 'Mini Split AC',
    'Window Air Conditioner': 'Window AC',
    'Free Standing Air Conditioner': 'Free Standing AC',
    # extra_2025 & week01-16 SUB FAMILY 값
    'MINI SPLIT AIR CONDITIONER': 'Mini Split AC',
    'WINDOW AIR CONDITIONER': 'Window AC',
    'SEEC WINDOW AIR CONDITIONER': 'Window AC',
    'FREE STANDING AIR CONDITIONER': 'Free Standing AC',
    'CASSETTE TYPE AIR CONDITIONER': 'Cassette AC',
    'PORTABLE COOLER': 'Portable AC',
    'PORTABLE': 'Portable AC',
    'AIR CURTAINS': 'Air Curtain',
}

AC_SUB_FAMILIES = set(SUB_FAMILY_NORM.keys())


def _norm_sf(raw: str) -> str:
    return SUB_FAMILY_NORM.get(str(raw).strip(), str(raw).strip())


def _load_price_tracking() -> pd.DataFrame:
    """Price Tracking Raw 파일 로드 → (year, week, brand, sub_family, compressor, avg_price_vat_ex, total_qty)"""
    paths = sorted(glob.glob(PRICE_TRACKING_GLOB))
    if not paths:
        print("  [경고] Price Tracking 파일 없음")
        return pd.DataFrame()

    frames = []
    for p in paths:
        try:
            df = pd.read_excel(p, sheet_name='Prices DB', engine='openpyxl')
            frames.append(df)
        except Exception as e:
            print(f"  [경고] {os.path.basename(p)} 읽기 실패: {e}")

    df = pd.concat(frames, ignore_index=True)
    df['Scraped_At'] = pd.to_datetime(df['Scraped_At'], errors='coerce')
    df = df.dropna(subset=['Scraped_At', 'Brand', 'Category'])

    # Sale_Price: VAT-포함 → VAT-제외
    df['Sale_Price'] = pd.to_numeric(df['Sale_Price'], errors='coerce')
    df['Standard_Price'] = pd.to_numeric(df['Standard_Price'], errors='coerce')
    # Sale_Price 없으면 Standard_Price 사용
    df['price_raw'] = df['Sale_Price'].where(df['Sale_Price'].notna(), df['Standard_Price'])
    df = df.dropna(subset=['price_raw'])
    df['price_vat_ex'] = df['price_raw'] / VAT_RATE

    df['sub_family'] = df['Category'].apply(_norm_sf)
    df['compressor'] = df['Compressor_Type'].fillna('-').astype(str).str.strip()
    df['brand'] = df['Brand'].astype(str).str.strip().str.upper()

    df['year'] = df['Scraped_At'].dt.isocalendar().year.astype(int)
    df['week_num'] = df['Scraped_At'].dt.isocalendar().week.astype(int)
    df['period'] = df['week_num'].apply(lambda w: f'W{w}')

    agg = (
        df.groupby(['year', 'period', 'brand', 'sub_family', 'compressor'])
        .agg(avg_price_vat_ex=('price_vat_ex', 'mean'),
             total_qty=('price_vat_ex', 'count'))
        .reset_index()
    )
    agg['period_type'] = 'week'
    agg['source'] = 'price_tracking'

    print(f"  Price Tracking: {len(paths)}개 파일 → {len(agg)}개 세그먼트-주차 레코드")
    return agg


def _load_extra_2025() -> pd.DataFrame:
    """extra_2025.xlsx 로드 → (year, period[M1-M12], brand, sub_family, compressor='-', avg_price_vat_ex, total_qty)"""
    try:
        df = pd.read_excel(EXTRA_2025_PATH, engine='openpyxl')
    except Exception as e:
        print(f"  [경고] extra_2025.xlsx 읽기 실패: {e}")
        return pd.DataFrame()

    # AC 필터
    df = df[df['SUB FAMILY'].isin(AC_SUB_FAMILIES)].copy()
    df['Unit price'] = pd.to_numeric(df['Unit price'], errors='coerce')
    df = df[df['Unit price'] > 0]

    df['sub_family'] = df['SUB FAMILY'].apply(_norm_sf)
    df['brand'] = df['BRAND'].astype(str).str.strip().str.upper()

    # Month 컬럼: 'Jan', 'Feb' 등 → 월 번호
    month_map = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
    df['month_num'] = df['Month'].map(month_map)
    df = df.dropna(subset=['month_num'])
    df['period'] = df['month_num'].apply(lambda m: f'M{int(m)}')
    df['year'] = df['Year'].astype(int)

    agg = (
        df.groupby(['year', 'period', 'brand', 'sub_family'])
        .agg(avg_price_vat_ex=('Unit price', 'mean'),
             total_qty=('QTY SOLD', 'sum'))
        .reset_index()
    )
    agg['compressor'] = '-'
    agg['period_type'] = 'month'
    agg['source'] = 'sellout_2025'
    agg['total_qty'] = agg['total_qty'].fillna(0).astype(int)

    print(f"  extra_2025: {len(agg)}개 세그먼트-월 레코드")
    return agg


def _load_weekly_sellout_2026() -> pd.DataFrame:
    """week01-16.xlsx 로드 → (year, week, brand, sub_family, compressor='-', avg_price_vat_ex, total_qty)"""
    paths = sorted(glob.glob(WEEKLY_SELLOUT_GLOB))
    if not paths:
        print("  [경고] 주간 sell-out 파일 없음")
        return pd.DataFrame()

    frames = []
    for p in paths:
        try:
            df = pd.read_excel(p, engine='openpyxl')
            frames.append(df)
        except Exception as e:
            print(f"  [경고] {os.path.basename(p)} 읽기 실패: {e}")

    df = pd.concat(frames, ignore_index=True)

    # AC 필터
    df = df[df['Sub Family Description'].isin(AC_SUB_FAMILIES)].copy()
    df['Sale Quantity'] = pd.to_numeric(df['Sale Quantity'], errors='coerce').fillna(0)
    df['Sale Value'] = pd.to_numeric(df['Sale Value'], errors='coerce').fillna(0)
    df = df[df['Sale Quantity'] > 0]

    df['unit_price'] = df['Sale Value'] / df['Sale Quantity']
    df = df[df['unit_price'] > 0]

    df['Calendar Date'] = pd.to_datetime(df['Calendar Date'], errors='coerce')
    df = df.dropna(subset=['Calendar Date'])

    df['year'] = df['Calendar Date'].dt.isocalendar().year.astype(int)
    df['week_num'] = df['Calendar Date'].dt.isocalendar().week.astype(int)
    df['period'] = df['week_num'].apply(lambda w: f'W{w}')

    df['sub_family'] = df['Sub Family Description'].apply(_norm_sf)
    df['brand'] = df['Brand Description'].astype(str).str.strip().str.upper()

    agg = (
        df.groupby(['year', 'period', 'brand', 'sub_family'])
        .agg(avg_price_vat_ex=('unit_price', 'mean'),
             total_qty=('Sale Quantity', 'sum'))
        .reset_index()
    )
    agg['compressor'] = '-'
    agg['period_type'] = 'week'
    agg['source'] = 'sellout_2026'
    agg['total_qty'] = agg['total_qty'].astype(int)

    print(f"  weekly_sellout_2026: {len(paths)}개 파일 → {len(agg)}개 세그먼트-주차 레코드")
    return agg


def build_price_segments(db_path: str) -> int:
    print("가격 세그먼트 구축 시작...")

    df_pt = _load_price_tracking()
    df_25 = _load_extra_2025()
    df_26 = _load_weekly_sellout_2026()

    frames = [f for f in [df_pt, df_25, df_26] if len(f) > 0]
    if not frames:
        print("  [오류] 데이터 없음")
        return 0

    combined = pd.concat(frames, ignore_index=True)

    conn = sqlite3.connect(db_path)
    # 기존 데이터 삭제 후 재삽입
    conn.execute("DELETE FROM competitor_prices")

    inserted = 0
    for _, row in combined.iterrows():
        conn.execute(
            "INSERT OR REPLACE INTO competitor_prices "
            "(year, period, period_type, brand, sub_family, compressor, avg_price_vat_ex, total_qty, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(row['year']),
                row['period'],
                row['period_type'],
                row['brand'],
                row['sub_family'],
                row['compressor'],
                round(float(row['avg_price_vat_ex']), 2) if pd.notna(row['avg_price_vat_ex']) else None,
                int(row.get('total_qty', 0)),
                row['source'],
            )
        )
        inserted += 1

    conn.commit()
    conn.close()

    print(f"\n완료: {inserted}개 레코드 저장")
    return inserted


def get_price_context(db_path: str) -> dict:
    """
    현재 LG vs 경쟁사 가격 갭을 세그먼트별로 반환.
    가장 최신 week 데이터 기준.
    """
    conn = sqlite3.connect(db_path)

    # 가장 최신 price_tracking 주차
    latest = conn.execute(
        "SELECT period FROM competitor_prices "
        "WHERE source='price_tracking' AND period_type='week' "
        "ORDER BY year DESC, CAST(SUBSTR(period,2) AS INTEGER) DESC LIMIT 1"
    ).fetchone()

    if not latest:
        conn.close()
        return {}

    period = latest[0]
    rows = conn.execute(
        "SELECT brand, sub_family, compressor, avg_price_vat_ex, total_qty "
        "FROM competitor_prices "
        "WHERE source='price_tracking' AND period=? AND avg_price_vat_ex > 0",
        (period,)
    ).fetchall()
    conn.close()

    from collections import defaultdict
    # sub_family + compressor 기준으로 LG vs 경쟁사 구분
    lg_prices: dict = defaultdict(list)
    comp_prices: dict = defaultdict(list)

    for brand, sf, comp, price, qty in rows:
        seg = (sf, comp)
        if brand == 'LG':
            lg_prices[seg].append(price)
        else:
            comp_prices[seg].append(price)

    context = {}
    all_segs = set(lg_prices.keys()) | set(comp_prices.keys())
    for seg in sorted(all_segs):
        sf, comp = seg
        lg_avg = sum(lg_prices[seg]) / len(lg_prices[seg]) if lg_prices[seg] else None
        competitor_avg = sum(comp_prices[seg]) / len(comp_prices[seg]) if comp_prices[seg] else None
        gap_pct = None
        if lg_avg and competitor_avg and competitor_avg > 0:
            gap_pct = round((lg_avg / competitor_avg - 1) * 100, 1)
        key = f"{sf} | {comp}"
        context[key] = {
            'reference_period': period,
            'lg_avg_price_vat_ex': round(lg_avg, 1) if lg_avg else None,
            'competitor_avg_price_vat_ex': round(competitor_avg, 1) if competitor_avg else None,
            'lg_vs_competitor_gap_pct': gap_pct,
        }

    return context


def get_brand_price_context(db_path: str) -> dict:
    """
    경쟁사별 LG 대비 가격 갭을 세그먼트별로 반환.
    가장 최신 price_tracking 주차 기준.
    """
    from collections import defaultdict

    conn = sqlite3.connect(db_path)

    # 가장 최신 price_tracking 주차
    latest = conn.execute(
        "SELECT period FROM competitor_prices "
        "WHERE source='price_tracking' AND period_type='week' "
        "ORDER BY year DESC, CAST(SUBSTR(period,2) AS INTEGER) DESC LIMIT 1"
    ).fetchone()

    if not latest:
        conn.close()
        return {}

    period = latest[0]
    rows = conn.execute(
        "SELECT brand, sub_family, compressor, avg_price_vat_ex "
        "FROM competitor_prices "
        "WHERE source='price_tracking' AND period=? AND period_type='week' AND avg_price_vat_ex > 0",
        (period,)
    ).fetchall()
    conn.close()

    # 세그먼트별로 LG 가격 및 경쟁사별 가격 수집
    lg_prices: dict = defaultdict(list)
    brand_prices: dict = defaultdict(lambda: defaultdict(list))

    for brand, sf, comp, price in rows:
        seg = f"{sf} | {comp}"
        if brand == 'LG':
            lg_prices[seg].append(price)
        else:
            brand_prices[seg][brand].append(price)

    context = {}
    all_segs = set(lg_prices.keys()) | set(brand_prices.keys())
    for seg in sorted(all_segs):
        lg_avg = sum(lg_prices[seg]) / len(lg_prices[seg]) if lg_prices[seg] else None

        brands = {}
        for brand, prices in brand_prices[seg].items():
            brand_avg = sum(prices) / len(prices)
            gap_pct = round((lg_avg / brand_avg - 1) * 100, 1) if lg_avg and brand_avg > 0 else None
            brands[brand] = {
                'avg_price_vat_ex': round(brand_avg, 1),
                'gap_pct': gap_pct,
            }

        context[seg] = {
            'reference_period': period,
            'lg_avg_price_vat_ex': round(lg_avg, 1) if lg_avg else None,
            'brands': brands,
        }

    return context


def get_oos_signals(db_path: str) -> dict:
    """
    최근 1주차에 없지만 이전 3주 중 2주 이상 존재했던 경쟁사 브랜드를 OOS 신호로 반환.
    LG 제외.
    """
    from collections import defaultdict

    conn = sqlite3.connect(db_path)

    # 최근 4개 주차 (최신순)
    recent_weeks = conn.execute(
        "SELECT DISTINCT period FROM competitor_prices "
        "WHERE source='price_tracking' AND period_type='week' "
        "ORDER BY year DESC, CAST(SUBSTR(period,2) AS INTEGER) DESC LIMIT 4"
    ).fetchall()

    if len(recent_weeks) < 2:
        conn.close()
        return {}

    if len(recent_weeks) < 4:
        print(f"  [경고] OOS 감지: 최근 주차 데이터 {len(recent_weeks)}개뿐 (4개 권장)")

    latest = recent_weeks[0][0]
    prev_weeks = [w[0] for w in recent_weeks[1:]]

    # 최근 주차 데이터 (brand, segment)
    latest_rows = conn.execute(
        "SELECT DISTINCT brand, sub_family, compressor FROM competitor_prices "
        "WHERE source='price_tracking' AND period_type='week' AND period=? AND brand != 'LG'",
        (latest,)
    ).fetchall()
    latest_set = {(r[0], f"{r[1]} | {r[2]}") for r in latest_rows}

    # 이전 주차별 데이터
    prev_counts: dict = defaultdict(int)
    for week in prev_weeks:
        rows = conn.execute(
            "SELECT DISTINCT brand, sub_family, compressor FROM competitor_prices "
            "WHERE source='price_tracking' AND period_type='week' AND period=? AND brand != 'LG'",
            (week,)
        ).fetchall()
        for brand, sf, comp in rows:
            key = (brand, f"{sf} | {comp}")
            prev_counts[key] += 1

    conn.close()

    # OOS 신호: 이전 주차 2회 이상 등장 + 최신 주차 미등장
    oos: dict = defaultdict(list)
    for (brand, seg), count in prev_counts.items():
        if count >= 2 and (brand, seg) not in latest_set:
            oos[seg].append(brand)

    # 알파벳 정렬
    return {seg: sorted(brands) for seg, brands in sorted(oos.items())}


if __name__ == '__main__':
    db = str(BASE_DIR / 'data' / 'sellout.db')
    n = build_price_segments(db)

    print("\n=== 가격 갭 현황 ===")
    ctx = get_price_context(db)
    for seg, info in ctx.items():
        gap = info.get('lg_vs_competitor_gap_pct')
        gap_str = f"+{gap:.1f}%" if gap and gap > 0 else (f"{gap:.1f}%" if gap else "N/A")
        print(f"  {seg}: LG {info['lg_avg_price_vat_ex']} vs 경쟁사 {info['competitor_avg_price_vat_ex']} SAR (갭: {gap_str})")
