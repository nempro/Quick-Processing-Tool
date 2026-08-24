from __future__ import annotations

from pathlib import Path


EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


def unique_output_path(folder: Path, source: Path, output_format: str) -> Path:
    """Return a non-existing output path without ever overwriting the input."""
    folder.mkdir(parents=True, exist_ok=True)
    extension = EXTENSIONS[output_format]
    candidate = folder / f"{source.stem}{extension}"
    try:
        same_as_source = candidate.resolve() == source.resolve()
    except OSError:
        same_as_source = candidate.absolute() == source.absolute()
    if not candidate.exists() and not same_as_source:
        return candidate

    index = 2
    while True:
        candidate = folder / f"{source.stem}_{index}{extension}"
        if not candidate.exists():
            return candidate
        index += 1
