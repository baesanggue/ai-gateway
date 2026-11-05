"""요청 로그 (SQLite, CLAUDE.md §5, #11). 민감정보 원본은 저장하지 않는다 (설계 결정 2번).

- 요청 문장은 가명화된 문장(text_masked)만 받는다. 저장 직전에 형식 있는 원본(전화번호·주민번호 등)이
  남아 있는지 한 번 더 보고, 남아 있으면 문장은 저장하지 않고 text_withheld=1만 남긴다.
  로그 때문에 사용자 요청이 실패하면 안 되므로 예외를 내지 않는다. 이름은 정규식으로 못 잡는 보조 장치다
- 개인정보는 종류·개수만 남긴다(pii_counts). LLM 답변은 저장하지 않는다. 복원된 답변에는 원본이 들어 있다
- WAL 모드: 게이트웨이(여러 스레드)가 쓰는 동안 관리 화면(Streamlit)이 읽는다. 저장할 때마다 연결을 새로 연다
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gateway.config import ROOT, StorageConfig
from gateway.decision.service import JudgeResult
from gateway.masking.patterns import find_pii_compat

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,              -- UTC ISO 8601
    security_level TEXT NOT NULL,
    text_masked TEXT,                      -- 가명화된 요청. 원본이 의심되면 NULL
    text_withheld INTEGER NOT NULL,        -- 1이면 원본 의심으로 문장을 저장하지 않음
    pii_counts TEXT NOT NULL,              -- JSON {"전화번호": 1, "고객": 1}. 값은 없다
    route TEXT NOT NULL,                   -- template | cheap | premium | block
    reasons TEXT NOT NULL,                 -- JSON 사유 코드 목록 (차단이면 걸린 사유 전부)
    faq_id TEXT,
    judgments TEXT NOT NULL,               -- JSON {"local": …, "cloud": …} (judge_summary)
    tier TEXT,
    model TEXT,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,    -- 추론 토큰 포함
    reasoning_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,                -- 실제 LLM 비용
    baseline_cost_usd REAL NOT NULL,       -- "전부 고성능이었다면" 비용 (D18)
    decision_input_tokens INTEGER NOT NULL, -- 클라우드 판단 입력 토큰 (판단 비용 근거)
    decision_latency_ms REAL NOT NULL,
    total_latency_ms REAL NOT NULL,
    llm_error TEXT,                        -- LLMError 메시지 (모델·상태 코드·오류 code만 들어 있다)
    unrestored_tokens INTEGER NOT NULL DEFAULT 0  -- 답변에서 복원하지 못한 가명 토큰 수 (#45)
);
CREATE INDEX IF NOT EXISTS requests_created_at ON requests (created_at);
"""
JSON_COLUMNS = ("pii_counts", "reasons", "judgments")


@dataclass
class RequestRecord:
    """요청 1건의 로그. 파이프라인(#12)이 판단·라우팅·LLM 결과로 채운다."""
    security_level: str
    text_masked: str
    route: str
    reasons: list[str]
    pii_counts: dict[str, int] = field(default_factory=dict)
    faq_id: str | None = None
    judgments: dict[str, Any] = field(default_factory=dict)
    tier: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    baseline_cost_usd: float = 0.0
    decision_input_tokens: int = 0
    decision_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    llm_error: str | None = None
    # 파이프라인이 "가리지 못한 개인정보가 남았다"(pii_unmasked)고 알리면 문장을 저장하지 않는다.
    # 회사별 식별번호처럼 이 모듈의 정규식이 모르는 값도 있어서, 파이프라인의 판단을 그대로 따른다 (D22)
    withhold_text: bool = False
    unrestored_tokens: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))


def judge_summary(result: JudgeResult) -> dict[str, Any]:
    """판단 결과를 로그용 dict로: 질문별 값·신뢰도·확률·거부 여부, 엔진 오류, 지연."""
    return {"engine": result.engine, "model": result.model, "latency_ms": round(result.latency_ms, 1),
            "input_tokens": result.input_tokens, "error": result.error, "cached": result.cached,
            "judgments": {q: {"value": j.value, "confidence": j.confidence, "refused": j.refused, "probs": j.probs}
                          for q, j in result.judgments.items()}}


class RequestLog:
    def __init__(self, path: str | Path):
        path = Path(path)
        self.path = path if path.is_absolute() else ROOT / path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            # #45 전에 만든 DB 파일에는 unrestored_tokens 열이 없다. 있는 로그는 그대로 두고 열만 더한다
            cols = {r["name"] for r in con.execute("PRAGMA table_info(requests)")}
            if "unrestored_tokens" not in cols:
                con.execute("ALTER TABLE requests ADD COLUMN unrestored_tokens INTEGER NOT NULL DEFAULT 0")

    @classmethod
    def from_config(cls, cfg: StorageConfig) -> "RequestLog":
        return cls(cfg.sqlite_path)

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            with con:            # 끝나면 commit, 예외면 rollback
                yield con
        finally:
            con.close()          # sqlite3의 with는 연결을 닫지 않는다

    def save(self, record: RequestRecord) -> str:
        row = asdict(record)
        withhold = row.pop("withhold_text")         # 열이 아니라 저장 지시다
        # NFKC 사본으로 본다: 동그라미 숫자처럼 가리지 못한 표기의 번호도 로그에 남기지 않는다
        row["text_withheld"] = int(withhold or bool(find_pii_compat(record.text_masked)))
        if row["text_withheld"]:
            row["text_masked"] = None
        for key in JSON_COLUMNS:
            row[key] = json.dumps(row[key], ensure_ascii=False)
        # 열 이름은 RequestRecord 필드에서 오므로 외부 입력이 SQL에 섞이지 않는다. 값은 자리표시로 넘긴다
        with self._db() as con:
            con.execute(f"INSERT INTO requests ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})", row)
        return record.id

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._db() as con:
            r = con.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return _decode(r) if r else None

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """최근 요청부터 (관리 화면용)."""
        with self._db() as con:
            rows = con.execute("SELECT * FROM requests ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_decode(r) for r in rows]

    def totals(self) -> dict[str, Any]:
        """누적 집계. 비용 카운터(시연 6번)는 baseline_cost_usd − cost_usd를 보여 준다."""
        with self._db() as con:
            t = con.execute("""SELECT COUNT(*) AS requests, COALESCE(SUM(cost_usd), 0) AS cost_usd,
                                      COALESCE(SUM(baseline_cost_usd), 0) AS baseline_cost_usd,
                                      COALESCE(SUM(decision_input_tokens), 0) AS decision_input_tokens,
                                      COALESCE(SUM(text_withheld), 0) AS text_withheld
                               FROM requests""").fetchone()
            by_route = dict(con.execute("SELECT route, COUNT(*) FROM requests GROUP BY route").fetchall())
        out = dict(t)
        out["saved_usd"] = out["baseline_cost_usd"] - out["cost_usd"]
        out["by_route"] = by_route
        return out


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in JSON_COLUMNS:
        d[key] = json.loads(d[key])
    d["text_withheld"] = bool(d["text_withheld"])
    return d
