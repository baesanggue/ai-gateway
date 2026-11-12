"""학과 「구독형 소프트웨어 활용 결과보고서」 한글 양식(hwpx)에 초안을 채운다 (#97).

    python docs/presentation/build_report.py "<양식.hwpx>" [<결과.hwpx>]

- 내용은 docs/presentation/report-draft.md의 "## 제목" 아래 글에서 읽는다(번호 목록은 칸 안의 문단 하나씩)
- 양식의 글꼴·서식("폰트 수정금지")은 건드리지 않는다: 칸 안의 첫 문단을 본떠 문단만 바꾼다(같은 문단·글자 모양 번호)
- 줄 배치 정보(linesegarray)는 빼서 한글이 열 때 다시 계산하게 한다
- 산출물 그림 6칸은 한글에서 직접 넣는다(docs/presentation/report-images/)
- hwpx는 zip이다. mimetype을 맨 앞에 압축 없이 두는 규칙을 지킨다
"""
import copy
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
DRAFT = HERE / "report-draft.md"
HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
NS = {"hp": HP}


def q(tag: str) -> str:
    return f"{{{HP}}}{tag}"


def read_draft() -> dict[str, list[str]]:
    """'## 제목' → 내용 줄들. 번호 목록이면 항목마다 한 줄(번호 포함), 아니면 문단 한 줄."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in DRAFT.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current and line.strip() and not line.startswith(("#", "`")):
            sections[current].append(line.strip())
    return sections


def section(sections: dict[str, list[str]], prefix: str) -> list[str]:
    for k, v in sections.items():
        if k.startswith(prefix):
            return v
    raise KeyError(prefix)


def set_paragraphs(cell, lines: list[str]) -> None:
    """칸의 문단을 lines로 바꾼다. 첫 문단의 모양(paraPrIDRef·styleIDRef·charPrIDRef)을 그대로 쓴다."""
    sub = cell.find("hp:subList", NS)
    paras = sub.findall("hp:p", NS)
    model = paras[0]
    for p in paras:
        sub.remove(p)
    for i, line in enumerate(lines):
        p = copy.deepcopy(model)
        if i:
            p.set("id", "0")
        runs = p.findall("hp:run", NS)
        for r in runs[1:]:
            p.remove(r)
        run = runs[0]
        for child in list(run):
            run.remove(child)
        t = etree.SubElement(run, q("t"))
        t.text = line
        for seg in p.findall("hp:linesegarray", NS):
            p.remove(seg)
        sub.append(p)


def fill(xml: bytes, sections: dict[str, list[str]]) -> bytes:
    root = etree.fromstring(xml)
    table = root.find(".//hp:tbl", NS)                     # 바깥 표 (안쪽 그림 표와 섞이지 않게 바로 아래 행만 본다)
    rows = table.findall("hp:tr", NS)
    content = [r.findall("hp:tc", NS)[-1] for r in rows[:5]]
    set_paragraphs(content[0], section(sections, "팀명"))
    set_paragraphs(content[1], section(sections, "프로젝트명"))
    set_paragraphs(content[2], section(sections, "생성형 AI 활용 목적"))
    set_paragraphs(content[3], section(sections, "생성형 AI 활용 성과"))
    set_paragraphs(content[4], section(sections, "생성형 AI 사용 후 느낀점"))
    body = etree.tostring(root, encoding="UTF-8")
    declaration = xml[: xml.index(b"?>") + 2]               # 양식의 XML 선언을 글자 그대로 둔다
    return declaration + body


def main() -> None:
    template = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "결과보고서_가림게이트.hwpx"
    sections = read_draft()
    with zipfile.ZipFile(template) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():                         # 원래 순서·압축 방식 그대로 (mimetype은 맨 앞, 압축 없음)
            data = src.read(info.filename)
            if info.filename == "Contents/section0.xml":
                data = fill(data, sections)
            dst.writestr(info, data, compress_type=info.compress_type)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
