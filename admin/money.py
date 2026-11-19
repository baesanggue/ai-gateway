"""화면에 비용을 원화로 보여 준다 (#132).

- 설문(D31)에서 비용 표시가 3.0점으로 가장 낮았다. "$0.0036"처럼 소수점 넷째 자리 달러는 감이 오지 않는다
- 기록(DB)·측정 문서는 달러 그대로 두고, 보여 줄 때만 원화로 바꾼다. 환율은 config.yaml의 display.krw_per_usd
- 계산은 코드가 한다(설계 결정 6번). 요청 한 건의 비용은 1원도 안 되므로, 작은 금액도 "0원"으로 뭉개지지 않게 자릿수를 바꾼다
"""


def krw(usd: float | None, rate: float) -> str:
    """달러 → '약 ○원'. 10원 이상은 정수, 1원 이상은 소수 한 자리, 그 아래는 소수 두 자리."""
    won = (usd or 0.0) * rate
    if won <= 0:
        return "0원"
    if won >= 10:
        return f"약 {won:,.0f}원"
    if won >= 1:
        return f"약 {won:.1f}원"
    if won >= 0.01:
        return f"약 {won:.2f}원"
    return "0.01원 미만"


def saving_sentence(cost: float, base: float, n: int, rate: float) -> str:
    """비용 카드 아래에 붙이는 한 문장. n: 요청 수. 1,000건 환산은 지금까지 보낸 요청과 같은 비율이라고 보고 계산한다."""
    if n <= 0 or base <= 0:
        return "요청을 보내면, 모든 요청을 비싼 고성능 모델로 보냈을 때와 비용을 비교해 보여 줍니다."
    saved = max(base - cost, 0.0)
    if saved <= 0:
        return f"이번 요청은 모두 고성능 모델이 필요해 {krw(cost, rate)}이 들었고, 아낀 비용은 없습니다."
    return (f"모든 요청을 비싼 고성능 모델로 보냈다면 {krw(base, rate)}, 가림게이트로는 {krw(cost, rate)}이 들어 "
            f"{saved / base:.0%}를 아꼈습니다. 같은 종류의 요청 1,000건이면 {krw(saved / n * 1000, rate)}을 아낍니다.")


def rate_line(rate: float, note: str) -> str:
    return f"1달러 = {rate:,.0f}원({note})으로 계산했습니다."
