from __future__ import annotations

from pathlib import Path


EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_COMPONENT_LENGTH = 200


def _sanitize_component(value: str) -> str:
    cleaned = []
    for char in value:
        if ord(char) < 32 or char in '<>:"/\\|?*':
            cleaned.append("_")
        else:
            cleaned.append(char)
    return "".join(cleaned).strip().rstrip(" .")


def _protect_reserved_windows_name(value: str) -> str:
    if not value:
        return value
    base, dot, remainder = value.partition(".")
    reserved_base = base.rstrip(" .")
    if reserved_base.casefold() not in WINDOWS_RESERVED_NAMES:
        return value
    safe_base = f"{reserved_base}_"
    if dot:
        return f"{safe_base}.{remainder}"
    return safe_base



def normalize_filename_stem(raw: str, *, default: str | None = None, max_length: int = MAX_COMPONENT_LENGTH) -> str:
    """Normalize a user-provided filename stem for Windows-safe output."""
    value = (raw or "").strip()
    while value.lower().endswith(".png"):
        value = value[:-4].rstrip()
    value = _sanitize_component(value)
    if value in {".", ".."}:
        value = ""

    if not value and default is not None:
        value = _sanitize_component(default)

    if not value:
        return ""

    value = _protect_reserved_windows_name(value)
    if len(value) > max_length:
        value = value[:max_length].rstrip(" .")
        value = _protect_reserved_windows_name(value)
        if len(value) > max_length:
            value = value[:max_length].rstrip(" .")
    return value or ""


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
