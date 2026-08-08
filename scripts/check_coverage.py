import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_coverage.py COVERAGE_JSON MINIMUM_PERCENT")
    report_path = Path(sys.argv[1])
    minimum = float(sys.argv[2])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    covered = float(report["totals"]["percent_covered"])
    print(f"Exact Python coverage: {covered:.2f}% (required: {minimum:.2f}%)")
    if covered < minimum:
        print("Python coverage gate failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
