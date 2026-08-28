"""Collect redistribution license texts from the pinned build environment."""

from __future__ import annotations

import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path


def copy_metadata_file(package: str, suffix: str, destination: Path) -> None:
    dist = distribution(package)
    matches = [item for item in dist.files or () if str(item).replace("\\", "/").lower().endswith(suffix.lower())]
    if len(matches) != 1:
        raise RuntimeError(f"{package}: expected one {suffix}, found {len(matches)}")
    source = Path(dist.locate_file(matches[0]))
    shutil.copy2(source, destination)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: collect_licenses.py OUTPUT_DIRECTORY")
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=True)

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"CPython license not found: {python_license}")
    shutil.copy2(python_license, destination / "CPython-LICENSE.txt")
    copy_metadata_file("Pillow", "licenses/LICENSE", destination / "Pillow-LICENSE.txt")
    copy_metadata_file("pyinstaller", "COPYING.txt", destination / "PyInstaller-COPYING.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
