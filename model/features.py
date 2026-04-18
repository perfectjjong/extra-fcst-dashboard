import sqlite3
from typing import Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder


def load_sellout(db_path: str) -> Tuple[pd.DataFrame, LabelEncoder, LabelEncoder]:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        "SELECT year, week, model, category, qty, sellthru "
        "FROM weekly_sellout WHERE channel LIKE '%United%'",
        conn
    )
    season = pd.read_sql("SELECT week, ramadan_flag, summer_flag, holiday_flag FROM season_vars", conn)
    prices = pd.read_sql("SELECT year, week, model, avg_sale_price, avg_discount_rate FROM price_weekly", conn)
    conn.close()

    # Parse week number
    df['week_num'] = df['week'].str.replace('W', '').astype(int)
    # Sort
    df = df.sort_values(['model', 'year', 'week_num']).reset_index(drop=True)

    # Lag features per model
    df['qty_lag_1w'] = df.groupby('model')['qty'].shift(1).fillna(0)
    df['qty_lag_4w'] = df.groupby('model')['qty'].shift(4).fillna(0)
    df['qty_lag_52w'] = df.groupby('model')['qty'].shift(52).fillna(0)
    df['qty_rollmean_4w'] = df.groupby('model')['qty'].transform(
        lambda x: x.shift(1).rolling(4, min_periods=1).mean()
    ).fillna(0)

    # Week of year
    df['week_of_year'] = df['week_num']

    # Season vars — join on composite key: "{year}-W{n}"
    df['season_key'] = df['year'].astype(str) + '-' + df['week']
    season = season.rename(columns={'week': 'season_key'})
    df = df.merge(season, on='season_key', how='left')
    df[['ramadan_flag', 'summer_flag', 'holiday_flag']] = (
        df[['ramadan_flag', 'summer_flag', 'holiday_flag']].fillna(0).astype(int)
    )

    # Price features
    df = df.merge(prices, on=['year', 'week', 'model'], how='left')
    df['lg_discount_rate'] = df['avg_discount_rate'].fillna(0.0)
    df['competitor_min_price'] = 0.0  # future: join competitor prices

    # Label encode
    cat_enc = LabelEncoder()
    model_enc = LabelEncoder()
    df['category_enc'] = cat_enc.fit_transform(df['category'].astype(str))
    df['model_enc'] = model_enc.fit_transform(df['model'].astype(str))

    return df, cat_enc, model_enc


FEATURE_COLS = [
    'qty_lag_1w', 'qty_lag_4w', 'qty_lag_52w', 'qty_rollmean_4w',
    'week_of_year', 'ramadan_flag', 'summer_flag', 'holiday_flag',
    'lg_discount_rate', 'competitor_min_price',
    'category_enc', 'model_enc',
]
TARGET_COL = 'qty'
