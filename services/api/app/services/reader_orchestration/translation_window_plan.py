"""Pure translation window planner.

Extracted from ``job_bootstrap`` so product analysis sections can reuse the
same greedy packing without copying it. No DB access, no side effects.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# ``translate_article`` batch job per window. Windows are bounded by a
# target char count (close the window once reached) and a safety max
# (never exceed). A single unit larger than safety max becomes its own
# window. The unit is the minimum boundary — units are never split.
#
# Translation windows are intentionally larger than vocabulary windows
# Translation output is per-group translated_text and needs more
# source context for coherent group planning/hydration. A target of 6000
# chars (one short-article equivalent) yields ~5 LLM calls on a 30k-char
# article instead of ~30 per-unit calls, matching the short-article
# per-char cost profile.
TRANSLATION_WINDOW_TARGET_CHAR_COUNT = 6000
TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT = 10000


# ---------------------------------------------------------------------------#
# Non-short translation batch window planner
# ---------------------------------------------------------------------------#
# Pure dataclasses + function. No DB access, no side effects. The bootstrap
# method loads unit metadata (unit_id, order_index, text_length) and calls
# ``plan_translation_windows`` to get a list of consecutive, non-overlapping
# windows. Each window becomes one ``translate_article`` batch job.
#
# Design constraints (see docs/development/mainline.md and docs/operations/testing.md):
# - Unit is the minimum boundary; never split a unit across windows.
# - Windows must be consecutive and non-overlapping, ordered by reading order.
# - A single unit larger than safety max becomes its own window.
# - ``window_id`` is a stable hash of the sorted unit_ids in the window, so
#   re-planning after partial publish produces the same window_id for
#   unchanged windows (idempotency relies on this).
# - The translation and vocabulary planners are intentionally separate: each
#   layer has its own default thresholds and its own idempotency namespace
#   (job_type + operation_fingerprint differ, so window_id collisions across
#   layers never cause idempotency false-positives).


@dataclass(frozen=True, slots=True)
class TranslationWindowUnit:
    """A single unit's metadata for translation window planning."""

    unit_id: str
    order_index: int
    text_length: int


@dataclass(frozen=True, slots=True)
class TranslationWindowPlan:
    """A planned translation batch window: a consecutive range of units."""

    units: tuple[TranslationWindowUnit, ...]

    @property
    def window_id(self) -> str:
        """Stable 12-char hex hash of the sorted unit_ids in this window.

        Two windows with the same unit set produce the same window_id
        regardless of planning order, so idempotency checks on
        ``target_key = f"{record_id}:window:{window_id}"`` correctly
        detect that a window job already exists.
        """
        sorted_ids = ":".join(sorted(u.unit_id for u in self.units))
        return hashlib.sha256(sorted_ids.encode("utf-8")).hexdigest()[:12]

    @property
    def target_unit_ids(self) -> tuple[str, ...]:
        return tuple(u.unit_id for u in self.units)


def plan_translation_windows(
    units: list[TranslationWindowUnit] | tuple[TranslationWindowUnit, ...],
    *,
    target_char_count: int = TRANSLATION_WINDOW_TARGET_CHAR_COUNT,
    safety_max_char_count: int = TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT,
) -> list[TranslationWindowPlan]:
    """Plan translation batch windows for non-short articles.

    Greedy accumulator over units ordered by ``order_index``:

    1. Start a new window with the first remaining unit.
    2. Add the next unit if ``current_chars + next.text_length`` does not
       exceed ``safety_max_char_count``.
    3. If adding would exceed safety max, close the current window and
       start a new one with that unit.
    4. If the current window reaches ``target_char_count``, close it.

    A single unit larger than safety max becomes its own window.

    Returns an empty list if ``units`` is empty. Every input unit appears
    in exactly one output window (coverage + no-overlap).
    """
    if not units:
        return []
    sorted_units = sorted(units, key=lambda u: u.order_index)
    windows: list[TranslationWindowPlan] = []
    current: list[TranslationWindowUnit] = []
    current_chars = 0
    for unit in sorted_units:
        if not current:
            current.append(unit)
            current_chars = unit.text_length
            continue
        if current_chars + unit.text_length > safety_max_char_count:
            windows.append(TranslationWindowPlan(units=tuple(current)))
            current = [unit]
            current_chars = unit.text_length
            continue
        current.append(unit)
        current_chars += unit.text_length
        if current_chars >= target_char_count:
            windows.append(TranslationWindowPlan(units=tuple(current)))
            current = []
            current_chars = 0
    if current:
        windows.append(TranslationWindowPlan(units=tuple(current)))
    return windows
