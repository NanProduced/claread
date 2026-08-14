from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, StrictBool, StrictStr

# Shared typed Literal for loader-owned
# atomic_facts provenance. This is the SINGLE source of truth — the
# case PrivateAttr AND the public ``atomic_facts_origin`` property both
# use this Literal. The ``real_phase1`` preflight guard accepts ONLY
# ``"explicit"``; any other value fail-closes BEFORE the model builder
# or provider is called.
#
# Values:
# - ``"explicit"``: the case file's ``expected.atomic_facts`` key was
#   present with at least one entry (the dataset author explicitly
#   authored atomic_facts). This is the ONLY value the preflight
#   accepts for ``real_phase1`` runs.
# - ``"legacy_migrated"``: the case file had no/empty
#   ``expected.atomic_facts`` AND non-empty
#   ``expected.required_article_facts`` — the loader auto-migrated
#   legacy facts to atomic_facts. The preflight guard REJECTS this
#   value for ``real_phase1`` runs (fail-closed).
AtomicFactsOrigin = Literal["explicit", "legacy_migrated"]


class AtomicExpectedFact(BaseModel):
    """One atomic expected fact for the context_support evaluator.

    Spec: `.trae/specs/reader-record-ask-r4-a3-rework-session-eval-closure/
    spec.md` — Requirement: context_support atomic fact contract.

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

    ``required`` is now
    :class:`StrictBool` — rejects ``"false"`` / ``"true"`` / ``0`` /
    ``1`` / ``0.0`` / ``1.0``. The previous lenient ``bool`` allowed
    ``required=0`` to silently coerce to ``required=False``, weakening
    the contract.

    The ``origin`` field has been REMOVED from this
    model. Provenance is now LOADER-OWNED — see
    the case's ``atomic_facts_origin`` property. Dataset JSON
    authors CANNOT declare or forge provenance on individual
    :class:`AtomicExpectedFact` entries; only the loader decides
    provenance by inspecting the raw JSON (does the case file declare
    ``expected.atomic_facts`` explicitly, or does it rely on the
    loader's auto-migration from ``required_article_facts``?).
    """

    model_config = {"extra": "forbid"}

    fact_id: str
    answer_alias_groups: list[list[str]] = Field(default_factory=list)
    source_aliases: list[str] = Field(default_factory=list)
    required: StrictBool = True
    severity: Literal["high", "medium", "low"] = "high"


class ReaderRecordAskExpected(BaseModel):
    """Per-case expected facts for the reader-record-ask eval.

    Each field maps to one or more of the 11 evaluator dimensions.
    Fields are intentionally permissive (defaults) so a case only declares
    the constraints it actually wants to assert.
    """

    # exhaustive_completeness: type -> expected entity set.
    #
    # Each entity entry may use ``|``-separated alias
    # lists (e.g. ``"Thunder Bay|雷霆湾|桑德贝"``). Any alias in the
    # list matching the final_text counts as a hit. This mirrors the
    # ``entity_catalog`` alias contract so recall and precision share
    # the same alias vocabulary.
    expected_entity_set: dict[str, list[str]] = Field(default_factory=dict)
    # exhaustive_completeness: explicit recall scope.
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
    # :class:`StrictBool` rejects
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
    # instruction_following: explicit subquestion
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
    # ``allow_subquestions`` is a :class:`StrictBool`.
    allow_subquestions: StrictBool = False
    # entity_precision: type -> allowed entity set (legacy field — still
    # respected by the evaluator; ``entity_catalog`` below is the
    # preferred typed catalog going forward).
    allowed_entities_by_type: dict[str, list[str]] = Field(default_factory=dict)
    # entity_precision: typed entity catalog. Supersedes
    # ``allowed_entities_by_type`` for the type-confusion check. When
    # present, the evaluator uses this to detect non-city entities
    # leaking into a city answer (e.g. region "纽约州西部部分地区"
    # listed as a city).
    entity_catalog: dict[str, list[str]] = Field(default_factory=dict)
    # context_support: atomic facts with alias groups. Supersedes
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


class ReaderRecordAskCase(BaseModel):
    """A single reader-record-ask eval case."""

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
    expected: ReaderRecordAskExpected
    tags: list[str] = Field(default_factory=list)
    # Explicit phase manifest. Each case declares which phases it
    # belongs to. Recognized tags:
    # - ``real_phase1``: candidate for initial real-model runs
    # - ``offline_only``: evaluator-only; never selected for real-model
    #   runs (used for ``known_bbc`` cases until the
    #   trusted-source-metadata injection seam)
    # - ``targeted_phase2_candidate``: expected to fail an initial run and
    #   enter a targeted follow-up run
    phase_tags: list[str] = Field(default_factory=list)
    # Explicit, auditable model-visible fixture
    # identity for real-BBC cases. When present, the harness preflight
    # verifies that the runtime envelope's ``envelope_fingerprint``
    # matches ``expected_envelope_fingerprint`` EXACTLY before any model
    # builder is invoked or provider call is made. Mismatch → fail-
    # closed (calls=0, builder=0). The aggregate also verifies each
    # artifact's ``envelope_fingerprint`` matches the declared expected
    # value; mismatch → ``blocked_incomplete_real_model_run``.
    #
    # This closes the audit finding where a BBC runtime record's
    # model-visible baseline chunks contained ``2015`` but the dataset's
    # ``allowed_temporal_claims`` was empty, causing the evaluator to
    # misjudge a body-supported year as a hallucination. By binding the
    # runtime to an auditable, deterministic identity, the dataset
    # author commits to a specific base_content_sha256 / record_id /
    # generation combination, and any drift (re-base, re-generation,
    # different record) is caught BEFORE paid calls are made.
    #
    # Synthetic cases may also declare this field; the harness computes
    # the envelope_fingerprint deterministically from
    # ``base_content_sha256 = sha256(article_text)`` and fixed UUIDs, so
    # the declared value can be computed offline and verified at
    # preflight.
    #
    # ``None`` (default) preserves backwards compat with cases authored
    # before this identity contract — no preflight check is performed, no aggregate
    # check is performed. New cases SHOULD declare this field.
    expected_envelope_fingerprint: StrictStr | None = None

    # True model-visible fixture identity. Deterministic
    # SHA-256 over ``baseline_status + is_complete + ordered
    # (chunk_ordinal, chunk_text)``. Excludes random evidence handle_ids,
    # absolute paths, record UUIDs, base_ids, stable_document_ids, and
    # timestamps. Two assemblies from the same snapshot produce the
    # same hash; chunk content / order / truncation / coverage changes
    # produce a different hash.
    #
    # Contract:
    # - Required for ``real_phase1`` cases with ``source_kind =
    #   "bbc_record"``. Missing / empty / mismatch → harness preflight
    #   fail-closed (``pytest.skip`` BEFORE model builder, calls=0,
    #   builder=0).
    # - Optional for ``synthetic_short`` / ``synthetic_medium_long``
    #   cases (the harness computes the fingerprint deterministically
    #   from ``article_text``, so the declared value can be computed
    #   offline and verified at preflight). Synthetic cases SHOULD
    #   declare this field.
    # - Optional for ``offline_only`` cases (backwards compat — these
    #   cases never enter the real-model run path, so no preflight
    #   check fires).
    #
    # The aggregate performs a three-layer check:
    #   dataset expected == manifest identity == artifact actual.
    # Missing / mixed / mismatch / foreign identity → typed blocker
    # (``blocked_incomplete_real_model_run``), NOT display-only.
    #
    # Supersedes ``expected_envelope_fingerprint`` as the final
    # identity contract. ``expected_envelope_fingerprint`` is retained
    # for backwards compatibility with older cases; the
    # harness checks BOTH when both are present (defense-in-depth).
    expected_runtime_fixture_fingerprint: StrictStr | None = None

    # Loader-owned
    # provenance for atomic_facts.
    #
    # This is a Pydantic ``PrivateAttr`` — it is NOT parsed from JSON
    # and CANNOT be set by dataset authors. Only the loader sets it,
    # by inspecting the raw JSON dict BEFORE Pydantic parsing:
    #
    # - ``"explicit"``: the case file's ``expected.atomic_facts`` key
    #   was present with at least one entry (the dataset author
    #   explicitly authored atomic_facts).
    # - ``"legacy_migrated"``: the case file had no/empty
    #   ``expected.atomic_facts`` AND non-empty
    #   ``expected.required_article_facts`` — the loader auto-migrated
    #   legacy facts to atomic_facts.
    # - ``"explicit"`` (default): for backwards compat with cases that
    #   have no atomic_facts AND no required_article_facts (the
    #   preflight guard's "no atomic_facts" check handles these).
    #
    # The ``real_phase1`` preflight guard reads this typed field via
    # the :attr:`atomic_facts_origin` property to fail-closed BEFORE
    # paid calls when a case relies on legacy auto-migration. Dataset
    # JSON authors CANNOT forge ``"explicit"`` provenance — the field
    # is not in the JSON schema, not parsed, and not settable via
    # ``model_validate``. This closes the audit finding where a
    # dataset author could set ``origin="explicit"`` on individual
    # AtomicExpectedFact entries to bypass the guard.
    #
    # The field type is the shared
    # :data:`AtomicFactsOrigin` Literal (was ``str``). This removes
    # the ``# type: ignore[return-value]`` from the property — the
    # PrivateAttr and the property now share the SAME typed Literal,
    # so the property return is type-safe without coercion. The
    # loader is still the only writer; the typed Literal is the
    # single source of truth.
    #
    # The field is a ``PrivateAttr`` (not a regular field) so it:
    #   1. Is excluded from ``model_dump()`` / ``model_dump_json()``
    #      (does not enter the dataset identity hash, does not enter
    #      artifact JSON).
    #   2. Is NOT parsed from input JSON (dataset authors cannot set
    #      it).
    #   3. Defaults to ``"explicit"`` for backwards compat with cases
    #      constructed directly in tests (e.g., the case model
    #      without going through the loader).
    _atomic_facts_origin: AtomicFactsOrigin = PrivateAttr(default="explicit")

    @property
    def atomic_facts_origin(self) -> AtomicFactsOrigin:
        """Loader-owned typed provenance for this case's atomic_facts.

        See the class docstring on ``_atomic_facts_origin`` for the
        full contract. Reads return the shared
        :data:`AtomicFactsOrigin` Literal; the loader is the only
        writer.

        The return type is the shared
        :data:`AtomicFactsOrigin` Literal (matching the PrivateAttr
        type). The previous ``# type: ignore[return-value]`` is
        REMOVED — both sides use the same typed Literal, so the
        return is type-safe without coercion.
        """
        return self._atomic_facts_origin


class ReaderRecordAskDataset(BaseModel):
    """Top-level reader-record-ask dataset manifest."""

    id: str = "reader-record-ask-r4-a3"
    schema_version: str = "r4-a3-dataset-v1"
    description: str = ""
    case_globs: list[str] = Field(default_factory=lambda: ["cases/*.json"])
    tags: list[str] = Field(default_factory=list)
    cases: list[ReaderRecordAskCase] = Field(default_factory=list)
