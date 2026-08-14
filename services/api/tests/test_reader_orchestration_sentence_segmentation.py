"""Sentence segmentation regression tests.

Covers the fix for record cb62a337-60c4-40a7-8858-35554b5da077 where
``... media in the U.K. It led to ...`` was split into anchors ending
``in the U.`` / ``K.`` because the legacy regex v1 segmenter saw only
the local tail ``u.`` at the first internal period of ``U.K.``.

Contracts under test:
1. Mid-sentence initialisms (U.K., U.S., Ph.D., e.g., i.e., Dr.) are
   never split internally - on BOTH the spaCy main path and the named
   regex v2 fallback.
2. A sentence-FINAL initialism followed by a new sentence
   (``... U.K. It ...``) IS a boundary.
3. The real recorded fragment splits into exactly two sentences.
4. Adjacent boundary classes (decimals, version numbers, URLs,
   quote-final punctuation) remain correct.
5. UTF-16 / non-BMP offsets round-trip for sentence slices.
6. When spaCy / the model is unavailable (or raises at runtime) the
   named regex v2 fallback runs, produces no initialism-internal
   boundary, and its identity is exposed verbatim (no impersonation).
7. Downstream Translation Group planning + snapshot projection on a
   ``U.K. It`` base never emits a bare ``K.`` anchor/group.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

import app.services.reader_orchestration.base_builder as base_builder_module
from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.services.reader_orchestration import (
    AUTO_SEGMENTER_POLICY,
    DETERMINISTIC_SEGMENTER_VERSION,
    MIXED_SEGMENTER_VERSION_SUFFIX,
    REGEX_V2_SEGMENTER_VERSION,
    SENTENCE_PROVIDER_REGEX_V1,
    SENTENCE_PROVIDER_REGEX_V2,
    SENTENCE_PROVIDER_SPACY,
    SPACY_EN_SENTENCE_SEGMENTER_VERSION,
    LowImpactReadingBaseBuildInput,
    build_low_impact_reading_base,
    build_reader_plate_snapshot,
)
from app.services.reader_orchestration.translation_worker import (
    TranslationAnchorSegmentTarget,
    TranslationBatchUnitContext,
    build_deterministic_translation_groups,
)

HARRY_FRAGMENT = (
    "This criticized the royal family and the media in the U.K. "
    "It led to more bad blood between Harry and his family."
)
EXPECTED_HARRY_SENTENCES = [
    "This criticized the royal family and the media in the U.K.",
    "It led to more bad blood between Harry and his family.",
]

# Fragment segments that must NEVER appear as anchors (the bug
# produced exactly these for "... U.K. It ...").
FORBIDDEN_INITIALISM_FRAGMENTS = {"U.", "K.", "S.", "Ph.", "D."}

SNAPSHOT_TAKEN_AT = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _build_result(
    source_text: str,
    *,
    language: str | None = "en",
    segmenter_version: str = AUTO_SEGMENTER_POLICY,
):
    return build_low_impact_reading_base(
        LowImpactReadingBaseBuildInput(
            reading_record_id="record-r71",
            base_id="base-r71",
            source_text=source_text,
            title="R7-1 fixture",
            language=language,
            segmenter_version=segmenter_version,
        )
    )


def _unit_segment_texts(result, unit_index: int = 0) -> list[str]:
    unit_id = result.units[unit_index].unit_id
    segments = sorted(
        (segment for segment in result.anchor_segments if segment.unit_id == unit_id),
        key=lambda segment: segment.unit_order_index,
    )
    return [segment.text for segment in segments]


def _assert_anchor_invariants(result) -> None:
    """Order / non-overlap / whitespace-gap coverage / slice round-trip /
    UTF-16 offsets / canonical hash invariants (requirement 5)."""
    base_text = result.base.text

    # Canonical text is untouched: hash + UTF-16 length match the text.
    assert result.base.content_sha256 == hashlib.sha256(base_text.encode("utf-8")).hexdigest()
    assert result.base.content_utf16_length == utf16_code_unit_length(base_text)

    # Units round-trip and do not overlap.
    previous_unit_end: int | None = None
    for unit in result.units:
        assert (
            slice_by_utf16_offsets(base_text, unit.base_start_utf16, unit.base_end_utf16)
            == unit.text
        )
        if previous_unit_end is not None:
            assert unit.base_start_utf16 >= previous_unit_end
            gap = slice_by_utf16_offsets(base_text, previous_unit_end, unit.base_start_utf16)
            assert gap is None or not gap.strip()
        previous_unit_end = unit.base_end_utf16

    # Global anchor order.
    order_indexes = [segment.order_index for segment in result.anchor_segments]
    assert order_indexes == sorted(order_indexes)
    assert order_indexes == list(range(1, len(order_indexes) + 1))

    # Per-unit anchors: non-overlapping, whitespace-only gaps, slices and
    # UTF-16 offsets round-trip, text hash matches.
    for unit in result.units:
        unit_segments = sorted(
            (
                segment
                for segment in result.anchor_segments
                if segment.unit_id == unit.unit_id
            ),
            key=lambda segment: segment.unit_order_index,
        )
        assert unit_segments
        previous_end: int | None = None
        for segment in unit_segments:
            assert segment.base_start_utf16 < segment.base_end_utf16
            absolute_slice = slice_by_utf16_offsets(
                base_text, segment.base_start_utf16, segment.base_end_utf16
            )
            local_slice = slice_by_utf16_offsets(
                unit.text, segment.unit_start_utf16, segment.unit_end_utf16
            )
            assert absolute_slice == segment.text
            assert local_slice == segment.text
            assert compute_text_range_hash(segment.text) == segment.text_hash
            if previous_end is not None:
                assert segment.unit_start_utf16 >= previous_end
                gap = slice_by_utf16_offsets(unit.text, previous_end, segment.unit_start_utf16)
                assert gap is None or not gap.strip()
            previous_end = segment.unit_end_utf16


def _assert_no_initialism_fragments(segment_texts: list[str]) -> None:
    for text in segment_texts:
        stripped = text.strip()
        assert stripped not in FORBIDDEN_INITIALISM_FRAGMENTS, segment_texts
        assert not stripped.startswith("K."), segment_texts
        assert not stripped.startswith("S."), segment_texts
        assert not stripped.rstrip().endswith(" U."), segment_texts


def _spacy_available() -> bool:
    return base_builder_module._load_spacy_sentence_pipeline() is not None


@pytest.fixture
def spacy_unavailable(monkeypatch):
    """Force the named regex v2 fallback (model unavailable)."""
    monkeypatch.setattr(
        base_builder_module, "_load_spacy_sentence_pipeline", lambda: None
    )


class _RaisingPipeline:
    """Simulates a spaCy pipeline that fails at runtime."""

    def __call__(self, text: str):  # noqa: ANN204
        raise RuntimeError("simulated spaCy runtime failure")


# ---------------------------------------------------------------------------
# 1 + 2. Initialism boundaries, both providers
# ---------------------------------------------------------------------------


def test_mid_sentence_initialism_is_not_split() -> None:
    result = _build_result("The U.K. government announced a change.")
    assert _unit_segment_texts(result) == ["The U.K. government announced a change."]
    if _spacy_available():
        assert result.units[0].sentence_provider == SENTENCE_PROVIDER_SPACY
    _assert_anchor_invariants(result)


def test_mid_sentence_initialism_is_not_split_under_fallback(spacy_unavailable) -> None:
    result = _build_result("The U.K. government announced a change.")
    assert _unit_segment_texts(result) == ["The U.K. government announced a change."]
    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    assert result.units[0].sentence_provider == SENTENCE_PROVIDER_REGEX_V2
    _assert_anchor_invariants(result)


def test_sentence_final_initialism_splits_into_two_sentences() -> None:
    result = _build_result("He returned to the U.K. It changed everything.")
    assert _unit_segment_texts(result) == [
        "He returned to the U.K.",
        "It changed everything.",
    ]
    _assert_no_initialism_fragments(_unit_segment_texts(result))
    _assert_anchor_invariants(result)


def test_sentence_final_initialism_splits_into_two_sentences_under_fallback(
    spacy_unavailable,
) -> None:
    result = _build_result("He returned to the U.K. It changed everything.")
    assert _unit_segment_texts(result) == [
        "He returned to the U.K.",
        "It changed everything.",
    ]
    _assert_no_initialism_fragments(_unit_segment_texts(result))
    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    _assert_anchor_invariants(result)


# ---------------------------------------------------------------------------
# 3. The real recorded fragment
# ---------------------------------------------------------------------------


def test_real_recorded_uk_fragment_splits_into_two_sentences() -> None:
    result = _build_result(HARRY_FRAGMENT)
    assert _unit_segment_texts(result) == EXPECTED_HARRY_SENTENCES
    _assert_no_initialism_fragments(_unit_segment_texts(result))
    _assert_anchor_invariants(result)
    assert len(result.units) == 1
    assert result.units[0].unit_type == "body"


def test_real_recorded_uk_fragment_splits_into_two_sentences_under_fallback(
    spacy_unavailable,
) -> None:
    result = _build_result(HARRY_FRAGMENT)
    assert _unit_segment_texts(result) == EXPECTED_HARRY_SENTENCES
    _assert_no_initialism_fragments(_unit_segment_texts(result))
    _assert_anchor_invariants(result)


# ---------------------------------------------------------------------------
# 4. Adjacent boundary classes
# ---------------------------------------------------------------------------

_BOUNDARY_CORPUS = [
    pytest.param(
        "The U.S. economy grew quickly. Growth was strong.",
        ["The U.S. economy grew quickly.", "Growth was strong."],
        id="us-mid-and-final",
    ),
    pytest.param(
        "She earned a Ph.D. in physics. Her work matters.",
        ["She earned a Ph.D. in physics.", "Her work matters."],
        id="phd-final",
    ),
    pytest.param(
        "She has a Ph.D. from Oxford.",
        ["She has a Ph.D. from Oxford."],
        id="phd-mid",
    ),
    pytest.param(
        "Dr. Smith arrived early. He smiled.",
        ["Dr. Smith arrived early.", "He smiled."],
        id="dr-abbreviation",
    ),
    pytest.param(
        "See e.g. the report for details. Then decide.",
        ["See e.g. the report for details.", "Then decide."],
        id="eg-mid",
    ),
    pytest.param(
        "Use i.e. the right term there. Thanks.",
        ["Use i.e. the right term there.", "Thanks."],
        id="ie-mid",
    ),
    pytest.param(
        "It costs $2.13 per hour. They rely on diners to tip.",
        ["It costs $2.13 per hour.", "They rely on diners to tip."],
        id="decimal-amount",
    ),
    pytest.param(
        "The library version is 1.2.3. It was released yesterday.",
        ["The library version is 1.2.3.", "It was released yesterday."],
        id="version-number",
    ),
    pytest.param(
        "Visit https://example.com/a.b for info. Thanks.",
        ["Visit https://example.com/a.b for info.", "Thanks."],
        id="url-with-internal-dots",
    ),
    pytest.param(
        "Visit https://example.com. Next sentence.",
        ["Visit https://example.com.", "Next sentence."],
        id="url-at-sentence-end",
    ),
    pytest.param(
        'He said "stop." She listened.',
        ['He said "stop."', "She listened."],
        id="quote-final-punctuation",
    ),
    pytest.param(
        "He returned to the U.K. It changed everything.",
        ["He returned to the U.K.", "It changed everything."],
        id="uk-sentence-final-pronoun-splits",
    ),
    pytest.param(
        "She left the U.K. They followed her.",
        ["She left the U.K.", "They followed her."],
        id="uk-sentence-final-they-splits",
    ),
]

# Rework counter-examples: title-case continuations after a
# sentence-medial initialism must NOT be split, on either path. No
# content-word exclusion lists: the rule is a closed pronoun class
# (see _INITIALISM_SENTENCE_STARTERS in base_builder).
_NO_SPLIT_CONTINUATION_CORPUS = [
    pytest.param(
        "The U.K. Prime Minister spoke today.",
        id="uk-prime-minister",
    ),
    pytest.param(
        "The U.S. President addressed Congress.",
        id="us-president",
    ),
    pytest.param(
        "She met the U.S. Secretary of State.",
        id="us-secretary-of-state",
    ),
    pytest.param(
        "Ph.D. Students may apply.",
        id="phd-students",
    ),
    pytest.param(
        "The U.K. IT sector grew quickly.",
        id="uk-it-acronym-not-pronoun",
    ),
    pytest.param(
        "The U.K. government announced a change.",
        id="uk-government-lowercase",
    ),
    pytest.param(
        "Consider renewable energy, e.g. This approach reduces emissions.",
        id="eg-introduces-same-sentence",
    ),
    pytest.param(
        "The result is inconclusive, i.e. It does not prove causation.",
        id="ie-introduces-same-sentence",
    ),
]


@pytest.mark.parametrize(("text", "expected"), _BOUNDARY_CORPUS)
def test_boundary_corpus_on_main_path(text: str, expected: list[str]) -> None:
    result = _build_result(text)
    assert _unit_segment_texts(result) == expected
    _assert_no_initialism_fragments(expected)
    _assert_anchor_invariants(result)


@pytest.mark.parametrize(("text", "expected"), _BOUNDARY_CORPUS)
def test_boundary_corpus_on_regex_v2_fallback(
    spacy_unavailable, text: str, expected: list[str]
) -> None:
    result = _build_result(text)
    assert _unit_segment_texts(result) == expected
    _assert_no_initialism_fragments(expected)
    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    _assert_anchor_invariants(result)


@pytest.mark.parametrize("text", _NO_SPLIT_CONTINUATION_CORPUS)
def test_title_case_continuation_after_initialism_is_not_split(text: str) -> None:
    """spaCy main path: title-case noun phrases after an initialism
    (Prime Minister / President / Secretary of State / Students) and
    all-caps acronyms (IT) stay in ONE sentence - no broad
    "uppercase = new sentence" splitting."""
    result = _build_result(text)
    assert _unit_segment_texts(result) == [text]
    _assert_anchor_invariants(result)


@pytest.mark.parametrize("text", _NO_SPLIT_CONTINUATION_CORPUS)
def test_title_case_continuation_after_initialism_is_not_split_under_fallback(
    spacy_unavailable, text: str
) -> None:
    """regex v2 fallback: same conservative pronoun-class rule."""
    result = _build_result(text)
    assert _unit_segment_texts(result) == [text]
    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    _assert_anchor_invariants(result)


# ---------------------------------------------------------------------------
# 5. UTF-16 / non-BMP coverage
# ---------------------------------------------------------------------------


def test_sentence_slices_and_utf16_offsets_with_non_bmp_characters() -> None:
    text = "Emoji \U0001F600 fun here. Next \U0001F4A9 sentence here."
    result = _build_result(text)
    segments = _unit_segment_texts(result)
    assert segments == ["Emoji \U0001F600 fun here.", "Next \U0001F4A9 sentence here."]

    # Emoji are non-BMP: each counts as 2 UTF-16 code units. The offsets
    # must still slice the exact segment text from the canonical base.
    assert utf16_code_unit_length("\U0001F600") == 2
    for segment in result.anchor_segments:
        assert (
            slice_by_utf16_offsets(
                result.base.text, segment.base_start_utf16, segment.base_end_utf16
            )
            == segment.text
        )
    _assert_anchor_invariants(result)


def test_sentence_slices_and_utf16_offsets_with_non_bmp_under_fallback(
    spacy_unavailable,
) -> None:
    text = "Emoji \U0001F600 fun here. Next \U0001F4A9 sentence here."
    result = _build_result(text)
    assert _unit_segment_texts(result) == [
        "Emoji \U0001F600 fun here.",
        "Next \U0001F4A9 sentence here.",
    ]
    for segment in result.anchor_segments:
        assert (
            slice_by_utf16_offsets(
                result.base.text, segment.base_start_utf16, segment.base_end_utf16
            )
            == segment.text
        )
    _assert_anchor_invariants(result)


# ---------------------------------------------------------------------------
# 6. Fallback behavior + segmenter identity
# ---------------------------------------------------------------------------


def test_fallback_when_model_unavailable_uses_named_regex_v2(
    spacy_unavailable,
) -> None:
    result = _build_result(HARRY_FRAGMENT)

    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    # The fallback must NOT impersonate the spaCy main path.
    assert SPACY_EN_SENTENCE_SEGMENTER_VERSION not in result.base.segmenter_version
    assert result.units[0].sentence_provider == SENTENCE_PROVIDER_REGEX_V2
    assert result.units[0].sentence_provider != SENTENCE_PROVIDER_SPACY

    assert _unit_segment_texts(result) == EXPECTED_HARRY_SENTENCES
    _assert_no_initialism_fragments(_unit_segment_texts(result))
    _assert_anchor_invariants(result)


def test_fallback_when_spacy_raises_at_runtime_uses_named_regex_v2(monkeypatch) -> None:
    monkeypatch.setattr(
        base_builder_module,
        "_load_spacy_sentence_pipeline",
        lambda: _RaisingPipeline(),
    )
    result = _build_result(HARRY_FRAGMENT)

    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    assert result.units[0].sentence_provider == SENTENCE_PROVIDER_REGEX_V2
    assert _unit_segment_texts(result) == EXPECTED_HARRY_SENTENCES
    _assert_no_initialism_fragments(_unit_segment_texts(result))
    _assert_anchor_invariants(result)


def test_fallback_produces_no_initialism_internal_boundary(spacy_unavailable) -> None:
    texts = [
        "The U.K. government announced a change.",
        "The U.S. economy grew. Growth was strong.",
        "She has a Ph.D. from Oxford.",
        "See e.g. the report for details. Then decide.",
    ]
    for text in texts:
        result = _build_result(text)
        assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
        _assert_no_initialism_fragments(_unit_segment_texts(result))
        _assert_anchor_invariants(result)


def test_non_english_text_uses_regex_v2_without_attempting_spacy() -> None:
    result = _build_result("这是一段中文. This is mixed.", language="zh")
    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    assert all(
        unit.sentence_provider
        in (SENTENCE_PROVIDER_REGEX_V2, None)
        for unit in result.units
    )
    _assert_anchor_invariants(result)


def test_unknown_segmenter_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported segmenter_version"):
        _build_result(HARRY_FRAGMENT, segmenter_version="pinned_legacy_v1")


def test_explicit_v2_identity_runs_regex_v2_without_loading_spacy(monkeypatch) -> None:
    monkeypatch.setattr(
        base_builder_module,
        "_load_spacy_sentence_pipeline",
        lambda: pytest.fail("explicit regex v2 must not load spaCy"),
    )
    result = _build_result(
        HARRY_FRAGMENT,
        segmenter_version=REGEX_V2_SEGMENTER_VERSION,
    )
    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    assert result.units[0].sentence_provider == SENTENCE_PROVIDER_REGEX_V2
    assert _unit_segment_texts(result) == EXPECTED_HARRY_SENTENCES


def test_explicit_v1_identity_runs_frozen_regex_v1_verbatim() -> None:
    """The v1 algorithm label is no longer the AUTO policy
    sentinel. Requesting it explicitly must run the FROZEN regex v1
    (with its historical U.K. mis-split, proving v1 really ran) and
    persist the label verbatim."""
    result = _build_result(
        HARRY_FRAGMENT, segmenter_version=DETERMINISTIC_SEGMENTER_VERSION
    )
    assert result.base.segmenter_version == DETERMINISTIC_SEGMENTER_VERSION
    assert result.units[0].sentence_provider == SENTENCE_PROVIDER_REGEX_V1
    # Frozen v1's known behavior: the internal U.K. period is treated as
    # a boundary, producing the historical "K." orphan. This documents
    # that explicit v1 pinning executes v1 (not spaCy / v2).
    assert any(
        segment.text == "K." for segment in result.anchor_segments
    ), [segment.text for segment in result.anchor_segments]
    _assert_anchor_invariants(result)


def test_auto_policy_resolves_spaCy_identity_when_model_available() -> None:
    if not _spacy_available():
        pytest.skip("spaCy en_core_web_sm not available in this environment")
    result = _build_result(HARRY_FRAGMENT)
    assert result.base.segmenter_version == SPACY_EN_SENTENCE_SEGMENTER_VERSION
    assert result.units[0].sentence_provider == SENTENCE_PROVIDER_SPACY


class _FakeSentence:
    def __init__(self, start_char: int, end_char: int) -> None:
        self.start_char = start_char
        self.end_char = end_char


class _FakeDoc:
    def __init__(self, spans: list[tuple[int, int]]) -> None:
        self.sents = [_FakeSentence(start, end) for start, end in spans]


class _WholeSpanPipeline:
    def __call__(self, text: str) -> _FakeDoc:
        return _FakeDoc([(0, len(text))])


def test_explicit_spacy_identity_runs_spacy_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        base_builder_module,
        "_load_spacy_sentence_pipeline",
        lambda: _WholeSpanPipeline(),
    )
    result = _build_result(
        "A complete sentence.",
        segmenter_version=SPACY_EN_SENTENCE_SEGMENTER_VERSION,
    )
    assert result.base.segmenter_version == SPACY_EN_SENTENCE_SEGMENTER_VERSION
    assert result.units[0].sentence_provider == SENTENCE_PROVIDER_SPACY


def test_explicit_spacy_identity_fails_when_model_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        base_builder_module,
        "_load_spacy_sentence_pipeline",
        lambda: None,
    )
    with pytest.raises(ValueError, match="model is unavailable"):
        _build_result(
            "A complete sentence.",
            segmenter_version=SPACY_EN_SENTENCE_SEGMENTER_VERSION,
        )


class _SelectiveFailurePipeline:
    """Works for blocks without 'Second'; raises for the rest."""

    def __call__(self, text: str):  # noqa: ANN204
        if "Second" in text:
            raise RuntimeError("simulated per-block spaCy failure")
        first = _FakeDoc([(0, 10), (11, 20)])  # "First U.K." | "It split."
        return first


def test_per_block_fallback_records_mixed_segmenter_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        base_builder_module,
        "_load_spacy_sentence_pipeline",
        lambda: _SelectiveFailurePipeline(),
    )
    text = "First U.K. It split.\n\nSecond block here. More text."
    result = _build_result(text)

    providers = [unit.sentence_provider for unit in result.units]
    assert providers == [SENTENCE_PROVIDER_SPACY, SENTENCE_PROVIDER_REGEX_V2]
    assert result.base.segmenter_version == (
        SPACY_EN_SENTENCE_SEGMENTER_VERSION + MIXED_SEGMENTER_VERSION_SUFFIX
    )
    assert _unit_segment_texts(result, 0) == ["First U.K.", "It split."]
    _assert_anchor_invariants(result)


# ---------------------------------------------------------------------------
# 7. Downstream: Translation Group planning + snapshot projection
# ---------------------------------------------------------------------------


def _to_batch_unit(result, unit_index: int = 0) -> TranslationBatchUnitContext:
    unit = result.units[unit_index]
    unit_segments = sorted(
        (
            segment
            for segment in result.anchor_segments
            if segment.unit_id == unit.unit_id
        ),
        key=lambda segment: segment.unit_order_index,
    )
    targets = tuple(
        TranslationAnchorSegmentTarget(
            anchor_segment_id=segment.anchor_segment_id,
            sentence_id=segment.sentence_id,
            order_index=segment.order_index,
            segment_type=segment.segment_type,
            boundary_quality=segment.boundary_quality,
            unit_start_utf16=segment.unit_start_utf16,
            unit_end_utf16=segment.unit_end_utf16,
            text_hash=segment.text_hash,
            source_text=segment.text,
        )
        for segment in unit_segments
    )
    return TranslationBatchUnitContext(
        unit_id=unit.unit_id,
        order_index=unit.order_index,
        source_text=unit.text,
        text_hash=unit.text_hash,
        anchor_segments=targets,
    )


def test_translation_groups_for_uk_it_base_have_no_stray_initialism_fragments() -> None:
    result = _build_result(HARRY_FRAGMENT)
    unit = _to_batch_unit(result, 0)
    groups = build_deterministic_translation_groups(unit)

    # The anchors feeding the planner are exactly the two real sentences.
    anchor_texts = [segment.source_text for segment in unit.anchor_segments]
    assert anchor_texts == EXPECTED_HARRY_SENTENCES

    # No translation group starts with a stray "K." or ends with a
    # stray "U." (the mis-grouping signature).
    assert groups
    for group in groups:
        assert not group.source_text.startswith("K."), group.source_text
        assert not group.source_text.rstrip().endswith(" U."), group.source_text
        assert group.source_text.strip() not in FORBIDDEN_INITIALISM_FRAGMENTS

    # The two short sentences form one semantic group covering the unit.
    assert len(groups) == 1
    assert groups[0].source_text == HARRY_FRAGMENT
    assert groups[0].anchor_segment_ids == tuple(
        segment.anchor_segment_id for segment in unit.anchor_segments
    )


def test_translation_groups_for_uk_it_base_under_fallback(spacy_unavailable) -> None:
    result = _build_result(HARRY_FRAGMENT)
    assert result.base.segmenter_version == REGEX_V2_SEGMENTER_VERSION
    unit = _to_batch_unit(result, 0)
    groups = build_deterministic_translation_groups(unit)

    anchor_texts = [segment.source_text for segment in unit.anchor_segments]
    assert anchor_texts == EXPECTED_HARRY_SENTENCES
    assert len(groups) == 1
    assert groups[0].source_text == HARRY_FRAGMENT


def test_snapshot_projection_for_uk_it_base_has_no_stray_initialism_leaves() -> None:
    def _collect_text(nodes: object) -> str:
        parts: list[str] = []
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            if isinstance(node.get("text"), str):
                parts.append(node["text"])
            children = node.get("children")
            if isinstance(children, list):
                parts.append(_collect_text(children))
        return "".join(parts)

    result = _build_result(HARRY_FRAGMENT)
    snapshot = build_reader_plate_snapshot(
        result,
        snapshot_taken_at=SNAPSHOT_TAKEN_AT,
        last_event_sequence=7,
    )

    # The source block rebuilds the full unit text (whitespace gaps
    # between anchors are their own leaves).
    source_block = snapshot.value[0]["children"][0]  # type: ignore[index]
    assert _collect_text(source_block["children"]) == result.units[0].text

    # Each anchor segment node rebuilds exactly its segment text, and
    # no anchor node is a bare initialism fragment (the bug
    # rendered "... in the U." and "K. It led ..." as separate blocks).
    anchor_nodes = [
        child
        for child in source_block["children"]  # type: ignore[index]
        if isinstance(child, dict) and child.get("type") == "reader_anchor_segment"
    ]
    assert [node["anchor_segment_id"] for node in anchor_nodes] == [
        segment.anchor_segment_id for segment in result.anchor_segments
    ]
    anchor_texts = [_collect_text(node.get("children")) for node in anchor_nodes]
    assert anchor_texts == EXPECTED_HARRY_SENTENCES
    for text in anchor_texts:
        assert text.strip() not in FORBIDDEN_INITIALISM_FRAGMENTS, anchor_texts
        assert not text.startswith("K."), anchor_texts
        assert not text.rstrip().endswith(" U."), anchor_texts
