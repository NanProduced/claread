"""共享文本归一化工具，确保预处理和后处理使用一致的归一化逻辑。

sanitize_text 对 render_text 做了 NFC、标点变体、零宽字符移除、省略号等变换。
后处理匹配（grounding、draft validation、projection、anchor resolution）
必须对 LLM 输出的锚点文本做同样的归一化，才能正确匹配。

本模块提供与 sanitize_text 一致的归一化子集，供后处理各模块使用。
"""

from __future__ import annotations

import unicodedata

# 直接从 input_preparation 导入常量，确保预处理和后处理始终一致。
# 如果 input_preparation 更新了映射，后处理自动同步，无需手动维护两份副本。
from app.services.analysis.preprocess.input_preparation import (
    _INVISIBLE_CHAR_PATTERN,
    _PUNCTUATION_MAP,
    _ELLIPSIS_PATTERN,
)


def normalize_for_comparison(text: str) -> str:
    """对文本应用与 sanitize_text 一致的归一化，用于后处理匹配。

    包含: NFC + 标点变体 + 零宽字符移除 + 省略号 + 空白压缩。
    不包含: HTML/URL/代码块移除（这些在预处理阶段已完成）。
    """
    # 1. NFC 归一化
    text = unicodedata.normalize("NFC", text)
    # 2. 标点变体归一化
    text = text.translate(_PUNCTUATION_MAP)
    text = _ELLIPSIS_PATTERN.sub("...", text)
    # 3. 零宽/控制字符移除
    text = _INVISIBLE_CHAR_PATTERN.sub("", text)
    # 4. 空白压缩
    return " ".join(text.split())


def is_substring(text: str, sentence_text: str) -> bool:
    """先尝试严格匹配，失败则尝试归一化匹配。

    用于 grounding check、draft validation、projection chunk validation
    等所有需要判断锚点文本是否为句子子串的场景。
    """
    if text in sentence_text:
        return True
    return normalize_for_comparison(text) in normalize_for_comparison(sentence_text)
