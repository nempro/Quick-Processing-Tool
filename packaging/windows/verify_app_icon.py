from __future__ import annotations

import sys
from pathlib import Path

from quick_processing_tool.app_icon import validate_icon


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: verify_app_icon.py <path-to-ico>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        sizes = validate_icon(path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    labels = ", ".join(f"{width}x{height}" for width, height in sorted(sizes))
    print(f"Formal app icon verified: {labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
