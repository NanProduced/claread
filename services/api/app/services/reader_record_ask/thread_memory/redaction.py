"""Compaction-input redaction (defense in depth).

R0.1 §8.3 安全门第 3 条: every compaction input is scanned for
secrets a second time. The upstream ``IncrementalRedactor``
(``reasoning_projection.py:353``) is the primary redaction seam and
runs at reasoning-projection time. This module is the **second layer**
that runs at compaction-input assembly time.

Normal path: 0 hits (upstream already redacted). Any hit here is an
incident metric — the upstream seam failed or a non-reasoning path
leaked a secret into canonical messages.

Pattern coverage mirrors ``IncrementalRedactor``'s deterministic
minimum rules (reasoning_projection.py:122-174):
- ``evh_`` opaque evidence handles
- ``Bearer`` credentials
- ``sk-`` API keys
- PEM regions (BEGIN/END label pairing)
- email addresses

Replacements are typed markers (``[REDACTED:<kind>]``) so callers can
audit hits per category. ``IncrementalRedactor`` itself is NOT modified
— we re-apply its rule patterns here with our own replacement format.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Re-derive the redaction rule patterns. We do NOT import the patterns
# from ``reasoning_projection`` because that module's rules emit empty
# strings / ``〔引用〕`` markers; compaction wants explicit typed
# markers so audit logs can distinguish "redacted by compaction layer"
# from "redacted by upstream reasoning_projection".

_EVH_HANDLE_RE = re.compile(r"(?<![A-Za-z0-9_])evh_[0-9A-Fa-f]{8,64}")
_BEARER_RE = re.compile(r"(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9._\-]{12,}")
_SK_KEY_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{12,}")
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

# PEM regions: non-greedy BEGIN..END pairing on identical labels. The
# upstream state machine in ``reasoning_projection._remove_pem_regions``
# does strict label normalization; here we accept the simple form which
# is sufficient for the second-layer scan (any genuine PEM block from
# an upstream leak will match).
_PEM_RE = re.compile(
    r"-----BEGIN (CERTIFICATE|PRIVATE KEY|PUBLIC KEY|RSA PRIVATE KEY|"
    r"EC PRIVATE KEY|ENCRYPTED PRIVATE KEY|OPENSSH PRIVATE KEY)-----"
    r"[\s\S]*?"
    r"-----END \1-----"
)

# Ordered so multi-pattern overlaps resolve deterministically (PEM
# first so its body is consumed before Bearer/sk- scans would hit
# internal base64 fragments).
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem", _PEM_RE),
    ("evh_handle", _EVH_HANDLE_RE),
    ("bearer", _BEARER_RE),
    ("sk_key", _SK_KEY_RE),
    ("email", _EMAIL_RE),
)


def redact_for_compaction_input(
    text: str,
) -> tuple[str, dict[str, Any]]:
    """Apply second-layer redaction to one compaction input text.

    Returns ``(redacted_text, metrics)`` where ``metrics`` is::

        {
            "<kind>": <hit count>,
            ...
            "total_hits": int,
        }

    ``<kind>`` ∈ ``{'pem', 'evh_handle', 'bearer', 'sk_key', 'email'}``.
    Kinds with zero hits are still present in the metrics dict (value
    0) so audit code can iterate without ``KeyError``.

    On any non-str input the function raises ``TypeError`` (defense in
    depth — never silently coerce).
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not text:
        return "", {"total_hits": 0}

    metrics: Counter[str] = Counter()
    out = text
    for kind, pattern in _RULES:
        out, hits = pattern.subn(f"[REDACTED:{kind}]", out)
        if hits:
            metrics[kind] = hits

    # Build a complete metrics dict (zero-fill missing kinds).
    # R1.5 P0-4: fix string-key bug — ``_RULES`` is ``tuple[(kind_str,
    # pattern), ...]`` so the kind STRING is the first element, not the
    # second. The old ``for _, kind in _RULES`` assigned the pattern
    # object to ``kind``, producing pattern-keyed (unusable) metrics.
    result: dict[str, Any] = {kind: metrics.get(kind, 0) for kind, _ in _RULES}
    result["total_hits"] = sum(metrics.values())
    return out, result
