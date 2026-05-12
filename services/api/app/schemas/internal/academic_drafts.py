from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.internal.analysis import BASE_MODEL_CONFIG


class TermNote(BaseModel):
    model_config = BASE_MODEL_CONFIG

    type: Literal["term_note"] = "term_note"
    sentence_ids: list[str] = Field(
        min_length=1,
        description="该术语出现的句子ID列表。支持跨句标注。",
    )
    text: str = Field(
        min_length=1,
        description="原文中的术语或概念表达。必须是精确子串。",
    )
    occurrence: int | None = Field(
        default=None, ge=1,
        description="同一句中该文本第几次出现",
    )
    term_category: Literal[
        "technical",
        "sub_technical",
        "abbreviation",
        "notation",
        "concept_opposition",
    ] = Field(
        description="术语类别：technical=专业术语, sub_technical=半技术词汇, abbreviation=缩写, notation=变量/符号/公式引用, concept_opposition=概念对立（如 nature vs. nurture）",
    )
    zh: str = Field(
        min_length=1,
        description="中文术语翻译。不确定时标注 zh_uncertain=True。",
    )
    zh_uncertain: bool = Field(
        default=False,
        description="术语翻译是否不确定（一词多译、领域不明时为 True）。",
    )
    context_definition: str = Field(
        min_length=1,
        description="在本文语境下的具体含义。必须说明为什么在这个语境下是这个意思。",
    )
    discipline: str | None = Field(
        default=None,
        description="所属学科领域。无法从文本推断时必须为 None。",
    )


class TermDraft(BaseModel):
    model_config = BASE_MODEL_CONFIG

    term_notes: list[TermNote] = Field(
        default_factory=list,
        description="术语标注列表",
    )


class AcademicSentenceTranslation(BaseModel):
    model_config = BASE_MODEL_CONFIG

    sentence_id: str = Field(description="句子ID")
    translation_zh: str = Field(
        min_length=1,
        description="研究阅读级准确翻译。必须保留 hedging 和限定条件。",
    )
    translation_notes: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "翻译决策说明。只在以下情况输出："
            "1) 术语翻译不确定；"
            "2) 主动保留了 hedging/限定条件；"
            "3) 做了拆句处理。"
        ),
    )


class AcademicTranslationDraft(BaseModel):
    model_config = BASE_MODEL_CONFIG

    title: str = Field(
        min_length=1,
        max_length=80,
        description="基于文本内容生成的中文标题，用于历史记录展示。",
    )
    sentence_translations: list[AcademicSentenceTranslation] = Field(
        default_factory=list,
        description="全量逐句翻译",
    )


class LogicNote(BaseModel):
    model_config = BASE_MODEL_CONFIG

    type: Literal["logic_note"] = "logic_note"
    sentence_ids: list[str] = Field(
        min_length=1,
        description="该逻辑关系涉及的句子列表。支持跨句标注。",
    )
    logic_type: Literal[
        "contrast",
        "causation",
        "concession",
        "condition",
        "evidence",
        "elaboration",
        "transition",
        "limitation",
        "hypothesis",
        "conclusion",
    ] = Field(
        description="逻辑关系类型：contrast=对比/转折, causation=因果, concession=让步, condition=条件/假设, evidence=证据支撑, elaboration=阐释/展开, transition=过渡/衔接, limitation=限定, hypothesis=假设, conclusion=结论",
    )
    anchor_text: str = Field(
        min_length=1,
        description="触发该逻辑关系的原文表达。必须是精确子串。",
    )
    occurrence: int | None = Field(
        default=None, ge=1,
        description="同一句中该文本第几次出现",
    )
    explanation: str = Field(
        max_length=120,
        description="对该逻辑关系的简要解释。不超过 2 句话。",
    )
    hedging_detected: bool = Field(
        default=False,
        description="该逻辑关系中是否包含模糊限制语。",
    )
    hedging_words: list[str] = Field(
        default_factory=list,
        description="检测到的 hedging 表达原文。",
    )


class InterpretationNote(BaseModel):
    model_config = BASE_MODEL_CONFIG

    type: Literal["interpretation_note"] = "interpretation_note"
    sentence_id: str = Field(description="被解释的句子ID")
    interpretation: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "对句意的解释性改写。只在以下情况输出："
            "1) 直译会产生严重理解偏差；"
            "2) 需要消解指代或省略才能理解。"
            "普通句子不需要解释性改写。"
        ),
    )
    interpretation_type: Literal[
        "decontextualization",
        "disambiguation",
    ] = Field(
        description="解释类型：decontextualization=消解指代/省略, disambiguation=消除歧义",
    )


class ParagraphRole(BaseModel):
    model_config = BASE_MODEL_CONFIG

    type: Literal["paragraph_role"] = "paragraph_role"
    paragraph_id: str = Field(description="段落ID")
    role: Literal[
        "background", "problem", "objective", "method",
        "result", "discussion", "conclusion",
    ] = Field(description="段落角色")
    confidence: Literal["high", "medium"] = Field(
        description="角色判定的置信度。medium 时前端不显示角色标签。",
    )
    summary: str = Field(
        max_length=80,
        description="该段落的一句话功能摘要。",
    )


class ContentSummary(BaseModel):
    model_config = BASE_MODEL_CONFIG

    completeness: Literal["full", "partial", "minimal"] = Field(
        description="full=可提取核心要素; partial=部分可提取; minimal=只能给片段概要。",
    )
    overview: str = Field(
        min_length=1,
        description="内容概要。full 时为结构化摘要; partial/minimal 时为片段概要。",
    )
    research_question: str | None = Field(default=None)
    methodology: str | None = Field(default=None)
    key_findings: list[str] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list, max_length=2)


class UnderstandingDraft(BaseModel):
    model_config = BASE_MODEL_CONFIG

    @model_validator(mode='before')
    @classmethod
    def _coerce_json_string_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in ('content_summary',):
            val = data.get(key)
            if isinstance(val, str):
                try:
                    data[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    data[key] = None
        return data

    logic_notes: list[LogicNote] = Field(
        default_factory=list,
        description="逻辑关系标注。P1 必做。",
    )
    interpretation_notes: list[InterpretationNote] = Field(
        default_factory=list,
        description="解释性改写。P1 必做，但允许为空——不是每句都需要解释。",
    )
    paragraph_roles: list[ParagraphRole] = Field(
        default_factory=list,
        description="段落角色标注。P2 可选，允许为空。",
    )
    content_summary: ContentSummary | None = Field(
        default=None,
        description="内容概要。P2 可选，允许为空。",
    )
