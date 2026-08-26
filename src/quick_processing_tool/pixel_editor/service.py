from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from ..naming import write_unique_bytes
from .models import PixelExportResult


class PixelExportError(RuntimeError):
    pass


def save_png(canvas, folder: str | Path, source_path: str | Path | None = None) -> PixelExportResult:
    directory = Path(folder)
    directory.mkdir(parents=True, exist_ok=True)
    stem = Path(source_path).stem + "_pixel" if source_path else "pixel_art"
    buf = BytesIO()
    canvas.image.save(buf, format="PNG")
    payload = buf.getvalue()
    try:
        output = write_unique_bytes(directory, stem, ".png", payload)
    except Exception as exc:
        raise PixelExportError(str(exc)) from exc
    if not output.exists() or output.stat().st_size <= 0:
        raise PixelExportError("保存されたPNGを確認できません")
    try:
        with Image.open(output) as checked:
            checked.verify()
        with Image.open(output) as reopened:
            if reopened.format != "PNG" or reopened.size != (canvas.width, canvas.height):
                raise PixelExportError("PNGのサイズを確認できません")
            if reopened.mode not in {"RGBA", "LA"} or "A" not in reopened.getbands():
                raise PixelExportError("PNGの透過情報を確認できません")
    except PixelExportError:
        raise
    except Exception as exc:
        raise PixelExportError(f"PNGの確認に失敗しました: {exc}") from exc
    return PixelExportResult(output, canvas.width, canvas.height, len(payload), True)
