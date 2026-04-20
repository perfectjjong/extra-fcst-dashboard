import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(os.path.abspath(__file__)).parent.parent))
from collections import defaultdict

from flask import Flask, jsonify, request, send_from_directory
from api.simulator import SimulationEngine, RIYADH_TEMP, RIYADH_HUMIDITY
from api.trends import get_trends_index
from api.note_interpreter import interpret_note
from pipeline.build_price_segments import get_brand_price_context, get_oos_signals

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


def create_app(db_path=DEFAULT_DB, fcst_path=DEFAULT_FCST):
    app = Flask(__name__)

    @app.route('/')
    def index():
        return send_from_directory(os.path.abspath(DASHBOARD_DIR), 'hub.html')

    @app.route('/fcst')
    def fcst_page():
        return send_from_directory(os.path.abspath(DASHBOARD_DIR), 'fcst_dashboard.html')

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
        return jsonify({'week': week, 'temp_c': temp, 'humidity_pct': humidity, 'oil_price_usd': oil})

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
        results = engine.simulate(fcst_data.get('long_range_forecasts', []), params, gaps)

        base_total = sum(r.get('predicted', 0) for r in results)
        adj_total  = sum(r.get('adjusted', 0) for r in results)
        delta_pct  = round((adj_total / base_total - 1) * 100, 1) if base_total > 0 else 0.0

        by_week = defaultdict(lambda: {'base': 0, 'adjusted': 0, 'promo': False, 'hangover': False})
        for r in results:
            w = r['week']
            by_week[w]['base']     += r['predicted']
            by_week[w]['adjusted'] += r['adjusted']
            if r['is_promo_week']:  by_week[w]['promo']    = True
            if r['is_hangover']:    by_week[w]['hangover'] = True

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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host='0.0.0.0', port=5050, debug=False)
