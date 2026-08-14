"""Deterministic vocabulary seed graders.

The four graders in this module operate on a vocabulary-specific artifact
shape (`VocabularyExecutionSnapshot`) and do not inherit from
`BaseGrader` because the parent class is bound to `EvalCase` /
`EvalCaseArtifact` (article-analysis oriented). The vocabulary seed task
must not pollute the existing article-analysis contract.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Protocol

from claread_eval.schemas.vocabulary import (
    ALLOWED_ITEM_TYPES,
    ALLOWED_REASON_CODES,
    MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH,
    MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH,
    MAX_VOCABULARY_DIAGNOSTIC_ITEMS,
    MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH,
    MAX_VOCABULARY_ITEMS,
    VocabularyEvalCase,
    VocabularyExecutionSnapshot,
    VocabularyGraderResult,
    VocabularyResolvedCandidate,
)


class VocabularyGrader(Protocol):
    """Protocol for deterministic vocabulary seed graders.

    Implementations expose a `name` and a `grade(case, snapshot)` method
    returning a `VocabularyGraderResult`. Concrete graders below conform
    to this protocol but are not bound to it (so call sites can pass any
    object exposing the same shape).
    """

    name: str

    def grade(
        self,
        case: VocabularyEvalCase,
        snapshot: VocabularyExecutionSnapshot,
    ) -> VocabularyGraderResult: ...

ITEM_PRIORITY: dict[str, int] = {
    "context_gloss": 0,
    "phrase_gloss": 1,
    "vocab_highlight": 2,
}


def span_key(candidate: VocabularyResolvedCandidate) -> tuple[str, int, int]:
    return (candidate.anchor_segment_id, candidate.unit_start_utf16, candidate.unit_end_utf16)


def _fail_closed_skip(case: VocabularyEvalCase, snapshot: VocabularyExecutionSnapshot) -> bool:
    if snapshot.fail_closed:
        return True
    return case.execution is not None and snapshot is not None and snapshot.fail_closed


def _diagnostics_field(snapshot: VocabularyExecutionSnapshot, key: str) -> object:
    return snapshot.diagnostics.get(key)


class AnchorResolutionGrader:
    """Verify that resolved candidates match the segment text exactly.

    - `selected_text` from the resolved item must equal
      `slice_by_utf16_offsets(unit_text, start, end)`.
    - `text_hash` must match the FNV-1a 32-bit UTF-16 hash of that slice.
    - Resolved offsets must fall inside the anchor_segment range.
    - No two resolved items may share the same `(anchor_segment_id, start, end)`.
    """

    name = "anchor_resolution"

    def grade(
        self, case: VocabularyEvalCase, snapshot: VocabularyExecutionSnapshot
    ) -> VocabularyGraderResult:
        if snapshot.fail_closed:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="skip",
                severity="info",
                metric="anchor_resolution",
                value=None,
                expected=None,
                evidence=f"fail-closed: {snapshot.fail_closed_reason}",
            )

        segments_by_id = {
            seg.anchor_segment_id: seg
            for seg in case.anchor_segments
        }

        span_counts: Counter[tuple[str, int, int]] = Counter()
        issues: list[str] = []

        for index, item in enumerate(snapshot.output.items):
            span_counts[span_key(item)] += 1
            segment = segments_by_id.get(item.anchor_segment_id)
            if segment is None:
                issues.append(
                    f"item[{index}] anchor_segment_id={item.anchor_segment_id} "
                    "not in unit anchor_segments"
                )
                continue
            if (
                item.unit_start_utf16 < segment.unit_start_utf16
                or item.unit_end_utf16 > segment.unit_end_utf16
            ):
                issues.append(
                    f"item[{index}] offsets "
                    f"({item.unit_start_utf16},{item.unit_end_utf16}) "
                    f"fall outside segment "
                    f"({segment.unit_start_utf16},{segment.unit_end_utf16})"
                )
                continue
            slice_text = _slice_utf16(case.unit_text, item.unit_start_utf16, item.unit_end_utf16)
            if slice_text != item.selected_text:
                issues.append(
                    f"item[{index}] selected_text round-trip mismatch: "
                    f"expected={slice_text!r} actual={item.selected_text!r}"
                )
                continue
            expected_hash = _fnv1a32_utf16(item.selected_text)
            if item.text_hash != expected_hash:
                issues.append(
                    f"item[{index}] text_hash mismatch: "
                    f"expected={expected_hash} actual={item.text_hash}"
                )

        for span, count in span_counts.items():
            if count > 1:
                issues.append(f"duplicate span {span[0]}@({span[1]},{span[2]}) x{count}")

        if issues:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="fail",
                severity="hard",
                metric="anchor_resolution",
                value={"item_count": len(snapshot.output.items)},
                expected="resolved offsets/hashes match segment slice",
                evidence="; ".join(issues),
            )

        return VocabularyGraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict="pass",
            severity="hard",
            metric="anchor_resolution",
            value={"item_count": len(snapshot.output.items)},
            expected="resolved offsets/hashes match segment slice",
            evidence="all resolved items match segment slice + fnv1a32 hash",
        )


class BoundsComplianceGrader:
    """Verify that the resolved output and diagnostics stay within bounds.

    - `MAX_VOCABULARY_ITEMS = 5` resolved items.
    - `selected_text` ≤ 160 UTF-16 code units.
    - `gloss` / `brief_explanation` / `reason` / `example` ≤ 240 chars
      (text fields are stored on the gold items, not in the snapshot;
      we only verify item count + selected_text length from the snapshot).
    - Diagnostics bounded to ≤ 8 entries with text ≤ 80 chars.
    - Diagnostics `reason_code` values must come from the worker enum.
    """

    name = "bounds_compliance"

    def grade(
        self, case: VocabularyEvalCase, snapshot: VocabularyExecutionSnapshot
    ) -> VocabularyGraderResult:
        issues: list[str] = []

        if snapshot.fail_closed:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="skip",
                severity="info",
                metric="bounds_compliance",
                value=None,
                expected=None,
                evidence=f"fail-closed: {snapshot.fail_closed_reason}",
            )

        items = snapshot.output.items
        if len(items) > MAX_VOCABULARY_ITEMS:
            issues.append(
                f"resolved item_count={len(items)} exceeds "
                f"MAX_VOCABULARY_ITEMS={MAX_VOCABULARY_ITEMS}"
            )

        for index, item in enumerate(items):
            utf16_len = _utf16_code_units(item.selected_text)
            if utf16_len > MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH:
                issues.append(
                    f"item[{index}] selected_text utf16_len={utf16_len} "
                    f"exceeds MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH="
                    f"{MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH}"
                )

        for index, gold in enumerate(case.gold_items):
            if gold.gloss is not None and len(gold.gloss) > MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH:
                issues.append(
                    f"gold_items[{index}] gloss length={len(gold.gloss)} "
                    f"exceeds MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH="
                    f"{MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH}"
                )
            if (
                gold.brief_explanation is not None
                and len(gold.brief_explanation) > MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH
            ):
                issues.append(
                    f"gold_items[{index}] brief_explanation length="
                    f"{len(gold.brief_explanation)} exceeds "
                    f"MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH="
                    f"{MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH}"
                )

        diag = snapshot.diagnostics
        skipped_items = diag.get("skipped_items") or []
        if not isinstance(skipped_items, list):
            issues.append("diagnostics.skipped_items must be a list")
            skipped_items = []
        if len(skipped_items) > MAX_VOCABULARY_DIAGNOSTIC_ITEMS:
            issues.append(
                f"diagnostics.skipped_items length={len(skipped_items)} "
                f"exceeds MAX_VOCABULARY_DIAGNOSTIC_ITEMS="
                f"{MAX_VOCABULARY_DIAGNOSTIC_ITEMS}"
            )

        for index, entry in enumerate(skipped_items):
            if not isinstance(entry, dict):
                issues.append(f"skipped_items[{index}] is not a dict")
                continue
            reason_code = entry.get("reason_code")
            if reason_code not in ALLOWED_REASON_CODES:
                issues.append(
                    f"skipped_items[{index}] reason_code={reason_code!r} "
                    f"not in allowed enum {ALLOWED_REASON_CODES}"
                )
            text = entry.get("selected_text")
            if isinstance(text, str):
                if len(text) > MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH:
                    issues.append(
                        f"skipped_items[{index}] selected_text length="
                        f"{len(text)} exceeds MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH="
                        f"{MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH}"
                    )

        item_types = [item.item_type for item in items]
        for t in item_types:
            if t not in ALLOWED_ITEM_TYPES:
                issues.append(f"item_type={t!r} not in {ALLOWED_ITEM_TYPES}")

        if issues:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="fail",
                severity="hard",
                metric="bounds_compliance",
                value={
                    "item_count": len(items),
                    "skipped_count": len(skipped_items),
                },
                expected={
                    "max_items": MAX_VOCABULARY_ITEMS,
                    "max_selected_text_utf16": MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH,
                    "max_note_length": MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH,
                    "max_diagnostic_items": MAX_VOCABULARY_DIAGNOSTIC_ITEMS,
                    "max_diagnostic_text": MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH,
                },
                evidence="; ".join(issues),
            )

        return VocabularyGraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict="pass",
            severity="hard",
            metric="bounds_compliance",
            value={
                "item_count": len(items),
                "skipped_count": len(skipped_items),
            },
            expected={
                "max_items": MAX_VOCABULARY_ITEMS,
                "max_selected_text_utf16": MAX_VOCABULARY_CANDIDATE_TEXT_LENGTH,
                "max_note_length": MAX_VOCABULARY_CANDIDATE_NOTE_LENGTH,
                "max_diagnostic_items": MAX_VOCABULARY_DIAGNOSTIC_ITEMS,
                "max_diagnostic_text": MAX_VOCABULARY_DIAGNOSTIC_TEXT_LENGTH,
            },
            evidence="all bounds within limits",
        )


class DiagnosticsCoverageGrader:
    """Verify the worker reports a reason when it rejects or skips items.

    Pass conditions:
      - Empty output with `candidate_item_count == 0` is allowed without
        skipped_items.
      - Empty output with `candidate_item_count > 0` must carry skipped_items
        with at least one allowed `reason_code`.
      - The diagnostics schema must contain the five required keys:
        `candidate_item_count`, `resolved_item_count`, `skipped_item_count`,
        `skipped_items`, `skipped_items_truncated_count`.
      - `expected_diagnostics` (if non-empty) must be honored: candidate /
        resolved / skipped counts and `skipped_reason_codes` must match or
        be bounded by the spec.
    """

    name = "diagnostics_coverage"

    REQUIRED_KEYS: tuple[str, ...] = (
        "candidate_item_count",
        "resolved_item_count",
        "skipped_item_count",
        "skipped_items",
        "skipped_items_truncated_count",
    )

    def grade(
        self, case: VocabularyEvalCase, snapshot: VocabularyExecutionSnapshot
    ) -> VocabularyGraderResult:
        if snapshot.fail_closed:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="skip",
                severity="info",
                metric="diagnostics_coverage",
                value=None,
                expected=None,
                evidence=f"fail-closed: {snapshot.fail_closed_reason}",
            )

        diag = snapshot.diagnostics
        missing = [k for k in self.REQUIRED_KEYS if k not in diag]
        issues: list[str] = []
        if missing:
            issues.append(f"missing diagnostics keys: {missing}")

        candidate_count = _safe_int(_diagnostics_field(snapshot, "candidate_item_count"))
        resolved_count = _safe_int(_diagnostics_field(snapshot, "resolved_item_count"))
        skipped_count = _safe_int(_diagnostics_field(snapshot, "skipped_item_count"))

        if (
            candidate_count is not None
            and resolved_count is not None
            and len(snapshot.output.items) != resolved_count
        ):
            issues.append(
                f"resolved_item_count={resolved_count} disagrees with "
                f"actual output items={len(snapshot.output.items)}"
            )
        if (
            resolved_count is not None
            and skipped_count is not None
            and candidate_count is not None
            and (
                resolved_count + skipped_count
                > candidate_count + len(diag.get("skipped_items") or [])
            )
        ):
            # Basic accounting; allow candidate_count + diag.skipped_items
            # to exceed by truncated_count.
            issues.append(
                "candidate_item_count < resolved_item_count + "
                "diagnostics.skipped_items total"
            )

        skipped_items = diag.get("skipped_items") or []
        reason_codes = [
            entry.get("reason_code")
            for entry in skipped_items
            if isinstance(entry, dict)
        ]

        resolved_count_actual = len(snapshot.output.items)
        if resolved_count_actual == 0 and candidate_count and candidate_count > 0:
            if not skipped_items or not any(
                code in ALLOWED_REASON_CODES for code in reason_codes if code
            ):
                issues.append(
                    "empty resolved output but candidates were rejected; "
                    "skipped_items must include at least one allowed reason_code"
                )

        expected = case.expected_diagnostics
        if expected.candidate_item_count is not None and candidate_count is not None:
            if candidate_count != expected.candidate_item_count:
                issues.append(
                    f"candidate_item_count={candidate_count} != "
                    f"expected={expected.candidate_item_count}"
                )
        if (
            expected.resolved_item_count is not None
            and resolved_count_actual != expected.resolved_item_count
        ):
            issues.append(
                f"actual resolved count={resolved_count_actual} != "
                f"expected={expected.resolved_item_count}"
            )
        if expected.skipped_item_count is not None and skipped_count is not None:
            if skipped_count != expected.skipped_item_count:
                issues.append(
                    f"skipped_item_count={skipped_count} != "
                    f"expected={expected.skipped_item_count}"
                )
        if expected.skipped_item_count_at_least is not None and skipped_count is not None:
            if skipped_count < expected.skipped_item_count_at_least:
                issues.append(
                    f"skipped_item_count={skipped_count} < "
                    f"at_least={expected.skipped_item_count_at_least}"
                )
        if expected.skipped_items_truncated_count is not None:
            truncated = _safe_int(_diagnostics_field(snapshot, "skipped_items_truncated_count"))
            if truncated != expected.skipped_items_truncated_count:
                issues.append(
                    f"skipped_items_truncated_count={truncated} != "
                    f"expected={expected.skipped_items_truncated_count}"
                )
        if expected.skipped_reason_codes_at_least:
            have = set(code for code in reason_codes if code)
            required = set(expected.skipped_reason_codes_at_least)
            missing_codes = required - have
            if missing_codes:
                issues.append(
                    f"missing required reason_codes: {sorted(missing_codes)}"
                )
        if expected.skipped_reason_codes:
            required = set(expected.skipped_reason_codes)
            actual = {code for code in reason_codes if code}
            missing_codes = required - actual
            unexpected_codes = actual - required
            if missing_codes or unexpected_codes:
                issues.append(
                    "skipped_reason_codes mismatch: "
                    f"missing={sorted(missing_codes)} "
                    f"unexpected={sorted(unexpected_codes)}"
                )

        if issues:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="fail",
                severity="hard",
                metric="diagnostics_coverage",
                value={
                    "candidate_item_count": candidate_count,
                    "resolved_item_count": resolved_count,
                    "skipped_item_count": skipped_count,
                },
                expected=expected.model_dump(),
                evidence="; ".join(issues),
            )

        return VocabularyGraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict="pass",
            severity="hard",
            metric="diagnostics_coverage",
            value={
                "candidate_item_count": candidate_count,
                "resolved_item_count": resolved_count,
                "skipped_item_count": skipped_count,
            },
            expected=expected.model_dump(),
            evidence="diagnostics schema + reason codes match expected",
        )


class SpanConflictArbitrationGrader:
    """Verify same-span collision is arbitrated by priority.

    The worker resolves priorities as
    `context_gloss > phrase_gloss > vocab_highlight`. After arbitration
    the final published output must not contain two items sharing the
    same `(anchor_segment_id, start_offset, end_offset)` triple. For
    cases that exercise arbitration, only the highest-priority item
    should survive and the other(s) should appear in `diagnostics.skipped_items`
    with `reason_code = span_conflict_higher_priority_kept`.
    """

    name = "span_conflict_arb"

    def grade(
        self, case: VocabularyEvalCase, snapshot: VocabularyExecutionSnapshot
    ) -> VocabularyGraderResult:
        if snapshot.fail_closed:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="skip",
                severity="info",
                metric="span_conflict_arb",
                value=None,
                expected=None,
                evidence=f"fail-closed: {snapshot.fail_closed_reason}",
            )

        issues: list[str] = []
        seen: dict[tuple[str, int, int], VocabularyResolvedCandidate] = {}
        for item in snapshot.output.items:
            key = span_key(item)
            if key in seen:
                issues.append(
                    f"duplicate span {key[0]}@({key[1]},{key[2]}) "
                    f"items={seen[key].item_type} and {item.item_type}"
                )
                continue
            seen[key] = item

        skipped_items = snapshot.diagnostics.get("skipped_items") or []
        arb_skips = [
            entry
            for entry in skipped_items
            if isinstance(entry, dict)
            and entry.get("reason_code") == "span_conflict_higher_priority_kept"
        ]

        output_by_span = {span_key(item): item for item in snapshot.output.items}
        gold_by_span: dict[tuple[str, int, int], list[str]] = defaultdict(list)
        for gold in case.gold_items:
            key = _resolve_gold_span_key(case, gold.anchor_segment_id, gold.selected_text)
            if key is None:
                continue
            gold_by_span[key].append(gold.item_type)

        for key, item_types in gold_by_span.items():
            if len(set(item_types)) <= 1:
                continue
            expected_item_type = min(item_types, key=lambda t: ITEM_PRIORITY.get(t, 99))
            surviving_item = output_by_span.get(key)
            if surviving_item is None:
                issues.append(
                    "same-span gold conflict has no surviving output item "
                    f"for {key[0]}@({key[1]},{key[2]})"
                )
            elif surviving_item.item_type != expected_item_type:
                issues.append(
                    "same-span gold conflict kept wrong item type "
                    f"for {key[0]}@({key[1]},{key[2]}): "
                    f"expected={expected_item_type} actual={surviving_item.item_type}"
                )

        # If gold specifies a winning item but no candidate survived, fail.
        if case.gold_items and not seen and arb_skips:
            issues.append(
                "gold expected a surviving item but all candidates were discarded "
                "by span_conflict arbitration"
            )

        if issues:
            return VocabularyGraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict="fail",
                severity="hard",
                metric="span_conflict_arb",
                value={
                    "resolved_spans": sorted(seen.keys()),
                    "arb_skip_count": len(arb_skips),
                },
                expected="one item per span, conflicts reported via diagnostics",
                evidence="; ".join(issues),
            )

        return VocabularyGraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict="pass",
            severity="hard",
            metric="span_conflict_arb",
            value={
                "resolved_span_count": len(seen),
                "arb_skip_count": len(arb_skips),
            },
            expected="one item per span, conflicts reported via diagnostics",
            evidence="no duplicate spans; arbitration respected",
        )


VOCABULARY_GRADERS: tuple[object, ...] = (
    AnchorResolutionGrader(),
    BoundsComplianceGrader(),
    DiagnosticsCoverageGrader(),
    SpanConflictArbitrationGrader(),
)


def run_all_graders(
    case: VocabularyEvalCase, snapshot: VocabularyExecutionSnapshot
) -> list[VocabularyGraderResult]:
    return [grader.grade(case, snapshot) for grader in VOCABULARY_GRADERS]


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _resolve_gold_span_key(
    case: VocabularyEvalCase,
    anchor_segment_id: str,
    selected_text: str,
) -> tuple[str, int, int] | None:
    segment = next(
        (
            seg
            for seg in case.anchor_segments
            if seg.anchor_segment_id == anchor_segment_id
        ),
        None,
    )
    if segment is None:
        return None
    segment_text = _slice_utf16(
        case.unit_text,
        segment.unit_start_utf16,
        segment.unit_end_utf16,
    )
    matches: list[tuple[int, int]] = []
    cursor = 0
    while True:
        index = segment_text.find(selected_text, cursor)
        if index < 0:
            break
        prefix_units = _utf16_code_units(segment_text[:index])
        selected_units = _utf16_code_units(selected_text)
        matches.append(
            (
                segment.unit_start_utf16 + prefix_units,
                segment.unit_start_utf16 + prefix_units + selected_units,
            )
        )
        cursor = index + len(selected_text)
    if len(matches) != 1:
        return None
    start, end = matches[0]
    return (anchor_segment_id, start, end)


def _slice_utf16(text: str, start: int, end: int) -> str:
    encoded = text.encode("utf-16-le", "surrogatepass")
    return encoded[start * 2 : end * 2].decode("utf-16-le", "surrogatepass")


def _utf16_code_units(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _fnv1a32_utf16(text: str) -> str:
    """Reproduce the FNV-1a 32-bit hash on UTF-16 code units.

    The worker uses `app.contracts.annotation.compute_text_range_hash`;
    its algorithm is documented as `fnv1a32-utf16`. The eval harness must
    avoid importing services/api so we replicate the contract locally.
    Each UTF-16 code unit is hashed as a little-endian 16-bit integer, which
    matches the worker implementation exactly.
    """
    encoded = text.encode("utf-16-le", "surrogatepass")
    h = 0x811C9DC5
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        h ^= code_unit
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def summarize(results: Iterable[VocabularyGraderResult]) -> dict[str, int]:
    counts = Counter(r.verdict for r in results)
    return {verdict: counts.get(verdict, 0) for verdict in ("pass", "fail", "skip")}
