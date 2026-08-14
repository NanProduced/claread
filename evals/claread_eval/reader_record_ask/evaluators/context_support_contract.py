"""Shared contract for the ``context_support`` classification tag.

Context-support contract de-duplication.

This module is the SINGLE source of truth for the
``context_support`` classification vocabulary. Three call sites
previously held their own private copies of the same string
constants and frozensets:

- :mod:`evaluators.context_support` (the evaluator that *emits*
  classification tags)
- :mod:`evaluators.aggregator` (consumes tags to route failure
  clusters)
- :mod:`scripts.run_reader_record_ask_eval` (consumes tags to
  populate :class:`AggregateReadinessAudit`)

The three copies had to be kept manually in sync — a typo in any
one would let an instrumentation blocker leak through to the normal
accepted/rework path. This module replaces the three copies with
one import.

Module boundary rules (do NOT violate):

- This module MUST NOT import from
  :mod:`evaluators.context_support`, :mod:`evaluators.aggregator`,
  :mod:`evaluators.result`, :mod:`evaluators.artifact`, or
  :mod:`scripts.run_reader_record_ask_eval`. It is a leaf module
  with no evaluator / aggregator / runner dependencies — importing
  it from any of those call sites must not create a cycle.
- This module MUST NOT depend on Pydantic models. It only exports
  ``Literal`` type aliases, plain string constants, and
  ``frozenset`` constants. The Pydantic boundary is enforced in
  :mod:`evaluators.result` via the
  :data:`ContextSupportClassification` Literal.
- The vocabulary is closed: a classification string not in
  :data:`ContextSupportClassification` MUST be rejected at the
  Pydantic model boundary (see
  :class:`evaluators.result.EvalDimensionResult`).

Vocabulary (8 tags, mutually exclusive at emit time):

- ``"legacy_artifact"`` — pre-contract artifact (``version=None,
  status=None``). Replay labels as
  ``indeterminate_requires_new_artifact``; authoritative aggregate
  blocks via :data:`LEGACY_BLOCKER_CLASSIFICATIONS`.
- ``"captured"`` — artifact has full instrumentation
  (``capture_status="captured"``) and the per-fact verdict is
  computed from observations. Emitted as a transient
  classification before per-fact reasons override it; in practice
  the final classification is one of ``supported`` /
  ``fact_not_supported`` / ``fact_not_cited`` /
  ``instrumentation_incomplete``.
- ``"baseline_unavailable"`` — new artifact with
  ``capture_status="unavailable"`` (no baseline context).
  Instrumentation blocker — does NOT enter rework, does NOT
  cluster as ``fact-not-grounded``.
- ``"runtime_exception"`` — new artifact with
  ``capture_status="failed"`` (runtime exception during the run).
  Instrumentation blocker — same semantics as
  ``baseline_unavailable``.
- ``"instrumentation_incomplete"`` — ``captured`` artifact but a
  per-fact invariant was violated (fingerprint mismatch, missing
  observation, duplicate/unknown fact_id, supporting handle not in
  model context). Instrumentation blocker.
- ``"fact_not_supported"`` — ``captured`` artifact, real model
  correctness failure: the fact was NOT supported by any
  model-visible chunk. Enters rework, clusters as
  ``fact-not-grounded``.
- ``"fact_not_cited"`` — ``captured`` artifact, real model
  correctness failure: the fact WAS supported but the model did
  not cite any of the supporting chunks. Enters rework, clusters
  as ``fact-not-grounded``.
- ``"supported"`` — ``captured`` artifact, success: the fact was
  supported by a model-visible chunk AND the model cited at least
  one of the supporting chunks.

The three frozensets partition the 8 tags into the verdict /
cluster routing buckets consumed by the aggregator and the runner:

- :data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` — the three
  instrumentation blocker tags. Verdict →
  ``blocked_incomplete_real_model_run`` (precedence row 9.5).
- :data:`LEGACY_BLOCKER_CLASSIFICATIONS` — the legacy tag.
  Verdict → ``blocked_incomplete_real_model_run`` (precedence
  row 9.5). Kept separate from instrumentation_incomplete because
  legacy artifacts did NOT fail at run time — they predate the
  contract — so the audit field
  ``instrumentation_incomplete_count`` stays semantically narrow.
- :data:`MODEL_FAILURE_CLASSIFICATIONS` — the two real model
  correctness failure tags. Verdict → ``rework`` (precedence
  row 12), cluster → ``fact-not-grounded``.

The ``captured`` and ``supported`` tags are intentionally NOT in
any blocker set — ``captured`` is a transient pre-per-fact
classification that is always overridden before emission, and
``supported`` is a success path that never reaches the verdict
gate as a blocker.
"""

from __future__ import annotations

from typing import Literal

# ---------------------------------------------------------------------------
# Single source of truth — classification vocabulary
# ---------------------------------------------------------------------------

#: Closed vocabulary of ``context_support`` classification tags.
#: Any string not in this Literal MUST be rejected at the Pydantic
#: model boundary (``EvalDimensionResult.classification``).
ContextSupportClassification = Literal[
    "legacy_artifact",
    "captured",
    "baseline_unavailable",
    "runtime_exception",
    "instrumentation_incomplete",
    "fact_not_supported",
    "fact_not_cited",
    "supported",
]

# ---------------------------------------------------------------------------
# Individual string constants (re-exported for ergonomic use at emit
# sites). These are plain ``str`` values that match the Literal members
# above; emitting one of these constants is equivalent to emitting the
# corresponding string literal.
# ---------------------------------------------------------------------------

CLASSIFICATION_LEGACY: ContextSupportClassification = "legacy_artifact"
CLASSIFICATION_CAPTURED: ContextSupportClassification = "captured"
CLASSIFICATION_BASELINE_UNAVAILABLE: ContextSupportClassification = (
    "baseline_unavailable"
)
CLASSIFICATION_RUNTIME_EXCEPTION: ContextSupportClassification = (
    "runtime_exception"
)
CLASSIFICATION_INSTRUMENTATION_INCOMPLETE: ContextSupportClassification = (
    "instrumentation_incomplete"
)
CLASSIFICATION_FACT_NOT_SUPPORTED: ContextSupportClassification = (
    "fact_not_supported"
)
CLASSIFICATION_FACT_NOT_CITED: ContextSupportClassification = "fact_not_cited"
CLASSIFICATION_SUPPORTED: ContextSupportClassification = "supported"

# ---------------------------------------------------------------------------
# Verdict / cluster routing buckets (partition of the 8 tags above)
# ---------------------------------------------------------------------------

#: Instrumentation blocker classifications. These do NOT enter rework,
#: do NOT count as ``confirmed_model_failure``, and do NOT cluster as
#: ``fact-not-grounded``. Verdict → ``blocked_incomplete_real_model_run``
#: (precedence row 9.5 in ``_decide_final_verdict``).
INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS: frozenset[str] = frozenset({
    CLASSIFICATION_BASELINE_UNAVAILABLE,
    CLASSIFICATION_RUNTIME_EXCEPTION,
    CLASSIFICATION_INSTRUMENTATION_INCOMPLETE,
})

#: Legacy blocker classifications. Legacy artifacts (``version=None,
#: status=None``) cannot be authoritatively re-evaluated under the new
#: contract — the authoritative aggregate MUST block them (precedence
#: row 9.5). Kept separate from
#: :data:`INSTRUMENTATION_INCOMPLETE_CLASSIFICATIONS` so the audit
#: field ``instrumentation_incomplete_count`` stays semantically
#: narrow (only the three instrumentation-failure tags).
LEGACY_BLOCKER_CLASSIFICATIONS: frozenset[str] = frozenset({
    CLASSIFICATION_LEGACY,
})

#: Real model correctness failure classifications. These DO enter
#: rework (precedence row 12) and DO cluster as ``fact-not-grounded``.
#: Only ``captured`` artifacts can produce these classifications.
MODEL_FAILURE_CLASSIFICATIONS: frozenset[str] = frozenset({
    CLASSIFICATION_FACT_NOT_SUPPORTED,
    CLASSIFICATION_FACT_NOT_CITED,
})
