import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(os.path.abspath(__file__)).parent.parent))
from collections import defaultdict

from flask import Flask, jsonify, request, send_from_directory
from api.simulator import (SimulationEngine, RIYADH_TEMP, RIYADH_HUMIDITY,
                            SAUDI_PMI_2026, _WEEK_TO_MONTH)
from api.trends import get_trends_index
from api.note_interpreter import interpret_note
from api.chat_bridge import ChatBridge
from api.b2c_data_loader import B2CDataLoader
from pipeline.build_price_segments import get_brand_price_context, get_oos_signals

B2C_HTML = Path("/home/ubuntu/Shaker-MD-App/docs/dashboards/b2c-unified/index.html")

# ── 재학습 상태 관리 ──────────────────────────────────────────────────
_retrain_state = {
    'status': 'idle',      # idle | running | done | error
    'started_at': None,
    'finished_at': None,
    'log': [],
    'error': None,
}
_retrain_lock = threading.Lock()
BASE_DIR = str(Path(os.path.abspath(__file__)).parent.parent)
CRON_SCRIPT = os.path.join(BASE_DIR, 'cron_update.py')
PYTHON_BIN = sys.executable


def _run_retrain():
    """별도 스레드에서 cron_update.py 실행. 로그를 _retrain_state['log']에 스트리밍."""
    with _retrain_lock:
        _retrain_state['status'] = 'running'
        _retrain_state['started_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        _retrain_state['finished_at'] = None
        _retrain_state['log'] = []
        _retrain_state['error'] = None

    try:
        proc = subprocess.Popen(
            [PYTHON_BIN, CRON_SCRIPT],
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            line = line.rstrip()
            with _retrain_lock:
                _retrain_state['log'].append(line)
        proc.wait()
        with _retrain_lock:
            if proc.returncode == 0:
                _retrain_state['status'] = 'done'
            else:
                _retrain_state['status'] = 'error'
                _retrain_state['error'] = f'exit code {proc.returncode}'
    except Exception as e:
        with _retrain_lock:
            _retrain_state['status'] = 'error'
            _retrain_state['error'] = str(e)
    finally:
        with _retrain_lock:
            _retrain_state['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')

FCST_TO_B2C_CAT = {
    'Inverter': 'Split AC',
    'Window': 'Window AC',
    'Floor Standing AC': 'Floor Standing AC',
}

_scale_cache = {'factors': None, 'ts': 0}


def _compute_scale_factors(forecasts: list, b2c_loader) -> dict[str, float]:
    """카테고리별 2025 실적 대비 예측 비율로 스케일 팩터 산출. 1시간 캐시."""
    now = time.time()
    if _scale_cache['factors'] and now - _scale_cache['ts'] < 3600:
        return _scale_cache['factors']

    fcst_weeks = set(r['week'] for r in forecasts)
    if not fcst_weeks:
        return {}

    week_nums = [int(w[1:]) for w in fcst_weeks]
    w_from, w_to = min(week_nums), max(week_nums)

    actuals_2025 = b2c_loader.get_sellout("2025", w_from, w_to)
    if not actuals_2025 or actuals_2025['total_qty'] == 0:
        return {}

    fcst_by_cat = defaultdict(int)
    for r in forecasts:
        fcst_by_cat[r.get('category', '')] += r.get('predicted', 0)

    factors = {}
    for fcst_cat, b2c_cat in FCST_TO_B2C_CAT.items():
        actual_qty = actuals_2025.get('by_category', {}).get(b2c_cat, 0)
        fcst_qty = fcst_by_cat.get(fcst_cat, 0)
        if fcst_qty > 0 and actual_qty > 0:
            factors[fcst_cat] = max(0.5, min(15.0, actual_qty / fcst_qty))

    _scale_cache['factors'] = factors
    _scale_cache['ts'] = now
    return factors

# 유가 캐시 (1시간)
_oil_cache = {'price': None, 'ts': 0}

def _fetch_oil_price() -> float:
    now = time.time()
    if _oil_cache['price'] and now - _oil_cache['ts'] < 3600:
        return _oil_cache['price']
    try:
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/CL=F'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        price = data['chart']['result'][0]['meta']['regularMarketPrice']
        _oil_cache['price'] = round(float(price), 1)
        _oil_cache['ts'] = now
    except Exception:
        _oil_cache['price'] = _oil_cache['price'] or 75.0
    return _oil_cache['price']

BASE_DIR = os.path.join(os.path.dirname(__file__), '..')
DEFAULT_DB = os.path.join(BASE_DIR, 'data', 'sellout.db')
DEFAULT_FCST = os.path.join(BASE_DIR, 'dashboard', 'fcst_output.json')
DASHBOARD_DIR = os.path.join(BASE_DIR, 'dashboard')
_chat_bridge = ChatBridge()


def create_app(db_path=DEFAULT_DB, fcst_path=DEFAULT_FCST):
    app = Flask(__name__)

    @app.route('/')
    def index():
        return send_from_directory(os.path.abspath(DASHBOARD_DIR), 'hub.html')

    @app.route('/fcst')
    def fcst_page():
        return send_from_directory(os.path.abspath(DASHBOARD_DIR), 'fcst_dashboard.html')

    HUESTAY_DIR = '/home/ubuntu/huestay'
    import sys as _sys
    _sys.path.insert(0, HUESTAY_DIR)
    import db as _hs_db
    import sheets_sync as _hs_sheets

    @app.route('/huestay/')
    @app.route('/huestay')
    def huestay_index():
        return send_from_directory(HUESTAY_DIR, 'index.html')

    @app.route('/huestay/api/submit', methods=['POST', 'OPTIONS'])
    def huestay_submit():
        if request.method == 'OPTIONS':
            return '', 204
        data = request.get_json(force=True)
        row_id = _hs_db.insert(data)
        rows = _hs_db.all_rows()
        row  = next((r for r in rows if r['id'] == row_id), None)
        synced = _hs_sheets.push_row(row) if row else False
        if synced:
            _hs_db.mark_synced(row_id)
        return jsonify({'result': 'ok', 'id': row_id, 'synced': synced})

    @app.route('/huestay/admin')
    def huestay_admin():
        rows = _hs_db.all_rows()
        has_creds = _hs_sheets.CREDS_FILE.exists()
        html = f'''<!DOCTYPE html><html lang="ko"><head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Hue Stay 고객 DB</title>
        <style>
          body{{font-family:sans-serif;padding:1.5rem;background:#fdfaf5;color:#3d2b1f}}
          h1{{font-size:1.4rem;margin-bottom:.5rem;color:#c4714a}}
          .badge{{display:inline-block;padding:.15rem .6rem;border-radius:8px;font-size:.72rem;font-weight:700}}
          .res{{background:#fef3c7;color:#92400e}} .inq{{background:#dbeafe;color:#1e40af}}
          .sync{{background:#d1fae5;color:#065f46}} .nosync{{background:#fee2e2;color:#991b1b}}
          table{{width:100%;border-collapse:collapse;font-size:.82rem;margin-top:1rem}}
          th{{background:#c4714a;color:#fff;padding:.5rem .6rem;text-align:left}}
          td{{padding:.45rem .6rem;border-bottom:1px solid #f0e8df;vertical-align:top}}
          tr:hover td{{background:#fef9f5}}
          .actions{{margin-bottom:1rem;display:flex;gap:.5rem;flex-wrap:wrap}}
          a.btn{{padding:.4rem 1rem;background:#c4714a;color:#fff;text-decoration:none;border-radius:8px;font-size:.83rem}}
          .stat{{color:{"#065f46" if has_creds else "#991b1b"};font-size:.8rem;margin-bottom:.8rem}}
        </style></head><body>
        <h1>Hue Stay 고객 DB</h1>
        <p class="stat">{'✅ Google Sheets 연동 활성' if has_creds else '⚠️ Google Sheets 미연동 (credentials.json 없음)'}</p>
        <div class="actions">
          <a class="btn" href="/huestay/export.csv">CSV 다운로드</a>
          <a class="btn" href="/huestay/admin/sync">미싱크 Sheets 전송</a>
        </div>
        <table><tr>
          <th>#</th><th>접수일시</th><th>유형</th><th>이름</th><th>연락처</th>
          <th>이메일</th><th>회사명</th><th>체크인</th><th>체크아웃</th>
          <th>인원</th><th>제목</th><th>내용</th><th>Sheets</th>
        </tr>'''
        for r in rows:
            typ  = '<span class="badge res">예약</span>' if r['type']=='reservation' else '<span class="badge inq">문의</span>'
            sync = '<span class="badge sync">✓</span>' if r['synced'] else '<span class="badge nosync">-</span>'
            html += f'''<tr>
              <td>{r["id"]}</td><td>{r["created"]}</td><td>{typ}</td>
              <td>{r["name"]}</td><td>{r["contact"]}</td><td>{r["email"]}</td>
              <td>{r["company"]}</td><td>{r["checkin"]}</td><td>{r["checkout"]}</td>
              <td>{r["guests"]}</td><td>{r["subject"]}</td>
              <td style="max-width:200px;word-break:break-word">{r["message"]}</td>
              <td>{sync}</td></tr>'''
        html += f'</table><p style="margin-top:1rem;font-size:.75rem;opacity:.5">총 {len(rows)}건</p></body></html>'
        return html

    @app.route('/huestay/export.csv')
    def huestay_export():
        import csv, io
        rows = _hs_db.all_rows()
        buf  = io.StringIO()
        w    = csv.writer(buf)
        w.writerow(['ID','접수일시','유형','이름','연락처','이메일','회사명',
                    '체크인','체크아웃','인원','제목','내용'])
        for r in rows:
            w.writerow([r['id'],r['created'],r['type'],r['name'],r['contact'],
                        r['email'],r['company'],r['checkin'],r['checkout'],
                        r['guests'],r['subject'],r['message']])
        from flask import Response
        return Response(buf.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition':'attachment;filename=huestay_db.csv'})

    @app.route('/huestay/admin/sync')
    def huestay_sync():
        rows  = _hs_db.all_rows()
        count = _hs_sheets.sync_unsynced(rows, _hs_db.mark_synced)
        return jsonify({'synced': count})

    @app.route('/huestay/<path:filename>')
    def huestay_static(filename):
        return send_from_directory(HUESTAY_DIR, filename)

    @app.after_request
    def add_cors(response):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    @app.route('/api/request', methods=['POST', 'OPTIONS'])
    def post_request():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        data = request.get_json() or {}
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO requests (model, date_from, date_to, requester) VALUES (?, ?, ?, ?)",
            (data.get('model'), data.get('date_from'), data.get('date_to'), data.get('requester'))
        )
        conn.commit()
        conn.close()
        return jsonify({'status': 'ok'})

    @app.route('/api/feedback', methods=['POST', 'OPTIONS'])
    def post_feedback():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        data = request.get_json() or {}
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO fcst_feedback (model, week, predicted, actual, note) VALUES (?, ?, ?, ?, ?)",
            (data.get('model'), data.get('week'),
             data.get('predicted'), data.get('actual'), data.get('note'))
        )
        conn.commit()

        # Check retrain trigger: MAPE > 20% for 3+ recent feedback entries
        recent = conn.execute(
            "SELECT predicted, actual FROM fcst_feedback WHERE model=? ORDER BY submitted_at DESC LIMIT 3",
            (data.get('model'),)
        ).fetchall()
        conn.close()

        should_retrain = False
        if len(recent) >= 3:
            mapes = []
            for pred, actual in recent:
                if actual and actual > 0:
                    mapes.append(abs(actual - pred) / actual)
            if mapes and sum(m > 0.20 for m in mapes) >= 3:
                should_retrain = True

        return jsonify({'status': 'ok', 'retrain_suggested': should_retrain})

    @app.route('/api/fcst', methods=['GET'])
    def get_fcst():
        try:
            with open(fcst_path, encoding='utf-8') as f:
                data = json.load(f)
            if 'yoy_bias_2026' not in data:
                try:
                    from model.ensemble import _compute_2026_bias
                    data['yoy_bias_2026'] = _compute_2026_bias(db_path)
                except Exception:
                    data['yoy_bias_2026'] = 1.0
            return jsonify(data)
        except FileNotFoundError:
            return jsonify({'error': 'fcst_output.json not found'}), 404

    @app.route('/simulator')
    def simulator_page():
        return send_from_directory(os.path.abspath(DASHBOARD_DIR), 'simulator.html')

    @app.route('/api/env-data', methods=['GET'])
    def get_env_data():
        """현재 주차 기준 리야드 기온/습도 + 실시간 WTI 유가."""
        week = request.args.get('week', 'W16')
        temp     = RIYADH_TEMP.get(week, 25)
        humidity = RIYADH_HUMIDITY.get(week, 30)
        oil      = _fetch_oil_price()
        month    = _WEEK_TO_MONTH.get(week, 1)
        pmi      = SAUDI_PMI_2026.get(month, 50.0)
        return jsonify({'week': week, 'temp_c': temp, 'humidity_pct': humidity,
                        'oil_price_usd': oil, 'pmi_monthly': pmi})

    @app.route('/api/oos', methods=['GET'])
    def get_oos():
        try:
            return jsonify(get_oos_signals(db_path))
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/trends', methods=['GET'])
    def get_trends():
        try:
            return jsonify(get_trends_index())
        except Exception:
            return jsonify({}), 200

    _b2c_loader = B2CDataLoader(str(B2C_HTML))

    @app.route('/api/simulate', methods=['POST', 'OPTIONS'])
    def post_simulate():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        params = request.get_json() or {}
        params.setdefault('price_positioning', {})
        params.setdefault('promo_periods', [])
        params.setdefault('external_vars', {
            'temp_scenario': 'normal', 'humidity_scenario': 'normal',
            'oil_price_usd': 75, 'electricity_burden': True, 'oos_brands': {},
            'pmi_override': None,
        })
        params.setdefault('scope', {'week_from': 1, 'week_to': 52, 'categories': []})
        params.setdefault('trends_index', {})

        try:
            with open(fcst_path, encoding='utf-8') as f:
                fcst_data = json.load(f)
        except FileNotFoundError:
            return jsonify({'error': 'fcst_output.json not found'}), 404

        try:
            gaps = get_brand_price_context(db_path)
        except Exception:
            gaps = {}

        engine = SimulationEngine()
        raw_forecasts = fcst_data.get('long_range_forecasts', [])

        # 카테고리별 스케일 보정: 2025 동기간 실적 vs 예측 비율
        scale_factors = _compute_scale_factors(raw_forecasts, _b2c_loader)

        results = engine.simulate(raw_forecasts, params, gaps)

        if scale_factors:
            for r in results:
                sf = scale_factors.get(r.get('category', ''), 1.0)
                if sf != 1.0:
                    r['predicted'] = round(r['predicted'] * sf)
                    r['adjusted'] = round(r['adjusted'] * sf)

        base_total = sum(r.get('predicted', 0) for r in results)
        adj_total  = sum(r.get('adjusted', 0) for r in results)
        delta_pct  = round((adj_total / base_total - 1) * 100, 1) if base_total > 0 else 0.0

        by_week = defaultdict(lambda: {'base': 0, 'adjusted': 0, 'promo': False, 'hangover': False, 'by_cat': {}})
        for r in results:
            w = r['week']
            by_week[w]['base']     += r['predicted']
            by_week[w]['adjusted'] += r['adjusted']
            if r['is_promo_week']:  by_week[w]['promo']    = True
            if r['is_hangover']:    by_week[w]['hangover'] = True
            cat = r.get('category', 'Other')
            by_week[w]['by_cat'][cat] = by_week[w]['by_cat'].get(cat, 0) + r['adjusted']

        return jsonify({
            'results': results,
            'by_week': dict(by_week),
            'summary': {
                'base_total':    base_total,
                'adjusted_total': adj_total,
                'delta_pct':     delta_pct,
                'model_count':   len(set(r['model'] for r in results)),
                'promo_weeks':   sum(1 for w in by_week.values() if w['promo']),
                'week_range':    [params['scope']['week_from'], params['scope']['week_to']],
                'scale_factors': {k: round(v, 2) for k, v in scale_factors.items()},
            },
            'current_price_gaps': gaps,
        })

    @app.route('/api/interpret-note', methods=['POST', 'OPTIONS'])
    def post_interpret_note():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        data = request.get_json() or {}
        text = data.get('text', '')
        try:
            result = interpret_note(text)
            return jsonify(result)
        except Exception as e:
            return jsonify({'relevant': False, 'adjustments': [], 'reasoning': f'해석 오류: {e}'}), 200

    # 시뮬레이터 카테고리명 → B2C 데이터 카테고리명 변환
    _SIM_TO_B2C_CAT = {
        'Mini Split':    'Split AC',
        'Window':        'Window AC',
        'Free Standing': 'Floor Standing AC',
        'Cassette':      'Cassette AC',
        'Packaged':      'Packaged AC',
    }

    # 채널 그룹 → B2C 채널명 집합
    _CHANNEL_GROUPS = {
        'ir': {'BH', 'BM', 'Tamkeen', 'Zagzoog', 'Dhamin',
               'Star Appliance', 'Al Ghanem', 'Al Shathri', 'IR_Others'},
        'or': {'Al Manea', 'SWS', 'Black Box', 'Al Khunizan',
               'eXtra', 'OR_Others'},
    }

    @app.route('/api/actuals', methods=['GET'])
    def get_actuals():
        week_from = int(request.args.get('week_from', 1))
        week_to = int(request.args.get('week_to', 52))
        cat_param = request.args.get('category', '').strip()
        ch_param  = request.args.get('channel', '').strip().lower()
        b2c_cat   = _SIM_TO_B2C_CAT.get(cat_param) if cat_param else None
        b2c_ch    = _CHANNEL_GROUPS.get(ch_param)   # None = 전체

        current = _b2c_loader.get_sellout("2026", week_from, week_to, channel=b2c_ch, category=b2c_cat)
        prev = _b2c_loader.get_sellout("2025", week_from, week_to, channel=b2c_ch, category=b2c_cat)

        if not current:
            return jsonify({'error': 'B2C 2026 데이터 없음'}), 404

        by_week = {}
        for w, d in current.get("by_week", {}).items():
            by_week[w] = {"actual_2026": d["qty"]}

        if prev:
            for w, d in prev.get("by_week", {}).items():
                if w not in by_week:
                    by_week[w] = {}
                by_week[w]["actual_2025"] = d["qty"]

        cat_summary = []
        cur_cats = current.get("by_category", {})
        prev_cats = prev.get("by_category", {}) if prev else {}
        for cat, qty in sorted(cur_cats.items(), key=lambda x: -x[1]):
            if not cat:
                continue
            entry = {"category": cat, "qty_2026": qty,
                     "share_pct": round(qty / current["total_qty"] * 100, 1) if current["total_qty"] else 0}
            if cat in prev_cats and prev_cats[cat] > 0:
                entry["qty_2025"] = prev_cats[cat]
                entry["yoy_pct"] = round((qty / prev_cats[cat] - 1) * 100, 1)
            cat_summary.append(entry)

        total_2025 = prev["total_qty"] if prev else 0
        yoy_total = round((current["total_qty"] / total_2025 - 1) * 100, 1) if total_2025 > 0 else None

        iso_week = __import__('datetime').date.today().isocalendar()[1]

        return jsonify({
            "current_week": iso_week,
            "total_2026": current["total_qty"],
            "total_2025": total_2025,
            "yoy_total_pct": yoy_total,
            "by_week": by_week,
            "category_summary": cat_summary,
            "data_as_of": current.get("data_as_of", "unknown"),
        })

    @app.route('/simulator-v2')
    def simulator_v2_page():
        return send_from_directory(os.path.abspath(DASHBOARD_DIR), 'simulator-v2.html')

    @app.route('/api/chat', methods=['POST', 'OPTIONS'])
    def post_chat():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        if not message:
            return jsonify({'reply': '메시지를 입력해 주세요.', 'method': 'none', 'session_id': None})
        context = {k: data[k] for k in ('annual_target', 'channel') if k in data}
        result = _chat_bridge.chat(message, context=context or None)
        return jsonify(result)

    @app.route('/api/retrain', methods=['POST', 'OPTIONS'])
    def post_retrain():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        with _retrain_lock:
            if _retrain_state['status'] == 'running':
                return jsonify({'error': '재학습이 이미 진행 중입니다.'}), 409
        t = threading.Thread(target=_run_retrain, daemon=True)
        t.start()
        return jsonify({'status': 'started'})

    @app.route('/api/retrain/status', methods=['GET'])
    def get_retrain_status():
        with _retrain_lock:
            snap = dict(_retrain_state)
            snap['log_tail'] = snap.pop('log')[-50:]  # 마지막 50줄만 반환
        return jsonify(snap)

    @app.route('/api/chat/follow', methods=['POST', 'OPTIONS'])
    def post_chat_follow():
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        session_id = data.get('session_id')
        if not message:
            return jsonify({'reply': '메시지를 입력해 주세요.', 'method': 'none', 'session_id': session_id})
        context = {k: data[k] for k in ('annual_target', 'channel') if k in data}
        result = _chat_bridge.chat(message, session_id, context=context or None)
        return jsonify(result)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)
