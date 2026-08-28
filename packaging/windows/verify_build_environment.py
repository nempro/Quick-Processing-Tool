"""Verify every exact release constraint before freezing the application."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_build_environment.py CONSTRAINTS_FILE")
    constraints = Path(sys.argv[1])
    mismatches: list[str] = []
    for raw_line in constraints.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            mismatches.append(f"not an exact constraint: {line}")
            continue
        package, expected = line.split("==", 1)
        try:
            actual = version(package)
        except PackageNotFoundError:
            mismatches.append(f"{package}: not installed (expected {expected})")
            continue
        if actual != expected:
            mismatches.append(f"{package}: {actual} (expected {expected})")
    if mismatches:
        raise RuntimeError("release dependency mismatch:\n" + "\n".join(mismatches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
