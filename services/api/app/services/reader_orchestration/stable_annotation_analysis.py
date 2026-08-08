"""Deep module: stable block annotation analysis (single owner).

Every stable block annotation derived from a freeze plan passes through
:func:`analyze_stable_annotations` **before any silent filtering**. The
module owns, and no caller may re-implement:

- half-open interval ``[start, end)`` intersection against unit ranges;
- out-of-bounds clipping (used ONLY to determine affected units — the
  clipped range is never disguised as a new annotation);
- consistent / conflicting duplicate judgement (deterministic first-wins);
- inline mark validation, discard, and exact-identity dedupe;
- structural policy override attribution with frozen primary-reason
  precedence;
- deterministic ordering of accepted annotations, diagnostics, and
  override records.

Inline mark corruption only drops the bad mark and records a diagnostic —
it never produces a policy override (no unit all-off). Only structural
annotation misalignment attributable to a unit yields an override record.

Dependencies: stdlib / typing plus the single shared source-link policy.
The module is imported one-way by ``base_builder`` and
``document_freeze_persistence``; it never imports them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from .source_link_policy import is_safe_source_link

DIAGNOSTICS_VERSION: Final[str] = "stable_annotation_diagnostics_v1"
STRUCTURAL_INTEGRITY_OVERRIDE_VERSION: Final[str] = "structural_integrity_override_v1"

# Diagnostics / override reason vocabulary (shared).
ANNOTATION_CONFLICTING_DUPLICATE: Final[str] = "annotation_conflicting_duplicate"
ANNOTATION_RANGE_OUT_OF_BOUNDS: Final[str] = "annotation_range_out_of_bounds"
ANNOTATION_MULTI_UNIT_OVERLAP: Final[str] = "annotation_multi_unit_overlap"
ANNOTATION_RANGE_MISMATCH: Final[str] = "annotation_range_mismatch"
ANNOTATION_RANGE_NON_INTEGER: Final[str] = "annotation_range_non_integer"
ANNOTATION_RANGE_EMPTY: Final[str] = "annotation_range_empty"
ANNOTATION_DUPLICATE_CONSISTENT: Final[str] = "annotation_duplicate_consistent"
ANNOTATION_INLINE_MARK_INVALID: Final[str] = "annotation_inline_mark_invalid"

# Frozen primary reason precedence when one unit hits multiple structural
# misalignments; the surviving override record carries the earliest reason.
_PRIMARY_REASON_PRECEDENCE: Final[tuple[str, ...]] = (
    ANNOTATION_CONFLICTING_DUPLICATE,
    ANNOTATION_RANGE_OUT_OF_BOUNDS,
    ANNOTATION_MULTI_UNIT_OVERLAP,
    ANNOTATION_RANGE_MISMATCH,
)

INLINE_MARK_TYPES: Final[frozenset[str]] = frozenset(
    {"strong", "em", "strikethrough", "inline_code", "link"}
)
_INLINE_MARK_KEYS: Final[frozenset[str]] = frozenset({"type", "start", "end", "href"})


@dataclass(frozen=True, slots=True)
class StableBlockAnnotation:
    """Stable block interval annotation for unit classification.

    Carries the stable ``block_type`` and payload of a
    ``StableDocumentBlock`` plus its UTF-16 range in the canonical text.
    """

    start_utf16: int
    end_utf16: int
    block_type: str
    block_id: str
    parent_block_id: str | None = None
    payload_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StableUnitRange:
    """Half-open ``[start_utf16, end_utf16)`` range of a built unit."""

    unit_id: str
    start_utf16: int
    end_utf16: int


@dataclass(frozen=True, slots=True)
class StableAnnotationDiagnostic:
    code: str
    severity: str
    scope: str
    ref_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class StableAnnotationPolicyOverride:
    """Deterministic per-unit structural integrity override record."""

    unit_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class AcceptedStableBlockAnnotation:
    """An accepted annotation plus its validated inline marks."""

    annotation: StableBlockAnnotation
    inline_marks: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class StableAnnotationAnalysis:
    accepted_annotations: tuple[AcceptedStableBlockAnnotation, ...]
    diagnostics: tuple[StableAnnotationDiagnostic, ...]
    policy_overrides: tuple[StableAnnotationPolicyOverride, ...]

    def diagnostics_payload(self) -> dict[str, Any]:
        """Versioned persistence payload for ``reading_bases.diagnostics_json``."""
        return {
            "version": DIAGNOSTICS_VERSION,
            "items": [asdict(item) for item in self.diagnostics],
        }


def empty_diagnostics_payload() -> dict[str, Any]:
    """Canonical empty payload — the versioned object, never a bare ``[]``."""
    return {"version": DIAGNOSTICS_VERSION, "items": []}


def _is_native_int(value: Any) -> bool:
    # ``bool`` is a subclass of ``int`` — reject it explicitly.
    return isinstance(value, int) and not isinstance(value, bool)


def _diagnostic(code: str, scope: str, ref_id: str, detail: str) -> StableAnnotationDiagnostic:
    return StableAnnotationDiagnostic(
        code=code,
        severity="warning",
        scope=scope,
        ref_id=ref_id,
        detail=detail,
    )


def _inline_mark_problem(entry: Any, block_utf16_length: int) -> str | None:
    if not isinstance(entry, dict):
        return "inline mark is not an object"
    keys = set(entry.keys())
    if not {"type", "start", "end"} <= keys:
        return "inline mark misses required keys"
    if not keys <= _INLINE_MARK_KEYS:
        return "inline mark carries unexpected keys"
    mark_type = entry["type"]
    if mark_type not in INLINE_MARK_TYPES:
        return "unknown inline mark type"
    start = entry["start"]
    end = entry["end"]
    if not _is_native_int(start) or not _is_native_int(end):
        return "inline mark range is not native integers"
    if not (0 <= start < end <= block_utf16_length):
        return "inline mark range outside block bounds"
    if mark_type == "link":
        href = entry.get("href")
        if not isinstance(href, str) or not href:
            return "link mark misses href"
        if not is_safe_source_link(href):
            return "link mark href is not a safe source link"
    elif "href" in entry:
        return "non-link inline mark carries href"
    return None


def _validate_inline_marks(
    payload: dict[str, Any],
    *,
    block_utf16_length: int,
    block_id: str,
) -> tuple[tuple[dict[str, Any], ...], list[StableAnnotationDiagnostic]]:
    raw = payload.get("inline_marks")
    if raw is None:
        return (), []
    if not isinstance(raw, list):
        return (), [
            _diagnostic(
                ANNOTATION_INLINE_MARK_INVALID,
                "inline_mark",
                block_id,
                "inline_marks payload is not a list",
            )
        ]
    marks: list[dict[str, Any]] = []
    diagnostics: list[StableAnnotationDiagnostic] = []
    seen: set[tuple[str, int, int, str | None]] = set()
    for entry in raw:
        problem = _inline_mark_problem(entry, block_utf16_length)
        if problem is not None:
            diagnostics.append(
                _diagnostic(ANNOTATION_INLINE_MARK_INVALID, "inline_mark", block_id, problem)
            )
            continue
        mark_type = entry["type"]
        identity = (
            mark_type,
            entry["start"],
            entry["end"],
            entry.get("href") if mark_type == "link" else None,
        )
        if identity in seen:
            # Exact duplicate (same type, range, and stored href): deterministic
            # dedupe. Overlapping-but-different marks are both legal.
            continue
        seen.add(identity)
        mark: dict[str, Any] = {
            "type": mark_type,
            "start": entry["start"],
            "end": entry["end"],
        }
        if mark_type == "link":
            mark["href"] = entry["href"]
        marks.append(mark)
    return tuple(marks), diagnostics


def _overlapping_units(
    start: int,
    end: int,
    unit_ranges: Sequence[StableUnitRange],
) -> list[StableUnitRange]:
    return [
        unit
        for unit in unit_ranges
        if start < unit.end_utf16 and unit.start_utf16 < end
    ]


def analyze_stable_annotations(
    *,
    raw_annotations: Sequence[StableBlockAnnotation],
    base_utf16_length: int,
    unit_ranges: Sequence[StableUnitRange],
) -> StableAnnotationAnalysis:
    """Analyze raw stable block annotations against built unit ranges.

    Every annotation enters here unfiltered. Returns the accepted
    annotations (exact range match, validated marks), the deterministic
    diagnostics, and the per-unit structural policy override records.
    """
    diagnostics: list[StableAnnotationDiagnostic] = []
    overrides_by_unit: dict[str, str] = {}

    def add_override(unit_id: str, reason: str) -> None:
        existing = overrides_by_unit.get(unit_id)
        if existing is None or _PRIMARY_REASON_PRECEDENCE.index(
            reason
        ) < _PRIMARY_REASON_PRECEDENCE.index(existing):
            overrides_by_unit[unit_id] = reason

    # Group by range, preserving first-seen order. Annotations whose
    # offsets are not native integers have no legal geometry at all —
    # they are excluded before any grouping or intersection.
    by_range: dict[tuple[int, int], list[StableBlockAnnotation]] = {}
    for annotation in raw_annotations:
        start = annotation.start_utf16
        end = annotation.end_utf16
        if not _is_native_int(start) or not _is_native_int(end):
            diagnostics.append(
                _diagnostic(
                    ANNOTATION_RANGE_NON_INTEGER,
                    "block",
                    annotation.block_id,
                    "annotation range offsets are not native integers",
                )
            )
            continue
        by_range.setdefault((start, end), []).append(annotation)

    accepted: list[AcceptedStableBlockAnnotation] = []

    for (start, end), group in by_range.items():
        first = group[0]
        duplicates = group[1:]

        for duplicate in duplicates:
            consistent = (
                duplicate.block_type == first.block_type
                and duplicate.block_id == first.block_id
                and duplicate.payload_json == first.payload_json
            )
            if consistent:
                diagnostics.append(
                    _diagnostic(
                        ANNOTATION_DUPLICATE_CONSISTENT,
                        "block",
                        duplicate.block_id,
                        "identical duplicate annotation dropped (first wins)",
                    )
                )
                continue
            diagnostics.append(
                _diagnostic(
                    ANNOTATION_CONFLICTING_DUPLICATE,
                    "block",
                    duplicate.block_id,
                    "conflicting duplicate annotation dropped (first wins)",
                )
            )
            targets = [
                unit
                for unit in unit_ranges
                if unit.start_utf16 == start and unit.end_utf16 == end
            ] or _overlapping_units(start, end, unit_ranges)
            for unit in targets:
                add_override(unit.unit_id, ANNOTATION_CONFLICTING_DUPLICATE)

        if start >= end:
            # Empty / reversed range: never swap endpoints or invent a span.
            diagnostics.append(
                _diagnostic(
                    ANNOTATION_RANGE_EMPTY,
                    "block",
                    first.block_id,
                    "annotation range is empty or reversed",
                )
            )
            continue

        if start < 0 or end > base_utf16_length:
            # Partially (or fully) outside the canonical base. Clipping is
            # used ONLY to determine affected units; the annotation itself
            # is always excluded.
            clipped_start = max(start, 0)
            clipped_end = min(end, base_utf16_length)
            affected = (
                _overlapping_units(clipped_start, clipped_end, unit_ranges)
                if clipped_start < clipped_end
                else []
            )
            diagnostics.append(
                _diagnostic(
                    ANNOTATION_RANGE_OUT_OF_BOUNDS,
                    "block",
                    first.block_id,
                    "annotation range exceeds the canonical base",
                )
            )
            for unit in affected:
                add_override(unit.unit_id, ANNOTATION_RANGE_OUT_OF_BOUNDS)
            continue

        exact = [
            unit
            for unit in unit_ranges
            if unit.start_utf16 == start and unit.end_utf16 == end
        ]
        if exact:
            inline_marks, mark_diagnostics = _validate_inline_marks(
                first.payload_json or {},
                block_utf16_length=end - start,
                block_id=first.block_id,
            )
            diagnostics.extend(mark_diagnostics)
            accepted.append(
                AcceptedStableBlockAnnotation(
                    annotation=first,
                    inline_marks=inline_marks,
                )
            )
            continue

        affected = _overlapping_units(start, end, unit_ranges)
        if not affected:
            diagnostics.append(
                _diagnostic(
                    ANNOTATION_RANGE_MISMATCH,
                    "block",
                    first.block_id,
                    "annotation range matches no unit and overlaps none",
                )
            )
            continue
        if len(affected) > 1:
            diagnostics.append(
                _diagnostic(
                    ANNOTATION_MULTI_UNIT_OVERLAP,
                    "block",
                    first.block_id,
                    "annotation range overlaps multiple units",
                )
            )
            for unit in affected:
                add_override(unit.unit_id, ANNOTATION_MULTI_UNIT_OVERLAP)
            continue
        diagnostics.append(
            _diagnostic(
                ANNOTATION_RANGE_MISMATCH,
                "block",
                first.block_id,
                "annotation range does not exactly match its overlapping unit",
            )
        )
        add_override(affected[0].unit_id, ANNOTATION_RANGE_MISMATCH)

    # Deterministic canonical ordering (module-owned).
    diagnostics.sort(key=lambda item: (item.code, item.ref_id, item.detail))
    policy_overrides = tuple(
        StableAnnotationPolicyOverride(unit_id=unit_id, reason_code=reason)
        for unit_id, reason in sorted(overrides_by_unit.items())
    )
    return StableAnnotationAnalysis(
        accepted_annotations=tuple(accepted),
        diagnostics=tuple(diagnostics),
        policy_overrides=policy_overrides,
    )
