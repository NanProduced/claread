"""Deterministic gate for ``reader_parse_eval_artifact.v1`` (R1 split).

R1 changes from the previous monolithic ``parse_eval_gate.py``:

1. ``run_gate`` now receives a separate :class:`CanonicalTextEvidence`
   alongside the artifact. The evidence carries the **full canonical
   text** (which is intentionally NOT embedded in the artifact). The
   gate recomputes:

   - Full-text SHA-256 — MUST equal
     ``artifact.document.canonical_text_sha256``.
   - Full-text UTF-16 code-unit length — MUST equal
     ``artifact.document.canonical_text_length_utf16``.
   - Full-text plain char length — MUST equal
     ``artifact.document.canonical_text_length_chars``.
   - Full-text word count — MUST equal
     ``artifact.document.word_count``.
   - Per-navigation-unit FNV-1a32 hash over the canonical-text slice
     ``[base_start_utf16, base_end_utf16)`` — MUST equal
     ``unit.text_hash``.
   - Per-anchor-segment FNV-1a32 hash over the canonical-text slice
     ``[base_start_utf16, base_end_utf16)`` — MUST equal
     ``segment.text_hash``.

   This is the canonical-text cross-check that the previous gate
   could not perform because it did not have the full text.

2. **Zero-hash regression negatives.** A artifact whose
   ``canonical_text_sha256`` is all-zeros, or whose
   ``unit.text_hash`` / ``segment.text_hash`` is all-zeros, MUST
   fail the gate with a structured finding. The
   :class:`ZeroHashRegressionNegatives` helper produces such
   corrupted artifacts for the negative test path.

3. **Key-only forbidden marker scan.** Per the R1 spec
   ("forbidden 检查只针对 key / 非法 payload shape，不扫描用户文本
   或 notes"), the scan walks the JSON **keys** only (not free-form
   string values). This prevents false positives when a legitimate
   ``notes`` / ``unavailable_reason`` string happens to contain a
   forbidden substring like ``render_scene``.

4. The gate is a **pure function**: it never raises. All failures
   are returned as :class:`GateFinding` entries in the
   :class:`GateReport`. The caller decides whether to treat any
   failure as fatal.

The gate does NOT verify semantic quality. It is structural and
deterministic only, per the Task 5A spec.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from .constants import (
    ARTIFACT_SCHEMA_VERSION,
    FORBIDDEN_KEY_MARKERS,
    PRODUCER_SEMANTIC_VERSION,
    PRODUCER_VERSION,
    SHA256_LOWERCASE_HEX_RE,
    ZERO_FNV1A32,
    ZERO_SHA256,
)
from .fixture_builder import (
    FIXTURE_PRODUCER_MODULE,
    derive_artifact_id,
    fnv1a32_utf16,
    sha256_hex,
    utf16_code_unit_length,
)
from .reader_adapter import ADAPTER_PRODUCER_MODULE
from .schema import (
    AnchorMap,
    ArtifactProvenance,
    ArtifactRunnerProvenance,
    LegacyBaselineFreeze,
    ParseEvalArtifactV1,
    PublishedLayerFact,
    PublishedLayerSummary,
)

# ---------------------------------------------------------------------------
# R3: Known fixture-grade producer modules
# ---------------------------------------------------------------------------
#
# Any artifact claiming real execution (executor_mode="real" or
# is_fake=False on model/prompt provenance) MUST NOT come from these
# modules. They are hermetic test helpers that never call the real
# LLM; if they emit a "real" artifact it is a provenance forgery.
# ---------------------------------------------------------------------------
_KNOWN_FIXTURE_PRODUCER_MODULES: frozenset[str] = frozenset(
    {
        FIXTURE_PRODUCER_MODULE,
        "services/api/verification/reader_baseline/parse_eval/reader_snapshot_fixture.py",
    }
)


# ---------------------------------------------------------------------------
# Canonical-text evidence (separate from the artifact)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalTextEvidence:
    """The full canonical text + sidecar payloads, supplied to the gate separately.

    The artifact intentionally does NOT embed the full canonical text
    (only its SHA-256, length, and a 200-char preview). The gate
    needs the full text to:

    - recompute the full-text SHA-256 / UTF-16 length / char length /
      word count and verify they match the artifact's
      :class:`~.schema.DocumentIdentity` fields.
    - recompute per-unit / per-segment FNV-1a32 hashes over the
      canonical-text slices and verify they match the artifact's
      ``text_hash`` fields.

    R2 (P1-4): the evidence also carries ``sidecar_payloads`` — a
    mapping from ``sidecar_ref`` (the string stored in
    :class:`~.schema.PublishedLayerFact.sidecar_ref`) to the canonical
    JSON string of the sidecar content. The gate resolves each
    ``sidecar_ref`` layer via this mapping, recomputes the SHA-256
    over the payload, and verifies it equals the layer's
    ``sidecar_sha256``. Without this, ``sidecar_ref`` was just an
    opaque string with no verifiable content.

    The evidence is held in memory only; it is not serialised.
    """

    canonical_text: str
    sidecar_payloads: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_text, str):
            raise TypeError(
                "CanonicalTextEvidence.canonical_text must be a str"
            )
        if not self.canonical_text:
            raise ValueError(
                "CanonicalTextEvidence.canonical_text must be non-empty"
            )
        if not isinstance(self.sidecar_payloads, Mapping):
            raise TypeError(
                "CanonicalTextEvidence.sidecar_payloads must be a Mapping"
            )
        for k, v in self.sidecar_payloads.items():
            if not isinstance(k, str) or not k:
                raise TypeError(
                    "CanonicalTextEvidence.sidecar_payloads keys must be "
                    "non-empty str"
                )
            if not isinstance(v, str):
                raise TypeError(
                    "CanonicalTextEvidence.sidecar_payloads values must "
                    "be str (canonical JSON of the sidecar content)"
                )


# ---------------------------------------------------------------------------
# Gate report shapes
# ---------------------------------------------------------------------------

GateSeverity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class GateFinding:
    """A single gate finding.

    ``check`` is the stable check identifier (e.g.
    ``"canonical_text.sha256_mismatch"``). ``severity`` is always
    ``"error"`` for the V1 gate — warnings are reserved for future
    soft checks. ``detail`` is a human-readable explanation that
    MUST NOT embed sensitive payload content beyond the field path
    that failed.
    """

    check: str
    severity: GateSeverity
    detail: str

    def to_jsonable(self) -> dict[str, str]:
        return {
            "check": self.check,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    """Aggregate gate report.

    ``passed`` is true iff ``findings`` is empty. ``payload_sha256``
    is the SHA-256 of the canonical serialized artifact — two
    artifacts with the same ``payload_sha256`` are byte-identical.
    """

    artifact_id: str
    payload_sha256: str
    schema_version: str
    passed: bool
    findings: tuple[GateFinding, ...] = ()

    def to_jsonable(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "payload_sha256": self.payload_sha256,
            "schema_version": self.schema_version,
            "passed": self.passed,
            "findings": [f.to_jsonable() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Canonical serialization helpers
# ---------------------------------------------------------------------------


def serialize_artifact(artifact: ParseEvalArtifactV1) -> str:
    """Canonical JSON serialization of an artifact.

    Sorted keys, no ASCII escaping, no extra whitespace. Two
    consecutive productions of the same fixed input produce
    byte-identical output via this function.
    """
    payload = artifact.model_dump(mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def artifact_payload_sha256(artifact: ParseEvalArtifactV1) -> str:
    """SHA-256 over the canonical serialized artifact."""
    return sha256_hex(serialize_artifact(artifact))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utf16_slice(text: str, start_utf16: int, end_utf16: int) -> str:
    """Slice ``text`` by UTF-16 code-unit offsets.

    Mirrors how the Reader anchor offsets are defined: the offset
    pair ``[start, end)`` refers to UTF-16 code units, not Python
    code points. We encode to UTF-16-LE, slice the byte buffer
    (2 bytes per code unit), then decode back to str.
    """
    encoded = text.encode("utf-16-le")
    start_byte = start_utf16 * 2
    end_byte = end_utf16 * 2
    if start_byte < 0 or end_byte > len(encoded) or end_byte < start_byte:
        raise ValueError(
            f"UTF-16 slice [{start_utf16}, {end_utf16}) out of range "
            f"for text of UTF-16 length {len(encoded) // 2}"
        )
    return encoded[start_byte:end_byte].decode("utf-16-le")


def _walk_json_keys(value: Any, parent_key: str = "") -> list[str]:
    """Walk a JSON-shaped value and return all keys (recursively).

    Used by the forbidden-marker scan so we only inspect keys, not
    free-form string values. ``parent_key`` is the dotted path of
    the enclosing object — included so the finding detail can point
    at the offending field path.
    """
    keys: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            full = f"{parent_key}.{k}" if parent_key else k
            keys.append(full)
            keys.extend(_walk_json_keys(v, full))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            keys.extend(_walk_json_keys(item, f"{parent_key}[{i}]"))
    return keys


def _scan_forbidden_markers_keys_only(
    artifact: ParseEvalArtifactV1,
) -> list[GateFinding]:
    """Scan the serialized artifact JSON for forbidden key markers.

    Per the R1 spec, the scan inspects **keys only** — it does not
    scan free-form string values like ``notes`` or
    ``unavailable_reason``. This prevents false positives when a
    legitimate user-supplied string happens to contain a forbidden
    substring (e.g. ``render_scene``).
    """
    payload = artifact.model_dump(mode="json")
    all_keys = _walk_json_keys(payload)
    findings: list[GateFinding] = []
    # Match on the last path segment (the actual key name) so a path
    # like "sample.notes" does not falsely match "note" markers.
    for full_path in all_keys:
        last_segment = full_path.rsplit(".", 1)[-1]
        # Strip list-index suffix from the last segment
        if "[" in last_segment:
            last_segment = last_segment.split("[", 1)[0]
        for marker in FORBIDDEN_KEY_MARKERS:
            if marker in last_segment:
                findings.append(
                    GateFinding(
                        check="forbidden_markers.key_present",
                        severity="error",
                        detail=(
                            f"forbidden key marker {marker!r} found at "
                            f"JSON path {full_path!r}"
                        ),
                    )
                )
    return findings


def _scan_forbidden_payload_shape(
    artifact: ParseEvalArtifactV1,
) -> list[GateFinding]:
    """Defense-in-depth: scan serialised JSON for forbidden payload shapes.

    Even though the closed-schema Pydantic boundary prevents these
    from appearing as field names, the scan catches the case where a
    future producer path bypasses the model validator and embeds a
    forbidden shape as a nested value. We only flag the marker when
    it appears as a JSON key path component (not as a free-form
    string value).
    """
    return _scan_forbidden_markers_keys_only(artifact)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_schema_version(artifact: ParseEvalArtifactV1) -> list[GateFinding]:
    if artifact.schema_version != ARTIFACT_SCHEMA_VERSION:
        return [
            GateFinding(
                check="schema.version",
                severity="error",
                detail=(
                    f"schema_version mismatch: expected "
                    f"{ARTIFACT_SCHEMA_VERSION!r}, got "
                    f"{artifact.schema_version!r}"
                ),
            )
        ]
    return []


def _check_round_trip(artifact: ParseEvalArtifactV1) -> list[GateFinding]:
    """Verify parse → dump → parse produces an equal artifact."""
    payload = artifact.model_dump(mode="json")
    serialised = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    try:
        reparsed = ParseEvalArtifactV1.model_validate_json(serialised)
    except ValidationError as exc:
        return [
            GateFinding(
                check="schema.round_trip",
                severity="error",
                detail=(
                    "round-trip parse failed: "
                    + str(exc).splitlines()[0][:200]
                ),
            )
        ]
    if reparsed != artifact:
        return [
            GateFinding(
                check="schema.round_trip",
                severity="error",
                detail="round-trip artifact is not equal to the original",
            )
        ]
    return []


def _check_canonical_text_evidence(
    artifact: ParseEvalArtifactV1,
    evidence: CanonicalTextEvidence,
) -> list[GateFinding]:
    """Recompute canonical-text facts from the evidence and compare.

    This is the R1 cross-check the previous gate could not perform
    because it did not have the full canonical text. We verify:

    - SHA-256 over the full canonical text.
    - UTF-16 code-unit length.
    - Plain char length.
    - Word count (whitespace-split).

    The canonical text used by the producer runs through
    :func:`.fixture_builder.canonicalize_hermetic` (CRLF→LF, strip).
    The gate applies the same canonicalization to the evidence so
    the comparison is apples-to-apples. If the caller already
    canonicalized the evidence, the canonicalization is idempotent.
    """
    from .fixture_builder import canonicalize_hermetic

    findings: list[GateFinding] = []
    canonical = canonicalize_hermetic(evidence.canonical_text)
    if not canonical:
        findings.append(
            GateFinding(
                check="canonical_text.evidence_empty",
                severity="error",
                detail=(
                    "canonicalized canonical_text evidence is empty"
                ),
            )
        )
        return findings

    recomputed_sha = sha256_hex(canonical)
    recomputed_utf16 = utf16_code_unit_length(canonical)
    recomputed_chars = len(canonical)
    recomputed_words = len(canonical.split())

    doc = artifact.document
    if recomputed_sha != doc.canonical_text_sha256:
        findings.append(
            GateFinding(
                check="canonical_text.sha256_mismatch",
                severity="error",
                detail=(
                    "recomputed canonical_text_sha256 does not match "
                    "artifact.document.canonical_text_sha256"
                ),
            )
        )
    if recomputed_utf16 != doc.canonical_text_length_utf16:
        findings.append(
            GateFinding(
                check="canonical_text.utf16_length_mismatch",
                severity="error",
                detail=(
                    f"recomputed UTF-16 length {recomputed_utf16} does "
                    f"not match artifact value "
                    f"{doc.canonical_text_length_utf16}"
                ),
            )
        )
    if recomputed_chars != doc.canonical_text_length_chars:
        findings.append(
            GateFinding(
                check="canonical_text.char_length_mismatch",
                severity="error",
                detail=(
                    f"recomputed char length {recomputed_chars} does "
                    f"not match artifact value "
                    f"{doc.canonical_text_length_chars}"
                ),
            )
        )
    if recomputed_words != doc.word_count:
        findings.append(
            GateFinding(
                check="canonical_text.word_count_mismatch",
                severity="error",
                detail=(
                    f"recomputed word count {recomputed_words} does "
                    f"not match artifact value {doc.word_count}"
                ),
            )
        )

    # Zero-hash regression negatives: SHA-256 and FNV-1a32 fields
    # MUST NOT be all-zeros. This catches the case where a corrupted
    # artifact has a well-formed hash format but a zero value.
    if doc.canonical_text_sha256 == ZERO_SHA256:
        findings.append(
            GateFinding(
                check="canonical_text.zero_sha256",
                severity="error",
                detail=(
                    "canonical_text_sha256 is all-zeros — artifact is "
                    "corrupted or uninitialized"
                ),
            )
        )

    return findings


def _check_anchor_map_consistency(
    artifact: ParseEvalArtifactV1,
    evidence: CanonicalTextEvidence,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    anchor_map: AnchorMap = artifact.anchor_map
    canonical = evidence.canonical_text
    # Apply hermetic canonicalization so the slice offsets line up.
    from .fixture_builder import canonicalize_hermetic

    canonical = canonicalize_hermetic(canonical)

    # 1. Navigation units: order_index unique + ascending from 1;
    #    offsets non-overlapping in order.
    unit_order_indices = [u.order_index for u in anchor_map.navigation_units]
    expected_unit_order = list(range(1, len(unit_order_indices) + 1))
    if unit_order_indices != expected_unit_order:
        findings.append(
            GateFinding(
                check="anchor_map.unit_order",
                severity="error",
                detail=(
                    f"navigation units order_index sequence "
                    f"{unit_order_indices!r} is not 1..N"
                ),
            )
        )
    prev_end: int | None = None
    for unit in anchor_map.navigation_units:
        if prev_end is not None and unit.base_start_utf16 < prev_end:
            findings.append(
                GateFinding(
                    check="anchor_map.unit_overlap",
                    severity="error",
                    detail=(
                        f"unit {unit.unit_id!r} starts at "
                        f"{unit.base_start_utf16} before previous unit "
                        f"ends at {prev_end}"
                    ),
                )
            )
        prev_end = unit.base_end_utf16

    # 2. Recompute FNV-1a32 hash for each navigation unit's UTF-16
    #    slice and compare to the embedded text_hash. This is the
    #    R1 cross-check the previous gate could not perform.
    for unit in anchor_map.navigation_units:
        try:
            slice_text = _utf16_slice(
                canonical,
                unit.base_start_utf16,
                unit.base_end_utf16,
            )
        except ValueError as exc:
            findings.append(
                GateFinding(
                    check="anchor_map.unit_slice_out_of_range",
                    severity="error",
                    detail=(
                        f"unit {unit.unit_id!r} UTF-16 slice "
                        f"[{unit.base_start_utf16},"
                        f"{unit.base_end_utf16}) out of range: {exc}"
                    ),
                )
            )
            continue
        recomputed_hash = fnv1a32_utf16(slice_text)
        if recomputed_hash != unit.text_hash:
            findings.append(
                GateFinding(
                    check="anchor_map.unit_hash_mismatch",
                    severity="error",
                    detail=(
                        f"unit {unit.unit_id!r} recomputed text_hash "
                        f"{recomputed_hash!r} != embedded "
                        f"{unit.text_hash!r}"
                    ),
                )
            )
        # Zero-hash regression negative.
        if unit.text_hash == ZERO_FNV1A32:
            findings.append(
                GateFinding(
                    check="anchor_map.unit_zero_hash",
                    severity="error",
                    detail=(
                        f"unit {unit.unit_id!r} text_hash is all-zeros "
                        f"— artifact is corrupted or uninitialized"
                    ),
                )
            )

    # 3. Each anchor segment's unit_id references an existing unit.
    unit_ids = {u.unit_id for u in anchor_map.navigation_units}
    for segment in anchor_map.anchor_segments:
        if segment.unit_id not in unit_ids:
            findings.append(
                GateFinding(
                    check="anchor_map.segment_unit_reference",
                    severity="error",
                    detail=(
                        f"anchor segment {segment.anchor_segment_id!r} "
                        f"references unknown unit_id {segment.unit_id!r}"
                    ),
                )
            )

    # 4. Each anchor segment's base range lies within its declared
    #    unit's base range (cross-check against the navigation unit's
    #    stored base_start_utf16 / base_end_utf16).
    unit_ranges: dict[str, tuple[int, int]] = {
        u.unit_id: (u.base_start_utf16, u.base_end_utf16)
        for u in anchor_map.navigation_units
    }
    for segment in anchor_map.anchor_segments:
        unit_range = unit_ranges.get(segment.unit_id)
        if unit_range is None:
            continue  # already reported above
        unit_start, unit_end = unit_range
        if not (
            unit_start <= segment.base_start_utf16
            and segment.base_end_utf16 <= unit_end
        ):
            findings.append(
                GateFinding(
                    check="anchor_map.segment_within_unit",
                    severity="error",
                    detail=(
                        f"anchor segment {segment.anchor_segment_id!r} "
                        f"base range "
                        f"[{segment.base_start_utf16},"
                        f"{segment.base_end_utf16}] not within unit "
                        f"{segment.unit_id!r} range "
                        f"[{unit_start},{unit_end}]"
                    ),
                )
            )
        if segment.unit_start_utf16 != unit_start:
            findings.append(
                GateFinding(
                    check="anchor_map.unit_start_drift",
                    severity="error",
                    detail=(
                        f"anchor segment {segment.anchor_segment_id!r} "
                        f"unit_start_utf16={segment.unit_start_utf16} "
                        f"!= unit.base_start_utf16={unit_start}"
                    ),
                )
            )
        if segment.unit_end_utf16 != unit_end:
            findings.append(
                GateFinding(
                    check="anchor_map.unit_end_drift",
                    severity="error",
                    detail=(
                        f"anchor segment {segment.anchor_segment_id!r} "
                        f"unit_end_utf16={segment.unit_end_utf16} "
                        f"!= unit.base_end_utf16={unit_end}"
                    ),
                )
            )

    # 5. Recompute FNV-1a32 hash for each anchor segment's UTF-16
    #    slice and compare to the embedded text_hash.
    for segment in anchor_map.anchor_segments:
        try:
            slice_text = _utf16_slice(
                canonical,
                segment.base_start_utf16,
                segment.base_end_utf16,
            )
        except ValueError as exc:
            findings.append(
                GateFinding(
                    check="anchor_map.segment_slice_out_of_range",
                    severity="error",
                    detail=(
                        f"segment {segment.anchor_segment_id!r} UTF-16 "
                        f"slice [{segment.base_start_utf16},"
                        f"{segment.base_end_utf16}) out of range: {exc}"
                    ),
                )
            )
            continue
        recomputed_hash = fnv1a32_utf16(slice_text)
        if recomputed_hash != segment.text_hash:
            findings.append(
                GateFinding(
                    check="anchor_map.segment_hash_mismatch",
                    severity="error",
                    detail=(
                        f"segment {segment.anchor_segment_id!r} "
                        f"recomputed text_hash {recomputed_hash!r} != "
                        f"embedded {segment.text_hash!r}"
                    ),
                )
            )
        if segment.text_hash == ZERO_FNV1A32:
            findings.append(
                GateFinding(
                    check="anchor_map.segment_zero_hash",
                    severity="error",
                    detail=(
                        f"segment {segment.anchor_segment_id!r} "
                        f"text_hash is all-zeros — artifact is "
                        f"corrupted or uninitialized"
                    ),
                )
            )

    # 6. Anchor segments within the same unit are ordered by
    #    order_index ascending and non-overlapping.
    by_unit: dict[str, list[int]] = {}
    for idx, segment in enumerate(anchor_map.anchor_segments):
        by_unit.setdefault(segment.unit_id, []).append(idx)
    for unit_id, indices in by_unit.items():
        segments = [anchor_map.anchor_segments[i] for i in indices]
        order_indices = [s.order_index for s in segments]
        expected = list(range(1, len(order_indices) + 1))
        if order_indices != expected:
            findings.append(
                GateFinding(
                    check="anchor_map.segment_order",
                    severity="error",
                    detail=(
                        f"unit {unit_id!r} anchor segments order_index "
                        f"sequence {order_indices!r} is not 1..N"
                    ),
                )
            )
        prev_seg_end: int | None = None
        for seg in segments:
            if prev_seg_end is not None and seg.base_start_utf16 < prev_seg_end:
                findings.append(
                    GateFinding(
                        check="anchor_map.segment_overlap",
                        severity="error",
                        detail=(
                            f"unit {unit_id!r} anchor segment "
                            f"{seg.anchor_segment_id!r} starts at "
                            f"{seg.base_start_utf16} before previous "
                            f"segment ends at {prev_seg_end}"
                        ),
                    )
                )
            prev_seg_end = seg.base_end_utf16

    # 7. hash_algorithm fields must be the canonical literal.
    for unit in anchor_map.navigation_units:
        if unit.hash_algorithm != "fnv1a32-utf16":
            findings.append(
                GateFinding(
                    check="anchor_map.unit_hash_algorithm",
                    severity="error",
                    detail=(
                        f"unit {unit.unit_id!r} hash_algorithm="
                        f"{unit.hash_algorithm!r}, expected 'fnv1a32-utf16'"
                    ),
                )
            )
    for seg in anchor_map.anchor_segments:
        if seg.hash_algorithm != "fnv1a32-utf16":
            findings.append(
                GateFinding(
                    check="anchor_map.segment_hash_algorithm",
                    severity="error",
                    detail=(
                        f"segment {seg.anchor_segment_id!r} "
                        f"hash_algorithm={seg.hash_algorithm!r}, "
                        f"expected 'fnv1a32-utf16'"
                    ),
                )
            )
    return findings


def _check_published_layers(
    artifact: ParseEvalArtifactV1,
    evidence: CanonicalTextEvidence,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    summary: PublishedLayerSummary = artifact.published_layers

    # layer_counts keys must match the sum of per-layer counts by type
    recomputed_counts: dict[str, int] = {}
    for layer in summary.layers:
        recomputed_counts[layer.layer_type] = (
            recomputed_counts.get(layer.layer_type, 0) + 1
        )
    if recomputed_counts != dict(summary.layer_counts):
        findings.append(
            GateFinding(
                check="published_layers.count_mismatch",
                severity="error",
                detail=(
                    f"layer_counts {dict(summary.layer_counts)!r} does "
                    f"not match recomputed counts {recomputed_counts!r}"
                ),
            )
        )

    # No two layers share the same layer_id (duplicate publication).
    seen_layer_ids: set[str] = set()
    for layer in summary.layers:
        if layer.layer_id in seen_layer_ids:
            findings.append(
                GateFinding(
                    check="published_layers.duplicate_layer_id",
                    severity="error",
                    detail=(
                        f"duplicate layer_id {layer.layer_id!r} in "
                        f"published_layers"
                    ),
                )
            )
        seen_layer_ids.add(layer.layer_id)

    # R1: each non-empty layer MUST carry reviewable evidence
    # (normalized_output OR sidecar_ref). The Pydantic validator
    # already enforces this, but the gate re-checks in case a future
    # producer path bypasses the model validator.
    for layer in summary.layers:
        if layer.output_kind == "normalized_output":
            if layer.normalized_output is None:
                findings.append(
                    GateFinding(
                        check="published_layers.normalized_output_missing",
                        severity="error",
                        detail=(
                            f"layer {layer.layer_id!r}: output_kind="
                            f"'normalized_output' but normalized_output "
                            f"is None"
                        ),
                    )
                )
            elif layer.normalized_output_sha256 is None:
                findings.append(
                    GateFinding(
                        check="published_layers.normalized_sha_missing",
                        severity="error",
                        detail=(
                            f"layer {layer.layer_id!r}: output_kind="
                            f"'normalized_output' but "
                            f"normalized_output_sha256 is None"
                        ),
                    )
                )
            else:
                # Recompute the normalized_output_sha256 and verify.
                recomputed_sha = _recompute_normalized_output_sha(layer)
                if (
                    recomputed_sha is not None
                    and recomputed_sha != layer.normalized_output_sha256
                ):
                    findings.append(
                        GateFinding(
                            check="published_layers.normalized_sha_mismatch",
                            severity="error",
                            detail=(
                                f"layer {layer.layer_id!r}: recomputed "
                                f"normalized_output_sha256 "
                                f"{recomputed_sha!r} != embedded "
                                f"{layer.normalized_output_sha256!r}"
                            ),
                        ),
                    )
        elif layer.output_kind == "sidecar_ref":
            if not layer.sidecar_ref or not layer.sidecar_sha256:
                findings.append(
                    GateFinding(
                        check="published_layers.sidecar_missing",
                        severity="error",
                        detail=(
                            f"layer {layer.layer_id!r}: output_kind="
                            f"'sidecar_ref' requires sidecar_ref and "
                            f"sidecar_sha256"
                        ),
                    )
                )
            else:
                # R2 (P1-4): resolve sidecar_ref via the evidence's
                # sidecar_payloads mapping. The sidecar_ref MUST
                # resolve to a canonical JSON string whose SHA-256
                # equals sidecar_sha256. Without this check,
                # sidecar_ref is just an opaque string with no
                # verifiable content.
                payload = evidence.sidecar_payloads.get(
                    layer.sidecar_ref
                )
                if payload is None:
                    findings.append(
                        GateFinding(
                            check="published_layers.sidecar_payload_unresolved",
                            severity="error",
                            detail=(
                                f"layer {layer.layer_id!r}: sidecar_ref "
                                f"is not present in the evidence's "
                                f"sidecar_payloads mapping; the gate "
                                f"cannot verify sidecar_sha256 without "
                                f"the resolved payload"
                            ),
                        )
                    )
                else:
                    recomputed_sidecar_sha = sha256_hex(payload)
                    if recomputed_sidecar_sha != layer.sidecar_sha256:
                        findings.append(
                            GateFinding(
                                check="published_layers.sidecar_sha_mismatch",
                                severity="error",
                                detail=(
                                    f"layer {layer.layer_id!r}: recomputed "
                                    f"sidecar_sha256 "
                                    f"{recomputed_sidecar_sha!r} != "
                                    f"embedded {layer.sidecar_sha256!r}"
                                ),
                            )
                        )
        elif layer.output_kind == "empty":
            if layer.item_count != 0:
                findings.append(
                    GateFinding(
                        check="published_layers.empty_with_nonzero_count",
                        severity="error",
                        detail=(
                            f"layer {layer.layer_id!r}: output_kind="
                            f"'empty' but item_count={layer.item_count}"
                        ),
                    )
                )

    return findings


def _recompute_normalized_output_sha(layer: PublishedLayerFact) -> str | None:
    """Recompute the SHA-256 over the canonical JSON of normalized_output."""
    if layer.normalized_output is None:
        return None
    canonical_json = json.dumps(
        layer.normalized_output.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256_hex(canonical_json)


def _check_runner_provenance(artifact: ParseEvalArtifactV1) -> list[GateFinding]:
    findings: list[GateFinding] = []
    prov: ArtifactRunnerProvenance = artifact.runner_provenance
    if not prov.stopped_reason.strip():
        findings.append(
            GateFinding(
                check="runner_provenance.stopped_reason",
                severity="error",
                detail="runner_provenance.stopped_reason is empty",
            )
        )
    if not prov.runner_version.strip():
        findings.append(
            GateFinding(
                check="runner_provenance.runner_version",
                severity="error",
                detail="runner_provenance.runner_version is empty",
            )
        )
    if prov.completion_status == "incomplete" and not prov.completion_reasons:
        findings.append(
            GateFinding(
                check="runner_provenance.incomplete_reasons_missing",
                severity="error",
                detail=(
                    "completion_status='incomplete' requires at least "
                    "one completion_reasons entry"
                ),
            )
        )
    return findings


def _check_model_profile_provenance(
    artifact: ParseEvalArtifactV1,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    prov = artifact.model_profile_provenance
    if prov.is_fake:
        if (
            prov.model_provider is not None
            or prov.model_name is not None
            or prov.model_profile is not None
        ):
            findings.append(
                GateFinding(
                    check="model_profile_provenance.fake_with_real_fields",
                    severity="error",
                    detail=(
                        "is_fake=True but real model fields are populated"
                    ),
                )
            )
    else:
        if not (prov.model_provider and prov.model_name and prov.model_profile):
            findings.append(
                GateFinding(
                    check="model_profile_provenance.real_missing_fields",
                    severity="error",
                    detail=(
                        "is_fake=False requires model_provider, "
                        "model_name, and model_profile to be non-empty"
                    ),
                )
            )
    return findings


def _check_prompt_revision_provenance(
    artifact: ParseEvalArtifactV1,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    prov = artifact.prompt_revision_provenance
    if prov.is_fake:
        if prov.prompt_revision is not None:
            findings.append(
                GateFinding(
                    check="prompt_revision_provenance.fake_with_revision",
                    severity="error",
                    detail=(
                        "is_fake=True but prompt_revision is populated"
                    ),
                )
            )
    else:
        if not prov.prompt_revision:
            findings.append(
                GateFinding(
                    check="prompt_revision_provenance.real_missing_revision",
                    severity="error",
                    detail=(
                        "is_fake=False requires non-empty prompt_revision"
                    ),
                )
            )
    return findings


def _check_forbidden_markers(artifact: ParseEvalArtifactV1) -> list[GateFinding]:
    """Key-only forbidden marker scan (R1)."""
    return _scan_forbidden_markers_keys_only(artifact)


def _check_legacy_baseline(artifact: ParseEvalArtifactV1) -> list[GateFinding]:
    findings: list[GateFinding] = []
    freeze: LegacyBaselineFreeze = artifact.legacy_baseline
    if freeze.status == "frozen":
        if (
            freeze.input_canonical_text_sha256
            != artifact.document.canonical_text_sha256
        ):
            findings.append(
                GateFinding(
                    check="legacy_baseline.input_hash_mismatch",
                    severity="error",
                    detail=(
                        "frozen baseline input_canonical_text_sha256 "
                        "does not match document.canonical_text_sha256"
                    ),
                )
            )
        if not freeze.content_hash or not SHA256_LOWERCASE_HEX_RE.match(
            freeze.content_hash
        ):
            findings.append(
                GateFinding(
                    check="legacy_baseline.content_hash_invalid",
                    severity="error",
                    detail="frozen baseline content_hash is invalid",
                )
            )
        if not freeze.source_location or not freeze.source_location.strip():
            findings.append(
                GateFinding(
                    check="legacy_baseline.source_location_missing",
                    severity="error",
                    detail="frozen baseline source_location is empty",
                )
            )
        if not freeze.provenance or not freeze.provenance.strip():
            findings.append(
                GateFinding(
                    check="legacy_baseline.provenance_missing",
                    severity="error",
                    detail="frozen baseline provenance is empty",
                )
            )
    elif freeze.status == "unavailable":
        if (
            not freeze.unavailable_reason
            or not freeze.unavailable_reason.strip()
        ):
            findings.append(
                GateFinding(
                    check="legacy_baseline.unavailable_reason_missing",
                    severity="error",
                    detail=(
                        "unavailable baseline requires non-empty "
                        "unavailable_reason"
                    ),
                )
            )
    return findings


def _check_artifact_provenance(
    artifact: ParseEvalArtifactV1,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    prov: ArtifactProvenance = artifact.artifact_provenance
    if prov.producer_version != PRODUCER_VERSION:
        findings.append(
            GateFinding(
                check="artifact_provenance.producer_version",
                severity="error",
                detail=(
                    f"producer_version {prov.producer_version!r} does "
                    f"not match gate-expected {PRODUCER_VERSION!r}"
                ),
            )
        )
    if prov.producer_semantic_version != PRODUCER_SEMANTIC_VERSION:
        findings.append(
            GateFinding(
                check="artifact_provenance.producer_semantic_version",
                severity="error",
                detail=(
                    f"producer_semantic_version "
                    f"{prov.producer_semantic_version!r} does not match "
                    f"gate-expected {PRODUCER_SEMANTIC_VERSION!r}"
                ),
            )
        )
    if not prov.deterministic_clock_token.strip():
        findings.append(
            GateFinding(
                check="artifact_provenance.deterministic_clock_token",
                severity="error",
                detail="deterministic_clock_token is empty",
            )
        )
    if prov.forbidden_fields_present is not False:
        findings.append(
            GateFinding(
                check="artifact_provenance.forbidden_fields_present",
                severity="error",
                detail=(
                    f"forbidden_fields_present="
                    f"{prov.forbidden_fields_present!r}, expected False"
                ),
            )
        )
    # artifact_id_semantic_inputs MUST match the actual artifact fields.
    sem = prov.artifact_id_semantic_inputs
    if sem.canonical_text_sha256 != artifact.document.canonical_text_sha256:
        findings.append(
            GateFinding(
                check="artifact_provenance.semantic_inputs.sha256_drift",
                severity="error",
                detail=(
                    "artifact_id_semantic_inputs.canonical_text_sha256 "
                    "does not match document.canonical_text_sha256"
                ),
            )
        )
    if sem.schema_version != artifact.schema_version:
        findings.append(
            GateFinding(
                check="artifact_provenance.semantic_inputs.schema_drift",
                severity="error",
                detail=(
                    "artifact_id_semantic_inputs.schema_version does "
                    "not match top-level schema_version"
                ),
            )
        )
    if sem.producer_semantic_version != prov.producer_semantic_version:
        findings.append(
            GateFinding(
                check="artifact_provenance.semantic_inputs.producer_drift",
                severity="error",
                detail=(
                    "artifact_id_semantic_inputs.producer_semantic_version "
                    "does not match artifact_provenance.producer_semantic_version"
                ),
            )
        )
    if sem.source_id != artifact.source_provenance.source_id:
        findings.append(
            GateFinding(
                check="artifact_provenance.semantic_inputs.source_id_drift",
                severity="error",
                detail=(
                    "artifact_id_semantic_inputs.source_id does not "
                    "match source_provenance.source_id"
                ),
            )
        )
    if sem.deterministic_clock_token != prov.deterministic_clock_token:
        findings.append(
            GateFinding(
                check="artifact_provenance.semantic_inputs.clock_drift",
                severity="error",
                detail=(
                    "artifact_id_semantic_inputs.deterministic_clock_token "
                    "does not match artifact_provenance.deterministic_clock_token"
                ),
            )
        )
    # R2 (P1-1): recompute the artifact_id from the declared semantic
    # inputs and verify it matches the artifact's declared artifact_id.
    # Without this check the gate would only verify that the semantic
    # inputs are *internally consistent* with each other — a malicious
    # or buggy producer could declare a wrong artifact_id and still
    # pass as long as the semantic_inputs fields agreed with the
    # other artifact fields.
    recomputed_artifact_id = derive_artifact_id(
        canonical_text_sha256=sem.canonical_text_sha256,
        schema_version=sem.schema_version,
        producer_semantic_version=sem.producer_semantic_version,
        source_id=sem.source_id,
        deterministic_clock_token=sem.deterministic_clock_token,
    )
    if recomputed_artifact_id != artifact.artifact_id:
        findings.append(
            GateFinding(
                check="artifact_provenance.artifact_id_recompute_mismatch",
                severity="error",
                detail=(
                    "artifact_id does not equal the value recomputed "
                    "from artifact_id_semantic_inputs via "
                    "derive_artifact_id(canonical_text_sha256, "
                    "schema_version, producer_semantic_version, "
                    "source_id, deterministic_clock_token)"
                ),
            )
        )
    return findings


def _check_provenance_producer_policy(
    artifact: ParseEvalArtifactV1,
) -> list[GateFinding]:
    """R3: An artifact claiming real execution MUST NOT come from a
    fixture-grade producer module, and MUST come from the official
    adapter producer.

    This check closes the provenance loophole where a fixture /
    schema-only helper could construct a full ``ParseEvalArtifactV1``
    with ``executor_mode="real"`` and ``is_fake=False`` directly via
    Pydantic — bypassing the official adapter — and pass the gate.

    Policy:

    1. If the artifact claims real execution (``executor_mode="real"``
       OR ``model_profile_provenance.is_fake is False`` OR
       ``prompt_revision_provenance.is_fake is False``):

       a. If ``producer_module`` is a known fixture module → emit
          ``artifact_provenance.fixture_claims_real_execution``.
       b. If ``producer_module`` is not the official adapter module
          (and not a known fixture — covered by (a)) → emit
          ``artifact_provenance.real_artifact_from_non_adapter_producer``.

    2. If the artifact does NOT claim real execution, no check fires
       — fixture producers are allowed to emit ``executor_mode="fake"``
       artifacts.

    This check is structural and deterministic. It does not verify
    that the adapter actually produced the artifact (that would
    require runtime attestation, which is out of scope for V1). It
    only ensures that no known fixture module can claim real
    execution and pass the gate.
    """
    findings: list[GateFinding] = []

    claims_real = (
        artifact.runner_provenance.executor_mode == "real"
        or artifact.model_profile_provenance.is_fake is False
        or artifact.prompt_revision_provenance.is_fake is False
    )
    if not claims_real:
        return findings

    producer_module = artifact.artifact_provenance.producer_module

    if producer_module in _KNOWN_FIXTURE_PRODUCER_MODULES:
        findings.append(
            GateFinding(
                check="artifact_provenance.fixture_claims_real_execution",
                severity="error",
                detail=(
                    "artifact claims real execution (executor_mode='real' "
                    "or is_fake=False) but producer_module is a known "
                    "fixture-grade module; fixture producers cannot "
                    "produce real-execution artifacts"
                ),
            )
        )
    elif producer_module != ADAPTER_PRODUCER_MODULE:
        findings.append(
            GateFinding(
                check=(
                    "artifact_provenance.real_artifact_from_non_adapter_producer"
                ),
                severity="error",
                detail=(
                    "artifact claims real execution but producer_module "
                    "is not the official adapter; real artifacts must "
                    "come from the adapter producer"
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Top-level gate entry
# ---------------------------------------------------------------------------


def run_gate(
    artifact: ParseEvalArtifactV1,
    evidence: CanonicalTextEvidence,
) -> GateReport:
    """Run the full deterministic gate over an artifact + canonical-text evidence.

    Pure function: never raises. All failures are returned as
    :class:`GateFinding` entries in the report.

    Args:
        artifact: The :class:`ParseEvalArtifactV1` to validate.
        evidence: The full canonical text, supplied separately so the
            gate can recompute the SHA-256 / UTF-16 length / FNV-1a32
            hashes without embedding the full text in the artifact.
    """
    findings: list[GateFinding] = []
    findings.extend(_check_schema_version(artifact))
    findings.extend(_check_round_trip(artifact))
    findings.extend(_check_canonical_text_evidence(artifact, evidence))
    findings.extend(_check_anchor_map_consistency(artifact, evidence))
    findings.extend(_check_published_layers(artifact, evidence))
    findings.extend(_check_runner_provenance(artifact))
    findings.extend(_check_model_profile_provenance(artifact))
    findings.extend(_check_prompt_revision_provenance(artifact))
    findings.extend(_check_forbidden_markers(artifact))
    findings.extend(_check_artifact_provenance(artifact))
    findings.extend(_check_provenance_producer_policy(artifact))

    payload_sha = artifact_payload_sha256(artifact)
    passed = not findings
    return GateReport(
        artifact_id=artifact.artifact_id,
        payload_sha256=payload_sha,
        schema_version=artifact.schema_version,
        passed=passed,
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Two-run determinism check
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeterminismReport:
    """Report comparing two consecutive artifact productions."""

    source_id: str
    first_payload_sha256: str
    second_payload_sha256: str
    byte_identical: bool
    findings: tuple[GateFinding, ...] = ()

    def to_jsonable(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "first_payload_sha256": self.first_payload_sha256,
            "second_payload_sha256": self.second_payload_sha256,
            "byte_identical": self.byte_identical,
            "findings": [f.to_jsonable() for f in self.findings],
        }


def run_determinism_check(
    first: ParseEvalArtifactV1,
    second: ParseEvalArtifactV1,
) -> DeterminismReport:
    """Verify two artifact productions are byte-identical.

    The two artifacts MUST come from the same source_id and the same
    ``deterministic_clock_token``. The check is over the canonical
    serialized payload (sorted keys, no ASCII escaping, no trailing
    whitespace) so it is robust against dict ordering drift.
    """
    findings: list[GateFinding] = []
    if first.source_provenance.source_id != second.source_provenance.source_id:
        findings.append(
            GateFinding(
                check="determinism.source_id_mismatch",
                severity="error",
                detail=(
                    f"first.source_id={first.source_provenance.source_id!r} "
                    f"!= second.source_id="
                    f"{second.source_provenance.source_id!r}"
                ),
            )
        )
    first_sha = artifact_payload_sha256(first)
    second_sha = artifact_payload_sha256(second)
    byte_identical = first_sha == second_sha
    if not byte_identical:
        findings.append(
            GateFinding(
                check="determinism.payload_diverged",
                severity="error",
                detail=(
                    f"first payload sha256 {first_sha} != second "
                    f"payload sha256 {second_sha}"
                ),
            )
        )
    return DeterminismReport(
        source_id=first.source_provenance.source_id,
        first_payload_sha256=first_sha,
        second_payload_sha256=second_sha,
        byte_identical=byte_identical,
        findings=tuple(findings),
    )


# ---------------------------------------------------------------------------
# Zero-hash regression negative helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ZeroHashRegressionNegatives:
    """Helpers that produce corrupted artifacts for negative tests.

    Each helper returns a copy of the input artifact with one
    well-formed hash field replaced by all-zeros. The gate MUST
    reject every such artifact with a structured finding.

    These helpers exist so the gate test suite can verify the
    zero-hash regression negative path without hand-constructing a
    corrupted artifact from scratch.
    """

    @staticmethod
    def with_zero_canonical_text_sha256(
        artifact: ParseEvalArtifactV1,
    ) -> ParseEvalArtifactV1:
        """Return a copy with canonical_text_sha256 = all-zeros.

        Bypasses the Pydantic validator by constructing the model
        with ``model_construct`` then re-validating via
        ``model_validate`` on the corrupted dict. The gate (not the
        Pydantic boundary) is expected to catch this.
        """
        payload = artifact.model_dump(mode="json")
        payload["document"]["canonical_text_sha256"] = ZERO_SHA256
        # The artifact_id_semantic_inputs also references the sha,
        # so corrupt it too to keep the model self-consistent enough
        # to load (the gate will still catch the zero hash).
        payload["artifact_provenance"]["artifact_id_semantic_inputs"][
            "canonical_text_sha256"
        ] = ZERO_SHA256
        # Pydantic may still reject the zero sha via the field
        # validator (which only checks format, not value). The zero
        # string IS 64 lowercase hex chars, so format validation
        # passes — the value check happens only in the gate.
        return ParseEvalArtifactV1.model_validate(payload)

    @staticmethod
    def with_zero_unit_text_hash(
        artifact: ParseEvalArtifactV1,
        unit_index: int = 0,
    ) -> ParseEvalArtifactV1:
        """Return a copy with one navigation unit's text_hash = all-zeros."""
        payload = artifact.model_dump(mode="json")
        if not payload["anchor_map"]["navigation_units"]:
            raise ValueError("artifact has no navigation units")
        if unit_index >= len(payload["anchor_map"]["navigation_units"]):
            raise ValueError(
                f"unit_index {unit_index} out of range "
                f"(have {len(payload['anchor_map']['navigation_units'])} units)"
            )
        payload["anchor_map"]["navigation_units"][unit_index][
            "text_hash"
        ] = ZERO_FNV1A32
        return ParseEvalArtifactV1.model_validate(payload)

    @staticmethod
    def with_zero_segment_text_hash(
        artifact: ParseEvalArtifactV1,
        segment_index: int = 0,
    ) -> ParseEvalArtifactV1:
        """Return a copy with one anchor segment's text_hash = all-zeros."""
        payload = artifact.model_dump(mode="json")
        if not payload["anchor_map"]["anchor_segments"]:
            raise ValueError("artifact has no anchor segments")
        if segment_index >= len(payload["anchor_map"]["anchor_segments"]):
            raise ValueError(
                f"segment_index {segment_index} out of range "
                f"(have {len(payload['anchor_map']['anchor_segments'])} segments)"
            )
        payload["anchor_map"]["anchor_segments"][segment_index][
            "text_hash"
        ] = ZERO_FNV1A32
        return ParseEvalArtifactV1.model_validate(payload)

    @staticmethod
    def with_zero_artifact_id(
        artifact: ParseEvalArtifactV1,
    ) -> ParseEvalArtifactV1:
        """Return a copy with ``artifact_id`` set to all-zeros.

        R2 (P1-1) regression negative: the gate MUST recompute
        ``derive_artifact_id(...)`` from the declared semantic inputs
        and reject any artifact whose declared ``artifact_id`` does
        not match. Setting ``artifact_id`` to all-zeros (a valid
        64-hex string) verifies the gate catches this without
        relying on Pydantic format validation.
        """
        payload = artifact.model_dump(mode="json")
        payload["artifact_id"] = ZERO_SHA256
        return ParseEvalArtifactV1.model_validate(payload)

    @staticmethod
    def with_wrong_artifact_id(
        artifact: ParseEvalArtifactV1,
        *,
        wrong_id: str | None = None,
    ) -> ParseEvalArtifactV1:
        """Return a copy with ``artifact_id`` set to a wrong value.

        R2 (P1-1) regression negative: if ``wrong_id`` is None, the
        helper flips the last hex character of the real artifact_id
        so the result is a valid 64-hex string but does NOT match
        the value recomputed from the semantic inputs.
        """
        payload = artifact.model_dump(mode="json")
        if wrong_id is None:
            real = payload["artifact_id"]
            if not isinstance(real, str) or len(real) != 64:
                raise ValueError(
                    "artifact_id is not a 64-char string; cannot flip"
                )
            last_char = real[-1]
            # Flip the last hex char to a different hex char.
            flip_map = {
                "0": "1", "1": "0", "2": "3", "3": "2",
                "4": "5", "5": "4", "6": "7", "7": "6",
                "8": "9", "9": "8", "a": "b", "b": "a",
                "c": "d", "d": "c", "e": "f", "f": "e",
            }
            wrong_id = real[:-1] + flip_map.get(last_char, "0")
        payload["artifact_id"] = wrong_id
        return ParseEvalArtifactV1.model_validate(payload)


__all__ = [
    "CanonicalTextEvidence",
    "GateFinding",
    "GateReport",
    "GateSeverity",
    "DeterminismReport",
    "ZeroHashRegressionNegatives",
    "serialize_artifact",
    "artifact_payload_sha256",
    "run_gate",
    "run_determinism_check",
]
