"""Refresh project metrics for README/CODE-MATRIX.

The script only prints measurements; it never rewrites docs automatically.
Usage:
    python scripts/refresh_metrics.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python_files() -> list[Path]:
    return sorted((ROOT / "pyharness").rglob("*.py"))


def _line_count(paths: list[Path]) -> int:
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in paths)


def _test_definition_count() -> int:
    pattern = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)
    count = 0
    for path in (ROOT / "tests").rglob("*.py"):
        count += len(pattern.findall(path.read_text(encoding="utf-8")))
    return count


def _collected_tests() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    if proc.returncode != 0:
        raise SystemExit(proc.stdout + proc.stderr)
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    if not match:
        raise SystemExit("Could not parse pytest collection output")
    return int(match.group(1))


def metrics() -> dict[str, int]:
    files = _python_files()
    return {
        "python_files": len(files),
        "python_lines": _line_count(files),
        "test_definitions": _test_definition_count(),
        "collected_tests": _collected_tests(),
    }


def main() -> int:
    print(json.dumps(metrics(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
