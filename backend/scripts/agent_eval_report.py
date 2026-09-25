"""Run the fixed Agent evaluation cases and save machine-readable evidence."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    output = Path(sys.argv[1])
    cases = json.loads(Path("evals/cases.json").read_text(encoding="utf-8"))
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_agent_eval.py"], capture_output=True, text=True, check=False)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "case_ids": [case["id"] for case in cases],
        "categories": [case["category"] for case in cases],
        "exit_code": result.returncode,
        "summary": [line for line in result.stdout.splitlines() if " passed" in line or " failed" in line],
        "status": "PASS" if result.returncode == 0 else "FAIL",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if result.returncode:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
