"""신뢰도 보정 측정기(#60). 기록된 클라우드 판단을 다시 넣어 경로를 계산하는 규칙과 정확도 집계를 고정한다 (네트워크 없이)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_calibration as rc  # noqa: E402

from gateway.config import load_settings  # noqa: E402


def logged(difficulty_conf, intent="work", intent_conf=0.9, route="cheap"):
    """게이트웨이 로그의 판단 요약 모양 (judge_summary)."""
    cloud = {"engine": "cloud", "model": "m", "error": None, "judgments": {
        "is_risky": {"value": False, "confidence": 0.98, "refused": False, "probs": {"yes": 0.01}},
        "difficulty": {"value": "simple", "confidence": difficulty_conf, "refused": False,
                       "probs": {"simple": 0.9, "complex": 0.1}},
        "intent": {"value": intent, "confidence": intent_conf, "refused": False,
                   "probs": {"faq": 0.1, "service": 0.5, "task": 0.3, "off_topic": 0.1}}}}
    return {"route": route, "reasons": "simple", "j": {"cloud": cloud},
            "truth": {"difficulty": "simple", "intent": "work"}, "label": {"expected_route": "cheap"}}


def test_low_confidence_goes_premium_only_when_a_threshold_is_set(tmp_path):
    cfg = load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env").config
    rows = [logged(0.3), logged(0.9)]
    assert rc.simulate(rows, None, cfg) == ["cheap", "cheap"]
    assert rc.simulate(rows, 0.5, cfg) == ["premium", "cheap"]      # low_confidence_route = premium


def test_template_rows_without_cloud_judgment_keep_their_route(tmp_path):
    cfg = load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env").config
    assert rc.simulate([{"route": "template", "j": {}}], 0.9, cfg) == ["template"]


def test_judgments_skip_refused_and_unlabeled():
    rows = [logged(0.3), logged(0.9)]
    rows[1]["j"]["cloud"]["judgments"]["difficulty"]["refused"] = True
    rows.append(dict(logged(0.5), truth={"difficulty": None}))
    assert rc.judgments(rows, "difficulty") == [(0.3, True)]
    assert json.dumps(rc.judgments(rows, "intent"))                    # 의도는 두 행 모두 (정답 있음)
