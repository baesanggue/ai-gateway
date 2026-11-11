"""웹 체험판 (#90, 11/11 사용자 결정): 테스터에게 이 PC의 화면을 인터넷 주소로 연다. 완성품은 설치형이다.

- `.env`에 BETA_PASSWORD가 있으면 켜진다. 공용 비밀번호로 들어온다(모든 메뉴 앞)
- 테스터는 게이트웨이 하나를 같이 쓴다. 그래서 현황·요청 기록은 **내가 보낸 요청만**, 설정은 **보기만**이다.
  사용자 사전 내용은 보이지 않는다
- 사용량: 한 사람(브라우저 세션) per_session회, 하루 전체 per_day회 (config.yaml beta). API 비용을 묶는다.
  세션 상한은 새로고침하면 다시 시작되므로, 비용을 확실히 묶는 것은 하루 전체 상한이다
- 게이트웨이·Ollaya는 그대로 이 PC 안(127.0.0.1)에만 있다. 인터넷에 열리는 것은 화면뿐이다
"""
import hmac
import ipaddress
import threading
import time
from datetime import date, datetime

import streamlit as st

# 비밀번호 틀림 (11/11 보안 검토): 세션마다 세면 새로고침으로 끝없이 시도할 수 있어 서버에서 센다
PER_CLIENT_FAILS = 5           # 접속 주소 하나가 WINDOW 안에 이만큼 틀리면 그 주소를 LOCK 동안 막는다
GLOBAL_FAILS = 50              # 모든 주소를 합쳐 WINDOW 안에 이만큼 틀리면 모두 LOCK 동안 막는다 (주소를 바꿔 가며 시도할 때)
WINDOW = 600
LOCK = 600

GUIDE = """**체험판입니다.** 이름·전화번호·주소는 **가상의 값만** 쓰세요. 입력한 글은 운영자 PC를 거치고, 개인정보는 가려서
외부 AI로 보냅니다. 시나리오 밖의 요청은 거부되거나, 가리지 못한 정보가 나갈 수 있습니다.

**해 볼 것** — 왼쪽 › 칸에 그대로 입력하세요

1. `주문한 거 배송 언제 와요?` → LLM 없이 정해 둔 답 (비용 0)
2. `김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘` → 밖으로는 `[고객1]`·`[전화번호1]`만, 받은 답변에는 원래 이름·번호
3. `이 주민번호 900101-1234567로 고객 나이 계산해줘` → 실제 값이 필요한 요청은 막고 안내
4. 가상의 이름·주소를 넣어 자유롭게 → 이 칸과 아래 두 칸에서 무엇이 가려졌는지 확인
"""


class Quota:
    """하루 전체 요청 수 (모든 세션이 같이 쓴다). 날짜가 바뀌면 0부터."""

    def __init__(self, per_day: int):
        self.per_day = per_day
        self.day: date | None = None
        self.used = 0
        self._lock = threading.Lock()          # 세션마다 다른 스레드에서 부른다

    def take(self, today: date | None = None) -> bool:
        today = today or datetime.now().astimezone().date()
        with self._lock:
            if self.day != today:
                self.day, self.used = today, 0
            if self.used >= self.per_day:
                return False
            self.used += 1
            return True


class LoginGuard:
    """비밀번호 틀림을 모든 세션에 걸쳐 센다: 접속 주소별, 그리고 전체."""

    def __init__(self, per_client: int = PER_CLIENT_FAILS, global_max: int = GLOBAL_FAILS,
                 window: float = WINDOW, lock: float = LOCK):
        self.per_client, self.global_max, self.window, self.lock = per_client, global_max, window, lock
        self._fails: dict[str, list[float]] = {}
        self._all: list[float] = []
        self._locked_until: dict[str, float] = {}       # 주소별. "*"는 모두
        self._lock = threading.Lock()

    def locked(self, client: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            return any(self._locked_until.get(k, 0) > now for k in (client, "*"))

    def fail(self, client: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            recent = [t for t in self._fails.get(client, []) if now - t < self.window] + [now]
            self._fails[client] = recent
            self._all = [t for t in self._all if now - t < self.window] + [now]
            if len(recent) >= self.per_client:
                self._locked_until[client] = now + self.lock
                self._fails[client] = []
            if len(self._all) >= self.global_max:
                self._locked_until["*"] = now + self.lock
                self._all = []

    def succeed(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)


def password_ok(given: str, secret: str) -> bool:
    """상수 시간 비교 (몇 글자까지 맞았는지 시간으로 드러나지 않게)."""
    return bool(secret) and hmac.compare_digest(given.encode("utf-8"), secret.encode("utf-8"))


# ── Streamlit ──

def enabled(s) -> bool:
    secret = s.secrets.beta_password
    return secret is not None and bool(secret.get_secret_value())


@st.cache_resource
def _quota(per_day: int) -> Quota:
    return Quota(per_day)


@st.cache_resource
def _guard() -> LoginGuard:
    return LoginGuard()


def _client() -> str:
    """접속한 쪽의 주소 (비밀번호 틀림을 셀 때). 화면은 127.0.0.1에서만 열리고 인터넷 요청은 이 PC의 Cloudflare 터널을
    거쳐 오므로, 소켓 주소가 루프백일 때만 터널이 붙인 CF-Connecting-IP를 믿는다. 아니면 소켓 주소를 쓴다
    (11/11 보안 검토: 직접 들어온 요청이 머리글을 꾸며 주소별 잠금을 피하지 못하게)."""
    return client_key(st.context.ip_address, st.context.headers.get("Cf-Connecting-Ip"))


def client_key(socket_ip: str | None, forwarded: str | None) -> str:
    """Streamlit은 루프백 연결(127.0.0.1, ::1)의 주소를 None으로 준다(st.context.ip_address). 터널은 이 PC에서 루프백으로
    들어오므로 None도 루프백으로 본다. 그때만, 그리고 IP 주소 모양일 때만 터널 머리글을 쓴다 (11/11 보안 검토 두 번)."""
    try:
        loopback = socket_ip is None or ipaddress.ip_address(socket_ip).is_loopback
    except ValueError:
        loopback = False
    if loopback and forwarded:
        try:
            return f"cf:{ipaddress.ip_address(forwarded.strip())}"
        except ValueError:
            pass
    return socket_ip or "local"


def gate(s) -> None:
    """체험판이면 비밀번호를 받는다. 통과하지 않으면 여기서 멈춘다(메뉴도 그리지 않는다)."""
    if not enabled(s) or st.session_state.get("beta_ok"):
        return
    from admin.ui import page_header
    st.markdown("<div style='height:2.2rem'></div>", unsafe_allow_html=True)   # 메뉴가 없는 화면이라 위 막대에 제목이 가린다
    page_header("가림게이트 체험판", "API 주소만 바꾸면 개인정보는 가려서 보내고 답변은 내 PC에서 되돌리는 게이트웨이의 체험판입니다. "
                                 "설문 안내 메시지에 있는 비밀번호를 넣어 주세요.")
    guard, client = _guard(), _client()
    box, _ = st.columns([1, 1.6])
    with box, st.form("beta_login", border=True):
        given = st.text_input("비밀번호", type="password")
        sent = st.form_submit_button("들어가기", type="primary")
    if sent:
        if guard.locked(client):                                  # 막혀 있으면 맞는지 보지도 않는다
            st.error("비밀번호를 여러 번 틀린 시도가 있어 잠시 막았습니다. 10분 뒤에 다시 시도해 주세요.")
        elif password_ok(given, s.secrets.beta_password.get_secret_value()):
            guard.succeed(client)
            st.session_state.beta_ok = True
            st.rerun()
        else:
            guard.fail(client)
            time.sleep(1)                                         # 하나씩 맞혀 보기를 늦춘다
            st.error("비밀번호가 맞지 않습니다.")
    st.stop()


def allow_request(s) -> str | None:
    """요청을 보내도 되면 None, 아니면 안내 문구. 보내도 되면 세션·하루 횟수를 하나씩 쓴다."""
    if not enabled(s):
        return None
    used = st.session_state.get("beta_used", 0)
    if used >= s.config.beta.per_session:
        return f"체험 횟수({s.config.beta.per_session}회)를 다 썼습니다. 참여해 주셔서 감사합니다. 설문을 이어서 작성해 주세요."
    if not _quota(s.config.beta.per_day).take():
        return "오늘 체험판의 전체 사용량을 다 썼습니다. 내일 다시 시도해 주세요."
    st.session_state.beta_used = used + 1
    return None


def remember(request_id: str | None) -> None:
    """이 세션이 보낸 요청 ID. 현황·요청 기록은 이것만 보여 준다."""
    if request_id:
        st.session_state.setdefault("beta_requests", []).append(request_id)


def my_requests() -> list[str]:
    return list(st.session_state.get("beta_requests", []))


def remaining(s) -> int:
    return max(s.config.beta.per_session - st.session_state.get("beta_used", 0), 0)


def my_rows(log) -> list[dict]:
    """이 세션이 보낸 요청의 기록 (최근 것부터). 다른 테스터의 요청은 보이지 않는다."""
    rows = [r for r in (log.get(i) for i in my_requests()) if r]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)
