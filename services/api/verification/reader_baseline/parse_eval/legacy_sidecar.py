"""Legacy baseline freeze helpers.

This module owns the **legacy-chain baseline freeze** logic for the
parse-eval artifact. R1 splits this out of :mod:`.fixture_builder` so
that:

- :mod:`.fixture_builder` stays a hermetic fixture-only producer for
  :class:`GoldenSample` inputs.
- :mod:`.reader_adapter` (the official API-side adapter) can call
  into a single legacy-sidecar helper for ``reader_record`` sources.
- Task 5B (recording a real frozen legacy baseline after a
  reviewer-approved real-LLM run) has a stable home for its
  ``build_legacy_baseline_frozen`` helper without polluting the
  fixture builder or the adapter.

Design boundaries (frozen by the Task 5A-R1 spec):

1. Task 5A is offline-only and refuses to call the real LLM. No
   qualifying already-existing frozen legacy output is checked into
   the repository, so the V1 freeze status is ``unavailable`` for
   every fixed sample / reader record. We never fabricate a frozen
   baseline.

2. The ``unavailable`` freeze carries a structured reason that
   explains *why* the legacy baseline could not be frozen, plus a
   ``visible_limitations`` list documenting the legacy-chain
   constraints. The reason text differs by source kind
   (``golden_sample`` vs ``reader_record``) so a reviewer can tell
   at a glance which path produced the freeze.

3. ``build_legacy_baseline_frozen`` is a stub for Task 5B. It
   validates its inputs and constructs a ``frozen``
   :class:`~.schema.LegacyBaselineFreeze`, but Task 5A does NOT call
   it — there is no qualifying already-existing legacy output to
   freeze. The stub is here so the API surface is stable when Task
   5B lands.

4. The module is hermetic for the offline path: no DB, no LLM, no
   spaCy, no ``app`` runtime import. It only depends on
   :mod:`.schema` for the :class:`LegacyBaselineFreeze` type.
"""

from __future__ import annotations

from .constants import SHA256_LOWERCASE_HEX_RE
from .schema import LegacyBaselineFreeze

# ---------------------------------------------------------------------------
# Unavailable baseline builders
# ---------------------------------------------------------------------------


def build_legacy_baseline_unavailable_for_record(
    source_id: str,
) -> LegacyBaselineFreeze:
    """Build the structured ``unavailable`` legacy baseline for a reader record.

    Used by :func:`.reader_adapter.build_artifact_from_snapshot` when
    the caller does not pass a pre-built ``legacy_baseline``. The
    reason text explicitly references the reader record path so a
    reviewer can tell the freeze apart from the fixture-grade one.
    """
    if not source_id or not source_id.strip():
        raise ValueError(
            "source_id must be a non-empty string when building a "
            "legacy baseline freeze for a reader record"
        )
    return LegacyBaselineFreeze(
        status="unavailable",
        unavailable_reason=(
            f"no qualifying already-existing legacy scene-render output "
            f"is checked into the repository for reader record "
            f"{source_id!r}; Task 5A is offline-only and refuses to "
            f"call the real LLM (env flag READER_BASELINE_REAL_LLM=1 "
            f"not set, and the legacy chain has no deterministic fake "
            f"executor). Use Task 5B to record a real frozen baseline "
            f"after a reviewer-approved real-LLM run."
        ),
        visible_limitations=[
            "legacy chain always calls a real LLM (no deterministic fake executor)",
            "legacy chain writes a scene-render payload, not enhancement_layers / reader_events",
            "legacy chain does not persist reading_records.reading_goal / reading_variant",
            "real-LLM runs require READER_BASELINE_REAL_LLM=1 and a configured model profile",
        ],
    )


def build_legacy_baseline_unavailable_for_sample(
    sample_id: str,
) -> LegacyBaselineFreeze:
    """Build the structured ``unavailable`` legacy baseline for a golden sample.

    This is the API-side equivalent of
    :func:`.fixture_builder.build_legacy_baseline_unavailable` —
    kept here so all legacy-sidecar logic lives in one module. The
    fixture builder re-exports its own thin wrapper for backwards
    compatibility with the existing fixture path.
    """
    if not sample_id or not sample_id.strip():
        raise ValueError(
            "sample_id must be a non-empty string when building a "
            "legacy baseline freeze for a golden sample"
        )
    return LegacyBaselineFreeze(
        status="unavailable",
        unavailable_reason=(
            f"no qualifying already-existing legacy scene-render output "
            f"is checked into the repository for sample {sample_id!r}; "
            f"Task 5A is offline-only and refuses to call the real LLM "
            f"(env flag READER_BASELINE_REAL_LLM=1 not set, and the "
            f"legacy chain has no deterministic fake executor). Use "
            f"Task 5B to record a real frozen baseline after a "
            f"reviewer-approved real-LLM run."
        ),
        visible_limitations=[
            "legacy chain always calls a real LLM (no deterministic fake executor)",
            "legacy chain writes a scene-render payload, not enhancement_layers / reader_events",
            "legacy chain does not persist reading_records.reading_goal / reading_variant",
            "real-LLM runs require READER_BASELINE_REAL_LLM=1 and a configured model profile",
        ],
    )


# ---------------------------------------------------------------------------
# Frozen baseline builder (Task 5B stub — NOT called by Task 5A)
# ---------------------------------------------------------------------------


def build_legacy_baseline_frozen(
    *,
    canonical_text_sha256: str,
    capability_code: str,
    chain_name: str,
    frozen_output_keys: list[str],
    layer_counts: dict[str, int],
    content_hash: str,
    source_location: str,
    provenance: str,
) -> LegacyBaselineFreeze:
    """Build a ``frozen`` :class:`LegacyBaselineFreeze` (Task 5B stub).

    Task 5A does NOT call this function — there is no qualifying
    already-existing legacy output to freeze. The stub is here so
    Task 5B has a stable entry point: it records a reviewer-approved
    real-LLM legacy output under
    ``verification/reader_baseline/legacy_frozen/`` and points
    ``source_location`` at it.

    The function validates the input shape but does NOT call the LLM,
    does NOT touch the DB, and does NOT verify the frozen file
    actually exists (that is Task 5B's responsibility).

    Args:
        canonical_text_sha256: MUST equal the artifact's
            ``document.canonical_text_sha256``. The top-level
            :class:`~.schema.ParseEvalArtifactV1` validator enforces
            this.
        capability_code: Stable legacy capability code (e.g.
            ``analysis_full``).
        chain_name: Stable legacy chain name (e.g.
            ``article_analysis``).
        frozen_output_keys: Sorted, de-duplicated legacy output keys
            that were frozen.
        layer_counts: Per-key counts of the frozen legacy output.
        content_hash: SHA-256 over a canonical summary of the frozen
            legacy output.
        source_location: Stable file path or origin marker.
        provenance: Free-text provenance describing how the frozen
            output was produced (reviewer, date, model profile).
    """
    if not SHA256_LOWERCASE_HEX_RE.match(canonical_text_sha256):
        raise ValueError(
            "canonical_text_sha256 must be 64 lowercase hex chars"
        )
    if not capability_code.strip():
        raise ValueError("capability_code must be non-empty")
    if not chain_name.strip():
        raise ValueError("chain_name must be non-empty")
    if not SHA256_LOWERCASE_HEX_RE.match(content_hash):
        raise ValueError(
            "content_hash must be 64 lowercase hex chars"
        )
    if not source_location.strip():
        raise ValueError("source_location must be non-empty")
    if not provenance.strip():
        raise ValueError("provenance must be non-empty")
    if not frozen_output_keys:
        raise ValueError("frozen_output_keys must be non-empty")
    if not layer_counts:
        raise ValueError("layer_counts must be non-empty")

    # De-duplicate + sort frozen_output_keys for byte-stability.
    deduped_keys = sorted(set(frozen_output_keys))
    # Sort layer_counts by key for byte-stability.
    sorted_counts = {k: int(v) for k, v in sorted(layer_counts.items())}

    return LegacyBaselineFreeze(
        status="frozen",
        capability_code=capability_code,
        chain_name=chain_name,
        input_canonical_text_sha256=canonical_text_sha256,
        frozen_output_keys=deduped_keys,
        layer_counts=sorted_counts,
        content_hash=content_hash,
        source_location=source_location,
        provenance=provenance,
    )


__all__ = [
    "build_legacy_baseline_unavailable_for_record",
    "build_legacy_baseline_unavailable_for_sample",
    "build_legacy_baseline_frozen",
]
