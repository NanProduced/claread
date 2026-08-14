"""Dimension 2/11 — context_support.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: context_support atomic fact contract.

Authoritative model-visible context binding
==================================================================

The previous implementation computed support against
``document_access.snapshot.units`` (the full document scope) and bound
every fact to ``cited_handles[0]``. This was contractually broken
because:

1. ``snapshot.units`` is NOT what the model actually sees. The
   baseline assembler applies a raw 8000-char budget, a serialized
   16000-char budget, and a 16-chunk cap. For medium/long articles
   the model sees only a prefix of the units; aliases in the
   truncated tail were marked "supported" when they were NOT
   model-visible (false negative for hallucination detection).
2. ``cited_handles[0]`` was bound to every fact regardless of which
   chunk's text actually contained the alias. A fact supported by
   the second chunk was mis-bound to the first chunk's handle, so a
   model that cited only the first chunk would still pass.
3. ``expected_baseline_fingerprint`` was an optional parameter that
   the real evaluation entrypoint (:func:`evaluate_artifact`) never
   passed — making the fingerprint check dead code and letting any
   observation pass regardless of which baseline it was computed
   against.

The current contract:

- The harness computes support against
  ``result.baseline_context.model_context_chunks`` (the ACTUAL
  model-visible chunks) — never ``snapshot.units``.
- Each observation carries ``supporting_handle_ids`` — the
  de-duplicated, order-preserving list of chunk handle_ids whose text
  contained an alias hit. ``support=True`` with an empty
  ``supporting_handle_ids`` is ``instrumentation_incomplete`` (the
  harness could not determine which chunk supported the fact).
- ``RawArtifact.model_context_fingerprint`` is the canonical SHA-256
  over the actual chunks (length-prefixed framing of ordinal +
  handle_id + text bytes). Each observation's
  ``model_context_fingerprint`` MUST equal the artifact's fingerprint
  — the evaluator rejects mismatches as
  ``fingerprint_mismatch_instrumentation_incomplete`` (fail-closed).
- ``RawArtifact.model_context_handle_ids`` lists the chunk handle_ids
  in the actual model context. Each observation's
  ``supporting_handle_ids`` MUST be a subset — observations naming
  handles not in the model context are rejected as
  ``supporting_handle_not_in_model_context`` (fail-closed).
- The evaluator requires at least one of the observation's
  ``supporting_handle_ids`` to appear in
  ``artifact.cited_evidence_handles`` for the fact to be grounded —
  this is the authoritative fact→chunk→handle→citation binding.

Explicit instrumentation lifecycle
=====================================================================

The previous heuristic (``fingerprint=None + observations=[]`` →
legacy OR exception) could not distinguish a legacy artifact from a
new runtime-exception artifact. The new contract uses two explicit
fields on :class:`RawArtifact`:

- ``model_context_instrumentation_version`` — ``None`` for legacy,
  ``"reader_record_ask_model_context_v1"`` for new artifacts.
- ``model_context_capture_status`` — ``None`` for legacy, one of
  ``"captured"`` / ``"unavailable"`` / ``"failed"`` for new.

The 4 mutually-exclusive states drive the verdict:

| ver   | status      | fp       | handles | obs       | classification                       |
|-------|-------------|----------|---------|-----------|--------------------------------------|
| None  | None        | (any)    | (any)   | (any)     | indeterminate_requires_new_artifact  |
| v1    | captured    | non-None | non-[]  | per-fact  | authoritative; per-fact verdict      |
| v1    | unavailable | None     | []      | []        | instrumentation_incomplete (blocker) |
| v1    | failed      | None     | []      | []        | instrumentation_incomplete (blocker) |

Only ``captured`` artifacts can produce ``fact_not_supported`` /
``fact_not_cited`` verdicts (real model correctness failures that
enter rework). ``unavailable`` / ``failed`` ALWAYS produce
``instrumentation_incomplete`` (blocker — does NOT enter rework, does
NOT count as ``fact-not-grounded`` cluster).

Coverage / fail-closed matrix (for ``captured`` artifacts):

| condition                                    | classification                  |
|----------------------------------------------|---------------------------------|
| Observation fingerprint ≠ artifact fp        | instrumentation_incomplete (fail) |
| Observation fact_id not in case.atomic_facts | instrumentation_incomplete (fail) |
| Duplicate fact_id in observations            | instrumentation_incomplete (fail) |
| Required fact missing observation             | instrumentation_incomplete (fail) |
| support=True but supporting_handle_ids empty | instrumentation_incomplete (fail) |
| supporting_handle_ids ⊄ model_context_handles| instrumentation_incomplete (fail) |
| support=True, handles valid, none cited      | fact_not_cited (fail, rework)   |
| support=False                                 | fact_not_supported (fail, rework)|
| support=True, handles valid, ≥1 cited        | supported (pass)                |

Legacy artifacts: the evaluator returns
``passed=True`` with ``coverage_incomplete=true
(legacy_artifact_no_model_context_support)``. This is NOT a failure
— old artifacts cannot be authoritatively re-evaluated under the new
contract; they require a new run. The historical replay tool labels
these as ``indeterminate_requires_new_artifact``.

The ``answer_alias_groups`` contract for "is the fact mentioned in
the answer?" is unchanged — see :class:`AtomicExpectedFact`.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import (
    ModelContextSupportObservation,
    RawArtifact,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_BASELINE_UNAVAILABLE as REASON_UNAVAILABLE,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_FACT_NOT_CITED as REASON_FACT_NOT_CITED,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_FACT_NOT_SUPPORTED as REASON_FACT_NOT_SUPPORTED,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_INSTRUMENTATION_INCOMPLETE as REASON_INSTRUMENTATION_INCOMPLETE,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_LEGACY as REASON_LEGACY,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_RUNTIME_EXCEPTION as REASON_FAILED,
)
from claread_eval.reader_record_ask.evaluators.context_support_contract import (
    CLASSIFICATION_SUPPORTED as REASON_SUPPORTED,
)
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import (
    AtomicExpectedFact,
    ReaderRecordAskCase,
)

DIMENSION = "context_support"

# Classification reason tags
# and the three routing frozensets are imported from
# :mod:`evaluators.context_support_contract` — the SINGLE source of
# truth shared by the evaluator, aggregator, and runner. The
# ``REASON_*`` aliases below preserve the existing emit-site
# ergonomics (``REASON_LEGACY`` etc.) without redefining the
# vocabulary. ``fact_not_supported`` / ``fact_not_cited`` are
# intentionally NOT in
# :data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` — they are real
# model correctness failures that DO enter rework.


def _alias_hit_in_text(alias: str, text_lower: str) -> bool:
    """Case-insensitive substring match for a single alias."""
    if not alias:
        return False
    return alias.lower() in text_lower


def _alias_group_hit(group: list[str], text_lower: str) -> bool:
    """Return ``True`` if ANY alias in ``group`` appears in ``text_lower``."""
    if not group:
        # An empty group is treated as "no constraint" — vacuously true.
        # This supports facts that are metadata-only (no answer aliases).
        return True
    return any(_alias_hit_in_text(alias, text_lower) for alias in group)


def _fact_mentioned_in_answer(fact: AtomicExpectedFact, final_text_lower: str) -> bool:
    """Return ``True`` if ``fact`` is mentioned in the answer text.

    A fact is "mentioned" iff EVERY alias group in
    ``fact.answer_alias_groups`` has at least one alias hit. Empty
    groups are vacuously true (no constraint).

    When ``answer_alias_groups`` is empty, the fact has no answer-side
    constraint — it's a metadata-only fact (e.g. "the article does not
    mention year X"). Return ``True`` so the fact is treated as
    "mentioned" (no answer-side failure), and the evidence-support
    check (if any) decides grounding.
    """
    if not fact.answer_alias_groups:
        return True
    return all(_alias_group_hit(group, final_text_lower) for group in fact.answer_alias_groups)


def _classify_observation(
    fact: AtomicExpectedFact,
    obs: ModelContextSupportObservation | None,
    *,
    artifact_fingerprint: str | None,
    model_context_handle_set: set[str],
    cited_handle_set: set[str],
) -> tuple[bool | None, str]:
    """Return ``(grounded, reason)`` for ``fact``.

    Returns:
        ``(True, "supported")`` — fact is grounded: a valid typed
        observation exists with ``support=True``, its fingerprint
        matches the artifact's, its ``supporting_handle_ids`` are all
        in the model context, and at least one is in the artifact's
        ``cited_evidence_handles``.

        ``(False, "fact_not_supported")`` — observation exists with
        ``support=False``. Real model failure: the answer cited a fact
        the model could not have seen in its baseline.

        ``(False, "fact_not_cited")`` — observation is valid and
        ``support=True``, but none of the ``supporting_handle_ids``
        appear in ``artifact.cited_evidence_handles``. The model saw
        the fact but did not cite the supporting chunk.

        ``(False, "<reason>")`` — instrumentation_incomplete reasons
        (fail-closed): ``no_observation_for_required_fact``,
        ``fingerprint_mismatch``, ``supporting_handle_not_in_model_context``,
        ``support_true_with_empty_supporting_handles``,
        ``duplicate_fact_id_in_observations`` (handled by caller),
        ``observation_fact_id_not_in_case_atomic_facts`` (handled by
        caller).

        ``(None, "no_source_aliases")`` — the fact has no
        ``source_aliases``. Metadata-only fact with no grounding
        constraint. Vacuously grounded.

        ``(None, "legacy_no_observation")`` — the artifact has no
        fingerprint and no observations (legacy artifact). The fact
        cannot be authoritatively evaluated; caller surfaces this as
        ``indeterminate_requires_new_artifact``.
    """
    if not fact.source_aliases:
        return True, "no_source_aliases"

    # Legacy artifact path: no fingerprint, no observations. Caller
    # has already verified this condition before calling
    # ``_classify_observation`` for each fact; we still handle it
    # defensively here in case the caller's precheck missed an edge.
    if artifact_fingerprint is None and obs is None:
        return None, "legacy_no_observation"

    if obs is None:
        # New authoritative artifact missing an observation for a
        # required fact with source_aliases — fail-closed.
        return False, "no_observation_for_required_fact"

    # Fingerprint must match the artifact's fingerprint.
    if artifact_fingerprint is None:
        # New artifact has observations but no fingerprint —
        # instrumentation incomplete, fail-closed.
        return False, "artifact_missing_model_context_fingerprint"
    if obs.model_context_fingerprint != artifact_fingerprint:
        return False, "fingerprint_mismatch"

    # Supporting_handle_ids must all be in the actual model
    # context. An observation naming a handle that was not in the
    # model context is forged / stale — fail-closed.
    if obs.supporting_handle_ids:
        unknown_handles = [
            h for h in obs.supporting_handle_ids
            if h not in model_context_handle_set
        ]
        if unknown_handles:
            return False, "supporting_handle_not_in_model_context"

    if not obs.support:
        # support=False with valid fingerprint + handle binding =
        # real model failure.
        return False, "fact_not_supported"

    # support=True. Supporting_handle_ids MUST be non-empty
    # (the harness must record which chunk(s) contained the alias
    # hit). An empty list with support=True means the harness could
    # not determine which chunk supported the fact — fail-closed.
    if not obs.supporting_handle_ids:
        return False, "support_true_with_empty_supporting_handles"

    # At least one supporting_handle_id must be in the
    # artifact's cited_evidence_handles. This is the authoritative
    # fact→chunk→handle→citation binding.
    if not any(h in cited_handle_set for h in obs.supporting_handle_ids):
        return False, "fact_not_cited"

    return True, "supported"


def evaluate_context_support(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    """Evaluate whether required atomic facts are mentioned and grounded.

    Authoritative grounding uses typed
    :class:`ModelContextSupportObservation` entries from the artifact,
    computed by the harness against the ACTUAL
    ``result.baseline_context.model_context_chunks`` (NOT
    ``document_access.snapshot.units``). The fingerprint and
    supporting_handle_ids are verified against the artifact's own
    ``model_context_fingerprint`` / ``model_context_handle_ids`` —
    there is no caller-supplied ``expected_baseline_fingerprint``
    parameter (the previous bypass is closed).

    The explicit lifecycle fields
    ``model_context_instrumentation_version`` and
    ``model_context_capture_status`` drive a 4-state classification:

    1. legacy (version=None, status=None) → ``passed=True`` with
       ``coverage_incomplete`` (NOT failure); replay classifies as
       ``indeterminate_requires_new_artifact``.
    2. captured (version=v1, status="captured") → authoritative per-
       fact verdict (supported / fact_not_supported / fact_not_cited
       / instrumentation_incomplete per-fact).
    3. unavailable (version=v1, status="unavailable") →
       ``passed=False``, ``classification=baseline_unavailable``
       (instrumentation_incomplete blocker — does NOT enter rework,
       does NOT cluster as fact-not-grounded).
    4. failed (version=v1, status="failed") → ``passed=False``,
       ``classification=runtime_exception`` (instrumentation_incomplete
       blocker — same handling as unavailable).

    The returned :class:`EvalDimensionResult` carries a typed
    ``classification`` field (one of the ``REASON_*`` constants) so
    the aggregator / readiness audit can distinguish instrumentation
    blockers from real model failures WITHOUT parsing ``details``
    text.

    Args:
        case: the eval case with ``expected.atomic_facts``.
        artifact: the raw artifact. ``artifact.model_context_support``
            provides typed per-fact support observations;
            ``artifact.model_context_fingerprint`` is the canonical
            SHA-256 over the actual chunks;
            ``artifact.model_context_handle_ids`` is the set of chunk
            handle_ids in the actual model context;
            ``artifact.model_context_instrumentation_version`` /
            ``artifact.model_context_capture_status`` are the explicit
            lifecycle fields.

    Returns:
        :class:`EvalDimensionResult` with ``dimension="context_support"``.
        ``passed=False`` when any required fact is not mentioned,
        not grounded, not cited, or when instrumentation is incomplete
        (fail-closed for new artifacts). Legacy artifacts (version=None)
        return ``passed=True`` with ``coverage_incomplete=true
        (legacy_artifact_no_model_context_support)`` — this is NOT a
        failure, but the historical replay tool labels it
        ``indeterminate_requires_new_artifact``.
    """
    final_text_lower = (artifact.final_text or "").lower()

    atomic_facts = case.expected.atomic_facts
    artifact_fingerprint = artifact.model_context_fingerprint
    model_context_handle_set: set[str] = set(artifact.model_context_handle_ids)
    cited_handle_set: set[str] = set(artifact.cited_evidence_handles)

    # Capability boundary signals.
    coverage_incomplete_no_facts = not atomic_facts

    # ----------------------------------------------------------------------
    # Explicit four-state lifecycle.
    # ----------------------------------------------------------------------
    # The previous heuristic (``fingerprint=None + observations=[]`` →
    # legacy OR exception) could NOT distinguish a legacy artifact
    # from a new runtime-exception artifact. The new contract uses
    # ``model_context_instrumentation_version`` /
    # ``model_context_capture_status`` as the SINGLE source of truth
    # for lifecycle classification — error text and finalized_reason
    # are NOT consulted.
    #
    # The cross-field validator on :class:`RawArtifact` already
    # enforces the (version, status, fingerprint, handle_ids,
    # observations) invariants at the artifact load boundary. Here we
    # branch on the explicit fields.
    instrumentation_version = artifact.model_context_instrumentation_version
    capture_status = artifact.model_context_capture_status

    is_legacy_artifact = instrumentation_version is None
    is_unavailable = (
        instrumentation_version is not None
        and capture_status == "unavailable"
    )
    is_failed = (
        instrumentation_version is not None
        and capture_status == "failed"
    )
    is_captured = (
        instrumentation_version is not None
        and capture_status == "captured"
    )

    # Explicit ``unavailable`` / ``failed`` → instrumentation
    # blocker (NOT model failure). The artifact's invariant (enforced
    # at load time) guarantees fingerprint=None, handle_ids=[],
    # observations=[] for these states.
    if is_unavailable or is_failed:
        blocker_reason = (
            REASON_UNAVAILABLE if is_unavailable else REASON_FAILED
        )
        blocker_label = (
            "baseline_unavailable" if is_unavailable else "runtime_exception"
        )
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=False,
            severity="none",
            details=(
                f"context_support: instrumentation_incomplete "
                f"({blocker_label}; model_context_capture_status="
                f"{capture_status!r}; cannot authoritatively evaluate "
                f"—— NOT a model correctness failure, NOT rework-eligible)"
            ),
            evidence_refs=[ev.handle_id for ev in artifact.resolved_evidence],
            classification=blocker_reason,
        )

    legacy_no_support = is_legacy_artifact and bool(atomic_facts)

    # Instrumentation_incomplete for captured artifacts: artifact
    # has observations OR a fingerprint, but is missing the other half.
    # This is fail-closed — the run cannot be authoritatively
    # evaluated. Triggers when:
    #   - has observations but no fingerprint (exception path with
    #     partial instrumentation)
    #   - has fingerprint but no observations (harness gap)
    #
    # Caveat: when no atomic_fact requires an observation (i.e. no
    # required fact has ``source_aliases`` — either because there are
    # no atomic_facts at all, or every fact is metadata-only), the
    # harness correctly emits zero observations. An artifact with a
    # fingerprint and empty observations is NOT an instrumentation
    # gap in that case — it is the expected state. The xor check is
    # only meaningful when at least one required fact needs grounding.
    has_observations = bool(artifact.model_context_support)
    has_fingerprint = artifact_fingerprint is not None
    needs_observations = any(
        f.source_aliases for f in atomic_facts if f.required
    )
    instrumentation_incomplete_artifact = False
    if is_captured and needs_observations:
        if has_fingerprint != has_observations:
            instrumentation_incomplete_artifact = True
        # Also: has fingerprint + observations but no
        # model_context_handle_ids means the harness did not record
        # which handles were in the model context — the evaluator
        # cannot verify supporting_handle_ids.
        elif (
            has_fingerprint
            and has_observations
            and not model_context_handle_set
        ):
            instrumentation_incomplete_artifact = True

    coverage_incomplete_facts: list[str] = []
    instrumentation_failures: list[str] = []
    fact_failures: list[str] = []
    # Track the set of classification reasons emitted. The
    # ``classification`` field on the returned EvalDimensionResult is
    # a single string — we pick the strongest reason in precedence:
    #   instrumentation_incomplete > fact_not_supported /
    #   fact_not_cited > supported > legacy
    emitted_reasons: list[str] = []

    # Build observation lookup, detecting duplicate fact_ids (
    # fail-closed). Also detect observations whose fact_id is not in
    # the case's atomic_facts (fail-closed).
    support_by_fact_id: dict[str, ModelContextSupportObservation] = {}
    duplicate_fact_ids: set[str] = set()
    case_fact_ids: set[str] = {f.fact_id for f in atomic_facts}
    unknown_observation_fact_ids: list[str] = []
    for obs in artifact.model_context_support:
        if obs.fact_id in support_by_fact_id:
            duplicate_fact_ids.add(obs.fact_id)
            continue
        support_by_fact_id[obs.fact_id] = obs
        if obs.fact_id not in case_fact_ids:
            unknown_observation_fact_ids.append(obs.fact_id)

    if duplicate_fact_ids:
        instrumentation_failures.append(
            "context_support: instrumentation_incomplete "
            "(duplicate_fact_id_in_observations: "
            + ",".join(sorted(duplicate_fact_ids))
            + ")"
        )
        emitted_reasons.append(REASON_INSTRUMENTATION_INCOMPLETE)
    if unknown_observation_fact_ids:
        instrumentation_failures.append(
            "context_support: instrumentation_incomplete "
            "(observation_fact_id_not_in_case_atomic_facts: "
            + ",".join(unknown_observation_fact_ids)
            + ")"
        )
        emitted_reasons.append(REASON_INSTRUMENTATION_INCOMPLETE)
    if instrumentation_incomplete_artifact:
        instrumentation_failures.append(
            "context_support: instrumentation_incomplete "
            "(artifact_has_observations_xor_fingerprint_or_no_handle_ids; "
            "cannot authoritatively evaluate)"
        )
        emitted_reasons.append(REASON_INSTRUMENTATION_INCOMPLETE)

    for fact in atomic_facts:
        fact_id = fact.fact_id
        mentioned = _fact_mentioned_in_answer(fact, final_text_lower)

        if not mentioned:
            if fact.required:
                fact_failures.append(
                    f"required fact not mentioned in final_text: fact_id={fact_id}"
                )
                # ``fact_not_mentioned`` is a real model failure —
                # the answer did not include the required fact. This
                # is NOT instrumentation_incomplete. We surface it as
                # a generic fact-level failure (no specific typed tag
                # because there is no observation to classify).
                if REASON_FACT_NOT_SUPPORTED not in emitted_reasons:
                    emitted_reasons.append(REASON_FACT_NOT_SUPPORTED)
            # Non-required facts: absence is informational only.
            continue

        obs = support_by_fact_id.get(fact_id)
        grounded, reason = _classify_observation(
            fact=fact,
            obs=obs,
            artifact_fingerprint=artifact_fingerprint,
            model_context_handle_set=model_context_handle_set,
            cited_handle_set=cited_handle_set,
        )

        if grounded is None:
            # Only ``legacy_no_observation`` / ``no_source_aliases``
            # return None. ``no_source_aliases`` returns ``True`` —
            # not None — so this branch is the legacy path. Surface
            # as coverage_incomplete (NOT failure).
            coverage_incomplete_facts.append(fact_id)
            continue

        if not grounded:
            if reason.startswith("no_observation_for_required_fact") or (
                reason == "legacy_no_observation"
            ):
                # ``no_observation_for_required_fact`` is
                # instrumentation_incomplete for new artifacts (fail-
                # closed). ``legacy_no_observation`` is handled above
                # (returns None) — defensive only.
                instrumentation_failures.append(
                    f"context_support: instrumentation_incomplete "
                    f"(no_observation_for_required_fact: fact_id={fact_id})"
                )
                if REASON_INSTRUMENTATION_INCOMPLETE not in emitted_reasons:
                    emitted_reasons.append(REASON_INSTRUMENTATION_INCOMPLETE)
            elif reason in (
                "artifact_missing_model_context_fingerprint",
                "fingerprint_mismatch",
                "supporting_handle_not_in_model_context",
                "support_true_with_empty_supporting_handles",
            ):
                instrumentation_failures.append(
                    f"context_support: instrumentation_incomplete "
                    f"({reason}: fact_id={fact_id})"
                )
                if REASON_INSTRUMENTATION_INCOMPLETE not in emitted_reasons:
                    emitted_reasons.append(REASON_INSTRUMENTATION_INCOMPLETE)
            elif reason == "fact_not_supported":
                fact_failures.append(
                    f"fact mentioned in final_text but not grounded in "
                    f"model-visible baseline: fact_id={fact_id} reason={reason}"
                )
                if REASON_FACT_NOT_SUPPORTED not in emitted_reasons:
                    emitted_reasons.append(REASON_FACT_NOT_SUPPORTED)
            elif reason == "fact_not_cited":
                fact_failures.append(
                    f"fact supported by model-visible chunk but not cited: "
                    f"fact_id={fact_id} reason={reason}"
                )
                if REASON_FACT_NOT_CITED not in emitted_reasons:
                    emitted_reasons.append(REASON_FACT_NOT_CITED)
            else:
                # Unknown reason — surface as a generic fact failure
                # (defense-in-depth; should not happen because
                # ``_classify_observation`` emits only the reasons
                # handled above).
                fact_failures.append(
                    f"fact mentioned in final_text but not grounded in "
                    f"model-visible baseline: fact_id={fact_id} reason={reason}"
                )

    # Final verdict: any fact failure OR instrumentation failure →
    # fail. Legacy artifacts (version=None) get ``passed=True`` with
    # coverage_incomplete (NOT failure).
    has_failures = bool(fact_failures)
    has_instrumentation_failures = bool(instrumentation_failures)
    passed = not has_failures and not has_instrumentation_failures
    severity = "none" if passed else _highest_severity(atomic_facts)

    # ----------------------------------------------------------------------
    # Typed classification.
    # ----------------------------------------------------------------------
    # Precedence (strongest first):
    #   1. instrumentation_incomplete (blocker — NOT rework-eligible)
    #   2. fact_not_supported (real model failure — rework-eligible)
    #   3. fact_not_cited (real model failure — rework-eligible)
    #   4. legacy_artifact (NOT failure — coverage_incomplete)
    #   5. supported (pass)
    #   6. None (no typed signal — e.g. metadata-only / no atomic_facts)
    if REASON_INSTRUMENTATION_INCOMPLETE in emitted_reasons:
        classification = REASON_INSTRUMENTATION_INCOMPLETE
    elif REASON_FACT_NOT_SUPPORTED in emitted_reasons:
        classification = REASON_FACT_NOT_SUPPORTED
    elif REASON_FACT_NOT_CITED in emitted_reasons:
        classification = REASON_FACT_NOT_CITED
    elif is_legacy_artifact:
        classification = REASON_LEGACY
    elif passed:
        classification = REASON_SUPPORTED
    else:
        classification = None

    details_parts: list[str] = []
    if coverage_incomplete_no_facts:
        details_parts.append(
            "context_support: coverage_incomplete=true (case has no "
            "atomic_facts; deterministic evaluator cannot assert coverage)"
        )
    if legacy_no_support:
        details_parts.append(
            "context_support: coverage_incomplete=true "
            "(legacy_artifact_no_model_context_support; grounding "
            "verdict deferred — requires new run with typed support "
            "observations + model_context_fingerprint + "
            "model_context_handle_ids)"
        )
    if coverage_incomplete_facts:
        details_parts.append(
            "context_support: coverage_incomplete_facts="
            + ",".join(coverage_incomplete_facts)
            + " (no typed ModelContextSupportObservation for these "
            "facts; grounding verdict deferred)"
        )
    if instrumentation_failures:
        details_parts.extend(instrumentation_failures)
    if passed:
        if (
            not coverage_incomplete_no_facts
            and not legacy_no_support
            and not coverage_incomplete_facts
            and not instrumentation_failures
        ):
            details_parts.append(
                "context_support: all required atomic facts mentioned and grounded"
            )
    else:
        details_parts.extend(fact_failures)

    evidence_refs = [ev.handle_id for ev in artifact.resolved_evidence]

    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity=severity,
        details="; ".join(details_parts),
        evidence_refs=evidence_refs,
        classification=classification,
    )


def _highest_severity(facts: list[AtomicExpectedFact]) -> str:
    """Return the highest severity among failing facts.

    Severity ordering: high > medium > low.
    """
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    highest = "low"
    for fact in facts:
        if fact.severity in severity_rank and severity_rank[fact.severity] > severity_rank[highest]:
            highest = fact.severity
    return highest
