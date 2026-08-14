"""Dimension 5/11 — entity_precision.

Requirement: entity_precision typed entity catalog.

Previous implementation only checked "other declared types" against
``allowed_entities_by_type``. When a BBC case declared only the
``city`` type, region entities like ``"纽约州西部部分地区"`` were
invisible — the evaluator silently reported precision=100% even when
the answer listed regions as cities.

The new contract uses :attr:`entity_catalog` (preferred) or falls
back to legacy :attr:`allowed_entities_by_type`:

- ``entity_catalog: dict[str, list[str]]`` maps entity TYPE to a list
  of entities. Each entity entry may use ``|`` to separate aliases:
  ``"Buffalo|布法罗"`` means either form is recognized as that entity.
- For ``city_enumeration`` questions:
  - Any entity of a NON-city type appearing in the answer → FAIL
    (type confusion).
  - Expected city set recall is handled by
    :mod:`exhaustive_completeness`, NOT this dimension. This keeps
    completeness and precision separate.
  - The catalog should include explicit negative examples like
    ``region: ["纽约州西部部分地区", "纽约州"]`` so the type-confusion
    check catches region-as-city errors.
- For entities in the answer that are NOT in any catalog type → the
  evaluator reports ``unclassified_external_entity`` as a capability
  boundary signal in details, but does NOT silently claim
  precision=100%. Detecting unclassified entities requires NER,
  which is out of scope for this deterministic evaluator.

LLM judge contract (unchanged): may only *supplement*; cannot flip
a deterministic ``passed=False`` to ``True``.
"""

from __future__ import annotations

from collections.abc import Callable

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.result import EvalDimensionResult
from claread_eval.reader_record_ask.schema import ReaderRecordAskCase

DIMENSION = "entity_precision"

# Maps question_category → the entity type the question is asking about.
# Only categories whose answer is expected to be an entity list need a
# mapping; other categories skip the type-confusion check.
QUESTION_CATEGORY_TO_TYPE: dict[str, str] = {
    "city_enumeration": "city",
}

#: Alias separator within a catalog entity entry.
#: ``"Buffalo|布法罗"`` means either ``Buffalo`` or ``布法罗`` is
#: recognized as that entity.
_ALIAS_SEPARATOR = "|"

#: Capability boundary signal included in details when the catalog is
#: non-empty but the evaluator cannot detect entities outside the
#: catalog (would require NER). The aggregator treats this as a soft
#: signal — it does NOT flip ``passed`` to ``False``.
_UNCLASSIFIED_ENTITY_SIGNAL = (
    "entity_precision: capability_boundary=unclassified_external_entity "
    "detection requires NER; entities outside the catalog are not checked"
)


def _entity_aliases(entity_entry: str) -> list[str]:
    """Split an entity entry into its alias list.

    ``"Buffalo|布法罗"`` → ``["Buffalo", "布法罗"]``.
    ``"Thunder Bay"`` → ``["Thunder Bay"]``.
    Empty aliases are dropped.
    """
    if not entity_entry:
        return []
    return [alias.strip() for alias in entity_entry.split(_ALIAS_SEPARATOR) if alias.strip()]


def _entity_appears_in_text(entity_entry: str, text: str) -> bool:
    """Return ``True`` if ANY alias of ``entity_entry`` is in ``text``.

    Case-insensitive for ASCII aliases; CJK aliases match exactly
    (Chinese has no case distinction).
    """
    aliases = _entity_aliases(entity_entry)
    if not aliases:
        return False
    text_lower = text.lower()
    for alias in aliases:
        # For ASCII aliases, lower-case both sides. For CJK aliases,
        # lower() is a no-op so this still works.
        if alias.lower() in text_lower:
            return True
    return False


def _resolve_catalog(case: ReaderRecordAskCase) -> dict[str, list[str]]:
    """Return the typed entity catalog to use.

    Prefers :attr:`entity_catalog` (new contract). Falls back to
    :attr:`allowed_entities_by_type` (legacy) when the new field is
    empty.
    """
    if case.expected.entity_catalog:
        return case.expected.entity_catalog
    return case.expected.allowed_entities_by_type


def evaluate_entity_precision(
    case: ReaderRecordAskCase,
    artifact: RawArtifact,
    llm_judge: Callable[[str, dict], dict] | None = None,
) -> EvalDimensionResult:
    """Evaluate whether the answer commits entity type confusion.

    Type confusion = an entity declared as a DIFFERENT type from the
    asked type appears in the answer. Example: ``region`` entity
    ``"纽约州西部部分地区"`` appears in a ``city_enumeration`` answer.

    See module docstring for the full contract.
    """
    final_text = artifact.final_text or ""
    catalog = _resolve_catalog(case)

    failures: list[str] = []
    llm_note: str | None = None
    llm_used = False

    asked_type = QUESTION_CATEGORY_TO_TYPE.get(case.question_category)

    if asked_type and asked_type in catalog:
        asked_allowed_entries = catalog[asked_type]
        # Build a set of all aliases declared as the asked type, so we
        # can skip type-confusion for entities that ARE in the asked
        # type's list (regardless of which alias matched).
        asked_aliases = {
            alias.lower()
            for entry in asked_allowed_entries
            for alias in _entity_aliases(entry)
        }

        # Type confusion: an entity of a DIFFERENT type appears in
        # final_text. The model has leaked a non-asked-type entity
        # into the answer.
        for other_type, entities in catalog.items():
            if other_type == asked_type:
                continue
            for entity_entry in entities:
                if not _entity_appears_in_text(entity_entry, final_text):
                    continue
                # The entity appears in the answer. Check if ANY of
                # its aliases are in the asked type's allowed set
                # (this would be a "shared alias" — the entity is
                # allowed under multiple types; we treat that as
                # non-confusion to avoid false positives).
                entry_aliases = [a.lower() for a in _entity_aliases(entity_entry)]
                if any(alias in asked_aliases for alias in entry_aliases):
                    continue
                # Type confusion detected.
                failures.append(
                    f"type confusion: entity {entity_entry!r} "
                    f"(type={other_type!r}) appears in answer but is "
                    f"not in {asked_type!r} catalog"
                )

    # Capability boundary signal: when the catalog is non-empty, the
    # evaluator can only verify entities WITHIN the catalog. Entities
    # in the answer that are NOT in any catalog type go undetected.
    # We do NOT silently claim precision=100% — we record the boundary.
    capability_boundary_active = bool(catalog) and asked_type is not None

    # LLM judge hook: only called when there are NO deterministic
    # failures, and may only RECORD a note — cannot flip passed.
    if llm_judge is not None and not failures:
        llm_used = True
        try:
            result = llm_judge(
                final_text,
                {
                    "catalog": catalog,
                    "asked_type": asked_type,
                },
            )
            if isinstance(result, dict):
                llm_note = result.get("note", str(result))
        except Exception as exc:  # noqa: BLE001 — judge must never crash the eval
            llm_note = f"llm_judge error: {type(exc).__name__}"

    passed = not failures

    details_parts: list[str] = []
    if passed:
        if asked_type is None:
            details_parts.append(
                f"entity_precision: question_category={case.question_category!r} "
                f"has no asked_type mapping; type-confusion check skipped"
            )
        else:
            details_parts.append("entity_precision: all catalog entities type-correct")
    else:
        details_parts.extend(failures)

    if capability_boundary_active:
        details_parts.append(_UNCLASSIFIED_ENTITY_SIGNAL)

    return EvalDimensionResult(
        dimension=DIMENSION,
        passed=passed,
        severity="none" if passed else "high",
        details="; ".join(details_parts),
        evidence_refs=[],
        llm_judge_used=llm_used,
        llm_judge_note=llm_note,
    )
