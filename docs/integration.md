# 기존 서비스에 붙이기 (개발자용, #133)

기존 서비스가 OpenAI SDK를 쓰고 있다면 **API 주소(base URL) 한 줄만** 가림게이트로 바꾼다. 나머지 코드는 그대로다.
같은 내용이 프로그램 화면의 **연동 안내** 메뉴에 있다. 화면은 이 PC의 실제 주소와 게이트웨이 키 설정을 보여 준다.

> 설문(D31)에서 개발 직군 응답자가 "내 코드에서 이 서비스를 쓴다면 어떻게 써야 할지 감이 안 온다"고 적어 만들었다.
> 아래 예시는 화면과 같은 함수(`admin/integration_page.py`)에서 만든다. Python 예시는 테스트(`tests/test_integration_page.py`)가
> 그대로 실행해 게이트웨이를 거치는지 확인한다.

## 코드 예시

설치 직후 주소는 `http://127.0.0.1:8000/v1`이다(`config.yaml`의 `gateway.host`·`gateway.port`).

### Python
`pip install openai`(이미 쓰고 있다면 그대로).

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",  # ← 이 줄만 더하거나 바꿉니다
    api_key="local",  # 같은 PC는 아무 값 (게이트웨이 키를 정했으면 그 키)
)
question = "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘"
res = client.chat.completions.create(
    model="gpt-6.1-sol",  # 그대로 둡니다 (모델은 가림게이트가 고름)
    messages=[{"role": "user", "content": question}],
)
print(res.choices[0].message.content)  # 이름·번호를 되돌린 답변
```

### JavaScript (Node.js)
Node.js 18 이상, `npm install openai`. 맨 위 `import`와 `await`를 쓰므로 파일 이름을 `.mjs`로 저장한다.

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8000/v1",  // ← 이 줄만 더하거나 바꿉니다
  apiKey: "local",  // 같은 PC는 아무 값 (게이트웨이 키를 정했으면 그 키)
});
const question = "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘";
const res = await client.chat.completions.create({
  model: "gpt-6.1-sol",  // 그대로 둡니다 (모델은 가림게이트가 고름)
  messages: [{ role: "user", content: question }],
});
console.log(res.choices[0].message.content);  // 이름·번호를 되돌린 답변
```

- **웹페이지(브라우저)에서 직접 부르지 않는다.** 같은 PC의 웹페이지가 보낸 요청도 막는다(403 `forbidden_origin`). 악성 웹페이지가 회사의 OpenAI 키를 대신 쓰지 못하게 하려는 것이다(#12 보안 검토). 서버 코드에서 부른다.
- 다른 언어는 같은 JSON을 `POST {주소}/chat/completions`로 보내면 된다(OpenAI Chat Completions 형식).

### 스트리밍 (`stream=true`, #84)
기존 서비스가 스트리밍으로 받고 있어도 그대로 된다. 가림게이트는 답을 다 만들고 이름·번호를 되돌린 뒤 OpenAI 스트리밍 형식(SSE)의 조각으로 보낸다. LLM의 답을 조각째 넘기면 `[고객1]`이 조각 경계에서 잘려 되돌리기 어렵고, 되돌리기 전 조각이 나가면 가명이 그대로 보이기 때문이다(D32). 그래서 **첫 글자가 오기까지 시간은 `stream=false`와 같다.**

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="local")
question = "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘"
stream = client.chat.completions.create(
    model="gpt-6.1-sol",
    messages=[{"role": "user", "content": question}],
    stream=True,  # 답을 다 만든 뒤 조각으로 옵니다
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
print()
```

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8000/v1",
  apiKey: "local",
});
const question = "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘";
const stream = await client.chat.completions.create({
  model: "gpt-6.1-sol",
  messages: [{ role: "user", content: question }],
  stream: true,  // 답을 다 만든 뒤 조각으로 옵니다
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}
process.stdout.write("\n");
```

- 순서: 역할 조각 → 내용 → 끝난 이유(`finish_reason`, 막은 요청은 `content_filter`) → `data: [DONE]`. `stream_options: {"include_usage": true}`를 보내면 `[DONE]` 앞에 토큰 수 조각(`choices`는 빈 배열)이 하나 더 온다.
- 오류는 보내기 전에 정해지므로 스트리밍이어도 보통 오류 응답(상태 코드 + 오류 본문)으로 온다. SDK는 stream을 열기 전에 예외로 알린다.

## 키

| 키 | 어디에 | 설명 |
|---|---|---|
| OpenAI 키 | 게이트웨이의 `.env` (`OPENAI_API_KEY`) | 서비스 코드에는 필요 없다. 서비스가 보낸 키는 밖으로 넘기지 않고 기록하지도 않는다 |
| 게이트웨이 키 | 게이트웨이의 `.env` (`GATEWAY_API_KEYS`, 쉼표로 여러 개) | **정하지 않으면** 같은 PC의 프로그램이 보낸 요청만 받는다. 이때 SDK의 `api_key`에는 아무 값이나 넣는다(SDK는 키 칸이 비어 있으면 시작하지 않는다). **정하면** 모든 요청에 `Authorization: Bearer <키>`가 필요하다. SDK에서는 `api_key`에 넣는다 (D20) |

화면에는 게이트웨이 키가 정해져 있는지만 나오고, 값은 나오지 않는다.

## 보내는 것

- `model`: 아무 이름이나 된다. 요청마다 정해 둔 답 · 저렴한 모델 · 고성능 모델 · 막음 중에서 가림게이트가 고른다(`config.yaml`의 `llm.cheap`·`llm.premium`). 완전 로컬 수준이면 밖으로 보낼 수 없는 요청은 이 PC의 로컬 LLM이 답하고, 응답의 `model`에 그 이름(`llm.local.model`)이 온다(#136).
- `messages`: 텍스트 메시지(system·developer·user·assistant). 이미지 등 텍스트가 아닌 조각은 받지 않는다.
- `stream`: `true`도 된다(위 스트리밍). `max_completion_tokens`(또는 `max_tokens`)는 LLM에 넘긴다. 그 밖의 필드는 받아 두지만 쓰지 않는다.

## 응답에서 알 수 있는 것

| 보는 곳 | 뜻 |
|---|---|
| `choices[0].message.content` | 답변. 가린 이름·번호는 이 PC에서 원래 값으로 되돌려 놓았다 |
| `finish_reason: "content_filter"` | 막은 요청이다. `content`에 안내문이 들어 있다(HTTP 200이라 오류로 오지 않는다) |
| `model` | LLM을 불렀으면 실제로 쓴 모델, 정해 둔 답·막음이면 보낸 이름 그대로 |
| `usage` | LLM 토큰 수. 정해 둔 답·막음은 0 |
| 헤더 `x-gateway-request-id` | 요청 기록에서 이 요청을 찾는 번호. Python은 `client.chat.completions.with_raw_response.create(...)`의 `.headers`, JS는 `.withResponse()`의 `response.headers`로 읽는다 |

## 오류

본문은 OpenAI 형식 `{"error": {"message", "type", "param", "code"}}`이라 SDK에서는 예외로 온다(Python `openai.APIStatusError`, JS `OpenAI.APIError`). 상태와 `code`로 구별한다.

| 상태 | code | 언제 | 할 일 |
|---|---|---|---|
| 400 | `invalid_request` | 요청 형식이 틀렸다(`model`·`messages`가 없는 등) | `param`에 적힌 칸을 고친다 |
| 400 | `unsupported_content` | 이미지 등 텍스트가 아닌 내용을 보냈다 | 텍스트만 보낸다 |
| 401 | `invalid_api_key` | 게이트웨이 키가 틀렸거나, 키를 정하지 않았는데 다른 PC에서 보냈다 | 키를 확인한다 |
| 403 | `forbidden_origin` | 웹페이지(브라우저)가 직접 보냈다 | 서버 코드에서 부른다 |
| 502 | `upstream_error` | LLM의 답을 받지 못했다 | 잠시 뒤 다시 보낸다(SDK가 기본으로 다시 시도한다) |

## 그 밖의 API

| API | 내용 |
|---|---|
| `POST /v1/files/scan` | 파일(PDF·워드·엑셀·텍스트) 속 개인정보의 종류·개수·위치와 가명화된 텍스트. 파일은 이 PC 밖으로 나가지 않는다. 키 규칙은 채팅과 같다 (`docs/file-scan.md`) |
| `GET /healthz` | 켜져 있는지와 보안 수준·업종·사전 종류 |
