"""테스트셋(data/testset.csv) 구조와 라벨 일관성 검사.

라벨을 고칠 때 범주 개수나 라벨 규칙이 깨지지 않았는지 확인한다.
"""
import csv
from collections import Counter
from pathlib import Path

TESTSET = Path(__file__).resolve().parent.parent / "data" / "testset.csv"
COLUMNS = ["id", "text", "expected_route", "has_pii", "pii_types",
           "needs_raw_value", "is_confidential", "is_risky"]
ROUTES = {"template", "cheap", "premium", "block"}
PII_TYPES = {"name", "phone", "email", "rrn", "card", "account", "address"}


def load_rows():
    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        return list(reader)


def test_100_rows_with_unique_ids_and_texts():
    rows = load_rows()
    assert len(rows) == 100
    assert len({r["id"] for r in rows}) == 100
    assert len({r["text"] for r in rows}) == 100


def test_values_are_in_allowed_sets():
    for r in load_rows():
        assert r["expected_route"] in ROUTES, r["id"]
        for col in ("has_pii", "needs_raw_value", "is_confidential", "is_risky"):
            assert r[col] in ("0", "1"), (r["id"], col)
        if r["pii_types"]:
            assert set(r["pii_types"].split("|")) <= PII_TYPES, r["id"]


def test_label_rules():
    for r in load_rows():
        rid = r["id"]
        # 민감정보 여부와 유형 목록은 함께 움직인다
        assert (r["has_pii"] == "1") == bool(r["pii_types"]), rid
        # 원본 값이 필요하면 민감정보가 있어야 하고, 기본 수준에서 차단된다
        if r["needs_raw_value"] == "1":
            assert r["has_pii"] == "1", rid
            assert r["expected_route"] == "block", rid
        # 위험·인젝션은 차단된다
        if r["is_risky"] == "1":
            assert r["expected_route"] == "block", rid


def test_category_counts():
    """CLAUDE.md 5장: 단순 30 / 복잡 20 / 민감 20 / 원본 필요 10 / 기밀·위험·무관 20."""
    rows = load_rows()
    raw = sum(r["needs_raw_value"] == "1" for r in rows)
    conf_risky_irrelevant = sum(
        r["is_confidential"] == "1" or r["is_risky"] == "1"
        or (r["expected_route"] == "block" and r["needs_raw_value"] == "0")
        for r in rows
    )
    assert raw == 10
    assert conf_risky_irrelevant == 20
    assert Counter(r["expected_route"] for r in rows)["template"] > 0
