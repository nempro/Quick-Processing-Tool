from __future__ import annotations

from io import BytesIO

from PIL import Image

from ..errors import TargetSizeUnreachable


def _prepare(image: Image.Image, output_format: str, background: tuple[int, int, int]) -> Image.Image:
    if output_format != "JPEG":
        return image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, background)
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        return flattened
    return image.convert("RGB")


def encode_once(
    image: Image.Image,
    output_format: str,
    quality: int,
    background: tuple[int, int, int],
    metadata: dict[str, object] | None = None,
) -> bytes:
    prepared = _prepare(image, output_format, background)
    stream = BytesIO()
    kwargs: dict[str, object] = dict(metadata or {})
    if output_format == "JPEG":
        kwargs.update(quality=quality, optimize=True, progressive=True, subsampling=0)
    elif output_format == "WEBP":
        kwargs.update(quality=quality, method=6)
    elif output_format == "PNG":
        kwargs.update(optimize=True, compress_level=9)
    prepared.save(stream, format=output_format, **kwargs)
    return stream.getvalue()


def encode_best_quality(
    image: Image.Image,
    output_format: str,
    target_bytes: int | None,
    max_quality: int,
    background: tuple[int, int, int],
    metadata: dict[str, object] | None = None,
) -> tuple[bytes, int | None]:
    max_quality = min(100, max(1, max_quality))
    if target_bytes is None or output_format == "PNG":
        data = encode_once(image, output_format, max_quality, background, metadata)
        if target_bytes is not None and len(data) > target_bytes:
            raise TargetSizeUnreachable(
                "PNGのままでは指定容量以下にできません。JPEGまたはWebPを選択してください。"
            )
        return data, None if output_format == "PNG" else max_quality

    best: tuple[bytes, int] | None = None
    low, high = 1, max_quality
    while low <= high:
        quality = (low + high) // 2
        data = encode_once(image, output_format, quality, background, metadata)
        if len(data) <= target_bytes:
            best = data, quality
            low = quality + 1
        else:
            high = quality - 1
    if best is None:
        raise TargetSizeUnreachable("最低品質でも指定容量以下にできません。解像度を小さくしてください。")
    return best
