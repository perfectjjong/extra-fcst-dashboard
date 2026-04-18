import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pipeline.init_db import init_db


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test.db")
    fcst_path = str(tmp_path / "fcst_output.json")
    init_db(db_path)
    with open(fcst_path, 'w') as f:
        json.dump({'forecasts': [{'model': 'AM182C', 'predicted': 100}]}, f)
    os.environ['FCST_DB_PATH'] = db_path
    os.environ['FCST_OUTPUT_PATH'] = fcst_path
    from api.server import create_app
    app = create_app(db_path, fcst_path)
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_post_request_saves_to_db(client, tmp_path):
    resp = client.post('/api/request', json={
        'model': 'AM182C',
        'date_from': '2026-01-01',
        'date_to': '2026-04-01',
        'requester': 'testuser'
    })
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['status'] == 'ok'


def test_post_feedback_saves_to_db(client):
    resp = client.post('/api/feedback', json={
        'model': 'AM182C',
        'week': 'W17',
        'predicted': 100.0,
        'actual': 115.0,
        'note': 'promo week'
    })
    assert resp.status_code == 200


def test_get_fcst_returns_json(client):
    resp = client.get('/api/fcst')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert 'forecasts' in data
