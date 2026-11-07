"""이름 인식 모델(#42, #53). 조각 합치기는 모델 없이, 실제 모델 시험은 모델 파일이 있을 때만 (저장소에 없다)."""
from pathlib import Path

import pytest

from gateway.masking.ner import MAX_TOKENS, merge_bio

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "koelectra-ko-pii-ner"
needs_model = pytest.mark.skipif(not (MODEL_DIR / "onnx" / "model.onnx").exists(),
                                 reason="이름 인식 모델 파일이 없다 (models/는 저장소에 넣지 않는다)")


def test_merge_bio_joins_pieces_and_splits_labels():
    text = "양미래 010-1234-5678"
    offsets = [(0, 1), (1, 3), (4, 7), (7, 18)]
    tagged = [("B-NAME", 0.9), ("I-NAME", 0.7), ("B-PHONE", 0.8), ("I-PHONE", 0.8)]
    spans = merge_bio(text, offsets, tagged)
    assert [(s.label, s.text, s.score) for s in spans] == [("NAME", "양미래", 0.8), ("PHONE", "010-1234-5678", 0.8)]


def test_merge_bio_i_without_b_starts_new_span_and_o_closes():
    text = "가 나 다"
    offsets = [(0, 1), (2, 3), (4, 5)]
    spans = merge_bio(text, offsets, [("I-NAME", 0.6), ("O", 0.9), ("I-NAME", 0.5)])
    assert [s.text for s in spans] == ["가", "다"]


@pytest.fixture(scope="module")
def model():
    from gateway.masking.ner import NerModel
    return NerModel(MODEL_DIR, "onnx/model.onnx")


@needs_model
def test_finds_unregistered_name_but_not_person_words(model):
    names = [s.text for s in model.find("민원인 이름 양미래를 한자로 바꿔서 증명서에 적어 줘", {"NAME"})]
    assert names == ["양미래"]                                       # "민원인"·"이름"은 이름이 아니다
    assert model.find("고객님께 안내 문자 보내줘", {"NAME"}) == []


@needs_model
def test_long_text_is_split_into_overlapping_windows(model):
    """512토큰을 넘는 문장도 끝까지 본다 (겹치는 구간으로 나눔)."""
    text = "주문 내역을 정리해 주세요. " * 120 + "마지막으로 남궁민 고객에게 안내해 주세요."
    assert len(model.tokenizer.encode(text, add_special_tokens=False).ids) > MAX_TOKENS
    spans = model.find(text, {"NAME"})
    assert [s.text for s in spans] == ["남궁민"] and text[spans[0].start:spans[0].end] == "남궁민"
