"""이름 인식 모델 (#42, #53): 한국어 문장에서 개인정보의 **위치**를 찾는다. 바꾸기·복원은 가명화 부품(코드)이 한다.

- 모델: `atonlee/koelectra-ko-pii-ner` (가중치 Apache-2.0, KoELECTRA-small 1,410만 매개변수, ONNX)
  - 커밋 1e75c01e707232401883cf364151bbe2e560c708로 고정해 받았다. 해시는 models/koelectra-ko-pii-ner/SHA256SUMS
  - 학습 데이터: BCCard openpii(금융·행정 문장), townboy/korean-pii-dataset (둘 다 CC BY 4.0), 작성자 예문
  - 라벨 29종(BIO): NAME, ADDRESS, PHONE, RRN, … 이 프로젝트는 우선 NAME만 쓴다
- 모델 파일은 저장소에 넣지 않는다 (models/는 .gitignore). 설치할 때 받는다
- 판단 모델(gliclass)과 다르다: 문장 전체에 예/아니오가 아니라 **낱말 조각마다** 라벨을 붙인다.
  그래서 "고객"·"민원인" 같은 낱말과 사람 이름을 가를 수 있는지 측정한다 (#53, 판단 모델은 실패)
"""
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MAX_TOKENS = 510          # 512에서 [CLS]·[SEP]를 뺀 값
STRIDE = 128              # 긴 문장을 나눌 때 겹치는 토큰 수 (겹친 곳은 가운데에 가까운 구간의 판단을 쓴다)


@dataclass(frozen=True)
class Span:
    label: str            # NAME, ADDRESS, …
    start: int            # 원문 글자 위치
    end: int
    text: str
    score: float          # 조각들의 라벨 확률 평균


class NerModel:
    def __init__(self, model_dir: Path, onnx_file: str = "onnx/model_int8.onnx"):
        model_dir = Path(model_dir)
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.cls = self.tokenizer.token_to_id("[CLS]")
        self.sep = self.tokenizer.token_to_id("[SEP]")
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        self.labels = {int(k): v for k, v in config["id2label"].items()}
        self.session = ort.InferenceSession(str(model_dir / onnx_file), providers=["CPUExecutionProvider"])

    def _tag(self, ids: list[int]) -> list[tuple[int, float]]:
        """토큰마다 (라벨 번호, 확률). 긴 문장은 겹치는 구간으로 나눠 보고, 구간 가운데에 가까운 판단을 쓴다."""
        best: list[tuple[int, float, int]] = [(0, 0.0, -1)] * len(ids)    # (라벨, 확률, 가운데와의 거리 점수)
        start = 0
        while True:
            window = ids[start:start + MAX_TOKENS]
            input_ids = np.array([[self.cls, *window, self.sep]], dtype=np.int64)
            logits = self.session.run(["logits"], {"input_ids": input_ids,
                                                   "attention_mask": np.ones_like(input_ids)})[0][0][1:-1]
            e = np.exp(logits - logits.max(axis=-1, keepdims=True))
            probs = e / e.sum(axis=-1, keepdims=True)
            for i, p in enumerate(probs):
                centrality = min(i, len(window) - 1 - i)
                if centrality > best[start + i][2]:
                    k = int(p.argmax())
                    best[start + i] = (k, float(p[k]), centrality)
            if start + MAX_TOKENS >= len(ids):
                return [(k, p) for k, p, _ in best]
            start += MAX_TOKENS - STRIDE

    def find(self, text: str, labels: set[str] | None = None) -> list[Span]:
        """개인정보 위치."""
        enc = self.tokenizer.encode(text, add_special_tokens=False)
        if not enc.ids:
            return []
        tagged = [(self.labels[k], p) for k, p in self._tag(enc.ids)]
        return [s for s in merge_bio(text, enc.offsets, tagged) if not labels or s.label in labels]


def merge_bio(text: str, offsets: list[tuple[int, int]], tagged: list[tuple[str, float]]) -> list[Span]:
    """토큰마다 붙은 BIO 라벨을 조각으로 합친다. 이어지는 B-·I-는 하나로, I-가 B- 없이 나오면 새로 시작한다."""
    spans, cur = [], None          # cur = [라벨, 시작 토큰, 끝 토큰, 확률들]
    for t, (tag, p) in enumerate(tagged):
        kind = tag[2:] if tag != "O" else None
        if cur and kind == cur[0] and tag.startswith("I-"):
            cur[2], cur[3] = t, cur[3] + [p]
            continue
        if cur:
            spans.append(cur)
        cur = [kind, t, t, [p]] if kind else None
    if cur:
        spans.append(cur)
    out = []
    for kind, a, b, ps in spans:
        s, e = offsets[a][0], offsets[b][1]
        out.append(Span(kind, s, e, text[s:e], round(sum(ps) / len(ps), 4)))
    return out
