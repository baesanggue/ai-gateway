"""동시 접속 시험(#115)의 도구: 문장 만들기, 단계별 집계. 네트워크 없이."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_load as rl  # noqa: E402


def test_sentences_differ_after_masking():
    """가상 값을 지운 뒤에도(= 가린 뒤) 문장이 겹치지 않아야 판단 캐시를 쓰지 않는다. 가상 값은 문장 안에 있어야 한다."""
    for kind in ("mask", "raw", "free"):
        rest = set()
        for i in range(80):
            text, secrets = rl.sentence(kind, i)
            assert secrets and all(s in text for s in secrets)
            for s in secrets:
                text = text.replace(s, "[가림]")
            rest.add(text)
        assert len(rest) == 80, kind
    assert rl.sentence("faq", 3) == ("주문한 거 배송 언제 와요?", [])


def test_cross_check_finds_values_from_other_requests():
    """동시 요청끼리 대응표가 섞이면 내 답에 다른 요청의 번호가 보인다. 이름은 요청끼리 겹칠 수 있어 하나뿐인 값으로 본다."""
    texts = [rl.sentence("mask", i) for i in range(3)]
    rows = [{"_secrets": s, "_answer": a} for (_, s), a in zip(texts, [
        f"{texts[0][1][0]}님({texts[0][1][1]}) 죄송합니다",         # 자기 값만
        f"{texts[2][1][1]}로 연락드리겠습니다",                     # 2번 요청의 번호가 섞임
        ""])]                                                        # 답 없음(막힘·실패)
    rows.append({"_secrets": [], "_answer": "자주 묻는 질문 답"})
    rl.cross_check(rows)
    assert [(r["own"], r["mixed"]) for r in rows] == [(1, 0), (0, 1), ("", 0), ("", 0)]


def _row(level, status=200, secs=1.0, local="", cached="", cloud="", cost="0", leaked="0", kind="mask"):
    return {"level": level, "i": 0, "kind": kind, "status": status, "client_s": secs, "request_id": "r", "route": "cheap",
            "local_ms": local, "local_cached": cached, "cloud_ms": cloud, "server_ms": "", "cost_usd": cost,
            "leaked": leaked, "error": ""}


def test_level_stats_counts_failures_and_skips_cached_judgments():
    rows = [_row(5, secs=2.0, local="400", cached="0", cloud="300", cost="0.001"),
            _row(5, secs=130.0, local="0", cached="1"),            # 캐시를 쓴 판단은 지연 0으로 남는다 → 빼야 한다
            _row(5, status=502, secs=3.0, local="600", cached="0"),
            _row(5, status=0, secs=300.0),                          # 연결 실패
            _row(1, secs=1.0, local="100", cached="0")]
    s = rl.level_stats(rows, 5)
    assert (s["n"], s["ok"], s["failed"], s["over"]) == (4, 2, 2, 2)
    assert s["local"] == "0.50 / 0.60 / 0.60"                       # 400·600ms만, 캐시 0ms는 빠진다
    assert s["cloud"] == "0.30 / 0.30 / 0.30"
    assert abs(s["cost"] - 0.001) < 1e-9


def test_summary_writes_table_per_level(tmp_path):
    rows = [_row(1, kind="faq"), _row(30, kind="faq", secs=4.0), _row(30, kind="free", secs=8.0, leaked="1")]
    with (tmp_path / "requests.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rl.FIELDS)
        w.writeheader()
        w.writerows(rows)
    (tmp_path / "meta.json").write_text(json.dumps({"date": "d", "pc": {"cpu": "c", "ram_gb": "1", "os": "o"}}),
                                        encoding="utf-8")
    text = rl.summary(tmp_path)
    assert "| 1 | 1 | 1 | 0 | 0 |" in text and "| 30 | 2 | 2 | 0 | 0 |" in text
    assert "| free | 1 | cheap | 8.0 / 8.0 |" in text
    assert "가린 문장에 남은 가상 값: 1개" in text
    assert "다른 요청의 값이 섞인 답: 0개" in text
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == text
