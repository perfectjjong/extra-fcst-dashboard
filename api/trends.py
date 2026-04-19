import json
from datetime import datetime
from pathlib import Path

CACHE_PATH = Path(__file__).parent.parent / 'data' / 'trends_cache.json'
CACHE_HOURS = 24


def _week_str(iso_week):
    return f'W{iso_week}'


def get_trends_index(keyword: str = 'مكيف', geo: str = 'SA',
                     use_cache: bool = True) -> dict:
    """
    Google Trends 주간 검색량 지수 (0-100) 반환.
    캐시: data/trends_cache.json (24시간 유효)
    실패 시 빈 dict 반환 (시뮬레이터는 factor=1.0으로 처리)
    """
    if use_cache and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
            age_hours = (datetime.now().timestamp() - cached.get('fetched_at', 0)) / 3600
            if age_hours < CACHE_HOURS:
                return cached.get('data', {})
        except Exception:
            pass

    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl='ar-SA', tz=180, timeout=(10, 25))
        pt.build_payload([keyword], cat=0, timeframe='today 12-m', geo=geo)
        df = pt.interest_over_time()
        if df.empty:
            return {}

        result = {}
        for ts, row in df.iterrows():
            iso = ts.isocalendar()
            week_key = _week_str(iso.week)
            result[week_key] = int(row[keyword])

        cache_obj = {'fetched_at': datetime.now().timestamp(), 'data': result}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache_obj, ensure_ascii=False), encoding='utf-8')
        return result

    except Exception as e:
        print(f'[trends] 조회 실패: {e}')
        return {}
