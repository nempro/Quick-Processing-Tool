"""Local GPU image upscaling."""

from .backend import BackendAvailability, UpscaleBackend
from .models import UpscaleMode, UpscaleOptions, UpscaleOutputFormat, UpscaleResult
from .real_esrgan import RealESRGANNCNNBackend
from .service import UpscaleService

__all__ = [
    "BackendAvailability",
    "RealESRGANNCNNBackend",
    "UpscaleBackend",
    "UpscaleMode",
    "UpscaleOptions",
    "UpscaleOutputFormat",
    "UpscaleResult",
    "UpscaleService",
]
