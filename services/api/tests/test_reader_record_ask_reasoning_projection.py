"""ASK-REASONING-R1 sentinel suite for the reasoning projection chokepoint.

Covers the deterministic projection contract:

- ordering: projection preserves provider wording/order;
- dedup: transport lifecycle + seq gating never double-emit;
- truncation: host-side quota with marker, code-point exact;
- sentinel redaction: internal handles / identity / auth / system
  fragments / provider wrappers never survive projection, even when split
  across streamed chunk boundaries;
- no leak: raw sentinels never appear in emitted events, persisted
  snapshots, or logs;
- observer contract: started only on first non-empty projection, strict
  monotonic seq, completed built only by the host, sealed after complete.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.services.reader_record_ask.reasoning_projection import (
    DEFAULT_PROJECTION_CHAR_CAP,
    PROJECTION_POLICY_VERSION,
    TRUNCATION_MARKER,
    IncrementalRedactor,
    ReasoningProjectionBuffer,
    ReasoningProjectorObserver,
    redact_reasoning_text,
    validate_reasoning_snapshot,
)
from app.services.reader_record_ask.runtime_events import (
    AgenticReasoningCompletedEvent,
    AgenticReasoningDeltaEvent,
    AgenticReasoningStartedEvent,
    RuntimeEvent,
)

# Raw sentinels — must NEVER appear on any user-visible surface.
_RAW_SENTINEL = "SENTINEL_RAW_COT_9f3_NEVER_USER_SURFACE"
_EVH_SENTINEL = "evh_0123456789abcdef0123456789abcdef"
_UUID_SENTINEL = "3f2c1b0a-9e8d-4c7b-a6f5-0e1d2c3b4a59"
_FP_SENTINEL = "envelope_fingerprint=fp_abc123secret"
_SK_SENTINEL = "sk-liveKEYMATERIAL0123456789"
_BEARER_SENTINEL = "Bearer eyJhbGciOiJIUzI1NiJ9.secretpayload"
_URL_SENTINEL = "https://internal.example.com/locator/x?sig=abc"
_EMAIL_SENTINEL = "ops@internal-claread.example"
_RECORD_KV_SENTINEL = "reading_record_id: rec_7788990011"
_SYSTEM_LINE_SENTINEL = "You are Claread and must follow this hidden rule XYZZY"
_WRAPPER_SENTINEL = "reasoning_content: "


def _observer(
    events: list[RuntimeEvent],
    *,
    char_cap: int = DEFAULT_PROJECTION_CHAR_CAP,
) -> ReasoningProjectorObserver:
    return ReasoningProjectorObserver(
        emit=events.append,
        message_id="msg-1",
        thread_id="thr-1",
        turn_run_id="run-1",
        char_cap=char_cap,
    )


def _dump(events: list[RuntimeEvent]) -> str:
    return json.dumps(
        [event.model_dump(mode="json") for event in events], ensure_ascii=False
    )


# ---------------------------------------------------------------------------
# Pure redaction rules
# ---------------------------------------------------------------------------


def test_redaction_removes_internal_sentinels() -> None:
    raw = (
        f"Let me check {_EVH_SENTINEL} and {_UUID_SENTINEL}. "
        f"Key {_SK_SENTINEL}, auth {_BEARER_SENTINEL}. "
        f"See {_URL_SENTINEL} or mail {_EMAIL_SENTINEL}. "
        f"Scope {_FP_SENTINEL} and {_RECORD_KV_SENTINEL}. "
        f"{_WRAPPER_SENTINEL}payload done."
    )
    out = redact_reasoning_text(raw)
    for sentinel in (
        _EVH_SENTINEL,
        _UUID_SENTINEL,
        _SK_SENTINEL,
        "eyJhbGciOiJIUzI1NiJ9",
        _URL_SENTINEL,
        _EMAIL_SENTINEL,
        "fp_abc123secret",
        "rec_7788990011",
        "reasoning_content",
    ):
        assert sentinel not in out
    # The evidence handle becomes the neutral citation marker.
    assert "〔引用〕" in out
    # Legitimate surrounding wording survives.
    assert "Let me check" in out
    assert "payload done." in out


def test_redaction_drops_system_instruction_lines() -> None:
    raw = f"visible line one\n{_SYSTEM_LINE_SENTINEL}\nvisible line two\n"
    out = redact_reasoning_text(raw)
    assert "XYZZY" not in out
    assert "You are Claread" not in out
    assert "visible line one" in out
    assert "visible line two" in out


def test_redaction_pem_block_removed() -> None:
    raw = "before -----BEGIN RSA PRIVATE KEY-----\nMIIBsecret\n-----END RSA PRIVATE KEY----- after"
    out = redact_reasoning_text(raw)
    assert "MIIBsecret" not in out
    assert "BEGIN RSA" not in out
    assert "before" in out and "after" in out


def test_redaction_works_adjacent_to_cjk_characters() -> None:
    # CJK chars are word characters for regex \b; sentinels glued to CJK
    # text (typical in mixed-language reasoning) must still be redacted.
    raw = (
        f"访问https://internal.example.com/x再查{_EVH_SENTINEL}，"
        f"键sk-secretKEY123456789完。{_FP_SENTINEL}尾部保留。"
    )
    out = redact_reasoning_text(raw)
    assert "internal.example.com" not in out
    assert _EVH_SENTINEL not in out
    assert "sk-secretKEY" not in out
    assert "fp_abc123secret" not in out
    # CJK prose — including text glued right after sentinels — survives.
    assert "访问" in out
    assert "再查" in out
    assert "完。" in out
    assert "尾部保留。" in out


def test_redaction_empty_and_passthrough() -> None:
    assert redact_reasoning_text("") == ""
    plain = "先判断句子主干，再分析从句。"
    assert redact_reasoning_text(plain) == plain


# ---------------------------------------------------------------------------
# Incremental redactor — chunk-boundary safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("split", [1, 3, 5, 8, 12, 20])
def test_incremental_redaction_matches_whole_text_across_splits(split: int) -> None:
    raw = (
        f"分析 {_EVH_SENTINEL} 之后，再看 {_UUID_SENTINEL}，"
        f"最后检查 {_FP_SENTINEL}。结束。"
    )
    expected = redact_reasoning_text(raw)
    redactor = IncrementalRedactor()
    pieces: list[str] = []
    for i in range(0, len(raw), split):
        pieces.append(redactor.feed(raw[i : i + split]))
    pieces.append(redactor.flush())
    assert "".join(pieces) == expected


def test_incremental_redactor_holds_partial_handle_until_complete() -> None:
    # Half of a handle must never be emitted in a form that survives
    # redaction once the full handle arrives.
    redactor = IncrementalRedactor()
    first = redactor.feed("引用开始 " + _EVH_SENTINEL[:14])
    second = redactor.feed(_EVH_SENTINEL[14:] + " 结束")
    tail = redactor.flush()
    combined = first + second + tail
    assert _EVH_SENTINEL not in combined
    assert "evh_0123" not in combined.replace("〔引用〕", "")
    assert "〔引用〕" in combined
    assert "引用开始" in combined and "结束" in combined


def test_incremental_redactor_releases_safe_text_immediately() -> None:
    # R2: ordinary safe text is released on the same feed it arrives in —
    # no fixed global holdback delays short reasoning until flush.
    redactor = IncrementalRedactor()
    assert redactor.feed("短句。") == "短句。"
    assert redactor.feed("再来一句。") == "再来一句。"
    # Only the minimal ambiguous tail (a trailing word run that could
    # still precede ``@`` / complete a handle) is retained.
    out = redactor.feed("plain trailing word")
    assert out == "plain trailing "
    assert redactor.flush() == "word"
    assert redactor.flush() == ""  # idempotent


def test_unterminated_pem_body_never_leaks_and_terminator_resumes() -> None:
    # R2 fail-closed: an unterminated PEM block discards its whole body
    # (not just the BEGIN/END markers — the base64 key material itself)
    # until the terminator arrives; text after the terminator resumes.
    body_sentinel = "MIIBVAIBADANBgkqhkiG9w0BAQEFAASCAT4wgg"
    redactor = IncrementalRedactor()
    out1 = redactor.feed("before -----BEGIN RSA PRIVATE KEY-----\n")
    out2 = redactor.feed(body_sentinel + "\nmore-secret-body\n")
    out3 = redactor.feed("still-secret ")
    # Nothing from the region is released while the block is open.
    assert body_sentinel not in out1 + out2 + out3
    assert "more-secret-body" not in out1 + out2 + out3
    assert "still-secret" not in out1 + out2 + out3
    assert "BEGIN" not in out1 + out2 + out3
    # The terminator closes the region; trailing text resumes normally.
    out4 = redactor.feed("-----END RSA PRIVATE KEY----- after")
    out5 = redactor.flush()
    combined = out1 + out2 + out3 + out4 + out5
    assert body_sentinel not in combined
    assert "more-secret-body" not in combined
    assert "still-secret" not in combined
    assert "before" in combined
    assert "after" in combined
    assert combined == redact_reasoning_text(
        "before -----BEGIN RSA PRIVATE KEY-----\n"
        + body_sentinel
        + "\nmore-secret-body\nstill-secret "
        + "-----END RSA PRIVATE KEY----- after"
    )


def test_unterminated_pem_discards_body_on_flush() -> None:
    body_sentinel = "MIIBSECRETKEYMATERIAL0123456789abcdef"
    redactor = IncrementalRedactor()
    outs = [redactor.feed("pre -----BEGIN K-----\n")]
    outs.append(redactor.feed(body_sentinel + "\n"))
    outs.append(redactor.flush())
    combined = "".join(outs)
    assert body_sentinel not in combined
    assert "pre" in combined
    # flush left the redactor empty and usable-state defined.
    assert redactor.flush() == ""


def test_pem_body_overflow_seals_redactor_permanently() -> None:
    # R2: an unterminated PEM region scanned beyond the ceiling seals the
    # redactor permanently — it must NEVER resume ordinary output, even
    # for perfectly safe text fed afterwards (fail-closed over liveness).
    body_sentinel = "MIIBOVERFLOWBODY" * 4096  # ~1MB of key material
    redactor = IncrementalRedactor()
    outs = [redactor.feed("safe prefix。")]
    outs.append(redactor.feed("-----BEGIN RSA PRIVATE KEY-----\n"))
    for i in range(0, len(body_sentinel), 8192):
        outs.append(redactor.feed(body_sentinel[i : i + 8192]))
    outs.append(redactor.feed("trailing innocent text"))
    outs.append(redactor.flush())
    combined = "".join(outs)
    assert redactor.sealed
    # Pre-PEM safe text was released; nothing from the region or after.
    assert "safe prefix。" in combined
    assert "MIIBOVERFLOWBODY" not in combined
    assert "trailing innocent text" not in combined
    # Seal is permanent.
    assert redactor.feed("perfectly safe。") == ""
    assert redactor.flush() == ""


# ---------------------------------------------------------------------------
# R3 P1: strict PEM BEGIN/END label pairing
# ---------------------------------------------------------------------------


def test_pem_mismatched_end_never_reopens_normal_output() -> None:
    # R3 P1 (task example): BEGIN RSA … / SECRET_ONE / END CERTIFICATE
    # (mismatch) / SECRET_TWO. BEGIN RSA has no matching END, so the whole
    # region — including SECRET_TWO after the mismatched END — is dropped.
    raw = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "SECRET_ONE\n"
        "-----END CERTIFICATE-----\n"
        "SECRET_TWO"
    )
    # Whole-text projection.
    out = redact_reasoning_text(raw)
    assert "SECRET_ONE" not in out
    assert "SECRET_TWO" not in out
    assert "BEGIN" not in out and "END" not in out
    # Streaming, single feed — must equal the whole-text projection.
    redactor = IncrementalRedactor()
    streamed = redactor.feed(raw) + redactor.flush()
    assert "SECRET_ONE" not in streamed
    assert "SECRET_TWO" not in streamed
    assert streamed == out


def test_pem_mismatched_end_cross_chunk() -> None:
    # The mismatched END and the trailing secret arrive in separate chunks;
    # nothing after the unterminated BEGIN is ever released.
    redactor = IncrementalRedactor()
    outs = [
        redactor.feed("-----BEGIN RSA PRIVATE KEY-----\n"),
        redactor.feed("SECRET_ONE\n"),
        redactor.feed("-----END CERTIFICATE-----\n"),  # mismatch
        redactor.feed("SECRET_TWO"),
        redactor.flush(),
    ]
    combined = "".join(outs)
    assert "SECRET_ONE" not in combined
    assert "SECRET_TWO" not in combined


def test_pem_multiple_mismatched_ends_then_correct_end_recovers() -> None:
    # Mismatched ENDs are body; the matching END closes the region and
    # trailing visible text resumes.
    raw = (
        "lead "
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "SECRET_ONE\n"
        "-----END CERTIFICATE-----\n"  # mismatch
        "-----END PUBLIC KEY-----\n"  # mismatch
        "-----END RSA PRIVATE KEY-----\n"  # match
        "VISIBLE"
    )
    redactor = IncrementalRedactor()
    out = redactor.feed(raw) + redactor.flush()
    whole = redact_reasoning_text(raw)
    assert "SECRET_ONE" not in out
    assert "CERTIFICATE" not in out and "PUBLIC KEY" not in out
    assert "lead" in out
    assert "VISIBLE" in out
    assert out == whole


def test_pem_correct_end_split_across_chunks_recovers() -> None:
    # The matching END terminator is split across chunk boundaries; the END
    # prefix tail is retained so the region still closes and trailing text
    # resumes.
    redactor = IncrementalRedactor()
    outs = [
        redactor.feed("lead -----BEGIN RSA PRIVATE KEY-----\nSECRET\n"),
        redactor.feed("-----END RSA PRIV"),  # partial terminator
        redactor.feed("ATE KEY-----\nVISIBLE"),
        redactor.flush(),
    ]
    combined = "".join(outs)
    assert "SECRET" not in combined
    assert "lead" in combined
    assert "VISIBLE" in combined


def test_pem_label_normalization_pairs_across_whitespace() -> None:
    # Labels are normalized (whitespace collapsed) before pairing, so a
    # double-spaced BEGIN label still pairs with a single-spaced END.
    raw = "x -----BEGIN RSA  PRIVATE KEY-----\nSEC\n-----END RSA PRIVATE KEY----- y"
    redactor = IncrementalRedactor()
    out = redactor.feed(raw) + redactor.flush()
    whole = redact_reasoning_text(raw)
    assert "SEC" not in out
    assert "x" in out and "y" in out
    assert out == whole


def test_pem_mismatch_overflow_seals_permanently() -> None:
    # 错配后超限: mismatched END(s) then a body beyond the PEM ceiling seals
    # the redactor permanently — no output afterwards.
    body = "MISMATCHED_SECRET_BODY" * 4096  # ~90KB
    redactor = IncrementalRedactor()
    outs = [redactor.feed("safe。")]
    outs.append(redactor.feed("-----BEGIN RSA PRIVATE KEY-----\n"))
    outs.append(redactor.feed("-----END CERTIFICATE-----\n"))  # mismatch
    for i in range(0, len(body), 8192):
        outs.append(redactor.feed(body[i : i + 8192]))
    outs.append(redactor.feed("trailing innocent"))
    outs.append(redactor.flush())
    combined = "".join(outs)
    assert redactor.sealed
    assert "safe。" in combined
    assert "MISMATCHED_SECRET_BODY" not in combined
    assert "trailing innocent" not in combined
    assert redactor.feed("perfectly safe。") == ""


def test_observer_pem_mismatch_body_absent_from_all_surfaces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # With a mismatched END, both secrets stay inside the unterminated
    # region and must be absent from delta / snapshot / SSE / DB payload /
    # log — and no reasoning events or persistence occur at all.
    secret_one = "SECRET_ONE_BASE64KEYMATERIAL_000"
    secret_two = "SECRET_TWO_BASE64KEYMATERIAL_111"
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    with caplog.at_level(logging.DEBUG):
        observer.on_reasoning_delta("-----BEGIN RSA PRIVATE KEY-----\n")
        observer.on_reasoning_delta(secret_one + "\n")
        observer.on_reasoning_delta("-----END CERTIFICATE-----\n")  # mismatch
        observer.on_reasoning_delta(secret_two)
        observer.on_analysis_finished()
        completed = observer.build_completed_event()
    wire = _dump(events + ([completed] if completed else []))
    snapshot = observer.persistence_payload()
    snapshot_blob = json.dumps(snapshot, ensure_ascii=False) if snapshot else ""
    for surface in (wire, snapshot_blob, caplog.text):
        assert secret_one not in surface
        assert secret_two not in surface
    # Nothing was projected → no events, no persistence, no completion.
    assert events == []
    assert snapshot is None
    assert completed is None


def test_ambiguous_token_overflow_seals_redactor_permanently() -> None:
    # A single ambiguous token (here: one unbounded URL with no
    # terminator) beyond the pending ceiling seals the redactor rather
    # than ever releasing unresolved raw content.
    redactor = IncrementalRedactor()
    out1 = redactor.feed("正常开头。")
    out2 = redactor.feed("https://internal.example.com/" + "x" * 20_000)
    out3 = redactor.feed(" normal after")
    out4 = redactor.flush()
    assert redactor.sealed
    combined = out1 + out2 + out3 + out4
    assert "正常开头。" in combined  # released before the ambiguity
    assert "internal.example.com" not in combined
    assert "normal after" not in combined  # never resumes
    assert out4 == ""


def test_committed_prefix_redacts_complete_sentinel() -> None:
    # A sentinel fully inside the committed region is redacted there.
    raw = "正" * 100 + " " + _EVH_SENTINEL + " " + "尾" * 500
    redactor = IncrementalRedactor()
    out1 = redactor.feed(raw)
    out2 = redactor.flush()
    combined = out1 + out2
    assert _EVH_SENTINEL not in combined
    assert "〔引用〕" in combined
    assert combined == redact_reasoning_text(raw)


def test_long_url_never_leaks_whether_held_or_committed() -> None:
    # A URL terminated by whitespace within the same feed is redacted on
    # commit; the surrounding safe prose is released immediately (R2
    # realtime semantics — no fixed holdback).
    long_url = "https://internal.example.com/" + "p" * 400
    raw = "正" * 240 + long_url + " 结尾"
    redactor = IncrementalRedactor()
    out1 = redactor.feed(raw)
    out2 = redactor.flush()
    combined = out1 + out2
    assert long_url not in combined
    assert "internal.example.com" not in combined
    assert "正" * 240 in out1  # safe prefix released on the same feed
    assert "结尾" in combined
    assert combined == redact_reasoning_text(raw)


_STREAM_EQUIVALENCE_SAMPLES: tuple[str, ...] = (
    f"分析 {_EVH_SENTINEL} 之后，再看 {_UUID_SENTINEL}，最后 {_FP_SENTINEL}。",
    f"id {_UUID_SENTINEL} end",
    f"key {_SK_SENTINEL} tail",
    f"auth {_BEARER_SENTINEL} here",
    "kv record_id=rec_7788990011 ok",
    "kv2 record_id: rec multi-word stays",
    f"url {_URL_SENTINEL} done",
    "url2 http://x.co z",
    f"mail {_EMAIL_SENTINEL} end",
    f"{_SYSTEM_LINE_SENTINEL}\nvisible line",
    "wrapper <think>x</think> end",
    "tag2 <thinking> deep </thinking> end",
    "pem -----BEGIN K-----\nSEC\n-----END K----- tail",
    "pem2 x -----BEGIN RSA PRIVATE KEY-----\nabc\nno terminator end",
    f"rc {_WRAPPER_SENTINEL}leak end",
    "angle a < b and c > d end",
    f"mixed {_EVH_SENTINEL} and {_BEARER_SENTINEL} fin",
    f"cjk 先分析 {_EVH_SENTINEL} 再确认",
    "gen generation: 42 end",
    "dash ----- not a pem here",
)


@pytest.mark.parametrize("raw", _STREAM_EQUIVALENCE_SAMPLES)
@pytest.mark.parametrize("split", [1, 2, 3, 5, 7, 13])
def test_streaming_projection_equivalent_to_whole_text(raw: str, split: int) -> None:
    # R2 lock: for every sentinel shape at every split granularity, the
    # concatenation of streamed releases equals the whole-text projection
    # byte-for-byte (order preserved, nothing double-emitted, nothing
    # lost — the hot≡cold construction invariant).
    expected = redact_reasoning_text(raw)
    redactor = IncrementalRedactor()
    pieces = [
        redactor.feed(raw[i : i + split]) for i in range(0, len(raw), split)
    ]
    pieces.append(redactor.flush())
    assert "".join(pieces) == expected


# ---------------------------------------------------------------------------
# Projection buffer — ordering, truncation, snapshot
# ---------------------------------------------------------------------------


def test_buffer_preserves_order_and_concatenates() -> None:
    buffer = ReasoningProjectionBuffer()
    parts = [buffer.feed(c) for c in ("第一段。", "第二段。", "第三段。")]
    parts.append(buffer.flush())
    visible = "".join(p for p in parts if p)
    assert visible == "第一段。第二段。第三段。"
    assert buffer.text == visible
    assert not buffer.truncated
    assert buffer.char_count == len(visible)


def test_buffer_truncates_with_marker_at_cap() -> None:
    cap = 100
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    raw = "思" * 500
    out = buffer.feed(raw) + buffer.flush()
    assert buffer.truncated
    assert out.endswith(TRUNCATION_MARKER)
    assert len(buffer.text) == cap
    assert buffer.text == out
    # Feeding more after truncation emits nothing further.
    assert buffer.feed("更多思考") == ""
    assert buffer.flush() == ""
    assert len(buffer.text) == cap


def test_buffer_truncation_is_code_point_exact() -> None:
    cap = 64
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    raw = "🌟" * 200  # astral code points
    buffer.feed(raw)
    buffer.flush()
    # No broken scalars: round-trips through strict UTF-8 encode.
    buffer.text.encode("utf-8", errors="strict")
    assert buffer.text.endswith(TRUNCATION_MARKER)
    assert len(buffer.text) <= cap


def test_buffer_snapshot_shape() -> None:
    buffer = ReasoningProjectionBuffer(char_cap=50)
    buffer.feed("可见内容。")
    buffer.flush()
    snapshot = buffer.snapshot()
    assert snapshot == {
        "projection_policy_version": PROJECTION_POLICY_VERSION,
        "text": "可见内容。",
        "char_count": len("可见内容。"),
        "truncated": False,
    }


def test_buffer_redacts_while_projecting() -> None:
    buffer = ReasoningProjectionBuffer()
    out = buffer.feed(f"检查 {_EVH_SENTINEL}。") + buffer.flush()
    assert _EVH_SENTINEL not in out
    assert "〔引用〕" in out
    assert _EVH_SENTINEL not in buffer.text


# ---------------------------------------------------------------------------
# Observer contract — gating, seq, events, leaks
# ---------------------------------------------------------------------------


def test_observer_started_only_on_first_nonempty_projection() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_analysis_started()  # phase-only: no event
    assert events == []
    observer.on_reasoning_delta("")  # empty: no event
    assert events == []
    observer.on_reasoning_delta(f"仅包含 {_EVH_SENTINEL}。")
    observer.on_analysis_finished()  # releases the incremental holdback
    # The handle redacts to 〔引用〕 — that IS non-empty visible content,
    # so started fires exactly once.
    started = [e for e in events if isinstance(e, AgenticReasoningStartedEvent)]
    assert len(started) == 1
    assert started[0].seq == 0
    assert started[0].projection_policy_version == PROJECTION_POLICY_VERSION
    assert started[0].message_id == "msg-1"
    assert started[0].thread_id == "thr-1"
    assert started[0].turn_run_id == "run-1"
    # The raw handle never appears; the projection does.
    assert _EVH_SENTINEL not in _dump(events)
    assert "〔引用〕" in _dump(events)


def test_observer_no_events_when_reasoning_entirely_invisible() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_analysis_started()
    # Fully dropped content (system-instruction line only).
    observer.on_reasoning_delta(_SYSTEM_LINE_SENTINEL + "\n")
    observer.on_analysis_finished()
    assert events == []
    assert observer.persistence_payload() is None
    assert observer.build_completed_event() is None


def test_observer_no_events_when_provider_returns_no_reasoning() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_analysis_started()
    observer.on_analysis_finished()
    assert events == []
    assert observer.persistence_payload() is None
    assert observer.build_completed_event() is None


def test_observer_delta_seq_strictly_monotonic() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    # R2 streaming redactor releases safe CJK text on the same feed, so
    # each chunk produces its own delta with strictly increasing seq.
    chunks = ("甲" * 150 + "。", "乙" * 150 + "。", "丙" * 150 + "。")
    for chunk in chunks:
        observer.on_reasoning_delta(chunk)
    observer.on_analysis_finished()
    deltas = [e for e in events if isinstance(e, AgenticReasoningDeltaEvent)]
    seqs = [d.seq for d in deltas]
    assert len(seqs) >= 2
    assert seqs == list(range(1, len(deltas) + 1))
    assert "".join(d.delta for d in deltas) == "".join(chunks)
    # Empty / invisible projected increments consume no seq; a dropped
    # instruction line leaves only its newline, which is a real (if
    # invisible) projected increment — concat(deltas) must equal the
    # persisted text byte-for-byte (hot≡cold invariant).
    observer2_events: list[RuntimeEvent] = []
    observer2 = _observer(observer2_events)
    observer2.on_reasoning_delta("实。")
    observer2.on_reasoning_delta("")  # raw empty
    observer2.on_reasoning_delta(_SYSTEM_LINE_SENTINEL + "\n")  # line dropped
    observer2.on_analysis_finished()
    deltas2 = [
        e for e in observer2_events if isinstance(e, AgenticReasoningDeltaEvent)
    ]
    seqs2 = [e.seq for e in deltas2]
    assert seqs2 == [1, 2]
    assert "".join(d.delta for d in deltas2) == "实。\n"
    payload2 = observer2.persistence_payload()
    assert payload2 is not None
    assert payload2["text"] == "实。\n"


def test_observer_completed_seq_is_last_and_host_built() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_reasoning_delta("思考一。")
    observer.on_reasoning_delta("思考二。")
    observer.on_analysis_finished()
    # The observer never emits completed itself.
    assert not any(
        isinstance(e, AgenticReasoningCompletedEvent) for e in events
    )
    last_delta_seq = max(
        e.seq for e in events if isinstance(e, AgenticReasoningDeltaEvent)
    )
    completed = observer.build_completed_event()
    assert completed is not None
    assert completed.seq == last_delta_seq + 1
    assert completed.has_content is True
    assert completed.truncated is False
    assert completed.projection_policy_version == PROJECTION_POLICY_VERSION
    assert (completed.message_id, completed.thread_id, completed.turn_run_id) == (
        "msg-1",
        "thr-1",
        "run-1",
    )


def test_observer_sealed_after_completed_stops_deltas() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_reasoning_delta("思考。")
    observer.on_analysis_finished()
    assert observer.build_completed_event() is not None
    count_before = len(events)
    observer.on_reasoning_delta("迟到的思考。")
    observer.on_analysis_finished()
    assert len(events) == count_before


def test_observer_truncation_flagged_in_completed_and_snapshot() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events, char_cap=80)
    observer.on_reasoning_delta("思" * 500)
    observer.on_analysis_finished()
    completed = observer.build_completed_event()
    assert completed is not None
    assert completed.truncated is True
    payload = observer.persistence_payload()
    assert payload is not None
    assert payload["truncated"] is True
    assert payload["text"].endswith(TRUNCATION_MARKER)


def test_observer_multi_round_single_stream_no_dup() -> None:
    # Mirrors transport lifecycle behavior: two reasoning rounds within
    # one turn append to ONE projection stream with monotonic seq.
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_reasoning_delta("第一轮思考。")
    # (tool round boundary: observer sees no boundary — single stream)
    observer.on_reasoning_delta("第二轮思考。")
    observer.on_analysis_finished()
    deltas = [e for e in events if isinstance(e, AgenticReasoningDeltaEvent)]
    assert "".join(d.delta for d in deltas) == "第一轮思考。第二轮思考。"
    started = [e for e in events if isinstance(e, AgenticReasoningStartedEvent)]
    assert len(started) == 1


def test_observer_persistence_payload_matches_concatenated_deltas() -> None:
    # The golden hot/cold invariant at projection level:
    # concat(deltas) == persisted text.
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    raw_chunks = [
        f"先检查 {_EVH_SENTINEL}。",
        "再分析句子结构。",
        f"最后确认 {_UUID_SENTINEL} 的范围。",
    ]
    for chunk in raw_chunks:
        observer.on_reasoning_delta(chunk)
    observer.on_analysis_finished()
    deltas = [e for e in events if isinstance(e, AgenticReasoningDeltaEvent)]
    payload = observer.persistence_payload()
    assert payload is not None
    assert "".join(d.delta for d in deltas) == payload["text"]
    assert payload["char_count"] == len(payload["text"])


def test_observer_raw_sentinel_never_published_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The projection preserves provider wording; security means sensitive
    # shapes (handles, keys, identity) never survive projection and raw
    # input never reaches logs.
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    with caplog.at_level(logging.DEBUG):
        observer.on_reasoning_delta(
            f"先检查 {_EVH_SENTINEL}，再用 {_SK_SENTINEL}，最后 {_FP_SENTINEL}。"
        )
        observer.on_analysis_finished()
        completed = observer.build_completed_event()
    wire = _dump(events + ([completed] if completed else []))
    # Sensitive raw shapes never appear on any published surface.
    assert _EVH_SENTINEL not in wire
    assert _SK_SENTINEL not in wire
    assert "fp_" not in wire
    # Projected content does flow (wording preserved).
    assert "先检查" in wire
    assert "〔引用〕" in wire
    # And nothing raw reached logs.
    assert _EVH_SENTINEL not in caplog.text
    assert _SK_SENTINEL not in caplog.text


def test_completed_event_carries_no_content_field() -> None:
    completed = AgenticReasoningCompletedEvent(
        message_id="m",
        thread_id="t",
        turn_run_id="r",
        seq=3,
        has_content=True,
        truncated=False,
        projection_policy_version=PROJECTION_POLICY_VERSION,
    )
    dumped = completed.model_dump(mode="json")
    assert "delta" not in dumped
    assert "text" not in dumped


def test_delta_event_rejects_empty_delta() -> None:
    with pytest.raises(ValueError):
        AgenticReasoningDeltaEvent(
            message_id="m",
            thread_id="t",
            turn_run_id="r",
            seq=1,
            delta="",
        )


def test_events_reject_extra_fields() -> None:
    with pytest.raises(ValueError):
        AgenticReasoningStartedEvent(
            message_id="m",
            thread_id="t",
            turn_run_id="r",
            seq=0,
            projection_policy_version=PROJECTION_POLICY_VERSION,
            envelope_fingerprint="fp_leak",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# R2: PEM body leak scan across every observer surface
# ---------------------------------------------------------------------------


def test_observer_pem_body_absent_from_all_surfaces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The PEM body (base64 key material) must not appear in ANY emitted
    # delta, the persistence payload, the completed event, or logs — not
    # just the BEGIN/END markers.
    pem_body = "MIIBVAIBADANBgkqhkiG9w0BAQEFAASCBKeyBODYsecret123"
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    with caplog.at_level(logging.DEBUG):
        observer.on_reasoning_delta("正常思考。")
        observer.on_reasoning_delta("-----BEGIN RSA PRIVATE KEY-----\n")
        observer.on_reasoning_delta(pem_body + "\n")
        observer.on_reasoning_delta("more-body-lines\n")
        observer.on_analysis_finished()  # unterminated at stream end
        completed = observer.build_completed_event()
    wire = _dump(events + ([completed] if completed else []))
    snapshot = observer.persistence_payload()
    snapshot_blob = json.dumps(snapshot, ensure_ascii=False)
    for surface in (wire, snapshot_blob, caplog.text):
        assert pem_body not in surface
        assert "more-body-lines" not in surface
        assert "BEGIN RSA" not in surface
    # Pre-PEM visible reasoning survives and persists.
    assert observer.projection_text == "正常思考。"
    assert snapshot is not None
    assert snapshot["text"] == "正常思考。"
    assert snapshot["char_count"] == len("正常思考。")
    started = [e for e in events if isinstance(e, AgenticReasoningStartedEvent)]
    assert len(started) == 1


def test_observer_seal_stops_events_but_keeps_safe_prefix() -> None:
    # Overflow seal mid-turn: safe pre-overflow projection persists and
    # streams; post-seal raw is dropped entirely (fail-closed).
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_reasoning_delta("安全前缀。")
    observer.on_reasoning_delta("https://internal.example.com/" + "y" * 20_000)
    observer.on_reasoning_delta(" post-seal text")
    observer.on_analysis_finished()
    completed = observer.build_completed_event()
    wire = _dump(events + ([completed] if completed else []))
    assert "安全前缀。" in wire
    assert "internal.example.com" not in wire
    assert "post-seal text" not in wire
    snapshot = observer.persistence_payload()
    assert snapshot is not None
    assert snapshot["text"] == "安全前缀。"


# ---------------------------------------------------------------------------
# R2: quota precision
# ---------------------------------------------------------------------------


def test_quota_marker_exactly_once_and_count_exact() -> None:
    cap = 100
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    outs = [buffer.feed("思" * 60)]
    outs.append(buffer.feed("考" * 60))  # crosses the cap mid-chunk
    outs.append(buffer.flush())
    visible = "".join(o for o in outs if o)
    assert buffer.truncated
    assert visible.count(TRUNCATION_MARKER) == 1
    assert visible.endswith(TRUNCATION_MARKER)
    # Never exceeds the cap, even at the crossing boundary.
    assert len(buffer.text) == cap
    assert buffer.text == visible
    snapshot = buffer.snapshot()
    assert snapshot["char_count"] == cap
    assert snapshot["truncated"] is True
    assert len(snapshot["text"]) == cap


def test_quota_exact_fit_has_no_marker() -> None:
    # Exact-cap protocol (R3): text without a marker may fill up to
    # char_cap − len(marker) (the content reservation) and stays
    # truncated=False with no marker.
    # NOTE: advance_round() is a no-op (R4-4) — this test exercises the
    # total-cap boundary in isolation.
    cap = 50
    content_cap = cap - len(TRUNCATION_MARKER)
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    buffer.advance_round()  # no-op (R4-4)
    buffer.feed("字" * content_cap)  # exactly fills the content reservation
    buffer.flush()
    assert not buffer.truncated
    assert TRUNCATION_MARKER not in buffer.text
    assert buffer.char_count == content_cap


def test_quota_fill_reservation_then_overflow_appends_marker_once() -> None:
    # Exact-cap protocol (R3): filling exactly to the content reservation
    # leaves no marker; one more code point crosses into the reservation and
    # appends the marker exactly once, landing the total at exactly char_cap
    # (marker at end). This is the case R2 got wrong (truncated=True with no
    # marker).
    # NOTE: advance_round() is a no-op (R4-4) — this test exercises the
    # total-cap boundary in isolation.
    cap = 50
    content_cap = cap - len(TRUNCATION_MARKER)
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    buffer.advance_round()  # no-op (R4-4)
    buffer.feed("字" * content_cap)
    assert not buffer.truncated
    assert TRUNCATION_MARKER not in buffer.text
    assert buffer.char_count == content_cap
    buffer.feed("更")  # one more code point ⇒ actual truncation
    assert buffer.truncated
    assert buffer.text.endswith(TRUNCATION_MARKER)
    assert buffer.text.count(TRUNCATION_MARKER) == 1
    assert buffer.char_count == cap  # lands exactly at the cap
    # No rewrite of already-emitted text: the first content_cap chars are
    # exactly what was emitted before truncation.
    assert buffer.text[:content_cap] == "字" * content_cap


@pytest.mark.parametrize(
    "char_cap",
    [
        True,
        False,
        0,
        -1,
        len(TRUNCATION_MARKER) - 1,
        4_000.0,
        "4000",
    ],
)
def test_quota_rejects_invalid_char_cap_before_streaming(
    char_cap: object,
) -> None:
    with pytest.raises(ValueError, match="char_cap"):
        ReasoningProjectionBuffer(char_cap=char_cap)  # type: ignore[arg-type]


def test_quota_accepts_marker_sized_minimum_without_exceeding_cap() -> None:
    buffer = ReasoningProjectionBuffer(char_cap=len(TRUNCATION_MARKER))

    increment = buffer.feed("思")

    assert increment == TRUNCATION_MARKER
    assert buffer.text == TRUNCATION_MARKER
    assert buffer.char_count == buffer.char_cap
    assert buffer.truncated is True


def test_quota_rejects_further_raw_after_cap() -> None:
    cap = 40
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    buffer.feed("一" * 100)
    assert buffer.truncated
    # After the cap: no further raw is fed to the redactor or buffered —
    # feeding sentinel-bearing raw cannot surface anything.
    assert buffer.feed(f"later {_EVH_SENTINEL}") == ""
    assert buffer.flush() == ""
    assert buffer.char_count == cap
    assert _EVH_SENTINEL not in buffer.text
    # White-box: the redactor never received the post-cap raw.
    assert buffer._redactor._pending == ""


# ---------------------------------------------------------------------------
# R2: canonical snapshot validator (shared by write + cold-read paths)
# ---------------------------------------------------------------------------


def _canonical_snapshot(**overrides: object) -> dict[str, object]:
    snap: dict[str, object] = {
        "projection_policy_version": PROJECTION_POLICY_VERSION,
        "text": "先判断句子主干。",
        "char_count": len("先判断句子主干。"),
        "truncated": False,
    }
    snap.update(overrides)
    return snap


def test_validate_reasoning_snapshot_accepts_canonical_shape() -> None:
    snap = _canonical_snapshot()
    assert validate_reasoning_snapshot(snap) == snap
    truncated = _canonical_snapshot(
        text="思" * 3900 + TRUNCATION_MARKER,
        char_count=3900 + len(TRUNCATION_MARKER),
        truncated=True,
    )
    assert validate_reasoning_snapshot(truncated) == truncated


@pytest.mark.parametrize(
    "mutate",
    [
        lambda: None,
        lambda: "not-a-dict",
        lambda: 42,
        lambda: _canonical_snapshot(extra_key=1),  # extra key
        lambda: {k: v for k, v in _canonical_snapshot().items() if k != "truncated"},  # missing key
        lambda: _canonical_snapshot(projection_policy_version="v0"),  # wrong version
        lambda: _canonical_snapshot(projection_policy_version=None),
        lambda: _canonical_snapshot(text=""),  # empty text
        lambda: _canonical_snapshot(text=None),
        lambda: _canonical_snapshot(text=123),
        lambda: _canonical_snapshot(text="x" * (DEFAULT_PROJECTION_CHAR_CAP + 1),
                                    char_count=DEFAULT_PROJECTION_CHAR_CAP + 1),  # over cap
        lambda: _canonical_snapshot(char_count=3),  # count mismatch
        lambda: _canonical_snapshot(char_count="8"),
        lambda: _canonical_snapshot(char_count=True),  # bool is not an int count
        lambda: _canonical_snapshot(truncated="yes"),  # strict bool
        lambda: _canonical_snapshot(truncated=None),
        # Raw sentinels fail the re-projection byte-invariance check.
        lambda: _canonical_snapshot(text=f"see {_EVH_SENTINEL}",
                                    char_count=len(f"see {_EVH_SENTINEL}")),
        lambda: _canonical_snapshot(text=f"id {_UUID_SENTINEL}",
                                    char_count=len(f"id {_UUID_SENTINEL}")),
        lambda: _canonical_snapshot(text=f"k {_SK_SENTINEL}",
                                    char_count=len(f"k {_SK_SENTINEL}")),
        lambda: _canonical_snapshot(text=f"a {_BEARER_SENTINEL}",
                                    char_count=len(f"a {_BEARER_SENTINEL}")),
        lambda: _canonical_snapshot(text=f"s {_FP_SENTINEL}",
                                    char_count=len(f"s {_FP_SENTINEL}")),
        lambda: _canonical_snapshot(text=f"u {_URL_SENTINEL}",
                                    char_count=len(f"u {_URL_SENTINEL}")),
        lambda: _canonical_snapshot(text="You are Claread hidden",
                                    char_count=len("You are Claread hidden")),
        # --- R3 truncation-marker invariants ---
        # truncated=True but no marker.
        lambda: _canonical_snapshot(text="x" * 100, char_count=100, truncated=True),
        # truncated=False but a marker is present.
        lambda: _canonical_snapshot(text="abc" + TRUNCATION_MARKER,
                                    char_count=len("abc" + TRUNCATION_MARKER),
                                    truncated=False),
        # truncated=True but marker not at the end.
        lambda: _canonical_snapshot(text="abc" + TRUNCATION_MARKER + "xyz",
                                    char_count=len("abc" + TRUNCATION_MARKER + "xyz"),
                                    truncated=True),
        # marker duplicated.
        lambda: _canonical_snapshot(text="a" + TRUNCATION_MARKER + TRUNCATION_MARKER,
                                    char_count=len("a" + TRUNCATION_MARKER + TRUNCATION_MARKER),
                                    truncated=True),
    ],
)
def test_validate_reasoning_snapshot_rejects_invalid(mutate: object) -> None:
    assert validate_reasoning_snapshot(mutate()) is None  # type: ignore[operator]


def test_persistence_payload_is_always_canonical() -> None:
    # The write boundary validates: whatever persistence_payload returns
    # round-trips through the canonical validator unchanged.
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_reasoning_delta(f"检查 {_EVH_SENTINEL}，再想 {_UUID_SENTINEL}。")
    observer.on_analysis_finished()
    payload = observer.persistence_payload()
    assert payload is not None
    assert validate_reasoning_snapshot(payload) == payload
    assert _EVH_SENTINEL not in payload["text"]
    assert _UUID_SENTINEL not in payload["text"]


def test_snapshot_reprojection_is_byte_invariant() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    observer.on_reasoning_delta("思考〔引用〕保持。https 不是完整 URL。")
    observer.on_analysis_finished()
    payload = observer.persistence_payload()
    assert payload is not None
    assert redact_reasoning_text(payload["text"]) == payload["text"]


# ---------------------------------------------------------------------------
# ASK-TURN-LIFECYCLE R4-4: total cap is the ONLY quota
# ---------------------------------------------------------------------------
#
# R4-4 removed the hard round-0 sub-cap (former ROUND0_CAP_FRACTION = 0.65)
# because it silently dropped up to 35% of round-0 reasoning without setting
# ``truncated=True``, producing an undeclared gap in the visible projection.
# The turn-level total cap is the ONLY quota. ``advance_round()`` is a no-op
# retained for backward-compat with thinking_transport boundary calls.


def test_no_initial_round_subcap_allows_full_total_budget_in_initial_round() -> None:
    # R4-4: there is NO round-0 sub-cap. A round-0 feed under the total
    # content cap is accepted in full — no silent drop, no marker.
    cap = 100
    marker_len = len(TRUNCATION_MARKER)
    content_cap = cap - marker_len
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    # Feed an amount that would have been OVER the old round-0 sub-cap
    # (65% of content_cap) but UNDER the total content cap. R4-4: all
    # of it is accepted.
    feed_chars = content_cap  # fill the entire content reservation
    out_round0 = buffer.feed("字" * feed_chars) + buffer.flush()
    assert not buffer.truncated
    assert TRUNCATION_MARKER not in buffer.text
    assert buffer.char_count == content_cap
    assert buffer.text == "字" * content_cap
    assert out_round0 == "字" * content_cap


def test_advance_round_is_noop_does_not_affect_quota() -> None:
    # R4-4: advance_round() is a no-op. Calling it does not change the
    # buffer state or the available budget — the total cap is the only
    # quota and it is shared across all rounds.
    cap = 100
    marker_len = len(TRUNCATION_MARKER)
    content_cap = cap - marker_len
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    # Feed some content.
    first_batch = 20
    buffer.feed("字" * first_batch)
    buffer.flush()
    assert buffer.char_count == first_batch
    # Call advance_round — no effect on char_count or budget.
    buffer.advance_round()
    assert buffer.char_count == first_batch
    # Feed more — still under the total cap, accepted in full.
    second_batch = content_cap - first_batch
    out = buffer.feed("后" * second_batch) + buffer.flush()
    assert out == "后" * second_batch
    assert buffer.char_count == content_cap
    assert not buffer.truncated


def test_total_cap_truncates_with_marker_without_advance_round() -> None:
    # R4-4: total cap truncation works the same with or without
    # advance_round. The marker is appended exactly once at the end.
    cap = 100
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    # Overflow the total cap immediately — no advance_round needed.
    buffer.feed("字" * 200)
    buffer.flush()
    assert buffer.truncated
    assert buffer.text.endswith(TRUNCATION_MARKER)
    assert buffer.text.count(TRUNCATION_MARKER) == 1
    assert buffer.char_count == cap  # exactly the total cap


def test_advance_round_idempotent_after_total_truncation() -> None:
    cap = 50
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    # Hit total cap immediately in round 0.
    buffer.feed("字" * 200)
    buffer.flush()
    assert buffer.truncated
    text_before = buffer.text
    char_count_before = buffer.char_count
    # advance_round is a no-op once truncated.
    buffer.advance_round()
    assert buffer.text == text_before
    assert buffer.char_count == char_count_before
    # Further feed is also a no-op.
    assert buffer.feed("更多内容") == ""
    assert buffer.flush() == ""


def test_multiple_advance_round_calls_are_all_noops() -> None:
    # R4-4: multiple advance_round calls (multiple tool boundaries) are
    # all no-ops. The total cap is the only quota — no per-round reserve.
    cap = 200
    marker_len = len(TRUNCATION_MARKER)
    content_cap = cap - marker_len
    buffer = ReasoningProjectionBuffer(char_cap=cap)
    # Round 0: consume some budget.
    round0_chars = 40
    buffer.feed("零" * round0_chars)
    buffer.flush()
    # First tool boundary — no-op.
    buffer.advance_round()
    # Round 1: consume some budget.
    round1_chars = 30
    buffer.feed("一" * round1_chars)
    buffer.flush()
    # Second tool boundary — no-op.
    buffer.advance_round()
    # Round 2: consume the rest of the budget.
    remaining = content_cap - round0_chars - round1_chars
    buffer.feed("二" * remaining)
    buffer.flush()
    assert not buffer.truncated
    assert buffer.char_count == content_cap
    # One more char triggers total truncation.
    buffer.feed("三")
    buffer.flush()
    assert buffer.truncated
    assert buffer.text.endswith(TRUNCATION_MARKER)


def test_observer_advance_round_is_noop() -> None:
    # R4-4: observer.advance_round() is a no-op — it does not affect
    # which reasoning is accepted or dropped. All reasoning under the
    # total cap is accepted regardless of round boundaries.
    events: list[RuntimeEvent] = []
    cap = 100
    observer = ReasoningProjectorObserver(
        emit=lambda e: events.append(e),
        message_id="msg-1",
        thread_id="thr-1",
        turn_run_id="run-1",
        char_cap=cap,
    )
    # Feed reasoning that would have been dropped by the old round-0
    # sub-cap. R4-4: all of it is accepted.
    observer.on_reasoning_delta("字" * 50)
    observer.on_analysis_finished()
    # Tool boundary — no-op.
    observer.advance_round()
    # More reasoning — accepted (total still under cap).
    observer.on_reasoning_delta("后round1内容应该被接受")
    observer.on_analysis_finished()
    # Verify all content made it into the projection.
    deltas = [e for e in events if isinstance(e, AgenticReasoningDeltaEvent)]
    visible = "".join(d.delta for d in deltas)
    assert "字" * 50 in visible
    assert "后round1内容应该被接受" in visible


def test_observer_advance_round_is_noop_when_sealed() -> None:
    events: list[RuntimeEvent] = []
    observer = _observer(events)
    # Build the completed event — this seals the observer.
    observer.on_reasoning_delta("一些推理内容。")
    observer.on_analysis_finished()
    completed = observer.build_completed_event()
    assert completed is not None
    # advance_round after sealing is a no-op (no exception, no effect).
    observer.advance_round()
    # Further deltas are dropped.
    observer.on_reasoning_delta("sealed后不应有任何输出")
    assert observer.projection_text == "一些推理内容。"


def test_default_band_keeps_long_turns_untruncated() -> None:
    # R4-4 regression: with the round-0 sub-cap removed, a representative
    # 6K round-0 reasoning + 4K round-1 reasoning (10K total) must NOT be
    # truncated under the 14K default cap.
    buffer = ReasoningProjectionBuffer()  # uses DEFAULT_PROJECTION_CHAR_CAP
    round0_chars = 6_000
    round1_chars = 4_000
    # Round 0: 6K — accepted in full (no sub-cap to drop it).
    buffer.feed("字" * round0_chars)
    buffer.flush()
    assert not buffer.truncated
    # Tool boundary — no-op.
    buffer.advance_round()
    # Round 1: 4K — total now 10K, still under 14K - marker_len.
    buffer.feed("字" * round1_chars)
    buffer.flush()
    assert not buffer.truncated
    assert TRUNCATION_MARKER not in buffer.text
    assert buffer.char_count == round0_chars + round1_chars
