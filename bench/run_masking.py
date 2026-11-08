"""가명화 성능 (#58): 개인정보 종류별 재현율·정밀도. 최종 가명화 부품(사전 + 이름 인식 모델 + 회사별 식별번호 + 정규식, D23).

정답이 확실한 데이터 두 가지
- 생성 데이터 (#21, `finetune/data/train-2026-11-07/items.csv`): 템플릿의 자리표시({이름}·{전화번호} …)와 채운 원문을 맞대면
  어떤 값이 어디 들어갔는지 정확히 안다. 2,531문장 중 개인정보가 있는 446문장
- 테스트셋 190행: 사람이 붙인 `pii_types` 라벨. 값은 모르므로 종류별 토큰·흔적으로 본다 (#46 run_masked.residual)

조건
- before: 시연 사전만, 이름 인식 없음 (D23 이전)
- default: 시연 사전 + 이름 인식 (지금 기본)
- all_names: 데이터의 이름을 전부 사전에 등록 + 이름 인식 (회사가 고객명을 다 등록한 경우)

값마다 원문 글자를 가린 문장과 맞대어(토큰을 값의 길이만큼 펼침, masker._kept_chars) "다 가림 / 일부만 / 놓침"으로 나눈다.
정밀도: 만든 토큰 중 정답 값과 겹치지 않는 것(개인정보가 아닌 것을 가림). 사내 고유명사·회사별 식별번호 토큰은 따로 센다.
생성 데이터의 값은 형식이 고르다(가상 값 생성기). 실제 사용자가 쓰는 표기 변형은 #44 보안 검토 시험이 맡는다.

실행: uv run python bench/run_masking.py --out bench/results/masking-2026-11-08
"""
import argparse
import csv
import importlib.util
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_masked as rm  # noqa: E402
from run_judges import NAMES  # noqa: E402

from gateway.config import NerConfig, load_settings  # noqa: E402
from gateway.masking.dictionary import Entry, parse  # noqa: E402
from gateway.masking.masker import TOKEN_SPLIT, RuleMasker, _kept_chars  # noqa: E402
from gateway.masking.ner import load as load_ner  # noqa: E402
from gateway.masking.patterns import normalize_text  # noqa: E402

ITEMS = ROOT / "finetune" / "data" / "train-2026-11-07" / "items.csv"
CONDITIONS = ("before", "default", "all_names")
KINDS = ("이름", "전화번호", "이메일", "주민번호", "계좌번호", "카드번호", "주소")
TYPE_TO_KIND = {"name": "이름", **rm.TYPE_KIND}                      # 테스트셋 라벨 → 종류
NAME_TOKENS = {"고객", "직원", "이름"}                                # 이름을 가리는 토큰 종류 (사전·이름 인식)
SIDE_KINDS = {"사내"}                                                 # 개인정보는 아니지만 회사가 가리라고 등록한 것


def _generator():
    spec = importlib.util.spec_from_file_location("generate", ROOT / "finetune" / "generate.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    return gen


def truth_values(template: str, original: str, placeholder: re.Pattern) -> list[tuple[str, str]] | None:
    """템플릿과 채운 원문을 맞대어 (종류, 값). 같은 자리표시는 같은 값이다. 맞지 않으면 None."""
    pattern, names, pos = "", {}, 0
    for m in placeholder.finditer(template):
        pattern += re.escape(template[pos:m.start()])
        key = m.group(0)
        if key in names:
            pattern += f"(?P={names[key]})"
        else:
            names[key] = f"g{len(names)}"
            pattern += f"(?P<{names[key]}>.+?)"
        pos = m.end()
    pattern += re.escape(template[pos:])
    mt = re.fullmatch(pattern, original, re.S)
    if mt is None:
        return None
    return [(placeholder.fullmatch(k).group(1), mt.group(g)) for k, g in names.items()]


def status(text: str, kept: list[bool], value: str) -> str:
    """값이 나오는 자리마다 글자·숫자가 몇 개 남았나. 가장 나쁜 자리로 정한다: masked < partial < missed."""
    worst = "masked"
    for m in re.finditer(re.escape(value), text):
        chars = [i for i in range(m.start(), m.end()) if text[i].isalnum()]
        left = sum(kept[i] for i in chars)
        s = "missed" if left == len(chars) else "partial" if left else "masked"
        worst = max(worst, s, key=("masked", "partial", "missed").index)
    return worst


def masker_for(condition: str, names: list[str], settings, ner) -> RuleMasker:
    cfg = settings.config.masking
    entries = parse((ROOT / cfg.example_dictionary_path).read_text(encoding="utf-8-sig"))
    if condition == "all_names":
        have = {e.value for e in entries}
        entries += [Entry("고객", n) for n in names if n not in have]
    custom = [(p.kind, re.compile(p.pattern)) for p in cfg.custom_patterns]
    return RuleMasker(entries, "custom", custom, None if condition == "before" else ner)


def token_kind(token: str) -> str:
    return rm.TOKEN_KIND.fullmatch(token).group(1)


def run_generated(settings, ner, out: Path) -> None:
    gen = _generator()
    with ITEMS.open(encoding="utf-8-sig", newline="") as f:
        items = [i for i in csv.DictReader(f) if not i["drop"]]
    truths = {}
    unaligned = 0
    for n, it in enumerate(items):
        tv = truth_values(it["template"], it["original"], gen.PLACEHOLDER)
        if tv is None:
            unaligned += 1
            continue
        truths[n] = tv
    names = sorted({v for tv in truths.values() for k, v in tv if k == "이름"})
    records = []
    for cond in CONDITIONS:
        masker = masker_for(cond, names, settings, ner)
        for n, tv in truths.items():
            text = normalize_text(items[n]["original"])
            m = masker.mask([{"role": "user", "content": text}])
            masked = m.messages[0]["content"]
            kept = _kept_chars(masked, m.mapping)
            aligned = len(kept) == len(text)
            pii = [(k, v) for k, v in tv if k in KINDS]
            for k, v in pii:
                records.append({"condition": cond, "row": n, "category": items[n]["category"], "type": "truth",
                                "kind": k, "value": v, "status": status(text, kept, v) if aligned else "unaligned",
                                "token_kind": "", "masked": masked})
            for token, value in m.mapping.items():
                hit = any(value in v or v in value for _, v in pii)
                tk = token_kind(token)
                group = "side" if tk in SIDE_KINDS or tk in {p.kind for p in settings.config.masking.custom_patterns} \
                    else "correct" if hit else "wrong"
                records.append({"condition": cond, "row": n, "category": items[n]["category"], "type": "token",
                                "kind": tk, "value": value, "status": group, "token_kind": tk, "masked": masked})
        print(f"생성 데이터 {cond} 끝 ({len(truths)}문장, 맞대기 실패 {unaligned})")
    rm._write(out / "generated.csv", records)


def run_testset(settings, ner, out: Path) -> None:
    records = []
    for cond in CONDITIONS:
        masker = masker_for(cond, list(NAMES), settings, ner)
        for row in rm.load_rows():
            m = masker.mask([{"role": "user", "content": row["text"]}])
            masked = m.messages[0]["content"]
            left, other = rm.residual(row, masked, m.mapping)
            labeled = [t for t in row["pii_types"].split("|") if t]
            for t in labeled:
                records.append({"condition": cond, "id": row["id"], "type": "truth", "kind": TYPE_TO_KIND[t],
                                "status": "missed" if t in left else "other_kind" if t in other else "masked",
                                "value": "", "masked": masked})
            kinds = {TYPE_TO_KIND[t] for t in labeled}
            for token, value in m.mapping.items():
                tk = token_kind(token)
                as_kind = "이름" if tk in NAME_TOKENS else tk
                group = "side" if tk in SIDE_KINDS or tk in {p.kind for p in settings.config.masking.custom_patterns} \
                    else "correct" if as_kind in kinds or (as_kind == "이름" and value in NAMES) else "wrong"
                records.append({"condition": cond, "id": row["id"], "type": "token", "kind": tk, "status": group,
                                "value": value, "masked": masked})
        print(f"테스트셋 {cond} 끝")
    rm._write(out / "testset.csv", records)


# ---------- 집계 ----------
def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def recall_table(rows: list[dict], title: str) -> list[str]:
    truth = [r for r in rows if r["type"] == "truth"]
    by = defaultdict(Counter)
    for r in truth:
        by[(r["condition"], r["kind"])][r["status"]] += 1
    lines = [f"### {title}: 재현율 (값이 다 가려진 비율)", "",
             "| 종류 | " + " | ".join(CONDITIONS) + " |", "|---|" + "---|" * len(CONDITIONS)]
    for kind in (*KINDS, "전체"):
        cells = []
        for cond in CONDITIONS:
            c = sum((by[(cond, k)] for k in KINDS), Counter()) if kind == "전체" else by[(cond, kind)]
            total = sum(c.values())
            if not total:
                cells.append("—")
                continue
            extra = ", ".join(f"{name} {c[key]}" for key, name in (("partial", "일부만"), ("missed", "놓침"),
                                                                   ("other_kind", "다른 종류로"), ("unaligned", "맞대기 실패"))
                              if c[key])
            cells.append(rm.rate(c["masked"] + c["other_kind"], total) + (f" ({extra})" if extra else ""))
        lines.append(f"| {kind} | " + " | ".join(cells) + " |")
    return lines + [""]


def precision_table(rows: list[dict], title: str) -> list[str]:
    tokens = [r for r in rows if r["type"] == "token"]
    lines = [f"### {title}: 정밀도 (가린 값 중 개인정보였던 비율)", "",
             "| 조건 | 정밀도 | 개인정보가 아닌 값을 가림 | 사내·회사 식별번호 토큰(따로 셈) |", "|---|---|---|---|"]
    for cond in CONDITIONS:
        ts = [r for r in tokens if r["condition"] == cond]
        good = sum(r["status"] == "correct" for r in ts)
        wrong = [r for r in ts if r["status"] == "wrong"]
        side = sum(r["status"] == "side" for r in ts)
        top = ", ".join(f"{k}({n})" for k, n in Counter(f"{r['kind']}:{r['value']}" for r in wrong).most_common(6))
        lines.append(f"| {cond} | {rm.rate(good, good + len(wrong))} | {len(wrong)}개{': ' + top if top else ''} | {side} |")
    return lines + [""]


def missed_list(rows: list[dict], cond: str, key: str, limit: int = 15) -> list[str]:
    bad = [r for r in rows if r["type"] == "truth" and r["condition"] == cond and r["status"] in ("missed", "partial")]
    out = [f"### 놓치거나 일부만 가린 값 ({cond}, {len(bad)}개)", ""]
    out += [f"- {r[key]} {r['kind']} {r['status']}: `{r['value'] or '-'}` — {r['masked']}" for r in bad[:limit]] or ["- 없음"]
    if len(bad) > limit:
        out.append(f"- … 외 {len(bad) - limit}개 (CSV)")
    return out + [""]


def summary(out: Path) -> None:
    gen_rows, test_rows = _read(out / "generated.csv"), _read(out / "testset.csv")
    lines = ["# 가명화 성능 (#58) — 개인정보 종류별 재현율·정밀도", "",
             "조건: `before` 시연 사전만·이름 인식 없음(D23 이전) / `default` 시연 사전 + 이름 인식(지금 기본) / "
             "`all_names` 이름 전부 사전 등록 + 이름 인식. 회사별 식별번호는 쇼핑몰 프리셋.", "",
             "## 생성 데이터 (#21, 값과 위치를 코드가 안다)", "",
             *recall_table(gen_rows, "생성 데이터"), *precision_table(gen_rows, "생성 데이터"),
             *missed_list(gen_rows, "default", "row"),
             "## 테스트셋 (사람이 쓴 문장, 라벨 기준)", "",
             *recall_table(test_rows, "테스트셋"), *precision_table(test_rows, "테스트셋"),
             *missed_list(test_rows, "default", "id")]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.summary_only:
        settings = load_settings()
        ner = load_ner(NerConfig())
        run_generated(settings, ner, args.out)
        run_testset(settings, ner, args.out)
    summary(args.out)


if __name__ == "__main__":
    main()
