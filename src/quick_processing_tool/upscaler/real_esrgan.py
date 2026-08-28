from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from threading import Event

from PIL import Image

from .backend import BackendAvailability, ProgressCallback, UpscaleBackend
from .errors import (
    BackendNotFoundError,
    GPUUnavailableError,
    ModelNotFoundError,
    UpscaleCancelledError,
    UpscaleProcessingError,
)
from .models import UpscaleMode


LOGGER = logging.getLogger(__name__)
_PERCENT = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)%")
_GPU_MARKERS = (
    "invalid gpu",
    "no vulkan",
    "vulkan device",
    "vkcreateinstance failed",
    "find_memory_index failed",
    "failed to create",
)


def default_runtime_dir() -> Path:
    configured = os.environ.get("QUICK_PROCESSING_TOOL_UPSCALER_DIR")
    if configured:
        return Path(configured).expanduser()
    application_root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[3]
    )
    portable = application_root / "runtime" / "upscaler" / "realesrgan-ncnn-vulkan"
    if (portable / "realesrgan-ncnn-vulkan.exe").is_file():
        return portable
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return local_app_data / "QuickProcessingTool" / "runtime" / "upscaler" / "realesrgan-ncnn-vulkan"


class RealESRGANNCNNBackend(UpscaleBackend):
    name = "Real-ESRGAN NCNN Vulkan"
    MODEL_BY_MODE = {
        UpscaleMode.ILLUSTRATION: "realesrgan-x4plus-anime",
        UpscaleMode.PHOTO: "realesrgan-x4plus",
    }

    def __init__(self, runtime_dir: Path | None = None) -> None:
        self.runtime_dir = (runtime_dir or default_runtime_dir()).resolve()
        self.executable = self.runtime_dir / "realesrgan-ncnn-vulkan.exe"
        self.models_dir = self.runtime_dir / "models"
        self._process: subprocess.Popen[str] | None = None

    def check_availability(self) -> BackendAvailability:
        if not self.executable.is_file():
            return BackendAvailability(
                False,
                "backend_not_found",
                "高画質化エンジンが準備されていません",
                f"Executable not found: {self.executable}",
            )
        missing: list[str] = []
        for model in self.MODEL_BY_MODE.values():
            for suffix in (".param", ".bin"):
                path = self.models_dir / f"{model}{suffix}"
                if not path.is_file():
                    missing.append(path.name)
        if missing:
            return BackendAvailability(
                False,
                "model_not_found",
                "高画質化モデルが見つかりません",
                ", ".join(missing),
            )
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            probe = subprocess.run(
                [str(self.executable), "-h"],
                cwd=self.runtime_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                startupinfo=startupinfo,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return BackendAvailability(
                False, "invalid_executable", "高画質化エンジンを起動できません", str(exc)
            )
        if "usage:" not in (probe.stdout + probe.stderr).lower():
            return BackendAvailability(
                False, "invalid_executable", "高画質化エンジンを起動できません",
                (probe.stdout + probe.stderr)[-1000:],
            )
        if os.name == "nt" and not (
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "vulkan-1.dll"
        ).is_file():
            return BackendAvailability(
                False, "gpu_unavailable", "Vulkan対応GPUを確認できません",
                "vulkan-1.dll not found",
            )
        return BackendAvailability(True)

    def _raise_unavailable(self, availability: BackendAvailability) -> None:
        if availability.code == "model_not_found":
            raise ModelNotFoundError(availability.user_message)
        if availability.code == "gpu_unavailable":
            raise GPUUnavailableError(availability.user_message)
        raise BackendNotFoundError(availability.user_message)

    def upscale(
        self,
        input_path: Path,
        output_path: Path,
        *,
        mode: UpscaleMode,
        scale: int,
        progress: ProgressCallback,
        cancel_event: Event,
    ) -> None:
        availability = self.check_availability()
        if not availability.available:
            self._raise_unavailable(availability)
        if scale not in (2, 4):
            raise UpscaleProcessingError("対応倍率は2倍または4倍です。")

        native_output = output_path if scale == 4 else output_path.with_name("native_4x.png")
        model = self.MODEL_BY_MODE[mode]
        command = [
            str(self.executable),
            "-i", str(input_path),
            "-o", str(native_output),
            "-s", "4",
            "-t", "0",
            "-m", str(self.models_dir),
            "-n", model,
            "-g", "auto",
            "-j", "1:2:2",
            "-f", "png",
        ]
        LOGGER.info("Upscale backend command: model=%s scale=4 tile=auto", model)
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            self._process = subprocess.Popen(
                command,
                cwd=self.runtime_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
            )
        except OSError as exc:
            raise BackendNotFoundError("高画質化エンジンを起動できませんでした。") from exc

        chunks: queue.Queue[str] = queue.Queue()

        def read_stream(stream) -> None:
            while True:
                part = stream.read(1)
                if not part:
                    break
                chunks.put(part)

        readers = [
            threading.Thread(target=read_stream, args=(self._process.stdout,), daemon=True),
            threading.Thread(target=read_stream, args=(self._process.stderr,), daemon=True),
        ]
        for reader in readers:
            reader.start()

        transcript = ""
        last_percent = -1
        try:
            while self._process.poll() is None:
                if cancel_event.is_set():
                    self._terminate_process()
                    raise UpscaleCancelledError("高画質化をキャンセルしました。")
                transcript, last_percent = self._drain_progress(
                    chunks, transcript, last_percent, scale, progress
                )
                time.sleep(0.05)
            for reader in readers:
                reader.join(timeout=1)
            transcript, _ = self._drain_progress(
                chunks, transcript, last_percent, scale, progress
            )
            if cancel_event.is_set():
                raise UpscaleCancelledError("高画質化をキャンセルしました。")
            if self._process.returncode != 0:
                lowered = transcript.lower()
                LOGGER.error("Upscale backend failed (%s): %s", self._process.returncode, transcript[-4000:])
                if any(marker in lowered for marker in _GPU_MARKERS):
                    raise GPUUnavailableError("この環境では高画質化エンジンを利用できません。")
                raise UpscaleProcessingError("高画質化処理に失敗しました。")
        finally:
            self._process = None

        if not native_output.is_file() or native_output.stat().st_size <= 0:
            raise UpscaleProcessingError("高画質化エンジンが画像を出力しませんでした。")
        if scale == 2:
            progress(92)
            with Image.open(input_path) as source, Image.open(native_output) as native:
                expected = (source.width * 2, source.height * 2)
                reduced = native.convert("RGBA" if "A" in native.getbands() else "RGB")
                reduced = reduced.resize(expected, Image.Resampling.LANCZOS)
                reduced.save(output_path, format="PNG", optimize=True)
            progress(100)
        else:
            progress(100)

    @staticmethod
    def _drain_progress(
        chunks: queue.Queue[str], transcript: str, last_percent: int,
        scale: int, progress: ProgressCallback,
    ) -> tuple[str, int]:
        while True:
            try:
                transcript += chunks.get_nowait()
            except queue.Empty:
                break
        matches = list(_PERCENT.finditer(transcript[-500:]))
        if matches:
            raw = min(100, int(float(matches[-1].group(1))))
            mapped = round(raw * (0.9 if scale == 2 else 1.0))
            if mapped > last_percent:
                progress(mapped)
                last_percent = mapped
        return transcript[-12000:], last_percent

    def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
