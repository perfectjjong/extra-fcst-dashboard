"""
자연어 특이사항 메모 → 시뮬레이션 파라미터 변환

ANTHROPIC_API_KEY 환경변수가 있으면 Claude Haiku 사용,
없으면 룰 기반 폴백.
"""
import os
import re
from datetime import date

# ── 룰 기반 키워드 ─────────────────────────────────────────────────
_POS = ['종전', '평화', '휴전', '해제', '완화', '봉쇄해제', '회복', '개선', '호황', '성장',
        'ceasefire', 'peace', 'recovery', 'boost', 'improvement', 'growth']
_NEG = ['전쟁', '발발', '분쟁', '갈등', '제재', '봉쇄', '파업', '불황', '침체', '불안',
        'war', 'conflict', 'strike', 'recession', 'sanctions', 'blockade']
_OIL_UP   = ['유가 상승', '유가 급등', '유가 폭등', 'oil spike', 'oil surge']
_OIL_DOWN = ['유가 하락', '유가 급락', '유가 안정', 'oil drop', 'oil decline']

# 룰 기반 소비심리 영향 (SAR 기준)
_RULE_FACTOR = {
    'pos_event': 1.04,   # 긍정 이벤트 → 소비심리 +4%
    'neg_event': 0.95,   # 부정 이벤트 → -5%
    'oil_up':    0.97,   # 유가 상승 → -3%
    'oil_down':  1.02,   # 유가 하락 → +2%
}


def _date_to_week(month: int, day: int, year: int = 2026) -> int:
    """날짜 → ISO 주차 변환."""
    try:
        d = date(year, month, day)
        return d.isocalendar()[1]
    except ValueError:
        return 1


def _extract_dates(text: str):
    """'2/28', '4/25', '2월28일' 등 날짜 패턴 추출 → [(month, day), ...]"""
    dates = []
    for m in re.findall(r'(\d{1,2})[/\.\-월](\d{1,2})일?', text):
        try:
            mo, dy = int(m[0]), int(m[1])
            if 1 <= mo <= 12 and 1 <= dy <= 31:
                dates.append((mo, dy))
        except ValueError:
            pass
    return dates


def _extract_lag_weeks(text: str) -> int:
    """'1주일', '2주', '7일' 등 지연 기간 추출 → 주 수."""
    m = re.search(r'(\d+)\s*주', text)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*일', text)
    if m:
        return max(1, round(int(m.group(1)) / 7))
    return 0


def _rule_based(text: str) -> dict:
    """룰 기반 해석. Claude API 없을 때 폴백."""
    text_lower = text.lower()
    dates = _extract_dates(text)
    lag = _extract_lag_weeks(text)

    # 이벤트 유형 판정
    pos = any(k in text for k in _POS)
    neg = any(k in text for k in _NEG)
    oil_up   = any(k in text for k in _OIL_UP)
    oil_down = any(k in text for k in _OIL_DOWN)

    if not (pos or neg or oil_up or oil_down):
        return {'relevant': False, 'adjustments': [], 'reasoning': '수요에 직접 영향을 미치는 이벤트를 감지하지 못했습니다.'}

    # 날짜 범위 → 주차 변환
    weeks = [_date_to_week(mo, dy) for mo, dy in dates]
    from_week = min(weeks) if weeks else 1
    to_week = 52

    # 긍정 이벤트 처리: 효과 시작 = 이벤트 종료 + lag
    if pos and not neg:
        factor = _RULE_FACTOR['pos_event']
        # 가장 마지막 날짜 = 이벤트 종료, 그 이후 + lag 주부터 효과
        effect_start = max(weeks) + lag if weeks else 1 + lag
        label = f"긍정 이벤트 소비심리 회복 ({factor:.0%} 수요 증가)"
        reasoning = f"긍정 이벤트 감지 (종전/회복 키워드). 효과 시작 W{effect_start} (지연 {lag}주 적용)."
        if effect_start > 52:
            return {'relevant': True, 'adjustments': [], 'reasoning': '효과 시작 주차가 예측 범위(W52)를 초과합니다.'}
        adjs = [{'week_from': effect_start, 'week_to': 52, 'factor': factor, 'label': label}]

    elif neg and not pos:
        factor = _RULE_FACTOR['neg_event']
        effect_start = from_week
        effect_end = max(weeks) if weeks else 52
        label = f"부정 이벤트 소비심리 위축 ({factor:.0%} 수요 감소)"
        reasoning = f"부정 이벤트 감지 (전쟁/분쟁 키워드). W{effect_start}-W{effect_end} 기간 수요 억제."
        adjs = [{'week_from': effect_start, 'week_to': effect_end, 'factor': factor, 'label': label}]

    elif pos and neg:
        # 부정 → 종결 → 긍정 패턴
        neg_start = from_week
        pos_start = max(weeks) + lag if len(weeks) > 1 else from_week + lag
        adjs = [
            {'week_from': neg_start, 'week_to': max(weeks), 'factor': _RULE_FACTOR['neg_event'],
             'label': f"부정 이벤트 기간 수요 억제 ({_RULE_FACTOR['neg_event']:.0%})"},
            {'week_from': pos_start, 'week_to': 52, 'factor': _RULE_FACTOR['pos_event'],
             'label': f"이벤트 종결 후 회복 (+{(_RULE_FACTOR['pos_event']-1)*100:.0f}%)"},
        ]
        reasoning = f"부정→긍정 전환 패턴. W{neg_start}-W{max(weeks)} 억제, W{pos_start}부터 회복."

    elif oil_up:
        factor = _RULE_FACTOR['oil_up']
        adjs = [{'week_from': from_week, 'week_to': 52, 'factor': factor, 'label': '유가 상승 소비 위축'}]
        reasoning = '유가 상승 감지. 에너지 비용 부담으로 가전 소비 소폭 위축.'

    else:  # oil_down
        factor = _RULE_FACTOR['oil_down']
        adjs = [{'week_from': from_week, 'week_to': 52, 'factor': factor, 'label': '유가 하락 소비 호전'}]
        reasoning = '유가 하락 감지. 에너지 비용 절감으로 소비 소폭 개선.'

    return {'relevant': True, 'adjustments': adjs, 'reasoning': reasoning, 'method': 'rule'}


def _claude_interpret(text: str, api_key: str) -> dict:
    """Claude Haiku API를 통한 자연어 해석."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    system = """You are a demand forecasting analyst for LG air conditioners sold in Saudi Arabia through the eXtra Electronics channel.
Interpret the user's note and determine how it affects AC demand in 2026.

Return ONLY valid JSON (no markdown, no explanation outside JSON):
{
  "relevant": <true/false>,
  "adjustments": [
    {
      "week_from": <ISO week number 1-52>,
      "week_to": <ISO week number 1-52>,
      "factor": <demand multiplier, 1.0=no change, 1.05=+5%, 0.95=-5%>,
      "label": "<short Korean label>"
    }
  ],
  "reasoning": "<1-2 sentence Korean explanation>"
}

Rules:
- Year is 2026. ISO week numbers (W1=Jan1 week, W52=Dec last week).
- Factor range: 0.85 to 1.20.
- If not relevant to AC demand in Saudi Arabia, return relevant=false with empty adjustments.
- Geopolitical events, oil prices, consumer confidence, Ramadan timing, economic policy = relevant.
- Apply consumer confidence lag: events take 1-2 weeks to affect purchasing behavior.
- Negative events during the event period reduce demand; positive event (ceasefire, recovery) boosts demand after lag.
"""

    try:
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            system=system,
            messages=[{'role': 'user', 'content': text}]
        )
        import json
        raw = msg.content[0].text.strip()
        return {**json.loads(raw), 'method': 'claude'}
    except Exception as e:
        # Claude 실패 → 룰 기반 폴백
        result = _rule_based(text)
        result['method'] = 'rule_fallback'
        result['api_error'] = str(e)
        return result


def interpret_note(text: str) -> dict:
    """공개 인터페이스."""
    if not text or not text.strip():
        return {'relevant': False, 'adjustments': [], 'reasoning': '메모가 비어 있습니다.'}

    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if api_key:
        return _claude_interpret(text, api_key)
    return _rule_based(text)
