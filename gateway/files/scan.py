"""파일 속 개인정보 검사 (#50, 11/03 면담 의견).

채팅과 같은 가명화 규칙(사용자 사전 → 이름 인식 모델 → 회사별 식별번호 → 정규식 → 마지막 검사)으로 파일 전체를 본다.
- 결과에는 값을 넣지 않는다: 종류별 개수, 위치(쪽·시트·셀), 가명화된 텍스트만
- 파일 전체를 한 번에 가명화해서, 같은 값은 어느 쪽·셀에 있든 같은 토큰이 된다
- 가명화된 텍스트는 사용자가 그대로 AI에 붙여넣을 수 있다. 복원 대응표는 돌려주지 않는다 (원본은 사용자 파일에 있다)
- 판단 모델은 쓰지 않는다. 긴 문서는 판단 모델이 읽을 수 있는 길이를 넘는다 (D19, #39)
  이름 인식 모델은 쓴다(D23). 긴 문서도 끝까지 구간으로 나눠 읽는다 (2백만 자 상한에서 1분 안팎, docs/file-scan.md)
"""
import re
from dataclasses import dataclass, field

from gateway.files.extract import extract
from gateway.masking.masker import Masker

_KIND = re.compile(r"\[(\D+?)\d+\]")      # 가명화 부품이 만든 토큰 [종류N]에서 종류


@dataclass
class Finding:
    where: str
    kinds: dict[str, int]          # 이 위치에서 찾은 종류별 개수 (같은 값이 두 번이면 두 번)


@dataclass
class ScanReport:
    file_type: str                 # pdf | docx | xlsx | txt | csv
    segments: int                  # 읽은 쪽·문단·셀·줄 수
    chars: int
    counts: dict[str, int]         # 종류별 서로 다른 값의 개수 ("전화번호 3개")
    findings: list[Finding] = field(default_factory=list)
    unmasked: list[str] = field(default_factory=list)   # 가리지 못한 표기로 남은 종류 (동그라미 숫자 등)
    suspected: list[str] = field(default_factory=list)  # 이름 인식 모델이 봤지만 가리지 못한 종류 (시·도 없이 쓴 주소 등)
    masked_text: str = ""

    @property
    def has_pii(self) -> bool:
        return bool(self.counts or self.unmasked or self.suspected)


def scan(filename: str, data: bytes, masker: Masker) -> ScanReport:
    segments = extract(filename, data)
    masked = masker.mask([{"role": "user", "content": s.text} for s in segments])
    findings = []
    for seg, msg in zip(segments, masked.messages):
        kinds: dict[str, int] = {}
        for token in masked.mapping:                  # 이번에 만든 토큰만 센다 (파일에 원래 있던 [고객1] 같은 글자는 빼고)
            if n := msg["content"].count(token):
                kind = _KIND.fullmatch(token).group(1)
                kinds[kind] = kinds.get(kind, 0) + n
        if kinds:
            findings.append(Finding(seg.where, kinds))
    return ScanReport(
        file_type=filename.rsplit(".", 1)[-1].lower(), segments=len(segments),
        chars=sum(len(s.text) for s in segments), counts=dict(masked.counts), findings=findings,
        unmasked=sorted(set(masked.unmasked)), suspected=sorted(set(masked.suspected)),
        masked_text="\n".join(msg["content"] for msg in masked.messages))


def summary(report: ScanReport) -> str:
    """사용자에게 보여 줄 한국어 안내. 값은 넣지 않는다."""
    if not report.has_pii:
        return f"개인정보를 찾지 못했습니다 ({report.segments}곳, {report.chars:,}자 검사)."
    lines = []
    if report.counts:
        parts = ", ".join(f"{kind} {n}개" for kind, n in sorted(report.counts.items()))
        lines.append(f"개인정보 {sum(report.counts.values())}개를 찾았습니다: {parts}.")
    if report.findings:
        shown = ", ".join(f.where for f in report.findings[:5])
        more = f" 외 {len(report.findings) - 5}곳" if len(report.findings) > 5 else ""
        lines.append(f"위치: {shown}{more}.")
    if report.unmasked:
        lines.append(f"가리지 못한 표기가 있습니다({', '.join(report.unmasked)}). 이 파일은 그대로 AI에 올리지 마세요.")
    if report.suspected:
        lines.append(f"가리지 못한 개인정보가 남아 있을 수 있습니다({', '.join(report.suspected)}: 시·도 없이 쓴 주소 등). "
                     "가명화된 텍스트도 올리기 전에 직접 확인하세요.")
    if not report.unmasked and not report.suspected:
        lines.append("이 파일을 그대로 외부 AI에 올리지 말고, 가명화된 텍스트를 쓰세요.")
    return " ".join(lines)
