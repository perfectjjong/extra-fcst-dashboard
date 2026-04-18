import os
import pickle
import sqlite3

import pandas as pd
from prophet import Prophet


def train_prophet_total(db_path: str, models_dir: str) -> float:
    """Train Prophet on total eXtra weekly sell-out. Returns next-week prediction."""
    os.makedirs(models_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        "SELECT year, week, SUM(qty) as qty FROM weekly_sellout "
        "WHERE channel LIKE '%United%' GROUP BY year, week ORDER BY year, week",
        conn
    )
    conn.close()

    if len(df) < 10:
        return None

    # week = 'W1', 'W17' → extract number, build ISO date
    # %Y-%W requires a weekday; append '-1' (Monday) to anchor the week
    df['week_num'] = df['week'].str.replace('W', '').astype(int)
    df['ds'] = pd.to_datetime(
        df['year'].astype(str) + '-' + df['week_num'].astype(str).str.zfill(2) + '-1',
        format='%Y-%W-%w',
        errors='coerce'
    )
    df = df.dropna(subset=['ds'])
    df = df.rename(columns={'qty': 'y'})[['ds', 'y']]
    df = df.sort_values('ds').reset_index(drop=True)

    m = Prophet(
        weekly_seasonality=False,
        yearly_seasonality=False,
        daily_seasonality=False,
        seasonality_mode='additive',
        changepoint_prior_scale=0.05,
    )
    m.fit(df)

    future = m.make_future_dataframe(periods=1, freq='W')
    forecast = m.predict(future)
    next_pred = forecast.iloc[-1]['yhat']

    # Fallback: if Prophet predicts nonsensical value, use recent 4-week average
    recent_avg = float(df['y'].tail(4).mean())
    if next_pred <= 0 or next_pred > recent_avg * 5:
        next_pred = recent_avg

    next_pred = max(0, next_pred)

    with open(os.path.join(models_dir, 'prophet_total.pkl'), 'wb') as f:
        pickle.dump(m, f)

    return float(next_pred)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    models_dir = os.path.join(os.path.dirname(__file__), 'models')
    pred = train_prophet_total(db, models_dir)
    print(f"Prophet total forecast: {pred:.1f} units")
