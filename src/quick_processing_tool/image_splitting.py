from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PIL import Image


class SplitDirection(str, Enum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


@dataclass(frozen=True, slots=True)
class ImageSplitOptions:
    enabled: bool = False
    direction: SplitDirection = SplitDirection.VERTICAL
    count: int = 4

    def __post_init__(self) -> None:
        if not 2 <= self.count <= 6:
            raise ValueError("split count must be between 2 and 6")


def partition_edges(length: int, count: int) -> tuple[int, ...]:
    """Return exact integer boundaries covering a dimension once, without gaps."""
    if count < 2:
        raise ValueError("split count must be at least 2")
    if length < count:
        raise ValueError("image dimension must be at least the split count")
    return tuple((length * index) // count for index in range(count + 1))


def split_boxes(
    width: int,
    height: int,
    direction: SplitDirection,
    count: int,
) -> tuple[tuple[int, int, int, int], ...]:
    """Build ordered crop boxes; custom boundaries can replace this function later."""
    if direction is SplitDirection.VERTICAL:
        edges = partition_edges(width, count)
        return tuple(
            (edges[index], 0, edges[index + 1], height)
            for index in range(count)
        )
    edges = partition_edges(height, count)
    return tuple(
        (0, edges[index], width, edges[index + 1])
        for index in range(count)
    )


def split_image(
    image: Image.Image,
    direction: SplitDirection,
    count: int,
) -> list[Image.Image]:
    return [
        image.crop(box)
        for box in split_boxes(image.width, image.height, direction, count)
    ]
