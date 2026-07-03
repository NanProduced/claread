"""Thin wrapper around the normalizer's inline-Markdown stripper.

The stable-ready path strips inline Markdown (`**bold**`, `[label](url)`,
`` `code` ``, etc.) so that ``reading_bases.text`` is canonical plain text.
The candidate-creation path must produce the same canonical text on confirm;
otherwise freeze plan ``canonical_text`` would carry Markdown syntax and
break anchor offsets + RAG chunking.

We re-export the existing helper rather than duplicate it so the two paths
stay byte-identical. The helper is module-private in ``input_document_normalizer``
but the implementation is pure (no I/O, no module state), so importing it
is safe.
"""
from __future__ import annotations

from app.services.reader_orchestration.input_document_normalizer import (
    _strip_inline_markdown,
)

__all__ = ["strip_inline_markdown"]


def strip_inline_markdown(text: str) -> tuple[str, list[dict[str, str]]]:
    """Strip inline Markdown; return (plain_text, links).

    See ``input_document_normalizer._strip_inline_markdown`` for the
    canonical implementation.
    """
    return _strip_inline_markdown(text)