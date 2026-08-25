from __future__ import annotations

from dataclasses import dataclass


# This is a warning threshold, not a hard processing limit.  The backend and
# available GPU memory determine whether a user-approved job can succeed.
LARGE_OUTPUT_WARNING_PIXELS = 100_000_000


@dataclass(frozen=True, slots=True)
class ProjectedOutput:
    width: int
    height: int
    total_pixels: int

    @property
    def megapixels(self) -> float:
        return self.total_pixels / 1_000_000.0

    @property
    def is_large(self) -> bool:
        return self.total_pixels >= LARGE_OUTPUT_WARNING_PIXELS


def projected_output(width: int, height: int, scale: int) -> ProjectedOutput:
    """Return the exact integer output dimensions before allocating pixels."""
    if width < 1 or height < 1:
        raise ValueError("Image dimensions must be positive")
    if scale < 1:
        raise ValueError("Scale must be positive")
    output_width = width * scale
    output_height = height * scale
    return ProjectedOutput(output_width, output_height, output_width * output_height)
