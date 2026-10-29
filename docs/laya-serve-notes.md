# laya-serve 확인 노트 (#20)

- 확인일: 2026-10-29 · `laya` 0.4.17 · `torch` 2.15.0+cpu · Windows 11 · Python 3.13
- 목적: 파인튜닝한 Laya 가중치(로컬 폴더)를 `/v1/systemone`으로 서빙할 수 있는지 확인한다. 안 되면 파인튜닝 트랙을 중단한다 (D15).
- 출처: [Laya README](https://github.com/NandhaKishorM/laya) "Self-Hosting: HTTP Server (Jev-compatible)"
- **결과: 통과.** 로컬 폴더로 등록한 체크포인트가 `/v1/systemone`에 한국어 판단으로 응답했다 → #21로 진행

## 설치 (게이트웨이와 분리된 가상환경)

게이트웨이 의존성에 PyTorch가 섞이지 않도록 `finetune/.venv`를 따로 쓴다 (`.gitignore`의 `.venv/`로 제외됨).

```bash
uv venv finetune/.venv --python 3.13
uv pip install --python finetune/.venv/Scripts/python.exe "laya[serve]" --torch-backend=cpu
```

- `laya-serve`, `laya-train`, `laya` 명령이 함께 설치된다. 학습(#22)에서는 CUDA용 PyTorch로 바꾼다.

## 로컬 체크포인트로 서빙하기

```powershell
$env:LAYA_HOST = '127.0.0.1'          # 기본값은 0.0.0.0(외부에 열림) → 반드시 로컬로
$env:LAYA_PORT = '8100'               # 기본값 8000은 게이트웨이와 겹침
$env:LAYA_DEVICE = 'cpu'
$env:LAYA_EXTRA_MODELS = '{"local-ml": "C:/dev/ai-gateway/finetune/models/laya-multilingual"}'
$env:LAYA_MODELS = 'local-ml'         # 시작할 때 이 모델만 미리 적재
$env:LAYA_DEFAULT_MODEL = 'local-ml'
$env:HF_HUB_OFFLINE = '1'             # Hugging Face에서 아무것도 받지 않게
finetune\.venv\Scripts\laya-serve.exe
```

- 요청의 `model`에 `local-ml`(등록한 이름)을 넣는다. 경로를 `model`에 직접 넣으면 문서대로 422 오류다(확인함).
- 체크포인트 폴더 구성(공식 `laya-multilingual` 기준): `config.json`, `rl_agent_config.json`, `model.safetensors`, `encoder/`, `tokenizer/`, `tokenizer.json`, `tokenizer_config.json`. 학습 결과물도 같은 구성으로 저장된다.

## 주의: 시작할 때 기본 모델을 내려받는다

`HF_HUB_OFFLINE`·`LAYA_MODELS` 없이 띄우면, 등록한 로컬 모델과 별개로 **기본 영어 체크포인트(`convaiinnovations/laya`)를 Hugging Face에서 받기 시작한다.** 필요 없는 다운로드라 중간에 멈췄다. 받다 만 파일이 Hugging Face 캐시(`~/.cache/huggingface/hub/models--convaiinnovations--laya`, 약 600MB)에 남아 있다.

## 응답 형식: Ollaya(TypeSafe 형식)와의 차이

| 항목 | Ollaya `/v1/systemone` | laya-serve `/v1/systemone` |
|---|---|---|
| 기본 필드 (`type`, `choice`, `probabilities`, `noul`, `score`, `legend`, `usage`) | 있음 | 있음 (같음) |
| `confidence` (choice·score) | TypeSafe 공식 (K·p_max − 1)/(K − 1) | **1 − 정규화 엔트로피** (Laya 방식). TypeSafe 방식은 `x_jev_confidence`로 따로 줌 |
| noul의 `confidence` | 없음 | 있음 (max(p, 1 − p)) |
| 추가 필드 | — | `answer_confidence`, `action.act_probability`, `x_jev_confidence` |
| 응답의 `model` | 답한 모델의 정식 이름 | `laya-rl-agent` (요청한 이름이 아님) |

→ 게이트웨이와 측정 스크립트는 **`confidence` 필드가 아니라 `probabilities`·`noul`로 직접 판단한다.** 그러면 두 서버 모두 같은 코드로 처리된다. 로그에는 응답의 `model`이 아니라 요청한 모델 이름을 남긴다.

## 측정 (참고)

- CPU에서 질문 3개(choice·noul·score) 요청 1건: 약 0.18~0.21초 (모델은 시작할 때 미리 적재)
- 기본(파인튜닝 전) 체크포인트라 판단 결과 자체는 #5와 같이 약하다. 이번 확인의 목적은 서빙 경로다.
