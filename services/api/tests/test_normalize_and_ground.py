from app.schemas.common import TextSpan
from app.schemas.internal.analysis import (
    Chunk,
    PreparedSentence,
    SentenceTranslation,
)
from app.schemas.internal.drafts import (
    AnchorQuote,
    DraftContextGloss,
    DraftGrammarNote,
    DraftPhraseGloss,
    DraftSentenceAnalysis,
    DraftVocabHighlight,
    GrammarDraft,
    TranslationDraft,
    VocabularyDraft,
)
from app.schemas.internal.execution_plan import GoalPolicy
from app.schemas.internal.normalized import (
    NormalizedContextGloss,
    NormalizedGrammarNote,
    NormalizedPhraseGloss,
    NormalizedSentenceAnalysis,
    NormalizedVocabHighlight,
)
from app.services.analysis.postprocess.normalize_and_ground import normalize_and_ground


def _sentence(sentence_id: str, text: str) -> PreparedSentence:
    return PreparedSentence(
        sentence_id=sentence_id,
        paragraph_id="p1",
        text=text,
        sentence_span=TextSpan(start=0, end=len(text)),
    )


def _policy(**overrides) -> GoalPolicy:
    defaults = dict(
        annotation_density=3,
        vocabulary_focus="high_value_only",
        grammar_focus="balanced",
        translation_focus="natural",
    )
    defaults.update(overrides)
    return GoalPolicy(**defaults)


def _translation(sentence_id: str = "s1") -> SentenceTranslation:
    return SentenceTranslation(
        sentence_id=sentence_id, translation_zh="翻译"
    )


def _translation_draft(*sids: str) -> TranslationDraft:
    return TranslationDraft(
        title="测试标题",
        sentence_translations=[_translation(sid) for sid in sids],
    )


def _empty_vocab() -> VocabularyDraft:
    return VocabularyDraft(
        vocab_highlights=[], phrase_glosses=[], context_glosses=[]
    )


def _empty_grammar() -> GrammarDraft:
    return GrammarDraft(grammar_notes=[], sentence_analyses=[])


def test_normalize_drops_spaced_vocab_highlight() -> None:
    invalid_vocab = DraftVocabHighlight.model_construct(
        type="vocab_highlight",
        sentence_id="s1",
        text="extreme lengths",
    )
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft.model_construct(
            vocab_highlights=[invalid_vocab],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "Shopkeepers are going to extreme lengths.")],
        policy=_policy(),
    )
    assert result.annotations == []
    assert any(
        "single_word" in item.drop_reason or "single" in item.drop_reason
        for item in result.drop_log
    )


def test_normalize_drops_invalid_single_word_phrase_gloss() -> None:
    invalid_phrase = DraftPhraseGloss.model_construct(
        type="phrase_gloss",
        sentence_id="s1",
        label="buzzword",
        anchor_quotes=[AnchorQuote(text="buzzword")],
        phrase_type="collocation",
        zh="流行词",
    )
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft.model_construct(
            vocab_highlights=[],
            phrase_glosses=[invalid_phrase],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "This concept became a buzzword.")],
        policy=_policy(),
    )
    assert result.annotations == []
    assert any(
        "single-token" in item.drop_reason or "single_token" in item.drop_reason
        for item in result.drop_log
    )


def test_density_control_uses_profile_limit() -> None:
    annotations = [
        DraftGrammarNote(
            sentence_id="s1",
            grammar_point="定语从句",
            anchor_quotes=[AnchorQuote(text="which", role="rel")],
            note_zh="说明 which 引导定语从句。",
        ),
        DraftGrammarNote(
            sentence_id="s1",
            grammar_point="宾语从句",
            anchor_quotes=[AnchorQuote(text="that", role="comp")],
            note_zh="说明 that 引导从句。",
        ),
        DraftVocabHighlight(sentence_id="s1", text="constitutional"),
        DraftVocabHighlight(sentence_id="s1", text="monarchy"),
    ]
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                a for a in annotations if isinstance(a, DraftVocabHighlight)
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=GrammarDraft(
            grammar_notes=[
                a for a in annotations if isinstance(a, DraftGrammarNote)
            ],
            sentence_analyses=[],
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "The constitutional monarchy, which many say matters, "
                "is something that people debate.",
            )
        ],
        policy=_policy(annotation_density=2, grammar_focus="focused"),
    )
    assert len(result.annotations) == 2
    assert any(item.drop_stage == "density_control" for item in result.drop_log)


def test_sentence_analysis_with_result_in_being_done_survives_normalize() -> None:
    analysis = DraftSentenceAnalysis(
        sentence_id="s1",
        label="主句加 result in 压缩结构",
        analysis_zh="先抓主句，再看后面的结果结构。",
        chunks=[
            Chunk(order=1, label="主句", text="Higher gas prices result in"),
            Chunk(
                order=2,
                label="结果结构",
                text="farmers being forced to pay more",
            ),
        ],
    )
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=GrammarDraft(
            grammar_notes=[], sentence_analyses=[analysis]
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "Higher gas prices result in farmers "
                "being forced to pay more for fertilizer.",
            )
        ],
        policy=_policy(),
    )
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "sentence_analysis"


def test_grammar_anchor_boundary_punctuation_is_trimmed() -> None:
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=GrammarDraft(
            grammar_notes=[
                DraftGrammarNote(
                    sentence_id="s1",
                    grammar_point="非限制性定语从句",
                    anchor_quotes=[
                        AnchorQuote(
                            text=", which does not depend on GDP growth"
                        )
                    ],
                    note_zh="which 引导非限制性定语从句。",
                )
            ],
            sentence_analyses=[],
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                'We should focus on "real wealth", '
                "which does not depend on GDP growth.",
            )
        ],
        policy=_policy(),
    )

    assert len(result.annotations) == 1
    assert result.annotations[0].type == "grammar_note"
    span_text = result.annotations[0].spans[0].text
    assert span_text == "which does not depend on GDP growth"
    assert result.drop_log == []


def test_grammar_anchor_schematic_ellipsis_is_expanded_when_unique() -> None:
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=GrammarDraft(
            grammar_notes=[
                DraftGrammarNote(
                    sentence_id="s1",
                    grammar_point="强调句型",
                    anchor_quotes=[
                        AnchorQuote(text="It wasn't until ... that ...")
                    ],
                    note_zh="这是 not until 的强调句型。",
                )
            ],
            sentence_analyses=[],
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "It wasn't until I began to research this advice "
                "that I understood the problem.",
            )
        ],
        policy=_policy(),
    )

    assert len(result.annotations) == 1
    assert result.annotations[0].type == "grammar_note"
    span_text = result.annotations[0].spans[0].text
    assert span_text == "It wasn't until I began to research this advice that"
    assert result.drop_log == []


def test_grammar_anchor_schematic_ellipsis_is_dropped_when_not_unique() -> None:
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=GrammarDraft(
            grammar_notes=[
                DraftGrammarNote(
                    sentence_id="s1",
                    grammar_point="介词 + which 引导定语从句",
                    anchor_quotes=[AnchorQuote(text="at which ... wildlife")],
                    note_zh="这是 at which 引导的结构。",
                )
            ],
            sentence_analyses=[],
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "We discussed the stage at which the plan changed "
                "and the stage at which the data shifted "
                "in reports from wildlife experts.",
            )
        ],
        policy=_policy(),
    )

    assert result.annotations == []
    assert any(
        item.drop_reason == "schematic_anchor_not_groundable"
        for item in result.drop_log
    )


def test_vocabulary_anchor_case_mismatch_is_canonicalized_to_source_text() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="languages")
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "Languages have changed over time.")],
        policy=_policy(),
    )

    assert len(result.annotations) == 1
    assert result.annotations[0].type == "vocab_highlight"
    assert result.annotations[0].text == "Languages"


def test_vocabulary_anchor_punctuation_variant_is_canonicalized_to_source_text() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="long–term",
                    anchor_quotes=[AnchorQuote(text="long-term")],
                    phrase_type="compound",
                    zh="长期的",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "This is a long-term challenge.")],
        policy=_policy(),
    )

    assert len(result.annotations) == 1
    assert result.annotations[0].type == "phrase_gloss"
    # label (em dash) → PhraseGloss.text; anchor quote (hyphen) → spans
    assert result.annotations[0].text == "long–term"
    assert result.annotations[0].spans[0].text == "long-term"


def test_vocabulary_anchor_schematic_ellipsis_is_preserved_for_multi_text_projection() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="apply ... to",
                    anchor_quotes=[
                        AnchorQuote(text="apply"),
                        AnchorQuote(text="to"),
                    ],
                    phrase_type="collocation",
                    zh="将……应用于……",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "Participants should immediately apply their learning "
                "to a specific intervention.",
            )
        ],
        policy=_policy(),
    )

    assert len(result.annotations) == 1
    assert result.annotations[0].type == "phrase_gloss"
    assert result.annotations[0].text == "apply ... to"


def test_phrase_gloss_explicit_spans_are_grounded_without_rewriting_title_text() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="turn ... into",
                    anchor_quotes=[
                        AnchorQuote(text="turn"),
                        AnchorQuote(text="into"),
                    ],
                    phrase_type="phrasal_verb",
                    zh="把……变成……",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence("s1", "Turn their passion into a stable income.")
        ],
        policy=_policy(),
    )

    assert len(result.annotations) == 1
    phrase = result.annotations[0]
    assert phrase.type == "phrase_gloss"
    assert phrase.text == "turn ... into"
    assert [span.text for span in phrase.spans or []] == ["Turn", "into"]


def test_vocabulary_anchor_without_schematic_notation_is_not_recovered() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="forge a link between",
                    anchor_quotes=[
                        AnchorQuote(text="forge a link between")
                    ],
                    phrase_type="collocation",
                    zh="建立联系",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "They hope to forge a smooth link "
                "between learning and doing.",
            )
        ],
        policy=_policy(),
    )

    assert result.annotations == []
    assert any(
        item.drop_reason == "anchor_not_substring"
        for item in result.drop_log
    )


def test_vocabulary_anchor_pedagogical_pattern_is_preserved_for_multi_text_projection() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="prompt sb to do sth",
                    anchor_quotes=[
                        AnchorQuote(text="prompt"),
                        AnchorQuote(text="to rethink"),
                    ],
                    phrase_type="collocation",
                    zh="促使某人做某事",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "Alternative realities can prompt them "
                "to rethink their current beliefs.",
            )
        ],
        policy=_policy(),
    )

    assert len(result.annotations) == 1
    assert result.annotations[0].type == "phrase_gloss"
    assert result.annotations[0].text == "prompt sb to do sth"


def test_vocab_highlight_between_schematic_phrase_parts_is_not_dropped() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="learning")
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="apply ... to",
                    anchor_quotes=[
                        AnchorQuote(text="apply"),
                        AnchorQuote(text="to"),
                    ],
                    phrase_type="collocation",
                    zh="将……应用于……",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "Participants should immediately apply their learning "
                "to a specific intervention.",
            )
        ],
        policy=_policy(),
    )

    assert sorted(item.type for item in result.annotations) == [
        "phrase_gloss",
        "vocab_highlight",
    ]
    assert not any(
        item.drop_reason.startswith("subsumed_by_")
        for item in result.drop_log
    )


def test_vocab_highlight_between_explicit_phrase_spans_is_not_dropped() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="passion")
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="turn ... into",
                    anchor_quotes=[
                        AnchorQuote(text="turn"),
                        AnchorQuote(text="into"),
                    ],
                    phrase_type="phrasal_verb",
                    zh="把……变成……",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence("s1", "People can turn passion into progress.")
        ],
        policy=_policy(),
    )

    assert sorted(item.type for item in result.annotations) == [
        "phrase_gloss",
        "vocab_highlight",
    ]
    assert not any(
        item.drop_reason.startswith("subsumed_by_")
        for item in result.drop_log
    )


def test_vocabulary_anchor_ambiguous_case_mismatch_is_not_canonicalized() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="apple")
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence("s1", "Apple and APPLE are styled differently.")
        ],
        policy=_policy(),
    )

    assert result.annotations == []
    assert any(
        item.drop_reason == "anchor_not_substring"
        for item in result.drop_log
    )


def test_same_text_cross_type_keeps_context_gloss() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="range")
            ],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="range",
                    anchor_quotes=[AnchorQuote(text="range")],
                    gloss="一系列",
                    reason="后文列举多个选择。",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The range of choices surprised them.")],
        policy=_policy(),
    )

    assert [item.type for item in result.annotations] == ["context_gloss"]
    assert any(
        item.drop_reason == "conflict_resolution"
        for item in result.drop_log
    )


def test_vocab_highlight_subsumed_by_phrase_gloss_is_dropped() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="settling")
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="settling down",
                    anchor_quotes=[AnchorQuote(text="settling down")],
                    phrase_type="phrasal_verb",
                    zh="安定下来",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence("s1", "They started settling down in the village.")
        ],
        policy=_policy(),
    )

    assert [item.type for item in result.annotations] == ["phrase_gloss"]
    assert any(
        item.drop_reason == "subsumed_by_phrase_gloss"
        for item in result.drop_log
    )


def test_phrase_and_context_overlap_are_not_dropped_by_vocab_subsumption_rule() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="coming and going",
                    anchor_quotes=[
                        AnchorQuote(text="coming and going")
                    ],
                    phrase_type="collocation",
                    zh="来来去去",
                )
            ],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="going",
                    anchor_quotes=[AnchorQuote(text="going")],
                    gloss="消失",
                    reason="和 coming 对比。",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "Languages have been coming and going for years.",
            )
        ],
        policy=_policy(),
    )

    assert sorted(item.type for item in result.annotations) == [
        "context_gloss",
        "phrase_gloss",
    ]
    assert not any(
        item.drop_reason.startswith("subsumed_by_")
        for item in result.drop_log
    )


def test_low_value_words_only_prune_vocab_highlights() -> None:
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="in")
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="in need",
                    anchor_quotes=[AnchorQuote(text="in need")],
                    phrase_type="collocation",
                    zh="需要帮助的",
                )
            ],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="need",
                    anchor_quotes=[AnchorQuote(text="need")],
                    gloss="困难处境",
                    reason="和 in 搭配表示处境。",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The family was in need of support.")],
        policy=_policy(),
    )

    assert sorted(item.type for item in result.annotations) == [
        "context_gloss",
        "phrase_gloss",
    ]
    assert any(
        item.drop_reason == "low_value_word" for item in result.drop_log
    )


def test_context_gloss_with_spans_is_canonicalized_and_preserved() -> None:
    """DraftContextGloss(display="prompt sb to do sth", anchor_quotes=["prompted", "to rethink"])
    经过 normalize 后 spans 保留并 canonical。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="prompt sb to do sth",
                    anchor_quotes=[
                        AnchorQuote(text="prompted"),
                        AnchorQuote(text="to rethink"),
                    ],
                    gloss="促使某人做某事",
                    reason="词典义不足以表达语境含义",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    ctx = [a for a in result.annotations if a.type == "context_gloss"]
    assert len(ctx) == 1
    assert ctx[0].spans is not None
    assert len(ctx[0].spans) == 2
    assert ctx[0].spans[0].text == "prompted"
    assert ctx[0].spans[1].text == "to rethink"
    assert ctx[0].display == "prompt sb to do sth"


def test_context_gloss_with_bad_anchor_quote_is_dropped_in_normalize() -> None:
    """第二个 context anchor_quote 不存在时，normalize 阶段 drop，
    drop_log 有 anchor_not_substring。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="prompt sb to do sth",
                    anchor_quotes=[
                        AnchorQuote(text="prompted"),
                        AnchorQuote(text="NONEXISTENT_WORD"),
                    ],
                    gloss="促使某人做某事",
                    reason="词典义不足以表达语境含义",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    ctx = [a for a in result.annotations if a.type == "context_gloss"]
    assert len(ctx) == 0
    assert any(
        item.drop_reason == "anchor_not_substring"
        and item.annotation_type == "context_gloss"
        for item in result.drop_log
    )


def test_context_gloss_span_aware_identity_prevents_density_misdrop() -> None:
    """两个 context_gloss 共享第一个 anchor_quote 但后续 quote 不同时，
    _annotation_identity 应区分它们；density_control=1 时只保留 1 个。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="apply to teams",
                    anchor_quotes=[
                        AnchorQuote(text="apply"),
                        AnchorQuote(text="to teams"),
                    ],
                    gloss="适用于团队",
                    reason="词典义不足以表达语境含义",
                ),
                DraftContextGloss(
                    sentence_id="s1",
                    display="apply to leaders",
                    anchor_quotes=[
                        AnchorQuote(text="apply"),
                        AnchorQuote(text="to leaders"),
                    ],
                    gloss="适用于领导者",
                    reason="词典义不足以表达语境含义",
                ),
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "The rule can apply to teams and to leaders.",
            )
        ],
        policy=_policy(annotation_density=1),
    )

    ctx = [a for a in result.annotations if a.type == "context_gloss"]
    assert len(ctx) == 1
    ctx_drops = [
        item for item in result.drop_log
        if item.annotation_type == "context_gloss"
    ]
    assert len(ctx_drops) == 1
    assert ctx_drops[0].drop_reason.startswith("density_exceeded")


# ── Phase 2.3A: Canonical shadow path tests ──────────────────────────


def test_normalize_and_ground_returns_both_annotations_and_normalized() -> None:
    """normalize_and_ground 同时返回旧 annotations 和 normalized_annotations。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompted")
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    # 旧 annotations 不变
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "vocab_highlight"

    # normalized_annotations 也有结果
    assert len(result.normalized_annotations) == 1
    assert isinstance(result.normalized_annotations[0], NormalizedVocabHighlight)
    assert result.normalized_annotations[0].spans[0].text == "prompted"


def test_canonical_stats_populated() -> None:
    """canonical_stats 包含所有要求的观测指标。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompted")
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    assert result.canonical_stats is not None
    assert "canonical_normalized_counts" in result.canonical_stats
    assert "canonical_drop_counts_by_type" in result.canonical_stats
    assert "canonical_drop_counts_by_reason" in result.canonical_stats
    assert "canonical_span_count" in result.canonical_stats
    assert "canonical_anchor_drop_summary" in result.canonical_stats

    assert result.canonical_stats["canonical_normalized_counts"]["vocab_highlight"] == 1
    assert result.canonical_stats["canonical_span_count"] == 1


def test_canonical_drop_log_independent_of_old_drop_log() -> None:
    """canonical_drop_log 与旧 drop_log 独立。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="in")
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="in need",
                    anchor_quotes=[AnchorQuote(text="in need")],
                    phrase_type="collocation",
                    zh="需要帮助的",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The family was in need of support.")],
        policy=_policy(),
    )

    # 旧 annotations: vocab_highlight "in" 被 low_value_word drop，
    # phrase_gloss "in need" 保留
    assert any(a.type == "phrase_gloss" for a in result.annotations)
    assert any(
        e.drop_reason == "low_value_word" for e in result.drop_log
    )

    # canonical_drop_log: "in" 被 quote_too_short drop
    assert any(
        e.drop_reason == "quote_too_short" for e in result.canonical_drop_log
    )
    # canonical 和旧 drop_log 是独立的列表
    assert result.canonical_drop_log is not result.drop_log


def test_quote_ambiguous_enters_canonical_stats_old_annotations_unchanged() -> None:
    """quote_ambiguous 会进入 canonical stats，但旧 annotations 行为不变。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="team")
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The team and the other team agreed.")],
        policy=_policy(),
    )

    # 旧 annotations: "team" 在旧链路中通过 is_substring 检查，保留
    # （旧链路不做歧义检查，这是 canonical 路径的改进）
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "vocab_highlight"

    # canonical stats: "team" 被 quote_ambiguous drop
    assert result.canonical_stats is not None
    assert result.canonical_stats["canonical_drop_counts_by_reason"].get("quote_ambiguous", 0) >= 1
    assert result.canonical_stats["canonical_anchor_drop_summary"]["total_anchor_drops"] >= 1


def test_quote_not_found_enters_canonical_stats_old_annotations_unchanged() -> None:
    """quote_not_found 会进入 canonical stats，但旧 annotations 行为不变。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="test phrase",
                    anchor_quotes=[AnchorQuote(text="NONEXISTENT")],
                    phrase_type="collocation",
                    zh="测试",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    # 旧 annotations: phrase_gloss 被 anchor_not_substring drop
    assert result.annotations == []
    assert any(
        e.drop_reason == "anchor_not_substring" for e in result.drop_log
    )

    # canonical stats: phrase_gloss 被 quote_not_found drop
    assert result.canonical_stats is not None
    assert result.canonical_stats["canonical_drop_counts_by_reason"].get("quote_not_found", 0) >= 1


def test_multi_quote_phrase_generates_multiple_canonical_spans() -> None:
    """phrase_gloss 的 multi quote 能生成多个 CanonicalSpan。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="turn ... into",
                    anchor_quotes=[
                        AnchorQuote(text="Turn"),
                        AnchorQuote(text="into"),
                    ],
                    phrase_type="phrasal_verb",
                    zh="把……变成……",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence("s1", "Turn their passion into a stable income.")
        ],
        policy=_policy(),
    )

    # 旧 annotations 不变
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "phrase_gloss"

    # normalized_annotations 有 2 个 spans
    assert len(result.normalized_annotations) == 1
    norm_phrase = result.normalized_annotations[0]
    assert isinstance(norm_phrase, NormalizedPhraseGloss)
    assert len(norm_phrase.spans) == 2
    assert norm_phrase.spans[0].text == "Turn"
    assert norm_phrase.spans[1].text == "into"

    # canonical_stats 的 span_count = 2
    assert result.canonical_stats is not None
    assert result.canonical_stats["canonical_span_count"] == 2


def test_multi_quote_context_gloss_generates_multiple_canonical_spans() -> None:
    """context_gloss 的 multi quote 能生成多个 CanonicalSpan。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="prompt sb to do sth",
                    anchor_quotes=[
                        AnchorQuote(text="prompted"),
                        AnchorQuote(text="to rethink"),
                    ],
                    gloss="促使某人做某事",
                    reason="词典义不足以表达语境含义",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    # 旧 annotations 不变
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "context_gloss"

    # normalized_annotations 有 2 个 spans
    assert len(result.normalized_annotations) == 1
    norm_ctx = result.normalized_annotations[0]
    assert isinstance(norm_ctx, NormalizedContextGloss)
    assert len(norm_ctx.spans) == 2
    assert norm_ctx.spans[0].text == "prompted"
    assert norm_ctx.spans[1].text == "to rethink"


def test_multi_quote_grammar_generates_multiple_canonical_spans() -> None:
    """grammar_note 的 multi quote 能生成多个 CanonicalSpan。"""
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=GrammarDraft(
            grammar_notes=[
                DraftGrammarNote(
                    sentence_id="s1",
                    grammar_point="not only 句首倒装",
                    anchor_quotes=[
                        AnchorQuote(text="Not only did", role="inversion_trigger"),
                        AnchorQuote(text="but he also", role="paired_structure"),
                    ],
                    note_zh="Not only 位于句首时使用部分倒装。",
                )
            ],
            sentence_analyses=[],
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "Not only did he win, but he also broke the record.",
            )
        ],
        policy=_policy(),
    )

    # 旧 annotations 不变
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "grammar_note"

    # normalized_annotations 有 2 个 spans
    assert len(result.normalized_annotations) == 1
    norm_grammar = result.normalized_annotations[0]
    assert isinstance(norm_grammar, NormalizedGrammarNote)
    assert len(norm_grammar.spans) == 2
    assert norm_grammar.spans[0].text == "Not only did"
    assert norm_grammar.spans[1].text == "but he also"
    assert norm_grammar.spans[0].role == "inversion_trigger"
    assert norm_grammar.spans[1].role == "paired_structure"


def test_canonical_stats_empty_when_no_drafts() -> None:
    """无 draft 时 canonical_stats 为空但结构完整。"""
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "Hello world.")],
        policy=_policy(),
    )

    assert result.canonical_stats is not None
    assert result.canonical_stats["canonical_normalized_counts"] == {}
    assert result.canonical_stats["canonical_drop_counts_by_type"] == {}
    assert result.canonical_stats["canonical_drop_counts_by_reason"] == {}
    assert result.canonical_stats["canonical_span_count"] == 0
    assert result.canonical_stats["canonical_anchor_drop_summary"]["total_anchor_drops"] == 0


def test_canonical_drop_does_not_affect_repair_trigger() -> None:
    """canonical drop 不影响旧 repair 触发逻辑。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="NONEXISTENT")
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    # 旧 annotations 和 drop_log 不受 canonical 影响
    assert result.annotations == []
    # 旧 drop_log 有 anchor_not_substring（旧链路）
    assert any(
        e.drop_reason == "anchor_not_substring" for e in result.drop_log
    )
    # canonical_drop_log 有 quote_not_found（新链路）
    assert any(
        e.drop_reason == "quote_not_found" for e in result.canonical_drop_log
    )
    # 两个 drop_log 是独立的
    assert not any(
        e.drop_reason == "quote_not_found" for e in result.drop_log
    )


def test_sentence_analysis_in_normalized_annotations() -> None:
    """sentence_analysis 也出现在 normalized_annotations 中。"""
    analysis = DraftSentenceAnalysis(
        sentence_id="s1",
        label="主句加 result in 压缩结构",
        analysis_zh="先抓主句，再看后面的结果结构。",
    )
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=GrammarDraft(
            grammar_notes=[], sentence_analyses=[analysis]
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence(
                "s1",
                "Higher gas prices result in farmers being forced to pay more.",
            )
        ],
        policy=_policy(),
    )

    assert len(result.normalized_annotations) == 1
    assert isinstance(result.normalized_annotations[0], NormalizedSentenceAnalysis)


# ── Phase 2.3B-1: Normalized postprocess parity tests ────────────────


def test_normalized_duplicate_dropped_and_logged() -> None:
    """normalized duplicate 被 drop，canonical_drop_log 有 duplicate。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    # 旧 annotations: dedup 保留 1 个
    assert len(result.annotations) == 1

    # normalized_annotations: dedup 保留 1 个
    assert len(result.normalized_annotations) == 1
    assert result.normalized_annotations[0].type == "vocab_highlight"

    # canonical_drop_log 有 duplicate
    dup_drops = [e for e in result.canonical_drop_log if e.drop_reason == "duplicate"]
    assert len(dup_drops) == 1
    assert dup_drops[0].annotation_type == "vocab_highlight"


def test_normalized_conflict_based_on_span_overlap() -> None:
    """normalized conflict 基于 CanonicalSpan overlap 生效。

    vocab_highlight "prompted" 和 context_gloss "prompted" 在同一句
    有完全重叠的 canonical span → context_gloss 赢。
    """
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
            ],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="prompt sb to do sth",
                    anchor_quotes=[AnchorQuote(text="prompted")],
                    gloss="促使某人做某事",
                    reason="词典义不足以表达语境含义",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    # 旧 annotations: context_gloss 赢，vocab_highlight 被 subsumed
    assert any(a.type == "context_gloss" for a in result.annotations)
    assert not any(a.type == "vocab_highlight" for a in result.annotations)

    # normalized_annotations: context_gloss 赢
    norm_types = [a.type for a in result.normalized_annotations]
    assert "context_gloss" in norm_types
    # vocab_highlight 被 conflict_resolution drop
    conflict_drops = [
        e for e in result.canonical_drop_log
        if e.drop_reason == "conflict_resolution" and e.annotation_type == "vocab_highlight"
    ]
    assert len(conflict_drops) >= 1


def test_normalized_conflict_subsumed_by_phrase_gloss() -> None:
    """vocab_highlight 被 phrase_gloss 的 span 包含时 drop。

    "need" 的 span 被 "in need" 的 span 包含，两者 overlap，
    phrase_gloss 优先级更高，vocab_highlight 被 conflict_resolution drop。
    """
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="need"),
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="in need",
                    anchor_quotes=[AnchorQuote(text="in need")],
                    phrase_type="collocation",
                    zh="需要帮助的",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The family was in need of support.")],
        policy=_policy(),
    )

    # normalized: phrase_gloss 保留，vocab_highlight "need" 被 drop
    norm_types = [a.type for a in result.normalized_annotations]
    assert "phrase_gloss" in norm_types
    assert "vocab_highlight" not in norm_types

    # vocab_highlight 被 conflict_resolution drop（overlap cluster）
    conflict_drops = [
        e for e in result.canonical_drop_log
        if e.drop_reason == "conflict_resolution" and e.annotation_type == "vocab_highlight"
    ]
    assert len(conflict_drops) >= 1


def test_normalized_density_control_based_on_canonical_spans() -> None:
    """normalized density control 基于 canonical spans 生效。"""
    # annotation_density=2, 但提供 3 个 vocab_highlights
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="results"),
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
                DraftVocabHighlight(sentence_id="s1", text="team"),
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(annotation_density=2),
    )

    # 旧 annotations: density control 保留 2 个
    assert len(result.annotations) == 2

    # normalized_annotations: density control 保留 2 个
    assert len(result.normalized_annotations) == 2

    # canonical_drop_log 有 density_exceeded
    density_drops = [
        e for e in result.canonical_drop_log
        if e.drop_reason.startswith("density_exceeded")
    ]
    assert len(density_drops) == 1


def test_normalized_multi_span_preserved_after_postprocess() -> None:
    """phrase/context/grammar multi-span 在 postprocess 后仍保留 spans 顺序和 role。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="turn ... into",
                    anchor_quotes=[
                        AnchorQuote(text="Turn", role="verb"),
                        AnchorQuote(text="into", role="preposition"),
                    ],
                    phrase_type="phrasal_verb",
                    zh="把……变成……",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "Turn their passion into a stable income.")],
        policy=_policy(),
    )

    # normalized_annotations 通过 postprocess 后保留
    assert len(result.normalized_annotations) == 1
    norm = result.normalized_annotations[0]
    assert isinstance(norm, NormalizedPhraseGloss)
    assert len(norm.spans) == 2
    # spans 顺序和 role 保持
    assert norm.spans[0].text == "Turn"
    assert norm.spans[1].text == "into"
    assert norm.spans[0].role == "verb"
    assert norm.spans[1].role == "preposition"


def test_normalized_grammar_multi_span_preserved_after_postprocess() -> None:
    """grammar multi-span 在 postprocess 后仍保留 spans 顺序和 role。"""
    result = normalize_and_ground(
        vocabulary_draft=_empty_vocab(),
        grammar_draft=GrammarDraft(
            grammar_notes=[
                DraftGrammarNote(
                    sentence_id="s1",
                    grammar_point="not only 句首倒装",
                    anchor_quotes=[
                        AnchorQuote(text="Not only did", role="inversion_trigger"),
                        AnchorQuote(text="but he also", role="paired_structure"),
                    ],
                    note_zh="Not only 位于句首时使用部分倒装。",
                )
            ],
            sentence_analyses=[],
        ),
        translation_draft=_translation_draft("s1"),
        sentences=[
            _sentence("s1", "Not only did he win, but he also broke the record.")
        ],
        policy=_policy(),
    )

    assert len(result.normalized_annotations) == 1
    norm = result.normalized_annotations[0]
    assert isinstance(norm, NormalizedGrammarNote)
    assert len(norm.spans) == 2
    assert norm.spans[0].text == "Not only did"
    assert norm.spans[1].text == "but he also"
    assert norm.spans[0].role == "inversion_trigger"
    assert norm.spans[1].role == "paired_structure"


def test_old_annotations_unchanged_by_normalized_postprocess() -> None:
    """旧 annotations 行为不变，projection 兼容路径不受影响。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    # 旧 annotations: 1 个（dedup 在旧链路也生效）
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "vocab_highlight"

    # 旧 drop_log 有自己的 duplicate（来自旧链路 dedup）
    assert any(e.drop_reason == "duplicate" for e in result.drop_log)

    # canonical_drop_log 也有 duplicate（来自 normalized dedup）
    assert any(e.drop_reason == "duplicate" for e in result.canonical_drop_log)

    # 两个 drop_log 是独立的列表
    assert result.canonical_drop_log is not result.drop_log


def test_canonical_stats_reflects_postprocessed_results() -> None:
    """canonical_stats 统计 postprocessed normalized count 和 canonical drop count。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    assert result.canonical_stats is not None
    # postprocessed: 1 个 vocab_highlight
    assert result.canonical_stats["canonical_normalized_counts"]["vocab_highlight"] == 1
    # 1 个 duplicate drop
    assert result.canonical_stats["canonical_drop_counts_by_reason"].get("duplicate", 0) == 1
    # span_count = 1 (postprocessed)
    assert result.canonical_stats["canonical_span_count"] == 1


def test_normalized_context_gloss_multi_span_preserved_after_postprocess() -> None:
    """context_gloss multi-span 在 postprocess 后仍保留 spans 顺序和 role。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="prompt sb to do sth",
                    anchor_quotes=[
                        AnchorQuote(text="prompted", role="verb"),
                        AnchorQuote(text="to rethink", role="infinitive"),
                    ],
                    gloss="促使某人做某事",
                    reason="词典义不足以表达语境含义",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    assert len(result.normalized_annotations) == 1
    norm = result.normalized_annotations[0]
    assert isinstance(norm, NormalizedContextGloss)
    assert len(norm.spans) == 2
    assert norm.spans[0].text == "prompted"
    assert norm.spans[1].text == "to rethink"
    assert norm.spans[0].role == "verb"
    assert norm.spans[1].role == "infinitive"


def test_normalized_different_explanation_same_span_not_deduped() -> None:
    """不同教学解释的 annotation 即使 span 相同也不应误合并。

    例如同一个 "prompted" 既是 vocab_highlight 又是 context_gloss，
    它们 span 相同但 type 不同，不应被 dedup 合并。
    """
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompted"),
            ],
            phrase_glosses=[],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="prompt sb to do sth",
                    anchor_quotes=[AnchorQuote(text="prompted")],
                    gloss="促使某人做某事",
                    reason="词典义不足以表达语境含义",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    # dedup 不应合并不同 type 的 annotation
    # 但 conflict_resolution 会 drop 低优先级的 vocab_highlight
    # 所以最终 normalized_annotations 只有 context_gloss
    norm_types = [a.type for a in result.normalized_annotations]
    assert "context_gloss" in norm_types

    # vocab_highlight 被 conflict_resolution drop（不是 dedup）
    conflict_drops = [
        e for e in result.canonical_drop_log
        if e.drop_reason == "conflict_resolution"
    ]
    assert len(conflict_drops) >= 1


def test_normalized_partial_overlap_conflict() -> None:
    """partial overlap 的 rich annotation 也会触发 conflict。

    phrase_gloss("prompted the team") 和 context_gloss("prompted")
    有部分重叠 → context_gloss 优先级更高，phrase_gloss 被 drop。
    """
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="prompt the team",
                    anchor_quotes=[
                        AnchorQuote(text="prompted the team"),
                    ],
                    phrase_type="collocation",
                    zh="促使团队",
                )
            ],
            context_glosses=[
                DraftContextGloss(
                    sentence_id="s1",
                    display="prompt sb to do sth",
                    anchor_quotes=[AnchorQuote(text="prompted")],
                    gloss="促使某人做某事",
                    reason="词典义不足以表达语境含义",
                )
            ],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    # context_gloss 赢（优先级 3 > phrase_gloss 优先级 2）
    norm_types = [a.type for a in result.normalized_annotations]
    assert "context_gloss" in norm_types
    assert "phrase_gloss" not in norm_types

    # phrase_gloss 被 conflict_resolution drop
    conflict_drops = [
        e for e in result.canonical_drop_log
        if e.drop_reason == "conflict_resolution" and e.annotation_type == "phrase_gloss"
    ]
    assert len(conflict_drops) == 1


def test_normalized_no_overlap_no_conflict() -> None:
    """无 overlap 的 vocabulary annotation 不触发 conflict。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="results"),
            ],
            phrase_glosses=[
                DraftPhraseGloss(
                    sentence_id="s1",
                    label="rethink approach",
                    anchor_quotes=[AnchorQuote(text="rethink")],
                    phrase_type="collocation",
                    zh="重新思考方法",
                )
            ],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team to rethink their approach.")],
        policy=_policy(),
    )

    # 两个 annotation 无 overlap，都保留
    norm_types = [a.type for a in result.normalized_annotations]
    assert "vocab_highlight" in norm_types
    assert "phrase_gloss" in norm_types

    # 没有 conflict_resolution drop
    conflict_drops = [
        e for e in result.canonical_drop_log
        if e.drop_reason == "conflict_resolution"
    ]
    assert len(conflict_drops) == 0


def test_quote_boundary_violation_enters_canonical_anchor_drop_summary() -> None:
    """quote_boundary_violation 应进入 canonical_anchor_drop_summary。"""
    result = normalize_and_ground(
        vocabulary_draft=VocabularyDraft(
            vocab_highlights=[
                DraftVocabHighlight(sentence_id="s1", text="prompt"),
            ],
            phrase_glosses=[],
            context_glosses=[],
        ),
        grammar_draft=_empty_grammar(),
        translation_draft=_translation_draft("s1"),
        sentences=[_sentence("s1", "The results prompted the team.")],
        policy=_policy(),
    )

    # "prompt" is a prefix inside "prompted" → quote_boundary_violation
    assert result.canonical_stats is not None
    assert result.canonical_stats["canonical_drop_counts_by_reason"].get(
        "quote_boundary_violation", 0
    ) >= 1
    # Should also be counted in anchor drop summary
    assert result.canonical_stats["canonical_anchor_drop_summary"]["total_anchor_drops"] >= 1
    # Verify the reason appears in anchor drop summary
    reasons = {
        entry["drop_reason"]
        for entry in result.canonical_stats["canonical_anchor_drop_summary"][
            "by_annotation_type_and_reason"
        ]
    }
    assert "quote_boundary_violation" in reasons
