from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..errors import ProcessingError
from ..image_workspace import MISSING_SOURCE_MESSAGE, MissingSourceError, require_source_file
from ..naming import EXTENSIONS, KNOWN_IMAGE_EXTENSIONS, normalize_filename_stem, write_unique_bytes
from ..processors.encode import encode_once
from .models import EditOutputFormat, EditResult, EditSettings
from .renderer import render_path


LOGGER = logging.getLogger(__name__)
SOURCE_FORMATS = {"PNG": "PNG", "JPEG": "JPEG", "WEBP": "WEBP"}


class EditProcessingError(ProcessingError):
    """The quick edit pipeline or its strict save validation failed."""

class _OutputVerificationError(OSError):
    def __init__(self, message: str, user_message: str) -> None:
        super().__init__(message)
        self.user_message = user_message


def _requires_alpha(image: Image.Image) -> bool:
    """Return whether the rendered pixels actually need alpha preservation."""
    if "A" not in image.getbands():
        return False
    minimum, _maximum = image.getchannel("A").getextrema()
    return minimum < 255


class EditService:
    def export(
        self,
        source_path: Path,
        output_folder: Path,
        settings: EditSettings,
        output_format: EditOutputFormat,
        jpeg_background: tuple[int, int, int] = (255, 255, 255),
        quality: int = 95,
        custom_stem: str | None = None,
    ) -> EditResult:
        source_path = source_path.resolve()
        require_source_file(source_path)
        try:
            with Image.open(source_path) as opened:
                opened.load()
                source_format = SOURCE_FORMATS.get((opened.format or "").upper())
                if source_format is None:
                    raise EditProcessingError("PNG / JPEG / WebP のみ開けます。")
            rendered = render_path(source_path, settings)
            selected_format = source_format if output_format is EditOutputFormat.SAME else output_format.value
            default_stem = f"{source_path.stem}_edited"
            output_stem = normalize_filename_stem(
                custom_stem if custom_stem is not None else default_stem,
                default=default_stem,
                strip_extensions=KNOWN_IMAGE_EXTENSIONS,
            )
            if not output_stem:
                raise EditProcessingError("保存するファイル名を入力してください。")
            data = encode_once(rendered, selected_format, quality, jpeg_background)
            output = write_unique_bytes(
                output_folder,
                output_stem,
                EXTENSIONS[selected_format],
                data,
            )
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            LOGGER.exception("Quick edit export failed: %s", source_path)
            if not source_path.is_file():
                raise MissingSourceError(MISSING_SOURCE_MESSAGE) from exc
            raise EditProcessingError("加工した画像を保存できませんでした。") from exc

        rendered_requires_alpha = _requires_alpha(rendered)
        try:
            if output.stat().st_size <= 0:
                raise _OutputVerificationError(
                    "empty output",
                    "保存後の確認に失敗しました（ファイルが空です）。",
                )
            with Image.open(output) as checked:
                checked.verify()
            with Image.open(output) as verified:
                verified.load()
                actual_format = (verified.format or "").upper()
                if actual_format != selected_format:
                    raise _OutputVerificationError(
                        "unexpected output format",
                        "保存後の確認に失敗しました（形式が一致しません）。",
                    )
                if verified.size != rendered.size:
                    raise _OutputVerificationError(
                        "unexpected output dimensions",
                        "保存後の確認に失敗しました（画像サイズが一致しません）。",
                    )
                has_alpha = "A" in verified.getbands() or (
                    verified.mode == "P" and "transparency" in verified.info
                )
                if selected_format in {"PNG", "WEBP"} and rendered_requires_alpha and not has_alpha:
                    raise _OutputVerificationError(
                        "alpha channel was lost",
                        "保存後の確認に失敗しました（透明情報を保持できませんでした）。",
                    )
                if selected_format == "JPEG" and has_alpha:
                    raise _OutputVerificationError(
                        "JPEG output unexpectedly has alpha",
                        "保存後の確認に失敗しました（JPEGの透明情報が不正です）。",
                    )
        except (OSError, UnidentifiedImageError) as exc:
            LOGGER.exception("Quick edit output verification failed: %s", output)
            try:
                output.unlink(missing_ok=True)
            except OSError:
                LOGGER.exception("Invalid quick edit output could not be removed: %s", output)
            if isinstance(exc, UnidentifiedImageError):
                message = "保存後の確認に失敗しました（ファイルを開けませんでした）。"
            elif isinstance(exc, _OutputVerificationError):
                message = exc.user_message
            else:
                message = "保存後の確認に失敗しました。"
            raise EditProcessingError(message) from exc

        LOGGER.info(
            "Quick edit saved: input=%s output=%s size=%sx%s alpha=%s",
            source_path,
            output,
            rendered.width,
            rendered.height,
            has_alpha,
        )
        return EditResult(
            output,
            rendered.width,
            rendered.height,
            output.stat().st_size,
            selected_format,
            has_alpha,
        )
