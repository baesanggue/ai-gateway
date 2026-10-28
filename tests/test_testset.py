"""테스트셋(data/testset.csv) 구조와 라벨 일관성 검사.

라벨을 고칠 때 업종별 구성이나 라벨 규칙이 깨지지 않았는지 확인한다.
id 앞글자로 업종을 구분한다: T 쇼핑몰·사내 업무, M 의료, F 금융, G 공공민원.
"""
import csv
from pathlib import Path

TESTSET = Path(__file__).resolve().parent.parent / "data" / "testset.csv"
COLUMNS = ["id", "text", "expected_route", "has_pii", "pii_types",
           "needs_raw_value", "is_confidential", "is_risky"]
ROUTES = {"template", "cheap", "premium", "block"}
PII_TYPES = {"name", "phone", "email", "rrn", "card", "account", "address"}

# 업종별 기대 구성: (행 수, 원본 필요, 기밀·위험·무관)
DOMAINS = {"T": (100, 9, 20), "M": (30, 3, 6), "F": (30, 3, 6), "G": (30, 3, 6)}


def load_rows():
    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        return list(reader)


def test_ids_and_texts_are_unique():
    rows = load_rows()
    assert len({r["id"] for r in rows}) == len(rows)
    assert len({r["text"] for r in rows}) == len(rows)


def test_values_are_in_allowed_sets():
    for r in load_rows():
        assert r["id"][0] in DOMAINS, r["id"]
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


def test_domain_composition():
    """업종마다 같은 범주 비율을 유지한다 (CLAUDE.md 5장 비율, 업종 추가분은 30행 기준)."""
    rows = load_rows()
    for prefix, (n, n_raw, n_conf_risky_irrelevant) in DOMAINS.items():
        rs = [r for r in rows if r["id"].startswith(prefix)]
        assert len(rs) == n, prefix
        assert sum(r["needs_raw_value"] == "1" for r in rs) == n_raw, prefix
        conf_risky_irrelevant = sum(
            r["is_confidential"] == "1" or r["is_risky"] == "1"
            or (r["expected_route"] == "block" and r["needs_raw_value"] == "0")
            for r in rs
        )
        assert conf_risky_irrelevant == n_conf_risky_irrelevant, prefix
        assert any(r["expected_route"] == "template" for r in rs), prefix
