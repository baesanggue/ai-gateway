"""파일 검사 시연용 가상 개인정보 파일을 만든다 (#90, 11/11). 실존 인물·번호와 겹치지 않게 만든다.

    uv run python scripts/make_demo_files.py [--seed 7] [--out data/demo-files]

- 전화번호: 010-0xxx-xxxx (가운데가 0으로 시작하는 휴대전화 번호는 배정되지 않는다)
- 주민번호: 형식은 맞지만 마지막 확인 숫자를 일부러 틀리게 한다 (실제로 있을 수 없는 번호)
- 카드번호: 카드사가 공개한 시험용 번호
- 이메일: example.com (예시용으로 예약된 도메인)
- 주소: 실제 구 이름 + 가상의 도로·동 이름(가상로, 예시동…). 도로명·지번·아파트 이름 주소를 섞는다 (#87)
- 이름: 흔한 성 + 이름을 무작위로 섞고, 시연용 사전(쇼핑몰 프리셋)의 이름도 넣는다 → 사전으로 가리는 이름과
  이름 인식 모델로 가리는 이름이 함께 보인다
- 회원번호 M + 8자리(쇼핑몰 프리셋의 회사별 식별번호), 주문번호(개인정보가 아니라 가리지 않아야 한다)

만든 뒤 게이트웨이와 같은 가명화 부품으로 검사해, 넣은 값이 가린 글에 남았는지 보고한다(남으면 놓친 값).
"""
import argparse
import csv
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# "시연용 가상 데이터"로 쓰면 이름 인식 모델이 "시연용"을 사람 이름으로 본다(11/11 확인). 그래서 이 문장을 쓴다
NOTICE = "※ 이 파일의 이름·연락처·주소는 모두 가상 데이터입니다. 실존 인물과 관계없습니다."
SURNAMES = "김 이 박 최 정 강 조 윤 장 임 한 오 서 신 권 황 안 송 류 홍".split()
GIVEN = "민준 서윤 도윤 하은 예준 지유 시우 수아 주원 하린 지훈 채원 현우 은서 건우 다은 우진 소율 준서 예린".split()
DICT_CUSTOMERS = ["김민수", "이서연", "박지훈"]          # config/presets/shop.dictionary.example.txt
DICT_STAFF = "최유진"
GU = ["마포구", "강남구", "송파구", "서초구", "노원구", "은평구", "관악구", "성동구"]
ROADS = ["가상로", "예시길", "시범대로", "샘플로", "모의길"]
DONGS = ["가상동", "예시동", "시범동", "샘플동"]
BUILDINGS = ["예시아파트", "가상리더스", "시범타워", "샘플빌", "모의팰리스"]
TEST_CARDS = ["4111-1111-1111-1111", "5555-5555-5555-4444", "4012-8888-8888-1881"]
ITEMS = ["니트 가디건", "러닝화", "에코백", "블루투스 이어폰", "텀블러", "패딩 점퍼"]
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


class Fake:
    def __init__(self, seed: int):
        self.r = random.Random(seed)
        self.used: set[str] = set()

    def name(self) -> str:
        while True:
            n = self.r.choice(SURNAMES) + self.r.choice(GIVEN)
            if n not in self.used and n not in DICT_CUSTOMERS and n != DICT_STAFF:
                self.used.add(n)
                return n

    def phone(self) -> str:
        return f"010-0{self.r.randint(100, 999)}-{self.r.randint(0, 9999):04d}"

    def email(self) -> str:
        return f"{self.r.choice(['cs', 'buyer', 'member', 'user'])}{self.r.randint(10, 999)}@example.com"

    def rrn(self) -> str:
        """형식(생년월일 + 성별 자리)은 맞고 확인 숫자는 틀린 주민번호."""
        yy, mm, dd = self.r.randint(70, 99), self.r.randint(1, 12), self.r.randint(1, 28)
        digits = f"{yy:02d}{mm:02d}{dd:02d}{self.r.choice('12')}{self.r.randint(0, 99999):05d}"
        check = (11 - sum(int(d) * w for d, w in zip(digits, [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5])) % 11) % 10
        return f"{digits[:6]}-{digits[6:]}{(check + 1) % 10}"

    def account(self) -> str:
        return f"{self.r.randint(100, 999)}-{self.r.randint(10, 99)}-{self.r.randint(100000, 999999)}"

    def member_no(self) -> str:
        return f"M{self.r.randint(10000000, 99999999)}"

    def order_no(self) -> str:
        return f"2026111{self.r.randint(1000000, 9999999)}"

    def address(self) -> str:
        kind = self.r.choice(["road", "lot", "apt"])
        gu = self.r.choice(GU)
        if kind == "road":
            return f"서울특별시 {gu} {self.r.choice(ROADS)} {self.r.randint(1, 300)}"
        if kind == "lot":
            return f"서울특별시 {gu} {self.r.choice(DONGS)} {self.r.randint(1, 999)}-{self.r.randint(1, 30)}"
        return (f"서울특별시 {gu} {self.r.choice(DONGS)} {self.r.choice(BUILDINGS)} "
                f"{self.r.randint(101, 115)}동 {self.r.randint(1, 25)}0{self.r.randint(1, 4)}호")


def make_xlsx(f: Fake, path: Path) -> list[str]:
    from openpyxl import Workbook
    planted: list[str] = []
    wb = Workbook()
    ws = wb.active
    ws.title = "고객"
    ws.append([NOTICE])
    ws.append(["회원번호", "이름", "휴대전화", "이메일", "배송지", "최근 주문번호", "메모"])
    names = DICT_CUSTOMERS + [f.name() for _ in range(5)]
    for n in names:
        row = [f.member_no(), n, f.phone(), f.email(), f.address(), f.order_no(),
               f.r.choice(["VIP", "반품 2회", "배송 메모: 문 앞", "-", "교환 요청 중"])]
        ws.append(row)
        planted += [row[0], n, row[2], row[3], row[4]]
    refund = wb.create_sheet("환불")
    refund.append(["이름", "은행", "계좌번호", "금액(원)"])
    for n in names[:4]:
        acc = f.account()
        refund.append([n, f.r.choice(["가상은행", "예시뱅크"]), acc, f.r.randint(1, 30) * 1000])
        planted.append(acc)
    wb.save(path)
    return planted


def make_docx(f: Fake, path: Path) -> list[str]:
    from docx import Document
    customer, staff = f.name(), DICT_STAFF
    phone, staff_phone, email, addr = f.phone(), f.phone(), f.email(), f.address()
    order = f.order_no()
    doc = Document()
    doc.add_paragraph(NOTICE)
    doc.add_heading("배송 지연 사과문 (초안)", level=1)
    doc.add_paragraph(f"{customer} 고객님께,")
    doc.add_paragraph(f"주문하신 상품(주문번호 {order})의 출고가 물류센터 사정으로 3일가량 늦어지고 있습니다. "
                      f"등록하신 배송지 {addr}로 11월 15일까지 도착하도록 하겠습니다.")
    doc.add_paragraph(f"배송 일정이 바뀌면 {phone}로 문자를 드리겠습니다. 불편을 드려 죄송합니다.")
    doc.add_paragraph(f"담당: 고객지원팀 {staff} (전화 {staff_phone}, 이메일 {email})")
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text, table.rows[0].cells[2].text = "상품", "수량", "보상"
    for item in f.r.sample(ITEMS, 2):
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = item, "1", "적립금 3,000원"
    doc.save(path)
    return [customer, staff, phone, staff_phone, email, addr]


def make_txt(f: Fake, path: Path) -> list[str]:
    a, b = f.name(), f.name()
    rrn, card, phone, addr, mem = f.rrn(), f.r.choice(TEST_CARDS), f.phone(), f.address(), f.member_no()
    lines = [
        NOTICE, "",
        "[상담 기록] 2026-11-10 14:02 채팅 상담",
        f"고객: 안녕하세요, {a}입니다. 회원번호는 {mem}이에요.",
        "상담원: 네 고객님, 무엇을 도와드릴까요?",
        f"고객: 결제한 카드({card})가 두 번 승인된 것 같아요.",
        f"고객: 본인 확인용으로 주민번호 {rrn} 불러 드릴게요.",
        "상담원: 주민번호는 말씀하지 않으셔도 됩니다. 연락처만 확인하겠습니다.",
        f"고객: {phone}이고, 배송지는 {addr}입니다.",
        f"상담원: 확인했습니다. 중복 승인은 오늘 안에 취소하고, 담당자 {b}가 다시 연락드리겠습니다.",
        f"메모: {a} 고객 중복 결제 건, X-프로젝트 정산 대상 아님.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [a, b, rrn, card, phone, addr, mem]


def make_csv(f: Fake, path: Path) -> list[str]:
    planted: list[str] = []
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([NOTICE])
        w.writerow(["이름", "휴대전화", "이메일", "생년월일 확인", "가입 경로"])
        for _ in range(6):
            row = [f.name(), f.phone(), f.email(), f.rrn(), f.r.choice(["앱", "웹", "매장"])]
            w.writerow(row)
            planted += row[:4]
    return planted


def make_pdf(f: Fake, path: Path) -> list[str]:
    """한글 PDF: HTML을 만들고 Edge(헤드리스)로 인쇄한다. Edge가 없으면 건너뛴다."""
    name, phone, email, addr = f.name(), f.phone(), f.email(), f.address()
    account, card = f.account(), f.r.choice(TEST_CARDS)
    html = f"""<!doctype html><meta charset="utf-8"><style>
body{{font-family:'Malgun Gothic',sans-serif;margin:40px;font-size:14px}} h1{{font-size:22px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #999;padding:6px 10px;text-align:left}}
.n{{color:#666;font-size:12px}}</style>
<p class="n">{NOTICE}</p><h1>환불 요청서</h1>
<table><tr><th>신청인</th><td>{name}</td></tr><tr><th>연락처</th><td>{phone}</td></tr>
<tr><th>이메일</th><td>{email}</td></tr><tr><th>주소</th><td>{addr}</td></tr>
<tr><th>환불 계좌</th><td>가상은행 {account}</td></tr><tr><th>결제 카드</th><td>{card}</td></tr>
<tr><th>주문번호</th><td>{f.order_no()}</td></tr><tr><th>사유</th><td>사이즈가 맞지 않아 반품합니다.</td></tr></table>
<p>위와 같이 환불을 요청합니다.</p><p>2026년 11월 11일 &nbsp; 신청인 {name} (서명)</p>"""
    if not EDGE.exists():
        print("  Edge가 없어 PDF는 건너뜁니다")
        return []
    tmp = path.with_suffix(".html")
    tmp.write_text(html, encoding="utf-8")
    subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={path}", tmp.resolve().as_uri()], check=True, timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.unlink()
    return [name, phone, email, addr, account, card]


def check(files: dict[Path, list[str]]) -> None:
    """게이트웨이와 같은 가명화 부품으로 검사: 넣은 값이 가린 글에 그대로 남았으면 놓친 값이다."""
    from gateway.config import load_settings
    from gateway.files.scan import scan
    from gateway.masking.masker import RuleMasker
    masker = RuleMasker.from_config(load_settings().config.masking)
    for path, planted in files.items():
        r = scan(path.name, path.read_bytes(), masker)
        left = sorted({v for v in planted if v in r.masked_text})
        counts = ", ".join(f"{k} {n}" for k, n in sorted(r.counts.items()))
        print(f"  {path.name}: {counts} | 넣은 값 {len(set(planted))}개 중 남은 것 {len(left)}개"
              + (f" → {left}" if left else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None, help="같은 파일을 다시 만들 때 (없으면 무작위)")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "demo-files")
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(10_000)
    args.out.mkdir(parents=True, exist_ok=True)
    f = Fake(seed)
    files = {
        args.out / "고객명단_가상.xlsx": make_xlsx(f, args.out / "고객명단_가상.xlsx"),
        args.out / "배송지연_사과문_가상.docx": make_docx(f, args.out / "배송지연_사과문_가상.docx"),
        args.out / "상담기록_가상.txt": make_txt(f, args.out / "상담기록_가상.txt"),
        args.out / "회원목록_가상.csv": make_csv(f, args.out / "회원목록_가상.csv"),
    }
    pdf = args.out / "환불요청서_가상.pdf"
    if planted := make_pdf(f, pdf):
        files[pdf] = planted
    print(f"만든 파일 (seed {seed}): {args.out}")
    check(files)


if __name__ == "__main__":
    main()
