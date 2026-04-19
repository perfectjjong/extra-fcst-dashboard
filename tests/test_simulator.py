import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.build_price_segments import get_brand_price_context, get_oos_signals
from api.trends import get_trends_index
from api.simulator import SimulationEngine, _seg_key

DB_PATH = str(Path(__file__).parent.parent / 'data' / 'sellout.db')


def test_brand_price_context_structure():
    ctx = get_brand_price_context(DB_PATH)
    assert isinstance(ctx, dict) and len(ctx) > 0
    first = next(iter(ctx.values()))
    assert 'reference_period' in first
    assert 'lg_avg_price_vat_ex' in first
    assert 'brands' in first


def test_brand_price_context_gap_pct():
    ctx = get_brand_price_context(DB_PATH)
    for seg, data in ctx.items():
        for brand, info in data['brands'].items():
            assert 'avg_price_vat_ex' in info
            assert 'gap_pct' in info


def test_oos_signals_structure():
    signals = get_oos_signals(DB_PATH)
    assert isinstance(signals, dict)
    for seg, brands in signals.items():
        assert isinstance(brands, list)


def test_trends_index_structure():
    """Google Trends 결과가 주차별 딕셔너리로 반환되어야 한다."""
    result = get_trends_index(use_cache=False)
    # 네트워크 없을 수 있으니 빈 dict도 허용
    assert isinstance(result, dict)
    for week, val in result.items():
        assert week.startswith('W')
        assert 0 <= val <= 100


# ── SimulationEngine 테스트 ──────────────────────────────────────────

FORECASTS = [
    {'model': 'AM182C0N2O', 'week': 'W1', 'predicted': 350,
     'category': 'Mini Split', 'level': 'L1_sku', 'ci_low': 280, 'ci_high': 420},
    {'model': 'W181EC.SN0', 'week': 'W1', 'predicted': 120,
     'category': 'Window', 'level': 'L3_coldstart', 'ci_low': 72, 'ci_high': 168},
]

PRICE_GAPS = {
    'Mini Split AC | Inverter': {
        'reference_period': 'W11', 'lg_avg_price_vat_ex': 2312.0,
        'brands': {
            'MIDEA': {'avg_price_vat_ex': 2158.0, 'gap_pct': 7.1},
            'CLASS PRO': {'avg_price_vat_ex': 1728.0, 'gap_pct': 33.8},
        }
    },
    'Window AC | Inverter': {
        'reference_period': 'W11', 'lg_avg_price_vat_ex': 1450.0,
        'brands': {'MIDEA': {'avg_price_vat_ex': 1290.0, 'gap_pct': 12.4}}
    },
}

BASE_PARAMS = {
    'price_positioning': {},
    'promo_periods': [],
    'external_vars': {
        'temp_scenario': 'normal',
        'humidity_scenario': 'normal',
        'oil_price_usd': 75,
        'electricity_burden': True,
        'oos_brands': {},
    },
    'scope': {'week_from': 1, 'week_to': 52, 'categories': []},
    'trends_index': {},
}


def test_no_change_returns_base():
    engine = SimulationEngine()
    results = engine.simulate(FORECASTS, BASE_PARAMS, PRICE_GAPS)
    assert len(results) == 2
    for r in results:
        assert abs(r['adjusted'] - r['predicted']) <= 1


def test_price_gap_reduction_boosts_demand():
    engine = SimulationEngine()
    params = {**BASE_PARAMS, 'price_positioning': {
        'Mini Split AC|Inverter': {'vs_CLASS PRO': 10}
    }}
    results = engine.simulate(FORECASTS, params, PRICE_GAPS)
    ms = next(r for r in results if r['model'] == 'AM182C0N2O')
    assert ms['adjusted'] > ms['predicted']
    assert ms['factors']['price'] > 1.0


def test_oil_above_baseline_boosts_demand():
    engine = SimulationEngine()
    params = {**BASE_PARAMS, 'external_vars': {**BASE_PARAMS['external_vars'], 'oil_price_usd': 100}}
    results = engine.simulate(FORECASTS, params, PRICE_GAPS)
    assert all(r['factors']['oil'] > 1.0 for r in results)


def test_eid_week_boosts_demand():
    engine = SimulationEngine()
    eid_f = [{'model': 'AM182C0N2O', 'week': 'W22', 'predicted': 300,
              'category': 'Mini Split', 'level': 'L1_sku', 'ci_low': 240, 'ci_high': 360}]
    results = engine.simulate(eid_f, BASE_PARAMS, PRICE_GAPS)
    assert results[0]['factors']['islamic_event'] > 1.0


def test_electricity_burden_boosts_inverter():
    engine = SimulationEngine()
    w31_f = [{'model': 'AM182C0N2O', 'week': 'W31', 'predicted': 300,
              'category': 'Mini Split', 'level': 'L1_sku', 'ci_low': 240, 'ci_high': 360}]
    results = engine.simulate(w31_f, BASE_PARAMS, PRICE_GAPS)
    assert results[0]['factors']['electricity'] > 1.0


def test_oos_boosts_lg_demand():
    engine = SimulationEngine()
    params = {**BASE_PARAMS, 'external_vars': {
        **BASE_PARAMS['external_vars'],
        'oos_brands': {'Mini Split AC | Inverter': ['MIDEA', 'CLASS PRO']}
    }}
    results = engine.simulate(FORECASTS, params, PRICE_GAPS)
    ms = next(r for r in results if r['model'] == 'AM182C0N2O')
    assert ms['factors']['oos'] > 1.0


def test_promo_hangover():
    f = [{'model': 'AM182C0N2O', 'week': 'W35', 'predicted': 300,
          'category': 'Mini Split', 'level': 'L1_sku', 'ci_low': 240, 'ci_high': 360}]
    engine = SimulationEngine()
    params = {**BASE_PARAMS, 'promo_periods': [{
        'start_week': 30, 'end_week': 34, 'segment': 'Mini Split AC|Inverter',
        'competitor': 'CLASS PRO', 'target_gap_pct': 8,
        'current_gap_pct': 33.8, 'hangover_weeks': 2,
    }]}
    results = engine.simulate(f, params, PRICE_GAPS)
    assert results[0]['is_hangover'] is True
    assert results[0]['adjusted'] < results[0]['predicted']


def test_result_fields():
    engine = SimulationEngine()
    results = engine.simulate(FORECASTS, BASE_PARAMS, PRICE_GAPS)
    required = {'model', 'week', 'predicted', 'adjusted', 'delta_pct',
                'factors', 'is_promo_week', 'is_hangover'}
    factor_required = {'price', 'heat_index', 'islamic_event', 'oil',
                       'electricity', 'oos', 'promo', 'trends'}
    for r in results:
        assert required.issubset(r.keys())
        assert factor_required.issubset(r['factors'].keys())


def test_seg_key():
    assert _seg_key('AM182C0N2O') == 'Mini Split AC|Inverter'
    assert _seg_key('W181EC.SN0') == 'Window AC|Inverter'
    assert _seg_key('APNQ55GT3MA') == 'Free Standing AC|Inverter'
    assert _seg_key('UT182CE') == 'Cassette AC|Inverter'
