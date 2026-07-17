"""T5.6a-P1 — SectionIdentity + strict delimiter-safe target keys."""

from __future__ import annotations

import pytest

from app.services.reader_orchestration.section_identity import (
    SECTION_TARGET_KEY_VERSION,
    SectionIdentity,
    SectionIdentityError,
    SectionUnit,
    decode_section_identity,
    decode_section_target_key,
    encode_section_identity,
    encode_section_target_key,
    expand_closed_unit_range,
    normalize_section_anchors,
    try_build_section_identity,
)

_UNITS = (
    SectionUnit("u1", 1),
    SectionUnit("u2", 2),
    SectionUnit("u3", 3),
    SectionUnit("u4", 4),
)


def test_try_build_happy_path_and_expand() -> None:
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u2",
        end_unit_id="u4",
        ordered_units=_UNITS,
    )
    assert identity.start_unit_id == "u2"
    assert identity.end_unit_id == "u4"
    assert identity.start_anchor_segment_id is None
    assert expand_closed_unit_range(
        start_unit_id="u2",
        end_unit_id="u4",
        ordered_units=_UNITS,
    ) == ("u2", "u3", "u4")


def test_inverted_and_missing_unit_fail() -> None:
    with pytest.raises(SectionIdentityError):
        try_build_section_identity(
            record_id="rec_1",
            base_id="base_1",
            generation=1,
            start_unit_id="u4",
            end_unit_id="u1",
            ordered_units=_UNITS,
        )
    with pytest.raises(SectionIdentityError):
        try_build_section_identity(
            record_id="rec_1",
            base_id="base_1",
            generation=1,
            start_unit_id="missing",
            end_unit_id="u1",
            ordered_units=_UNITS,
        )


def test_anchor_both_valid_kept() -> None:
    anchors = {"a_start": "u1", "a_end": "u3"}
    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u3",
        ordered_units=_UNITS,
        start_anchor_segment_id="a_start",
        end_anchor_segment_id="a_end",
        anchor_to_unit=anchors,
    )
    assert identity.start_anchor_segment_id == "a_start"
    assert identity.end_anchor_segment_id == "a_end"


def test_anchor_one_sided_or_wrong_unit_becomes_double_null() -> None:
    anchors = {"a_start": "u1", "a_end": "u3", "wrong": "u2"}
    sa, ea = normalize_section_anchors(
        start_unit_id="u1",
        end_unit_id="u3",
        start_anchor_segment_id="a_start",
        end_anchor_segment_id=None,
        anchor_to_unit=anchors,
    )
    assert (sa, ea) == (None, None)

    sa, ea = normalize_section_anchors(
        start_unit_id="u1",
        end_unit_id="u3",
        start_anchor_segment_id="a_start",
        end_anchor_segment_id="wrong",
        anchor_to_unit=anchors,
    )
    assert (sa, ea) == (None, None)

    identity = try_build_section_identity(
        record_id="rec_1",
        base_id="base_1",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u3",
        ordered_units=_UNITS,
        start_anchor_segment_id="a_start",
        end_anchor_segment_id=None,
        anchor_to_unit=anchors,
    )
    assert identity.start_anchor_segment_id is None
    assert identity.end_anchor_segment_id is None


def test_sc19_target_key_roundtrip_and_delimiter_safe() -> None:
    evil_units = (
        SectionUnit("a|b:c", 1),
        SectionUnit("a|b:d", 2),
        SectionUnit("plain", 3),
    )
    identity = try_build_section_identity(
        record_id="rec|x",
        base_id="base:1",
        generation=2,
        start_unit_id="a|b:c",
        end_unit_id="a|b:d",
        ordered_units=evil_units,
        start_anchor_segment_id="seg|1",
        end_anchor_segment_id="seg|2",
        anchor_to_unit={"seg|1": "a|b:c", "seg|2": "a|b:d"},
    )
    key = encode_section_target_key(identity)
    assert key.startswith(f"{SECTION_TARGET_KEY_VERSION}|")
    naive = f"{identity.start_unit_id}:{identity.end_unit_id}"
    assert key != naive
    start, end, sa, ea = decode_section_target_key(key)
    assert (start, end, sa, ea) == (
        "a|b:c",
        "a|b:d",
        "seg|1",
        "seg|2",
    )

    full = encode_section_identity(identity)
    restored = decode_section_identity(full)
    assert restored == identity
    assert encode_section_identity(restored) == full


def test_sc19_strict_decode_rejects_garbage_and_collision_trap() -> None:
    with pytest.raises(SectionIdentityError):
        decode_section_target_key("unit_range_v0|1.a|1.b|0.|0.")
    with pytest.raises(SectionIdentityError):
        decode_section_target_key("unit_range_v1|not-length-prefixed")
    with pytest.raises(SectionIdentityError):
        decode_section_target_key("unit_range_v1|1.a|1.b|0.|0.|extra")

    u = (
        SectionUnit("ab", 1),
        SectionUnit("c", 2),
        SectionUnit("a", 3),
        SectionUnit("bc", 4),
    )
    left = try_build_section_identity(
        record_id="r",
        base_id="b",
        generation=1,
        start_unit_id="ab",
        end_unit_id="c",
        ordered_units=u,
    )
    right = try_build_section_identity(
        record_id="r",
        base_id="b",
        generation=1,
        start_unit_id="a",
        end_unit_id="bc",
        ordered_units=u,
    )
    assert encode_section_target_key(left) != encode_section_target_key(right)
    assert decode_section_target_key(encode_section_target_key(left))[0:2] == (
        "ab",
        "c",
    )


def test_codec_rejects_leading_zero_length_prefix() -> None:
    # P1: `unit_range_v1|01.a|1.b|0.|0.` must throw.
    with pytest.raises(SectionIdentityError):
        decode_section_target_key("unit_range_v1|01.a|1.b|0.|0.")
    with pytest.raises(SectionIdentityError):
        decode_section_target_key("unit_range_v1|+1.a|1.b|0.|0.")
    with pytest.raises(SectionIdentityError):
        decode_section_target_key("unit_range_v1|.a|1.b|0.|0.")
    # Unicode digit length (fullwidth digit) is not ASCII decimal.
    with pytest.raises(SectionIdentityError):
        decode_section_target_key("unit_range_v1|\uff11.a|1.b|0.|0.")


def test_decode_identity_rejects_empty_record_base_and_noncanonical_generation() -> None:
    good = try_build_section_identity(
        record_id="rec",
        base_id="base",
        generation=1,
        start_unit_id="u1",
        end_unit_id="u1",
        ordered_units=_UNITS,
    )
    canonical = encode_section_identity(good)

    # Empty record_id / base_id via handcrafted blob.
    with pytest.raises(SectionIdentityError):
        decode_section_identity("section_id_v1|0.|4.base|1.1|12.unit_range_v1|")
    with pytest.raises(SectionIdentityError):
        # 0-length record
        decode_section_identity(
            "section_id_v1|0.|4.base|1.1|"
            + f"{len(encode_section_target_key(good))}."
            + encode_section_target_key(good)
        )
    with pytest.raises(SectionIdentityError):
        decode_section_identity(
            "section_id_v1|3.rec|0.|1.1|"
            + f"{len(encode_section_target_key(good))}."
            + encode_section_target_key(good)
        )

    # generation=01 non-canonical
    tk = encode_section_target_key(good)
    with pytest.raises(SectionIdentityError):
        decode_section_identity(
            f"section_id_v1|3.rec|4.base|2.01|{len(tk)}.{tk}"
        )

    # trailing garbage on identity blob
    with pytest.raises(SectionIdentityError):
        decode_section_identity(canonical + "|extra")

    # re-encode equality holds for valid
    assert decode_section_identity(canonical) == good


def test_identity_excludes_node_and_revision_fields() -> None:
    fields = set(SectionIdentity.__dataclass_fields__)
    assert "node_id" not in fields
    assert "outline_revision" not in fields
