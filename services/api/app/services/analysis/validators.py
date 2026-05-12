"""
Annotation 校验器

负责校验 LLM 输出的 annotation 是否符合规范。

校验规则：

1. VocabHighlight：
   - text 必须是 sentence 的真实子串
   - 不允许有释义字段（由后端词典接口提供）

2. PhraseGloss：
   - text 必须是 sentence 的真实子串
   - phrase_type 必须是预定义集合
   - zh 必须为中文

3. ContextGloss：
   - text 必须是 sentence 的真实子串
   - gloss 和 reason 必须为中文

4. GrammarNote：
   - spans 中每个 text 必须是 sentence 的真实子串
   - note_zh 必须为中文

5. SentenceAnalysis：
   - chunks 中每个 text 必须是 sentence 的真实子串
   - chunks 按 order 排序
   - analysis_zh 必须为中文
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.schemas.internal.analysis import (
    AnnotationOutput,
    ContextGloss,
    GrammarNote,
    PhraseGloss,
    SentenceAnalysis,
    VocabHighlight,
)

if TYPE_CHECKING:
    from app.services.analysis.preprocess.input_preparation import PreparedInput

logger = logging.getLogger(__name__)

# 允许的 PhraseType
ALLOWED_PHRASE_TYPES: set[str] = {"collocation", "phrasal_verb", "idiom", "proper_noun", "compound"}

# 中文字符正则
CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _is_chinese(text: str) -> bool:
    """检查是否包含中文字符"""
    return bool(CHINESE_PATTERN.search(text))


def _is_substring(text: str, sentence_text: str) -> bool:
    """检查 text 是否为 sentence_text 的子串"""
    return text in sentence_text


class ValidationResult:
    def __init__(self) -> None:
        self.errors: list[dict[str, object]] = []
        self.warnings: list[dict[str, object]] = []

    def add_error(self, code: str, message: str, **kwargs: object) -> None:
        self.errors.append({"code": code, "message": message, **kwargs})

    def add_warning(self, code: str, message: str, **kwargs: object) -> None:
        self.warnings.append({"code": code, "message": message, **kwargs})

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def validate_vocab_highlight(
    annotation: VocabHighlight,
    sentence_map: dict[str, str],
) -> ValidationResult:
    """校验 VocabHighlight"""
    result = ValidationResult()

    # 检查 sentence_id
    sentence_text = sentence_map.get(annotation.sentence_id)
    if sentence_text is None:
        result.add_error(
            "sentence_id_invalid",
            f"sentence_id 不存在: {annotation.sentence_id}",
        )
        return result

    # 检查 text 是否为真实子串
    if not _is_substring(annotation.text, sentence_text):
        result.add_error(
            "anchor_not_substring",
            f"VocabHighlight.text 不是句子真实子串: '{annotation.text}'",
            sentence_id=annotation.sentence_id,
        )

    return result


def validate_phrase_gloss(
    annotation: PhraseGloss,
    sentence_map: dict[str, str],
) -> ValidationResult:
    """校验 PhraseGloss"""
    result = ValidationResult()

    sentence_text = sentence_map.get(annotation.sentence_id)
    if sentence_text is None:
        result.add_error(
            "sentence_id_invalid",
            f"sentence_id 不存在: {annotation.sentence_id}",
        )
        return result

    if not _is_substring(annotation.text, sentence_text):
        result.add_error(
            "anchor_not_substring",
            f"PhraseGloss.text 不是句子真实子串: '{annotation.text}'",
            sentence_id=annotation.sentence_id,
        )

    if annotation.phrase_type not in ALLOWED_PHRASE_TYPES:
        result.add_error(
            "phrase_type_invalid",
            f"不允许的 phrase_type: {annotation.phrase_type}",
            allowed=list(ALLOWED_PHRASE_TYPES),
        )

    if not _is_chinese(annotation.zh):
        result.add_warning(
            "zh_not_chinese",
            f"PhraseGloss.zh 建议为中文: '{annotation.zh}'",
        )

    return result


def validate_context_gloss(
    annotation: ContextGloss,
    sentence_map: dict[str, str],
) -> ValidationResult:
    """校验 ContextGloss"""
    result = ValidationResult()

    sentence_text = sentence_map.get(annotation.sentence_id)
    if sentence_text is None:
        result.add_error(
            "sentence_id_invalid",
            f"sentence_id 不存在: {annotation.sentence_id}",
        )
        return result

    if not _is_substring(annotation.text, sentence_text):
        result.add_error(
            "anchor_not_substring",
            f"ContextGloss.text 不是句子真实子串: '{annotation.text}'",
            sentence_id=annotation.sentence_id,
        )

    if not _is_chinese(annotation.gloss):
        result.add_warning(
            "gloss_not_chinese",
            f"ContextGloss.gloss 建议为中文: '{annotation.gloss}'",
        )

    if not _is_chinese(annotation.reason):
        result.add_warning(
            "reason_not_chinese",
            f"ContextGloss.reason 建议为中文: '{annotation.reason}'",
        )

    return result


def validate_grammar_note(
    annotation: GrammarNote,
    sentence_map: dict[str, str],
) -> ValidationResult:
    """校验 GrammarNote"""
    result = ValidationResult()

    sentence_text = sentence_map.get(annotation.sentence_id)
    if sentence_text is None:
        result.add_error(
            "sentence_id_invalid",
            f"sentence_id 不存在: {annotation.sentence_id}",
        )
        return result

    for i, span in enumerate(annotation.spans):
        if not _is_substring(span.text, sentence_text):
            result.add_error(
                "anchor_not_substring",
                f"GrammarNote.spans[{i}].text 不是句子真实子串: '{span.text}'",
                sentence_id=annotation.sentence_id,
            )

    if not _is_chinese(annotation.note_zh):
        result.add_warning(
            "note_zh_not_chinese",
            f"GrammarNote.note_zh 建议为中文: '{annotation.note_zh}'",
        )

    if len(annotation.spans) > 4:
        result.add_warning(
            "spans_too_many",
            f"GrammarNote.spans 建议 1-4 个，当前: {len(annotation.spans)}",
        )

    return result


def validate_sentence_analysis(
    annotation: SentenceAnalysis,
    sentence_map: dict[str, str],
) -> ValidationResult:
    """校验 SentenceAnalysis"""
    result = ValidationResult()

    sentence_text = sentence_map.get(annotation.sentence_id)
    if sentence_text is None:
        result.add_error(
            "sentence_id_invalid",
            f"sentence_id 不存在: {annotation.sentence_id}",
        )
        return result

    chunks = annotation.chunks or []

    # 检查 chunks
    if not chunks:
        result.add_warning(
            "chunks_missing",
            "SentenceAnalysis.chunks 缺失；对复杂句建议提供 2-6 个 chunks 以支持结构化渲染",
        )
        return result

    if len(chunks) < 2:
        result.add_warning(
            "chunks_too_few",
            f"SentenceAnalysis.chunks 建议至少 2 个，当前: {len(chunks)}",
        )

    if len(chunks) > 8:
        result.add_warning(
            "chunks_too_many",
            f"SentenceAnalysis.chunks 建议最多 8 个，当前: {len(chunks)}",
        )

    # 检查 order 连续性
    orders = [c.order for c in chunks]
    expected_orders = list(range(1, len(chunks) + 1))
    if sorted(orders) != expected_orders:
        result.add_error(
            "chunks_order_invalid",
            f"SentenceAnalysis.chunks order 必须连续: {orders}",
        )

    for i, chunk in enumerate(chunks):
        if not _is_substring(chunk.text, sentence_text):
            result.add_error(
                "chunk_not_substring",
                f"SentenceAnalysis.chunks[{i}].text 不是句子真实子串: '{chunk.text}'",
                sentence_id=annotation.sentence_id,
            )

    if not _is_chinese(annotation.analysis_zh):
        result.add_warning(
            "analysis_zh_not_chinese",
            f"SentenceAnalysis.analysis_zh 建议为中文: '{annotation.analysis_zh}'",
        )

    return result


def validate_annotation_output(
    annotation_output: AnnotationOutput,
    prepared_input: PreparedInput,
) -> ValidationResult:
    """
    校验完整的 AnnotationOutput

    - sentence_map 用于验证锚点是否为真实子串
    - 检查翻译覆盖
    """
    result = ValidationResult()

    # 构建 sentence_map
    sentence_map = {
        sentence.sentence_id: sentence.text
        for sentence in prepared_input.sentences
    }

    for i, annotation in enumerate(annotation_output.annotations):
        annotation_id = f"{annotation.type}_{i}"

        if isinstance(annotation, VocabHighlight):
            r = validate_vocab_highlight(annotation, sentence_map)
        elif isinstance(annotation, PhraseGloss):
            r = validate_phrase_gloss(annotation, sentence_map)
        elif isinstance(annotation, ContextGloss):
            r = validate_context_gloss(annotation, sentence_map)
        elif isinstance(annotation, GrammarNote):
            r = validate_grammar_note(annotation, sentence_map)
        elif isinstance(annotation, SentenceAnalysis):
            r = validate_sentence_analysis(annotation, sentence_map)
        else:
            r = ValidationResult()
            r.add_error("unknown_annotation_type", f"未知 annotation 类型: {type(annotation)}")

        for err in r.errors:
            err["annotation_id"] = annotation_id
        for warn in r.warnings:
            warn["annotation_id"] = annotation_id

        result.errors.extend(r.errors)
        result.warnings.extend(r.warnings)

    # 检查翻译覆盖
    expected_ids = {s.sentence_id for s in prepared_input.sentences}
    translated_ids = {t.sentence_id for t in annotation_output.sentence_translations}
    missing = expected_ids - translated_ids
    if missing:
        result.add_error(
            "translation_missing",
            f"缺少以下句子的翻译: {missing}",
        )

    return result
