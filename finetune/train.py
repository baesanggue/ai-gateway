"""파인튜닝 (#22, D15): laya-multilingual을 #21 학습 데이터로 이 PC의 GPU(RTX 4080 SUPER, CUDA)에서 학습한다.

실행 (finetune/.venv: PyTorch·laya 전용. 게이트웨이 의존성과 섞지 않는다):
  finetune/.venv/Scripts/python.exe -I finetune/train.py \\
      --data finetune/data/train-2026-11-07/train.jsonl --eval finetune/data/train-2026-11-07/eval-pilot.jsonl \\
      --out finetune/models/laya-ko-2026-11-07 --record bench/results/finetune-2026-11-07/train.json

- 학습 함수는 laya 0.4.17의 `laya.train.finetune` (설치된 패키지 코드에서 확인). 학습·온도 보정·저장을 한 번에 한다
- 설정은 공개 노트북과 같은 기본값(TrainConfig)을 쓴다. 한 장치에서 실효 배치 64 = 8 × 8
- 테스트셋(data/testset.csv)은 학습·학습 중 평가 어디에도 넣지 않는다. 마지막 비교(bench)에만 쓴다
- 학습 중 평가(--eval)는 10/31 시범 데이터에서 학습 데이터와 겹치는 문장을 뺀 것 (generate.py --make-eval)
- 결과물(체크포인트)은 finetune/models/ 아래라 저장소에 넣지 않는다. 기록(--record)만 넣는다
"""
import argparse
import json
import platform
import time
from pathlib import Path

import laya
import torch
from laya.train import TrainConfig, finetune

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--eval", type=Path, required=True)
    ap.add_argument("--base", type=Path, default=ROOT / "finetune" / "models" / "laya-multilingual")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--record", type=Path, required=True, help="학습 기록 JSON (저장소에 넣는다)")
    ap.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if "testset" in str(args.data) or "testset" in str(args.eval):
        raise SystemExit("테스트셋은 학습에 쓰지 않는다 (D15)")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA GPU가 보이지 않는다 (finetune/.venv에 CUDA용 PyTorch가 필요하다)")

    config = TrainConfig(epochs=args.epochs, seed=args.seed, eval_data=str(args.eval))
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    summary = finetune(str(args.data), str(args.base), str(args.out), config=config, device="cuda")
    seconds = time.perf_counter() - t0

    record = {
        "data": str(args.data.relative_to(ROOT)) if args.data.is_absolute() else str(args.data),
        "eval": str(args.eval.relative_to(ROOT)) if args.eval.is_absolute() else str(args.eval),
        "base": args.base.name, "out": args.out.name,
        "config": {k: v for k, v in vars(config).items() if not k.startswith("_")},
        "seconds": round(seconds, 1),
        "gpu": torch.cuda.get_device_name(0),
        "gpu_peak_memory_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "torch": torch.__version__, "laya": getattr(laya, "__version__", "?"), "python": platform.python_version(),
        "summary": summary,
    }
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"학습 끝: {seconds / 60:.1f}분, GPU 최대 {record['gpu_peak_memory_gb']}GB → {args.out}")


if __name__ == "__main__":
    main()
