"""요청 로그 검사 (#11). 임시 폴더의 SQLite 파일에 저장한다."""
import threading

import pytest

from gateway.decision.service import JudgeResult, Judgment
from gateway.storage.log import RequestLog, RequestRecord, judge_summary

# 시연 4번 (가상 값)
ORIGINAL = "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘"
MASKED = "[고객1] 고객([전화번호1])에게 배송 지연 사과 메일 써줘"


@pytest.fixture
def log(tmp_path):
    return RequestLog(tmp_path / "gateway.db")


def record(**kw):
    base = dict(security_level="basic", text_masked=MASKED, route="cheap", reasons=["simple"],
                pii_counts={"고객": 1, "전화번호": 1})
    return RequestRecord(**(base | kw))


def db_bytes(log):
    """DB와, 아직 합쳐지지 않은 WAL·공유 메모리 파일까지 전부 읽는다."""
    return b"".join(p.read_bytes() for p in log.path.parent.glob(log.path.name + "*"))


def test_save_and_get_roundtrip(log):
    cloud = JudgeResult("cloud", "gpt-6-luna", {
        "is_risky": Judgment("is_risky", {"yes": 0.01}, False, 0.98),
        "difficulty": Judgment("difficulty", None, None, None),                 # 판단 거부
    }, latency_ms=812.34, input_tokens=171)
    local = JudgeResult("local", "gliclass:v4-edge", error="ConnectError")    # 엔진 오류
    rid = log.save(record(judgments={"local": judge_summary(local), "cloud": judge_summary(cloud)},
                          tier="cheap", model="gpt-6-luna", prompt_tokens=122, completion_tokens=120,
                          cost_usd=7.2e-05, baseline_cost_usd=0.001444, decision_input_tokens=171,
                          decision_latency_ms=990.0, total_latency_ms=3210.5))
    r = log.get(rid)
    assert (r["route"], r["reasons"], r["tier"], r["model"]) == ("cheap", ["simple"], "cheap", "gpt-6-luna")
    assert r["text_masked"] == MASKED and r["text_withheld"] is False
    assert r["pii_counts"] == {"고객": 1, "전화번호": 1}
    c = r["judgments"]["cloud"]
    assert c["judgments"]["is_risky"] == {"value": False, "confidence": 0.98, "refused": False, "probs": {"yes": 0.01}}
    assert c["judgments"]["difficulty"]["refused"] is True
    assert (c["latency_ms"], c["input_tokens"]) == (812.3, 171)
    assert r["judgments"]["local"]["error"] == "ConnectError"
    assert r["created_at"].endswith("+00:00")


def test_original_values_never_reach_the_db_files(log):
    log.save(record())                                      # 정상: 가명화된 문장
    log.save(record(text_masked=ORIGINAL))                  # 실수로 원문이 들어온 경우
    data = db_bytes(log)
    for value in ("010-1234-5678", "김민수"):
        assert value.encode() not in data
    assert MASKED.encode() in data                          # 가명화된 문장은 남는다 (검사가 제대로 도는지)


@pytest.mark.parametrize("text", [
    "900101-1234567로 나이 계산해 줘",
    "카드 4111-1111-1111-1111 승인 실패 안내",
    "jiwoo.han@example.com 으로 보내 줘",
    "환불 계좌 110-123-456789",
    ORIGINAL,
])
def test_text_with_structured_pii_is_withheld(log, text):
    r = log.get(log.save(record(text_masked=text)))
    assert r["text_masked"] is None and r["text_withheld"] is True
    assert r["route"] == "cheap"                            # 문장만 빼고 나머지는 남는다


@pytest.mark.parametrize("text", [MASKED, "[주민번호1]로 나이 계산해 줘", "2026-11-03 회의록 정리해 줘"])
def test_masked_tokens_and_dates_are_kept(log, text):
    assert log.get(log.save(record(text_masked=text)))["text_masked"] == text


def test_totals_feed_the_cost_counter(log):
    log.save(record(route="template", reasons=["faq"], cost_usd=0.0, baseline_cost_usd=0.00177))
    log.save(record(cost_usd=0.00007, baseline_cost_usd=0.00144, decision_input_tokens=171))
    log.save(record(route="block", reasons=["risky"], baseline_cost_usd=0.0, decision_input_tokens=150))
    t = log.totals()
    assert t["requests"] == 3 and t["by_route"] == {"template": 1, "cheap": 1, "block": 1}
    assert t["saved_usd"] == pytest.approx(0.00177 + 0.00144 - 0.00007)
    assert t["decision_input_tokens"] == 321


def test_recent_returns_newest_first(log):
    ids = [log.save(record(created_at=f"2026-11-01T00:00:0{i}.000+00:00")) for i in range(3)]
    assert [r["id"] for r in log.recent(2)] == [ids[2], ids[1]]


def test_wal_mode_and_concurrent_writes(log):
    """FastAPI는 요청을 여러 스레드에서 처리한다. 동시에 저장해도 빠지는 줄이 없어야 한다."""
    def worker():
        for _ in range(25):
            log.save(record())
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert log.totals()["requests"] == 100
    with log._db() as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_empty_log_totals(log):
    t = log.totals()
    assert (t["requests"], t["saved_usd"], t["by_route"]) == (0, 0, {})
