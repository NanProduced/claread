"""ASK-REASONING-/the single approved reasoning projection chokepoint.

Provider reasoning (``ThinkingPart`` content) is untrusted provider output.
It must never be logged or leave this chokepoint without deterministic
filtering.

This module is the ONLY path by which reasoning may become user-visible:

- raw reasoning enters exclusively via ``ThinkingObserver`` callbacks
  (structural source isolation — there is no other feed);
- a streaming state-machine redactor (:class:`IncrementalRedactor`)
  releases ordinary safe text on the same ``feed`` call it arrives in,
  holding back only the minimal ambiguous tail (a potential pattern
  prefix) or an open sensitive region (an unterminated PEM block);
- deterministic minimum security projection redacts internal evidence
  handles, envelope/record identity, authentication material, system
  instruction fragments, and provider raw wrappers;
- a bounded host-side quota caps the visible projection per turn;
- only the filtered projection is published as ordered
  ``agentic.reasoning.*`` runtime events and persisted with the terminal
  turn snapshot.

Fail-closed overflow semantics: when an unterminated sensitive
region exceeds its scan ceiling, or a single ambiguous token exceeds the
pending ceiling, the redactor SEALS permanently — it never resumes
ordinary output for the rest of the turn. Leaking raw sensitive content
is the only outcome this design refuses.

Redaction is defense in depth, not the security boundary. The boundary is
that only observer callbacks can reach this module and only projected text
can leave it. The visible reasoning is a user-approved projection of the
model's thinking — possibly redacted and truncated — not the complete raw
CoT and never the final answer.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.reader_record_ask.runtime_events import (
    AgenticReasoningCompletedEvent,
    AgenticReasoningDeltaEvent,
    AgenticReasoningStartedEvent,
    RuntimeEvent,
)

PROJECTION_POLICY_VERSION: str = "provider_reasoning_v1"

# Host-side quota for one turn's visible projection (code points).
# ASK-TURN-LIFECYCLE raised from 4_000 to 14_000 (within the audit-
# recommended 12K–16K band). Long agentic turns with thinking + article
# RAG + web search + retry routinely produce >4K reasoning; the old cap
# guaranteed truncation on every long turn. 14K balances visibility with
# DB/SSE cost. Override via ``CLAREAD_REASONING_PROJECTION_CHAR_CAP``.
_DEFAULT_PROJECTION_CHAR_CAP_BAND: tuple[int, int] = (12_000, 20_000)


def _resolve_projection_char_cap() -> int:
    """Resolve the reasoning projection cap from env or default.

    Priority: ``CLAREAD_REASONING_PROJECTION_CHAR_CAP`` env > default.
    The env value must be in the [12K, 20K] band — values outside are
    clamped to the band to prevent misconfiguration (too small → always
    truncated; too large → DB/SSE bloat).
    """
    default = 14_000
    raw = os.environ.get("CLAREAD_REASONING_PROJECTION_CHAR_CAP")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    lo, hi = _DEFAULT_PROJECTION_CHAR_CAP_BAND
    return max(lo, min(hi, value))


DEFAULT_PROJECTION_CHAR_CAP: int = _resolve_projection_char_cap()

# ASK-TURN-LIFECYCLE the hard round-0 sub-cap (former
# ``ROUND0_CAP_FRACTION = 0.65``) was removed because it silently dropped
# up to 35% of round-0 reasoning without setting ``truncated=True``,
# producing an undeclared gap in the visible projection. The turn-level
# total cap is the ONLY quota: ``truncated`` now accurately reflects
# whether the user-visible projection was truncated by the total cap.

# Appended exactly once when the quota is hit; part of the visible text so
# hot deltas, persisted snapshot, and cold history stay byte-identical.
TRUNCATION_MARKER: str = "…（思考内容已截断）"

# Fail-closed ceilings for the streaming redactor. An ambiguous pending
# region (a single token no terminator has resolved) beyond the pending
# ceiling, or an unterminated PEM region scanned beyond the PEM ceiling,
# seals the redactor permanently rather than ever resuming plain output.
_MAX_PENDING_NORMAL: int = 16_384
_MAX_PEM_DISCARD: int = 65_536

# ---------------------------------------------------------------------------
# Deterministic minimum redaction rules (defense in depth).
#
# Anchors are ASCII-only lookarounds rather than ``\b``: CJK characters are
# word characters, so ``\b`` would fail exactly where mixed-language
# reasoning glues sentinels to prose. Value/body classes are restricted to
# printable ASCII so adjacent CJK prose is preserved. Inline flags use the
# scoped ``(?i:...)`` / ``(?m:...)`` forms so the rules can be joined into
# one boundary-detection alternation (Python 3.13 rejects unscoped inline
# global flags away from pattern start).
# ---------------------------------------------------------------------------

_IDENTITY_KEY_ALTERNATION: str = (
    r"envelope_fingerprint|content_sha256|stable_document_id|"
    r"reading_record_id|analysis_record_id|record_id|base_id|generation|"
    r"user_id|turn_run_id|thread_id|message_id|handle_id|rag_substrate_id"
)

_REDACTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Opaque internal evidence handles → neutral citation marker.
    (re.compile(r"(?<![A-Za-z0-9_])evh_[0-9A-Fa-f]{8,64}"), "〔引用〕"),
    # NOTE: PEM regions are removed by ``_remove_pem_regions`` (strict
    # label pairing) inside ``_redact_block`` BEFORE these rules run — a
    # plain regex cannot pair BEGIN/END labels, so it must not be used.
    (re.compile(r"(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9._\-]{12,}"), ""),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{12,}"), ""),
    # Envelope / record / turn identity as key[:=]value pairs.
    (
        re.compile(
            rf"(?<![A-Za-z0-9_])(?:{_IDENTITY_KEY_ALTERNATION})"
            r"\s*[:=：]\s*['\"]?(?:(?=[!-~])[^'\",;)}\]])+"
        ),
        "",
    ),
    # UUID-shaped identity (8-4-4-4-12).
    (
        re.compile(
            r"(?<![0-9A-Fa-f])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
            r"(?![0-9a-fA-F-])"
        ),
        "",
    ),
    # Provider raw wrapper artifacts (opening and closing think tags).
    (re.compile(r"(?i:<\/?\|?\s*think(?:ing)?\s*\|?>)"), ""),
    (
        re.compile(
            r"(?i:(?<![A-Za-z0-9_])reasoning_content(?![A-Za-z0-9_])"
            r"\s*[:=]\s*\"?)"
        ),
        "",
    ),
    # Locator surfaces. URL bodies are ASCII printable chars only so CJK
    # prose/punctuation glued to a URL survives.
    (
        re.compile(r"(?<![A-Za-z0-9])https?://(?:(?=[!-~])[^<>\s\"')\]])+"),
        "",
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), ""),
    # Known system-instruction fragments: drop the whole line (newline
    # excluded, structure preserved).
    (
        re.compile(
            r"(?m:^(?:You are Claread|## Answer correctness|## Tools|"
            r"## Evidence|## Output contract|SYSTEM:|<system>)[^\n]*)"
        ),
        "",
    ),
)

# Trust-boundary failures stop the public reasoning stream for the turn.
# These are narrower than the ordinary redaction rules: harmless locators
# may be redacted and processing may continue, while credentials, private
# keys, connection strings, and illegal control characters fail closed.
_HARD_BLOCK_RE = re.compile(
    r"(?i:"
    r"(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9._\-]{12,}"
    r"|(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{12,}"
    r"|(?<![A-Za-z0-9])(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,})"
    r"|(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?"
    r"[A-Za-z0-9._/+\-=]{8,}"
    r"|(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)

# Final-flush only: a trailing unterminated line that begins with a known
# system-instruction marker is dropped too (mid-stream such lines are held
# back by the ambiguous-tail logic until their newline resolves them).
_TRAILING_MARKER_LINE_RE = re.compile(
    r"(?m:^(?:You are Claread|## Answer correctness|## Tools|"
    r"## Evidence|## Output contract|SYSTEM:|<system>)[^\n]*\Z)"
)

# Line-start markers that may begin a droppable instruction line. The
# streaming redactor holds back a trailing partial line whose text is still
# a prefix of a marker (or already starts with one) until its newline.
_SYSTEM_LINE_PREFIXES: tuple[str, ...] = (
    "You are Claread",
    "## Answer correctness",
    "## Tools",
    "## Evidence",
    "## Output contract",
    "SYSTEM:",
    "<system>",
)

# PEM block opener / terminator used by the streaming state machine. The
# label is captured so a region is only closed by the END whose label
# matches its BEGIN (strict pairing). Labels are normalized (whitespace
# collapsed, uppercased) before comparison.
_PEM_OPEN_RE = re.compile(r"-----BEGIN ([A-Z ]+)-----")
_PEM_END_RE = re.compile(r"-----END ([A-Z ]+)-----")


def _normalize_pem_label(label: str) -> str:
    """Canonical PEM label form: collapse whitespace runs, uppercase."""
    return " ".join(label.split()).upper()


def _find_matching_pem_end(text: str, label: str, search_from: int) -> int | None:
    """End offset (exclusive) of the END whose normalized label equals
    ``label``, scanning from ``search_from``; None if none follows.

    Mismatched END markers are skipped (they are body, not terminators).
    """
    pos = search_from
    while True:
        end_match = _PEM_END_RE.search(text, pos)
        if end_match is None:
            return None
        if _normalize_pem_label(end_match.group(1)) == label:
            return end_match.end()
        pos = end_match.end()


def _first_unterminated_pem(text: str) -> tuple[int, int, str] | None:
    """Locate the first BEGIN whose matching END is absent within ``text``.

    Returns ``(begin_start, body_start, normalized_label)`` or None when
    every BEGIN in ``text`` is closed by a matching END.
    """
    pos = 0
    while True:
        open_match = _PEM_OPEN_RE.search(text, pos)
        if open_match is None:
            return None
        label = _normalize_pem_label(open_match.group(1))
        end_pos = _find_matching_pem_end(text, label, open_match.end())
        if end_pos is None:
            return (open_match.start(), open_match.end(), label)
        pos = end_pos


def _matched_pem_spans(text: str) -> list[tuple[int, int]]:
    """Spans ``[begin_start, end_end)`` of BEGIN..END pairs whose labels
    match, stopping at the first unterminated BEGIN (that BEGIN and all
    after it are not matched spans).
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        open_match = _PEM_OPEN_RE.search(text, pos)
        if open_match is None:
            break
        label = _normalize_pem_label(open_match.group(1))
        end_pos = _find_matching_pem_end(text, label, open_match.end())
        if end_pos is None:
            break
        spans.append((open_match.start(), end_pos))
        pos = end_pos
    return spans


def _remove_pem_regions(text: str) -> str:
    """Remove PEM regions with strict BEGIN/END label pairing.

    A region runs from a BEGIN to the END carrying the identical
    normalized label; mismatched ENDs are body (removed with the region).
    An unterminated BEGIN drops everything to end of text. Non-PEM text is
    preserved verbatim (and in order).
    """
    out: list[str] = []
    i = 0
    while True:
        open_match = _PEM_OPEN_RE.search(text, i)
        if open_match is None:
            out.append(text[i:])
            break
        out.append(text[i : open_match.start()])
        label = _normalize_pem_label(open_match.group(1))
        end_pos = _find_matching_pem_end(text, label, open_match.end())
        if end_pos is None:
            # Unterminated region: discard to end of text.
            break
        i = end_pos
    return "".join(out)


# Trailing ambiguous tails held back in NORMAL state. A held tail is the
# MINIMAL region that could still resolve into a sensitive pattern once
# more raw text arrives:
#  - a trailing word/local-part run (could precede ``@`` for an email, or
#    complete an evh_ handle, sk- key, Bearer token, identity key, UUID,
#    or a <think>/reasoning_content wrapper);
#  - an in-progress URL, email (post-@), or identity key[:=]value;
#  - a dash progression that could still become ``-----BEGIN …``.
_WORD_TAIL_RE = re.compile(r"[A-Za-z0-9._%+-]*\Z")
_SPECIAL_TAIL_RE = re.compile(
    r"(?:"
    # In-progress URL: http: / https: / http:/ / https:/ / https://<body>.
    r"https?:(?:/(?:/(?:(?=[!-~])[^<>\s\"')\]]*)?)?)?"
    # In-progress email after the local part: user@ / user@domain.part.
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]*"
    # In-progress identity key[:=]value (value run stops at whitespace,
    # mirroring the redaction rule's printable-ASCII lookahead).
    rf"|(?:{_IDENTITY_KEY_ALTERNATION})\s*[:=：]\s*['\"]?"
    r"(?:(?=[!-~])[^'\",;)}\]\s])*"
    # In-progress Bearer credential: Bearer / Bearer<ws> / Bearer<ws>token.
    r"|Bearer(?:\s+[A-Za-z0-9._\-]*)?"
    # In-progress labelled credential or database connection string.
    r"|(?i:(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?"
    r"[A-Za-z0-9._/+\-=]*)"
    r"|(?i:(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)"
    r"(?::(?:/{0,2}[^\s]*)?)?)"
    # In-progress reasoning_content wrapper: key / key: / key:".
    r"|(?i:reasoning_content(?:\s*[:=]\s*\"?)?)"
    # Trailing angle-bracket fragment that could become a wrapper tag.
    r"|<[^>\n]{0,15}"
    # Dash progression toward a PEM BEGIN opener (including the closing
    # dash run after the key label, e.g. ``-----BEGIN K-----``).
    r"|-{1,5}"
    r"|-----B(?:E(?:G(?:I(?:N(?: [A-Z ]*(?:-{1,5})?)?)?)?)?)?"
    r")\Z"
)

# Trailing region that could still become a PEM terminator (IN_PEM state).
_PEM_END_TAIL_RE = re.compile(r"(?:-{1,5}|-----E(?:N(?:D(?: [A-Z ]*(?:-{1,5})?)?)?)?)\Z")


def redact_reasoning_text(text: str, *, final: bool = False) -> str:
    """Apply the full deterministic projection to one complete raw text.

    Pure and stable: the same raw text always produces the same projected
    text. ``final=True`` additionally drops a trailing unterminated line
    that begins with a known system-instruction marker (used only at
    stream end and by snapshot validation callers that pass already
    projected text, where it is a no-op).
    """
    if not text:
        return ""
    hard_block = _HARD_BLOCK_RE.search(text)
    if hard_block is not None:
        text = text[: hard_block.start()]
    projected = _redact_block(text)
    if final:
        projected = _TRAILING_MARKER_LINE_RE.sub("", projected)
    return projected


def _redact_block(block: str) -> str:
    # PEM regions first (strict label pairing); a regex cannot pair labels.
    block = _remove_pem_regions(block)
    for pattern, replacement in _REDACTION_RULES:
        if not block:
            return ""
        block = pattern.sub(replacement, block)
    return block


class IncrementalRedactor:
    """Streaming state-machine redactor.

    States:

    - ``normal``: ordinary safe text is released on the same ``feed`` it
      arrives in. Only the minimal ambiguous tail is retained: a potential
      pattern prefix, an in-progress URL/email/identity value, or a
      trailing line that could still complete to a droppable instruction
      line. An unterminated ``-----BEGIN`` block switches to ``pem``.
    - ``pem``: raw text is discarded until the matching PEM terminator
      arrives. Only a possible terminator prefix is retained, so memory
      stays bounded regardless of the block size.
    - sealed (terminal): after an overflow ceiling is exceeded, the
      redactor emits nothing ever again — fail-closed, never resumes
      ordinary output.

    Everything released is exactly ``redact_reasoning_text`` of the
    committed raw prefix, so the concatenation of all releases equals the
    whole-text projection for any chunking.
    """

    def __init__(self) -> None:
        self._pending: str = ""
        self._in_pem: bool = False
        self._pem_label: str | None = None
        self._pem_discarded: int = 0
        self._sealed: bool = False

    @property
    def blocked(self) -> bool:
        return self._sealed

    @property
    def sealed(self) -> bool:
        return self._sealed

    def feed(self, chunk: str) -> str:
        """Feed one raw chunk; return newly released projected text."""
        if self._sealed or not chunk:
            return ""
        if self._in_pem:
            return self._feed_pem(chunk)
        return self._feed_normal(chunk)

    def flush(self) -> str:
        """Provider input ended: release everything still resolvable.

        An unterminated PEM region is discarded whole (never emitted); a
        sealed redactor emits nothing.
        """
        if self._sealed or self._in_pem:
            self._pending = ""
            self._pem_label = None
            return ""
        if not self._pending:
            return ""
        out = redact_reasoning_text(self._pending, final=True)
        self._pending = ""
        return out

    # -- normal state ------------------------------------------------------

    def _feed_normal(self, chunk: str) -> str:
        self._pending += chunk
        hard_block = _HARD_BLOCK_RE.search(self._pending)
        if hard_block is not None:
            released = redact_reasoning_text(self._pending[: hard_block.start()])
            self._seal()
            return released
        # A BEGIN whose matching END has not arrived yet opens a discard
        # region: commit the redacted text before the BEGIN, then discard
        # the body until the matching END arrives (strict label pairing —
        # a mismatched END is body, not a terminator).
        unterminated = _first_unterminated_pem(self._pending)
        if unterminated is not None:
            begin_start, body_start, label = unterminated
            released = redact_reasoning_text(self._pending[:begin_start])
            body = self._pending[body_start:]
            self._in_pem = True
            self._pem_label = label
            self._pending = ""
            self._pem_discarded = 0
            tail_release = self._discard_pem_body(body)
            # tail_release is always "" (PEM discard never releases text).
            return released + tail_release
        # No open region: every BEGIN (if any) is closed by a matching END.
        tail_start = self._ambiguous_tail_start(self._pending)
        # Never split a complete PEM pair: if the ambiguous tail starts
        # inside a matched pair, commit the whole pair (a closed pair is
        # fully resolved, never ambiguous).
        for begin_start, end_pos in _matched_pem_spans(self._pending):
            if begin_start < tail_start < end_pos:
                tail_start = end_pos
                break
        held = len(self._pending) - tail_start
        if held > _MAX_PENDING_NORMAL:
            # A single ambiguous token beyond the ceiling: fail closed.
            self._seal()
            return ""
        if tail_start == 0:
            return ""
        committed, self._pending = self._pending[:tail_start], self._pending[tail_start:]
        return redact_reasoning_text(committed)

    def _ambiguous_tail_start(self, text: str) -> int:
        """Start offset of the minimal trailing region still ambiguous."""
        start = len(text)
        word_tail = _WORD_TAIL_RE.search(text)
        if word_tail is not None:
            start = min(start, word_tail.start())
        special_tail = _SPECIAL_TAIL_RE.search(text)
        if special_tail is not None and special_tail.end() == len(text):
            start = min(start, special_tail.start())
        # Trailing partial line that could still complete to a droppable
        # system-instruction line: hold back to the line start.
        line_start = text.rfind("\n", 0, len(text))
        line_start = 0 if line_start == -1 else line_start + 1
        partial_line = text[line_start:]
        if partial_line:
            for marker in _SYSTEM_LINE_PREFIXES:
                if marker.startswith(partial_line) or partial_line.startswith(marker):
                    start = min(start, line_start)
                    break
        return start

    # -- pem state -----------------------------------------------------------

    def _feed_pem(self, chunk: str) -> str:
        self._pending += chunk
        # Scan for the END whose normalized label matches the open region.
        # Mismatched ENDs are body (kept discarded); only the matching END
        # closes the region and resumes normal output.
        search_from = 0
        while True:
            end_match = _PEM_END_RE.search(self._pending, search_from)
            if end_match is None:
                # No matching END yet: discard body, keep a possible END
                # prefix tail so a terminator split across chunks survives.
                return self._discard_pem_body(self._pending)
            if _normalize_pem_label(end_match.group(1)) == self._pem_label:
                # Matching terminator: discard through it, resume normal on
                # whatever follows the block.
                rest = self._pending[end_match.end() :]
                self._in_pem = False
                self._pem_label = None
                self._pending = ""
                self._pem_discarded = 0
                if not rest:
                    return ""
                return self._feed_normal(rest)
            # Mismatched END: it is body, keep scanning past it.
            search_from = end_match.end()

    def _discard_pem_body(self, body: str) -> str:
        """Discard PEM body text, retaining only a possible END prefix."""
        tail_match = _PEM_END_TAIL_RE.search(body)
        keep_from = tail_match.start() if tail_match is not None else len(body)
        self._pem_discarded += keep_from
        self._pending = body[keep_from:]
        if self._pem_discarded > _MAX_PEM_DISCARD:
            self._seal()
        return ""

    # -- fail-closed -----------------------------------------------------------

    def _seal(self) -> None:
        """Permanent fail-closed: never emit anything for the rest of the
        turn. Leaking raw sensitive content is the only refused outcome."""
        self._sealed = True
        self._pending = ""
        self._in_pem = False
        self._pem_label = None


@dataclass
class ReasoningProjectionBuffer:
    """Bounded projected-text buffer for one turn.

    ``feed`` returns the newly visible projected increment (possibly
    empty); ``text`` is the exact concatenation of every increment ever
    returned — the same string that is persisted and cold-loaded. Quota
    truncation appends ``TRUNCATION_MARKER`` once and then stays silent;
    after the quota is reached no further raw reasoning is fed or bufferred.

    ASK-TURN-LIFECYCLE the per-round sub-cap was removed. The
    turn-level total cap is the ONLY quota. ``truncated=True`` iff the
    total cap was hit (marker appended exactly once at the end).
    """

    char_cap: int = DEFAULT_PROJECTION_CHAR_CAP
    _redactor: IncrementalRedactor = field(default_factory=IncrementalRedactor, init=False)
    _text: str = field(default="", init=False)
    _truncated: bool = field(default=False, init=False)
    _blocked: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        marker_len = len(TRUNCATION_MARKER)
        if (
            isinstance(self.char_cap, bool)
            or not isinstance(self.char_cap, int)
            or self.char_cap < marker_len
        ):
            raise ValueError(
                "char_cap must be an integer greater than or equal to the truncation marker length"
            )

    def feed(self, raw_chunk: str) -> str:
        if self._truncated or self._blocked or not raw_chunk:
            return ""
        increment = self._absorb(self._redactor.feed(raw_chunk))
        self._blocked = self._redactor.blocked
        return increment

    def flush(self) -> str:
        if self._truncated or self._blocked:
            return ""
        increment = self._absorb(self._redactor.flush())
        self._blocked = self._redactor.blocked
        return increment

    def _absorb(self, projected: str) -> str:
        if not projected:
            return ""
        if self._truncated:
            return ""
        if not self._text.strip() and not projected.strip():
            # Leading whitespace before any visible content (e.g. the bare
            # newline left after a fully-dropped instruction line). It is
            # invisible to users; dropping it keeps empty reasoning empty
            # and preserves concat(deltas) == persisted text.
            return ""
        marker_len = len(TRUNCATION_MARKER)
        # Total cap is the ONLY quota. Provable:
        #   no marker   ⇒ text ≤ char_cap − marker_len
        #   with marker ⇒ text == char_cap exactly, marker at end, once
        # Hence truncated=True ⇔ marker at end, and truncated=False ⇒ no
        # marker. Emitted text is only ever appended — never rewritten —
        # so concat(SSE deltas) == buffer.text == persisted == cold history.
        content_room = (self.char_cap - marker_len) - len(self._text)
        if len(projected) > content_room:
            keep = max(0, content_room)
            increment = projected[:keep] + TRUNCATION_MARKER
            self._text += increment
            self._truncated = True
            return increment
        self._text += projected
        return projected

    @property
    def text(self) -> str:
        return self._text

    @property
    def truncated(self) -> bool:
        return self._truncated

    @property
    def visibility_status(self) -> str:
        if self._blocked:
            return "blocked"
        if self._truncated:
            return "truncated"
        return "complete"

    @property
    def char_count(self) -> int:
        return len(self._text)

    @property
    def has_content(self) -> bool:
        return bool(self._text.strip())

    def snapshot(self) -> dict[str, Any]:
        """Persistable safety metadata + visible text (never raw content)."""
        return {
            "projection_policy_version": PROJECTION_POLICY_VERSION,
            "text": self._text,
            "char_count": self.char_count,
            "truncated": self._truncated,
            "visibility_status": self.visibility_status,
        }


# ---------------------------------------------------------------------------
# Canonical snapshot validation: one validator shared by the write
# path (persistence) and the cold-read path (history projection).
# ---------------------------------------------------------------------------

_CANONICAL_SNAPSHOT_KEYS = frozenset(
    {
        "projection_policy_version",
        "text",
        "char_count",
        "truncated",
        "visibility_status",
    }
)


def validate_reasoning_snapshot(payload: Any) -> dict[str, Any] | None:
    """Validate a persisted reasoning projection, fail-closed.

    Returns the validated snapshot or None. Any deviation — wrong policy
    version, extra/missing keys, empty or oversized text, char_count
    mismatch, non-bool truncated, or text that is not already a stable
    safe projection (re-projection must be byte-identical) — yields None,
    and cold history shows no reasoning rather than degrading to raw text.
    """
    if not isinstance(payload, dict):
        return None
    if set(payload.keys()) != _CANONICAL_SNAPSHOT_KEYS:
        return None
    if payload["projection_policy_version"] != PROJECTION_POLICY_VERSION:
        return None
    text = payload["text"]
    if not isinstance(text, str) or not text:
        return None
    if len(text) > DEFAULT_PROJECTION_CHAR_CAP:
        return None
    char_count = payload["char_count"]
    if isinstance(char_count, bool) or not isinstance(char_count, int):
        return None
    if char_count != len(text):
        return None
    truncated = payload["truncated"]
    if not isinstance(truncated, bool):
        return None
    visibility_status = payload["visibility_status"]
    if visibility_status not in {"complete", "truncated", "blocked"}:
        return None
    if truncated != (visibility_status == "truncated"):
        return None
    # Truncation-marker invariants (exact-cap protocol):
    #   truncated=True  ⇔ marker present, exactly once, at the text end
    #   truncated=False ⇒ marker appears nowhere
    marker_count = text.count(TRUNCATION_MARKER)
    if truncated:
        if marker_count != 1 or not text.endswith(TRUNCATION_MARKER):
            return None
    elif marker_count != 0:
        return None
    # The persisted text must already be a safe projection: re-projecting
    # it may not change a single byte (catches any raw sentinel that ever
    # slipped into the stored shape).
    if redact_reasoning_text(text) != text:
        return None
    return payload


class UserSafeReasoningObserver:
    """Emergency kill-switch observer that discards reasoning at ingress.

    It emits no SSE events, retains no text, and produces no persistence
    payload while leaving progress and final-answer transport unchanged.
    """

    def __init__(
        self,
        *,
        emit: Callable[[RuntimeEvent], None],
        message_id: str,
        thread_id: str,
        turn_run_id: str,
        char_cap: int = DEFAULT_PROJECTION_CHAR_CAP,
    ) -> None:
        del emit, message_id, thread_id, turn_run_id, char_cap

    def on_analysis_started(self) -> None:
        return None

    def on_reasoning_delta(self, text: str) -> None:
        del text
        return None

    def on_analysis_finished(self) -> None:
        return None

    @property
    def started(self) -> bool:
        return False

    @property
    def has_content(self) -> bool:
        return False

    @property
    def projection_text(self) -> str:
        return ""

    @property
    def truncated(self) -> bool:
        return False

    @property
    def visibility_status(self) -> str:
        return "complete"

    def persistence_payload(self) -> None:
        return None

    def build_completed_event(self) -> None:
        return None


class ProviderReasoningObserver:
    """Production observer for provider-supplied readable reasoning.

    It publishes the deterministic redaction as
    ``AgenticReasoningStartedEvent`` / ``AgenticReasoningDeltaEvent`` via
    the injected sink. ``started`` fires only when the first non-empty
    projected increment exists — a provider that returns no non-empty
    reasoning produces no events at all.

    This is a deterministic host gate, not an LLM projector: ordinary model
    self-talk can remain after secrets and internal identifiers are removed.

    ``AgenticReasoningCompletedEvent`` is built by
    :meth:`build_completed_event` and emitted by the host only after the
    projection and the final answer were persisted in the same successful
    transaction.
    """

    def __init__(
        self,
        *,
        emit: Callable[[RuntimeEvent], None],
        message_id: str,
        thread_id: str,
        turn_run_id: str,
        char_cap: int = DEFAULT_PROJECTION_CHAR_CAP,
    ) -> None:
        self._emit = emit
        self._message_id = message_id
        self._thread_id = thread_id
        self._turn_run_id = turn_run_id
        self._buffer = ReasoningProjectionBuffer(char_cap=char_cap)
        self._seq: int = -1
        self._started: bool = False
        self._sealed: bool = False

    # -- ThinkingObserver protocol ------------------------------------------

    def on_analysis_started(self) -> None:
        # Phase-only signal. Progress owns phase; reasoning events are
        # gated strictly on projected content, never on phase.
        return None

    def on_reasoning_delta(self, text: str) -> None:
        if self._sealed or not text:
            return
        self._publish(self._buffer.feed(text))

    def on_analysis_finished(self) -> None:
        if self._sealed:
            return
        # Provider reasoning input ended for this run: release the
        # resolvable tail. No public "finished" event — completion is a
        # post-persistence host promise, not stream end.
        self._publish(self._buffer.flush())

    # -- Host API ------------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def has_content(self) -> bool:
        return self._buffer.has_content

    @property
    def projection_text(self) -> str:
        """The exact visible projection (== concat of emitted deltas)."""
        return self._buffer.text

    @property
    def truncated(self) -> bool:
        return self._buffer.truncated

    @property
    def visibility_status(self) -> str:
        """Buffer safety state: ``complete`` / ``truncated`` / ``blocked``.

        Read by the host for the non-sensitive reasoning observation on
        usage events / logs — a typed status string, never reasoning text.
        """
        return self._buffer.visibility_status

    def persistence_payload(self) -> dict[str, Any] | None:
        """Canonical snapshot for a normal terminal transaction.

        Validated through :func:`validate_reasoning_snapshot` at the write
        boundary (fail-closed): an invalid shape persists nothing and cold
        history shows no reasoning element.
        """
        if not self._started:
            return None
        return validate_reasoning_snapshot(self._buffer.snapshot())

    def build_completed_event(self) -> AgenticReasoningCompletedEvent | None:
        """Build the replayable-completion event after persist success.

        Returns None when reasoning never started — there is nothing to
        promise. Sealing stops any further delta emission regardless of
        provider behavior. The host emits it before ``message.completed``.
        """
        self._sealed = True
        if not self._started:
            return None
        self._seq += 1
        return AgenticReasoningCompletedEvent(
            message_id=self._message_id,
            thread_id=self._thread_id,
            turn_run_id=self._turn_run_id,
            seq=self._seq,
            has_content=True,
            truncated=self._buffer.truncated,
            visibility_status=self._buffer.visibility_status,
            projection_policy_version=PROJECTION_POLICY_VERSION,
        )

    # -- internals -----------------------------------------------------------

    def _publish(self, increment: str) -> None:
        if not increment:
            return
        if not self._started:
            self._started = True
            self._seq = 0
            self._emit(
                AgenticReasoningStartedEvent(
                    message_id=self._message_id,
                    thread_id=self._thread_id,
                    turn_run_id=self._turn_run_id,
                    seq=0,
                    projection_policy_version=PROJECTION_POLICY_VERSION,
                )
            )
        self._seq += 1
        self._emit(
            AgenticReasoningDeltaEvent(
                message_id=self._message_id,
                thread_id=self._thread_id,
                turn_run_id=self._turn_run_id,
                seq=self._seq,
                delta=increment,
            )
        )


__all__ = [
    "DEFAULT_PROJECTION_CHAR_CAP",
    "PROJECTION_POLICY_VERSION",
    "TRUNCATION_MARKER",
    "IncrementalRedactor",
    "ReasoningProjectionBuffer",
    "ProviderReasoningObserver",
    "UserSafeReasoningObserver",
    "redact_reasoning_text",
    "validate_reasoning_snapshot",
]
