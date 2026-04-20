import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from model.ensemble import SEGMENT_ELASTICITY, _DEFAULT_ELASTICITY, _get_model_segment

# ── Riyadh 주간 평균 기온 (°C) ─────────────────────────────────────
RIYADH_TEMP = {
    'W1':15,'W2':15,'W3':16,'W4':16,'W5':17,'W6':18,'W7':19,'W8':20,
    'W9':21,'W10':23,'W11':25,'W12':27,'W13':28,'W14':30,'W15':32,'W16':34,
    'W17':36,'W18':38,'W19':39,'W20':40,'W21':41,'W22':42,'W23':43,'W24':44,
    'W25':44,'W26':45,'W27':45,'W28':45,'W29':44,'W30':43,'W31':42,'W32':40,
    'W33':38,'W34':36,'W35':34,'W36':32,'W37':30,'W38':28,'W39':26,'W40':24,
    'W41':22,'W42':20,'W43':19,'W44':18,'W45':17,'W46':16,'W47':15,'W48':15,
    'W49':15,'W50':14,'W51':14,'W52':15,
}

# 리야드 주간 평균 습도 (%)
RIYADH_HUMIDITY = {
    'W1':44,'W2':42,'W3':40,'W4':38,'W5':36,'W6':34,'W7':32,'W8':30,
    'W9':28,'W10':26,'W11':24,'W12':22,'W13':20,'W14':18,'W15':17,'W16':16,
    'W17':15,'W18':14,'W19':13,'W20':13,'W21':12,'W22':12,'W23':11,'W24':11,
    'W25':11,'W26':12,'W27':12,'W28':13,'W29':14,'W30':15,'W31':16,'W32':17,
    'W33':18,'W34':20,'W35':22,'W36':24,'W37':26,'W38':28,'W39':30,'W40':32,
    'W41':34,'W42':36,'W43':38,'W44':40,'W45':42,'W46':43,'W47':44,'W48':44,
    'W49':44,'W50':44,'W51':44,'W52':44,
}

TEMP_SENSITIVITY = {
    'Mini Split AC': 0.025,
    'Window AC': 0.030,
    'Free Standing AC': 0.015,
    'Cassette AC': 0.010,
}

# 2026 이슬람 이벤트 주차별 수요 부스트
ISLAMIC_BOOSTS = {
    8:  1.10,   # Founding Day W8
    12: 1.30,   # Eid Al-Fitr W12
    13: 1.20,   # Eid Al-Fitr W13
    22: 1.35,   # Eid Al-Adha W22
    23: 1.20,   # Eid Al-Adha W23
    39: 1.15,   # National Day W39
    47: 1.40,   # White Friday W47
    48: 1.25,   # White Friday W48
}

# 전기요금 부담 지수 (W26-W37, Inverter 모델만 적용)
ELECTRICITY_BURDEN = {
    'W26':0.3,'W27':0.5,'W28':0.7,'W29':0.8,'W30':0.9,
    'W31':1.0,'W32':1.0,'W33':0.9,'W34':0.8,'W35':0.6,'W36':0.4,'W37':0.2,
}
ELECTRICITY_INVERTER_BOOST = 0.05  # 부담 지수 1.0당 +5%

OIL_SENSITIVITY = 0.003
OIL_BASELINE = 75.0


def _seg_key(model: str) -> str:
    """'Mini Split AC|Inverter' 형태 문자열 반환 (파이프, 공백 없음)."""
    seg = _get_model_segment(model)
    return f"{seg[0]}|{seg[1]}"


def _heat_index_c(temp_c: float, humidity: float) -> float:
    """체감온도 계산 (Rothfusz Heat Index 공식). temp < 27°C이면 그대로 반환."""
    if temp_c < 27:
        return temp_c
    t = temp_c * 9 / 5 + 32
    r = humidity
    hi = (-42.379 + 2.04901523*t + 10.14333127*r
          - 0.22475541*t*r - 0.00683783*t*t
          - 0.05481717*r*r + 0.00122874*t*t*r
          + 0.00085282*t*r*r - 0.00000199*t*t*r*r)
    return (hi - 32) * 5 / 9


class SimulationEngine:

    # UI 카테고리명 → fcst_output에 실제 저장된 값 매핑
    CAT_ALIASES = {
        'Mini Split': {'Inverter', 'Split AC', 'Mini Split AC', 'Mini Split'},
        'Window':     {'Window', 'Window AC'},
        'Free Standing': {'Floor Standing AC', 'Free Standing AC', 'Free Standing', 'PAC'},
        'Cassette':   {'Cassette AC', 'Cassette'},
        'Packaged':   {'Packaged AC', 'Packaged'},
    }

    def _expand_categories(self, cats: set) -> set:
        expanded = set()
        for c in cats:
            expanded |= self.CAT_ALIASES.get(c, {c})
        return expanded

    def simulate(self, base_forecasts: list, params: dict,
                 current_price_gaps: dict) -> list:
        scope = params.get('scope', {})
        week_from = scope.get('week_from', 1)
        week_to = scope.get('week_to', 52)
        categories = self._expand_categories(set(scope.get('categories', [])))
        ext = params.get('external_vars', {})
        trends = params.get('trends_index', {})

        results = []
        for f in base_forecasts:
            week = f.get('week', 'W1')
            week_num = int(week[1:]) if week.startswith('W') else 0
            if not (week_from <= week_num <= week_to):
                continue
            if categories and f.get('category', '') not in categories:
                continue

            model = f['model']
            base = f.get('predicted', 0)
            sk = _seg_key(model)
            sub_family, compressor = sk.split('|')

            pf  = self._price_factor(sk, params.get('price_positioning', {}), current_price_gaps)
            hf  = self._heat_index_factor(week, sub_family, ext)
            ief = self._islamic_event_factor(week_num)
            of  = self._oil_factor(ext)
            ef  = self._electricity_factor(week, compressor, ext)
            oof = self._oos_factor(sk, ext.get('oos_brands', {}))
            prf, is_promo, is_hangover = self._promo_factor(
                week_num, sk, params.get('promo_periods', [])
            )
            tf  = self._trends_factor(week, trends)

            total = pf * hf * ief * of * ef * oof * prf * tf
            adjusted = round(base * total)
            delta_pct = round((adjusted / base - 1) * 100, 1) if base > 0 else 0.0

            results.append({
                **f,
                'adjusted': adjusted,
                'delta_pct': delta_pct,
                'factors': {
                    'price':         round(pf,  4),
                    'heat_index':    round(hf,  4),
                    'islamic_event': round(ief, 4),
                    'oil':           round(of,  4),
                    'electricity':   round(ef,  4),
                    'oos':           round(oof, 4),
                    'promo':         round(prf, 4),
                    'trends':        round(tf,  4),
                },
                'is_promo_week': is_promo,
                'is_hangover':   is_hangover,
            })
        return results

    def _price_factor(self, sk: str, positioning: dict, gaps: dict) -> float:
        if sk not in positioning:
            return 1.0
        seg_tuple = tuple(sk.split('|'))
        elasticity = SEGMENT_ELASTICITY.get(seg_tuple, _DEFAULT_ELASTICITY)
        db_key = sk.replace('|', ' | ')
        brands = gaps.get(db_key, {}).get('brands', {})
        effects = []
        for comp_key, target in positioning[sk].items():
            brand = comp_key.replace('vs_', '').replace('_', ' ')
            current = brands.get(brand, {}).get('gap_pct')
            if current is None:
                continue
            effects.append((current - target) / 100.0)
        if not effects:
            return 1.0
        avg = sum(effects) / len(effects)
        return max(0.5, min(3.0, 1.0 + elasticity * avg))

    def _heat_index_factor(self, week: str, sub_family: str, ext: dict) -> float:
        base_temp = RIYADH_TEMP.get(week, 30)
        base_hum  = RIYADH_HUMIDITY.get(week, 25)
        temp_offset = {'hot': 5, 'normal': 0, 'mild': -5}.get(
            ext.get('temp_scenario', 'normal'), 0)
        hum_offset = {'high': 15, 'normal': 0, 'low': -10}.get(
            ext.get('humidity_scenario', 'normal'), 0)
        actual_temp = base_temp + temp_offset
        actual_hum  = min(100, max(0, base_hum + hum_offset))
        baseline_hi = _heat_index_c(base_temp, base_hum)
        actual_hi   = _heat_index_c(actual_temp, actual_hum)
        delta = actual_hi - baseline_hi
        sens = TEMP_SENSITIVITY.get(sub_family, 0.020)
        return max(0.7, min(1.6, 1.0 + sens * delta))

    def _islamic_event_factor(self, week_num: int) -> float:
        return ISLAMIC_BOOSTS.get(week_num, 1.0)

    def _oil_factor(self, ext: dict) -> float:
        oil = ext.get('oil_price_usd', OIL_BASELINE)
        return max(0.9, min(1.3, 1.0 + OIL_SENSITIVITY * (oil - OIL_BASELINE)))

    def _electricity_factor(self, week: str, compressor: str, ext: dict) -> float:
        if not ext.get('electricity_burden', True):
            return 1.0
        if compressor != 'Inverter':
            return 1.0
        burden = ELECTRICITY_BURDEN.get(week, 0.0)
        return 1.0 + ELECTRICITY_INVERTER_BOOST * burden

    def _oos_factor(self, sk: str, oos_brands: dict) -> float:
        db_key = sk.replace('|', ' | ')
        oosed = oos_brands.get(db_key, [])
        if not oosed:
            return 1.0
        return min(1.20, 1.0 + 0.05 * len(oosed))

    def _trends_factor(self, week: str, trends_index: dict) -> float:
        if not trends_index:
            return 1.0
        val = trends_index.get(week)
        if val is None:
            return 1.0
        # 지수 50 = 기준(1.0), 100 = +10%, 0 = -10%
        return max(0.9, min(1.1, 1.0 + (val - 50) / 500.0))

    def _promo_factor(self, week_num: int, sk: str, promos: list) -> tuple:
        seg_tuple = tuple(sk.split('|'))
        elasticity = SEGMENT_ELASTICITY.get(seg_tuple, _DEFAULT_ELASTICITY)
        for p in promos:
            # 세그먼트 필터: 'ALL'이면 전체 적용
            seg = p.get('segment', '')
            if seg != 'ALL' and seg != sk:
                continue
            start, end = p['start_week'], p['end_week']
            hangover = p.get('hangover_weeks', 0)

            # 직접 부스트 모드 (boost_direct_pct 사용)
            if 'boost_direct_pct' in p:
                boost = p['boost_direct_pct'] / 100.0
                if start <= week_num <= end:
                    return max(1.0, 1.0 + boost), True, False
                if hangover > 0 and end < week_num <= end + hangover:
                    return max(0.85, 1.0 - 0.30 * boost), False, True
                continue

            # 기존 갭 기반 모드
            current_gap = p.get('current_gap_pct', 0)
            target_gap  = p.get('target_gap_pct', 0)
            cut = max(0.0, (current_gap - target_gap) / 100.0)
            if start <= week_num <= end:
                return max(1.0, 1.0 + elasticity * cut), True, False
            if hangover > 0 and end < week_num <= end + hangover:
                return max(0.85, 1.0 - 0.30 * elasticity * cut), False, True
        return 1.0, False, False
