import os
import pickle
import sys
from pathlib import Path
from typing import Dict, List

import lightgbm as lgb
import numpy as np
import pandas as pd

from model.features import FEATURE_COLS, TARGET_COL, load_sellout

MIN_WEEKS_LEVEL1 = 26
HOLDOUT_WEEKS = 12
FORECAST_HORIZON = 8

# 단종 모델: 2026년 FCST 생성 제외 (역사 데이터는 학습용으로 유지)
DISCONTINUED_MODELS = {
    # NW 계열 → NS로 대체
    'NW182C', 'NW182H', 'NW242C', 'NW242H',
    # NV 계열 → NS로 대체
    'NV182C', 'NV182H', 'NV242C', 'NV242H',
    # NF 18/24 계열 → ND로 대체 (NF122 계열은 유지)
    'NF182C', 'NF182H', 'NF242C', 'NF242H',
    # Window SN2 계열 단종
    'C182EC.SN2', 'C182EH.SN2', 'C242EC.SN2', 'C242EH.SN2',
}


def _train_lgbm(X_train, y_train, quantile=None):
    params = {
        'n_estimators': 200,
        'learning_rate': 0.05,
        'num_leaves': 31,
        'min_child_samples': 5,
        'random_state': 42,
        'verbose': -1,
    }
    if quantile is not None:
        params['objective'] = 'quantile'
        params['alpha'] = quantile
    else:
        params['objective'] = 'regression'
    model = lgb.LGBMRegressor(**params)
    model.fit(X_train, y_train)
    return model


def _compute_mape(actual, predicted):
    mask = actual > 0
    if mask.sum() == 0:
        return float('nan')
    return float(np.mean(np.abs(actual[mask] - predicted[mask]) / actual[mask]))


def train_and_predict(db_path: str, models_dir: str) -> List[Dict]:
    os.makedirs(models_dir, exist_ok=True)
    df, cat_enc, model_enc = load_sellout(db_path)

    results = []
    model_week_counts = df.groupby('model')['week'].count()
    # 단종 모델 제외
    active_counts = model_week_counts[~model_week_counts.index.isin(DISCONTINUED_MODELS)]
    l1_models = active_counts[active_counts >= MIN_WEEKS_LEVEL1].index.tolist()
    l2_skus = active_counts[active_counts < MIN_WEEKS_LEVEL1].index.tolist()

    # --- Level 1: per-SKU ---
    for sku in l1_models:
        sub = df[df['model'] == sku].copy()
        if len(sub) < HOLDOUT_WEEKS + 4:
            continue
        train = sub.iloc[:-HOLDOUT_WEEKS]
        X_train = train[FEATURE_COLS].values
        y_train = train[TARGET_COL].values

        mdl = _train_lgbm(X_train, y_train)
        mdl_q10 = _train_lgbm(X_train, y_train, quantile=0.1)
        mdl_q90 = _train_lgbm(X_train, y_train, quantile=0.9)

        safe_sku = sku.replace('/', '_').replace(' ', '_')
        with open(os.path.join(models_dir, f'lgbm_L1_{safe_sku}.pkl'), 'wb') as f:
            pickle.dump({'point': mdl, 'q10': mdl_q10, 'q90': mdl_q90}, f)

        last_row = sub.iloc[[-1]][FEATURE_COLS].values
        pred = max(0, mdl.predict(last_row)[0])
        ci_low = max(0, mdl_q10.predict(last_row)[0])
        ci_high = max(0, mdl_q90.predict(last_row)[0])

        holdout = sub.iloc[-HOLDOUT_WEEKS:]
        mape = _compute_mape(holdout[TARGET_COL].values, mdl.predict(holdout[FEATURE_COLS].values))

        results.append({
            'model': sku,
            'category': sub['category'].iloc[-1],
            'level': 'L1_sku',
            'week': 'NEXT',
            'predicted': round(pred, 1),
            'ci_low': round(ci_low, 1),
            'ci_high': round(ci_high, 1),
            'mape': round(mape, 4) if not np.isnan(mape) else None,
        })

    # --- Level 2: per-category (for SKUs with < MIN_WEEKS_LEVEL1) ---
    if l2_skus:
        cat_groups = df[df['model'].isin(l2_skus)].groupby('category')
        for cat, cat_df in cat_groups:
            cat_agg = (
                cat_df.groupby(['year', 'week', 'week_of_year', 'ramadan_flag', 'summer_flag', 'holiday_flag'])
                .agg(qty=('qty', 'sum'))
                .reset_index()
            )
            if len(cat_agg) < HOLDOUT_WEEKS + 4:
                # Fallback to L3 for these SKUs
                for sku in [s for s in l2_skus if not df[df['model'] == s].empty and df[df['model'] == s]['category'].iloc[0] == cat]:
                    sku_avg = df[df['model'] == sku]['qty'].mean()
                    results.append({
                        'model': sku,
                        'category': cat,
                        'level': 'L3_total',
                        'week': 'NEXT',
                        'predicted': round(float(sku_avg), 1),
                        'ci_low': 0.0,
                        'ci_high': round(float(sku_avg) * 2, 1),
                        'mape': None,
                    })
                continue

            cat_agg = cat_agg.sort_values(['year', 'week_of_year']).reset_index(drop=True)
            cat_agg['qty_lag_1w'] = cat_agg['qty'].shift(1).fillna(0)
            cat_agg['qty_lag_4w'] = cat_agg['qty'].shift(4).fillna(0)
            cat_agg['qty_lag_52w'] = cat_agg['qty'].shift(52).fillna(0)
            cat_agg['qty_rollmean_4w'] = cat_agg['qty'].shift(1).rolling(4, min_periods=1).mean().fillna(0)
            cat_agg['lg_discount_rate'] = 0.0
            cat_agg['competitor_min_price'] = 0.0
            cat_agg['category_enc'] = cat_enc.transform([cat])[0] if cat in cat_enc.classes_ else 0
            cat_agg['model_enc'] = 0

            feat_cols = [c for c in FEATURE_COLS if c in cat_agg.columns]
            train = cat_agg.iloc[:-HOLDOUT_WEEKS]
            mdl = _train_lgbm(train[feat_cols].values, train['qty'].values)
            mdl_q10 = _train_lgbm(train[feat_cols].values, train['qty'].values, quantile=0.1)
            mdl_q90 = _train_lgbm(train[feat_cols].values, train['qty'].values, quantile=0.9)

            last_row = cat_agg.iloc[[-1]][feat_cols].values
            pred = max(0, mdl.predict(last_row)[0])
            ci_low = max(0, mdl_q10.predict(last_row)[0])
            ci_high = max(0, mdl_q90.predict(last_row)[0])

            skus_in_cat = [
                s for s in l2_skus
                if not df[df['model'] == s].empty and df[df['model'] == s]['category'].iloc[0] == cat
            ]
            sku_avgs = {s: df[df['model'] == s]['qty'].mean() for s in skus_in_cat}
            cat_total = sum(sku_avgs.values()) or 1

            for sku in skus_in_cat:
                share = sku_avgs[sku] / cat_total
                results.append({
                    'model': sku,
                    'category': cat,
                    'level': 'L2_category',
                    'week': 'NEXT',
                    'predicted': round(pred * share, 1),
                    'ci_low': round(ci_low * share, 1),
                    'ci_high': round(ci_high * share, 1),
                    'mape': None,
                })

    return results


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from model.features import FEATURE_COLS, TARGET_COL, load_sellout  # noqa: F811
    db = os.path.join(os.path.dirname(__file__), '..', 'data', 'sellout.db')
    models_dir = os.path.join(os.path.dirname(__file__), 'models')
    results = train_and_predict(db, models_dir)
    print(f"Generated {len(results)} forecasts")
    l1 = [r for r in results if r['level'] == 'L1_sku']
    l2 = [r for r in results if r['level'] == 'L2_category']
    l3 = [r for r in results if r['level'] == 'L3_total']
    print(f"  L1 (SKU-level): {len(l1)}")
    print(f"  L2 (category):  {len(l2)}")
    print(f"  L3 (total):     {len(l3)}")
    if l1:
        mapes = [r['mape'] for r in l1 if r['mape'] is not None]
        if mapes:
            print(f"  Mean L1 MAPE: {np.mean(mapes):.1%}")
