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
