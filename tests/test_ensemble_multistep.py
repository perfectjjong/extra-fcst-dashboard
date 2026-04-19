import sys, json, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from model.ensemble import build_fcst_output
from model.train_lgbm import train_and_predict, forecast_multistep

DB_PATH = str(Path(__file__).parent.parent / 'data' / 'sellout.db')
MODELS_DIR = str(Path(__file__).parent.parent / 'model' / 'models')


def test_build_fcst_output_includes_multistep():
    """JSON 출력에 long_range_forecasts 키가 있어야 한다."""
    lgbm_results = train_and_predict(DB_PATH, MODELS_DIR)
    multistep = forecast_multistep(DB_PATH, MODELS_DIR, start_week_num=17, n_weeks=3)
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        out_path = f.name
    try:
        build_fcst_output(lgbm_results, DB_PATH, out_path, multistep_results=multistep)
        with open(out_path) as f:
            data = json.load(f)
        assert 'long_range_forecasts' in data, "long_range_forecasts 키 없음"
        assert len(data['long_range_forecasts']) > 0
    finally:
        os.unlink(out_path)


def test_build_fcst_output_includes_scenarios():
    """JSON 출력에 scenarios 키가 있어야 한다."""
    lgbm_results = train_and_predict(DB_PATH, MODELS_DIR)
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        out_path = f.name
    try:
        build_fcst_output(lgbm_results, DB_PATH, out_path)
        with open(out_path) as f:
            data = json.load(f)
        assert 'scenarios' in data, "scenarios 키 없음"
        assert 'promo_10' in data['scenarios'], "promo_10 시나리오 없음"
        assert 'promo_20' in data['scenarios'], "promo_20 시나리오 없음"
    finally:
        os.unlink(out_path)
