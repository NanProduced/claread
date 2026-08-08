"""Shared source-link safety policy (single owner).

Both the Markdown source parser (block extraction) and the stable
annotation analyzer (inline mark validation) consume this one allowlist;
neither may grow a second copy. Relative and fragment links are allowed;
absolute URLs are limited to ``http`` / ``https`` / ``mailto``.
"""

from __future__ import annotations

from urllib.parse import urlparse

SAFE_LINK_PROTOCOLS = frozenset({"http", "https", "mailto"})


def is_safe_source_link(href: str) -> bool:
    """Return True if the link protocol is whitelisted (or relative)."""
    if not href:
        return False
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if not scheme:
        return True  # Relative link / anchor
    return scheme in SAFE_LINK_PROTOCOLS
