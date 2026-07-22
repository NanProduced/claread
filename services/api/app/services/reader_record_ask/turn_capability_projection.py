"""Port-derived TurnCapabilityProjection (R4-A5-1 foundation).

Construction rule
-----------------
``can_search_article`` is decided **only** from:

1. the actual injected ``ArticleRagSearchPort | None`` object;
2. stable document identity (non-None ``stable_document_id``);
3. the product search switch.

This module **must not** read or copy ``envelope.capabilities.article_rag_ready``
(or any other envelope capability flag that once short-circuited port wiring).

Model-safe surface
------------------
The projection deliberately excludes:

- selection / article body text (``selected_text``, ``snippet``, previews);
- raw locators (``unit_id``, ``anchor_segment_id``, offsets);
- scores, content hashes, plan/content sha256;
- record / base / user / document UUIDs and envelope fingerprint.

A5-2 selection model-view supplies ``SelectionCapabilityView`` metadata
only; the live production runtime still uses
``envelope.to_agent_projection()`` until A5-7 production wiring.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from app.services.reader_record_ask.article_rag_port import ArticleRagSearchPort

# Forbidden keys that must never appear on the model-facing projection dict.
# Used by tests and by :meth:`TurnCapabilityProjection.to_model_dict` defence.
_FORBIDDEN_PROJECTION_KEYS: frozenset[str] = frozenset(
    {
        "selected_text",
        "selection_preview",
        "snippet",
        "text",
        "unit_id",
        "anchor_segment_id",
        "segment_id",
        "score",
        "chunk_id",
        "text_hash",
        "content_sha256",
        "base_content_sha256",
        "plan_content_sha256",
        "user_id",
        "reading_record_id",
        "record_id",
        "base_id",
        "stable_document_id",
        "rag_substrate_id",
        "envelope_fingerprint",
        "article_rag_ready",
        "initial_selection_locator",
        "start_offset",
        "end_offset",
        "base_start_utf16",
        "base_end_utf16",
    }
)


@dataclass(frozen=True, slots=True)
class SelectionCapabilityView:
    """Selection metadata only — no body text, no raw locator."""

    present: bool
    handle_id: str | None = None
    expandable: bool = False
    visible_char_count: int = 0
    full_char_count: int = 0


@dataclass(frozen=True, slots=True)
class ArticleMapCapabilityView:
    """Map presence metadata only — labels live in untrusted map blocks (A5-4)."""

    present: bool
    entry_count: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class TurnCapabilityProjection:
    """Model-visible capability projection for one Ask turn.

    Free of auth identity, body text, scores, hashes, and raw locators.
    """

    turn_id: str
    can_search_article: bool
    can_read_range: bool
    has_visible_range: bool
    baseline_injected: bool
    baseline_complete: bool
    selection: SelectionCapabilityView
    article_map: ArticleMapCapabilityView

    def to_model_dict(self) -> dict[str, Any]:
        """JSON-ready mapping safe for ModelViewRenderer.render_json.

        Nested dataclasses are expanded; forbidden sidecar keys are absent
        by construction.
        """
        payload: dict[str, Any] = {
            "turn_id": self.turn_id,
            "can_search_article": self.can_search_article,
            "can_read_range": self.can_read_range,
            "has_visible_range": self.has_visible_range,
            "baseline_injected": self.baseline_injected,
            "baseline_complete": self.baseline_complete,
            "selection": asdict(self.selection),
            "article_map": asdict(self.article_map),
        }
        _assert_no_forbidden_keys(payload)
        return payload


def mint_turn_id() -> str:
    """Server-minted opaque turn id (never model-supplied)."""
    return f"turn_{secrets.token_hex(16)}"


def resolve_can_search_article(
    *,
    article_rag_port: ArticleRagSearchPort | None,
    stable_document_id: UUID | None,
    product_search_enabled: bool,
) -> bool:
    """Pure decision for ``can_search_article``.

    Does **not** call the port (zero I/O). Does **not** consult
    ``article_rag_ready`` or any envelope capability field.
    """
    return (
        article_rag_port is not None
        and stable_document_id is not None
        and product_search_enabled
    )


def build_turn_capability_projection(
    *,
    article_rag_port: ArticleRagSearchPort | None,
    stable_document_id: UUID | None,
    product_search_enabled: bool,
    baseline_injected: bool,
    baseline_complete: bool = False,
    can_read_range: bool = True,
    has_visible_range: bool = False,
    selection_present: bool = False,
    selection_handle_id: str | None = None,
    selection_expandable: bool = False,
    selection_visible_char_count: int = 0,
    selection_full_char_count: int = 0,
    article_map_present: bool = False,
    article_map_entry_count: int = 0,
    article_map_truncated: bool = False,
    turn_id: str | None = None,
) -> TurnCapabilityProjection:
    """Build a model-safe capability projection from host facts only.

    Parameters are explicit host-owned inputs. Callers must **not** pass
    ``envelope.capabilities.article_rag_ready`` into ``product_search_enabled``
    as a substitute for the real port object — the port argument is the
    readiness seam for search capability.

    ``turn_id`` is minted server-side when omitted.
    """
    if selection_visible_char_count < 0 or selection_full_char_count < 0:
        raise ValueError("selection char counts must be non-negative")
    if article_map_entry_count < 0:
        raise ValueError("article_map_entry_count must be non-negative")
    if selection_handle_id is not None and not selection_handle_id:
        raise ValueError("selection_handle_id must be non-empty when provided")

    can_search = resolve_can_search_article(
        article_rag_port=article_rag_port,
        stable_document_id=stable_document_id,
        product_search_enabled=product_search_enabled,
    )

    # A5-1: selection body is not yet switched into model chunks. Metadata
    # only — handle_id may be pre-registered by the host when present.
    if not selection_present:
        selection = SelectionCapabilityView(present=False)
    else:
        selection = SelectionCapabilityView(
            present=True,
            handle_id=selection_handle_id,
            expandable=selection_expandable,
            visible_char_count=selection_visible_char_count,
            full_char_count=selection_full_char_count,
        )

    if not article_map_present:
        article_map = ArticleMapCapabilityView(present=False)
    else:
        article_map = ArticleMapCapabilityView(
            present=True,
            entry_count=article_map_entry_count,
            truncated=article_map_truncated,
        )

    return TurnCapabilityProjection(
        turn_id=turn_id if turn_id is not None else mint_turn_id(),
        can_search_article=can_search,
        can_read_range=can_read_range,
        has_visible_range=has_visible_range,
        baseline_injected=baseline_injected,
        baseline_complete=baseline_complete and baseline_injected,
        selection=selection,
        article_map=article_map,
    )


def _assert_no_forbidden_keys(payload: Mapping[str, Any], *, _path: str = "") -> None:
    """Fail closed if a forbidden key ever appears on the projection tree."""
    for key, value in payload.items():
        if key in _FORBIDDEN_PROJECTION_KEYS:
            raise ValueError(
                f"forbidden projection key {_path + key!r} must not be model-visible"
            )
        if isinstance(value, Mapping):
            _assert_no_forbidden_keys(value, _path=f"{_path}{key}.")
