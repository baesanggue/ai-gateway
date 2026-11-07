"""가명화 부품 (#12에서 자리를 만들고, #44에서 규칙 가명화 RuleMasker를 기본으로 끼웠다).

파이프라인은 Masker만 알고 구현은 모른다. 그래서 M2에서 부품을 바꿔 끼우면 흐름은 그대로다.
- mask(messages): 밖으로 나갈 메시지 전체를 가명화한다 (#37: 도구 결과·대화 이력 포함).
  복원 대응표(mapping)는 요청 처리 중 메모리에만 있다 (DB·로그·외부 저장 금지)
- restore(text, mapping): LLM 답변의 가명 토큰을 원래 값으로 되돌린다 (조사 교정, 복원 못 한 토큰 수 → restore.py, #45)
- available: False면 가명화를 할 수 없는 부품이다. 파이프라인은 개인정보가 보이거나 확인할 수 없는 요청을
  밖으로 보내지 않고 막는다 (routing.policy.decide_without_masking)
- 사전에 없는 이름은 이름 인식 모델이 찾아 [이름1]로 가린다 (#53, D23). 모델은 위치만 찾고 바꾸기는 여기 코드가 한다
"""
import re
from dataclasses import dataclass, field
from typing import Protocol

from gateway.config import MaskingConfig
from gateway.masking.dictionary import Entry
from gateway.masking.dictionary import load as load_dictionary
from gateway.masking.ner import NerModel
from gateway.masking.ner import load as load_ner
from gateway.masking.patterns import PATTERNS, compat_copy, find_pii, find_pii_compat, is_date, normalize_text
from gateway.masking.restore import Restored
from gateway.masking.restore import restore as restore_tokens


@dataclass
class MaskResult:
    messages: list[dict[str, str]]                         # 가명화된 메시지 ({"role", "content"})
    mapping: dict[str, str] = field(default_factory=dict)  # 가명 토큰 → 원래 값. 메모리에만 둔다
    counts: dict[str, int] = field(default_factory=dict)   # 개인정보 종류별 개수 (로그용, 값 없음)
    # 가린 뒤에도 남은 개인정보 종류. 정규화로도 못 맞춘 표기(동그라미 숫자 등)다. 있으면 파이프라인이 막는다
    unmasked: list[str] = field(default_factory=list)
    # 가린 뒤에도 남았을 수 있는 개인정보 종류. 이름 인식 모델이 주소로 본 곳이 가린 뒤에도 그대로 있다
    # (시·도 없이 쓴 주소 등, 정규식이 못 맞춤). 엄격 수준은 막고, 어느 수준이든 로그에 문장을 남기지 않는다 (D23)
    suspected: list[str] = field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return bool(self.counts)


class Masker(Protocol):
    available: bool

    def mask(self, messages: list[dict[str, str]]) -> MaskResult: ...

    def restore(self, text: str, mapping: dict[str, str]) -> Restored: ...


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

    def restore(self, text: str, mapping: dict[str, str]) -> Restored:
        return Restored(text)


LOOSE_TOKEN = re.compile(r"([^\W\d_]+)\s?(\d+)(?!\d)")   # 고객1, 고객 1 (괄호 종류와 관계없이)
TOKEN_SHAPE = r"\[[^\W\d_]+\d+\]"                          # [고객1]
NAME_KIND = "이름"        # 사전에 없는 이름의 토큰 종류 [이름1]. 사전에 등록된 이름은 사전의 종류([고객1])를 쓴다
ADDRESS_KIND = "주소"


class RuleMasker:
    """규칙 가명화 (#44): 사용자 사전 + 정규식으로 찾은 값을 [종류N] 토큰으로 바꾼다.

    - 사전 먼저(긴 값부터: "김민수"보다 "김민수진"을 먼저), 그다음 정규식(patterns.PATTERNS 순서)
    - 같은 값 = 같은 토큰. 한 요청 안에서는 메시지가 달라도 같다
    - 메시지 전체를 가린다(#37): system·user·assistant·tool. 복원된 답변이 다음 요청의 이력으로 돌아오고,
      도구 결과에는 회사 DB의 고객 정보가 들어 있다
    - 입력에 이미 [고객1]·[고객 1]·고객1 같은 글자가 있으면 그 번호는 건너뛴다. 복원할 때 사용자가 쓴 글자를 다른 값으로 바꾸지 않게
    - 이름이 다른 낱말 안에 들어 있어도 가린다(한국어는 조사가 붙어 낱말 경계를 정하기 어렵다). 사전에는 두 글자 이상만
    - 가리기 전에 표기를 맞춘다(patterns.normalize_text: NFC, 전각 → 반각, 대시 → 하이픈). 밖으로 나가는 문장도
      맞춘 표기다. 사전은 영문 대소문자를 구분하지 않는다 ("x-프로젝트"도 가린다)
    - 이름 인식 모델(#53, D23): 요청 전체를 한 번에 읽혀 사람 이름을 찾고, 사전 다음에 값 단위로 [이름N]으로 가린다.
      모델은 토큰이 끼기 전의 원문을 본다. 두 글자 이상, 숫자 없는 것만(사전과 같은 규칙). 주소는 가리지 않고
      가린 뒤에도 남았는지만 본다(suspected). 주소 경계가 모델마다 달라 반쪽만 가리면 나머지가 그대로 나간다
    """

    available = True

    def __init__(self, entries: list[Entry], dictionary_source: str = "custom",
                 custom_patterns: list[tuple[str, re.Pattern[str]]] = (), ner: NerModel | None = None):
        self.dictionary_source = dictionary_source     # "custom"(회사 사전) | "example"(시연용 사전 복사본) → /healthz
        self.ner = ner                                 # 이름 인식 모델. 게이트웨이는 늘 있다(없으면 시작하지 않음, D23)
        # 회사별 식별번호 (#38, 업종 프리셋): (토큰 종류, 정규식). 기본 정규식보다 먼저 본다 (더 구체적이다)
        self.custom_patterns = list(custom_patterns)
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
        custom = [(p.kind, re.compile(p.pattern)) for p in cfg.custom_patterns]   # 설정에서 이미 검증했다
        ner = load_ner(cfg.ner)                          # 모델 파일이 없거나 해시가 다르면 NerModelError (D23)
        return cls(entries, "example" if is_example else "custom", custom, ner)

    @property
    def ner_source(self) -> str | None:
        """/healthz에 보이는 이름 인식 모델 파일."""
        return self.ner.onnx_file if self.ner is not None else None

    def mask(self, messages: list[dict[str, str]]) -> MaskResult:
        # 입력에 이미 있는 토큰 모양의 번호는 쓰지 않는다. 복원은 바꿔 쓴 모양([고객 1], 고객1님)도 되돌리므로 그 모양도 센다
        taken = {f"[{m.group(1)}{m.group(2)}]" for msg in messages
                 for m in LOOSE_TOKEN.finditer(normalize_text(msg["content"]))}
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

        texts = [normalize_text(msg["content"]) for msg in messages]
        names, addresses = self._find(texts)
        found = re.compile("|".join(map(re.escape, names)), re.IGNORECASE) if names else None
        # 사전이 만든 토큰([고객1])은 건드리지 않는다. 입력에 원래 있던 토큰 모양 글자 안의 이름은 가린다
        skip = re.compile(rf"{TOKEN_SHAPE}|{found.pattern}", re.IGNORECASE) if found else None

        def name_token(m: re.Match) -> str:
            v = m.group()
            if not v.startswith("["):
                return token(self.kinds.get(v.casefold(), NAME_KIND), v)
            return v if v in mapping else found.sub(name_token, v)

        out = []
        for msg, text in zip(messages, texts):
            if self.names is not None:
                text = self.names.sub(lambda m: token(self._kind(m.group()), m.group()), text)
            if skip is not None:
                text = skip.sub(name_token, text)
            for kind, pattern in self.custom_patterns:
                text = pattern.sub(lambda m, k=kind: token(k, m.group()), text)
            for kind, pattern in PATTERNS:
                text = pattern.sub(lambda m, k=kind: m.group() if is_date(k, m.group()) else token(k, m.group()), text)
            out.append({**msg, "content": text})
        # 마지막 검사: 검사용 사본(NFKC, 결합 표시 제거)에 형식 있는 개인정보나 사전 값이 남았으면 unmasked
        unmasked = []
        for msg in out:
            copy = compat_copy(msg["content"])
            unmasked += find_pii(copy)
            unmasked += [kind for kind, pattern in self.custom_patterns for _ in pattern.finditer(copy)]
            if self.names_compat is not None:
                unmasked += [self._kind(m.group()) for m in self.names_compat.finditer(copy)]
        joined = "\n".join(msg["content"] for msg in out)
        suspected = [ADDRESS_KIND for a in addresses if a in joined]
        return MaskResult(out, mapping, counts, unmasked, suspected)

    def _find(self, texts: list[str]) -> tuple[list[str], list[str]]:
        """이름 인식 모델로 (이름들, 주소들). 메시지 전체를 이어 한 번에 읽힌다(파일은 칸이 수천 개다).
        이어 붙인 줄바꿈을 넘는 조각은 줄마다 나눈다. 긴 값부터 (같은 위치에서 "김민수진"이 "김민수"보다 먼저)."""
        if self.ner is None:
            return [], []
        names, addresses = [], []
        for span in self.ner.find("\n".join(texts), {"NAME", "ADDRESS"}):
            for piece in span.text.split("\n"):
                piece = piece.strip()
                if len(piece) < 2 or "[" in piece or "]" in piece:
                    continue
                if span.label == "ADDRESS":
                    addresses.append(piece)
                elif not any(c.isdigit() for c in piece):
                    names.append(piece)
        return sorted(dict.fromkeys(names), key=len, reverse=True), list(dict.fromkeys(addresses))

    def _kind(self, matched: str) -> str:
        """사전에서 찾은 값의 종류. 정규식의 대소문자 무시와 casefold()는 몇몇 특수 문자에서 규칙이 달라
        조회가 빗나갈 수 있다. 그때도 값은 가려야 하므로 오류 대신 하나씩 다시 맞춰 보고, 그래도 없으면 고객으로 둔다."""
        kind = self.kinds.get(matched.casefold())
        if kind is None:
            kind = next((k for v, k in self.kinds.items() if re.fullmatch(re.escape(v), matched, re.IGNORECASE)), "고객")
        return kind

    def restore(self, text: str, mapping: dict[str, str]) -> Restored:
        """가명 토큰을 원래 값으로 (#45). 조사를 원래 값의 받침에 맞추고, 바꿔 쓴 토큰([고객 1], 【고객1】)도 되돌린다.
        복원하지 못한 토큰은 사전·정규식 종류의 토큰 모양만 센다."""
        kinds = set(self.kinds.values()) | {k for k, _ in PATTERNS} | ({NAME_KIND} if self.ner is not None else set())
        return restore_tokens(text, mapping, kinds)
