"""Dimension 6/11 — exhaustive_completeness.

Spec: for each type in ``expected_entity_set``, compute set recall =
|appeared in final_text| / |expected|. recall < 1.0 ⇒ high-severity
failure, details listing the missing entities (e.g. Thunder Bay).
Pairs with ``entity_precision`` (type purity) but is scored
independently.

Explicit recall scope contract
=================================================

The previous implementation unconditionally required every entity in
``expected_entity_set`` to appear in the answer. This wrongly failed
``main_idea`` / ``core_viewpoint`` / ``author_intent`` /
``argument_structure`` cases that happen to declare an entity set but
where the user question did NOT ask for an exhaustive enumeration.

The new contract:

- Recall is enforced ONLY when
  ``requires_exhaustive_entity_recall``
  is ``True``. The default is ``False``.
- The scope flag MUST be set explicitly by the case author. The
  evaluator does NOT infer it from ``question_category``, suggestion
  text, or keywords.
- Entity entries support ``|``-separated alias lists
  (e.g. ``"Thunder Bay|雷霆湾|桑德贝"``). Any alias in the list matching
  the final_text counts as a hit — the same alias vocabulary used by
  the typed ``entity_catalog``. This ensures ``雷霆湾`` matches
  ``Thunder Bay`` rather than being counted as missing.
- When ``requires_exhaustive_entity_recall`` is ``False``, the
  dimension passes vacuously (informational details noting that recall
  is not required). It must NOT fail, regardless of how many entities
  are missing.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "exhaustive_completeness"


def _entity_aliases(entity_spec: str) -> list[str]:
    """Split an entity spec on ``|`` into a list of aliases.

    ``"Thunder Bay|雷霆湾|桑德贝"`` → ``["Thunder Bay", "雷霆湾", "桑德贝"]``.
    A plain entity without ``|`` → ``["Thunder Bay"]``.
    Aliases are stripped of surrounding whitespace; empty aliases are
    dropped (so ``"Thunder Bay|"`` → ``["Thunder Bay"]``).
    """
    if not entity_spec:
        return []
    return [a.strip() for a in entity_spec.split("|") if a.strip()]


def _entity_in_text(entity_spec: str, text: str) -> bool:
    """Return True if any alias of ``entity_spec`` appears in ``text``.

    Case-insensitive matching for Latin-script aliases; Chinese aliases
    are matched verbatim (case-folding is a no-op for CJK).
    """
    aliases = _entity_aliases(entity_spec)
    if not aliases:
        return False
    text_lower = text.lower()
    for alias in aliases:
        # Latin aliases: case-insensitive. CJK aliases: lower() is a
        # no-op so this is safe for both.
        if alias.lower() in text_lower:
            return True
    return False


def evaluate_exhaustive_completeness(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
) -> EvalDimensionResult:
    final_text = artifact.final_text or ""
    expected_set = case.expected.expected_entity_set

    # Only enforce exhaustive recall when the case
    # author has explicitly opted in. The previous implementation
    # unconditionally required every entity to appear, which wrongly
    # failed main_idea / core_viewpoint / author_intent /
    # argument_structure cases whose user question did not ask for an
    # exhaustive enumeration.
    if not case.expected.requires_exhaustive_entity_recall:
        return EvalDimensionResult(
            dimension=DIMENSION,
            passed=True,
            severity="none",
            details=(
                "exhaustive_completeness: recall not required "
                "(requires_exhaustive_entity_recall=False)"
            ),
            evidence_refs=[],
        )

    failures: list[str] = []
    for type_name, entities in expected_set.items():
        if not entities:
            continue
        appeared = [e for e in entities if _entity_in_text(e, final_text)]
        missing = [e for e in entities if not _entity_in_text(e, final_text)]
        if missing:
            recall = len(appeared) / len(entities)
            failures.append(
                f"{type_name} recall={recall:.2f} missing={missing}"
            )

    passed = not failures
    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "high",
        details=(
            "exhaustive_completeness: all expected entities present"
            if passed
            else "; ".join(failures)
        ),
        evidence_refs=[],
    )
