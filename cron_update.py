#!/usr/bin/env python3
"""
Weekly automation: 매주 월요일 실행.
Pipeline:
  1. OR 주간 파일 → DB 적재
  2. 가격 파일 파싱
  3. season_vars 재구성
  4. 이전 주 FCST 정확도 계산 (fcst_snapshots → fcst_accuracy_log)
  5. LightGBM 재학습
  6. W52 전체 예측 생성
  7. 다음 주 스냅샷 저장
  8. Ensemble + fcst_output.json 생성
  9. dashboard_data.json 생성
  10. Git push (GitHub Pages 배포)
"""
import glob
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

OR_WEEKLY_GLOB = (
    "/home/ubuntu/2026/10. Automation/01. Sell Out Dashboard/"
    "00. OR/00. Raw/00. eXtra/00. Weekly Sell out/week*.xlsx"
)
PRICE_GLOB = (
    "/home/ubuntu/2026/06. Price Tracking/00. eXtra/00. Raw/"
    "extra_ac_Prices_Tracking_Master_*.xlsx"
)
DB_PATH = os.path.join(BASE_DIR, 'data', 'sellout.db')
MODELS_DIR = os.path.join(BASE_DIR, 'model', 'models')
FCST_OUTPUT = os.path.join(BASE_DIR, 'dashboard', 'fcst_output.json')
DASHBOARD_DATA = os.path.join(BASE_DIR, 'dashboard', 'dashboard_data.json')
MAPE_RETRAIN_THRESHOLD = 0.30

OR_PIPELINE_PATH = (
    '/home/ubuntu/2026/10. Automation/01. Sell Out Dashboard/'
    '00. OR/01. Python Code'
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'data', 'cron.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


def get_latest_actual_week() -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT week FROM weekly_sellout WHERE year=2026 "
        "ORDER BY CAST(SUBSTR(week,2) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row[0] if row else None


def load_or_data(or_files: list) -> int:
    """OR 주간 eXtra 파일을 직접 파싱해 DB에 적재. Returns: upserted row 수."""
    import re
    import openpyxl
    sys.path.insert(0, OR_PIPELINE_PATH)

    # 모델 매핑 로드 (있으면 사용, 없으면 raw model 그대로)
    try:
        from or_unified_dashboard_generator import UNIFIED_MAP, MODEL_INFO, normalize_sku, is_lg_ac
        has_map = True
    except Exception:
        has_map = False

    LG_BRANDS = {'LG', 'LG ELECTRONICS', 'LG전자'}
    AC_FAMILIES = {'AC', 'AIR CONDITIONER', 'ROOM AIR CONDITIONER', 'AIR CON', 'SPLIT AC', 'WINDOW AC'}

    conn = sqlite3.connect(DB_PATH)
    count = 0

    for fpath in or_files:
        fname = os.path.basename(fpath)
        m = re.search(r'week(\d+)', fname, re.IGNORECASE)
        if not m:
            continue
        week_num = int(m.group(1))
        week_label = f'W{week_num}'

        try:
            wb = openpyxl.load_workbook(fpath, read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            wb.close()
        except Exception as e:
            log.warning(f"  파일 읽기 실패 {fname}: {e}")
            continue

        for row in rows:
            if not row or len(row) < 12:
                continue
            country = str(row[0]).strip() if row[0] else ''
            if country != 'SA':
                continue
            brand    = str(row[10]).strip().upper() if len(row) > 10 and row[10] else ''
            family   = str(row[8]).strip().upper()  if row[8] else ''
            subfam   = str(row[9]).strip().upper()  if row[9] else ''
            model_raw = str(row[4]).strip()         if row[4] else ''
            qty_raw  = row[11] if len(row) > 11 else 0

            # LG AC 여부 필터
            if has_map:
                if not is_lg_ac(brand, family, subfam, model_raw):
                    continue
            else:
                if brand not in LG_BRANDS:
                    continue
                if not any(fam in family or fam in subfam for fam in AC_FAMILIES):
                    continue

            try:
                qty = int(float(qty_raw))
            except (ValueError, TypeError):
                continue
            if qty == 0:
                continue

            # 모델 정규화 + 카테고리
            category = ''
            if has_map:
                model_norm = normalize_sku(model_raw.replace(' ', ''))
                info = (UNIFIED_MAP.get(('eXtra', model_raw))
                        or UNIFIED_MAP.get(('eXtra', model_norm))
                        or MODEL_INFO.get(model_norm)
                        or MODEL_INFO.get(model_raw))
                if info and info.get('excluded'):
                    continue
                if info:
                    model_raw = info['unified']
                    category  = info.get('category', '')
            else:
                category = subfam or family

            conn.execute(
                "INSERT OR REPLACE INTO weekly_sellout "
                "(channel, year, week, model, category, qty) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ('United Electronics Company الشركة ا',
                 2026, week_label, model_raw, category, float(qty))
            )
            count += 1

    conn.commit()
    conn.close()
    return count


def run():
    log.info("=== cron_update.py START ===")
    start = datetime.now()

    from pipeline.init_db import init_db
    log.info("Ensuring DB schema...")
    init_db(DB_PATH)

    # Step 1: OR 주간 파일 적재
    or_files = sorted(glob.glob(OR_WEEKLY_GLOB))
    log.info(f"OR 주간 파일 {len(or_files)}개 발견")
    if not or_files:
        log.warning(f"OR 파일 없음: {OR_WEEKLY_GLOB}")
    else:
        try:
            n = load_or_data(or_files)
            log.info(f"  {n} rows upserted via OR 파이프라인")
        except ImportError as e:
            log.error(f"or_unified_dashboard_generator import 실패: {e}")
            log.error("수동으로 DB를 업데이트하세요.")
            sys.exit(1)

    # Step 2: 가격 파일
    price_files = glob.glob(PRICE_GLOB)
    log.info(f"가격 파일 {len(price_files)}개 파싱...")
    from pipeline.parse_prices import parse_prices
    n = parse_prices(price_files, DB_PATH)
    log.info(f"  {n} rows upserted into price_weekly")

    # Step 3: Season vars
    from pipeline.build_season_vars import build_season_vars
    log.info("Season vars 재구성...")
    n = build_season_vars(DB_PATH)
    log.info(f"  {n} rows")

    # Step 4: 이전 주 정확도 계산
    latest_week = get_latest_actual_week()
    if latest_week:
        from pipeline.fcst_snapshot import compute_accuracy
        log.info(f"정확도 계산: {latest_week}...")
        retrain = compute_accuracy(DB_PATH, week=latest_week, mape_threshold=MAPE_RETRAIN_THRESHOLD)
        if retrain:
            log.warning(f"  MAPE > {MAPE_RETRAIN_THRESHOLD:.0%} — 재학습 트리거됨")
        else:
            log.info(f"  MAPE OK (threshold {MAPE_RETRAIN_THRESHOLD:.0%} 이하)")

    # Step 5: LightGBM 재학습
    from model.train_lgbm import train_and_predict
    log.info("LightGBM 재학습...")
    lgbm_results = train_and_predict(DB_PATH, MODELS_DIR)
    log.info(f"  {len(lgbm_results)} 단기 예측 생성")

    # Step 6: W52 다중 스텝 예측
    latest_week = get_latest_actual_week()
    start_w = int(latest_week.replace('W', '')) + 1 if latest_week else 17
    remaining = max(1, 52 - start_w + 1)
    from model.train_lgbm import forecast_multistep
    log.info(f"다중 스텝 예측: W{start_w} ~ W52 ({remaining}주)...")
    multistep = forecast_multistep(DB_PATH, MODELS_DIR, start_week_num=start_w, n_weeks=remaining)
    log.info(f"  {len(multistep)} rows 생성")

    # Step 7: 다음 주 스냅샷 저장
    next_week = f'W{start_w}'
    from pipeline.fcst_snapshot import save_snapshot
    n = save_snapshot(DB_PATH, lgbm_results, week=next_week)
    log.info(f"스냅샷 저장: {n}개 모델 for {next_week}")

    # Step 8: Ensemble + fcst_output.json
    from model.ensemble import build_fcst_output
    log.info("Ensemble 출력 생성...")
    build_fcst_output(lgbm_results, DB_PATH, FCST_OUTPUT, multistep_results=multistep)
    log.info(f"  Written: {FCST_OUTPUT}")

    # Step 9: dashboard_data.json
    log.info("dashboard_data.json 생성...")
    from pipeline.generate_dashboard_data import main as gen_dashboard
    gen_dashboard()
    log.info(f"  Written: {DASHBOARD_DATA}")

    # Step 10: Git push
    log.info("GitHub Pages 배포...")
    today_str = datetime.now().strftime("%Y-%m-%d")
    os.system(
        f'cd "{BASE_DIR}" && '
        f'git add dashboard/fcst_output.json dashboard/dashboard_data.json && '
        f'git commit -m "auto: weekly update {today_str}" && '
        f'git push origin master:main'
    )

    elapsed = (datetime.now() - start).total_seconds()
    log.info(f"=== cron_update.py DONE in {elapsed:.1f}s ===")


if __name__ == "__main__":
    run()
