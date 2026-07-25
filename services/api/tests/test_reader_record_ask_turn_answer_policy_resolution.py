"""Contract tests for the pure turn answer policy resolver (P2C-A)."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json

import pytest

from app.services.reader_record_ask.turn_answer_policy import TurnAnswerPolicy
from app.services.reader_record_ask.turn_answer_policy_resolution import (
    TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
    RequestedAnswerConstraints,
    ServerAnswerCapabilities,
    TurnAnswerPolicySnapshot,
    parse_requested_answer_constraints,
    resolve_turn_answer_policy,
)


def _ordinary_assertions(policy: TurnAnswerPolicy, *, web_capability: str) -> None:
    assert policy.article_only is False
    assert policy.citation_required is False
    assert policy.requested_citation_scope == "none"
    assert policy.web_capability == web_capability
    assert policy.host_drafting_decision().kind == "model_draft_allowed"


# 1. 缺省 constraints → ordinary
def test_missing_constraints_resolve_to_ordinary_policy() -> None:
    policy = resolve_turn_answer_policy(None, ServerAnswerCapabilities())

    _ordinary_assertions(policy, web_capability="unavailable")


def test_missing_constraints_preserve_current_server_capability() -> None:
    policy = resolve_turn_answer_policy(
        None, ServerAnswerCapabilities(web_capability="available")
    )

    _ordinary_assertions(policy, web_capability="available")


# 2. explicit ordinary → ordinary
def test_explicit_ordinary_constraints_resolve_to_ordinary_policy() -> None:
    policy = resolve_turn_answer_policy(
        RequestedAnswerConstraints(article_only=False, requested_citation_scope="none"),
        ServerAnswerCapabilities(),
    )

    _ordinary_assertions(policy, web_capability="unavailable")


def test_empty_payload_resolves_to_ordinary_policy() -> None:
    constraints = parse_requested_answer_constraints({})
    policy = resolve_turn_answer_policy(constraints, ServerAnswerCapabilities())

    _ordinary_assertions(policy, web_capability="unavailable")


# 3. article_only=true + scope=none：仅依据本文，但不强制展示来源
def test_article_only_without_citation_scope() -> None:
    policy = resolve_turn_answer_policy(
        RequestedAnswerConstraints(article_only=True, requested_citation_scope="none"),
        ServerAnswerCapabilities(),
    )

    assert policy.article_only is True
    assert policy.citation_required is False
    assert policy.requested_citation_scope == "none"
    assert policy.host_drafting_decision().kind == "model_draft_allowed"


# 4. article_only=false + scope=article：要求本文出处，可有独立 general 补充
def test_article_citation_scope_without_article_only() -> None:
    policy = resolve_turn_answer_policy(
        RequestedAnswerConstraints(
            article_only=False, requested_citation_scope="article"
        ),
        ServerAnswerCapabilities(),
    )

    assert policy.article_only is False
    assert policy.citation_required is True
    assert policy.requested_citation_scope == "article"
    assert policy.host_drafting_decision().kind == "model_draft_allowed"


# 5. article_only=true + scope=article：全部 article，并要求出处
def test_article_only_with_article_citation_scope() -> None:
    policy = resolve_turn_answer_policy(
        RequestedAnswerConstraints(article_only=True, requested_citation_scope="article"),
        ServerAnswerCapabilities(),
    )

    assert policy.article_only is True
    assert policy.citation_required is True
    assert policy.requested_citation_scope == "article"
    assert policy.host_drafting_decision().kind == "model_draft_allowed"


# 6. scope=web + capability unavailable → host-owned web_unavailable，不降级
def test_web_scope_with_unavailable_capability_is_constructible_and_host_owned() -> None:
    policy = resolve_turn_answer_policy(
        RequestedAnswerConstraints(article_only=False, requested_citation_scope="web"),
        ServerAnswerCapabilities(web_capability="unavailable"),
    )

    assert policy.citation_required is True
    assert policy.requested_citation_scope == "web"
    assert policy.host_drafting_decision().kind == "web_unavailable"


# 7. scope=web + capability available → v1 仍在 drafting 前拦截，不降级为 general
def test_web_scope_with_available_capability_is_host_blocked_not_downgraded() -> None:
    policy = resolve_turn_answer_policy(
        RequestedAnswerConstraints(article_only=False, requested_citation_scope="web"),
        ServerAnswerCapabilities(web_capability="available"),
    )

    assert policy.citation_required is True
    assert policy.requested_citation_scope == "web"
    assert policy.web_capability == "available"
    assert policy.host_drafting_decision().kind == "web_not_supported_in_v1"


# 8. article_only=true + scope=web → fail-closed
def test_article_only_with_web_scope_fails_closed() -> None:
    with pytest.raises(ValueError, match="article_only"):
        resolve_turn_answer_policy(
            RequestedAnswerConstraints(
                article_only=True, requested_citation_scope="web"
            ),
            ServerAnswerCapabilities(web_capability="available"),
        )


# 9. 非严格 bool（0、1、"true"）拒绝
@pytest.mark.parametrize("bad_bool", [0, 1, "true", "false"])
def test_constraints_reject_non_strict_bool(bad_bool: object) -> None:
    with pytest.raises(ValueError, match="article_only must be a bool"):
        RequestedAnswerConstraints(
            article_only=bad_bool,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("bad_bool", [0, 1, "true"])
def test_payload_parse_rejects_non_strict_bool(bad_bool: object) -> None:
    with pytest.raises(ValueError, match="article_only must be a bool"):
        parse_requested_answer_constraints({"article_only": bad_bool})


# 10. 非法/未知 scope 拒绝
@pytest.mark.parametrize("bad_scope", ["everything", "both", "", "NONE", 1, None])
def test_constraints_reject_illegal_scope(bad_scope: object) -> None:
    with pytest.raises(ValueError, match="requested_citation_scope"):
        RequestedAnswerConstraints(
            requested_citation_scope=bad_scope,  # type: ignore[arg-type]
        )


def test_payload_parse_rejects_illegal_scope() -> None:
    with pytest.raises(ValueError, match="requested_citation_scope"):
        parse_requested_answer_constraints({"requested_citation_scope": "everything"})


# 11. 客户端 payload 含 citation_required 拒绝
def test_client_payload_cannot_supply_citation_required() -> None:
    with pytest.raises(ValueError, match="citation_required"):
        parse_requested_answer_constraints({"citation_required": True})


def test_constraints_dataclass_has_no_citation_required_field() -> None:
    assert "citation_required" not in {
        field.name for field in dataclasses.fields(RequestedAnswerConstraints)
    }


# 12. 客户端 payload 含 web_capability 拒绝
def test_client_payload_cannot_supply_web_capability() -> None:
    with pytest.raises(ValueError, match="web_capability"):
        parse_requested_answer_constraints({"web_capability": "available"})


def test_constraints_dataclass_has_no_server_capability_fields() -> None:
    field_names = {field.name for field in dataclasses.fields(RequestedAnswerConstraints)}
    assert field_names == {"article_only", "requested_citation_scope"}


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "openai"},
        {"model": "gpt-x"},
        {"tool_availability": {"web": True}},
        {"search_backend": "serp"},
        {"article_only": False, "unknown_flag": True},
    ],
    ids=["provider", "model", "tool_availability", "search_backend", "unknown_flag"],
)
def test_client_payload_rejects_server_owned_and_unknown_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="non-client fields"):
        parse_requested_answer_constraints(payload)


def test_payload_must_be_mapping_or_none() -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse_requested_answer_constraints("article_only=true")  # type: ignore[arg-type]


def test_capabilities_reject_illegal_web_capability() -> None:
    with pytest.raises(ValueError, match="web_capability"):
        ServerAnswerCapabilities(web_capability="maybe")  # type: ignore[arg-type]


def test_resolver_returns_canonical_policy_instance() -> None:
    policy = resolve_turn_answer_policy(None, ServerAnswerCapabilities())

    assert type(policy) is TurnAnswerPolicy


# 13. snapshot round trip 字节级/字段级稳定
def test_snapshot_round_trip_is_byte_and_field_stable() -> None:
    policy = resolve_turn_answer_policy(
        RequestedAnswerConstraints(article_only=True, requested_citation_scope="article"),
        ServerAnswerCapabilities(web_capability="available"),
    )
    snapshot = TurnAnswerPolicySnapshot.from_policy(policy)

    text = snapshot.to_json()
    restored = TurnAnswerPolicySnapshot.from_json(text)

    assert restored == snapshot
    assert restored.to_json() == text
    assert restored.to_json_dict() == snapshot.to_json_dict()
    assert restored.to_policy() == policy


def test_snapshot_json_is_deterministic_across_construction_paths() -> None:
    from_mapping = TurnAnswerPolicySnapshot.from_json_dict(
        {
            "web_capability": "unavailable",
            "requested_citation_scope": "none",
            "article_only": False,
            "policy_version": TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
        }
    )
    from_default = TurnAnswerPolicySnapshot.from_policy(
        resolve_turn_answer_policy(None, ServerAnswerCapabilities())
    )

    assert from_mapping.to_json() == from_default.to_json()


# 14. snapshot 无敏感 sentinel
def test_snapshot_carries_no_sensitive_payload() -> None:
    snapshot = TurnAnswerPolicySnapshot.from_policy(
        resolve_turn_answer_policy(
            RequestedAnswerConstraints(
                article_only=True, requested_citation_scope="article"
            ),
            ServerAnswerCapabilities(web_capability="available"),
        )
    )
    text = snapshot.to_json()

    assert set(json.loads(text)) == {
        "policy_version",
        "article_only",
        "requested_citation_scope",
        "web_capability",
    }
    for sentinel in (
        "user",
        "record",
        "question",
        "message",
        "handle",
        "evidence",
        "envelope",
        "provider",
        "model",
        "citation_required",
    ):
        assert sentinel not in text


def test_snapshot_is_immutable() -> None:
    snapshot = TurnAnswerPolicySnapshot.from_policy(
        resolve_turn_answer_policy(None, ServerAnswerCapabilities())
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.article_only = True  # type: ignore[misc]


# 15. 未知 policy version 拒绝
def test_snapshot_rejects_unknown_policy_version() -> None:
    with pytest.raises(ValueError, match="version"):
        TurnAnswerPolicySnapshot.from_json(
            json.dumps(
                {
                    "policy_version": TURN_ANSWER_POLICY_SNAPSHOT_VERSION + 1,
                    "article_only": False,
                    "requested_citation_scope": "none",
                    "web_capability": "unavailable",
                }
            )
        )


@pytest.mark.parametrize("bad_version", [True, "1", 1.0])
def test_snapshot_rejects_non_int_policy_version(bad_version: object) -> None:
    with pytest.raises(ValueError, match="policy_version"):
        TurnAnswerPolicySnapshot.from_json_dict(
            {
                "policy_version": bad_version,
                "article_only": False,
                "requested_citation_scope": "none",
                "web_capability": "unavailable",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        # 字段缺失
        {
            "article_only": False,
            "requested_citation_scope": "none",
            "web_capability": "unavailable",
        },
        # 额外字段（含 citation_required）
        {
            "policy_version": TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
            "article_only": False,
            "requested_citation_scope": "none",
            "web_capability": "unavailable",
            "citation_required": False,
        },
        # 非严格 bool
        {
            "policy_version": TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
            "article_only": 1,
            "requested_citation_scope": "none",
            "web_capability": "unavailable",
        },
        # 非法 scope
        {
            "policy_version": TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
            "article_only": False,
            "requested_citation_scope": "both",
            "web_capability": "unavailable",
        },
        # 非法 capability
        {
            "policy_version": TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
            "article_only": False,
            "requested_citation_scope": "none",
            "web_capability": "maybe",
        },
    ],
    ids=[
        "missing_field",
        "extra_citation_required",
        "non_strict_bool",
        "illegal_scope",
        "illegal_capability",
    ],
)
def test_snapshot_parse_fails_closed(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TurnAnswerPolicySnapshot.from_json_dict(payload)


def test_snapshot_from_json_rejects_invalid_json_and_non_mapping() -> None:
    with pytest.raises(ValueError, match="JSON"):
        TurnAnswerPolicySnapshot.from_json("{not json")
    with pytest.raises(ValueError, match="mapping"):
        TurnAnswerPolicySnapshot.from_json("[1, 2, 3]")


def test_snapshot_rehydration_of_illegal_combination_fails_closed() -> None:
    snapshot = TurnAnswerPolicySnapshot(
        policy_version=TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
        article_only=True,
        requested_citation_scope="web",
        web_capability="available",
    )

    with pytest.raises(ValueError, match="article_only"):
        snapshot.to_policy()


# 16. snapshot rehydrate 不读取当前 capability
def test_snapshot_rehydration_takes_no_capability_input() -> None:
    signature = inspect.signature(TurnAnswerPolicySnapshot.to_policy)

    assert list(signature.parameters) == ["self"]


def test_snapshot_rehydration_replays_persisted_capability() -> None:
    snapshot = TurnAnswerPolicySnapshot(
        policy_version=TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
        article_only=False,
        requested_citation_scope="web",
        web_capability="available",
    )

    rehydrated = snapshot.to_policy()

    assert rehydrated.web_capability == "available"
    assert rehydrated.host_drafting_decision().kind == "web_not_supported_in_v1"


def test_snapshot_rehydration_rederives_citation_required() -> None:
    snapshot = TurnAnswerPolicySnapshot(
        policy_version=TURN_ANSWER_POLICY_SNAPSHOT_VERSION,
        article_only=False,
        requested_citation_scope="article",
        web_capability="unavailable",
    )

    assert snapshot.to_policy().citation_required is True


# 17. resolver 不读取 user_message
def test_resolver_signatures_have_no_user_message_parameter() -> None:
    for fn in (
        resolve_turn_answer_policy,
        parse_requested_answer_constraints,
    ):
        for name in inspect.signature(fn).parameters:
            assert "message" not in name
            assert "question" not in name


# 18. AST / reverse guard
def _resolver_module() -> object:
    import app.services.reader_record_ask.turn_answer_policy_resolution as module

    return module


def test_resolver_imports_stay_within_pure_contracts() -> None:
    module = _resolver_module()
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    allowed = {
        "app.services.reader_record_ask.turn_answer_policy",
        "json",
        "dataclasses",
        "typing",
        "collections.abc",
        "__future__",
    }
    for name in imported:
        assert name in allowed, f"unexpected import {name}"


def test_resolver_does_not_define_keyword_or_regex_routing() -> None:
    module = _resolver_module()
    source = inspect.getsource(module)  # type: ignore[arg-type]

    assert "user_message" not in source
    assert "re.compile" not in source
    assert "re.match" not in source
    assert "re.search" not in source


def test_resolver_does_not_reimplement_provenance_validation() -> None:
    import app.services.reader_record_ask.turn_answer_policy as canonical

    module = _resolver_module()

    assert not hasattr(module, "validate_answer_blocks")
    assert module.TurnAnswerPolicy is canonical.TurnAnswerPolicy  # type: ignore[attr-defined]
