"""가명화 부품 (#12에서 자리를 만들고, #44에서 규칙 가명화 RuleMasker를 기본으로 끼웠다).

파이프라인은 Masker만 알고 구현은 모른다. 그래서 M2에서 부품을 바꿔 끼우면 흐름은 그대로다.
- mask(messages): 밖으로 나갈 메시지 전체를 가명화한다 (#37: 도구 결과·대화 이력 포함).
  복원 대응표(mapping)는 요청 처리 중 메모리에만 있다 (DB·로그·외부 저장 금지)
- restore(text, mapping): LLM 답변의 가명 토큰을 원래 값으로 되돌린다
- available: False면 가명화를 할 수 없는 부품이다. 파이프라인은 개인정보가 보이거나 확인할 수 없는 요청을
  밖으로 보내지 않고 막는다 (routing.policy.decide_without_masking)
"""
import re
from dataclasses import dataclass, field
from typing import Protocol

from gateway.config import MaskingConfig
from gateway.masking.dictionary import Entry
from gateway.masking.dictionary import load as load_dictionary
from gateway.masking.patterns import PATTERNS, compat_copy, find_pii, find_pii_compat, is_date, normalize_text


@dataclass
class MaskResult:
    messages: list[dict[str, str]]                         # 가명화된 메시지 ({"role", "content"})
    mapping: dict[str, str] = field(default_factory=dict)  # 가명 토큰 → 원래 값. 메모리에만 둔다
    counts: dict[str, int] = field(default_factory=dict)   # 개인정보 종류별 개수 (로그용, 값 없음)
    # 가린 뒤에도 남은 개인정보 종류. 정규화로도 못 맞춘 표기(동그라미 숫자 등)다. 있으면 파이프라인이 막는다
    unmasked: list[str] = field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return bool(self.counts)


class Masker(Protocol):
    available: bool

    def mask(self, messages: list[dict[str, str]]) -> MaskResult: ...

    def restore(self, text: str, mapping: dict[str, str]) -> str: ...


class UnavailableMasker:
    """M1용: 가명화가 아직 없다. 메시지는 그대로 두고, 정규식으로 보이는 개인정보 종류만 센다.
    available=False라서 파이프라인이 개인정보 요청을 막는다. 원본이 밖으로 나가지 않게 하는 임시 장치다."""

    available = False

    def mask(self, messages: list[dict[str, str]]) -> MaskResult:
        counts: dict[str, int] = {}
        for m in messages:
            for kind in find_pii(normalize_text(m["content"])):
                counts[kind] = counts.get(kind, 0) + 1
        return MaskResult([dict(m) for m in messages], {}, counts)

    def restore(self, text: str, mapping: dict[str, str]) -> str:
        return text


TOKEN = re.compile(r"\[([^\[\]\d]+)(\d+)\]")   # [고객1], [전화번호2]


class RuleMasker:
    """규칙 가명화 (#44): 사용자 사전 + 정규식으로 찾은 값을 [종류N] 토큰으로 바꾼다.

    - 사전 먼저(긴 값부터: "김민수"보다 "김민수진"을 먼저), 그다음 정규식(patterns.PATTERNS 순서)
    - 같은 값 = 같은 토큰. 한 요청 안에서는 메시지가 달라도 같다
    - 메시지 전체를 가린다(#37): system·user·assistant·tool. 복원된 답변이 다음 요청의 이력으로 돌아오고,
      도구 결과에는 회사 DB의 고객 정보가 들어 있다
    - 입력에 이미 [고객1] 같은 글자가 있으면 그 번호는 건너뛴다. 복원할 때 사용자가 쓴 글자를 다른 값으로 바꾸지 않게
    - 이름이 다른 낱말 안에 들어 있어도 가린다(한국어는 조사가 붙어 낱말 경계를 정하기 어렵다). 사전에는 두 글자 이상만
    - 가리기 전에 표기를 맞춘다(patterns.normalize_text: NFC, 전각 → 반각, 대시 → 하이픈). 밖으로 나가는 문장도
      맞춘 표기다. 사전은 영문 대소문자를 구분하지 않는다 ("x-프로젝트"도 가린다)
    """

    available = True

    def __init__(self, entries: list[Entry], dictionary_source: str = "custom"):
        self.dictionary_source = dictionary_source     # "custom"(회사 사전) | "example"(시연용 사전 복사본) → /healthz
        # 사전 값 전체를 정규식 하나로: 긴 값부터(같은 위치에서 "김민수진"이 "김민수"보다 먼저), 대소문자 무시
        values = sorted({normalize_text(e.value) for e in entries}, key=len, reverse=True)
        self.kinds = {}
        for e in entries:
            self.kinds.setdefault(normalize_text(e.value).casefold(), e.kind)
        self.names = re.compile("|".join(map(re.escape, values)), re.IGNORECASE) if values else None
        # 마지막 검사용: 사전 값도 검사용 사본과 같은 방식으로 바꿔 둔다 (수학 글꼴 "𝐗-프로젝트" 같은 표기를 잡는다)
        compat = sorted({compat_copy(v) for v in values}, key=len, reverse=True)
        self.names_compat = re.compile("|".join(map(re.escape, compat)), re.IGNORECASE) if compat else None

    @classmethod
    def from_config(cls, cfg: MaskingConfig) -> "RuleMasker":
        entries, _, is_example = load_dictionary(cfg)    # 사전이 없으면 DictionaryMissing (시작하지 않는다, D21)
        return cls(entries, "example" if is_example else "custom")

    def mask(self, messages: list[dict[str, str]]) -> MaskResult:
        taken = {m.group() for msg in messages for m in TOKEN.finditer(normalize_text(msg["content"]))}
        mapping: dict[str, str] = {}
        by_value: dict[str, str] = {}
        counts: dict[str, int] = {}
        last_no: dict[str, int] = {}

        def token(kind: str, value: str) -> str:
            key = value.casefold()          # "X-프로젝트"와 "x-프로젝트"는 같은 값 → 같은 토큰 (처음 나온 표기로 복원)
            if key not in by_value:
                n = last_no.get(kind, 0) + 1
                while f"[{kind}{n}]" in taken:
                    n += 1
                last_no[kind] = n
                by_value[key] = f"[{kind}{n}]"
                mapping[by_value[key]] = value
                counts[kind] = counts.get(kind, 0) + 1
            return by_value[key]

        out = []
        for msg in messages:
            text = normalize_text(msg["content"])
            if self.names is not None:
                text = self.names.sub(lambda m: token(self._kind(m.group()), m.group()), text)
            for kind, pattern in PATTERNS:
                text = pattern.sub(lambda m, k=kind: m.group() if is_date(k, m.group()) else token(k, m.group()), text)
            out.append({**msg, "content": text})
        # 마지막 검사: 검사용 사본(NFKC, 결합 표시 제거)에 형식 있는 개인정보나 사전 값이 남았으면 unmasked
        unmasked = []
        for msg in out:
            copy = compat_copy(msg["content"])
            unmasked += find_pii(copy)
            if self.names_compat is not None:
                unmasked += [self._kind(m.group()) for m in self.names_compat.finditer(copy)]
        return MaskResult(out, mapping, counts, unmasked)

    def _kind(self, matched: str) -> str:
        """사전에서 찾은 값의 종류. 정규식의 대소문자 무시와 casefold()는 몇몇 특수 문자에서 규칙이 달라
        조회가 빗나갈 수 있다. 그때도 값은 가려야 하므로 오류 대신 하나씩 다시 맞춰 보고, 그래도 없으면 고객으로 둔다."""
        kind = self.kinds.get(matched.casefold())
        if kind is None:
            kind = next((k for v, k in self.kinds.items() if re.fullmatch(re.escape(v), matched, re.IGNORECASE)), "고객")
        return kind

    def restore(self, text: str, mapping: dict[str, str]) -> str:
        """가명 토큰을 원래 값으로. 조사 교정·바꿔 쓴 토큰 처리는 #45에서 더한다."""
        return TOKEN.sub(lambda m: mapping.get(m.group(), m.group()), text)
