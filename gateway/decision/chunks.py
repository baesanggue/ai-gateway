"""로컬 판단 입력을 판단 모델이 읽을 수 있는 구간으로 나눈다 (#71, D19 보완).

로컬 판단 모델(gliclass:v4-edge)은 약 1,024토큰(한국어 1,275자)까지 읽는다(#39). 로컬 판단은 대화 전체(system 지시문,
이전 대화, 이번 질문)를 보므로 쉽게 넘는다. 그래서
1. **메시지 단위가 먼저다.** system 지시문과 이전 대화는 매 요청 그대로 반복되므로, 메시지 경계로 나누면 같은 구간이
   다시 나와 판단 결과를 기억할 수 있다(DecisionService). 대화가 길어져도 새 메시지가 든 끝 구간만 새로 판단한다
2. 짧은 메시지는 앞에서부터 묶는다(size 이하). 짧은 요청 하나는 지금처럼 판단 한 번이다
3. size보다 긴 메시지는 줄바꿈·문장 끝·띄어쓰기에서 자르고, 경계에 걸친 문장을 놓치지 않게 overlap 글자씩 겹친다

앞에서부터 정해지므로, 뒤에 메시지가 붙어도 앞 구간은 바뀌지 않는다(기억한 결과를 그대로 쓴다).
"""

CUT_AT = ("\n", ". ", "? ", "! ", "다. ", "요. ", " ")   # 자를 곳 우선순위


def split_long(text: str, size: int, overlap: int) -> list[str]:
    """size 글자 이하 조각으로. 조각 뒤쪽 절반 안에서 자를 곳을 찾고, 다음 조각은 overlap 글자 앞에서 시작한다."""
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while True:
        end = start + size
        if end >= len(text):
            out.append(text[start:])
            return out
        window = text[start:end]
        for sep in CUT_AT:
            i = window.rfind(sep)
            if i >= size // 2:
                end = start + i + len(sep)
                break
        out.append(text[start:end])
        start = max(end - overlap, start + 1)


def chunks(texts: list[str], size: int, overlap: int) -> list[str]:
    """메시지들을 판단할 구간으로: 긴 메시지는 나누고, 짧은 조각은 앞에서부터 size 이하로 묶는다(줄바꿈으로 잇는다)."""
    pieces = [p for t in texts for p in split_long(t, size, overlap)]
    out: list[str] = []
    for p in pieces:
        if out and len(out[-1]) + 1 + len(p) <= size:
            out[-1] = out[-1] + "\n" + p
        else:
            out.append(p)
    return out or [""]


def halves(text: str) -> list[str]:
    """판단 모델이 그래도 길다고 하면(글자당 토큰이 많은 글) 가운데 가까운 띄어쓰기에서 반으로."""
    mid = len(text) // 2
    left, right = text.rfind(" ", 0, mid), text.find(" ", mid)
    cut = min((c for c in (left, right) if c > 0), key=lambda c: abs(c - mid), default=mid)
    return [text[:cut], text[cut:]]
