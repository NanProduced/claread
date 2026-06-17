"""Normalized annotation postprocess helpers.

公共 postprocess 逻辑，供 normalize_and_ground.py 和 repair_items.py 复用。
包含 dedup、conflict resolution、density control、canonical stats。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.internal.normalized import (
    CanonicalSpan,
    DropLogEntry,
    NormalizedAnnotation,
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedSentenceAnalysis,
)

# ── Priority ranking ──────────────────────────────────────────────

PRIORITY_RANK: dict[str, int] = {
    "context_gloss": 3,
    "phrase_gloss": 2,
    "vocab_highlight": 1,
    "grammar_note": 10,
    "sentence_analysis": 10,
}


# ── Drop log helper ───────────────────────────────────────────────


def log_drop(
    source_agent: Literal["vocabulary", "grammar", "translation"],
    annotation_type: str,
    sentence_id: str,
    anchor_text: str,
    drop_reason: str,
    drop_stage: Literal[
        "grounding",
        "deduplication",
        "conflict_resolution",
        "density_control",
        "pruning",
        "repair",
    ],
    drop_log: list[DropLogEntry],
) -> None:
    """Append a DropLogEntry to the given drop_log list."""
    drop_log.append(
        DropLogEntry(
            source_agent=source_agent,
            annotation_type=annotation_type,
            sentence_id=sentence_id,
            anchor_text=anchor_text,
            drop_reason=drop_reason,
            drop_stage=drop_stage,
            dropped_at=datetime.now(),
        )
    )


# ── Identity helpers ──────────────────────────────────────────────


def normalized_span_identity(annotation: NormalizedAnnotation) -> str:
    """Identity key for NormalizedAnnotation dedup.

    Includes annotation type, sentence_id, span tuple, and key display
    field to avoid merging annotations with same spans but different
    explanations.
    """
    import json

    spans = getattr(annotation, "spans", None)
    if spans is not None:
        span_payload = json.dumps(
            [{"start": s.start, "end": s.end, "text": s.text} for s in spans],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        span_payload = ""

    display_key = ""
    if isinstance(annotation, NormalizedPhraseGloss):
        display_key = annotation.label
    elif isinstance(annotation, NormalizedContextGloss):
        display_key = annotation.display
    elif isinstance(annotation, NormalizedGrammarNote):
        display_key = annotation.grammar_point
    elif isinstance(annotation, NormalizedSentenceAnalysis):
        display_key = annotation.label

    return f"{annotation.sentence_id}:{annotation.type}:{span_payload}:{display_key}"


def normalized_anchor_text(annotation: NormalizedAnnotation) -> str:
    """Get a representative anchor text for drop log."""
    spans = getattr(annotation, "spans", None)
    if spans:
        return spans[0].text
    if isinstance(annotation, NormalizedSentenceAnalysis):
        return annotation.label
    return ""


def normalized_source_agent(annotation: NormalizedAnnotation) -> str:
    """Determine source agent from annotation type."""
    if annotation.type in {"vocab_highlight", "phrase_gloss", "context_gloss"}:
        return "vocabulary"
    return "grammar"


# ── Overlap helpers ───────────────────────────────────────────────


def canonical_spans_overlap(
    left: list[CanonicalSpan],
    right: list[CanonicalSpan],
) -> bool:
    """Check if two CanonicalSpan lists overlap (same sentence, overlapping ranges)."""
    if not left or not right:
        return False
    if left[0].sentence_id != right[0].sentence_id:
        return False
    return any(
        l_span.start < r_span.end and r_span.start < l_span.end
        for l_span in left
        for r_span in right
    )


def canonical_span_group_contains(
    container: list[CanonicalSpan],
    inner: CanonicalSpan,
) -> bool:
    """Check if any span in container fully contains inner span."""
    return any(
        s.start <= inner.start and inner.end <= s.end
        for s in container
    )


# ── Dedup ─────────────────────────────────────────────────────────


def _normalized_dedup(
    annotations: list[NormalizedAnnotation],
    drop_log: list[DropLogEntry],
) -> list[NormalizedAnnotation]:
    """Dedup NormalizedAnnotations based on CanonicalSpan identity."""
    result: list[NormalizedAnnotation] = []
    seen: set[str] = set()

    for annotation in annotations:
        identity = normalized_span_identity(annotation)
        if identity in seen:
            log_drop(
                normalized_source_agent(annotation),
                annotation.type,
                annotation.sentence_id,
                normalized_anchor_text(annotation),
                "duplicate",
                "deduplication",
                drop_log,
            )
            continue
        seen.add(identity)
        result.append(annotation)

    return result


# ── Conflict resolution ───────────────────────────────────────────


def _build_overlap_clusters(
    items: list[NormalizedAnnotation],
) -> list[list[int]]:
    """Build overlap clusters using Union-Find."""
    n = len(items)
    if n <= 1:
        return [[i] for i in range(n)]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        i_spans = getattr(items[i], "spans", None) or []
        for j in range(i + 1, n):
            j_spans = getattr(items[j], "spans", None) or []
            if canonical_spans_overlap(i_spans, j_spans):
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return list(clusters.values())


def _normalized_conflict_resolution(
    annotations: list[NormalizedAnnotation],
    drop_log: list[DropLogEntry],
) -> list[NormalizedAnnotation]:
    """Resolve conflicts based on span overlap and priority."""
    grammar_annotations: list[NormalizedAnnotation] = []
    vocab_annotations: list[NormalizedAnnotation] = []
    for a in annotations:
        if a.type in {"grammar_note", "sentence_analysis"}:
            grammar_annotations.append(a)
        else:
            vocab_annotations.append(a)

    by_sentence: dict[str, list[NormalizedAnnotation]] = {}
    for a in vocab_annotations:
        by_sentence.setdefault(a.sentence_id, []).append(a)

    vocab_winners: list[NormalizedAnnotation] = []
    for sentence_id, items in by_sentence.items():
        clusters = _build_overlap_clusters(items)

        for member_indices in clusters:
            if len(member_indices) == 1:
                vocab_winners.append(items[member_indices[0]])
                continue
            member_indices.sort(
                key=lambda idx: (
                    PRIORITY_RANK.get(items[idx].type, 0),
                    normalized_span_identity(items[idx]),
                ),
                reverse=True,
            )
            vocab_winners.append(items[member_indices[0]])
            for idx in member_indices[1:]:
                loser = items[idx]
                log_drop(
                    normalized_source_agent(loser),
                    loser.type,
                    sentence_id,
                    normalized_anchor_text(loser),
                    "conflict_resolution",
                    "conflict_resolution",
                    drop_log,
                )

    # Drop vocab_highlights subsumed by phrase_gloss / context_gloss
    rich_annotations = [
        a for a in vocab_winners
        if a.type in {"context_gloss", "phrase_gloss"}
    ]
    rich_spans = [
        (a, getattr(a, "spans", []))
        for a in rich_annotations
        if getattr(a, "spans", None)
    ]

    survivors: list[NormalizedAnnotation] = []
    for a in vocab_winners:
        if a.type != "vocab_highlight":
            survivors.append(a)
            continue
        a_spans = getattr(a, "spans", [])
        if not a_spans:
            survivors.append(a)
            continue
        subsumer = None
        inner_span = a_spans[0]
        for rich_a, rich_span_group in rich_spans:
            if (
                rich_a.sentence_id == a.sentence_id
                and canonical_span_group_contains(rich_span_group, inner_span)
            ):
                subsumer = rich_a
                break
        if subsumer is None:
            survivors.append(a)
            continue
        log_drop(
            "vocabulary",
            a.type,
            a.sentence_id,
            normalized_anchor_text(a),
            f"subsumed_by_{subsumer.type}",
            "conflict_resolution",
            drop_log,
        )

    return [*grammar_annotations, *survivors]


# ── Density control ───────────────────────────────────────────────


def _normalized_density_control(
    annotations: list[NormalizedAnnotation],
    max_per_sentence: int,
    drop_log: list[DropLogEntry],
) -> list[NormalizedAnnotation]:
    """Density control based on canonical span count per sentence."""
    grouped: dict[str, list[NormalizedAnnotation]] = {}
    for a in annotations:
        grouped.setdefault(a.sentence_id, []).append(a)

    survivors: set[str] = set()
    for sentence_id, items in grouped.items():
        ranked = sorted(
            items,
            key=lambda item: (
                PRIORITY_RANK.get(item.type, 0),
                normalized_span_identity(item),
            ),
            reverse=True,
        )
        for item in ranked[:max_per_sentence]:
            survivors.add(normalized_span_identity(item))
        for item in ranked[max_per_sentence:]:
            log_drop(
                normalized_source_agent(item),
                item.type,
                sentence_id,
                normalized_anchor_text(item),
                f"density_exceeded_max_{max_per_sentence}",
                "density_control",
                drop_log,
            )

    return [
        a for a in annotations
        if normalized_span_identity(a) in survivors
    ]


# ── Public API ────────────────────────────────────────────────────


def postprocess_normalized_annotations(
    annotations: list[NormalizedAnnotation],
    drop_log: list[DropLogEntry],
    annotation_density: int,
) -> list[NormalizedAnnotation]:
    """Run full postprocess pipeline: dedup → conflict resolution → density control."""
    annotations = _normalized_dedup(annotations, drop_log)
    annotations = _normalized_conflict_resolution(annotations, drop_log)
    annotations = _normalized_density_control(
        annotations, annotation_density, drop_log,
    )
    return annotations


def build_canonical_stats(
    normalized_annotations: list[NormalizedAnnotation],
    canonical_drop_log: list[DropLogEntry],
) -> dict[str, object]:
    """Build canonical shadow path observation stats."""
    from collections import Counter

    canonical_counts = Counter(
        getattr(a, "type", str(type(a).__name__))
        for a in normalized_annotations
    )
    drop_by_type = Counter(
        getattr(e, "annotation_type", "") for e in canonical_drop_log
    )
    drop_by_reason = Counter(
        getattr(e, "drop_reason", "") for e in canonical_drop_log
    )

    total_span_count = 0
    for a in normalized_annotations:
        spans = getattr(a, "spans", None)
        if spans is not None:
            total_span_count += len(spans)

    CANONICAL_ANCHOR_DROP_REASONS: frozenset[str] = frozenset({
        "quote_not_found", "quote_ambiguous",
        "quote_out_of_order", "quote_too_short",
        "quote_boundary_violation",
        "sentence_id_invalid",
    })
    anchor_drops = [
        e for e in canonical_drop_log
        if getattr(e, "drop_reason", "") in CANONICAL_ANCHOR_DROP_REASONS
    ]
    by_type_and_reason = Counter(
        (getattr(e, "annotation_type", ""), getattr(e, "drop_reason", ""))
        for e in anchor_drops
    )

    return {
        "canonical_normalized_counts": dict(sorted(canonical_counts.items())),
        "canonical_drop_counts_by_type": dict(sorted(drop_by_type.items())),
        "canonical_drop_counts_by_reason": dict(sorted(drop_by_reason.items())),
        "canonical_span_count": total_span_count,
        "canonical_anchor_drop_summary": {
            "total_anchor_drops": len(anchor_drops),
            "by_annotation_type_and_reason": [
                {"annotation_type": at, "drop_reason": dr, "count": cnt}
                for (at, dr), cnt in by_type_and_reason.most_common()
            ],
        },
    }
