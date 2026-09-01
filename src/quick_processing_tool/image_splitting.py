from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from PIL import Image


class SplitDirection(str, Enum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


MIN_SPLIT_PANEL_PIXELS = 16


@dataclass(frozen=True, slots=True)
class ImageSplitOptions:
    enabled: bool = False
    direction: SplitDirection = SplitDirection.VERTICAL
    count: int = 4
    boundaries: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not 2 <= self.count <= 6:
            raise ValueError("split count must be between 2 and 6")
        if self.boundaries is None:
            return
        boundaries = tuple(float(value) for value in self.boundaries)
        if len(boundaries) != self.count - 1:
            raise ValueError("custom split boundaries must match the split count")
        if any(not isfinite(value) or not 0.0 < value < 1.0 for value in boundaries):
            raise ValueError("custom split boundaries must be finite ratios between 0 and 1")
        if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
            raise ValueError("custom split boundaries must be strictly increasing")
        object.__setattr__(self, "boundaries", boundaries)

    def boundary_ratios(self) -> tuple[float, ...]:
        return self.boundaries or equal_split_boundaries(self.count)


def equal_split_boundaries(count: int) -> tuple[float, ...]:
    if count < 2:
        raise ValueError("split count must be at least 2")
    return tuple(index / count for index in range(1, count))


def partition_edges(
    length: int,
    count: int,
    boundaries: tuple[float, ...] | None = None,
    minimum_panel_pixels: int = 1,
) -> tuple[int, ...]:
    """Return exact integer boundaries covering a dimension once, without gaps."""
    if count < 2:
        raise ValueError("split count must be at least 2")
    if length < count:
        raise ValueError("image dimension must be at least the split count")
    if boundaries is None:
        return tuple((length * index) // count for index in range(count + 1))
    ratios = ImageSplitOptions(True, SplitDirection.VERTICAL, count, boundaries).boundary_ratios()
    effective_minimum = min(max(1, minimum_panel_pixels), length // count)
    edges = [0]
    for index, ratio in enumerate(ratios, start=1):
        desired = int(length * ratio + 0.5)
        lower = edges[-1] + effective_minimum
        remaining_panels = count - index
        upper = length - (effective_minimum * remaining_panels)
        edges.append(max(lower, min(upper, desired)))
    edges.append(length)
    return tuple(edges)


def split_boxes(
    width: int,
    height: int,
    direction: SplitDirection,
    count: int,
    boundaries: tuple[float, ...] | None = None,
    minimum_panel_pixels: int = 1,
) -> tuple[tuple[int, int, int, int], ...]:
    """Build ordered crop boxes from equal or normalized custom boundaries."""
    if direction is SplitDirection.VERTICAL:
        edges = partition_edges(width, count, boundaries, minimum_panel_pixels)
        return tuple(
            (edges[index], 0, edges[index + 1], height)
            for index in range(count)
        )
    edges = partition_edges(height, count, boundaries, minimum_panel_pixels)
    return tuple(
        (0, edges[index], width, edges[index + 1])
        for index in range(count)
    )


def split_image(
    image: Image.Image,
    direction: SplitDirection,
    count: int,
    boundaries: tuple[float, ...] | None = None,
    minimum_panel_pixels: int = 1,
) -> list[Image.Image]:
    return [
        image.crop(box)
        for box in split_boxes(
            image.width,
            image.height,
            direction,
            count,
            boundaries,
            minimum_panel_pixels,
        )
    ]
