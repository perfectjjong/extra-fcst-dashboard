import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.build_price_segments import get_brand_price_context, get_oos_signals
from api.trends import get_trends_index

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
