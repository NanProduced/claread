"""
字典查询专用 spaCy Pipeline。
包含 tagger、lemmatizer、parser 和 NER，用于句子分析和依存关系判断。
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_dict_spacy_available: Optional[bool] = None
_dict_spacy_checked: bool = False
_dict_spacy_last_check: float = 0
_DICT_SPACY_RETRY_INTERVAL = 300
_dict_nlp = None
_dict_matcher = None

def check_dict_spacy_model() -> bool:
    global _dict_spacy_available, _dict_spacy_checked, _dict_spacy_last_check
    now = time.time()
    if _dict_spacy_checked and _dict_spacy_available:
        return True
    if _dict_spacy_checked and (now - _dict_spacy_last_check) < _DICT_SPACY_RETRY_INTERVAL:
        return False
    _dict_spacy_last_check = now
    _dict_spacy_checked = True
    try:
        import spacy
        import spacy.util
        if not spacy.util.is_package("en_core_web_sm"):
            _dict_spacy_available = False
            logger.warning("dict: spaCy model en_core_web_sm unavailable. Falling back to exact/n-gram lookup. Install with: python -m spacy download en_core_web_sm")
        else:
            _dict_spacy_available = True
    except ImportError:
        _dict_spacy_available = False
        logger.warning("dict: spaCy package unavailable. Falling back to exact/n-gram lookup.")
    return bool(_dict_spacy_available)

def get_dict_nlp():
    """Lazy-load spaCy model. MUST only be called after check_dict_spacy_model() returns True."""
    global _dict_nlp
    if _dict_nlp is None:
        import spacy
        _dict_nlp = spacy.load("en_core_web_sm", disable=[])
    return _dict_nlp

def get_dict_matcher():
    """Lazy-load and return a spaCy Matcher with rules for high-frequency dictionary structures."""
    global _dict_matcher
    if _dict_matcher is None:
        nlp = get_dict_nlp()
        if nlp is None:
            return None
        from spacy.matcher import Matcher
        matcher = Matcher(nlp.vocab)

        matcher.add("COMP_STRICT", [
            [{"POS": {"IN": ["ADJ", "ADV"]}, "TAG": {"IN": ["JJR", "RBR"]}}, {"LOWER": "than"}]
        ])

        matcher.add("COMP_MORE_LESS", [
            [{"LOWER": {"IN": ["more", "less"]}}, {"POS": {"IN": ["ADJ", "ADV"]}}, {"LOWER": "than"}]
        ])

        matcher.add("COMP_GAP", [
            [
                {"POS": {"IN": ["ADJ", "ADV"]}, "TAG": {"IN": ["JJR", "RBR"]}},
                {"LOWER": {"IN": ["much", "far", "slightly", "way", "even"]}, "OP": "?"},
                {"LOWER": "than"}
            ]
        ])

        matcher.add("BE_THERE_FOR", [
            [{"LEMMA": "be"}, {"LOWER": "there"}, {"LOWER": "for"}]
        ])

        _dict_matcher = matcher
    return _dict_matcher


def preload_dict_nlp() -> bool:
    """
    在应用启动阶段预热词典专用 spaCy pipeline。

    Returns:
        bool: 模型可用且已成功加载时返回 True，否则返回 False。
    """
    if not check_dict_spacy_model():
        return False
    get_dict_nlp()
    logger.info("dict: spaCy pipeline preloaded")
    return True
