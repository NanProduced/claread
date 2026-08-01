"""Turn-local private reasoning buffer (ring, newest-biased).

Raw content exists only in process memory for the current turn. Never
logged, never persisted, never published.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.reader_record_ask.learner_reasoning.schemas import (
    TURN_BUFFER_CHAR_CAP,
    WINDOW_CHAR_LIMIT,
)


@dataclass
class PrivateReasoningBuffer:
    """Ring buffer: always accepts new text; drops oldest past turn_cap.

    - ``turn_cap``: retained characters (default 12K). When full, oldest
      parts are discarded so new text still enters.
    - ``window_limit``: :meth:`freeze_window` returns the most recent
      ``window_limit`` characters of retained content (default 2K).
    - ``_absolute_written`` is monotonic for causal cursor consistency.
    """

    turn_cap: int = TURN_BUFFER_CHAR_CAP
    window_limit: int = WINDOW_CHAR_LIMIT
    _parts: list[str] = field(default_factory=list)
    _retained: int = 0
    _cursor: int = 0
    _absolute_written: int = 0

    def append(self, text: str) -> None:
        if not text:
            return
        # Always accept new text (including a single chunk > turn_cap), then
        # retain only the newest ``turn_cap`` characters.
        self._parts.append(text)
        self._retained += len(text)
        self._absolute_written += len(text)
        self._compact_to_cap()

    def _compact_to_cap(self) -> None:
        """Keep only the newest ``turn_cap`` characters (never wipe all)."""
        if self._retained <= self.turn_cap:
            return
        joined = "".join(self._parts)
        keep = joined[-self.turn_cap :]
        self._parts = [keep] if keep else []
        self._retained = len(keep)

    def clear_generation(self) -> None:
        """Drop retained text for a new generation (retry / tool boundary)."""
        self._parts.clear()
        self._retained = 0
        self._cursor = self._absolute_written

    @property
    def retained_chars(self) -> int:
        return self._retained

    @property
    def cursor(self) -> int:
        return self._cursor

    def joined(self) -> str:
        """Host/test only — never SSE/DTO/log."""
        return "".join(self._parts)

    def freeze_window(self) -> tuple[str, int]:
        """Return (immutable newest window, absolute cursor)."""
        text = self.joined()
        if len(text) > self.window_limit:
            text = text[-self.window_limit :]
        return text, self._absolute_written

    def advance_to(self, cursor: int) -> None:
        if cursor > self._cursor:
            self._cursor = cursor


__all__ = ["PrivateReasoningBuffer"]
