"""판단 모델 비교표 만들기 (#18).

spike_laya.py --model ... --out-dir <폴더> 로 모델마다 만든 결과(CSV + JSON)를 읽어
한 장의 비교표(<폴더>/README.md)로 정리한다.

실행: uv run python bench/compare_spike.py bench/results/model-compare-2026-10-26
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_laya import BINARY, auc  # noqa: E402

LABELS = {"has_pii": "민감정보", "needs_raw_value": "원본 필요", "is_confidential": "기밀", "is_risky": "위험"}


def load(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def binary_auc(rows: list[dict], key: str, variant: str) -> float | None:
    rs = [r for r in rows if r["question"] == key and r["variant"] == variant]
    return auc([float(r["raw"]) for r in rs if r["label"] == "True"],
               [float(r["raw"]) for r in rs if r["label"] == "False"])


def accuracy(rows: list[dict], key: str, variant: str) -> float:
    rs = [r for r in rows if r["question"] == key and r["variant"] == variant and r["correct"] != ""]
    return sum(r["correct"] == "True" for r in rs) / len(rs)


def fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}"


def main():
    folder = Path(sys.argv[1])
    runs = []
    for meta_path in sorted(folder.glob("*-spike-*-ko.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        runs.append((meta, load(meta_path.with_suffix(".csv"))))

    lines = [
        f"# 판단 모델 비교 — 한국어 20문장 ({folder.name})", "",
        f"- 실행 PC: {runs[0][0]['pc']}",
        f"- 질문 {runs[0][0]['questions']}개를 요청 1건에 묶어 문장 20개에 보냄. 질문 문장은 모두 같음 (`bench/spike_laya.py`)",
        "- **20문장 간이 비교다.** 정답 '예'가 기밀 1개, 위험 2개, 원본 필요 4개, 민감정보 8개뿐이라 "
        "기밀·위험의 수치는 참고만 한다.", "",
        "## 예/아니오 판단의 신호 (AUC: 0.5 = 구분 못 함, 1.0 = 완벽)", "",
        "기준값과 무관하게 정답 '예' 문장에 더 높은 확률을 주는지 본다. 높으면 기준값 보정으로 쓸 수 있다.", "",
        "| 모델 | " + " | ".join(f"{LABELS[k]} noul / choice" for k in BINARY) + " |",
        "|---|" + "---|" * len(BINARY),
    ]
    for meta, rows in runs:
        cells = [f"{fmt(binary_auc(rows, k, 'noul'))} / {fmt(binary_auc(rows, k, 'choice'))}" for k in BINARY]
        lines.append(f"| `{meta['model']}` | " + " | ".join(cells) + " |")

    lines += [
        "", "## 라우팅 판단 (기본 기준값 정확도)", "",
        "| 모델 | 난이도 score | 난이도 choice | 의도 choice | 요청 1건 지연 (평균 / p95) | 질문당 |",
        "|---|---|---|---|---|---|",
    ]
    for meta, rows in runs:
        per_q = meta["eval_ms_mean"] / meta["questions"]
        lines.append(
            f"| `{meta['model']}` | {accuracy(rows, 'difficulty', 'score'):.2f} | "
            f"{accuracy(rows, 'difficulty', 'choice'):.2f} | {accuracy(rows, 'intent', 'choice'):.2f} | "
            f"{meta['eval_ms_mean']} / {meta['eval_ms_p95']} ms | {per_q:.0f} ms |")
    lines += ["", "- 다수결 기준선: 난이도 0.54, 의도 0.83 (표본의 정답이 '업무'에 몰려 있음)",
              "- 적재 상태: " + " · ".join(f"`{m['model']}` {m['device']}" for m, _ in runs)]

    (folder / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
