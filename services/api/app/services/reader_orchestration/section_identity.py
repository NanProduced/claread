"""pure SectionIdentity + versioned delimiter-safe target keys.

No I/O, no jobs, no LLM. node_id / outline_revision never enter identity or keys.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

SECTION_TARGET_KEY_VERSION = "unit_range_v1"
_VERSION_PREFIX = f"{SECTION_TARGET_KEY_VERSION}|"
_IDENTITY_PREFIX = "section_id_v1|"


@dataclass(frozen=True, slots=True)
class SectionUnit:
    unit_id: str
    order_index: int


@dataclass(frozen=True, slots=True)
class SectionIdentity:
    """Durable geometric section identity (not a tree node)."""

    record_id: str
    base_id: str
    generation: int
    start_unit_id: str
    end_unit_id: str
    start_anchor_segment_id: str | None = None
    end_anchor_segment_id: str | None = None

    def geometric_key(self) -> tuple[str, str, str | None, str | None]:
        return (
            self.start_unit_id,
            self.end_unit_id,
            self.start_anchor_segment_id,
            self.end_anchor_segment_id,
        )

    def unit_pair(self) -> tuple[str, str]:
        return (self.start_unit_id, self.end_unit_id)


class SectionIdentityError(ValueError):
    """Fail-closed identity construction or target-key codec error."""


def _require_nonempty_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SectionIdentityError(f"{field} must be a non-empty string")
    return value


def _is_canonical_ascii_decimal(text: str) -> bool:
    """Canonical ASCII decimal: '0' or non-zero-leading digits. No '+', Unicode, empty."""
    if not text or not text.isascii() or not text.isdigit():
        return False
    if len(text) > 1 and text[0] == "0":
        return False
    return True


def _lp_encode(value: str) -> str:
    """Length-prefixed segment: '<decimal_len>.<payload>' (payload may contain '|')."""
    return f"{len(value)}.{value}"


def _lp_decode(blob: str, *, cursor: int) -> tuple[str, int]:
    if cursor >= len(blob):
        raise SectionIdentityError("target_key truncated while reading length prefix")
    dot = blob.find(".", cursor)
    if dot < 0:
        raise SectionIdentityError("target_key missing length prefix delimiter")
    length_text = blob[cursor:dot]
    if not _is_canonical_ascii_decimal(length_text):
        raise SectionIdentityError("target_key length prefix is not canonical ASCII decimal")
    length = int(length_text)
    start = dot + 1
    end = start + length
    if end > len(blob):
        raise SectionIdentityError("target_key length prefix exceeds remaining bytes")
    return blob[start:end], end


def encode_section_target_key(identity: SectionIdentity) -> str:
    """Encode range+anchors only (job/layer target_key geometry).

    Format::
        unit_range_v1|<lp:start>|<lp:end>|<lp:start_anchor>|<lp:end_anchor>

    Empty string payload means null anchor. Length-prefix makes unit_ids with
    '|' or ':' collision-safe (unlike naive start:end joins).
    """
    sa = identity.start_anchor_segment_id or ""
    ea = identity.end_anchor_segment_id or ""
    return (
        f"{_VERSION_PREFIX}"
        f"{_lp_encode(identity.start_unit_id)}|"
        f"{_lp_encode(identity.end_unit_id)}|"
        f"{_lp_encode(sa)}|"
        f"{_lp_encode(ea)}"
    )


def decode_section_target_key(target_key: str) -> tuple[str, str, str | None, str | None]:
    """Strict decode of :func:`encode_section_target_key`. Raises on any malformation."""
    if not isinstance(target_key, str) or not target_key.startswith(_VERSION_PREFIX):
        raise SectionIdentityError("target_key missing unit_range_v1 prefix")
    body = target_key[len(_VERSION_PREFIX) :]
    cursor = 0
    parts: list[str] = []
    for index in range(4):
        value, cursor = _lp_decode(body, cursor=cursor)
        parts.append(value)
        if index < 3:
            if cursor >= len(body) or body[cursor] != "|":
                raise SectionIdentityError("target_key missing field separator")
            cursor += 1
    if cursor != len(body):
        raise SectionIdentityError("target_key has trailing garbage")
    start_u, end_u, sa, ea = parts
    if not start_u or not end_u:
        raise SectionIdentityError("target_key start/end unit must be non-empty")
    sa_n = sa or None
    ea_n = ea or None
    # Re-encode equality: reject non-canonical equivalent encodings.
    probe = SectionIdentity(
        record_id="_",
        base_id="_",
        generation=1,
        start_unit_id=start_u,
        end_unit_id=end_u,
        start_anchor_segment_id=sa_n,
        end_anchor_segment_id=ea_n,
    )
    if encode_section_target_key(probe) != target_key:
        raise SectionIdentityError("target_key is not in canonical encoding")
    return start_u, end_u, sa_n, ea_n


def encode_section_identity(identity: SectionIdentity) -> str:
    """Full reversible identity encoding (includes record/source fence fields)."""
    return (
        f"{_IDENTITY_PREFIX}"
        f"{_lp_encode(identity.record_id)}|"
        f"{_lp_encode(identity.base_id)}|"
        f"{_lp_encode(str(identity.generation))}|"
        f"{_lp_encode(encode_section_target_key(identity))}"
    )


def decode_section_identity(blob: str) -> SectionIdentity:
    if not isinstance(blob, str) or not blob.startswith(_IDENTITY_PREFIX):
        raise SectionIdentityError("identity blob missing section_id_v1 prefix")
    body = blob[len(_IDENTITY_PREFIX) :]
    cursor = 0
    record_id, cursor = _lp_decode(body, cursor=cursor)
    if cursor >= len(body) or body[cursor] != "|":
        raise SectionIdentityError("identity blob missing separator after record_id")
    cursor += 1
    base_id, cursor = _lp_decode(body, cursor=cursor)
    if cursor >= len(body) or body[cursor] != "|":
        raise SectionIdentityError("identity blob missing separator after base_id")
    cursor += 1
    generation_text, cursor = _lp_decode(body, cursor=cursor)
    if cursor >= len(body) or body[cursor] != "|":
        raise SectionIdentityError("identity blob missing separator after generation")
    cursor += 1
    target_key, cursor = _lp_decode(body, cursor=cursor)
    if cursor != len(body):
        raise SectionIdentityError("identity blob has trailing garbage")
    if not record_id:
        raise SectionIdentityError("record_id must be non-empty")
    if not base_id:
        raise SectionIdentityError("base_id must be non-empty")
    if not _is_canonical_ascii_decimal(generation_text):
        raise SectionIdentityError("generation must be canonical ASCII decimal")
    generation = int(generation_text)
    if generation < 1:
        raise SectionIdentityError("generation must be >= 1")
    start_u, end_u, sa, ea = decode_section_target_key(target_key)
    identity = SectionIdentity(
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        start_unit_id=start_u,
        end_unit_id=end_u,
        start_anchor_segment_id=sa,
        end_anchor_segment_id=ea,
    )
    if encode_section_identity(identity) != blob:
        raise SectionIdentityError("identity blob is not in canonical encoding")
    return identity


def _unit_order_map(units: Sequence[SectionUnit]) -> dict[str, int]:
    order: dict[str, int] = {}
    for unit in units:
        if unit.unit_id in order:
            raise SectionIdentityError(f"duplicate unit_id in universe: {unit.unit_id}")
        order[unit.unit_id] = unit.order_index
    return order


def expand_closed_unit_range(
    *,
    start_unit_id: str,
    end_unit_id: str,
    ordered_units: Sequence[SectionUnit],
) -> tuple[str, ...]:
    """Return exact ordered unit ids in the closed reading-order range."""
    order = _unit_order_map(ordered_units)
    if start_unit_id not in order or end_unit_id not in order:
        raise SectionIdentityError("start/end unit missing from universe")
    start_o = order[start_unit_id]
    end_o = order[end_unit_id]
    if start_o > end_o:
        raise SectionIdentityError("inverted unit range")
    sorted_units = sorted(ordered_units, key=lambda u: u.order_index)
    return tuple(
        u.unit_id for u in sorted_units if start_o <= u.order_index <= end_o
    )


def normalize_section_anchors(
    *,
    start_unit_id: str,
    end_unit_id: str,
    start_anchor_segment_id: str | None,
    end_anchor_segment_id: str | None,
    anchor_to_unit: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """Keep both anchors only when both present and unit-owned; else (None, None)."""
    sa = start_anchor_segment_id if start_anchor_segment_id else None
    ea = end_anchor_segment_id if end_anchor_segment_id else None
    if sa is None or ea is None:
        return None, None
    if anchor_to_unit.get(sa) != start_unit_id:
        return None, None
    if anchor_to_unit.get(ea) != end_unit_id:
        return None, None
    return sa, ea


def try_build_section_identity(
    *,
    record_id: str,
    base_id: str,
    generation: int,
    start_unit_id: str,
    end_unit_id: str,
    ordered_units: Sequence[SectionUnit],
    start_anchor_segment_id: str | None = None,
    end_anchor_segment_id: str | None = None,
    anchor_to_unit: Mapping[str, str] | None = None,
) -> SectionIdentity:
    """Build a validated SectionIdentity or raise SectionIdentityError."""
    record_id = _require_nonempty_str(record_id, "record_id")
    base_id = _require_nonempty_str(base_id, "base_id")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise SectionIdentityError("generation must be int >= 1")
    start_unit_id = _require_nonempty_str(start_unit_id, "start_unit_id")
    end_unit_id = _require_nonempty_str(end_unit_id, "end_unit_id")
    expand_closed_unit_range(
        start_unit_id=start_unit_id,
        end_unit_id=end_unit_id,
        ordered_units=ordered_units,
    )
    sa, ea = normalize_section_anchors(
        start_unit_id=start_unit_id,
        end_unit_id=end_unit_id,
        start_anchor_segment_id=start_anchor_segment_id,
        end_anchor_segment_id=end_anchor_segment_id,
        anchor_to_unit=anchor_to_unit or {},
    )
    return SectionIdentity(
        record_id=record_id,
        base_id=base_id,
        generation=generation,
        start_unit_id=start_unit_id,
        end_unit_id=end_unit_id,
        start_anchor_segment_id=sa,
        end_anchor_segment_id=ea,
    )


def parse_section_identity_mapping(raw: object) -> SectionIdentity:
    """Parse ``input_json.section_identity`` object fail-closed.

    Requires non-empty ``record_id`` / ``base_id`` / ``start_unit_id`` /
    ``end_unit_id`` and int ``generation >= 1``. Anchors are optional
    (null/empty → None). Does not expand unit ranges (no ordered universe).
    """
    if not isinstance(raw, Mapping):
        raise SectionIdentityError("section_identity must be an object")
    record_id = _require_nonempty_str(raw.get("record_id"), "record_id")
    base_id = _require_nonempty_str(raw.get("base_id"), "base_id")
    generation_raw = raw.get("generation")
    if (
        not isinstance(generation_raw, int)
        or isinstance(generation_raw, bool)
        or generation_raw < 1
    ):
        raise SectionIdentityError("generation must be int >= 1")
    start_unit_id = _require_nonempty_str(raw.get("start_unit_id"), "start_unit_id")
    end_unit_id = _require_nonempty_str(raw.get("end_unit_id"), "end_unit_id")
    sa = raw.get("start_anchor_segment_id")
    ea = raw.get("end_anchor_segment_id")
    if sa is not None and not isinstance(sa, str):
        raise SectionIdentityError("start_anchor_segment_id must be str or null")
    if ea is not None and not isinstance(ea, str):
        raise SectionIdentityError("end_anchor_segment_id must be str or null")
    return SectionIdentity(
        record_id=record_id,
        base_id=base_id,
        generation=generation_raw,
        start_unit_id=start_unit_id,
        end_unit_id=end_unit_id,
        start_anchor_segment_id=sa or None,
        end_anchor_segment_id=ea or None,
    )


__all__ = [
    "SECTION_TARGET_KEY_VERSION",
    "SectionIdentity",
    "SectionIdentityError",
    "SectionUnit",
    "decode_section_identity",
    "decode_section_target_key",
    "encode_section_identity",
    "encode_section_target_key",
    "expand_closed_unit_range",
    "normalize_section_anchors",
    "parse_section_identity_mapping",
    "try_build_section_identity",
]
