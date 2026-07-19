from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool


class AtomicExpectedFact(BaseModel):
    """One atomic expected fact for the context_support evaluator.

    Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
    spec.md` — Requirement: context_support atomic fact contract（P0-6）.

    Replaces the prior ``required_article_facts: list[str]`` contract
    which required a hand-rewritten full sentence to appear verbatim in
    ``final_text`` AND in a 500-char evidence snippet. The new contract:

    - ``answer_alias_groups``: list of alias groups. Each group must be
      "hit" (any alias in the group appears in final_text) for the fact
      to be considered mentioned. Multiple groups = AND; multiple
      aliases within a group = OR.
    - ``source_aliases``: canonical tokens from the article, used by the
      evaluator's evidence-support check (not the 500-char public
      snippet). May be empty when the fact is metadata-only.
    - ``required``: when ``False``, the fact is informational only and
      its absence does not fail the dimension.
    - ``severity``: failure severity when ``required=True`` and the fact
      is not mentioned.

    R4-A4-0 final closure (P1-1): ``required`` is now
    :class:`StrictBool` — rejects ``"false"`` / ``"true"`` / ``0`` /
    ``1`` / ``0.0`` / ``1.0``. The previous lenient ``bool`` allowed
    ``required=0`` to silently coerce to ``required=False``, weakening
    the contract.
    """

    model_config = {"extra": "forbid"}

    fact_id: str
    answer_alias_groups: list[list[str]] = Field(default_factory=list)
    source_aliases: list[str] = Field(default_factory=list)
    required: StrictBool = True
    severity: Literal["high", "medium", "low"] = "high"


class ReaderRecordAskR4A3Expected(BaseModel):
    """Per-case expected facts for the R4-A3 reader-record-ask eval.

    Each field maps to one or more of the 11 R4-A3 evaluator dimensions.
    Fields are intentionally permissive (defaults) so a case only declares
    the constraints it actually wants to assert.
    """

    # exhaustive_completeness: type -> expected entity set.
    #
    # R4-A4-0 (Task 2): each entity entry may use ``|``-separated alias
    # lists (e.g. ``"Thunder Bay|雷霆湾|桑德贝"``). Any alias in the
    # list matching the final_text counts as a hit. This mirrors the
    # ``entity_catalog`` alias contract so recall and precision share
    # the same alias vocabulary.
    expected_entity_set: dict[str, list[str]] = Field(default_factory=dict)
    # exhaustive_completeness (R4-A4-0 Task 2): explicit recall scope.
    # When ``False`` (default), the evaluator does NOT require every
    # entity in ``expected_entity_set`` to appear in the answer. Only
    # when the user question explicitly asks for an exhaustive list
    # (e.g. ``city_enumeration``) should this be set to ``True``.
    # Cases like ``main_idea`` / ``core_viewpoint`` / ``author_intent``
    # / ``argument_structure`` / ``exercise_one`` default to ``False``
    # because the user did not ask for an exhaustive entity enumeration.
    # The evaluator MUST NOT infer the scope from ``question_category``,
    # suggestion text, or keywords — only this explicit field.
    #
    # R4-A4-0 final closure (P1-1): now :class:`StrictBool` — rejects
    # ``"false"`` / ``"true"`` / ``0`` / ``1`` / ``0.0`` / ``1.0``.
    # The previous lenient ``bool`` allowed ``requires_exhaustive_entity_recall=1``
    # to silently coerce to ``True`` even when the case author meant a
    # tag / count, not a boolean.
    requires_exhaustive_entity_recall: StrictBool = False
    # unsupported_temporal_claims: trusted metadata or article evidence allows
    # year/date tokens that the answer may legitimately cite.
    allowed_temporal_claims: list[str] = Field(default_factory=list)
    # numeric_grounding: numeric tokens (string form, e.g. "858", "30")
    allowed_numerics: list[str] = Field(default_factory=list)
    # instruction_following
    requested_count: int | None = None
    requested_count_kind: Literal["exercise_items", "sentences", "none"] = "none"
    # instruction_following (R4-A4-0 Task 3): explicit subquestion
    # permission for ``exercise_one`` cases. When ``False`` (default),
    # an unnumbered single exercise block containing multiple related
    # sub-questions (separated by ``?``) is counted as ONE top-level
    # exercise item — the evaluator must NOT inflate the count by
    # treating each ``?`` as a separate item. When ``True``, the case
    # author explicitly allows compound sub-questions to be counted as
    # separate items.
    #
    # Top-level numbering markers (``1.``, ``2.``, ``Q1``, ``第1题``)
    # always determine the count regardless of this flag — they are
    # the authoritative signal that the model produced N distinct
    # exercises. ``allow_subquestions`` only affects how an unnumbered
    # block with multiple ``?`` is interpreted.
    #
    # R4-A4-0 final closure (P1-1): now :class:`StrictBool`.
    allow_subquestions: StrictBool = False
    # entity_precision: type -> allowed entity set (legacy field — still
    # respected by the evaluator; ``entity_catalog`` below is the
    # preferred typed catalog going forward).
    allowed_entities_by_type: dict[str, list[str]] = Field(default_factory=dict)
    # entity_precision (P0-7): typed entity catalog. Supersedes
    # ``allowed_entities_by_type`` for the type-confusion check. When
    # present, the evaluator uses this to detect non-city entities
    # leaking into a city answer (e.g. region "纽约州西部部分地区"
    # listed as a city).
    entity_catalog: dict[str, list[str]] = Field(default_factory=dict)
    # context_support (P0-6): atomic facts with alias groups. Supersedes
    # ``required_article_facts`` (kept for backwards compat — loader
    # converts old facts to single-alias AtomicExpectedFact entries).
    atomic_facts: list[AtomicExpectedFact] = Field(default_factory=list)
    # context_support (legacy, deprecated): hand-rewritten full
    # sentences that must appear verbatim. Kept so existing cases still
    # load; the loader auto-converts to ``atomic_facts``.
    required_article_facts: list[str] = Field(default_factory=list)
    # answer_success: answer must not contain these patterns
    forbidden_answer_patterns: list[str] = Field(default_factory=list)
    # language_consistency
    answer_language: Literal["zh", "en", "mixed"] = "zh"
    # tool_decision: forbidden=baseline sufficient; optional=may call; required=should call
    expect_tool_calls: Literal["forbidden", "optional", "required"] = "optional"
    # absent_year question: answer must explicitly state "article does not
    # provide year" or equivalent.
    must_declare_no_year: bool = False
    # external_knowledge case: answer must distinguish article content vs external
    must_distinguish_external_knowledge: bool = False


class ReaderRecordAskR4A3Case(BaseModel):
    """A single R4-A3 reader-record-ask eval case."""

    id: str
    source_kind: Literal[
        "synthetic_short",
        "synthetic_medium_long",
        "bbc_record",
    ]
    record_id: str | None = None
    article_text: str | None = None
    article_title: str | None = None
    input_mode: Literal["manual", "suggestion_equivalent", "no_selection"]
    selection: str | None = None
    rag_mode: Literal["off"] = "off"
    source_metadata: Literal["unknown", "known_bbc", "known_synthetic"]
    baseline_mode: Literal["complete", "partial_truncated"]
    external_knowledge_policy: Literal[
        "forbidden",
        "allowed_must_distinguish",
    ] = "forbidden"
    question: str
    question_category: Literal[
        "main_idea",
        "core_viewpoint",
        "author_intent",
        "argument_structure",
        "exercise_one",
        "city_enumeration",
        "publish_date",
        "one_sentence_summary",
        "absent_year",
        "multiple_choice_one",
    ]
    expected: ReaderRecordAskR4A3Expected
    tags: list[str] = Field(default_factory=list)
    # P0-5: explicit phase manifest. Each case declares which phases it
    # belongs to. Recognized tags:
    # - ``real_phase1``: candidate for Phase 1 real-model runs
    # - ``offline_only``: evaluator-only; never selected for real-model
    #   runs (used for ``known_bbc`` cases until R4-A4 lands the
    #   trusted-source-metadata injection seam)
    # - ``targeted_phase2_candidate``: expected to fail in Phase 1 and
    #   enter Phase 2
    phase_tags: list[str] = Field(default_factory=list)


class ReaderRecordAskR4A3Dataset(BaseModel):
    """Top-level R4-A3 reader-record-ask dataset manifest."""

    id: str = "reader-record-ask-r4-a3"
    schema_version: str = "r4-a3-dataset-v1"
    description: str = ""
    case_globs: list[str] = Field(default_factory=lambda: ["cases/*.json"])
    tags: list[str] = Field(default_factory=list)
    cases: list[ReaderRecordAskR4A3Case] = Field(default_factory=list)
