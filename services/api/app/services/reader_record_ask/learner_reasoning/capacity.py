"""Process-global non-blocking capacity limiter for projector runs.

Does **not** use ``Semaphore.acquire(timeout=0)``. Uses an atomic counter
with try/finally release. Saturation drops the checkpoint (fail-closed).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class NonBlockingCapacityLimiter:
    """Thread-safe non-blocking capacity gate.

    ``try_acquire`` returns True only when a slot was taken. Callers that
    receive True **must** call ``release`` exactly once (typically in
    ``finally``). Concurrent oversubscription is prevented by a lock.
    """

    limit: int = 8
    _held: int = 0
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.limit < 1:
            self.limit = 1
        object.__setattr__(self, "_lock", threading.Lock())
        self._held = 0

    @property
    def held(self) -> int:
        with self._lock:
            return self._held

    @property
    def available(self) -> int:
        with self._lock:
            return max(0, self.limit - self._held)

    def try_acquire(self) -> bool:
        with self._lock:
            if self._held >= self.limit:
                return False
            self._held += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._held > 0:
                self._held -= 1


# Process-wide limiter for learner-reasoning projector requests.
_GLOBAL_LIMITER = NonBlockingCapacityLimiter(limit=8)


def get_global_projector_limiter() -> NonBlockingCapacityLimiter:
    return _GLOBAL_LIMITER


def reset_global_projector_limiter_for_tests(
    *, limit: int = 8
) -> NonBlockingCapacityLimiter:
    """Replace the process limiter (tests only)."""
    global _GLOBAL_LIMITER
    _GLOBAL_LIMITER = NonBlockingCapacityLimiter(limit=limit)
    return _GLOBAL_LIMITER


__all__ = [
    "NonBlockingCapacityLimiter",
    "get_global_projector_limiter",
    "reset_global_projector_limiter_for_tests",
]
