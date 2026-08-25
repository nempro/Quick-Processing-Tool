from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from threading import Event

from PIL import Image, ImageOps, UnidentifiedImageError

from ..naming import EXTENSIONS, write_unique_bytes
from ..processors.encode import encode_once
from .backend import ProgressCallback, UpscaleBackend
from .errors import InputDecodeError, UpscaleCancelledError, UpscaleProcessingError, UpscaleSaveError
from .models import UpscaleOptions, UpscaleOutputFormat, UpscaleResult


LOGGER = logging.getLogger(__name__)
_SOURCE_FORMATS = {"PNG": "PNG", "JPEG": "JPEG", "JPG": "JPEG", "WEBP": "WEBP"}


class UpscaleService:
    def __init__(self, backend: UpscaleBackend) -> None:
        self.backend = backend

    def run(
        self,
        source_path: Path,
        output_folder: Path,
        options: UpscaleOptions,
        progress: ProgressCallback,
        cancel_event: Event,
    ) -> UpscaleResult:
        started = time.perf_counter()
        source_path = source_path.resolve()
        LOGGER.info(
            "Upscale start: input=%s mode=%s scale=%sx backend=%s",
            source_path, options.mode.value, options.scale, self.backend.name,
        )
        try:
            with Image.open(source_path) as opened:
                opened.load()
                source_format = _SOURCE_FORMATS.get((opened.format or "").upper(), "PNG")
                source = ImageOps.exif_transpose(opened).copy()
        except (OSError, UnidentifiedImageError) as exc:
            LOGGER.exception("Upscale input decode failed: %s", source_path)
            raise InputDecodeError("画像を読み込めませんでした。") from exc

        width, height = source.size
        if width < 1 or height < 1:
            raise InputDecodeError("画像サイズを確認できませんでした。")
        if cancel_event.is_set():
            raise UpscaleCancelledError("高画質化をキャンセルしました。")
        output_format = (
            source_format
            if options.output_format is UpscaleOutputFormat.SAME
            else options.output_format.value
        )
        extension = EXTENSIONS[output_format]

        with tempfile.TemporaryDirectory(prefix="quick-processing-upscale-") as temp_name:
            temp = Path(temp_name)
            normalized = temp / "input.png"
            engine_output = temp / "upscaled.png"
            normalized_source = source.convert("RGBA" if self._has_alpha(source) else "RGB")
            normalized_source.save(normalized, format="PNG")
            self.backend.upscale(
                normalized,
                engine_output,
                mode=options.mode,
                scale=options.scale,
                progress=progress,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                raise UpscaleCancelledError("高画質化をキャンセルしました。")
            try:
                with Image.open(engine_output) as generated:
                    generated.load()
                    image = generated.copy()
            except (OSError, UnidentifiedImageError) as exc:
                raise UpscaleProcessingError("高画質化後の画像を確認できませんでした。") from exc
            expected = (width * options.scale, height * options.scale)
            if image.size != expected:
                raise UpscaleProcessingError(
                    f"高画質化後のサイズが正しくありません（{image.width} × {image.height}）。"
                )
            if self._has_alpha(source) and output_format in {"PNG", "WEBP"} and not self._has_alpha(image):
                raise UpscaleProcessingError("透明部分を保持できませんでした。")
            if cancel_event.is_set():
                raise UpscaleCancelledError("高画質化をキャンセルしました。")
            try:
                data = encode_once(image, output_format, options.jpeg_quality, (255, 255, 255))
                if cancel_event.is_set():
                    raise UpscaleCancelledError("高画質化をキャンセルしました。")
                output = write_unique_bytes(
                    output_folder,
                    f"{source_path.stem}_{options.scale}x",
                    extension,
                    data,
                )
                if cancel_event.is_set():
                    output.unlink(missing_ok=True)
                    raise UpscaleCancelledError("高画質化をキャンセルしました。")
            except OSError as exc:
                LOGGER.exception("Upscale save failed: %s", output_folder)
                raise UpscaleSaveError("高画質化した画像を保存できませんでした。") from exc

        try:
            if output.stat().st_size <= 0:
                raise OSError("empty output")
            with Image.open(output) as verified:
                verified.load()
                if verified.size != (width * options.scale, height * options.scale):
                    raise OSError("invalid output dimensions")
            if cancel_event.is_set():
                output.unlink(missing_ok=True)
                raise UpscaleCancelledError("高画質化をキャンセルしました。")
        except OSError as exc:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                LOGGER.exception("Invalid upscale output could not be removed: %s", output)
            raise UpscaleSaveError("保存した画像を確認できませんでした。") from exc

        duration = time.perf_counter() - started
        LOGGER.info(
            "Upscale success: input_size=%sx%s output=%s duration=%.3fs",
            width, height, output, duration,
        )
        return UpscaleResult(
            output_path=output,
            width=width * options.scale,
            height=height * options.scale,
            size_bytes=output.stat().st_size,
            duration_seconds=duration,
            mode=options.mode,
            scale=options.scale,
            backend_name=self.backend.name,
        )

    @staticmethod
    def _has_alpha(image: Image.Image) -> bool:
        return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
