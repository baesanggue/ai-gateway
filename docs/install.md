# 설치와 실행 (Windows, #77)

받은 폴더에서 스크립트 셋으로 설치·실행·끄기를 한다. macOS·Linux는 #85(M6).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1   # 설치 (한 번, 다시 실행해도 된다)
powershell -ExecutionPolicy Bypass -File scripts\start.ps1     # 켜기 + 프로그램 창
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1      # 끄기
```

## install.ps1이 하는 일

밖에서 받는 것은 시작 전에 목록을 보여 주고 확인받는다(`-Yes`면 묻지 않는다). 이미 있는 것은 건너뛴다.

| 단계 | 하는 일 | 받는 곳 · 고정 방법 |
|---|---|---|
| 1. uv | 없으면 설치 | astral.sh 공식 설치 스크립트 |
| 2. 파이썬 패키지 | `uv sync --frozen` | `uv.lock`에 고정한 버전. 파이썬 3.11+가 없으면 uv가 받는다 |
| 3. Ollaya | 없으면 **CPU용만** 설치(약 50MB, `OLLAYA_NO_CUDA=1`) | ollaya.dev 공식 설치 스크립트(sha256 확인, `docs/ollaya-notes.md` §2). GPU 팩 1.4GB는 가벼움 원칙에 맞지 않아 받지 않는다 |
| 4. 판단 모델 | `gliclass:v4-edge` 약 895MB | **측정한 버전의 digest로 고정**: `sha256:3b1849b2b5ec38109cbf8772715bccaf8f546ab48f6ecefdee41f4d6af1c5b39` (`ollaya pull 모델@digest`는 그 버전을 digest로 확인한 뒤 저장한다). 태그가 나중에 다른 버전을 가리켜도 측정값(D25)과 같은 모델을 쓴다 |
| 5. 이름 인식 모델 | 3개 파일 약 15.5MB | huggingface.co `atonlee/koelectra-ko-pii-ner` 고정 커밋, **SHA-256이 다르면 지우고 멈춘다** (D23, `docs/ner-model.md`) |
| 6. `.env` | 없으면 `.env.example`에서 만들고 OpenAI API 키를 받는다 | 입력은 화면에 보이지 않고 어디에도 출력하지 않는다. `.env`는 커밋되지 않는다 |
| 7. 사용자 사전 | 없으면 업종 프리셋의 시연용 가상 사전을 복사 | 실제 고객명이 가려지지 않으므로 노란 글씨로 알린다(D21). 화면의 [설정 > 사용자 사전]에서 바꾼다 |
| 8. 확인 | 가명화 부품을 게이트웨이와 같은 코드로 한 번 만든다 | 사전 형식·이름 인식 모델 해시를 확인한다 |

- 시연용 챗봇 지시문 등록(#73)은 설치에서 하지 않는다. 화면의 [설정 > 등록 지시문]에서 한다(D26).

## start.ps1 / stop.ps1

- Ollaya(11435) → 게이트웨이(8000) → 화면(8501)을 창 없이 켜고, 프로그램 창(`open-app.ps1`, 1200×800)을 연다. `-NoWindow`면 창을 열지 않는다
- 이미 켜져 있는 것(포트가 열려 있음)은 건드리지 않는다. 이 스크립트가 켠 것만 `%LOCALAPPDATA%\ai-gateway\pids.txt`에 적고, `stop.ps1`은 그것만 끈다(그 전부터 켜져 있던 Ollaya는 그대로)
- PID가 그사이 다른 프로그램에 다시 쓰였을 수 있어, 적어 둔 시작 시각이 같을 때만 끈다. `uv run`의 자식 파이썬까지 함께 끈다
- 실행 기록은 `%LOCALAPPDATA%\ai-gateway\logs\`. 게이트웨이는 가명화된 정보만, Ollaya는 판단할 글을 기록하지 않는다
- 켜지지 않으면 그 프로그램의 오류 기록 마지막 줄을 보여 준다(사전이 없거나 모델 해시가 다르면 게이트웨이가 시작하지 않는다, D21·D23)

## 스크립트 파일

- Windows PowerShell 5.1은 BOM 없는 파일을 ANSI(cp949)로 읽어 한글이 깨진다. 그래서 `.ps1`은 **UTF-8(BOM) + CRLF**로 저장한다
- 5.1에서는 외부 명령의 stderr가 오류로 바뀔 수 있어, 외부 명령은 종료 코드로 판단한다(`Invoke-Native`)
