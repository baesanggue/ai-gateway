"""형식 있는 개인정보의 정규식 (M2 가명화의 출발점). 지금은 요청 로그의 안전장치(#11)가 쓴다.

- 측정용 사본(bench/run_judges.py의 PATTERNS)과 같은 패턴에서 시작했다. 지난 측정 결과를 다시 낼 수 있도록
  측정용 사본은 고치지 않고, 게이트웨이는 이 파일을 고쳐 나간다
- 이름은 정규식으로 잡지 않는다. 사용자 사전(M2)과 로컬 판단(남은 개인정보)의 몫이다
- 알려진 한계: 주소 패턴이 넓어 "내년도 연구개발비로 30억"에도 걸린다
  (bench/results/generator-trial-2026-10-30/summary.md). M2에서 좁힌다
"""
import re

# 긴 패턴부터 본다. 주민번호(6-7자리)가 계좌번호 패턴에 먼저 잡히지 않게 한다
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("주민번호", re.compile(r"\d{6}-\d{7}")),
    ("카드번호", re.compile(r"\d{4}-\d{4}-\d{4}-\d{4}")),
    ("전화번호", re.compile(r"0\d{1,2}-\d{3,4}-\d{4}")),
    ("이메일", re.compile(r"[\w.]+@[\w-]+(\.[\w-]+)+")),
    ("계좌번호", re.compile(r"\d{2,6}-\d{2,6}-\d{2,6}(-\d{1,6})?")),
    ("주소", re.compile(r"\S+(시|도)\s*\S+(구|군)\s*\S+?(대로|로|길)\s*[\d-]+(,\s*\d+동\s*\d+호)?")),
]
_DATE = re.compile(r"(19|20)\d{2}-\d{1,2}-\d{1,2}")   # 2026-11-03은 계좌번호 모양이지만 날짜다


def find_pii(text: str) -> list[str]:
    """형식 있는 개인정보 값의 종류를 찾은 순서대로 돌려준다(같은 종류가 여러 번이면 여러 번).
    찾은 값은 지우고 다음 패턴을 봐서, 주민번호 하나가 계좌번호로 한 번 더 세지지 않는다."""
    found = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(text):
            if kind == "계좌번호" and _DATE.fullmatch(m.group()):
                continue
            found.append(kind)
            text = text.replace(m.group(), " ")
    return found
