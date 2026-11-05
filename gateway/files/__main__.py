"""명령줄 파일 검사: uv run python -m gateway.files <파일> [--masked-out 가명화.txt]

게이트웨이 서버 없이 같은 설정(config.yaml, 업종 프리셋, 사용자 사전)으로 검사한다. 결과에 값은 나오지 않는다.
"""
import argparse
import sys
from pathlib import Path

from gateway.config import load_settings
from gateway.files.extract import FileError
from gateway.files.scan import scan, summary
from gateway.masking.masker import RuleMasker


def main() -> int:
    ap = argparse.ArgumentParser(description="파일 속 개인정보 검사")
    ap.add_argument("file", type=Path)
    ap.add_argument("--masked-out", type=Path, help="가명화된 텍스트를 저장할 파일")
    args = ap.parse_args()
    masker = RuleMasker.from_config(load_settings().config.masking)
    try:
        report = scan(args.file.name, args.file.read_bytes(), masker)
    except FileError as e:
        print(f"검사할 수 없습니다: {e}")
        return 2
    print(summary(report))
    for f in report.findings:
        print(f"  {f.where}: " + ", ".join(f"{k} {n}" for k, n in f.kinds.items()))
    if args.masked_out:
        args.masked_out.write_text(report.masked_text, encoding="utf-8")
        print(f"가명화된 텍스트: {args.masked_out}")
    return 1 if report.has_pii else 0


if __name__ == "__main__":
    sys.exit(main())
