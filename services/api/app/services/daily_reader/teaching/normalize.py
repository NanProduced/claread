"""Shared deterministic normalization for the teaching contracts.

Single source for evals and services/api. ``normalize_expression`` keeps
the naive-suffix morphology; the runtime highlight dedup key in
``workflow.py`` (``_normalize_highlight_key``) intentionally has its own
stemming semantics and is NOT merged with this module.
"""

from __future__ import annotations

import re


def normalize_text(s: str) -> str:
    """Casefold + collapse all whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def normalize_expression(s: str) -> str:
    """normalize_text + naive morphology: plural / simple tense suffixes.

    ponytail: suffix strip only (no lemmatizer). Single source shared by
    evals gates and services/api; ``_normalize_highlight_key`` in
    workflow.py mirrors it with extra stemming on purpose.
    """
    words = []
    for w in normalize_text(s).split(" "):
        if not w:
            continue
        if len(w) > 5 and w.endswith("ing"):
            w = w[:-3]
            if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "aeiouy":
                w = w[:-1]
        elif len(w) > 4 and w.endswith("ied"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("ed"):
            w = w[:-2]
            if len(w) > 2 and w[-1] == w[-2] and w[-1] not in "aeiouy":
                w = w[:-1]
        elif len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("es"):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]
        if len(w) > 4 and w.endswith("e"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)
