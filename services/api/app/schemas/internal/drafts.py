"""Draft annotation schemas for LLM output layer.

Phase 1 引入 AnchorQuote 概念和 DraftAnnotation union，
将 LLM 输出层的 display 字段与 source evidence 字段分离。
analysis.py 中的旧类型（VocabHighlight/PhraseGloss/ContextGloss/GrammarNote/SentenceAnalysis）
保留给 normalize/projection 链路，Phase 1 不改。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.internal.analysis import (
    BASE_MODEL_CONFIG,
    Chunk,
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    SentenceAnalysis,
    SentenceTranslation,
    SpanRef,
    VocabHighlight,
    is_likely_basic_english_word,
    is_single_token,
)

# ── AnchorQuote ─────────────────────────────────────────────────────


class AnchorQuote(BaseModel):
    """LLM 输出的原文逐字引用，用作锚点证据。"""

    model_config = BASE_MODEL_CONFIG

    text: str = Field(
        min_length=1,
        description=(
            "从原句逐字复制的连续片段。"
            "必须与原句中对应文本完全一致，包括大小写、标点、引号、撇号、连字符；"
            "不允许省略号、不允许改写、不允许概括性文本。"
        ),
    )
    role: str | None = Field(
        default=None,
        description=(
            "结构角色（可选，如 verb、preposition、inversion_trigger、"
            "paired_structure 等）"
        ),
    )


# ── Draft annotation types ──────────────────────────────────────────


class DraftVocabHighlight(BaseModel):
    """Draft 层 vocab_highlight：单词级原文引用。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["vocab_highlight"] = "vocab_highlight"
    sentence_id: str = Field(description="句子ID")
    text: str = Field(
        min_length=1,
        description=(
            "用于前端原文高亮的单个英文词锚点。"
            "必须逐字复制原句中的单个词形；"
            "不能含空格，不得改成词典原形或其他非原句 surface form。"
            "多词表达请使用 DraftPhraseGloss 或 DraftContextGloss。"
        ),
    )
    # 不含 occurrence：第一版不让 LLM 负责消歧，
    # 重复/歧义交给 backend resolve 后 drop

    @field_validator("text")
    @classmethod
    def validate_single_word(cls, value: str) -> str:
        if " " in value:
            raise ValueError(
                "DraftVocabHighlight.text must be a single word "
                "without spaces"
            )
        return value


class DraftPhraseGloss(BaseModel):
    """Draft 层 phrase_gloss：展示标签与原文锚点证据分离。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["phrase_gloss"] = "phrase_gloss"
    sentence_id: str = Field(description="句子ID")
    label: str = Field(
        min_length=1,
        description=(
            "短语卡片标题 / 教学短语名。可以是原句中的连续短语，"
            "也可以是 turn ... into、refer to ... as 这类教学短语名。"
            "不要求是原文子串。"
        ),
    )
    anchor_quotes: list[AnchorQuote] = Field(
        min_length=1,
        max_length=4,
        description=(
            "1-4 个原文锚点引用。每个 quote.text 必须逐字复制原句中的"
            "连续片段，不允许省略号或改写。"
            "连续短语 1 个 quote，不连续短语 2-4 个 quotes。"
        ),
    )
    phrase_type: Literal[
        "collocation", "phrasal_verb", "idiom",
        "proper_noun", "compound",
    ] = Field(
        description=(
            "短语类型。collocation 为默认的常见搭配；"
            "phrasal_verb 用于以动词为核心的整体动作短语；"
            "idiom 仅用于明显非字面或高度固定的惯用表达；"
            "proper_noun 仅用于正式命名的专名；"
            "compound 用于稳定的多词概念名词、术语或类别名称。"
        ),
    )
    zh: str = Field(min_length=1, description="中文释义")

    @model_validator(mode="after")
    def validate_draft_phrase(self) -> DraftPhraseGloss:
        if (
            is_single_token(self.label)
            and self.phrase_type not in {"proper_noun", "compound"}
        ):
            raise ValueError(
                "Single-token label only allowed for "
                "proper_noun or compound"
            )
        if (
            self.phrase_type == "proper_noun"
            and is_likely_basic_english_word(self.label)
        ):
            raise ValueError(
                "proper_noun must not use a basic English word"
            )
        return self


class DraftContextGloss(BaseModel):
    """Draft 层 context_gloss：展示字段与原文锚点证据分离。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["context_gloss"] = "context_gloss"
    sentence_id: str = Field(description="句子ID")
    display: str = Field(
        min_length=1,
        description=(
            "语境义展示文本。可以是原句中的连续片段，"
            "也可以是示意性框架（如 refer to ... as）。"
            "不要求是原文子串。"
        ),
    )
    anchor_quotes: list[AnchorQuote] = Field(
        min_length=1,
        max_length=4,
        description=(
            "1-4 个原文锚点引用。每个 quote.text 必须逐字复制原句中的"
            "连续片段，不允许省略号或改写。"
        ),
    )
    gloss: str = Field(
        min_length=1, description="当前语境下的准确含义",
    )
    reason: str = Field(
        min_length=1,
        description="说明为什么词典义不足以解释当前句意",
    )


class DraftGrammarNote(BaseModel):
    """Draft 层 grammar_note：语法点展示与原文锚点证据分离。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["grammar_note"] = "grammar_note"
    sentence_id: str = Field(description="句子ID")
    grammar_point: str = Field(
        min_length=1,
        description=(
            "语法点名称，可以是抽象表达，"
            "如 'not only 句首倒装'、'with 复合结构'"
        ),
    )
    pattern: str | None = Field(
        default=None,
        description=(
            "抽象语法模式，如 "
            "'Not only + auxiliary + subject + verb'"
        ),
    )
    anchor_quotes: list[AnchorQuote] = Field(
        min_length=1,
        max_length=4,
        description=(
            "1-4 个原文锚点引用。每个 quote.text 必须逐字复制原句中的"
            "连续片段，不允许省略号或改写。"
        ),
    )
    note_zh: str = Field(min_length=1, description="中文说明")


class DraftSentenceAnalysis(BaseModel):
    """Draft 层 sentence_analysis：与现有 SentenceAnalysis 相同。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["sentence_analysis"] = "sentence_analysis"
    sentence_id: str = Field(description="句子ID")
    label: str = Field(min_length=1, description="句型概述")
    analysis_zh: str = Field(
        min_length=1,
        description="中文解析，说明句子主干、层次关系和理解难点",
    )
    chunks: list[Chunk] | None = Field(
        default=None,
        description=(
            "按阅读顺序拆解的句子成分。默认应提供 2-6 个 chunks；"
            "只有在确实无法稳定拆出真实子串时才允许为空。"
        ),
    )


DraftAnnotation = Annotated[
    (
        DraftVocabHighlight
        | DraftPhraseGloss
        | DraftContextGloss
        | DraftGrammarNote
        | DraftSentenceAnalysis
    ),
    Field(discriminator="type"),
]


# ── Draft → Annotation 兼容转换 ────────────────────────────────────


def draft_to_annotation(draft: DraftAnnotation) -> (
    VocabHighlight | PhraseGloss | ContextGloss
    | GrammarNote | SentenceAnalysis
):
    """将 DraftAnnotation 转换为当前 Annotation 类型。

    供 normalize/projection 链路使用。
    Phase 1 兼容层：Draft 类型 → 旧 Annotation 类型。

    使用 model_construct 避免重复校验；
    Draft 可能通过 model_construct 绕过校验（如测试），
    归一化链路负责处理无效数据。
    """
    if isinstance(draft, DraftVocabHighlight):
        return VocabHighlight.model_construct(
            type="vocab_highlight",
            sentence_id=draft.sentence_id,
            text=draft.text,
            occurrence=None,
        )
    if isinstance(draft, DraftPhraseGloss):
        spans = [
            SpanRef(text=q.text, occurrence=None, role=q.role)
            for q in draft.anchor_quotes
        ]
        return PhraseGloss.model_construct(
            type="phrase_gloss",
            sentence_id=draft.sentence_id,
            text=draft.label,
            spans=spans,
            occurrence=None,
            phrase_type=draft.phrase_type,
            zh=draft.zh,
        )
    if isinstance(draft, DraftContextGloss):
        # 全量转换 anchor_quotes → spans
        spans = [
            SpanRef(text=q.text, occurrence=None, role=q.role)
            for q in draft.anchor_quotes
        ]
        # 取第一个 quote 的 text 作为兼容 text（原文绑定用）
        text = (
            draft.anchor_quotes[0].text
            if draft.anchor_quotes else draft.display
        )
        # display 与 text 不同时保留 display（前端展示用）
        display = draft.display if draft.display != text else None
        return ContextGloss.model_construct(
            type="context_gloss",
            sentence_id=draft.sentence_id,
            text=text,
            display=display,
            spans=spans,
            occurrence=None,
            gloss=draft.gloss,
            reason=draft.reason,
        )
    if isinstance(draft, DraftGrammarNote):
        spans = [
            SpanRef(text=q.text, occurrence=None, role=q.role)
            for q in draft.anchor_quotes
        ]
        return GrammarNote.model_construct(
            type="grammar_note",
            sentence_id=draft.sentence_id,
            spans=spans,
            label=draft.grammar_point,
            note_zh=draft.note_zh,
        )
    if isinstance(draft, DraftSentenceAnalysis):
        return SentenceAnalysis.model_construct(
            type="sentence_analysis",
            sentence_id=draft.sentence_id,
            label=draft.label,
            analysis_zh=draft.analysis_zh,
            chunks=draft.chunks,
        )
    raise ValueError(f"Unknown DraftAnnotation type: {type(draft)}")


# ── Draft containers ────────────────────────────────────────────────


class VocabularyDraft(BaseModel):
    """Vocabulary agent 产出的标注草案。

    包含 vocab_highlight、phrase_gloss、context_gloss 三类词汇维度标注。
    设计原则：
    - 不负责语法说明、长难句拆解、逐句翻译
    - 允许漏标，不允许大面积低价值误标
    """

    model_config = BASE_MODEL_CONFIG

    vocab_highlights: list[DraftVocabHighlight] = Field(
        default_factory=list,
        description="高价值单词高亮列表",
    )
    phrase_glosses: list[DraftPhraseGloss] = Field(
        default_factory=list,
        description="需要整体解释的短语、术语或专名列表",
    )
    context_glosses: list[DraftContextGloss] = Field(
        default_factory=list,
        description="语境义标注列表",
    )


class GrammarDraft(BaseModel):
    """Grammar agent 产出的标注草案。

    包含 grammar_note、sentence_analysis 两类结构维度标注。
    设计原则：
    - 不负责词汇标注、词典查语、逐句翻译
    - 优先覆盖显著复杂句，不追求数量
    - sentence_analysis.chunks 允许为空，但默认应提供
    """

    model_config = BASE_MODEL_CONFIG

    grammar_notes: list[DraftGrammarNote] = Field(
        default_factory=list,
        description="语法旁注列表",
    )
    sentence_analyses: list[DraftSentenceAnalysis] = Field(
        default_factory=list,
        description=(
            "长难句拆解列表"
            "（chunks 默认应提供，仅在无法稳定拆块时允许为空）"
        ),
    )


class TranslationDraft(BaseModel):
    """Translation agent 产出的翻译草案。

    设计原则：
    - 逐句翻译完整优先于风格花哨
    - 独立完成，不依赖 annotation 链路
    - 缺失应有明确 warning，不允许静默吞掉
    """

    model_config = BASE_MODEL_CONFIG

    title: str = Field(
        min_length=1,
        max_length=80,
        description="基于全文内容生成的中文标题，用于历史记录展示。",
    )
    sentence_translations: list[SentenceTranslation] = Field(
        default_factory=list,
        description="全量逐句翻译",
    )
