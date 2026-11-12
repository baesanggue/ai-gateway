"""시연 화면의 "게이트웨이가 한 일" (#76): 요청 로그 한 줄을 다섯 단계로 바꾼다. 화면(Streamlit)과 나눠 시험할 수 있게 했다.

가리기 → 로컬 판단 → 클라우드 판단 → 경로 → 복원
- 단계마다 한 줄 요약(summary)만 바로 보이고, 확률·기준값·토큰·지연은 한 단계 아래(details)에 둔다 (11/09 사용자 의견:
  "당장 확인할 정보가 아니면 깊이를 늘린다", "흐름이 보이게")
- 예/아니오 판단은 신뢰도 대신 "확률 / 기준값"으로 보여 준다 (D24: 기준값이 0.5에서 멀면 신뢰도는 맞지 않는다)
- 로그에는 가명화된 문장만 있다. 원문은 시연 화면의 세션에만 있다 (설계 결정 2번)
"""
import html
import re
from dataclasses import dataclass, field

BOLD = re.compile(r"\*\*(\S(?:[^\n]*?\S)?)\*\*")   # 한 줄 안의 **굵게**. 공백으로 감싼 별표("2 ** 3")는 아니다


def answer_html(text: str) -> str:
    """받은 답변을 화면에 넣을 HTML로. 먼저 이스케이프해 답 속 HTML이 화면에서 동작하지 않게 하고, 그다음 굵게만 <b>로 바꾼다.
    LLM 답은 마크다운으로 오는데 글자 그대로 두면 시연에서 "**제목**"이 보인다 (#80 리허설)."""
    return BOLD.sub(r"<b>\1</b>", html.escape(text))

ROUTE_KO = {"template": "템플릿 답변", "cheap": "저렴한 모델", "premium": "고성능 모델", "block": "막음"}
REASON_KO = {
    "faq": "등록된 FAQ", "simple": "단순한 요청", "complex": "복잡한 요청", "faq_unregistered": "FAQ지만 답이 등록되지 않음",
    "routing_refused": "난이도 판단 거부 → 안전하게 고성능", "low_confidence": "판단이 애매함",
    "risky": "위험한 요청", "needs_raw": "개인정보의 실제 값이 필요", "refused": "안전한지 확인하지 못함",
    "off_topic": "업무와 무관", "confidential": "사내 기밀", "pii_residual": "가리지 못한 주소",
    "pii_unmasked": "가리지 못한 표기", "masking_unavailable": "가명화 기능 없음", "local_error": "보안 검사를 마치지 못함",
    "cloud_error": "요청 분석을 마치지 못함",
}
QUESTION_KO = {"is_confidential": "기밀", "needs_raw_value": "원본 값 필요", "is_risky": "위험", "has_pii": "개인정보",
               "difficulty": "난이도", "intent": "의도"}
VALUE_KO = {"simple": "단순", "complex": "복잡", "faq": "FAQ", "work": "업무", "off_topic": "무관"}
LOCAL_BLOCKS = {"confidential", "pii_residual", "local_error"}           # 로컬 단계에서 막힌 사유
BEFORE_JUDGE_BLOCKS = {"pii_unmasked", "pii_residual"}                    # 판단 전에 막힌 사유 (가리기 단계)


@dataclass
class Step:
    key: str                     # mask | local | cloud | route | restore
    title: str
    status: str                  # done | skipped | blocked
    summary: str                 # 바로 보이는 한 줄
    details: list[tuple[str, str]] = field(default_factory=list)   # 펼쳐야 보이는 것


def _counts(pii: dict[str, int]) -> str:
    return ", ".join(f"{k} {n}" for k, n in pii.items())


def _yes_no(j: dict, threshold: float | None) -> str:
    """예/아니오 판단: '아니오 (0.03 / 기준 0.10)'. 거부면 '판단 거부'."""
    if j.get("refused"):
        return "판단 거부"
    p = (j.get("probs") or {}).get("yes")
    answer = "예" if j.get("value") else "아니오"
    return f"{answer} ({p:.2f} / 기준 {threshold:.2f})" if p is not None and threshold is not None else answer


def _choice(j: dict) -> str:
    if j.get("refused"):
        return "판단 거부"
    conf = j.get("confidence")
    return f"{VALUE_KO.get(j.get('value'), j.get('value'))}" + (f" (신뢰도 {conf:.2f})" if conf is not None else "")


def _judge_details(judge: dict, thresholds: dict[str, float]) -> list[tuple[str, str]]:
    out = []
    for q, j in (judge.get("judgments") or {}).items():
        label = QUESTION_KO.get(q, q)
        if q in ("difficulty", "intent"):
            out.append((label, _choice(j)))
        else:
            out.append((label, _yes_no(j, thresholds.get(q))))
    if judge.get("parts", 1) and judge.get("parts", 1) > 1:
        out.append(("나눠 읽은 구간", f"{judge['parts']}개 (기억한 구간 {judge.get('cached_parts', 0)}개)"))
    if judge.get("latency_ms") is not None:
        out.append(("걸린 시간", f"{judge['latency_ms']:.0f}ms"))
    if judge.get("error"):
        out.append(("오류", judge["error"]))
    return out


def outbound_text(row: dict) -> str | None:
    """밖(클라우드 판단·LLM)으로 나간 글. 템플릿이거나 클라우드 전에 막혔으면 None (아무것도 나가지 않음)."""
    if "cloud" not in (row.get("judgments") or {}):
        return None
    return row.get("text_masked")


def build_steps(row: dict, thresholds: dict[str, float]) -> list[Step]:
    """요청 로그 한 줄(RequestLog.get) → 다섯 단계."""
    reasons = row.get("reasons") or []
    route = row.get("route")
    judgments = row.get("judgments") or {}
    pii = row.get("pii_counts") or {}
    blocked_at_mask = route == "block" and reasons and reasons[0] in BEFORE_JUDGE_BLOCKS and "local" not in judgments

    # ① 가리기
    mask_summary = f"개인정보 {sum(pii.values())}개를 가렸습니다 ({_counts(pii)})" if pii else "가릴 개인정보가 없습니다"
    if blocked_at_mask:
        mask_summary = f"{REASON_KO.get(reasons[0], reasons[0])} → 밖으로 보내지 않았습니다"
    mask_details = [("밖으로 나갈 문장", row.get("text_masked") or "(가리지 못한 값이 있어 로그에 남기지 않음)")]
    steps = [Step("mask", "가리기", "blocked" if blocked_at_mask else "done", mask_summary, mask_details)]

    # ② 로컬 판단 (이 PC)
    local = judgments.get("local")
    if local is None:
        why = "FAQ에 맞아 판단 없이 답했습니다" if route == "template" else "앞 단계에서 끝났습니다"
        steps.append(Step("local", "로컬 판단", "skipped", why))
    else:
        conf = (local.get("judgments") or {}).get("is_confidential")
        blocked = route == "block" and bool(set(reasons) & LOCAL_BLOCKS) and "cloud" not in judgments
        if local.get("error"):
            summary = "판단하지 못했습니다" + (" → 막았습니다 (엄격 수준)" if blocked else " → 기본 수준이라 진행합니다")
        elif blocked:
            summary = f"{REASON_KO.get(reasons[0], reasons[0])}로 보여 밖으로 보내지 않았습니다"
        else:
            summary = "기밀이 아닙니다" if conf and not conf.get("value") else "기밀로 보입니다 (기본 수준이라 진행)"
        steps.append(Step("local", "로컬 판단", "blocked" if blocked else "done", summary,
                          _judge_details(local, thresholds)))

    # ③ 클라우드 판단 (가명화된 문장만)
    cloud = judgments.get("cloud")
    if cloud is None:
        steps.append(Step("cloud", "클라우드 판단", "skipped",
                          "밖으로 보내지 않았습니다" if route == "block" or route == "template" else "부르지 않았습니다"))
    else:
        blocked = route == "block"
        if cloud.get("error"):
            summary = "판단하지 못했습니다"
        elif blocked:
            summary = " · ".join(REASON_KO.get(r, r) for r in reasons) + " → 막았습니다"
        else:
            j = cloud.get("judgments") or {}
            parts = []
            if "difficulty" in j and not j["difficulty"].get("refused"):
                parts.append(VALUE_KO.get(j["difficulty"].get("value"), "") + "한 요청")
            if "intent" in j and not j["intent"].get("refused"):
                parts.append(VALUE_KO.get(j["intent"].get("value"), ""))
            summary = " · ".join(p for p in parts if p) or "판단했습니다"
        steps.append(Step("cloud", "클라우드 판단", "blocked" if blocked else "done", summary,
                          _judge_details(cloud, thresholds)))

    # ④ 경로
    cost, base = row.get("cost_usd") or 0.0, row.get("baseline_cost_usd") or 0.0
    if route == "block":
        route_summary = f"막고 안내했습니다: {REASON_KO.get(reasons[0], reasons[0]) if reasons else ''}"
    elif route == "template":
        route_summary = "정해 둔 답으로 바로 답했습니다 · 비용 0"
    else:
        route_summary = f"{ROUTE_KO.get(route, route)}({row.get('model') or ''})으로 보냈습니다"
    route_details = [("이유", " · ".join(REASON_KO.get(r, r) for r in reasons) or "-"),
                     ("비용", f"${cost:.4f} (전부 고성능이었다면 ${base:.4f})")]
    if row.get("prompt_tokens") or row.get("completion_tokens"):
        route_details.append(("토큰", f"입력 {row.get('prompt_tokens', 0)} · 출력 {row.get('completion_tokens', 0)}"))
    if row.get("total_latency_ms") is not None:
        route_details.append(("전체 시간", f"{row['total_latency_ms'] / 1000:.1f}초"))
    steps.append(Step("route", "경로", "blocked" if route == "block" else "done", route_summary, route_details))

    # ⑤ 복원
    if route in ("cheap", "premium"):
        unrestored = row.get("unrestored_tokens") or 0
        summary = (f"답변의 가명 토큰을 원래 값으로 되돌렸습니다" if pii else "되돌릴 토큰이 없습니다")
        if unrestored:
            summary += f" (되돌리지 못한 토큰 {unrestored}개)"
        steps.append(Step("restore", "복원", "done", summary))
    else:
        steps.append(Step("restore", "복원", "skipped", "LLM을 부르지 않았습니다"))
    return steps


def session_totals(rows: list[dict]) -> tuple[float, float]:
    """시연에서 보낸 요청들의 (게이트웨이 비용, 전부 고성능이었다면). 판단 API 비용은 넣지 않는다 (요청당 $0.0001 안팎)."""
    return sum(r.get("cost_usd") or 0.0 for r in rows), sum(r.get("baseline_cost_usd") or 0.0 for r in rows)
