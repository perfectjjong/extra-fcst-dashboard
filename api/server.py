import json
import os
import sqlite3

from flask import Flask, jsonify, request

BASE_DIR = os.path.join(os.path.dirname(__file__), '..')
DEFAULT_DB = os.path.join(BASE_DIR, 'data', 'sellout.db')
DEFAULT_FCST = os.path.join(BASE_DIR, 'dashboard', 'fcst_output.json')


def create_app(db_path=DEFAULT_DB, fcst_path=DEFAULT_FCST):
    app = Flask(__name__)

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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host='0.0.0.0', port=5050, debug=False)
