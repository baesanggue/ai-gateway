"""연동 안내 화면 (#133, 11/21 사용자 선택: 왼쪽 메뉴에 따로). 기존 서비스 개발자가 API 주소 한 줄을 바꿔 붙이는 방법.

설문(D31): 개발 직군 응답 "내 코드에서 이 서비스를 쓴다면 어떻게 써야 할지 감이 안 온다",
"주소만 바꾸는 도입이 쉬워 보임" 3.9점(방식은 이해했지만 실제로 붙이는 방법이 안 보였다).
- 예시 코드는 python_example·js_example에서만 만든다. 화면과 docs/integration.md가 같은 코드를 쓰고,
  테스트가 화면의 Python 예시를 한 글자도 바꾸지 않고 실행해 게이트웨이로 가는지 본다 (tests/test_integration_page.py)
- 오류 표(ERRORS)는 게이트웨이가 돌려주는 오류 code와 같은지 테스트로 묶는다
- 게이트웨이 키는 값을 보이지 않는다. .env에 GATEWAY_API_KEYS가 있는지만 보인다
"""
import html

import streamlit as st

from admin.ui import gateway_url, page_header, settings

# 시연 ② 문장 (이름·번호는 가상). 예시 모델 이름은 "원래 쓰던 이름 그대로"를 보이려고 고성능 모델 이름을 쓴다
SAMPLE = "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘"
SAMPLE_MODEL = "gpt-6.1-sol"


# 예시 코드는 한 줄을 80칸(한글은 2칸) 안으로 쓴다. 화면의 코드 칸(1200×800 창)에서 가로로 잘리지 않게
def python_example(base_url: str) -> str:
    """Python(openai 패키지) 예시. base_url은 게이트웨이 주소 + /v1."""
    return f'''from openai import OpenAI

client = OpenAI(
    base_url="{base_url}",  # ← 이 줄만 더하거나 바꿉니다
    api_key="local",  # 같은 PC는 아무 값 (게이트웨이 키를 정했으면 그 키)
)
question = "{SAMPLE}"
res = client.chat.completions.create(
    model="{SAMPLE_MODEL}",  # 그대로 둡니다 (모델은 가림게이트가 고름)
    messages=[{{"role": "user", "content": question}}],
)
print(res.choices[0].message.content)  # 이름·번호를 되돌린 답변
'''


def js_example(base_url: str) -> str:
    """JavaScript(Node.js, openai 패키지) 예시. 맨 위 import와 await를 쓰므로 .mjs 파일로 저장한다."""
    return f'''import OpenAI from "openai";

const client = new OpenAI({{
  baseURL: "{base_url}",  // ← 이 줄만 더하거나 바꿉니다
  apiKey: "local",  // 같은 PC는 아무 값 (게이트웨이 키를 정했으면 그 키)
}});
const question = "{SAMPLE}";
const res = await client.chat.completions.create({{
  model: "{SAMPLE_MODEL}",  // 그대로 둡니다 (모델은 가림게이트가 고름)
  messages: [{{ role: "user", content: question }}],
}});
console.log(res.choices[0].message.content);  // 이름·번호를 되돌린 답변
'''


def python_stream_example(base_url: str) -> str:
    """stream=True 예시 (#84, docs/integration.md). 답을 다 만들고 되돌린 뒤 조각으로 온다."""
    return f'''from openai import OpenAI

client = OpenAI(base_url="{base_url}", api_key="local")
question = "{SAMPLE}"
stream = client.chat.completions.create(
    model="{SAMPLE_MODEL}",
    messages=[{{"role": "user", "content": question}}],
    stream=True,  # 답을 다 만든 뒤 조각으로 옵니다
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
print()
'''


def js_stream_example(base_url: str) -> str:
    """stream: true 예시 (#84, docs/integration.md)."""
    return f'''import OpenAI from "openai";

const client = new OpenAI({{
  baseURL: "{base_url}",
  apiKey: "local",
}});
const question = "{SAMPLE}";
const stream = await client.chat.completions.create({{
  model: "{SAMPLE_MODEL}",
  messages: [{{ role: "user", content: question }}],
  stream: true,  // 답을 다 만든 뒤 조각으로 옵니다
}});
for await (const chunk of stream) {{
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}}
process.stdout.write("\\n");
'''


# 응답에서 알 수 있는 것: (보는 곳, 뜻)
RESPONSE = [
    ("choices[0].message.content", "답변. 가린 이름·번호는 이 PC에서 원래 값으로 되돌려 놓았습니다"),
    ('finish_reason: "content_filter"', "막은 요청입니다. content에 안내문이 들어 있습니다(HTTP 200이라 오류로 오지 않습니다)"),
    ("model", "LLM을 불렀으면 실제로 쓴 모델, 정해 둔 답·막음이면 보낸 이름 그대로"),
    ("usage", "LLM 토큰 수. 정해 둔 답·막음은 0입니다"),
    ("헤더 x-gateway-request-id", "요청 기록에서 이 요청을 찾는 번호. Python은 with_raw_response, JS는 .withResponse()로 읽습니다"),
]

# 오류: (HTTP 상태, code, 언제, 할 일). 본문은 OpenAI 형식 {"error": {"message", "type", "param", "code"}}
ERRORS = [
    (400, "invalid_request", "요청 형식이 틀렸습니다(model·messages가 없는 등)", "param에 적힌 칸을 고칩니다"),
    (400, "unsupported_content", "이미지 등 텍스트가 아닌 내용을 보냈습니다", "텍스트만 보냅니다"),
    (401, "invalid_api_key", "게이트웨이 키가 틀렸거나, 키를 정하지 않았는데 다른 PC에서 보냈습니다", "키를 확인합니다"),
    (403, "forbidden_origin", "웹페이지(브라우저)가 직접 보냈습니다", "서버 코드에서 부릅니다"),
    (502, "upstream_error", "LLM의 답을 받지 못했습니다", "잠시 뒤 다시 보냅니다(SDK가 기본으로 다시 시도합니다)"),
]


def _key_status(s) -> str:
    """게이트웨이 키 규칙 (D20). 값은 보이지 않는다."""
    keys = s.secrets.gateway_api_keys
    if keys and any(k.strip() for k in keys.get_secret_value().split(",")):
        return ("<b>정함</b> — 모든 요청에 <code>Authorization: Bearer &lt;게이트웨이 키&gt;</code>가 필요합니다. "
                "SDK에서는 api_key에 넣습니다. 키는 <code>.env</code>의 <code>GATEWAY_API_KEYS</code>에 있고 여기에는 보이지 않습니다")
    return ("<b>정하지 않음</b> — 같은 PC의 프로그램이 보낸 요청만 받습니다. api_key에는 아무 값이나 넣습니다"
            "(SDK는 키 칸이 비어 있으면 시작하지 않습니다). 다른 PC에서 쓰려면 <code>.env</code>에 "
            "<code>GATEWAY_API_KEYS</code>를 정합니다")


def _connection(s, base: str) -> str:
    llm = s.config.llm
    rows = [
        ("주소", f"<code>{html.escape(base)}</code> (원래는 OpenAI 주소 <code>https://api.openai.com/v1</code>)"),
        ("게이트웨이 키", _key_status(s)),
        ("OpenAI 키", "서비스 코드에는 필요 없습니다. 게이트웨이의 <code>.env</code>에만 둡니다. "
                      "서비스가 보낸 키는 밖으로 넘기지 않고 기록하지도 않습니다"),
        ("모델", "아무 이름이나 됩니다. 요청마다 정해 둔 답 · 저렴한 모델"
                 f"(<code>{html.escape(llm.cheap.model)}</code>) · 고성능 모델(<code>{html.escape(llm.premium.model)}</code>) · "
                 "막음 중에서 가림게이트가 고릅니다"),
        ("보낼 수 있는 것", "텍스트 메시지(system·user·assistant). <code>stream=true</code>도 됩니다(#84): 답을 다 만들고 "
                            "이름·번호를 되돌린 뒤 조각으로 보내므로, 첫 글자가 오기까지 시간은 같습니다"),
    ]
    kv = "".join(f"<span>{k}</span><div>{v}</div>" for k, v in rows)
    return f"<div class='gw-box'><div class='hd'>연결 정보</div><div class='bd'><div class='gw-kv int'>{kv}</div></div></div>"


def _table(title: str, head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (f"<div class='gw-box int'><div class='hd'>{title}</div><div class='bd'>"
            f"<table class='gw-tbl'><tr>{th}</tr>{body}</table></div></div>")


def page() -> None:
    s = settings()
    base = f"{gateway_url(s)}/v1"
    page_header("연동 안내", "기존 서비스는 OpenAI SDK의 API 주소(base URL)만 가림게이트로 바꾸면 됩니다. 나머지 코드는 그대로입니다.")
    st.markdown(_connection(s, base), unsafe_allow_html=True)

    with st.container(key="int_code"):
        st.markdown("<div class='gw-int-hd'>코드 예시</div>", unsafe_allow_html=True)
        py, js = st.tabs(["Python", "JavaScript (Node.js)"])
        with py:
            st.code(python_example(base), language="python")
            st.caption("openai 패키지(pip install openai)를 이미 쓰고 있다면 그대로 씁니다. 오른쪽 위 단추로 복사합니다.")
        with js:
            st.code(js_example(base), language="javascript")
            st.caption("Node.js 18 이상, npm install openai. 파일 이름을 .mjs로 저장합니다(맨 위 import와 await). "
                       "웹페이지(브라우저)에서 직접 부르면 막힙니다(403). 서버 코드에서 부릅니다.")

    # 표는 위아래로 놓는다. 나란히 두면 긴 이름(choices[0].message.content)이 줄을 못 바꿔 설명 칸이 잘린다
    st.markdown(_table("응답에서 알 수 있는 것", ["보는 곳", "뜻"],
                       [[f"<code>{html.escape(k)}</code>", html.escape(v)] for k, v in RESPONSE]), unsafe_allow_html=True)
    st.markdown(_table("오류와 할 일", ["상태", "code", "언제", "할 일"],
                       [[str(status), f"<code>{code}</code>", html.escape(when), html.escape(todo)]
                        for status, code, when, todo in ERRORS]), unsafe_allow_html=True)
    st.caption("오류 본문은 OpenAI 형식이라 SDK에서는 예외로 옵니다(Python openai.APIStatusError, JS OpenAI.APIError — "
               "status와 code로 구별). 스트리밍 예시, 파일 속 개인정보 검사(POST /v1/files/scan)와 상태(GET /healthz)는 "
               "docs/integration.md에 있습니다.")
