# FCST Demand Simulator v2 — Claude Code OAuth 기반

## 개요

기존 FCST Demand Simulator의 Anthropic API 키 의존성을 제거하고, Claude Code CLI의 OAuth 인증을 활용하여 AI 예측 + 대화형 시뮬레이터로 확장한다. B2C 대시보드의 실적 데이터를 연동하여 시뮬레이션 적합도를 추적한다.

## 목표

1. API 키 없이 Claude Code OAuth(구독)로 AI 시뮬레이션 실행
2. 대화형 AI — 메모 해석, 후속 질문, 결과 설명
3. B2C 실적 데이터 연동 — 2023~2026 sell-out 데이터 활용
4. 적합도 트래킹 — 시뮬레이션 vs 실적 MAPE 자동 계산

## 비목표

- 다수 동시 사용자 지원 (1인 사용 전제)
- 스트리밍 응답 (완성 후 표시)
- 모바일 최적화

---

## 아키텍처

```
┌─────────────────────────────────────┐
│  브라우저 (simulator-v2.html)        │
│  ├ 기존 UI: 슬라이더, 차트, KPI     │
│  └ 채팅 패널 (우측 접이식)           │
└──────────┬──────────────────────────┘
           │ HTTP (REST)
┌──────────▼──────────────────────────┐
│  Bridge Server (Flask, :5050)        │
│  ├ /api/simulate     ← 기존 유지     │
│  ├ /api/env-data     ← 기존 유지     │
│  ├ /api/oos          ← 기존 유지     │
│  ├ /api/trends       ← 기존 유지     │
│  ├ /api/chat         ← 신규 (AI대화) │
│  └ /api/chat/follow  ← 신규 (후속)   │
└──────────┬──────────────────────────┘
           │ subprocess (stdio)
┌──────────▼──────────────────────────┐
│  claude -p "프롬프트"                │
│  --model opus                        │
│  --output-format json                │
│  --mcp-config mcp_config.json        │
│  ┌─────────────────────────────┐    │
│  │  MCP Server (Python, stdio) │    │
│  │  ├ simulate()               │    │
│  │  ├ get_environment()        │    │
│  │  ├ get_oos_signals()        │    │
│  │  ├ get_trends()             │    │
│  │  ├ get_actual_sellout()     │    │
│  │  └ get_forecast_accuracy()  │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

### 데이터 흐름

1. **슬라이더 조작** → Flask `/api/simulate` 직접 호출 → 즉시 반영 (기존과 동일)
2. **AI 대화** → Flask `/api/chat` → `claude -p` subprocess → MCP 도구 호출 → 결과 반환
3. **실적 조회** → Claude가 `get_actual_sellout` MCP 도구 호출 → B2C HTML 파싱 데이터 반환

---

## MCP 서버 도구 설계

### 도구 1: simulate

시뮬레이션 엔진(SimulationEngine) 실행.

**입력:**
```json
{
  "scope": {
    "week_from": 1,
    "week_to": 52,
    "categories": []
  },
  "price_positioning": {
    "Mini Split AC|Inverter": { "vs_Samsung": -5 }
  },
  "promo_periods": [
    {
      "segment": "ALL",
      "start_week": 25,
      "end_week": 28,
      "boost_direct_pct": 15,
      "hangover_weeks": 2
    }
  ],
  "external_vars": {
    "temp_scenario": "hot|normal|mild",
    "humidity_scenario": "high|normal|low",
    "oil_price_usd": 75,
    "electricity_burden": true,
    "oos_brands": {}
  },
  "trends_index": {},
  "note_adjustments": [
    {
      "week_from": 10,
      "week_to": 20,
      "factor": 0.95,
      "label": "지정학 리스크"
    }
  ]
}
```

**출력:**
```json
{
  "summary": {
    "base_total": 45000,
    "adjusted_total": 48150,
    "delta_pct": 7.0,
    "model_count": 120,
    "promo_weeks": 4,
    "week_range": [1, 52]
  },
  "by_week": {
    "W1": { "base": 800, "adjusted": 820, "promo": false, "hangover": false },
    "...": "..."
  },
  "top_movers": [
    { "week": "W25", "delta_pct": 18.2, "reason": "프로모 + 폭염" }
  ]
}
```

### 도구 2: get_environment

**입력:** `{ "week": "W16" }` (선택, 미지정 시 현재 주)
**출력:** `{ "week": "W16", "temp_c": 34, "humidity_pct": 16, "oil_price_usd": 82.3 }`

### 도구 3: get_oos_signals

**입력:** 없음
**출력:** `{ "Mini Split AC | Inverter": ["Samsung", "Gree"], "Window AC | Rotary": [] }`

### 도구 4: get_trends

**입력:** 없음
**출력:** `{ "W1": 42, "W2": 45, ..., "W52": 38 }`

### 도구 5: get_actual_sellout

B2C 대시보드 HTML에서 파싱한 실적 데이터 조회.

**입력:**
```json
{
  "year": "2026",
  "week_from": 1,
  "week_to": 17,
  "channel": null,
  "category": null
}
```

**출력:**
```json
{
  "year": "2026",
  "total_qty": 42350,
  "by_week": {
    "W1": { "qty": 1200, "channels": { "BH": 320, "eXtra": 180, "..." : "..." } },
    "...": "..."
  },
  "by_category": {
    "Split AC": 28500,
    "Window AC": 8200,
    "Floor Standing AC": 3100,
    "Cassette AC": 2550
  },
  "data_as_of": "2026-04-23"
}
```

### 도구 6: get_forecast_accuracy

시뮬레이션 로그와 실적을 비교하여 적합도 계산.

**입력:** 없음 (가장 최근 시뮬레이션 기준)
**출력:**
```json
{
  "simulation_id": "2026-04-15_sim01",
  "simulation_date": "2026-04-15",
  "weeks_compared": 15,
  "overall_mape": 6.1,
  "by_category": {
    "Split AC": { "sim": 12400, "actual": 11800, "mape": 4.8 },
    "Window AC": { "sim": 3200, "actual": 3500, "mape": 9.4 }
  },
  "worst_weeks": [
    { "week": "W12", "sim": 2800, "actual": 3400, "error_pct": 17.6, "note": "Eid Al-Fitr" }
  ]
}
```

---

## Bridge Server (Flask)

### 기존 엔드포인트 유지

- `POST /api/simulate` — 슬라이더 조작 시 직접 호출 (즉시 반영)
- `GET /api/env-data` — 환경 데이터
- `GET /api/oos` — OOS 신호
- `GET /api/trends` — Google Trends

### 신규 엔드포인트

#### POST /api/chat

첫 대화 또는 새 주제 시작.

**요청:** `{ "message": "사우디 폭염 + 유가 상승이면?" }`

**처리:**
1. 시스템 프롬프트 구성 (시뮬레이터 컨텍스트 + 도구 설명)
2. `claude -p "시스템프롬프트 + 사용자메시지" --output-format json --mcp-config mcp_config.json` 실행
3. 응답 파싱 → 도구 호출 결과 추출
4. 세션 히스토리에 저장
5. 응답 반환

**응답:**
```json
{
  "session_id": "abc123",
  "reply": "폭염(extreme) + 유가 90달러 조건으로 시뮬레이션했습니다...",
  "tool_calls": [
    { "tool": "simulate", "params": { "..." : "..." } }
  ],
  "simulation_result": { "..." : "..." },
  "method": "claude"
}
```

#### POST /api/chat/follow

후속 질문 (히스토리 포함).

**요청:** `{ "session_id": "abc123", "message": "W25 행오버가 왜 이렇게 큰 거야?" }`

**처리:**
1. 세션 히스토리 로드 (최대 20턴)
2. 이전 대화 + 새 질문을 하나의 프롬프트로 구성
3. `claude -p` 실행
4. 히스토리 업데이트

---

## 채팅 UI

### 레이아웃

기존 simulator.html 우측에 접이식 채팅 패널 추가.

```
┌──────────────────────────┬──────────────┐
│  기존 시뮬레이터 UI       │  💬 AI 채팅   │
│  ┌──────────────────┐    │              │
│  │ KPI Strip        │    │  메시지 목록  │
│  ├──────────────────┤    │  ...         │
│  │ Chart            │    │  ...         │
│  ├──────────────────┤    │              │
│  │ 슬라이더/프로모    │    │  ┌─────────┐│
│  │ 탭 패널          │    │  │ 입력창   ││
│  └──────────────────┘    │  └─────────┘│
└──────────────────────────┴──────────────┘
```

### 기능

- 메시지 입력 → `/api/chat` 또는 `/api/chat/follow` 호출
- AI 응답에 simulate 도구 호출이 포함되면 좌측 차트/KPI 자동 업데이트
- 응답 중 로딩 상태 표시 ("⏳ Claude 분석 중...")
- 메서드 배지: Claude AI / 룰 기반 폴백
- 채팅 접기/펼치기 토글 버튼

### UI 연동 흐름

1. 사용자가 채팅에 메모 입력
2. Flask → Claude CLI → MCP simulate 도구 호출
3. 응답에 `simulation_result` 포함
4. 채팅 패널: AI 설명 텍스트 표시
5. 시뮬레이터 UI: simulation_result로 차트/KPI 업데이트 (renderChart, renderKPIs 재호출)

---

## 실적 데이터 연동

### 데이터 소스

```
/home/ubuntu/Shaker-MD-App/docs/dashboards/b2c-unified/index.html
```

서버 기동 시 HTML에서 `const _ALL = {...}` JSON을 파싱하여 메모리에 캐시.

### 파싱 대상

| 필드 | _ALL 경로 | 용도 |
|------|-----------|------|
| sell-out 수량 | `data[year].raw[].q` | 실적 수량 |
| 주차 | `data[year].raw[].w` | 주차 매핑 |
| 채널 | `data[year].raw[].ch` | 채널별 분석 |
| 카테고리 | `data[year].raw[].c` | 카테고리별 분석 |
| 컴프레서 | `data[year].raw[].comp` | 세그먼트 매핑 |
| sell-thru | `data[year].sellthru[].q/v` | 공급 대비 판매 |

### 갱신 주기

`b2c_unified_dashboard_generator.py` 실행 후 HTML이 갱신되면, 다음 MCP 도구 호출 시 파일 mtime을 확인하여 자동 리로드.

---

## 적합도 트래킹

### 로그 저장

시뮬레이션 실행 시 `forecast_log.json`에 자동 저장.

```json
{
  "logs": [
    {
      "id": "2026-04-23_001",
      "timestamp": "2026-04-23T14:30:00",
      "params": {
        "scope": { "week_from": 1, "week_to": 52 },
        "external_vars": { "oil_price_usd": 85, "temp_scenario": "hot" },
        "promo_periods": [],
        "note_adjustments": []
      },
      "results_by_week": {
        "W1": { "base": 800, "adjusted": 820 },
        "W2": { "base": 850, "adjusted": 870 }
      },
      "results_by_category": {
        "Split AC": { "base": 30000, "adjusted": 32100 },
        "Window AC": { "base": 8000, "adjusted": 8200 }
      },
      "note": "유가 85달러, 폭염 시나리오"
    }
  ]
}
```

### MAPE 계산

```
MAPE = (1/n) × Σ |actual - simulated| / actual × 100
```

- 주차별 MAPE
- 카테고리별 MAPE
- 전체 가중평균 MAPE
- worst weeks (가장 오차가 큰 주차) 하이라이트

---

## 에러 처리

### Claude Code CLI 실패

| 상황 | 감지 방법 | 폴백 |
|------|-----------|------|
| 타임아웃 (30초) | subprocess timeout | 룰 기반 메모 해석 |
| 인증 만료 | exit code ≠ 0 | 에러 메시지 + 룰 기반 |
| 구독 한도 초과 | exit code ≠ 0 | 에러 메시지 + 룰 기반 |

폴백 시 응답에 `method: "rule_fallback"` 포함, UI에 경고 배지 표시.

### B2C 데이터 파싱 실패

HTML 파일 부재 또는 JSON 파싱 실패 시:
- `get_actual_sellout`, `get_forecast_accuracy` 도구만 비활성화
- 나머지 시뮬레이션 기능 정상 동작
- 채팅에서 실적 질문 시 "실적 데이터를 로드할 수 없습니다" 응답

---

## 파일 구조

```
api/
├── server.py              ← 기존 + /api/chat, /api/chat/follow 추가
├── simulator.py           ← 기존 유지 (8-factor 엔진)
├── note_interpreter.py    ← 기존 유지 (룰 기반 폴백용)
├── mcp_server.py          ← 신규: MCP 도구 서버 (6개 도구)
├── chat_bridge.py         ← 신규: Claude CLI 호출 + 세션 관리
├── b2c_data_loader.py     ← 신규: B2C HTML 파싱 + 캐시
├── forecast_logger.py     ← 신규: 시뮬레이션 로그 저장/조회
└── mcp_config.json        ← 신규: MCP 서버 설정
dashboard/
├── simulator.html         ← 기존 유지 (레거시)
└── simulator-v2.html      ← 신규: 채팅 패널 추가 버전
data/
├── sellout.db             ← 기존 유지
├── fcst_output.json       ← 기존 유지
└── forecast_log.json      ← 신규: 시뮬레이션 로그
```

---

## MCP 설정 파일

```json
// mcp_config.json
{
  "mcpServers": {
    "fcst-simulator": {
      "command": "python3",
      "args": ["api/mcp_server.py"],
      "cwd": "/home/ubuntu/2026/03. Reporting/01. FCST"
    }
  }
}
```

Claude CLI 호출 시: `claude -p "프롬프트" --output-format json --mcp-config api/mcp_config.json`

---

## Claude 시스템 프롬프트

```
You are a demand forecasting analyst for LG air conditioners in Saudi Arabia.
You have access to simulation tools and actual sell-out data (2023-2026).

When the user describes a scenario:
1. Translate it into simulation parameters
2. Call the simulate tool
3. Explain the results in Korean with key insights

When asked about accuracy:
1. Call get_actual_sellout for real data
2. Call get_forecast_accuracy for MAPE comparison
3. Highlight which weeks/categories had the largest errors and why

Always respond in Korean. Use specific numbers and week references.
Factor range: 0.85-1.20. Week range: W1-W52 (ISO weeks, 2026).
```

---

## 제약사항

- 1인 사용 전제 (동시 접속 미고려)
- Claude Code 구독(Max 플랜) 필수
- `claude` CLI가 서버에 설치 및 인증된 상태여야 함
- B2C 데이터는 대시보드 HTML 갱신에 의존 (실시간 아님, 주 1회)
- 대화 히스토리는 서버 메모리 저장 (서버 재시작 시 초기화)
