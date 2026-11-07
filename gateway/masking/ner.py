"""이름 인식 모델 (#42, #53): 한국어 문장에서 개인정보의 **위치**를 찾는다. 바꾸기·복원은 가명화 부품(코드)이 한다.

- 모델: `atonlee/koelectra-ko-pii-ner` (가중치 Apache-2.0, KoELECTRA-small 1,410만 매개변수, ONNX)
  - 커밋 1e75c01e707232401883cf364151bbe2e560c708로 고정해 받았다. 파일 해시는 아래 PINNED (docs/ner-model.md)
  - 학습 데이터: BCCard openpii(금융·행정 문장), townboy/korean-pii-dataset (둘 다 CC BY 4.0), 작성자 예문
  - 라벨 29종(BIO): NAME, ADDRESS, PHONE, RRN, … 게이트웨이는 NAME(가린다)과 ADDRESS(남았는지 본다)만 쓴다 (D23)
- 이 PC 안에서만 돈다: 파일을 읽어 onnxruntime(CPU)으로 계산한다. 네트워크를 쓰지 않는다
- 모델 파일은 저장소에 넣지 않는다 (models/는 .gitignore). 없거나 해시가 다르면 게이트웨이를 시작하지 않는다 (D23)
- 판단 모델(gliclass)과 다르다: 문장 전체에 예/아니오가 아니라 **낱말 조각마다** 라벨을 붙인다.
  그래서 "고객"·"민원인" 같은 낱말과 사람 이름을 가른다 (#53 측정, 판단 모델은 실패)
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from gateway.config import ROOT, NerConfig

# 긴 글은 겹치는 구간으로 나눠 본다. 512토큰 한 구간보다 256토큰 구간을 묶어 보내는 쪽이 빠르다
# (7.6만 자: 512토큰 4.1초 → 256토큰 8개씩 2.5초, 11/07). 겹친 곳은 구간 가운데에 가까운 판단을 쓴다
WINDOW = 254              # 256에서 [CLS]·[SEP]를 뺀 값
OVERLAP = 64              # 구간끼리 겹치는 토큰 수. 어느 토큰이든 양쪽에 32토큰 이상 문맥이 있는 구간의 판단을 쓴다
BATCH = 8                 # 한 번에 계산하는 구간 수

# 고정한 커밋의 파일 해시 (SHA-256). 읽은 바이트를 확인한 뒤 그 바이트로 불러온다 (확인과 불러오기 사이에 바뀔 틈이 없다)
PINNED = {
    "onnx/model_int8.onnx": "53cd2da73867d0a66ea2bec70b582d164115803788438bd63fd8a9ed365868e7",
    "onnx/model.onnx": "7794bdaa2daaa524d1e7f5e4f80e6b62c6fe3bf36d7a9175f4cd4c011837ddc3",
    "config.json": "65b1c2e17c4eb6d4f5be29e4374784a03c7b886229af948757e55f3b79444a23",
    "tokenizer.json": "9bc85a2412ae3694bffac69cb152a9aafd59fd0357e540a343fa9daa62099c38",
}
GUIDE = "받는 방법은 docs/ner-model.md에 있습니다"


class NerModelError(RuntimeError):
    """이름 인식 모델 파일이 없거나 고정한 파일과 다르다. 게이트웨이를 시작하지 않는다 (D23)."""


@dataclass(frozen=True)
class Span:
    label: str            # NAME, ADDRESS, …
    start: int            # 원문 글자 위치
    end: int
    text: str
    score: float          # 조각들의 라벨 확률 평균


class NerModel:
    def __init__(self, model_dir: Path, onnx_file: str = "onnx/model_int8.onnx"):
        if onnx_file not in PINNED or not onnx_file.endswith(".onnx"):
            raise NerModelError(f"{onnx_file}은(는) 고정한 모델 파일이 아닙니다 "
                                f"(onnx/model_int8.onnx 또는 onnx/model.onnx). {GUIDE}")
        model_dir = Path(model_dir)
        self.onnx_file = onnx_file
        self.tokenizer = Tokenizer.from_str(_read(model_dir, "tokenizer.json").decode("utf-8"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.cls = self.tokenizer.token_to_id("[CLS]")
        self.sep = self.tokenizer.token_to_id("[SEP]")
        config = json.loads(_read(model_dir, "config.json"))
        self.labels = {int(k): v for k, v in config["id2label"].items()}
        self.session = ort.InferenceSession(_read(model_dir, onnx_file), providers=["CPUExecutionProvider"])

    def _tag(self, ids: list[int]) -> list[tuple[int, float]]:
        """토큰마다 (라벨 번호, 확률). 긴 글은 겹치는 구간으로 나눠 BATCH개씩 계산하고, 구간 가운데에 가까운 판단을 쓴다."""
        best: list[tuple[int, float, int]] = [(0, 0.0, -1)] * len(ids)    # (라벨, 확률, 가운데와의 거리 점수)
        starts = list(range(0, max(len(ids) - OVERLAP, 1), WINDOW - OVERLAP))
        for b in range(0, len(starts), BATCH):
            group = starts[b:b + BATCH]
            windows = [ids[s:s + WINDOW] for s in group]
            width = max(map(len, windows)) + 2
            input_ids = np.zeros((len(windows), width), dtype=np.int64)       # 짧은 구간은 뒤를 채우고 attention_mask로 가린다
            mask = np.zeros_like(input_ids)
            for r, w in enumerate(windows):
                input_ids[r, :len(w) + 2] = [self.cls, *w, self.sep]
                mask[r, :len(w) + 2] = 1
            logits = self.session.run(["logits"], {"input_ids": input_ids, "attention_mask": mask})[0]
            for r, (s, w) in enumerate(zip(group, windows)):
                rows = logits[r, 1:len(w) + 1]
                e = np.exp(rows - rows.max(axis=-1, keepdims=True))
                probs = e / e.sum(axis=-1, keepdims=True)
                for i, p in enumerate(probs):
                    centrality = min(i, len(w) - 1 - i)
                    if centrality > best[s + i][2]:
                        k = int(p.argmax())
                        best[s + i] = (k, float(p[k]), centrality)
        return [(k, p) for k, p, _ in best]

    def find(self, text: str, labels: set[str] | None = None) -> list[Span]:
        """개인정보 위치."""
        enc = self.tokenizer.encode(text, add_special_tokens=False)
        if not enc.ids:
            return []
        tagged = [(self.labels[k], p) for k, p in self._tag(enc.ids)]
        return [s for s in merge_bio(text, enc.offsets, tagged) if not labels or s.label in labels]


def load(cfg: NerConfig) -> NerModel:
    """설정대로 모델을 불러온다. 파일이 없거나 해시가 다르면 NerModelError (게이트웨이를 시작하지 않는다)."""
    model_dir = Path(cfg.model_dir)
    return NerModel(model_dir if model_dir.is_absolute() else ROOT / model_dir, cfg.onnx_file)


def _read(model_dir: Path, rel: str) -> bytes:
    path = model_dir / rel
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise NerModelError(f"이름 인식 모델 파일 {path}이(가) 없어 게이트웨이를 시작하지 않습니다. {GUIDE}") from None
    if hashlib.sha256(data).hexdigest() != PINNED[rel]:
        raise NerModelError(f"이름 인식 모델 파일 {path}이(가) 고정한 파일과 다릅니다(해시 불일치). "
                            f"게이트웨이를 시작하지 않습니다. 지우고 다시 받으세요. {GUIDE}")
    return data


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
