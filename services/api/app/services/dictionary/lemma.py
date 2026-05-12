"""
Lemma fallback using lemminflect.

在 exact / redirect / disambiguation / .n lp 查找都失败后，
对"单词级"查询尝试通过 lemminflect 还原英文词形，
再用还原后的 lemma 查询词典。

触发条件：
- 查询词不包含空格（短语不参与 lemma fallback）
- 现有所有查询路径均返回空候选
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lemminflect import getAllLemmas as _getAllLemmas

__all__ = ["get_lemma_candidates"]


def _get_getAllLemmas():
    # 延迟导入，避免在未安装时报错
    try:
        from lemminflect import getAllLemmas as _fn

        return _fn
    except ImportError:
        return None


# 查询优先级：名词优先（处理复数），动词其次（处理时态），
# 形容词和副词最后（处理比较级/最高级，如 smaller→small, better→good/well）
_POS_TAGS = ["NOUN", "VERB", "ADJ", "ADV"]


def get_lemma_candidates(word: str) -> list[str]:
    """
    返回 word 所有可能的 lemma 候选列表。

    策略：对名词、动词、形容词和副词分别调用 getAllLemmas，
    合并结果并去重，保持稳定优先顺序。
    """
    getAllLemmas = _get_getAllLemmas()
    if getAllLemmas is None:
        return []

    seen: set[str] = set()
    candidates: list[str] = []

    for pos in _POS_TAGS:
        try:
            result = getAllLemmas(word, pos)
        except Exception:
            # lemminflect 对未知词或边界情况会抛异常，静默忽略
            result = {}
        # result is like {'NOUN': ('human',)} or {'VERB': ('crew',)}
        for lemma in result.get(pos, ()):
            if lemma and lemma not in seen and lemma != word:
                seen.add(lemma)
                candidates.append(lemma)

    return candidates
