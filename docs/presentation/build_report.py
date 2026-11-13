"""학과 「구독형 소프트웨어 활용 결과보고서」 한글 양식(hwpx)에 초안을 채운다 (#97).

    python docs/presentation/build_report.py "<양식.hwpx>" [<결과.hwpx>]

- 결과 파일 이름을 주지 않으면 양식 이름의 "_양식_이름"을 "_배상규"로 바꿔 이 폴더에 만든다(제출 형식, #113)

- 내용은 docs/presentation/report-draft.md의 "## 제목" 아래 글에서 읽는다(번호 목록은 칸 안의 문단 하나씩)
- 양식의 글꼴·서식("폰트 수정금지")은 건드리지 않는다: 칸 안의 첫 문단을 본떠 문단만 바꾼다(같은 문단·글자 모양 번호)
- 줄 배치 정보(linesegarray)는 빼서 한글이 열 때 다시 계산하게 한다
- 산출물 그림 6칸도 넣는다(#108): report-draft.md의 산출물 목록 순서대로 report-images/의 그림과 설명 한 줄을 칸 가운데에.
  그림은 BinData/에 넣고 Contents/content.hpf 목록에 올린다. 칸 크기에 맞게 비율을 지켜 줄인다
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
HC = "http://www.hancom.co.kr/hwpml/2011/core"
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


def fill(xml: bytes, sections: dict[str, list[str]]) -> tuple[bytes, list[tuple[str, Path]]]:
    root = etree.fromstring(xml)
    table = root.find(".//hp:tbl", NS)                     # 바깥 표 (안쪽 그림 표와 섞이지 않게 바로 아래 행만 본다)
    rows = table.findall("hp:tr", NS)
    content = [r.findall("hp:tc", NS)[-1] for r in rows[:5]]
    set_paragraphs(content[0], section(sections, "팀명"))
    set_paragraphs(content[1], section(sections, "프로젝트명"))
    set_paragraphs(content[2], section(sections, "생성형 AI 활용 목적"))
    set_paragraphs(content[3], section(sections, "생성형 AI 활용 성과"))
    set_paragraphs(content[4], section(sections, "생성형 AI 사용 후 느낀점"))
    files = add_pictures(root, picture_items(sections))
    body = etree.tostring(root, encoding="UTF-8")
    declaration = xml[: xml.index(b"?>") + 2]               # 양식의 XML 선언을 글자 그대로 둔다
    return declaration + body, files


# ── 산출물 그림 (#106) ───────────────────────────────────────────────────────────
PX = 75                                  # 그림 1픽셀 = 75 HWPUNIT (96dpi 기준, 1인치 = 7200)
PIC_PARA, PIC_STYLE, PIC_CHAR = "24", "23", "17"   # 양식의 그림 칸 빈 문단 모양(가운데 정렬)
CAPTION_H = 1700                         # 설명 한 줄이 차지하는 높이(글자 10pt + 줄 간격)


def picture_items(sections: dict[str, list[str]]) -> list[tuple[str, Path]]:
    """산출물 목록 '1. 설명 (`파일.png`, …)' → (설명, 그림 경로)."""
    items = []
    for line in section(sections, "생성형 AI 활용 산출물"):
        m = re.match(r"\d+\.\s*(.+?)\s*\(`([^`]+)`", line)
        if m:
            items.append((m.group(1), HERE / "report-images" / m.group(2)))
    return items


def _para(children: str):
    return etree.fromstring(
        f'<hp:p xmlns:hp="{HP}" xmlns:hc="{HC}" id="2147483648" paraPrIDRef="{PIC_PARA}" styleIDRef="{PIC_STYLE}" '
        f'pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{PIC_CHAR}">{children}</hp:run></hp:p>')


def _pic(n: int, item_id: str, px: tuple[int, int], max_w: int, max_h: int) -> str:
    """그림 개체 하나(글자처럼 취급, 문단 가운데). 크기 단위는 HWPUNIT."""
    ow, oh = px[0] * PX, px[1] * PX
    scale = min(max_w / ow, max_h / oh, 1.0)
    cw, ch = int(ow * scale), int(oh * scale)
    pid = 1900000000 + n
    return (f'<hp:pic id="{pid}" zOrder="{10 + n}" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
            f'lock="0" dropcapstyle="None" href="" groupLevel="0" instid="{pid - 1000000000}" reverse="0">'
            f'<hp:offset x="0" y="0"/><hp:orgSz width="{ow}" height="{oh}"/><hp:curSz width="{cw}" height="{ch}"/>'
            f'<hp:flip horizontal="0" vertical="0"/>'
            f'<hp:rotationInfo angle="0" centerX="{cw // 2}" centerY="{ch // 2}" rotateimage="1"/>'
            f'<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
            f'<hc:scaMatrix e1="{scale:.6f}" e2="0" e3="0" e4="0" e5="{scale:.6f}" e6="0"/>'
            f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
            f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{ow}" y="0"/><hc:pt2 x="{ow}" y="{oh}"/><hc:pt3 x="0" y="{oh}"/></hp:imgRect>'
            f'<hp:imgClip left="0" right="{ow}" top="0" bottom="{oh}"/>'
            f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:imgDim dimwidth="{ow}" dimheight="{oh}"/>'
            f'<hc:img binaryItemIDRef="{item_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
            f'<hp:sz width="{cw}" widthRelTo="ABSOLUTE" height="{ch}" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
            f'vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/></hp:pic>')


def add_pictures(root, items: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    """안쪽 3x2 그림 표의 칸마다 그림 + 설명. 칸 안의 원래 문단(안내 문구)은 지운다. 돌려주는 것: (BinData 이름, 파일)."""
    from PIL import Image
    grid = next(t for t in root.iter(q("tbl")) if t.get("rowCnt") == "3" and t.get("colCnt") == "2")
    inner = grid.find("hp:inMargin", NS)
    cells = sorted(grid.iter(q("tc")), key=lambda c: (int(c.find("hp:cellAddr", NS).get("rowAddr")),
                                                    int(c.find("hp:cellAddr", NS).get("colAddr"))))
    if len(items) > len(cells):
        raise ValueError(f"그림 {len(items)}개 > 칸 {len(cells)}개")
    files = []
    for n, ((caption, path), cell) in enumerate(zip(items, cells), 1):
        sz = cell.find("hp:cellSz", NS)
        max_w = int(sz.get("width")) - int(inner.get("left")) - int(inner.get("right"))
        max_h = int(sz.get("height")) - int(inner.get("top")) - int(inner.get("bottom")) - CAPTION_H
        name = f"image{n}"
        with Image.open(path) as im:
            px = im.size
        sub = cell.find("hp:subList", NS)
        for para in sub.findall("hp:p", NS):
            sub.remove(para)
        sub.append(_para(_pic(n, name, px, max_w, max_h)))
        cap = _para("<hp:t/>")
        cap.find(".//hp:t", NS).text = f"{n}. {caption}"
        sub.append(cap)
        files.append((f"BinData/{name}{path.suffix.lower()}", path))
    return files


def manifest_with(hpf: bytes, files: list[tuple[str, Path]]) -> bytes:
    """content.hpf 목록에 그림을 올린다(id = 파일 이름에서 확장자를 뺀 것, section0의 binaryItemIDRef와 같다)."""
    items = "".join(f'<opf:item id="{Path(n).stem}" href="{n}" media-type="image/{Path(n).suffix[1:]}" isEmbeded="1"/>'
                    for n, _ in files)
    return hpf.replace(b"</opf:manifest>", items.encode("utf-8") + b"</opf:manifest>", 1)


AUTHOR = "배상규"


def default_out(template: Path) -> Path:
    """제출 파일 이름은 양식 이름을 따른다: '…결과보고서_양식_이름.hwpx' → '…결과보고서_배상규.hwpx' (#113)."""
    name = template.name.replace("_양식_이름", f"_{AUTHOR}")
    return HERE / (name if name != template.name else f"결과보고서_{AUTHOR}.hwpx")


def main() -> None:
    template = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else default_out(template)
    sections = read_draft()
    with zipfile.ZipFile(template) as src, zipfile.ZipFile(out, "w") as dst:
        section_xml, files = fill(src.read("Contents/section0.xml"), sections)
        for info in src.infolist():                         # 원래 순서·압축 방식 그대로 (mimetype은 맨 앞, 압축 없음)
            data = src.read(info.filename)
            if info.filename == "Contents/section0.xml":
                data = section_xml
            elif info.filename == "Contents/content.hpf":
                data = manifest_with(data, files)
            dst.writestr(info, data, compress_type=info.compress_type)
        for name, path in files:                            # 그림은 이미 압축된 형식이라 그대로 둔다(양식의 미리보기 그림과 같게)
            dst.write(path, name, compress_type=zipfile.ZIP_STORED)
    print(f"saved {out} (그림 {len(files)}개)")


if __name__ == "__main__":
    main()
