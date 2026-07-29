"""R4-A5-1 / A5-1R unit tests: ModelVisibleTurnBudget + ModelViewRenderer.

Scope: foundation modules only. No agent loop, no real LLM, no RAG I/O.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping
from uuid import UUID
from xml.sax.saxutils import escape as xml_escape

import pytest

from app.services.reader_record_ask.article_rag_port import FakeArticleRagSearchPort
from app.services.reader_record_ask.context_envelope import (
    EnvelopeInitialAnchor,
    VerifiedEnvelopeInput,
    build_context_envelope,
)
from app.services.reader_record_ask.model_view_budget import (
    ACCOUNT_RESERVES,
    MODEL_VISIBLE_TURN_PAYLOAD_CAP,
    RESERVE_BASELINE,
    RESERVE_CONTROL,
    RESERVE_EXPAND,
    RESERVE_MAP,
    RESERVE_RAG,
    RESERVE_REQUEST_FRAME,
    RESERVE_SELECTION,
    BudgetChargeDenied,
    BudgetChargeOk,
    ModelViewBudgetError,
    ModelViewRenderer,
    ModelViewSerializationError,
    ModelVisibleTurnBudget,
    RenderedModelView,
    RequestFrameParts,
)
from app.services.reader_record_ask.turn_capability_projection import (
    build_turn_capability_projection,
    resolve_can_search_article,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_USER = UUID("11111111-1111-1111-1111-111111111111")
_RECORD = UUID("22222222-2222-2222-2222-222222222222")
_BASE = UUID("33333333-3333-3333-3333-333333333333")
_DOC = UUID("44444444-4444-4444-4444-444444444444")
_BASE_SHA = "b" * 64
_HANDLE = "evh_" + ("ab" * 16)

_FORBIDDEN_SUBSTRINGS = (
    "selected_text",
    "selection_preview",
    "snippet",
    "unit_id",
    "anchor_segment_id",
    "segment_id",
    "score",
    "chunk_id",
    "text_hash",
    "content_sha256",
    "reading_record_id",
    "base_id",
    "stable_document_id",
    "user_id",
    "envelope_fingerprint",
    "article_rag_ready",
    "initial_selection_locator",
)


def _renderer() -> ModelViewRenderer:
    return ModelViewRenderer()


def _chars(n: int) -> object:
    """Render exactly n chars via the public metering entry."""
    return _renderer().render_plain("x" * n)


# ---------------------------------------------------------------------------
# Budget: seven accounts + hard caps
# ---------------------------------------------------------------------------


def test_account_reserves_sum_to_96k_cap() -> None:
    assert sum(ACCOUNT_RESERVES.values()) == MODEL_VISIBLE_TURN_PAYLOAD_CAP
    assert MODEL_VISIBLE_TURN_PAYLOAD_CAP == 96_000
    assert ACCOUNT_RESERVES == {
        "request_frame": RESERVE_REQUEST_FRAME,
        "selection": RESERVE_SELECTION,
        "baseline": RESERVE_BASELINE,
        "map": RESERVE_MAP,
        "expand": RESERVE_EXPAND,
        "rag": RESERVE_RAG,
        "control": RESERVE_CONTROL,
    }
    assert RESERVE_REQUEST_FRAME == 16_000
    assert RESERVE_SELECTION == 6_000
    assert RESERVE_BASELINE == 14_000
    assert RESERVE_MAP == 6_000
    assert RESERVE_EXPAND == 30_000
    assert RESERVE_RAG == 20_000
    assert RESERVE_CONTROL == 4_000


def test_per_account_hard_cap_denies_without_mutating() -> None:
    budget = ModelVisibleTurnBudget()
    ok = budget.try_charge("selection", _chars(RESERVE_SELECTION))
    assert isinstance(ok, BudgetChargeOk)
    assert budget.spent("selection") == RESERVE_SELECTION

    denied = budget.try_charge("selection", _chars(1))
    assert isinstance(denied, BudgetChargeDenied)
    assert denied.reason == "account_exhausted"
    assert budget.spent("selection") == RESERVE_SELECTION  # unchanged


def test_each_account_denies_without_mutation() -> None:
    renderer = _renderer()
    for account, reserve in ACCOUNT_RESERVES.items():
        budget = ModelVisibleTurnBudget()
        filled = renderer.render_plain("y" * reserve)
        ok = budget.try_charge(account, filled)
        assert isinstance(ok, BudgetChargeOk)
        assert budget.spent(account) == reserve
        denied = budget.try_charge(account, renderer.render_plain("z"))
        assert isinstance(denied, BudgetChargeDenied)
        assert denied.reason == "account_exhausted"
        assert denied.account == account
        assert budget.spent(account) == reserve
        assert budget.total_spent() == reserve


def test_total_cap_denies_even_when_account_has_room() -> None:
    budget = ModelVisibleTurnBudget()
    # Spend every account to its reserve except leave 1 char on request_frame.
    for account, reserve in ACCOUNT_RESERVES.items():
        if account == "request_frame":
            budget.charge(account, _chars(reserve - 1))
        else:
            budget.charge(account, _chars(reserve))
    assert budget.total_remaining() == 1
    # request_frame still has 1 char room, but charging 2 would exceed total.
    denied = budget.try_charge("request_frame", _chars(2))
    assert isinstance(denied, BudgetChargeDenied)
    assert denied.reason in ("account_exhausted", "total_exhausted")
    assert budget.total_remaining() == 1  # no mutation


def test_charge_raises_typed_budget_error_not_model_retry() -> None:
    budget = ModelVisibleTurnBudget()
    budget.charge("map", _chars(RESERVE_MAP))
    with pytest.raises(ModelViewBudgetError) as exc_info:
        budget.charge("map", _chars(1))
    denial = exc_info.value.denial
    assert denial.account == "map"
    assert denial.reason == "account_exhausted"
    # Guard: no pydantic_ai / ModelRetry import on the budget enforcement path.
    import app.services.reader_record_ask.model_view_budget as mod

    assert not hasattr(mod, "ModelRetry")
    source = open(mod.__file__, encoding="utf-8").read()
    assert "from pydantic_ai" not in source
    assert "import pydantic_ai" not in source
    assert "exceptions.ModelRetry" not in source


def test_public_charge_rejects_bare_int() -> None:
    budget = ModelVisibleTurnBudget()
    with pytest.raises(TypeError) as exc_info:
        budget.charge("map", 10)  # type: ignore[arg-type]
    msg = str(exc_info.value)
    assert msg == (
        "budget charge requires RenderedModelView from ModelViewRenderer"
    )

    with pytest.raises(TypeError):
        budget.try_charge("baseline", RESERVE_BASELINE)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        budget.can_charge("selection", 1)  # type: ignore[arg-type]
    assert budget.total_spent() == 0

    # Private raw-char path exists but is not the public seam.
    assert hasattr(budget, "_try_charge_chars")
    assert callable(budget._try_charge_chars)


# ---------------------------------------------------------------------------
# request_frame includes user question; never truncates it
# ---------------------------------------------------------------------------


def test_user_question_is_counted_in_request_frame_cost() -> None:
    renderer = _renderer()
    base_parts = RequestFrameParts(
        system_instructions="SYS",
        user_question="",
        projection_json='{"can_search_article":false}',
        handles_block="",
        coverage_block="",
    )
    base = renderer.render_request_frame(base_parts)

    long_q = "Q" * 200
    with_q = renderer.render_request_frame(
        RequestFrameParts(
            system_instructions="SYS",
            user_question=long_q,
            projection_json='{"can_search_article":false}',
        )
    )
    # Question appears fully and contributes its length to the cost.
    assert long_q in with_q.text
    assert with_q.char_cost == len(with_q.text)
    assert with_q.char_cost - base.char_cost == len(long_q)


def test_oversized_user_question_denies_without_truncation() -> None:
    renderer = _renderer()
    budget = ModelVisibleTurnBudget()
    # Force request_frame over reserve via a huge question.
    huge_q = "用户问题" * (RESERVE_REQUEST_FRAME)  # multi-byte chars, full cost
    parts = RequestFrameParts(
        system_instructions="S",
        user_question=huge_q,
        projection_json="{}",
    )
    rendered = renderer.render_request_frame(parts)
    assert huge_q in rendered.text  # never truncated at render time
    assert rendered.char_cost > RESERVE_REQUEST_FRAME

    denied = budget.try_charge("request_frame", rendered)
    assert isinstance(denied, BudgetChargeDenied)
    assert budget.spent("request_frame") == 0


def test_charge_request_frame_helper_raises_typed_error() -> None:
    renderer = _renderer()
    budget = ModelVisibleTurnBudget()
    huge_q = "x" * (RESERVE_REQUEST_FRAME + 500)
    with pytest.raises(ModelViewBudgetError) as exc_info:
        renderer.charge_request_frame(
            budget,
            RequestFrameParts(
                system_instructions="sys",
                user_question=huge_q,
                projection_json="{}",
            ),
        )
    assert exc_info.value.denial.account == "request_frame"
    assert budget.spent("request_frame") == 0


# ---------------------------------------------------------------------------
# Renderer determinism + XML serialized-cost basics
# ---------------------------------------------------------------------------


def test_renderer_deterministic_for_same_inputs() -> None:
    renderer = _renderer()
    payload = {"b": 2, "a": 1, "nested": {"z": True, "m": "中文"}}
    a = renderer.render_json(payload)
    b = renderer.render_json(payload)
    assert a.text == b.text
    assert a.char_cost == b.char_cost
    # Sorted keys / compact separators.
    assert a.text == json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    block_a = renderer.render_untrusted_article_text(
        handle_id=_HANDLE, ordinal=0, role="selection", text="a & b < c"
    )
    block_b = renderer.render_untrusted_article_text(
        handle_id=_HANDLE, ordinal=0, role="selection", text="a & b < c"
    )
    assert block_a.text == block_b.text
    assert block_a.char_cost == block_b.char_cost


def test_serialized_cost_ampersand_and_angle_brackets() -> None:
    renderer = _renderer()
    raw = 'a & b <c> "quote"'
    rendered = renderer.render_untrusted_article_text(
        handle_id=_HANDLE,
        ordinal=0,
        role="selection",
        text=raw,
    )
    # Content must be XML-escaped inside the block.
    assert "&amp;" in rendered.text
    assert "&lt;" in rendered.text
    assert "&gt;" in rendered.text
    assert "a & b" not in rendered.text  # raw ampersand must not leak unescaped
    # Cost is full serialized length, not raw text length.
    assert rendered.char_cost == len(rendered.text)
    assert rendered.char_cost > len(raw)
    # Recompute escape cost independently.
    expected_body = xml_escape(raw)
    assert expected_body in rendered.text
    assert f'handle="{_HANDLE}"' in rendered.text
    assert 'role="selection"' in rendered.text
    assert 'ordinal="0"' in rendered.text


def test_tool_view_cost_is_canonical_json_not_xml() -> None:
    renderer = _renderer()
    view = {"status": "ok", "summary": "found & more", "handles": [_HANDLE]}
    rendered = renderer.render_tool_view(view)
    assert rendered.text.startswith("{")
    assert "<untrusted" not in rendered.text
    assert rendered.char_cost == len(
        json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


# ---------------------------------------------------------------------------
# A5-1R: fail-closed JSON + sanitized errors
# ---------------------------------------------------------------------------


class _CustomObj:
    def __repr__(self) -> str:
        return "LEAKED_CUSTOM_REPR_SECRET_VALUE"


def test_render_json_rejects_uuid_without_leaking_value() -> None:
    renderer = _renderer()
    secret = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    with pytest.raises(ModelViewSerializationError) as exc_info:
        renderer.render_json({"id": secret})
    err = exc_info.value
    assert err.code == "non_json_native"
    msg = str(err)
    assert msg == "model_view_serialization_error code=non_json_native"
    assert str(secret) not in msg
    assert "aaaaaaaa" not in msg
    assert "UUID" not in msg


def test_render_json_rejects_custom_object_without_leaking_repr() -> None:
    renderer = _renderer()
    with pytest.raises(ModelViewSerializationError) as exc_info:
        renderer.render_json({"obj": _CustomObj()})
    err = exc_info.value
    assert err.code == "non_json_native"
    msg = str(err)
    assert "LEAKED_CUSTOM_REPR_SECRET_VALUE" not in msg
    assert "CustomObj" not in msg
    assert msg == "model_view_serialization_error code=non_json_native"


@pytest.mark.parametrize(
    "bad_float",
    [float("nan"), float("inf"), float("-inf")],
)
def test_render_json_rejects_non_finite_float_without_leaking(bad_float: float) -> None:
    renderer = _renderer()
    with pytest.raises(ModelViewSerializationError) as exc_info:
        renderer.render_json({"n": bad_float})
    err = exc_info.value
    assert err.code == "non_finite_float"
    msg = str(err)
    assert msg == "model_view_serialization_error code=non_finite_float"
    assert "nan" not in msg.lower()
    assert "inf" not in msg.lower()
    # math.isnan/isinf would confirm input, but error must stay sanitized.
    assert math.isfinite(0.0)


def test_render_tool_view_same_fail_closed_path() -> None:
    renderer = _renderer()
    with pytest.raises(ModelViewSerializationError) as exc_info:
        renderer.render_tool_view({"hit_id": _DOC})
    assert exc_info.value.code == "non_json_native"
    assert str(_DOC) not in str(exc_info.value)


def test_json_native_payload_stable_sort_and_metering() -> None:
    renderer = _renderer()
    payload = {
        "z": [1, True, None, 1.5, "中文"],
        "a": {"m": False, "b": 0},
    }
    expected = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    rendered = renderer.render_json(payload)
    assert rendered.text == expected
    assert rendered.char_cost == len(expected)
    # tool_view shares the path.
    tool = renderer.render_tool_view(payload)
    assert tool.text == expected
    assert tool.char_cost == rendered.char_cost


def test_no_default_str_in_json_dumps_path() -> None:
    import app.services.reader_record_ask.model_view_budget as mod

    source = open(mod.__file__, encoding="utf-8").read()
    # Guard the dumps call site — no custom default callable.
    assert re.search(r"json\.dumps\([^)]*default\s*=", source) is None
    # UUID still rejected (proves no silent str coercion).
    with pytest.raises(ModelViewSerializationError):
        ModelViewRenderer().render_json({"id": _USER})


class _ExplodingMapping(Mapping[str, object]):
    """Mapping whose items() raises with a probe string (sanitization target)."""

    _PROBE = "PROBE_EXPLODING_ITEMS_SECRET_VALUE_9f3c"

    def __init__(self) -> None:
        self._data = {"ok_key": 1}

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def items(self):  # type: ignore[override]
        raise RuntimeError(self._PROBE)


def test_render_json_sanitizes_exploding_mapping_items() -> None:
    """Hostile Mapping.items() must not leak RuntimeError / probe text."""
    renderer = _renderer()
    exploding = _ExplodingMapping()
    with pytest.raises(ModelViewSerializationError) as exc_info:
        renderer.render_json(exploding)
    err = exc_info.value
    assert isinstance(err, ModelViewSerializationError)
    assert err.code == "non_json_native"
    msg = str(err)
    assert msg == "model_view_serialization_error code=non_json_native"
    assert _ExplodingMapping._PROBE not in msg
    assert "RuntimeError" not in msg
    assert "PROBE_" not in msg
    assert "Exploding" not in msg
    # ``from None``: no explicit cause; context suppressed in traceback.
    assert err.__cause__ is None
    assert err.__suppress_context__ is True


def test_render_json_rejects_non_string_key_sanitized() -> None:
    renderer = _renderer()
    with pytest.raises(ModelViewSerializationError) as exc_info:
        renderer.render_json({1: "bad"})  # type: ignore[dict-item]
    err = exc_info.value
    assert err.code == "non_string_key"
    assert str(err) == "model_view_serialization_error code=non_string_key"


def test_hand_constructed_rendered_view_cannot_charge() -> None:
    """Hand-built or forged RenderedModelView must fail before mutation."""
    budget = ModelVisibleTurnBudget()
    forged = RenderedModelView(text="hello", char_cost=5)
    # Brand must not appear in public repr.
    assert "_origin" not in repr(forged)
    assert "RENDERER" not in repr(forged).upper()

    with pytest.raises(TypeError) as can_exc:
        budget.can_charge("selection", forged)
    with pytest.raises(TypeError) as try_exc:
        budget.try_charge("selection", forged)
    with pytest.raises(TypeError) as charge_exc:
        budget.charge("selection", forged)

    for exc in (can_exc, try_exc, charge_exc):
        assert str(exc.value) == (
            "budget charge requires RenderedModelView from ModelViewRenderer"
        )
        # Brand token / private field name must not leak into the error.
        assert "_origin" not in str(exc.value)
        assert "RENDERER_ORIGIN" not in str(exc.value)

    assert budget.total_spent() == 0
    assert budget.spent("selection") == 0

    # Forging a non-brand origin object still fails; budget unchanged.
    object.__setattr__(forged, "_origin", object())
    with pytest.raises(TypeError):
        budget.charge("baseline", forged)
    assert budget.total_spent() == 0


def test_renderer_outputs_remain_chargeable_across_surfaces() -> None:
    """plain / json / tool / untrusted / request-frame all mint chargeable views."""
    renderer = _renderer()
    budget = ModelVisibleTurnBudget()
    surfaces = [
        renderer.render_plain("plain-ok"),
        renderer.render_json({"a": 1, "b": "x"}),
        renderer.render_tool_view({"status": "ok", "n": 0}),
        renderer.render_untrusted_article_text(
            handle_id=_HANDLE,
            ordinal=0,
            role="selection",
            text="a & b",
        ),
        renderer.render_request_frame(
            RequestFrameParts(
                system_instructions="S",
                user_question="Q",
                projection_json="{}",
            )
        ),
    ]
    total = 0
    for view in surfaces:
        assert "_origin" not in repr(view)
        ok = budget.charge("baseline", view)
        assert isinstance(ok, BudgetChargeOk)
        total += view.char_cost
    assert budget.spent("baseline") == total
    assert budget.total_spent() == total


# ---------------------------------------------------------------------------
# TurnCapabilityProjection: port-derived can_search
# ---------------------------------------------------------------------------


def test_port_none_can_search_false_and_zero_port_calls() -> None:
    # Use a real fake only to prove we do not call it when port is None.
    spy = FakeArticleRagSearchPort()
    assert spy.call_count == 0

    projection = build_turn_capability_projection(
        article_rag_port=None,
        stable_document_id=_DOC,
        product_search_enabled=True,
        baseline_injected=True,
    )
    assert projection.can_search_article is False
    assert spy.call_count == 0  # never touched

    # Even with a live port instance available but not passed, decision is False.
    assert (
        resolve_can_search_article(
            article_rag_port=None,
            stable_document_id=_DOC,
            product_search_enabled=True,
        )
        is False
    )
    assert spy.call_count == 0


def test_fake_port_with_identity_and_product_flag_true_zero_io() -> None:
    port = FakeArticleRagSearchPort()
    projection = build_turn_capability_projection(
        article_rag_port=port,
        stable_document_id=_DOC,
        product_search_enabled=True,
        baseline_injected=True,
        baseline_complete=True,
    )
    assert projection.can_search_article is True
    assert port.call_count == 0  # resolve must not call search_current_article


def test_missing_identity_or_product_flag_disables_search() -> None:
    port = FakeArticleRagSearchPort()
    assert (
        resolve_can_search_article(
            article_rag_port=port,
            stable_document_id=None,
            product_search_enabled=True,
        )
        is False
    )
    assert (
        resolve_can_search_article(
            article_rag_port=port,
            stable_document_id=_DOC,
            product_search_enabled=False,
        )
        is False
    )
    assert port.call_count == 0


def test_envelope_article_rag_ready_does_not_affect_projection() -> None:
    """Old envelope flag must not be read or copied into can_search_article."""
    # Envelope claims ready=True but we pass port=None → still false.
    envelope_ready = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_BASE_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            article_rag_ready=True,
            can_search_current_article=True,
        )
    )
    assert envelope_ready.capabilities.article_rag_ready is True

    projection = build_turn_capability_projection(
        article_rag_port=None,
        stable_document_id=envelope_ready.stable_document_id,
        product_search_enabled=True,
        baseline_injected=True,
    )
    assert projection.can_search_article is False

    # Envelope claims ready=False but we pass a real port → true.
    envelope_not_ready = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_BASE_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            article_rag_ready=False,
            can_search_current_article=False,
        )
    )
    assert envelope_not_ready.capabilities.article_rag_ready is False
    port = FakeArticleRagSearchPort()
    projection2 = build_turn_capability_projection(
        article_rag_port=port,
        stable_document_id=envelope_not_ready.stable_document_id,
        product_search_enabled=True,
        baseline_injected=True,
    )
    assert projection2.can_search_article is True
    assert port.call_count == 0

    # Implementation must not import or read envelope capability flags.
    import app.services.reader_record_ask.turn_capability_projection as proj_mod

    assert not hasattr(proj_mod, "ReadingRecordAskContextEnvelope")
    assert not hasattr(proj_mod, "EnvelopeCapabilityState")
    source = open(proj_mod.__file__, encoding="utf-8").read()
    assert "from app.services.reader_record_ask.context_envelope" not in source
    assert "import context_envelope" not in source


def test_projection_has_no_body_uuid_hash_score_or_raw_locator() -> None:
    port = FakeArticleRagSearchPort()
    # Selection present with metadata only — still no body text fields.
    projection = build_turn_capability_projection(
        article_rag_port=port,
        stable_document_id=_DOC,
        product_search_enabled=True,
        baseline_injected=True,
        baseline_complete=False,
        has_visible_range=True,
        selection_present=True,
        selection_handle_id=_HANDLE,
        selection_expandable=True,
        selection_visible_char_count=12,
        selection_full_char_count=4000,
        article_map_present=True,
        article_map_entry_count=3,
        article_map_truncated=True,
        turn_id="turn_deadbeef",
    )
    payload = projection.to_model_dict()
    # Meter via renderer (JSON-native path) — no regression on sensitive fields.
    rendered = _renderer().render_json(payload)
    blob = rendered.text

    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in blob, f"forbidden key leaked: {forbidden}"

    # UUIDs of record/base/user/doc must not appear.
    for uuid_val in (_USER, _RECORD, _BASE, _DOC):
        assert str(uuid_val) not in blob

    # No 64-char hex hashes.
    assert re.search(r"[0-9a-f]{64}", blob) is None

    # Selection metadata present without body / locator.
    assert payload["selection"]["present"] is True
    assert payload["selection"]["handle_id"] == _HANDLE
    assert "selected_text" not in payload["selection"]
    assert "unit_id" not in payload["selection"]

    # Map metadata only.
    assert payload["article_map"] == {
        "present": True,
        "entry_count": 3,
        "truncated": True,
    }

    # turn_id is server-minted opaque string, not a UUID of record identity.
    assert payload["turn_id"] == "turn_deadbeef"
    assert payload["can_search_article"] is True


def test_projection_turn_id_minted_when_omitted() -> None:
    p1 = build_turn_capability_projection(
        article_rag_port=None,
        stable_document_id=None,
        product_search_enabled=False,
        baseline_injected=False,
    )
    p2 = build_turn_capability_projection(
        article_rag_port=None,
        stable_document_id=None,
        product_search_enabled=False,
        baseline_injected=False,
    )
    assert p1.turn_id.startswith("turn_")
    assert p2.turn_id.startswith("turn_")
    assert p1.turn_id != p2.turn_id


def test_projection_json_via_renderer_is_deterministic() -> None:
    renderer = _renderer()
    projection = build_turn_capability_projection(
        article_rag_port=None,
        stable_document_id=None,
        product_search_enabled=False,
        baseline_injected=True,
        turn_id="turn_fixed",
    )
    a = renderer.render_json(projection.to_model_dict())
    b = renderer.render_json(projection.to_model_dict())
    assert a.text == b.text
    assert a.char_cost == b.char_cost


def test_request_frame_with_projection_charges_request_frame_only() -> None:
    renderer = _renderer()
    budget = ModelVisibleTurnBudget()
    projection = build_turn_capability_projection(
        article_rag_port=None,
        stable_document_id=None,
        product_search_enabled=False,
        baseline_injected=True,
        turn_id="turn_fixed",
    )
    parts = RequestFrameParts(
        system_instructions="You are Claread.",
        user_question="文章主旨是什么？",
        projection_json=renderer.render_json(projection.to_model_dict()).text,
        handles_block="## handles\nevh_test\n",
        coverage_block="## Baseline coverage\nStatus: complete.\n",
    )
    rendered, ok = renderer.charge_request_frame(budget, parts)
    assert isinstance(ok, BudgetChargeOk)
    assert ok.account == "request_frame"
    assert budget.spent("request_frame") == rendered.char_cost
    assert budget.spent("selection") == 0
    assert budget.spent("baseline") == 0
    assert "文章主旨是什么？" in rendered.text
    assert "turn_fixed" in rendered.text


def test_old_envelope_projection_still_has_preview_unchanged_by_a5_1() -> None:
    """A5-1 must not switch runtime selection preview; old path intact."""
    envelope = build_context_envelope(
        VerifiedEnvelopeInput(
            user_id=_USER,
            reading_record_id=_RECORD,
            base_id=_BASE,
            record_generation=1,
            stable_document_id=_DOC,
            base_content_sha256=_BASE_SHA,
            product_state="readable_enhancing",
            readiness_state="article_ready",
            initial_anchor=EnvelopeInitialAnchor(
                unit_id="u1",
                anchor_segment_id="s1",
                start_offset=0,
                end_offset=5,
                selected_text="hello",
                text_hash="a1b2c3d4",
            ),
            article_rag_ready=False,
        )
    )
    old = envelope.to_agent_projection()
    assert old.selection_preview == "hello"
    assert old.initial_selection_locator is not None
    assert old.initial_selection_locator.unit_id == "u1"
