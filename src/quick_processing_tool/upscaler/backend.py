from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from .models import UpscaleMode


ProgressCallback = Callable[[int | None], None]


@dataclass(frozen=True, slots=True)
class BackendAvailability:
    available: bool
    code: str = "available"
    user_message: str = "高画質化エンジンを利用できます"
    detail: str = ""


class UpscaleBackend(ABC):
    name = "Upscale backend"

    @abstractmethod
    def check_availability(self) -> BackendAvailability:
        raise NotImplementedError

    @abstractmethod
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
        """Write an exact scale PNG to output_path or raise UpscaleError."""
        raise NotImplementedError
