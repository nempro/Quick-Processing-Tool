"""Validate the deliberately small LGPL-compatible frozen Qt surface."""

from __future__ import annotations

import sys
from pathlib import Path

import pefile


ALLOWED_PLUGINS = {
    "plugins/imageformats/qico.dll",
    "plugins/imageformats/qjpeg.dll",
    "plugins/imageformats/qwebp.dll",
    "plugins/platforms/qwindows.dll",
    "plugins/styles/qmodernwindowsstyle.dll",
}
BANNED_NAME_PARTS = (
    "virtualkeyboard", "qt6qml", "qt6quick", "qt6pdf", "qt6svg",
    "qt6network", "qtnetwork.pyd", "qt6opengl", "opengl32sw",
)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_frozen_qt.py QT_ROOT")
    root = Path(sys.argv[1]).resolve()
    files = [path for path in root.rglob("*") if path.is_file()]
    relative = {path.relative_to(root).as_posix().lower() for path in files}
    plugins = {item for item in relative if item.startswith("plugins/")}
    if plugins != ALLOWED_PLUGINS:
        raise RuntimeError(f"unexpected Qt plugins: {sorted(plugins ^ ALLOWED_PLUGINS)}")
    banned = [item for item in relative if any(part in item for part in BANNED_NAME_PARTS)]
    if banned:
        raise RuntimeError(f"banned Qt binaries remain: {banned}")

    names = {path.name.lower() for path in files}
    missing: list[str] = []
    for path in files:
        if path.suffix.lower() not in {".dll", ".pyd"}:
            continue
        try:
            image = pefile.PE(str(path), fast_load=True)
            image.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
            )
        except pefile.PEFormatError:
            continue
        for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", ()):
            dependency = entry.dll.decode(errors="replace")
            if dependency.lower().startswith("qt6") and dependency.lower() not in names:
                missing.append(f"{path.relative_to(root)} -> {dependency}")
    if missing:
        raise RuntimeError("missing Qt dependencies:\n" + "\n".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
