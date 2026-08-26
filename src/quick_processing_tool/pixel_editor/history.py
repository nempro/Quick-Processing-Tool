from __future__ import annotations

from collections import deque


class PixelHistory:
    """Bounded snapshot history; callers commit once per logical operation/stroke."""

    def __init__(self, initial: bytes, max_entries: int = 100) -> None:
        if max_entries < 2:
            raise ValueError("History must retain at least two entries")
        self._entries: deque[bytes] = deque([bytes(initial)], maxlen=max_entries)
        self._index = 0

    @property
    def current(self) -> bytes:
        return self._entries[self._index]

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index + 1 < len(self._entries)

    def commit(self, snapshot: bytes) -> bool:
        snapshot = bytes(snapshot)
        if snapshot == self.current:
            return False
        while len(self._entries) > self._index + 1:
            self._entries.pop()
        self._entries.append(snapshot)
        self._index = len(self._entries) - 1
        return True

    def undo(self) -> bytes:
        if self.can_undo:
            self._index -= 1
        return self.current

    def redo(self) -> bytes:
        if self.can_redo:
            self._index += 1
        return self.current
