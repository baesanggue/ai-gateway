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
from gateway.masking.ner import NerModel, Span
from gateway.masking.ner import load as load_ner
from gateway.masking.patterns import ADDRESS, PATTERNS, compat_copy, find_pii, find_pii_compat, is_date, normalize_text
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
TOKEN_SPLIT = re.compile(r"(\[[^\W\d_]+\d+\])")             # [고객1] 모양으로 나눈다 (괄호로 묶어 토큰도 남긴다)
NAME_PIECE = re.compile(r"[^\W\d_]+(?: [^\W\d_]+)*")       # 이름 조각에서 글자 덩어리만 ("[양미래]" → 양미래, "Kim Mira")
NAME_KIND = "이름"        # 사전에 없는 이름의 토큰 종류 [이름1]. 사전에 등록된 이름은 사전의 종류([고객1])를 쓴다
ADDRESS_KIND = "주소"
# 이름 인식 모델이 장소(PLACE)로 본 곳 중 주소로 가릴 것 (#87): 시·도/시·군·구 다음 동·읍·면·리·가가 오고, 그 뒤에 건물 이름이나
# 번호가 더 있다("마포구 가상동 예시리더스"). 동에서 끝나는 지역 이름("서울시 마포구 망원동 맛집")은 가리지 않는다
ADDRESS_LIKE_PLACE = re.compile(r"[가-힣]{1,6}[시도구군]\s{0,3}[가-힣\d]{1,8}?[동읍면리가]\s{0,3}[가-힣A-Za-z\d]")
_ADDRESS_GAP = re.compile(r"[\s,]*")      # 이 글자만 사이에 있는 두 주소 조각은 한 주소다
# 번호·호·층·번지 뒤의 조사. 모델이 "샘플길 7로"처럼 조사까지 주소로 보면 값에 조사가 들어가, LLM이 "[주소1]로"라고 쓸 때
# "7로로"로 복원된다 (#62 G018). 조사는 토큰 밖에 남긴다. "가"·"이"·"도"는 넣지 않는다: "종로3가"처럼 주소의 일부일 수 있다
_ADDRESS_JOSA = re.compile(r"(?<=[\d호층지])(?:으로|로|에서|에게|에|은|는|을|를|와|과|의|까지|부터)$")


def _alternation(values: list[str]) -> re.Pattern[str] | None:
    return re.compile("|".join(map(re.escape, values)), re.IGNORECASE) if values else None


def _outside(text: str, mapping: dict[str, str], fn) -> str:
    """이번 요청에서 만든 토큰([고객1]) 밖에서만 바꾼다. 입력에 원래 있던 토큰 모양 글자([양미래1]) 안은 바꾼다."""
    parts = TOKEN_SPLIT.split(text)
    return "".join(p if i % 2 and p in mapping else fn(p) for i, p in enumerate(parts))


def _kept_chars(masked: str, mapping: dict[str, str]) -> list[bool]:
    """가린 문장을 원문 글자 자리로 펼친다: 원문 글자마다 그대로 남았으면 True, 토큰으로 바뀌었으면 False.
    가린 문장은 원문(정규화한 것)의 몇 군데를 토큰으로 바꾼 것이므로, 토큰을 그 값의 길이만큼 펼치면 원문과 자리가 맞는다."""
    kept: list[bool] = []
    for i, part in enumerate(TOKEN_SPLIT.split(masked)):
        if i % 2 and part in mapping:
            kept += [False] * len(mapping[part])
        else:
            kept += [True] * len(part)
    return kept


def _address_values(texts: list[str], spans: list[Span]) -> list[str]:
    """주소로 가릴 값 (#87): 메시지마다 정규식 주소와 이름 인식 모델이 찾은 주소의 위치를 합친다.
    겹치거나 공백·쉼표만 사이에 있으면 하나로 본다. 정규식은 번호까지의 끝이 정확하고 모델은 건물 이름·동호수까지 보므로,
    따로 가리면 한쪽만 가려 나머지("예시리더스 101동")가 그대로 나간다. 위치(spans)는 메시지를 이어 붙인 글 기준이다."""
    values: list[str] = []
    offset = 0
    for text in texts:
        ranges = [(m.start(), m.end()) for m in ADDRESS.finditer(text)]
        ranges += [(s.start - offset, s.end - offset) for s in spans if offset <= s.start and s.end <= offset + len(text)]
        merged: list[tuple[int, int]] = []
        for a, b in sorted(ranges):
            if merged and (a <= merged[-1][1] or _ADDRESS_GAP.fullmatch(text[merged[-1][1]:a])):
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        values += [_ADDRESS_JOSA.sub("", text[a:b].strip(" ,")) for a, b in merged]
        offset += len(text) + 1                      # 이어 붙인 줄바꿈 자리
    return [v for v in dict.fromkeys(values) if sum(c.isalnum() for c in v) >= 2]


def _address_left(texts: list[str], out: list[dict[str, str]], mapping: dict[str, str], addresses: list[Span]) -> bool:
    """이름 인식 모델이 원문에서 찾은 주소가 가린 뒤에도 남았는가: 주소의 글자·숫자 중 절반 이상이 그대로면 남은 것이다.
    - 정규식이 못 맞춘 주소("마포구 가상로 123")는 전부 남는다
    - 일부만 다른 규칙으로 바뀐 주소(사전 값 "가상" → "마포구 [사내1]로 123")도 남은 것으로 본다. 글자가 그대로 있는지만
      비교하면 이 경우를 놓친다 (11/07 보안 검토)
    - 정규식이 시·도~번호를 가리고 층·호수만 남은 것("[주소1] 3층")은 남은 것으로 보지 않는다. 그것만으로는 사람을 알 수 없다
    자리를 맞추지 못하면(대소문자를 무시한 같은 값의 길이가 다를 때) 남은 것으로 본다(막는 쪽)."""
    kept: list[bool] = []
    for text, msg in zip(texts, out):
        k = _kept_chars(msg["content"], mapping)
        if len(k) != len(text):
            return True
        kept += k + [True]                       # 이어 붙인 줄바꿈 자리
    joined = "\n".join(texts)
    for s in addresses:
        chars = [i for i in range(s.start, s.end) if joined[i].isalnum()]
        if chars and sum(kept[i] for i in chars) * 2 >= len(chars):
            return True
    return False


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
    - 이름 인식 모델(#53, D23): 요청 전체를 한 번에 읽혀 사람 이름을 찾고, 값 단위로 [이름N]으로 가린다.
      모델은 토큰이 끼기 전의 원문을 본다. 글자 덩어리 두 글자 이상만(사전과 같은 규칙). 사전 값을 품은 이름은 사전보다
      먼저, 나머지는 사전 다음에 가린다(긴 값 먼저와 같은 효과). 이번 요청에서 만든 토큰 안은 어느 단계도 건드리지 않는다
    - 주소는 맨 먼저 가린다 (#87, 11/11): 정규식 주소와 모델이 찾은 주소(ADDRESS, 건물 이름이 붙은 PLACE)의 위치를 합쳐
      한 값으로 [주소N]. 전에는 모델이 찾은 주소를 가리지 않고 남았는지만 봐서(D23), 기본 수준은 지번·아파트 이름 주소를
      그대로 보냈다. 합친 뒤에도 남은 주소가 있으면(suspected) 엄격 수준은 막는다
    """

    available = True

    def __init__(self, entries: list[Entry], dictionary_source: str = "custom",
                 custom_patterns: list[tuple[str, re.Pattern[str]]] = (), ner: NerModel | None = None,
                 not_names: list[str] = ()):
        self.dictionary_source = dictionary_source     # "custom"(회사 사전) | "example"(시연용 사전 복사본) → /healthz
        self.ner = ner                                 # 이름 인식 모델. 게이트웨이는 늘 있다(없으면 시작하지 않음, D23)
        # 이름 인식 모델이 이름으로 잘못 보는 업무 낱말 (#65, 프리셋). 정확히 같으면 가리지 않는다. 사전 값은 그대로 가린다
        self.not_names = {normalize_text(w).casefold() for w in not_names}
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
        return cls(entries, "example" if is_example else "custom", custom, ner, cfg.not_names)

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
        # 주소를 가장 먼저 가린다 (#87): 정규식 + 모델이 찾은 주소를 합친 값. 이름·사전이 먼저 바꾸면 주소 값이 깨져 못 가린다
        address_re = _alternation(sorted(_address_values(texts, addresses), key=len, reverse=True))
        # 사전 값을 품은 이름("남궁민수" ⊃ 사전 "민수")은 사전보다 먼저 가린다. 사전이 먼저면 "남궁[고객1]"로 성이 남는다
        first = [n for n in names if self.names is not None and self.names.search(n)]
        first_re = _alternation(first)
        rest_re = _alternation([n for n in names if n not in first])

        def name_token(m: re.Match) -> str:
            v = m.group()
            in_dictionary = self.names is not None and self.names.fullmatch(v)
            return token(self._kind(v) if in_dictionary else NAME_KIND, v)

        def dict_token(m: re.Match) -> str:
            return token(self._kind(m.group()), m.group())

        def address_token(m: re.Match) -> str:
            return token(ADDRESS_KIND, m.group())

        out = []
        for msg, text in zip(messages, texts):
            if address_re is not None:
                text = _outside(text, mapping, lambda t: address_re.sub(address_token, t))
            if first_re is not None:
                text = _outside(text, mapping, lambda t: first_re.sub(name_token, t))
            if self.names is not None:
                text = _outside(text, mapping, lambda t: self.names.sub(dict_token, t))
            if rest_re is not None:
                text = _outside(text, mapping, lambda t: rest_re.sub(name_token, t))
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
        suspected = [ADDRESS_KIND] if addresses and _address_left(texts, out, mapping, addresses) else []
        return MaskResult(out, mapping, counts, unmasked, suspected)

    def _find(self, texts: list[str]) -> tuple[list[str], list[Span]]:
        """이름 인식 모델로 (이름들, 주소 조각들). 메시지 전체를 이어 한 번에 읽힌다(파일은 칸이 수천 개다).
        - 바로 붙은 이름 조각은 합친다(모델이 "양|미래"로 두 번 시작해도 성까지 가린다)
        - 이름 조각에서 글자 덩어리만 쓴다: 괄호·숫자·문장부호를 떼고("[양미래]" → 양미래), 두 글자 이상만.
          한 글자는 가리지 않는다(사전과 같은 규칙). 한 음절로는 사람을 알 수 없고, 값 단위로 가리면 그 글자가 어디서나 사라진다
        - 주소는 위치째로 둔다(괄호가 있어도). 모델이 ADDRESS로 본 곳과, PLACE로 본 곳 중 동·읍·면 뒤에 건물 이름·번호가
          더 있는 곳(ADDRESS_LIKE_PLACE)이 주소다 (#87). 정규식 주소와 합쳐 가리고(_address_values), 가린 뒤 남았는지도 본다
        긴 이름부터 (같은 위치에서 "김민수진"이 "김민수"보다 먼저)."""
        if self.ner is None:
            return [], []
        joined = "\n".join(texts)
        spans: list[Span] = []
        for s in self.ner.find(joined, {"NAME", "ADDRESS", "PLACE"}):
            if spans and s.label == "NAME" == spans[-1].label and spans[-1].end == s.start:
                p = spans[-1]
                spans[-1] = Span("NAME", p.start, s.end, joined[p.start:s.end], p.score)
            else:
                spans.append(s)
        names = [p for s in spans if s.label == "NAME" for p in NAME_PIECE.findall(s.text)
                 if len(p) >= 2 and p.casefold() not in self.not_names]
        addresses = [s for s in spans if sum(c.isalnum() for c in s.text) >= 2 and
                     (s.label == "ADDRESS" or (s.label == "PLACE" and ADDRESS_LIKE_PLACE.search(s.text)))]
        return sorted(dict.fromkeys(names), key=len, reverse=True), addresses

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
