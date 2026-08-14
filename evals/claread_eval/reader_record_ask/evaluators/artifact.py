"""Serializable raw artifact view for the reader-record-ask evaluators.

The evaluators operate on :class:`RawArtifact` — a pure-data, serializable
projection of :class:`ReadingRecordAskRunResult` plus
:class:`FinalizedAskResult` / :class:`BaselineAgentContext`. Evaluators must
NOT import runtime classes; they consume this artifact only.

Field names follow the spec (``.trae/specs/reader-record-ask-r4-a3-
correctness-eval/spec.md`` — Requirement: 11 维确定性 evaluator + 真实模型运行
策略 + 报告脱敏与可聚合).

Strict contract (artifact audit boundary):
Audit-critical fields use ``Strict*`` types + format validators so Pydantic
coercion CANNOT turn ``run_index=True`` into ``1``, ``budget_exhausted="false"``
into ``False``, or accept malformed dataset identity SHAs. This is the fail-
closed boundary for the artifact-load audit seam
(:func:`claread_eval.reader_record_ask.artifact_loading.load_artifacts_with_audit`).
Non-audit display fields (model_short_name, final_text, error, etc.) remain
lenient to avoid breaking backwards compatibility with non-audited consumers.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from claread_eval.reader_record_ask.errors import SafeErrorCode

#: Strict SHA-256 lowercase hex pattern (exactly 64 lowercase hex chars).
#: Used to validate ``dataset_content_sha256``. Mirrors the same regex used
#: by the manifest schema validator — artifact-side identity MUST be as
#: strict as manifest-side identity so the two can be compared reliably.
_SHA256_LOWERCASE_HEX_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# Explicit instrumentation lifecycle
# ---------------------------------------------------------------------------
# Distinguishes 4 mutually-exclusive states WITHOUT inspecting error text
# or finalized_reason:
#
#   1. legacy artifact:
#        version=None, capture_status=None
#        → indeterminate_requires_new_artifact (replay only)
#        → blocked_incomplete_real_model_run (authoritative aggregate)
#
#   2. new successful artifact (model ran, baseline captured):
#        version="reader_record_ask_model_context_v1",
#        capture_status="captured"
#        → MUST have legal fingerprint + model_context_handle_ids +
#          complete observations for required source facts
#
#   3. baseline unavailable (model ran but baseline assembly yielded no
#      model_context_chunks — e.g. envelope_mismatch / no_units):
#        version="reader_record_ask_model_context_v1",
#        capture_status="unavailable"
#        → fingerprint=None, handle_ids=[], observations=[]
#        → instrumentation/run incomplete blocker (NOT model failure)
#
#   4. runtime exception (run_reading_record_ask raised before/independent
#      of baseline assembly, or baseline assembler itself failed):
#        version="reader_record_ask_model_context_v1",
#        capture_status="failed"
#        → fingerprint=None, handle_ids=[], observations=[]
#        → instrumentation/run incomplete blocker (NOT model failure)
#
# The version literal is the migration marker — only artifacts produced
# Later artifacts carry it. ``None`` always means legacy.
# The capture_status literal distinguishes the three new-artifact states
# without parsing error text or finalized_reason.
# ---------------------------------------------------------------------------
MODEL_CONTEXT_INSTRUMENTATION_VERSION_LITERAL = Literal[
    "reader_record_ask_model_context_v1",
]
MODEL_CONTEXT_CAPTURE_STATUS_LITERAL = Literal[
    "captured",
    "unavailable",
    "failed",
]

# ---------------------------------------------------------------------------
# Evaluator-consumed enum literals
# ---------------------------------------------------------------------------
# These mirror the production-side Literal types so a typo'd
# kind/provenance/status is rejected at the artifact load boundary,
# not silently passed to the evaluator. Sources:
# - FinalizeStatus: services/api/app/services/reader_record_ask/finalizer.py:30-35
# - ResponseKind:   services/api/app/services/reader_record_ask/finalizer.py:39
# - EvidenceKind:   services/api/app/services/reader_record_ask/evidence.py:32-38
# - EvidenceOrigin: services/api/app/services/reader_record_ask/evidence.py:55-60
# - BaselineStatus: services/api/app/services/reader_record_ask/baseline_context.py:184-189
# ---------------------------------------------------------------------------
EVIDENCE_KIND_LITERAL = Literal[
    "initial_anchor",
    "read_range",
    "search_hit",
    "observation",
    "article_seed",
]
EVIDENCE_PROVENANCE_LITERAL = Literal[
    "initial_anchor",
    "read_range",
    "search_current_article",
    "baseline_context",
]
FINALIZED_STATUS_LITERAL = Literal[
    "ok",
    "context_stale",
    "invalid_citations",
    "unavailable",
]
RESPONSE_KIND_LITERAL = Literal[
    "grounded_answer",
    "clarification",
    "unavailable",
]
BASELINE_STATUS_LITERAL = Literal[
    "injected",
    "document_scope_unavailable",
    "envelope_mismatch",
    "no_units",
]

# ---------------------------------------------------------------------------
# Legal (kind, provenance) cross-field invariant
# ---------------------------------------------------------------------------
# Mirrors the production contract
# ``LEGAL_EVIDENCE_KIND_SOURCE`` at
# ``services/api/app/services/reader_record_ask/evidence.py:64-77``.
#
# This mapping is duplicated LOCALLY in evals (not reverse-imported from
# services/api) to keep the evaluator-input boundary self-contained and
# avoid any runtime dependency on the production package. The two copies
# MUST stay semantically identical; any change to the production contract
# requires a corresponding change here and a new test in
# ``test_reader_record_ask_evidence_contract.py``.
#
# Semantics:
# - ``initial_anchor`` evidence is produced only by the initial_anchor tool
#   (user's first anchor selection).
# - ``read_range`` evidence is produced only by the read_range tool.
# - ``search_hit`` evidence is produced only by search_current_article.
# - ``observation`` is a generic kind that may be produced by any of the
#   three first-wave sources (initial_anchor / read_range /
#   search_current_article) but NEVER by baseline_context (the full-article
#   baseline seed is exclusively ``article_seed``).
# - ``article_seed`` is produced exclusively by the baseline context
#   assembler (``baseline_context``) — it must NOT carry initial_anchor /
#   read_range / search_current_article because the full-article baseline
#   is not a user selection, a tool-driven read range, or a RAG search hit.
#
# Total: 5 kinds × 4 provenances = 20 cartesian combinations, of which
# exactly 7 are legal. The remaining 13 are contract corruption and MUST
# be rejected at the artifact load boundary (``invalid_schema_count``),
# never passed to the evaluator.
# ---------------------------------------------------------------------------
LEGAL_EVIDENCE_KIND_PROVENANCE: dict[str, frozenset[str]] = {
    "initial_anchor": frozenset({"initial_anchor"}),
    "read_range": frozenset({"read_range"}),
    "search_hit": frozenset({"search_current_article"}),
    "observation": frozenset(
        {"initial_anchor", "read_range", "search_current_article"}
    ),
    "article_seed": frozenset({"baseline_context"}),
}


class RawEvidenceObservation(BaseModel):
    """Serializable projection of :class:`ServerEvidenceObservation`.

    ``handle_id`` / ``kind`` / ``provenance`` come from the nested
    ``ServerEvidenceHandle``; ``snippet`` is the already-truncated public
    snippet (≤2000 chars) and is the only article content the evaluator
    inspects — never the full article text.

    Strict contract (evaluator-input boundary):
    All four fields are strict. ``handle_id`` rejects bool/int/float AND
    empty/whitespace strings (the evaluator's
    ``{ev.handle_id for ev in ...}`` set-membership check would silently
    produce wrong results on an empty handle). ``kind`` / ``provenance``
    are :data:`Literal` enums mirroring the production
    :data:`EvidenceKind` / :data:`EvidenceOrigin` types — a typo like
    ``"search_hits"`` is rejected at the artifact load boundary instead
    of silently bypassing the ``evidence_minimality`` soft-failure
    check (``all(k == "search_hit" for k in kinds)``). ``snippet`` is
    :class:`StrictStr` (rejects bool/int/float) but allows empty
    string — an empty snippet is a valid server-side truncation result.
    """

    model_config = ConfigDict(extra="forbid")

    handle_id: StrictStr
    kind: EVIDENCE_KIND_LITERAL
    snippet: StrictStr = ""
    provenance: EVIDENCE_PROVENANCE_LITERAL

    @field_validator("handle_id")
    @classmethod
    def _non_empty_handle_id(cls, v: str) -> str:
        """Reject empty or whitespace-only handle ids.

        ``StrictStr`` rejects bool/int/float, but ``""`` and ``"   "``
        are valid ``str`` instances that would silently pass and then
        corrupt the ``evidence_minimality`` set-membership check.
        """
        if not v or not v.strip():
            raise ValueError("handle_id must be a non-empty, non-whitespace string")
        return v

    @model_validator(mode="after")
    def _validate_kind_provenance_pair(self) -> RawEvidenceObservation:
        """Reject illegal (kind, provenance) combinations.

        Cross-field invariant: each ``kind`` has a strictly
        enumerated set of legal ``provenance`` values (see
        :data:`LEGAL_EVIDENCE_KIND_PROVENANCE`). A mismatch is contract
        corruption — e.g. ``kind=article_seed + provenance=search_current_article``
        would let search evidence masquerade as baseline seed and bypass
        the ``evidence_minimality`` soft-failure check (``all(k == "search_hit")``).

        This validator runs AFTER the per-field Literal validators, so
        both ``kind`` and ``provenance`` are already known to be legal
        enum values when this check executes. The cross-field check is
        the only thing that rejects combinations like
        ``observation + baseline_context`` (both individually legal).

        The error message is carefully scoped: it contains ONLY the
        ``kind`` and ``provenance`` enum values and the allowed set —
        never ``handle_id``, ``snippet``, or any other field content
        that could leak sensitive article text.
        """
        allowed = LEGAL_EVIDENCE_KIND_PROVENANCE.get(self.kind)
        # ``allowed`` is always non-None here because the ``kind`` Literal
        # validator already rejected unknown values, but we keep the
        # defensive check for forward-compatibility.
        if allowed is None or self.provenance not in allowed:
            raise ValueError(
                f"illegal evidence kind/provenance combination: "
                f"kind={self.kind!r}, provenance={self.provenance!r}; "
                f"allowed provenances for this kind: {sorted(allowed) if allowed else []}"
            )
        return self


class RawUsage(BaseModel):
    """Agent usage telemetry projected from ``agent_output.usage()``.

    Strict contract: counters are :class:`StrictInt` (rejects bool /
    str / float coercion) and validated non-negative when present. The
    previous lenient ``int`` annotation accepted ``True`` → ``1``,
    ``"3"`` → ``3``, ``1.0`` → ``1`` silently, which would let a
    corrupted JSON file masquerade as a well-formed artifact.
    """

    model_config = ConfigDict(extra="forbid")

    requests: StrictInt | None = None
    input_tokens: StrictInt | None = None
    output_tokens: StrictInt | None = None

    @field_validator("requests", "input_tokens", "output_tokens")
    @classmethod
    def _non_negative_or_none(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("usage counter must be non-negative")
        return v


class ModelContextSupportObservation(BaseModel):
    """Typed per-fact model-context support observation.

    Computed at harness run time against the **actual model-visible
    context** — i.e. ``result.baseline_context.model_context_chunks`` —
    NOT ``document_access.snapshot.units`` and NOT the truncated public
    snippet. The model sees only the chunks that survived the
    short/medium-long baseline assembler (raw 8000 / serialized 16000 /
    16-chunk cap), so the support observation MUST be computed against
    those exact chunks.

    Contract properties:

    - **Reads actual ``model_context_chunks``.** The harness passes the
      real :class:`ModelContextChunk` tuple from
      ``ReadingRecordAskRunResult.baseline_context``. For each atomic
      fact, support is computed by matching ``source_aliases`` against
      the concatenated text of those chunks (NOT snapshot.units, NOT
      the 500/2000-char public snippet).
    - **Does NOT persist chunk text / article body / alias hit
      fragments.** Only ``fact_id``, ``support`` (bool),
      ``model_context_fingerprint`` (SHA-256 over canonical framing of
      ``model_context_chunks``), and ``supporting_handle_ids`` (the
      de-duplicated, order-preserving list of chunk handle_ids whose
      text actually contained an alias hit) are stored.
    - **Does NOT trust the case author's declaration.** ``support=True``
      means at least one real model-visible chunk hit an alias. A case
      author cannot declare a non-existent alias and have it auto-pass.
    - **Proves cited handle corresponds to model-visible baseline.**
      ``supporting_handle_ids`` are the chunk handle_ids that
      contributed the support. The evaluator requires at least one of
      them to appear in ``artifact.cited_evidence_handles`` for the
      fact to be grounded — this is the authoritative
      fact→chunk→handle→citation binding. The previous contract bound
      every fact to ``cited_handles[0]`` which silently mis-bound
      facts supported by the second chunk to the first chunk's handle.
    - **Fingerprint is artifact-internal integrity binding.**
      ``model_context_fingerprint`` MUST equal
      ``RawArtifact.model_context_fingerprint`` (the canonical framing
      of all chunks the model saw). A mismatch means the observation
      was computed against a different baseline than the artifact
      records — the observation is not authoritative for this artifact.
      This is NOT an independent security proof; it is an internal
      consistency check that closes the ``expected_baseline_fingerprint
      =None`` bypass.
    - **Legacy artifacts missing this field →
      ``coverage_incomplete``.** The evaluator surfaces this as
      ``indeterminate_requires_new_artifact`` (not auto-pass, not
      auto-fail). Old artifacts cannot be authoritatively re-evaluated
      under the new contract; they require a new run.

    Strict contract: ``fact_id`` is :class:`StrictStr` with a
    non-empty validator. ``support`` is :class:`StrictBool`.
    ``model_context_fingerprint`` is :class:`StrictStr` with the same
    64-lowercase-hex SHA-256 format validator as
    ``RawArtifact.dataset_content_sha256`` so the two can be compared
    byte-for-byte. ``supporting_handle_ids`` is a list of
    :class:`StrictStr` (each non-empty, no whitespace-only entries);
    it MUST be empty when ``support=False`` and SHOULD be non-empty
    when ``support=True`` (an empty list with ``support=True`` is
    treated by the evaluator as ``instrumentation_incomplete`` —
    fail-closed, not auto-pass).
    """

    model_config = ConfigDict(extra="forbid")

    fact_id: StrictStr
    support: StrictBool
    model_context_fingerprint: StrictStr
    supporting_handle_ids: list[StrictStr] = Field(default_factory=list)

    @field_validator("fact_id")
    @classmethod
    def _non_empty_fact_id(cls, v: str) -> str:
        """Reject empty or whitespace-only fact ids.

        ``StrictStr`` rejects bool/int/float, but ``""`` would silently
        pass and then fail to match any atomic_fact in the evaluator's
        ``{obs.fact_id: obs for obs in ...}`` lookup, causing the fact
        to be incorrectly marked coverage_incomplete.
        """
        if not v or not v.strip():
            raise ValueError("fact_id must be a non-empty, non-whitespace string")
        return v

    @field_validator("model_context_fingerprint")
    @classmethod
    def _sha256_lowercase_hex(cls, v: str) -> str:
        """Require exactly 64 lowercase hex chars.

        The fingerprint MUST be byte-for-byte comparable to
        ``RawArtifact.model_context_fingerprint``. Uppercase hex, short
        strings, and non-hex characters are all rejected.
        """
        if not _SHA256_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "model_context_fingerprint must be 64 lowercase hex chars (SHA-256)"
            )
        return v

    @field_validator("supporting_handle_ids")
    @classmethod
    def _non_empty_handle_ids(cls, v: list[str]) -> list[str]:
        """Reject empty/whitespace handle_ids in the list.

        Each entry must be a non-empty, non-whitespace string. The list
        itself may be empty (when ``support=False`` or when the harness
        cannot determine which chunk supported the fact — the latter is
        treated by the evaluator as ``instrumentation_incomplete``).
        """
        for h in v:
            if not h or not h.strip():
                raise ValueError(
                    "supporting_handle_ids entries must be non-empty, "
                    "non-whitespace strings"
                )
        return v


class RawArtifact(BaseModel):
    """Evaluator input — pure data view of one independent agent run.

    Built by the harness from
    :class:`ReadingRecordAskRunResult`. Contains no runtime object
    references; safe to serialize to JSON and persist under the
    :class:`RunSessionLayout`-managed local ignored run directory.

    Strict contract (artifact audit boundary):

    Audit-critical fields use ``Strict*`` types so Pydantic coercion
    CANNOT silently turn malformed JSON into a valid-looking artifact:

    - ``case_id`` / ``run_id``: ``StrictStr`` (rejects bool / int / float)
      + non-empty / non-whitespace validator. Empty or whitespace-only
      strings are rejected (previously ``""`` would pass and then fail
      silently downstream in ``cases_by_id.get(artifact.case_id)``).
    - ``run_index``: ``StrictInt`` (rejects bool / str / float) +
      non-negative validator. ``True`` / ``"1"`` / ``1.0`` are all
      rejected — previously Pydantic coerced them to ``1``, which could
      then satisfy a manifest's ``planned_run_index=1`` and bypass the
      coverage audit (the minimal reproduction bug).
    - ``thinking_enabled`` / ``budget_exhausted``: ``StrictBool`` (rejects
      int / str). ``"false"`` / ``0`` / ``1`` are all rejected —
      previously Pydantic coerced them to ``False`` / ``False`` / ``True``.
    - ``executed_requests`` / ``executed_tokens``: ``StrictInt | None``
      with non-negative validator. Rejects bool / str / float / negative.
    - ``dataset_id`` / ``dataset_schema_version``: ``StrictStr | None``
      with non-empty validator when present. Empty / whitespace strings
      rejected.
    - ``dataset_content_sha256``: ``StrictStr | None`` with 64-lowercase-
      hex format validator when present. ``"abc123"`` / ``"g"*64`` /
      uppercase hex all rejected — the SHA must be comparable to the
      manifest's SHA byte-for-byte.

    Strict contract (evaluator-input boundary):

    Evaluator-scored structural fields are ALSO strict, because they
    directly drive evaluator verdicts (see Evaluator-consumed Field
    Matrix in the delivery report). Coercion here would let a corrupted
    JSON file masquerade as a valid artifact and silently flip a
    ``passed=False`` to ``passed=True`` (or vice versa):

    - ``read_range_calls`` / ``search_current_article_calls``:
      :class:`StrictInt` (rejects bool/str/float) + non-negative
      validator. ``True`` / ``"1"`` / ``1.0`` are all rejected —
      previously Pydantic coerced them to ``1``, which would satisfy
      the ``tool_decision`` evaluator's "tool calls were made" branch
      and bypass a ``required``-policy failure.
    - ``baseline_is_complete`` / ``baseline_is_injected``:
      :class:`StrictBool` | None (rejects int/str). ``"false"`` / ``0``
      / ``1`` are all rejected — previously Pydantic coerced them to
      ``False`` / ``False`` / ``True``, which would flip the
      ``evidence_minimality`` soft-failure check
      (``if artifact.baseline_is_complete is True``) and the
      ``tool_decision`` baseline-expansion note.
    - ``latency_seconds``: ``float | None`` with a ``mode="before"``
      validator that rejects bool / str / NaN / Infinity / negative.
      JSON integer is explicitly allowed (normalized to float) as a
      legitimate seconds representation. Previously Pydantic coerced
      ``True`` → ``1.0`` and ``"1"`` → ``1.0``, which would satisfy
      the ``usage_observability`` ``latency_seconds > 0`` check.
    - ``finalized_status`` / ``response_kind`` / ``baseline_status``:
      :data:`Literal` enums mirroring the production
      :data:`FinalizeStatus` / :data:`ResponseKind` /
      :data:`BaselineStatus` types. A typo like ``"ok "`` (trailing
      space) or ``"completed"`` is rejected at the artifact load
      boundary instead of silently bypassing the
      ``answer_success`` ``finalized_status != "ok"`` check.
    - ``cited_evidence_handles``: ``list[StrictStr]`` with a
      non-empty validator on each entry. Rejects bool/int/float
      elements AND empty/whitespace strings. Duplicate handles and
      unknown handles are PRESERVED for the ``evidence_minimality``
      evaluator to fail (content-quality issue, not schema issue).
    - ``resolved_evidence`` / ``all_evidence_observations``: strict
      nested :class:`RawEvidenceObservation` DTOs (see that class's
      docstring for the strict contract).

    Non-audit display fields (``model_short_name``, ``final_text``,
    ``error``, ``model_route``, etc.) remain lenient — they are
    consumed only by evaluators for content-quality checks (not
    structural-equality / structural-count checks) and by report
    rendering.

    The strict contract is enforced at the model level (NOT in a test
    helper or a loader-only pre-check) so every consumer that
    ``RawArtifact.model_validate()`` an untrusted payload gets the same
    fail-closed behavior. The production artifact-load seam
    (:func:`load_artifacts_with_audit`) relies on this model-level
    validation as its single parsing truth — it does NOT add a parallel
    validation layer.
    """

    model_config = ConfigDict(extra="forbid")

    # ------------------------------------------------------------------
    # Audit-critical strict fields
    # ------------------------------------------------------------------

    case_id: StrictStr
    run_id: StrictStr
    run_index: StrictInt = 0
    thinking_enabled: StrictBool = False
    budget_exhausted: StrictBool = False

    # Budget telemetry. StrictInt | None — bool/str/float/negative
    # all rejected at the model level.
    executed_requests: StrictInt | None = None
    executed_tokens: StrictInt | None = None

    # Dataset identity. StrictStr | None with format validation —
    # None allowed for backwards compatibility with older artifacts, but
    # when present MUST be a valid 64-lowercase-hex SHA so it can be
    # compared byte-for-byte with the manifest's identity.
    dataset_id: StrictStr | None = None
    dataset_schema_version: StrictStr | None = None
    dataset_content_sha256: StrictStr | None = None

    # ------------------------------------------------------------------
    # Evaluator-scored structural fields (strict)
    # ------------------------------------------------------------------
    # These directly drive evaluator verdicts (tool_decision,
    # evidence_minimality, usage_observability, answer_success). See
    # the class docstring for the full strict contract.
    # ------------------------------------------------------------------

    # Internal-only finalize status — mirrors production FinalizeStatus.
    finalized_status: FINALIZED_STATUS_LITERAL | None = None
    # Internal-only response discriminator — mirrors production ResponseKind.
    response_kind: RESPONSE_KIND_LITERAL | None = None
    # Baseline injection status — mirrors production BaselineStatus.
    baseline_status: BASELINE_STATUS_LITERAL | None = None
    baseline_is_complete: StrictBool | None = None
    baseline_is_injected: StrictBool | None = None

    # Tool-decision counters — direct evaluator input. StrictInt rejects
    # bool/str/float coercion; non-negative validator rejects negatives.
    read_range_calls: StrictInt = 0
    search_current_article_calls: StrictInt = 0

    # Evidence handles — direct input to evidence_minimality. Each handle
    # must be a non-empty strict string. Duplicates / unknown handles
    # are PRESERVED for the evaluator to fail (content-quality issue,
    # NOT rejected at the schema layer).
    cited_evidence_handles: list[StrictStr] = Field(default_factory=list)
    resolved_evidence: list[RawEvidenceObservation] = Field(default_factory=list)
    all_evidence_observations: list[RawEvidenceObservation] = Field(default_factory=list)

    # Typed model-context support
    # observations computed against the ACTUAL model-visible context
    # (``result.baseline_context.model_context_chunks``), NOT
    # ``document_access.snapshot.units`` and NOT the truncated public
    # snippet. Each observation binds a ``fact_id`` to a ``support``
    # boolean + ``model_context_fingerprint`` + ``supporting_handle_ids``
    # (the chunk handle_ids whose text contained an alias hit). The full
    # chunk text / article body / alias hit fragments are NEVER
    # persisted — only these compact verdicts.
    #
    # ``model_context_fingerprint`` (below) is the canonical SHA-256
    # over the actual ``model_context_chunks`` (ordinal + handle_id +
    # text bytes, length-prefixed framing). Each observation's
    # ``model_context_fingerprint`` MUST equal this value — the
    # evaluator rejects observations whose fingerprint does not match.
    # This closes the ``expected_baseline_fingerprint=None`` bypass:
    # the fingerprint is now carried by the artifact itself, so the
    # evaluator cannot be called without it.
    #
    # ``model_context_handle_ids`` is the de-duplicated, order-preserving
    # list of chunk handle_ids in the actual model-visible context. The
    # evaluator requires each observation's ``supporting_handle_ids``
    # to be a subset of this list — observations naming handles that
    # were not in the model context are rejected as
    # ``instrumentation_incomplete`` (fail-closed).
    #
    # Legacy artifacts predating this instrumentation contract have:
    #   - ``model_context_support = []``
    #   - ``model_context_fingerprint = None``
    #   - ``model_context_handle_ids = []``
    # The evaluator surfaces this as
    # ``indeterminate_requires_new_artifact`` (NOT auto-pass, NOT
    # auto-fail). Old artifacts cannot be authoritatively re-evaluated
    # under the new contract; they require a new run.
    model_context_support: list[ModelContextSupportObservation] = Field(
        default_factory=list
    )
    # Canonical SHA-256 over actual
    # ``model_context_chunks``. ``None`` for legacy artifacts or
    # artifacts produced when ``run_reading_record_ask`` raised before
    # assembling the baseline (exception path).
    model_context_fingerprint: StrictStr | None = None
    # Handle_ids of the actual
    # model-visible chunks. Empty for legacy artifacts / exception
    # paths. The evaluator uses this to verify that each observation's
    # ``supporting_handle_ids`` came from real chunks.
    model_context_handle_ids: list[StrictStr] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Explicit instrumentation
    # lifecycle.
    # ------------------------------------------------------------------
    # ``model_context_instrumentation_version`` is the migration marker:
    #   - ``None`` → legacy artifact. Evaluator
    #     surfaces as ``indeterminate_requires_new_artifact`` (replay)
    #     or ``blocked_incomplete_real_model_run`` (authoritative).
    #   - ``"reader_record_ask_model_context_v1"`` → new artifact. The
    #     capture_status literal then distinguishes captured /
    #     unavailable / failed.
    #
    # ``model_context_capture_status`` is None iff version is None
    # (legacy). For new artifacts it MUST be one of:
    #   - ``"captured"`` — baseline assembled, model_context_chunks
    #     non-empty. MUST have legal fingerprint + handle_ids.
    #   - ``"unavailable"`` — model ran but baseline assembly yielded no
    #     chunks (envelope_mismatch / no_units). fingerprint=None,
    #     handle_ids=[], observations=[].
    #   - ``"failed"`` — runtime exception before/independent of
    #     baseline, or baseline assembler itself failed.
    #     fingerprint=None, handle_ids=[], observations=[].
    #
    # The cross-field validator below enforces the (version, status,
    # fingerprint, handle_ids, observations) invariants. This replaces
    # the previous heuristic (fingerprint=None + observations=[] →
    # legacy OR exception — indistinguishable).
    model_context_instrumentation_version: (
        MODEL_CONTEXT_INSTRUMENTATION_VERSION_LITERAL | None
    ) = None
    model_context_capture_status: (
        MODEL_CONTEXT_CAPTURE_STATUS_LITERAL | None
    ) = None

    # Latency — direct input to usage_observability. ``mode="before"``
    # validator rejects bool/str/NaN/Infinity/negative; JSON integer
    # is explicitly allowed and normalized to float.
    latency_seconds: float | None = None

    agent_usage: RawUsage | None = None

    # ------------------------------------------------------------------
    # Display-only fields (lenient — not consumed by evaluator scoring)
    # ------------------------------------------------------------------

    model_short_name: str | None = None
    model_route: str | None = None

    final_text: str | None = None
    finalized_reason: str | None = None
    envelope_fingerprint: str | None = None

    # Actual runtime fixture fingerprint persisted for
    # post-call audit. Deterministic SHA-256 over baseline_status +
    # is_complete + ordered (chunk_ordinal, chunk_text); excludes
    # random handle_ids, paths, UUIDs, timestamps. Computed by the
    # harness from the ACTUAL ``BaselineAgentContext`` produced by
    # ``run_reading_record_ask`` (NOT from the preflight's preview
    # assembly). The aggregate compares this against the dataset's
    # declared ``expected_runtime_fixture_fingerprint`` and the
    # manifest's per-case identity — three-layer check.
    #
    # ``None`` is allowed for backwards compatibility with older
    # artifacts (the harness MUST populate this for new writes, but
    # old artifacts on disk may lack the field). When present, the
    # SHA MUST be 64 lowercase hex chars — the format validator
    # below rejects malformed values.
    runtime_fixture_fingerprint: StrictStr | None = None

    error: str | None = None

    # Safe error code — typed Literal from the
    # single source of truth in :mod:`claread_eval.reader_record_ask.errors`.
    # Pydantic rejects unknown values, empty strings, and type coercion
    # (e.g. ``True`` → ``"true"``) at the artifact-load boundary. ``None``
    # is allowed for backwards compatibility with older artifacts and for
    # success-path artifacts (no error).
    safe_error_code: SafeErrorCode | None = None

    # Preflight status — set when harness aborts before any model call.
    # Values: "ok" / "db_unavailable" / "model_route_invalid" /
    # "thinking_mismatch" / "run_dir_not_writable" / "budget_not_executable".
    # None means preflight was not run (e.g. offline unit test).
    preflight_status: str | None = None

    # ------------------------------------------------------------------
    # Strict field validators (run AFTER Strict* type checks)
    # ------------------------------------------------------------------

    @field_validator("case_id", "run_id")
    @classmethod
    def _non_empty_str(cls, v: str) -> str:
        """Reject empty or whitespace-only strings.

        ``StrictStr`` already rejects bool/int/float, but ``""`` and
        ``"   "`` are valid ``str`` instances that would silently pass
        and then fail downstream (e.g. ``cases_by_id.get("")`` returns
        None, the artifact is skipped, and the verdict sees an empty
        ``case_results`` list — the root cause of the ``all([]) → accepted``
        bug).
        """
        if not v or not v.strip():
            raise ValueError("must be a non-empty, non-whitespace string")
        return v

    @field_validator("run_index")
    @classmethod
    def _non_negative_run_index(cls, v: int) -> int:
        """Reject negative run indices."""
        if v < 0:
            raise ValueError("run_index must be non-negative")
        return v

    @field_validator("executed_requests", "executed_tokens")
    @classmethod
    def _non_negative_or_none_counters(cls, v: int | None) -> int | None:
        """Reject negative counters (None is allowed — counters may be unset)."""
        if v is not None and v < 0:
            raise ValueError("counter must be non-negative when present")
        return v

    @field_validator("dataset_id", "dataset_schema_version")
    @classmethod
    def _non_empty_or_none_identity_str(cls, v: str | None) -> str | None:
        """Reject empty/whitespace strings when present (None is allowed)."""
        if v is not None and (not v or not v.strip()):
            raise ValueError(
                "identity string must be non-empty when present (None is allowed)"
            )
        return v

    @field_validator("dataset_content_sha256")
    @classmethod
    def _sha256_lowercase_hex_or_none(cls, v: str | None) -> str | None:
        """Require exactly 64 lowercase hex chars when present.

        None is allowed for backwards compatibility with older artifacts
        (the harness MUST populate this for new writes, but old artifacts
        on disk may lack the field). When present, the SHA MUST be
        byte-for-byte comparable to the manifest's identity SHA —
        uppercase hex, short strings, and non-hex characters are all
        rejected so a malformed artifact cannot satisfy a manifest's
        identity triple via string-coincidence.
        """
        if v is not None and not _SHA256_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "dataset_content_sha256 must be 64 lowercase hex chars when present"
            )
        return v

    @field_validator("runtime_fixture_fingerprint")
    @classmethod
    def _runtime_fixture_fingerprint_hex_or_none(cls, v: str | None) -> str | None:
        """Require 64 lowercase hex chars when present.

        ``None`` is allowed for backwards compatibility with older
        artifacts (the harness MUST populate this for new writes, but
        old artifacts on disk may lack the field). When present, the
        SHA MUST be 64 lowercase hex chars — the same strict format as
        ``dataset_content_sha256`` so the aggregate can compare it
        byte-for-byte against the manifest's identity map and the
        dataset's declared expected value.
        """
        if v is not None and not _SHA256_LOWERCASE_HEX_RE.match(v):
            raise ValueError(
                "runtime_fixture_fingerprint must be 64 lowercase hex chars when present"
            )
        return v

    # ------------------------------------------------------------------
    # Evaluator-scored structural field validators
    # ------------------------------------------------------------------

    @field_validator("read_range_calls", "search_current_article_calls")
    @classmethod
    def _non_negative_tool_call_count(cls, v: int) -> int:
        """Reject negative tool call counts.

        :class:`StrictInt` already rejects bool/str/float; this validator
        rejects negatives so a corrupted ``read_range_calls=-1`` cannot
        satisfy the ``tool_decision`` evaluator's ``rr > 0`` branch.
        """
        if v < 0:
            raise ValueError("tool call count must be non-negative")
        return v

    @field_validator("cited_evidence_handles")
    @classmethod
    def _non_empty_cited_handles(cls, v: list[str]) -> list[str]:
        """Reject empty/whitespace handle entries.

        :class:`StrictStr` element annotation already rejects
        bool/int/float elements. This validator runs after Pydantic's
        element-level check and rejects empty/whitespace strings — a
        ``""`` handle would corrupt the ``evidence_minimality``
        set-membership check.

        Duplicate handles and unknown handles are PRESERVED — they are
        content-quality issues that the ``evidence_minimality``
        evaluator is responsible for detecting and failing.
        """
        for handle in v:
            if not isinstance(handle, str) or not handle or not handle.strip():
                raise ValueError(
                    "cited_evidence_handles entries must be non-empty, "
                    "non-whitespace strings"
                )
        return v

    @field_validator("latency_seconds", mode="before")
    @classmethod
    def _strict_latency_seconds(cls, v: object) -> object:
        """Reject bool / str / NaN / Infinity / negative latency.

        Design choice (per spec section 三): JSON integer is explicitly
        ALLOWED as a legitimate seconds representation and is normalized
        to float. Bool is rejected even though ``isinstance(True, int)``
        returns True — a bool latency is a clear type confusion that
        would silently satisfy the ``usage_observability``
        ``latency_seconds > 0`` check.

        ``mode="before"`` is required so the validator intercepts the
        raw input BEFORE Pydantic's default ``float | None`` coercion
        turns ``True`` → ``1.0`` and ``"1"`` → ``1.0``.
        """
        if v is None:
            return None
        # Bool must be rejected BEFORE the int check (bool is a subtype
        # of int in Python).
        if isinstance(v, bool):
            raise ValueError("latency_seconds must not be a bool")
        # JSON integer is allowed and normalized to float.
        if isinstance(v, int):
            v = float(v)
        if not isinstance(v, float):
            raise ValueError(
                "latency_seconds must be a number (int or float), "
                "not a string or other type"
            )
        # NaN / Infinity are not valid latencies.
        if not math.isfinite(v):
            raise ValueError("latency_seconds must be finite (NaN/Infinity rejected)")
        if v < 0:
            raise ValueError("latency_seconds must be non-negative")
        return v

    @model_validator(mode="after")
    def _validate_model_context_instrumentation_lifecycle(self) -> RawArtifact:
        """Enforce the four-state
        instrumentation lifecycle invariants.

        The 4 mutually-exclusive states are distinguished by
        ``(model_context_instrumentation_version,
        model_context_capture_status)`` without inspecting error text
        or finalized_reason:

        1. **legacy** — version=None AND capture_status=None. Allowed
           to have empty fingerprint / handle_ids / observations
           (older artifact).
        2. **captured** — version=v1 AND capture_status="captured".
           When at least one required atomic fact has source_aliases,
           the harness MUST have produced a fingerprint AND
           handle_ids. When there are no required source facts
           (metadata-only / no atomic_facts), fingerprint is allowed
           to be None (no chunks needed capture) — this is the
           no-facts case, not an instrumentation gap.
        3. **unavailable** — version=v1 AND capture_status="unavailable".
           fingerprint MUST be None, handle_ids MUST be empty,
           observations MUST be empty.
        4. **failed** — version=v1 AND capture_status="failed".
           fingerprint MUST be None, handle_ids MUST be empty,
           observations MUST be empty.

        Any other combination is contract corruption and is rejected
        at the artifact load boundary (fail-closed).

        The validator deliberately does NOT inspect ``error`` or
        ``finalized_reason`` — those are display-only fields and
        must not drive lifecycle classification.
        """
        v = self.model_context_instrumentation_version
        s = self.model_context_capture_status

        # Invariant 1: version and status are BOTH None (legacy) or
        # BOTH non-None (new artifact). A mixed state is corruption.
        if (v is None) != (s is None):
            raise ValueError(
                "model_context_instrumentation_version and "
                "model_context_capture_status must be both None "
                "(legacy artifact) or both non-None (new artifact); "
                f"got version={v!r}, status={s!r}"
            )

        if v is None and s is None:
            # Legacy artifact. No further constraints — the evaluator
            # surfaces this as indeterminate_requires_new_artifact
            # (replay) or blocked_incomplete_real_model_run
            # (authoritative). We deliberately allow legacy artifacts
            # to carry non-empty fingerprint / handle_ids / observations
            # in case a partial migration wrote them, but the evaluator
            # will still treat the artifact as legacy because version
            # is None.
            return self

        # New artifact (v is not None, s is not None).
        # The Literal types already constrain v ∈ {"reader_record_ask_model_context_v1"}
        # and s ∈ {"captured", "unavailable", "failed"}.

        if s == "captured":
            # fingerprint + handle_ids MUST be present when capture
            # succeeded. The only exception is when there are zero
            # model_context_chunks (e.g. empty document) — in that
            # case the harness MUST have set capture_status="unavailable"
            # instead. So under "captured", fingerprint is non-None
            # and handle_ids is non-empty.
            if self.model_context_fingerprint is None:
                raise ValueError(
                    "model_context_capture_status='captured' requires "
                    "model_context_fingerprint to be set (got None); "
                    "use capture_status='unavailable' when no chunks "
                    "were captured"
                )
            if not self.model_context_handle_ids:
                raise ValueError(
                    "model_context_capture_status='captured' requires "
                    "model_context_handle_ids to be non-empty "
                    "(got []); use capture_status='unavailable' when "
                    "no chunks were captured"
                )
            # observations may be empty only when no required atomic
            # fact has source_aliases (metadata-only / no atomic_facts).
            # The evaluator enforces the per-fact observation
            # completeness check; here we only enforce that captured
            # observations reference handles that are in
            # model_context_handle_ids.
            handle_set = set(self.model_context_handle_ids)
            for obs in self.model_context_support:
                unknown = [
                    h for h in obs.supporting_handle_ids if h not in handle_set
                ]
                if unknown:
                    raise ValueError(
                        "model_context_capture_status='captured' "
                        "observation references handle_ids not in "
                        "model_context_handle_ids; first unknown="
                        f"{unknown[0]!r}"
                    )
            return self

        # s in {"unavailable", "failed"}.
        # fingerprint MUST be None, handle_ids MUST be empty,
        # observations MUST be empty.
        if self.model_context_fingerprint is not None:
            raise ValueError(
                f"model_context_capture_status={s!r} requires "
                "model_context_fingerprint=None (got "
                f"{self.model_context_fingerprint!r})"
            )
        if self.model_context_handle_ids:
            raise ValueError(
                f"model_context_capture_status={s!r} requires "
                "model_context_handle_ids=[] (got non-empty)"
            )
        if self.model_context_support:
            raise ValueError(
                f"model_context_capture_status={s!r} requires "
                "model_context_support=[] (got non-empty)"
            )
        return self
