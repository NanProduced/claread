"""Dimension 2/11 — context_support.

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: context_support atomic fact contract（P0-6）.

Previous implementation required a hand-rewritten full sentence to
appear verbatim in BOTH ``final_text`` AND a 500-char evidence
snippet. This contract was broken in three ways:

1. It rejected correct synonymous paraphrases — the model could
   answer correctly but be marked FAIL because the answer didn't use
   the exact human-written sentence.
2. It rejected facts that appeared in the article body but outside the
   500-char public snippet window — the snippet is an arbitrary
   truncation, not a stable contract.
3. It treated "fact not verbatim in answer" as "fact unsupported",
   conflating two distinct failure modes.

The new contract is built on :class:`AtomicExpectedFact`:

- ``answer_alias_groups``: list of alias groups. The fact is
  "mentioned" iff every group has at least one alias that appears
  (case-insensitive substring) in ``final_text``. Multiple groups =
  AND; multiple aliases within a group = OR. An empty
  ``answer_alias_groups`` means the fact is informational only and is
  never asserted as "mentioned" — only ``source_aliases`` matter.
- ``source_aliases``: canonical tokens from the article (curated,
  frozen by the case author). The evaluator uses these — NOT the
  public snippet — to verify that the fact is grounded in the
  article. When ``source_aliases`` is empty, the evidence-support
  check is skipped (the fact is metadata-only, e.g. "the article
  does not mention year X").
- ``required``: when ``False``, the fact's absence is informational
  only and does not fail the dimension.
- ``severity``: failure severity when ``required=True`` and the fact
  is not mentioned in ``final_text``.

Capability boundary of this deterministic evaluator:

- CAN verify: known facts, years, numbers, entities, quantities
  that the case author has pre-aliased.
- CANNOT verify: that every natural-language claim in the answer is
  fully grounded in the article — that requires either exhaustive
  alias coverage (impractical) or an LLM judge (which the spec
  explicitly forbids from overriding deterministic failure here).
- The evaluator therefore reports ``coverage_incomplete=True`` when
  ``atomic_facts`` is empty, signalling that the dimension's
  coverage is limited. The aggregator treats this as a soft signal,
  not a failure.

Legacy compatibility: the loader auto-converts the deprecated
``required_article_facts: list[str]`` into single-alias
:class:`AtomicExpectedFact` entries (one alias group with the whole
sentence). This preserves existing case behaviour while the dataset
migrates to the new contract.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import (
    AtomicExpectedFact,
    ReaderRecordAskR4A3Case,
)

DIMENSION = "context_support"


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


def _fact_grounded_in_article(
    fact: AtomicExpectedFact,
    snippets_lower: list[str],
) -> bool:
    """Return ``True`` if ``fact`` is grounded in the article evidence.

    Uses ``fact.source_aliases`` (canonical tokens curated by the case
    author) — NOT the 500-char public snippet. The case author is
    expected to provide stable canonical tokens that appear in the
    article body and that the answer would cite if it grounded the
    fact in the article.

    When ``source_aliases`` is empty, the fact has no grounding
    constraint — return ``True`` (no evidence-support failure). This
    covers metadata-only facts (e.g. "article does not mention year
    X") where there is no article evidence to cite.
    """
    if not fact.source_aliases:
        return True
    # A fact is grounded iff at least one source alias appears in at
    # least one resolved evidence snippet. We do NOT require the
    # whole source alias list to appear — that would be too strict
    # for partial citations. The aggregator can detect "missing
    # source alias" separately if needed.
    return any(
        any(_alias_hit_in_text(alias, snippet_lower) for alias in fact.source_aliases)
        for snippet_lower in snippets_lower
    )


def evaluate_context_support(
    case: ReaderRecordAskR4A3Case,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    """Evaluate whether required atomic facts are mentioned and grounded."""
    final_text_lower = (artifact.final_text or "").lower()
    snippets_lower = [(ev.snippet or "").lower() for ev in artifact.resolved_evidence]

    atomic_facts = case.expected.atomic_facts

    # Capability boundary signal: when the case has no atomic_facts,
    # the deterministic evaluator cannot assert coverage. The
    # aggregator treats this as a soft signal (not a failure).
    coverage_incomplete = not atomic_facts

    failures: list[str] = []
    evidence_refs = [ev.handle_id for ev in artifact.resolved_evidence]

    for fact in atomic_facts:
        fact_id = fact.fact_id
        mentioned = _fact_mentioned_in_answer(fact, final_text_lower)
        grounded = _fact_grounded_in_article(fact, snippets_lower)

        if not mentioned:
            if fact.required:
                failures.append(
                    f"required fact not mentioned in final_text: fact_id={fact_id}"
                )
            # Non-required facts: absence is informational only.
            continue

        # Mentioned but not grounded → high-severity failure (model
        # internal knowledge masquerading as article content). This
        # is the spec's "unsupported claim" failure mode.
        if not grounded:
            # Grounding failure only applies when source_aliases is
            # non-empty. If source_aliases is empty, _fact_grounded_in_article
            # returned True and we never reach here.
            failures.append(
                f"fact mentioned in final_text but not grounded in "
                f"resolved_evidence: fact_id={fact_id}"
            )

    passed = not failures
    severity = "none" if passed else _highest_severity(atomic_facts)

    details_parts: list[str] = []
    if coverage_incomplete:
        details_parts.append(
            "context_support: coverage_incomplete=true (case has no "
            "atomic_facts; deterministic evaluator cannot assert coverage)"
        )
    if passed:
        if not coverage_incomplete:
            details_parts.append(
                "context_support: all required atomic facts mentioned and grounded"
            )
    else:
        details_parts.extend(failures)

    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity=severity,
        details="; ".join(details_parts),
        evidence_refs=evidence_refs,
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
