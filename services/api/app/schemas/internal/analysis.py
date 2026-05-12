from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import TextSpan

ReadingGoal = Literal["exam", "daily_reading", "academic"]
ReadingVariant = Literal[
    "gaokao",
    "cet",
    "kaoyan",
    "tem",
    "ielts_toefl",
    "beginner_reading",
    "intermediate_reading",
    "intensive_reading",
    "academic_general",
]
AnnotationStyle = Literal["exam_oriented", "plain_and_supportive", "structural_and_academic"]
TranslationStyle = Literal["exam", "natural", "academic", "literal_support", "nuanced_aesthetic"]
GrammarGranularity = Literal["focused", "balanced", "structural", "explicit_split", "structural_logic", "explicit_exam", "speed_support", "rhetorical", "info_extraction"]
VocabularyPolicy = Literal["high_value_only", "exam_priority", "academic_priority", "semantic_nuance", "exam_depth", "literary_depth", "academic_strategy"]

BASE_MODEL_CONFIG = ConfigDict(extra="ignore", str_strip_whitespace=True)
PHRASE_TYPES = ("collocation", "phrasal_verb", "idiom", "proper_noun", "compound")


def is_single_token(text: str) -> bool:
    return len(text.split()) == 1


def is_likely_basic_english_word(text: str) -> bool:
    token = text.strip()
    if not token or " " in token:
        return False
    if not token.isalpha():
        return False
    if token.islower():
        return True
    if token[0].isupper() and token[1:].islower():
        return True
    return False


class UserRules(BaseModel):
    model_config = BASE_MODEL_CONFIG

    profile_id: str = Field(description="规则包ID")
    reading_goal: ReadingGoal = Field(description="阅读目标")
    reading_variant: ReadingVariant = Field(description="阅读场景")
    annotation_style: AnnotationStyle = Field(description="标注风格")
    translation_style: TranslationStyle = Field(description="翻译风格")
    grammar_granularity: GrammarGranularity = Field(description="语法颗粒度")
    vocabulary_policy: VocabularyPolicy = Field(description="词汇筛选策略")


class SanitizeReport(BaseModel):
    actions: list[str] = Field(
        default_factory=list, description="输入清洗阶段实际执行的规范化或剔除动作。"
    )
    removed_segment_count: int = Field(
        ge=0, default=0, description="被清洗逻辑剔除或替换的噪音片段数量。"
    )


class PreparedParagraph(BaseModel):
    """输入预处理后的段落。"""

    paragraph_id: str = Field(description="段落稳定标识。")
    text: str = Field(description="清洗后用于渲染的段落文本。")
    render_span: TextSpan = Field(description="段落在 render_text 中的绝对坐标。")
    sentence_ids: list[str] = Field(default_factory=list, description="该段落包含的句子标识列表。")


class PreparedSentence(BaseModel):
    """输入预处理后的句子。"""

    sentence_id: str = Field(description="句子稳定标识。")
    paragraph_id: str = Field(description="所属段落标识。")
    text: str = Field(description="清洗后的句子文本。")
    sentence_span: TextSpan = Field(description="句子在 render_text 中的绝对坐标。")


class PreparedInput(BaseModel):
    source_text: str = Field(description='用户输入的原始文本，用于结果页的"查看原文"模块。')
    render_text: str = Field(description="清洗后可安全渲染、可定位的正文文本。")
    paragraphs: list[PreparedParagraph] = Field(default_factory=list, description="清洗后的段落列表。")
    sentences: list[PreparedSentence] = Field(default_factory=list, description="清洗后的句子列表。")
    sanitize_report: SanitizeReport = Field(description="输入清洗报告。")
    english_ratio: float = Field(ge=0.0, le=1.0, description="render_text 中英文字符的粗略占比。")
    noise_ratio: float = Field(ge=0.0, le=1.0, description="原始输入中被视为噪音并被处理的比例。")
    text_type: Literal[
        "article_en", "article_mixed", "structured_doc", "html_like", "code_like", "other"
    ] = Field(description="本地粗判得到的文本类型。")
    fast_path: bool = Field(
        default=False,
        description="是否走了快路径（规范英文正文 + spaCy 断句）。",
    )
    language_detected: str | None = Field(
        default=None,
        description="ISO 639-1 主语言码（如 'en'、'zh'）。",
    )


class SentenceTranslation(BaseModel):
    """逐句翻译。"""

    model_config = BASE_MODEL_CONFIG

    sentence_id: str = Field(description="句子ID")
    translation_zh: str = Field(min_length=1, description="中文翻译")


class SpanRef(BaseModel):
    """语法锚点片段。"""

    model_config = BASE_MODEL_CONFIG

    text: str = Field(min_length=1, description="对应句子中的精确子串")
    occurrence: int | None = Field(
        default=None, ge=1, description="同一句中该文本第几次出现"
    )
    role: str | None = Field(default=None, description="结构角色（可选）")


class Chunk(BaseModel):
    """长难句拆解的成分块。"""

    model_config = BASE_MODEL_CONFIG

    order: int = Field(ge=1, description="阅读顺序，从1开始。")
    label: str = Field(min_length=1, description="成分名称")
    text: str = Field(min_length=1, description="对应句子中的精确子串")
    occurrence: int | None = Field(default=None, ge=1, description="同一句中该文本第几次出现")


class VocabHighlight(BaseModel):
    """高价值单词高亮。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["vocab_highlight"] = "vocab_highlight"
    sentence_id: str = Field(description="句子ID")
    text: str = Field(
        min_length=1,
        description="原文中的单个英文词。不能含空格；多词表达请使用 PhraseGloss 或 ContextGloss。",
    )
    occurrence: int | None = Field(default=None, ge=1, description="同一句中该文本第几次出现")

    @field_validator("text")
    @classmethod
    def validate_single_word(cls, value: str) -> str:
        if " " in value:
            raise ValueError("VocabHighlight.text must be a single word without spaces")
        return value


class PhraseGloss(BaseModel):
    """整体释义短语。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["phrase_gloss"] = "phrase_gloss"
    sentence_id: str = Field(description="句子ID")
    text: str = Field(
        min_length=1,
        description=(
            "需要整体解释的原文表达。默认应为多词表达；若为单个词，只允许在需要整体解释的专有名词"
            "或紧密复合词场景下出现。"
        ),
    )
    occurrence: int | None = Field(default=None, ge=1, description="同一句中该文本第几次出现")
    phrase_type: Literal["collocation", "phrasal_verb", "idiom", "proper_noun", "compound"] = Field(
        description="短语类型。proper_noun 仅用于确实需要整体说明的专名。"
    )
    zh: str = Field(min_length=1, description="中文释义")

    @model_validator(mode="after")
    def validate_phrase_semantics(self) -> "PhraseGloss":
        if is_single_token(self.text) and self.phrase_type not in {"proper_noun", "compound"}:
            raise ValueError("Single-token PhraseGloss is only allowed for proper_noun or compound")
        if self.phrase_type == "proper_noun" and is_likely_basic_english_word(self.text):
            raise ValueError("proper_noun PhraseGloss must not use a basic English word")
        return self


class ContextGloss(BaseModel):
    """语境义标注。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["context_gloss"] = "context_gloss"
    sentence_id: str = Field(description="句子ID")
    text: str = Field(
        min_length=1,
        description=(
            "原文中的词或表达。用于词典义不足以解释当前语境的情况；若只是固定搭配整体义，优先使用"
            " PhraseGloss。"
        ),
    )
    occurrence: int | None = Field(default=None, ge=1, description="同一句中该文本第几次出现")
    gloss: str = Field(min_length=1, description="当前语境下的准确含义")
    reason: str = Field(min_length=1, description="说明为什么词典义不足以解释当前句意")


class GrammarNote(BaseModel):
    """语法旁注。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["grammar_note"] = "grammar_note"
    sentence_id: str = Field(description="句子ID")
    spans: list[SpanRef] = Field(min_length=1, max_length=4, description="语法锚点片段")
    label: str = Field(min_length=1, description="语法点名称")
    note_zh: str = Field(min_length=1, description="中文说明")


class SentenceAnalysis(BaseModel):
    """长难句拆解。"""

    model_config = BASE_MODEL_CONFIG

    type: Literal["sentence_analysis"] = "sentence_analysis"
    sentence_id: str = Field(description="句子ID")
    label: str = Field(min_length=1, description="句型概述")
    analysis_zh: str = Field(min_length=1, description="中文解析，说明句子主干、层次关系和理解难点")
    chunks: list[Chunk] | None = Field(
        default=None,
        description=(
            "按阅读顺序拆解的句子成分。默认应提供 2-6 个 chunks；"
            "只有在确实无法稳定拆出真实子串时才允许为空。"
        ),
    )


Annotation = Annotated[
    VocabHighlight | PhraseGloss | ContextGloss | GrammarNote | SentenceAnalysis,
    Field(discriminator="type"),
]


class AnnotationOutput(BaseModel):
    """LLM 生成的 annotation 结果（含全量逐句翻译）。"""

    model_config = BASE_MODEL_CONFIG

    annotations: list[Annotation] = Field(description="结构化标注列表")
    sentence_translations: list[SentenceTranslation] = Field(description="全量逐句翻译")
