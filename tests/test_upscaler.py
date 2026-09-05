from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

import quick_processing_tool.upscaler.service as upscale_service_module

from quick_processing_tool.upscale_ui import UpscalePage
from quick_processing_tool.upscaler.backend import BackendAvailability, UpscaleBackend
from quick_processing_tool.upscaler.errors import InputDecodeError, UpscaleCancelledError, UpscaleProcessingError
from quick_processing_tool.upscaler.models import UpscaleMode, UpscaleOptions, UpscaleOutputFormat, UpscaleResult
from quick_processing_tool.upscaler.real_esrgan import RealESRGANNCNNBackend, default_runtime_dir
from quick_processing_tool.upscaler.service import UpscaleService


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class MockBackend(UpscaleBackend):
    name = "Mock GPU"

    def __init__(self, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail
        self.calls: list[tuple[UpscaleMode, int]] = []

    def check_availability(self) -> BackendAvailability:
        return BackendAvailability(
            self.available,
            "available" if self.available else "backend_not_found",
            "高画質化エンジンを利用できます" if self.available else "高画質化エンジンが準備されていません",
        )

    def upscale(self, input_path, output_path, *, mode, scale, progress, cancel_event) -> None:
        self.calls.append((mode, scale))
        if cancel_event.is_set():
            raise UpscaleCancelledError()
        if self.fail:
            raise UpscaleProcessingError("mock failure")
        progress(40)
        with Image.open(input_path) as source:
            output = source.resize((source.width * scale, source.height * scale), Image.Resampling.NEAREST)
            output.save(output_path, format="PNG")
        progress(100)


def make_image(path: Path, mode: str = "RGBA") -> None:
    color = (20, 80, 160, 90) if mode == "RGBA" else (20, 80, 160)
    Image.new(mode, (13, 9), color).save(path)


def test_default_runtime_dir_prefers_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "設定 Runtime"
    monkeypatch.setenv("QUICK_PROCESSING_TOOL_UPSCALER_DIR", str(configured))
    assert default_runtime_dir() == configured


def test_frozen_runtime_dir_prefers_portable_then_user_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "配布 folder" / "Quick Processing Tool.exe"
    portable = executable.parent / "runtime" / "upscaler" / "realesrgan-ncnn-vulkan"
    user_data = tmp_path / "App Data"
    monkeypatch.delenv("QUICK_PROCESSING_TOOL_UPSCALER_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(user_data))
    monkeypatch.setattr("quick_processing_tool.upscaler.real_esrgan.sys.frozen", True, raising=False)
    monkeypatch.setattr("quick_processing_tool.upscaler.real_esrgan.sys.executable", str(executable))

    expected_user = user_data / "QuickProcessingTool" / "runtime" / "upscaler" / "realesrgan-ncnn-vulkan"
    assert default_runtime_dir() == expected_user

    portable.mkdir(parents=True)
    (portable / "realesrgan-ncnn-vulkan.exe").write_bytes(b"runtime")
    assert default_runtime_dir() == portable


def test_real_backend_reports_missing_executable_and_models(tmp_path: Path) -> None:
    backend = RealESRGANNCNNBackend(tmp_path)
    unavailable = backend.check_availability()
    assert not unavailable.available
    assert unavailable.code == "backend_not_found"

    backend.executable.write_bytes(b"not an executable")
    models = tmp_path / "models"
    models.mkdir()
    missing = backend.check_availability()
    assert not missing.available
    assert missing.code == "model_not_found"
    assert "realesrgan-x4plus" in missing.detail

    for model in backend.MODEL_BY_MODE.values():
        for suffix in (".param", ".bin"):
            (models / f"{model}{suffix}").write_bytes(b"model")
    invalid = backend.check_availability()
    assert not invalid.available
    assert invalid.code == "invalid_executable"


@pytest.mark.parametrize(
    ("scale", "mode"),
    ((2, UpscaleMode.ILLUSTRATION), (4, UpscaleMode.PHOTO)),
)
def test_service_writes_exact_verified_dimensions_and_mode(
    tmp_path: Path, scale: int, mode: UpscaleMode
) -> None:
    source = tmp_path / "source.png"
    make_image(source)
    backend = MockBackend()
    progress: list[int | None] = []
    result = UpscaleService(backend).run(
        source,
        tmp_path / "out",
        UpscaleOptions(scale=scale, mode=mode),
        progress.append,
        Event(),
    )
    assert backend.calls == [(mode, scale)]
    assert result.output_path.is_file()
    assert result.output_path.stat().st_size > 0
    assert result.output_path.name == f"source_{scale}x.png"
    with Image.open(result.output_path) as output:
        output.load()
        assert output.size == (13 * scale, 9 * scale)
    assert progress[-1] == 100


def test_service_preserves_alpha_and_uses_duplicate_safe_name(tmp_path: Path) -> None:
    source = tmp_path / "alpha.png"
    make_image(source)
    service = UpscaleService(MockBackend())
    options = UpscaleOptions(scale=2, output_format=UpscaleOutputFormat.PNG)
    first = service.run(source, tmp_path, options, lambda _value: None, Event())
    second = service.run(source, tmp_path, options, lambda _value: None, Event())
    assert first.output_path.name == "alpha_2x.png"
    assert second.output_path.name == "alpha_2x_2.png"
    with Image.open(second.output_path) as output:
        assert output.mode == "RGBA"
        assert output.getchannel("A").getextrema() == (90, 90)


def test_service_does_not_leave_output_on_cancel_or_failure(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    make_image(source, "RGB")
    cancelled = Event()
    cancelled.set()
    with pytest.raises(UpscaleCancelledError):
        UpscaleService(MockBackend()).run(
            source, tmp_path / "cancelled", UpscaleOptions(), lambda _value: None, cancelled
        )
    with pytest.raises(UpscaleProcessingError):
        UpscaleService(MockBackend(fail=True)).run(
            source, tmp_path / "failed", UpscaleOptions(), lambda _value: None, Event()
        )
    assert not (tmp_path / "cancelled").exists()
    assert not (tmp_path / "failed").exists()


def test_upscale_page_has_clear_defaults_and_single_image_flow(
    app: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "sample.webp"
    Image.new("RGB", (31, 17), (50, 80, 110)).save(source)
    page = UpscalePage(UpscaleService(MockBackend()))
    page.show()
    app.processEvents()
    assert page.drop_zone.title.text() == "画像をここにドロップ"
    assert page.drop_zone.choose.text() == "画像を選ぶ"
    assert page.scale_2.isChecked()
    assert page.scale_2.objectName() == "scaleOption"
    assert page.scale_4.objectName() == "scaleOption"
    assert ":checked" in page.styleSheet()
    assert page.illustration.isChecked()
    page.load_image(source)
    app.processEvents()
    assert "31 × 17" in page.original_info.text()
    assert "62 × 34" in page.output_info.text()
    assert page.start_button.isEnabled()
    assert page.start_button.text() == "2倍で高画質化を開始"
    page.result = UpscaleResult(source, 62, 34, source.stat().st_size, 1.0, UpscaleMode.ILLUSTRATION, 2, "Mock GPU")
    page.scale_4.setChecked(True)
    assert page.result is None
    assert "124 × 68" in page.output_info.text()
    page.photo.setChecked(True)
    assert "写真" in page.output_info.text()
    page.close()


def test_upscale_drop_zone_accepts_supported_image(
    app: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "drop.png"
    make_image(source, "RGB")
    page = UpscalePage(UpscaleService(MockBackend()))
    page.show()
    app.processEvents()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    enter = QDragEnterEvent(
        QPoint(10, 10), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(page.drop_zone, enter)
    assert enter.isAccepted()
    assert page.drop_zone.title.text() == "ここにドロップして画像を読み込み"
    drop = QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(page.drop_zone, drop)
    app.processEvents()
    assert page.source_path == source.resolve()
    assert page.drop_zone.title.text() == "画像をここにドロップ"
    page.close()

def test_indeterminate_progress_switches_to_numeric(
    app: QApplication,
) -> None:
    page = UpscalePage(UpscaleService(MockBackend()))
    page.progress.setRange(0, 0)
    assert page.progress.maximum() == 0
    page._on_progress(37)
    assert page.progress.maximum() == 100
    assert page.progress.value() == 37
    page.close()

def test_invalid_input_and_jpeg_webp_outputs(tmp_path: Path) -> None:
    invalid = tmp_path / "broken.png"
    invalid.write_bytes(b"not an image")
    service = UpscaleService(MockBackend())
    with pytest.raises(InputDecodeError):
        service.run(invalid, tmp_path / "bad", UpscaleOptions(), lambda _value: None, Event())

    source = tmp_path / "transparent.png"
    make_image(source)
    jpeg = service.run(
        source, tmp_path / "jpeg",
        UpscaleOptions(output_format=UpscaleOutputFormat.JPEG),
        lambda _value: None, Event(),
    )
    webp = service.run(
        source, tmp_path / "webp",
        UpscaleOptions(output_format=UpscaleOutputFormat.WEBP),
        lambda _value: None, Event(),
    )
    assert jpeg.output_path.suffix == ".jpg"
    assert webp.output_path.suffix == ".webp"
    with Image.open(jpeg.output_path) as image:
        assert image.mode == "RGB"
    with Image.open(webp.output_path) as image:
        assert "A" in image.getbands()


def test_unavailable_backend_disables_start(
    app: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "source.png"
    make_image(source, "RGB")
    page = UpscalePage(UpscaleService(MockBackend(available=False)))
    page.load_image(source)
    assert not page.start_button.isEnabled()
    assert "Runtimeが導入されていません" in page.engine_label.text()
    assert "再起動してください" in page.engine_label.text()
    page.close()

def test_upscale_page_auto_saves_verified_result(
    app: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "automatic.png"
    make_image(source, "RGB")
    destination = tmp_path / "saved"
    page = UpscalePage(UpscaleService(MockBackend()))
    page.show()
    app.processEvents()
    page.load_image(source)
    assert page.output_folder == source.parent
    page.output_folder = destination
    page._output_folder_explicit = True
    page.folder_label.set_path(destination)

    page.start()
    deadline = time.monotonic() + 5
    while not page.can_close() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    assert page.can_close()
    assert page.result is not None
    output = page.result.output_path
    assert output.parent == destination
    assert output.name == "automatic_2x.png"
    assert output.is_file()
    assert output.stat().st_size > 0
    with Image.open(output) as reopened:
        reopened.load()
        assert reopened.size == (26, 18)
    assert page._saved_output == output
    assert page.saved_box.isVisible()
    assert "✓ 2倍の画像を保存しました" in page.result_label.text()
    assert output.name in page.result_label.text()
    assert page.saved_path.toolTip() == str(destination)
    assert not hasattr(page, "save_button")
    page.close()


def test_cancel_after_final_write_removes_auto_saved_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "cancel-race.png"
    make_image(source, "RGB")
    destination = tmp_path / "cancelled-output"
    cancel_event = Event()
    original_write = upscale_service_module.write_unique_bytes

    def write_then_cancel(*args, **kwargs):
        output = original_write(*args, **kwargs)
        cancel_event.set()
        return output

    monkeypatch.setattr(
        upscale_service_module, "write_unique_bytes", write_then_cancel
    )
    with pytest.raises(UpscaleCancelledError):
        UpscaleService(MockBackend()).run(
            source, destination, UpscaleOptions(),
            lambda _value: None, cancel_event,
        )
    assert not list(destination.glob("*"))
