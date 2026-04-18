#!/usr/bin/env python3
"""
Weekly automation: runs every Monday.
Pipeline: parse Excel → parse prices → rebuild season vars → retrain → generate fcst_output.json
"""
import glob
import logging
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'data', 'cron.log')),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

EXCEL_PATH = "/home/ubuntu/2026/B2C Dealer Sell out FCST_2025_Actual_W17_rev_재작업.xlsx"
PRICE_GLOB = "/home/ubuntu/2026/06. Price Tracking/00. eXtra/00. Raw/extra_ac_Prices_Tracking_Master_*.xlsx"
DB_PATH = os.path.join(BASE_DIR, 'data', 'sellout.db')
MODELS_DIR = os.path.join(BASE_DIR, 'model', 'models')
FCST_OUTPUT = os.path.join(BASE_DIR, 'dashboard', 'fcst_output.json')


def run():
    log.info("=== cron_update.py START ===")
    start = datetime.now()

    from pipeline.init_db import init_db
    log.info("Ensuring DB schema...")
    init_db(DB_PATH)

    from pipeline.parse_excel import parse_excel
    log.info("Parsing Excel sell-out data...")
    n = parse_excel(EXCEL_PATH, DB_PATH)
    log.info(f"  {n} rows upserted into weekly_sellout")

    from pipeline.parse_prices import parse_prices
    price_files = glob.glob(PRICE_GLOB)
    log.info(f"Parsing {len(price_files)} price files...")
    n = parse_prices(price_files, DB_PATH)
    log.info(f"  {n} rows upserted into price_weekly")

    from pipeline.build_season_vars import build_season_vars
    log.info("Rebuilding season vars...")
    n = build_season_vars(DB_PATH)
    log.info(f"  {n} season_vars rows")

    from model.train_lgbm import train_and_predict
    log.info("Training LightGBM (hierarchical)...")
    lgbm_results = train_and_predict(DB_PATH, MODELS_DIR)
    log.info(f"  {len(lgbm_results)} forecasts generated")

    from model.ensemble import build_fcst_output
    log.info("Building ensemble + writing fcst_output.json...")
    build_fcst_output(lgbm_results, DB_PATH, FCST_OUTPUT)
    log.info(f"  Written: {FCST_OUTPUT}")

    elapsed = (datetime.now() - start).total_seconds()
    log.info(f"=== cron_update.py DONE in {elapsed:.1f}s ===")


if __name__ == "__main__":
    run()
