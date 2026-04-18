import sqlite3, sys, os, pytest, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db
from pipeline.parse_excel import parse_excel, EXTRA_CHANNEL_PATTERN

def make_test_excel(tmp_path):
    rows = [
        ['=B2', 'United Electronics Company', 'IR', 'Jan', 1, 2026, 'W01', 'AM182C', 'Split Inverter', 50, 'Riyadh', '', 'AM182C', 40, 10],
        ['=B3', 'United Electronics Company', 'IR', 'Jan', 1, 2026, 'W01', 'AM242C', 'Split Inverter', 30, 'Riyadh', '', 'AM242C', 25, 5],
        ['=B4', 'Other Channel', 'IR', 'Jan', 1, 2026, 'W01', 'AM182C', 'Split Inverter', 20, 'Riyadh', '', 'AM182C', 15, 5],
    ]
    cols = ['Channel_Formula','Channel','Or_IR','Month','Day','Year','Week',
            'Dealer Channel Models','Category','Sell out Qty','Region',
            'Petname','Model Mapping','Sell Thru Qty','Ch. Stock']
    df = pd.DataFrame(rows, columns=cols)
    path = str(tmp_path / "test_sellout.xlsx")
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='for Bi RAW_Weekly Sell out', index=False)
    return path

def test_filters_extra_only(tmp_path):
    excel_path = make_test_excel(tmp_path)
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    parse_excel(excel_path, db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT channel, model, qty FROM weekly_sellout ORDER BY model").fetchall()
    conn.close()
    assert len(rows) == 2
    assert all('United' in r[0] for r in rows)

def test_aggregates_duplicate_weeks(tmp_path):
    rows = [
        ['=B2', 'United Electronics Company', 'IR', 'Jan', 1, 2026, 'W01', 'AM182C', 'Split Inverter', 30, 'Riyadh', '', 'AM182C', 20, 10],
        ['=B3', 'United Electronics Company', 'IR', 'Jan', 8, 2026, 'W01', 'AM182C', 'Split Inverter', 20, 'Jeddah', '', 'AM182C', 15, 5],
    ]
    cols = ['Channel_Formula','Channel','Or_IR','Month','Day','Year','Week',
            'Dealer Channel Models','Category','Sell out Qty','Region',
            'Petname','Model Mapping','Sell Thru Qty','Ch. Stock']
    df = pd.DataFrame(rows, columns=cols)
    path = str(tmp_path / "dup.xlsx")
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='for Bi RAW_Weekly Sell out', index=False)
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    parse_excel(path, db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT qty FROM weekly_sellout WHERE model='AM182C' AND week='W01'").fetchone()
    conn.close()
    assert row[0] == 50

def test_extra_channel_pattern():
    assert EXTRA_CHANNEL_PATTERN in 'United Electronics Company'
