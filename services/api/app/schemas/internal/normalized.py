"""Normalized annotation schemas for backend trusted result layer.

Phase 2 引入 CanonicalSpan 与 NormalizedAnnotation union，
将后端 resolved 的可信结果与 LLM Draft 层彻底分离。
旧 Annotation union 保留给兼容链路，子任务 3 统一切换。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.internal.analysis import (
    BASE_MODEL_CONFIG,
    Annotation,
    Chunk,
    SentenceTranslation,
)

# ── CanonicalSpan ──────────────────────────────────────────────────


class CanonicalSpan(BaseModel):
    """后端 resolved 的可信锚点 span。

    start/end 使用 Python 侧绝对偏移（相对 render_text），
    投影到 RenderScene 时再转 UTF-16。
    text 必须等于 render_text[start:end]。
    """

    model_config = BASE_MODEL_CONFIG

    sentence_id: str = Field(description="句子ID")
    start: int = Field(
        ge=0,
        description="相对于 render_text 的绝对起始偏移 (0-based)",
    )
    end: int = Field(
        gt=0,
        description="相对于 render_text 的绝对结束偏移 (半开区间 [start, end))",
    )
    text: str = Field(
        min_length=1,
        description="render_text[start:end] 的原文切片",
    )
    role: str | None = Field(
        default=None,
        description="结构角色（来自 AnchorQuote.role）",
    )
    source_quote: str | None = Field(
        default=None,
        description="原始 LLM quote 文本（用于 debug / eval）",
    )
    resolution_kind: Literal[
        "exact",
        "canonicalized",
        "boundary_trimmed",
    ] = Field(description="resolve 方式")
    occurrence: int | None = Field(
        default=None,
        ge=1,
        description="同一句中该 text 第几次出现（仅多次出现时设置）",
    )

    @model_validator(mode="after")
    def validate_range(self) -> CanonicalSpan:
        if self.end <= self.start:
            raise ValueError(
                f"CanonicalSpan.end ({self.end}) must be greater "
                f"than start ({self.start})"
            )
        return self


# ── Normalized annotation types ────────────────────────────────────


class NormalizedVocabHighlight(BaseModel):
    """Normalized 层 vocab_highlight：单词级 canonical span。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["vocab_highlight"] = "vocab_highlight"
    sentence_id: str = Field(description="句子ID")
    spans: list[CanonicalSpan] = Field(
        min_length=1,
        max_length=1,
        description="单个 canonical span，单词级",
    )


class NormalizedPhraseGloss(BaseModel):
    """Normalized 层 phrase_gloss：展示标签与 canonical spans。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["phrase_gloss"] = "phrase_gloss"
    sentence_id: str = Field(description="句子ID")
    spans: list[CanonicalSpan] = Field(
        min_length=1,
        max_length=4,
        description="1-4 个 canonical spans",
    )
    label: str = Field(
        min_length=1,
        description="短语卡片标题 / 教学短语名（来自 DraftPhraseGloss.label）",
    )
    phrase_type: Literal[
        "collocation", "phrasal_verb", "idiom",
        "proper_noun", "compound",
    ] = Field(description="短语类型")
    zh: str = Field(min_length=1, description="中文释义")


class NormalizedContextGloss(BaseModel):
    """Normalized 层 context_gloss：展示文本与 canonical spans。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["context_gloss"] = "context_gloss"
    sentence_id: str = Field(description="句子ID")
    spans: list[CanonicalSpan] = Field(
        min_length=1,
        max_length=4,
        description="1-4 个 canonical spans",
    )
    display: str = Field(
        min_length=1,
        description="语境义展示文本（来自 DraftContextGloss.display）",
    )
    gloss: str = Field(min_length=1, description="当前语境下的准确含义")
    reason: str = Field(
        min_length=1,
        description="说明为什么词典义不足以解释当前句意",
    )


class NormalizedGrammarNote(BaseModel):
    """Normalized 层 grammar_note：语法点与 canonical spans。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["grammar_note"] = "grammar_note"
    sentence_id: str = Field(description="句子ID")
    spans: list[CanonicalSpan] = Field(
        min_length=1,
        max_length=4,
        description="1-4 个 canonical spans",
    )
    grammar_point: str = Field(
        min_length=1,
        description="语法点名称（来自 DraftGrammarNote.grammar_point）",
    )
    pattern: str | None = Field(
        default=None,
        description="抽象语法模式（来自 DraftGrammarNote.pattern）",
    )
    note_zh: str = Field(min_length=1, description="中文说明")


class NormalizedSentenceAnalysis(BaseModel):
    """Normalized 层 sentence_analysis：句型拆解。"""

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
        description="按阅读顺序拆解的句子成分（暂不转 CanonicalSpan）",
    )


NormalizedAnnotation = Annotated[
    (
        NormalizedVocabHighlight
        | NormalizedPhraseGloss
        | NormalizedContextGloss
        | NormalizedGrammarNote
        | NormalizedSentenceAnalysis
    ),
    Field(discriminator="type"),
]


# ── Drop log ───────────────────────────────────────────────────────


class DropLogEntry(BaseModel):
    """normalize_and_ground 阶段的删除/降级日志。

    记录所有被删除的候选标注及其原因。
    """

    model_config = BASE_MODEL_CONFIG

    source_agent: Literal[
        "vocabulary", "grammar", "translation", "term", "understanding"
    ] = Field(description="来源 agent")
    annotation_type: str = Field(
        description="被删除的标注类型，如 vocab_highlight、phrase_gloss 等"
    )
    sentence_id: str = Field(description="句子ID")
    anchor_text: str = Field(description="锚定文本（用于追溯）")
    drop_reason: str = Field(
        description=(
            "删除原因，如 duplicate、low_value、anchor_invalid、"
            "quote_not_found、quote_ambiguous、quote_out_of_order、"
            "quote_too_short、conflict 等"
        ),
    )
    drop_stage: Literal[
        "grounding",
        "deduplication",
        "conflict_resolution",
        "density_control",
        "pruning",
        "repair",
    ] = Field(description="删除发生的阶段")
    dropped_at: datetime = Field(
        default_factory=datetime.now,
        description="删除时间戳",
    )


# ── Result container ───────────────────────────────────────────────


class NormalizedAnnotationResult(BaseModel):
    """归一化后的标注结果。

    经过 normalize_and_ground 阶段处理后：
    - 已完成 substring grounding
    - 已校验 sentence_id
    - 已处理 occurrence
    - 已去重
    - 已消解类型冲突
    - 已裁剪低价值标注
    - 记录所有删除日志
    """

    model_config = BASE_MODEL_CONFIG

    annotations: list[Annotation] = Field(
        default_factory=list,
        description="归一化后的标注列表（旧字段，子任务 3 切换为 NormalizedAnnotation）",
    )
    normalized_annotations: list[NormalizedAnnotation] = Field(
        default_factory=list,
        description="归一化后的标注列表（新字段，Phase 2 子任务 3 统一后替换 annotations）",
    )
    sentence_translations: list[SentenceTranslation] = Field(
        default_factory=list,
        description="归一化后的逐句翻译",
    )
    drop_log: list[DropLogEntry] = Field(
        default_factory=list,
        description="删除/降级日志列表",
    )
    canonical_stats: dict[str, object] | None = Field(
        default=None,
        description=(
            "Canonical shadow path 观测指标。"
            "包含 canonical_normalized_counts、canonical_drop_counts_by_type、"
            "canonical_drop_counts_by_reason、canonical_span_count、"
            "canonical_anchor_drop_summary。"
            "Phase 2.3A 新增，不影响旧 annotations 行为。"
        ),
    )
    canonical_drop_log: list[DropLogEntry] = Field(
        default_factory=list,
        description=(
            "Canonical shadow path 的 drop log。"
            "记录 draft_to_normalized_annotation 转换失败的条目，"
            "与旧 drop_log 独立，不影响 repair 触发逻辑。"
        ),
    )
