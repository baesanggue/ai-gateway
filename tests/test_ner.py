"""이름 인식 모델(#42, #53, D23). 조각 합치기·불러오기 검사는 모델 없이, 실제 모델 시험은 모델 파일이 있을 때만 (저장소에 없다)."""
import socket
from pathlib import Path

import pytest

from gateway.config import NerConfig
from gateway.masking.ner import BATCH, OVERLAP, PINNED, WINDOW, NerModel, NerModelError, load, merge_bio

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "koelectra-ko-pii-ner"
needs_model = pytest.mark.skipif(not all((MODEL_DIR / f).exists() for f in PINNED),
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


# ── 불러오기: 없거나 다르면 시작하지 않는다 (D23) ──────────────

def test_missing_model_refuses_to_start_with_download_guide(tmp_path):
    with pytest.raises(NerModelError) as e:
        load(NerConfig(model_dir=str(tmp_path / "없음")))
    assert "시작하지 않습니다" in str(e.value) and "docs/ner-model.md" in str(e.value)


def test_tampered_file_is_rejected_by_hash(tmp_path):
    """받은 파일이 바뀌었으면(다른 버전, 손상, 바꿔치기) 불러오지 않는다."""
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    with pytest.raises(NerModelError) as e:
        NerModel(tmp_path)
    assert "해시 불일치" in str(e.value)


@pytest.mark.parametrize("onnx_file", ["onnx/other.onnx", "config.json", "../model.onnx"])
def test_only_pinned_onnx_files_are_accepted(tmp_path, onnx_file):
    with pytest.raises(NerModelError) as e:
        NerModel(tmp_path, onnx_file)
    assert "고정한 모델 파일이 아닙니다" in str(e.value)


# ── 실제 모델 (파일이 있을 때만) ─────────────────────────────

@pytest.fixture(scope="module")
def model():
    return load(NerConfig())             # config.yaml과 같은 기본값: int8


@needs_model
def test_finds_unregistered_name_but_not_person_words(model):
    names = [s.text for s in model.find("민원인 이름 양미래를 한자로 바꿔서 증명서에 적어 줘", {"NAME"})]
    assert names == ["양미래"]                                       # "민원인"·"이름"은 이름이 아니다
    assert model.find("고객님께 안내 문자 보내줘", {"NAME"}) == []


@needs_model
def test_finds_address_the_regex_misses(model):
    """시·도 없이 쓴 주소는 정규식이 못 잡는다. 엄격 수준은 이 모델이 본 주소로 막는다 (D23)."""
    assert [s.text for s in model.find("배송지를 마포구 가상로 123으로 바꿔 주세요", {"ADDRESS"})] == ["마포구 가상로 123"]
    assert model.find("요구 사항으로 3개만 정리해 줘", {"ADDRESS"}) == []


@needs_model
@pytest.mark.parametrize("text, masked", [
    ("주소가 서울특별시 마포구 가상동 예시리더스 인지 확인해줘", "주소가 [주소1] 인지 확인해줘"),     # 11/10 사고 모양 (#87)
    ("서울특별시 마포구 가상동 예시아파트 101동 1001호로 보내줘", "[주소1]로 보내줘"),
    ("마포구 가상동 예시리더스로 배송해 주세요", "[주소1]로 배송해 주세요"),
    ("제주도 서귀포시 가상읍 예시리 123번지", "[주소1]"),
    ("서울특별시 구로구 가상로 12 예시리더스 101동으로 보내줘", "[주소1]으로 보내줘"),
    ("서울시 마포구 망원동 맛집 추천해줘", "서울시 마포구 망원동 맛집 추천해줘"),                  # 지역 이름은 그대로
    ("다음 주에 마포구청에서 설명회가 있다", "다음 주에 마포구청에서 설명회가 있다"),
], ids=["sido-dong-building", "apartment-unit", "no-sido", "lot-number", "road-plus-building", "region", "office"])
def test_real_model_addresses_are_masked_but_region_names_are_not(model, text, masked):
    """실제 모델로 (#87): 지번·건물 이름 주소는 [주소1]로, 동에서 끝나는 지역 이름과 기관 이름은 그대로."""
    from gateway.masking.masker import RuleMasker
    r = RuleMasker([], ner=model).mask([{"role": "user", "content": text}])
    assert r.messages[0]["content"] == masked and r.suspected == []


@needs_model
def test_long_text_is_read_to_the_end_in_overlapping_batched_windows(model):
    """구간 여러 묶음(BATCH개 넘게)을 넘는 글도 끝까지 본다. 마지막 묶음은 짧은 구간을 채워서 계산한다."""
    text = "주문 내역을 정리해 주세요. " * 400 + "마지막으로 남궁민 고객에게 안내해 주세요."
    n = len(model.tokenizer.encode(text, add_special_tokens=False).ids)
    assert n > WINDOW + (WINDOW - OVERLAP) * BATCH                 # 두 번째 묶음까지 간다
    spans = model.find(text, {"NAME"})
    assert [s.text for s in spans] == ["남궁민"] and text[spans[0].start:spans[0].end] == "남궁민"


@needs_model
def test_runs_without_network(monkeypatch):
    """불러오기부터 찾기까지 이 PC 안에서만 한다. 밖으로 연결하려 하면 실패하게 막아도 동작한다."""
    def no_network(*args, **kwargs):
        raise AssertionError("이름 인식 모델이 네트워크에 연결하려 했다")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    m = load(NerConfig())
    assert [s.text for s in m.find("양미래 고객에게 안내해 줘", {"NAME"})] == ["양미래"]
