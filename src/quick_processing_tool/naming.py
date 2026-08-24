from __future__ import annotations

from pathlib import Path


EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


def unique_named_path(folder: Path, stem: str, extension: str) -> Path:
    """Return a collision-safe path for a pre-sanitized stem."""
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{stem}{extension}"
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = folder / f"{stem}_{index}{extension}"
        if not candidate.exists():
            return candidate
        index += 1


def write_unique_bytes(folder: Path, stem: str, extension: str, data: bytes) -> Path:
    """Write bytes with exclusive creation, retrying suffixes without overwrite."""
    folder.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = "" if index == 1 else f"_{index}"
        candidate = folder / f"{stem}{suffix}{extension}"
        created = False
        try:
            with candidate.open("xb") as output:
                created = True
                output.write(data)
            return candidate
        except FileExistsError:
            index += 1
        except OSError:
            if created:
                candidate.unlink(missing_ok=True)
            raise


def unique_output_path(folder: Path, source: Path, output_format: str) -> Path:
    """Return a non-existing output path without ever overwriting the input."""
    extension = EXTENSIONS[output_format]
    candidate = unique_named_path(folder, source.stem, extension)
    try:
        same_as_source = candidate.resolve() == source.resolve()
    except OSError:
        same_as_source = candidate.absolute() == source.absolute()
    if not same_as_source:
        return candidate
    return unique_named_path(folder, f"{source.stem}_2", extension)
