"""응답 복원 (#45): LLM 답변의 가명 토큰을 원래 값으로 되돌리고, 뒤에 붙은 조사를 원래 값의 받침에 맞게 고친다.

- 조사는 코드가 고친다 (받침 계산은 규칙이다. LLM에 맡기지 않는다).
  LLM은 "[고객1]"만 보고 조사를 붙이므로, 원래 값이 "김민지"인데 "[고객1]은"이라고 쓰면 "김민지은"이 된다
- 받침: 한글은 글자에서, 숫자는 읽는 소리에서(…3 → 삼, …0 → 십·백·천·만, 모두 받침 있음).
  영문·기호로 끝나는 값(이메일 등)은 읽는 법이 하나가 아니라 조사를 고치지 않는다
- LLM이 토큰 모양을 바꿔 쓴 경우: 괄호 종류(【고객1】 ［고객1］ 〔고객1〕), 안쪽 공백([고객 1], [ 고객1 ])은 복원한다.
  괄호가 없는 "고객1"은 뒤에 조사·님·씨·공백·문장부호가 올 때만 복원한다 ("고객1명"은 그대로)
- 대응표에 없는 토큰은 지어내지 않고 그대로 둔다. 남은 토큰 모양 수를 unrestored로 돌려주고 로그에 남긴다
"""
import re
from dataclasses import dataclass

# (받침 있을 때, 받침 없을 때). 긴 것부터 맞춘다
_PAIRS = [("이라고", "라고"), ("이라는", "라는"), ("이에요", "예요"), ("이랑", "랑"), ("이나", "나"), ("이며", "며"),
          ("으로", "로"), ("은", "는"), ("이", "가"), ("을", "를"), ("과", "와")]
_FORMS = {f: pair for pair in _PAIRS for f in pair}
_JOSA = "|".join(sorted(_FORMS, key=len, reverse=True))
# 조사 뒤에 올 수 있는 것: 끝, 한글이 아닌 글자, 조사에 이어 붙는 말 (으로는, 과도, 이랑만 …).
# "[고객1]이다"의 "이"나 "가요"처럼 낱말 일부인 경우는 고치지 않는다
_AFTER = r"(?=$|[^가-힣]|는|도|의|만|서|요|부터|까지)"

_OPEN, _CLOSE = r"[\[［【〔]", r"[\]］】〕]"
_FINALS = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")   # 한글 글자의 받침 순서
_DIGIT_FINAL = {"0": "ㅂ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ", "9": ""}
_ANY_TOKEN = re.compile(_OPEN + r"\s*([^\[\]［］【】〔〕\d\s]+)\s*(\d+)\s*" + _CLOSE)


@dataclass
class Restored:
    text: str
    unrestored: int = 0      # 복원하지 못한 토큰 모양 수 (대응표에 없는 번호, 지어낸 토큰). 로그용


def final_consonant(value: str) -> str | None:
    """값을 읽었을 때 마지막 받침. "" = 받침 없음, None = 알 수 없음(영문·기호)."""
    v = value.rstrip(" )]}>'\".,!?")
    if not v:
        return None
    c = v[-1]
    if "가" <= c <= "힣":
        return _FINALS[(ord(c) - 0xAC00) % 28]
    if c.isdecimal() and c.isascii():
        return _DIGIT_FINAL[c]      # 끝이 0이면 십·백·천·만, 전화번호처럼 하나씩 읽어도 "영". 모두 받침 있음
    return None


def fix_josa(value: str, josa: str) -> str:
    """원래 값 뒤에 올 조사를 받침에 맞춘다. 알 수 없으면 LLM이 쓴 그대로."""
    final = final_consonant(value)
    if final is None:
        return josa
    with_b, without_b = _FORMS[josa]
    if with_b == "으로":
        return "로" if final in ("", "ㄹ") else "으로"
    return with_b if final else without_b


def restore(text: str, mapping: dict[str, str], kinds: set[str] | None = None) -> Restored:
    """가명 토큰을 원래 값으로. kinds는 unrestored를 셀 때 토큰으로 볼 종류 (대응표 종류는 늘 포함)."""
    by_key = {}
    for tok, value in mapping.items():
        m = _ANY_TOKEN.fullmatch(tok)
        if m:
            by_key[(m.group(1), m.group(2))] = value
    kinds = set(kinds or ()) | {k for k, _ in by_key}
    out = text
    if by_key:
        names = "|".join(map(re.escape, sorted({k for k, _ in by_key}, key=len, reverse=True)))
        bracketed = _OPEN + rf"\s*(?P<k1>{names})\s*(?P<n1>\d+)\s*" + _CLOSE
        # 괄호 없는 "고객1": 앞은 글자·숫자가 아니고, 뒤에는 조사·님·씨·께나 낱말 경계가 와야 한다
        bare = rf"(?<![\w\[［【〔])(?P<k2>{names})\s?(?P<n2>\d+)(?!\d)(?=$|[^\w]|{_JOSA}|님|씨|께)"
        pattern = re.compile(rf"(?:{bracketed}|{bare})(?P<josa>(?:{_JOSA}){_AFTER})?")

        def sub(m: re.Match) -> str:
            key = (m.group("k1") or m.group("k2"), m.group("n1") or m.group("n2"))
            value = by_key.get(key)
            if value is None:
                return m.group()
            josa = m.group("josa")
            return value + (fix_josa(value, josa) if josa else "")

        out = pattern.sub(sub, text)
    unrestored = sum(1 for m in _ANY_TOKEN.finditer(out) if m.group(1) in kinds)
    return Restored(out, unrestored)
