from __future__ import annotations

import os
import random
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture(scope="session", autouse=True)
def isolated_application_data(tmp_path_factory: pytest.TempPathFactory):
    previous = os.environ.get("QUICK_PROCESSING_TOOL_DATA_DIR")
    os.environ["QUICK_PROCESSING_TOOL_DATA_DIR"] = str(
        tmp_path_factory.mktemp("quick-processing-tool-data")
    )
    yield
    if previous is None:
        os.environ.pop("QUICK_PROCESSING_TOOL_DATA_DIR", None)
    else:
        os.environ["QUICK_PROCESSING_TOOL_DATA_DIR"] = previous


@pytest.fixture
def rgb_image_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.jpg"
    rng = random.Random(42)
    data = bytes(rng.randrange(256) for _ in range(640 * 480 * 3))
    Image.frombytes("RGB", (640, 480), data).save(path, "JPEG", quality=96)
    return path


@pytest.fixture
def rgba_image_path(tmp_path: Path) -> Path:
    path = tmp_path / "transparent.png"
    image = Image.new("RGBA", (80, 60), (20, 80, 220, 0))
    for y in range(10, 50):
        for x in range(10, 70):
            image.putpixel((x, y), (220, 30, 50, 128))
    image.save(path, "PNG")
    return path
