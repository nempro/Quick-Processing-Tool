from PIL import Image
from PySide6.QtWidgets import QApplication
import pytest

from quick_processing_tool.editing.filters import apply_color_adjustments
from quick_processing_tool.editing.models import ColorAdjustmentSettings, EditSettings
from quick_processing_tool.editing.renderer import render_edit

@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])

def test_color_adjustments_preserve_alpha_and_dimensions():
    source = Image.new("RGBA", (8, 6), (40, 80, 120, 90))
    adjusted = apply_color_adjustments(source, ColorAdjustmentSettings(brightness=40, contrast=25, saturation=35, temperature=-30, tint=20, hue=90, fade=10))
    assert adjusted.size == source.size
    assert adjusted.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    assert adjusted.getpixel((0, 0))[:3] != source.getpixel((0, 0))[:3]

def test_horizontal_flip_round_trip_for_rgba_image():
    source = Image.new("RGBA", (3, 1))
    source.putdata([(10, 20, 30, 255), (80, 120, 160, 64), (200, 100, 20, 255)])
    flipped = render_edit(source, EditSettings(flip_horizontal=True))
    assert flipped.getpixel((0, 0))[:3] == (200, 100, 20)
    assert flipped.getpixel((2, 0))[:3] == (10, 20, 30)
    assert render_edit(flipped, EditSettings(flip_horizontal=True)).getpixel((1, 0)) == source.getpixel((1, 0))

def test_color_controls_round_trip_and_reset(qt_app):
    from quick_processing_tool.edit_ui import QuickEditPage
    page = QuickEditPage()
    page.brightness_slider.setValue(55)
    page.hue_spin.setValue(-90)
    assert page.settings().color_adjustments.brightness == 55
    assert page.settings().color_adjustments.hue == -90
    page.reset_color_adjustments()
    assert page.settings().color_adjustments == ColorAdjustmentSettings()
    page.close()

def test_color_section_is_nested_in_filter_accordion(qt_app):
    from quick_processing_tool.edit_ui import QuickEditPage
    page = QuickEditPage()
    assert page.color_section.parent() is page.filter_section.content
    assert page.flip_horizontal_check.accessibleName() == "左右反転"
    page.close()
