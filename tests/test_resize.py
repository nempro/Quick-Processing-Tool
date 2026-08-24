from PIL import Image

from quick_processing_tool.models import ProcessingOptions, ResizeMode
from quick_processing_tool.processors.resize import output_dimensions, resize_image


def test_dimensions_keep_aspect_ratio() -> None:
    options = ProcessingOptions(resize_mode=ResizeMode.DIMENSIONS, width=500, height=500, keep_aspect=True)
    assert output_dimensions(1000, 500, options) == (500, 250)


def test_long_edge_for_landscape_and_portrait() -> None:
    options = ProcessingOptions(resize_mode=ResizeMode.LONG_EDGE, long_edge=1600)
    assert output_dimensions(3000, 2000, options) == (1600, 1067)
    assert output_dimensions(1200, 2400, options) == (800, 1600)


def test_percentage_uses_lanczos_and_expected_size() -> None:
    image = Image.new("RGB", (400, 200), "red")
    options = ProcessingOptions(resize_mode=ResizeMode.PERCENTAGE, percentage=25)
    assert resize_image(image, options).size == (100, 50)
