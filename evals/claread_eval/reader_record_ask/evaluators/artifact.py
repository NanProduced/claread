"""Serializable raw artifact view for the R4-A3 reader-record-ask evaluators.

The evaluators operate on :class:`RawArtifact` — a pure-data, serializable
projection of :class:`ReadingRecordAskRunResult` plus
:class:`FinalizedAskResult` / :class:`BaselineAgentContext`. Evaluators must
NOT import runtime classes; they consume this artifact only.

Field names follow the spec (``.trae/specs/reader-record-ask-r4-a3-
correctness-eval/spec.md`` — Requirement: 11 维确定性 evaluator + 真实模型运行
策略 + 报告脱敏与可聚合).

P0-1 strict contract (R4-A3 final closure — artifact audit boundary):
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

#: Strict SHA-256 lowercase hex pattern (exactly 64 lowercase hex chars).
#: Used to validate ``dataset_content_sha256``. Mirrors the same regex used
#: by the manifest schema validator — artifact-side identity MUST be as
#: strict as manifest-side identity so the two can be compared reliably.
_SHA256_LOWERCASE_HEX_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")

# ---------------------------------------------------------------------------
# P0-2: Evaluator-consumed enum literals
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
# P0-3: Legal (kind, provenance) cross-field invariant
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

    P0-2 strict contract (R4-A3 final closure — evaluator-input boundary):
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

        P0-3 cross-field invariant: each ``kind`` has a strictly
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

    P0-1 strict contract: counters are :class:`StrictInt` (rejects bool /
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


class RawArtifact(BaseModel):
    """Evaluator input — pure data view of one independent agent run.

    Built by the harness (Task 4) from
    :class:`ReadingRecordAskRunResult`. Contains no runtime object
    references; safe to serialize to JSON and persist under the
    :class:`RunSessionLayout`-managed local ignored run directory.

    P0-1 strict contract (R4-A3 final closure — artifact audit boundary):

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

    P0-2 strict contract (R4-A3 final closure — evaluator-input boundary):

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

    # P0-8: budget telemetry. StrictInt | None — bool/str/float/negative
    # all rejected at the model level.
    executed_requests: StrictInt | None = None
    executed_tokens: StrictInt | None = None

    # P0-2 dataset identity. StrictStr | None with format validation —
    # None allowed for backwards compat with pre-P0-2 artifacts, but
    # when present MUST be a valid 64-lowercase-hex SHA so it can be
    # compared byte-for-byte with the manifest's identity.
    dataset_id: StrictStr | None = None
    dataset_schema_version: StrictStr | None = None
    dataset_content_sha256: StrictStr | None = None

    # ------------------------------------------------------------------
    # P0-2: Evaluator-scored structural fields (strict)
    # ------------------------------------------------------------------
    # These directly drive evaluator verdicts (tool_decision,
    # evidence_minimality, usage_observability, answer_success). See
    # the class docstring for the full P0-2 strict contract.
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
    error: str | None = None

    # P1-2: safe error code — allowlisted code from project_safe_error().
    safe_error_code: str | None = None

    # P1-2: preflight status — set when harness aborts before any model call.
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

        None is allowed for backwards compat with pre-P0-2 artifacts
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

    # ------------------------------------------------------------------
    # P0-2: Evaluator-scored structural field validators
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
