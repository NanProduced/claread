"""Tests for entity_precision evaluator (P0-7 typed entity catalog).

Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
spec.md` — Requirement: entity_precision typed entity catalog（P0-7）.

Covers:
- Legacy ``allowed_entities_by_type`` contract still works (backwards compat).
- New ``entity_catalog`` contract (preferred):
  - ``|``-separated aliases within an entity entry.
  - Region-as-city type confusion detected (core P0-7 regression).
  - Shared-alias non-confusion (entity allowed under multiple types).
  - Capability boundary signal ``unclassified_external_entity`` recorded
    in details when catalog is non-empty (cannot detect external
    entities without NER).
- LLM judge contract: may supplement but never flip a deterministic
  ``passed=False`` to ``True``.
- Non-entity question category skips the type-confusion check.
"""

from __future__ import annotations

from claread_eval.reader_record_ask.evaluators.artifact import RawArtifact
from claread_eval.reader_record_ask.evaluators.entity_precision import (
    evaluate_entity_precision,
)
from claread_eval.reader_record_ask.schema import (
    ReaderRecordAskR4A3Case,
    ReaderRecordAskR4A3Expected,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case(
    *,
    allowed_by_type: dict[str, list[str]],
) -> ReaderRecordAskR4A3Case:
    """Build a case using the LEGACY ``allowed_entities_by_type`` field.

    Kept so we can assert backwards compatibility — the evaluator falls
    back to this field when ``entity_catalog`` is empty.
    """
    return ReaderRecordAskR4A3Case(
        id="t-entity-precision",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category="city_enumeration",
        expected=ReaderRecordAskR4A3Expected(
            allowed_entities_by_type=allowed_by_type,
        ),
    )


def _make_case_with_catalog(
    *,
    entity_catalog: dict[str, list[str]],
    question_category: str = "city_enumeration",
    question: str = "文章提到了哪些城市？",
    case_id: str = "t-entity-precision-catalog",
) -> ReaderRecordAskR4A3Case:
    """Build a case using the NEW ``entity_catalog`` field (P0-7)."""
    return ReaderRecordAskR4A3Case(
        id=case_id,
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question=question,
        question_category=question_category,
        expected=ReaderRecordAskR4A3Expected(
            entity_catalog=entity_catalog,
        ),
    )


def _make_artifact(final_text: str) -> RawArtifact:
    return RawArtifact(
        case_id="t-entity-precision",
        run_id="run-1",
        finalized_status="ok",
        final_text=final_text,
    )


# ---------------------------------------------------------------------------
# Legacy contract — backwards compatibility (must still pass)
# ---------------------------------------------------------------------------


def test_positive_entities_type_correct() -> None:
    case = _make_case(
        allowed_by_type={
            "city": ["Thunder Bay", "Toronto"],
            "region": ["安大略省"],
        },
    )
    artifact = _make_artifact("文章提到的城市包括 Thunder Bay 和 Toronto。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_negative_type_confusion_region_into_city() -> None:
    # "纽约州西部" is declared as a region entity but appears in the
    # city answer — type confusion.
    case = _make_case(
        allowed_by_type={
            "city": ["Thunder Bay", "Toronto"],
            "region": ["纽约州西部"],
        },
    )
    artifact = _make_artifact(
        "城市包括 Thunder Bay、Toronto 和纽约州西部。"
    )
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "纽约州西部" in result.details
    assert "type confusion" in result.details


def test_llm_judge_does_not_override_deterministic_failure() -> None:
    case = _make_case(
        allowed_by_type={
            "city": ["Thunder Bay"],
            "region": ["纽约州西部"],
        },
    )
    artifact = _make_artifact("城市有 Thunder Bay 和纽约州西部。")

    def _positive_judge(text: str, ctx: dict) -> dict:  # noqa: ARG001
        return {"note": "all entities look reasonable", "reject": []}

    result = evaluate_entity_precision(case, artifact, llm_judge=_positive_judge)
    assert result.passed is False
    assert result.severity == "high"
    assert "type confusion" in result.details


def test_llm_judge_called_when_no_deterministic_failure() -> None:
    case = _make_case(allowed_by_type={"city": ["Thunder Bay"]})
    artifact = _make_artifact("城市包括 Thunder Bay。")

    captured: list[bool] = []

    def _judge(text: str, ctx: dict) -> dict:  # noqa: ARG001
        captured.append(True)
        return {"note": "no unknown entities"}

    result = evaluate_entity_precision(case, artifact, llm_judge=_judge)
    assert result.passed is True
    assert result.llm_judge_used is True
    assert result.llm_judge_note == "no unknown entities"
    assert captured == [True]


def test_non_entity_question_skips_type_confusion() -> None:
    case = ReaderRecordAskR4A3Case(
        id="t-entity-precision-main",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="这篇文章主要说了什么？",
        question_category="main_idea",
        expected=ReaderRecordAskR4A3Expected(
            allowed_entities_by_type={"city": ["Thunder Bay"]},
        ),
    )
    artifact = _make_artifact("文章讨论了 Thunder Bay 的绿化。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# P0-7 regression: entity_catalog with |-separated aliases
# ---------------------------------------------------------------------------


def test_entity_catalog_aliases_recognize_either_form() -> None:
    """``"Buffalo|布法罗"`` means either ``Buffalo`` or ``布法罗`` is
    recognized as that entity. The answer may use either alias.
    """
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Buffalo|布法罗", "Thunder Bay|桑德贝"],
            "region": ["纽约州西部部分地区"],
        },
    )
    # Answer uses both Chinese aliases — should PASS.
    artifact = _make_artifact("文章提到的城市包括 布法罗 和 桑德贝。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True
    assert result.severity == "none"


def test_entity_catalog_aliases_case_insensitive_ascii() -> None:
    """ASCII aliases match case-insensitively."""
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Buffalo|布法罗"],
            "region": ["纽约州"],
        },
    )
    artifact = _make_artifact("文章提到的城市包括 BUFFALO。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True


def test_entity_catalog_region_as_city_detected() -> None:
    """P0-7 core regression: region entity ``"纽约州西部部分地区"``
    appearing in a city answer is detected as type confusion.

    This is the failure case the previous implementation could NOT
    catch (the BBC case declared only ``city`` type, so non-city
    entities were invisible).
    """
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Thunder Bay|桑德贝", "Toronto|多伦多"],
            "region": ["纽约州西部部分地区", "纽约州"],
            "province": ["安大略省"],
        },
    )
    # Answer lists the region "纽约州西部部分地区" as a city — type confusion.
    artifact = _make_artifact(
        "城市包括 Thunder Bay、Toronto 和纽约州西部部分地区。"
    )
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is False
    assert result.severity == "high"
    assert "type confusion" in result.details
    assert "纽约州西部部分地区" in result.details
    assert "region" in result.details


def test_entity_catalog_multiple_non_city_types_detected() -> None:
    """Multiple non-city types leaking into a city answer are ALL reported."""
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Thunder Bay"],
            "region": ["纽约州西部"],
            "province": ["安大略省"],
        },
    )
    artifact = _make_artifact(
        "城市有 Thunder Bay、纽约州西部 和 安大略省。"
    )
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is False
    assert "纽约州西部" in result.details
    assert "安大略省" in result.details


def test_entity_catalog_completeness_and_precision_separate() -> None:
    """Spec: "completeness 与 precision 分开计算".

    entity_precision only flags type-confusion (entity of a DIFFERENT
    type appears in answer). It does NOT fail when an expected city is
    missing — that is the exhaustive_completeness dimension's job.

    Here the answer lists only one of two expected cities, but no
    non-city entity leaks in → entity_precision passes.
    """
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Thunder Bay", "Toronto"],
            "region": ["纽约州"],
        },
    )
    # Answer mentions only Thunder Bay (Toronto missing) — but no
    # type confusion. entity_precision should PASS; completeness is
    # a separate dimension.
    artifact = _make_artifact("文章提到的城市包括 Thunder Bay。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# P0-7: shared alias (entity allowed under multiple types) — non-confusion
# ---------------------------------------------------------------------------


def test_shared_alias_not_flagged_as_confusion() -> None:
    """When an entity alias is declared under BOTH the asked type and a
    different type, the appearance of that alias in the answer is NOT
    type confusion.

    Example: ``"Thunder Bay"`` may be declared as both a ``city`` and a
    ``region`` (perhaps a metropolitan area). The answer mentions
    ``"Thunder Bay"`` as a city — we should not flag this just because
    it also appears in the ``region`` catalog.
    """
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Thunder Bay"],
            "region": ["Thunder Bay"],  # same alias, different type
        },
    )
    artifact = _make_artifact("城市包括 Thunder Bay。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True


def test_shared_alias_via_pipe_not_flagged_as_confusion() -> None:
    """Same shared-alias concept, but with ``|``-separated aliases."""
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Thunder Bay|桑德贝"],
            "region": ["Thunder Bay|桑德贝"],  # shared alias
        },
    )
    artifact = _make_artifact("城市包括 桑德贝。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True


# ---------------------------------------------------------------------------
# P0-7: capability boundary signal (unclassified_external_entity)
# ---------------------------------------------------------------------------


def test_capability_boundary_signal_when_catalog_nonempty() -> None:
    """When the catalog is non-empty and question asks for a typed entity
    list, the evaluator reports ``unclassified_external_entity`` as a
    capability boundary signal in details.

    This is a SOFT signal — it does NOT flip ``passed`` to ``False``.
    The aggregator treats it as informational.
    """
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Thunder Bay"],
            "region": ["纽约州"],
        },
    )
    artifact = _make_artifact("城市包括 Thunder Bay。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True
    assert "unclassified_external_entity" in result.details
    assert "capability_boundary" in result.details


def test_no_capability_boundary_signal_when_no_catalog() -> None:
    """When the catalog is empty, no capability boundary signal is emitted."""
    case = _make_case_with_catalog(entity_catalog={})
    artifact = _make_artifact("城市包括 Thunder Bay。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True
    assert "unclassified_external_entity" not in result.details


def test_no_capability_boundary_signal_when_non_entity_question() -> None:
    """When the question category has no entity-type mapping (e.g.
    main_idea), the type-confusion check is skipped and no capability
    boundary signal is emitted.
    """
    case = _make_case_with_catalog(
        entity_catalog={
            "city": ["Thunder Bay"],
            "region": ["纽约州"],
        },
        question_category="main_idea",
        question="这篇文章主要说了什么？",
    )
    artifact = _make_artifact("文章讨论了 Thunder Bay。")
    result = evaluate_entity_precision(case, artifact)
    assert result.passed is True
    assert "unclassified_external_entity" not in result.details
    assert "no asked_type mapping" in result.details


# ---------------------------------------------------------------------------
# P0-7: precedence — entity_catalog wins over legacy field
# ---------------------------------------------------------------------------


def test_entity_catalog_precedence_over_legacy_field() -> None:
    """When BOTH ``entity_catalog`` and ``allowed_entities_by_type`` are
    declared, ``entity_catalog`` wins (it is the preferred field).
    """
    case = ReaderRecordAskR4A3Case(
        id="t-entity-precision-precedence",
        source_kind="synthetic_short",
        input_mode="manual",
        source_metadata="unknown",
        baseline_mode="complete",
        question="文章提到了哪些城市？",
        question_category="city_enumeration",
        expected=ReaderRecordAskR4A3Expected(
            # Legacy field declares only city — would NOT catch
            # region-as-city confusion.
            allowed_entities_by_type={"city": ["Thunder Bay"]},
            # New field declares region as well — DOES catch confusion.
            entity_catalog={
                "city": ["Thunder Bay"],
                "region": ["纽约州西部"],
            },
        ),
    )
    artifact = _make_artifact("城市有 Thunder Bay 和纽约州西部。")
    result = evaluate_entity_precision(case, artifact)
    # entity_catalog wins, so type confusion IS detected.
    assert result.passed is False
    assert "纽约州西部" in result.details
    assert "type confusion" in result.details
