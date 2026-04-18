import sqlite3, sys, os, pandas as pd, pytest
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db
from pipeline.parse_prices import parse_prices

def make_price_xlsx(tmp_path, filename="prices_260225.xlsx"):
    rows = [
        [datetime(2026,2,25,10,0), 'LG', 'LG 1.5T AC', 'AM182C', 'SKU1', 'Split Inverter', 'Cold', 1.5, 18000, 'Inverter', 3000.0, 2700.0, None, 300.0, 0.10, None, None, 0, 0, 0, '2 Years', '7 Years', 'In Stock', None, 'No', 2700.0, None],
        [datetime(2026,2,26,10,0), 'LG', 'LG 1.5T AC', 'AM182C', 'SKU1', 'Split Inverter', 'Cold', 1.5, 18000, 'Inverter', 3000.0, 2600.0, None, 400.0, 0.13, None, None, 0, 0, 0, '2 Years', '7 Years', 'In Stock', None, 'No', 2600.0, None],
        [datetime(2026,2,25,10,0), 'Samsung', 'Samsung AC', 'SAM01', 'SKU2', 'Split', 'Cold', 1.5, 18000, 'Inverter', 2800.0, 2500.0, None, 300.0, 0.11, None, None, 0, 0, 0, '2 Years', '5 Years', 'In Stock', None, 'No', 2500.0, None],
    ]
    cols = ['Scraped_At','Brand','Product_Name','Model_No','SKU','Category',
            'Cold_or_HC','Cooling_Capacity_Ton','BTU','Compressor_Type','Standard_Price',
            'Sale_Price','Jood_Gold_Price','Discount_Amount','Discount_Rate','Promo_Code',
            'Promo_Label','Offer_Count','Gift_Count','Gift_Value','Warranty_Period',
            'Compressor_Warranty','Stock_Status','Stock_Label','eXtra_Exclusive',
            'Final_Sale_Price','Final_Jood_Gold_Price']
    df = pd.DataFrame(rows, columns=cols)
    path = str(tmp_path / filename)
    # Write to "Prices DB" sheet to match actual file structure
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Prices DB', index=False)
    return path

def test_filters_lg_only(tmp_path):
    path = make_price_xlsx(tmp_path)
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    parse_prices([path], db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT model FROM price_weekly").fetchall()
    conn.close()
    models = [r[0] for r in rows]
    assert 'SAM01' not in models
    assert 'AM182C' in models

def test_weekly_aggregation(tmp_path):
    path = make_price_xlsx(tmp_path)
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    parse_prices([path], db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT avg_sale_price, avg_discount_rate FROM price_weekly WHERE model='AM182C'").fetchone()
    conn.close()
    assert abs(row[0] - 2650.0) < 1
    assert abs(row[1] - 0.115) < 0.01
