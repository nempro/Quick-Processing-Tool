from __future__ import annotations

from PIL import Image


ORIENTATION_TAG = 274


def safe_metadata(image: Image.Image, remove: bool) -> dict[str, object]:
    if remove:
        return {}
    metadata: dict[str, object] = {}
    exif = image.getexif()
    if exif:
        if ORIENTATION_TAG in exif:
            del exif[ORIENTATION_TAG]
        encoded = exif.tobytes()
        if encoded:
            metadata["exif"] = encoded
    icc = image.info.get("icc_profile")
    if icc:
        metadata["icc_profile"] = icc
    return metadata
