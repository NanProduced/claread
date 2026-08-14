"""Grammar layer payload validator (layer_published extended payload).

Grammar First-Publish Layer Event Payload

为 grammar_note 首发的 ``layer_published`` 事件提供专用 payload validator。
不复用 ``representation_event_payload.py``（那是 projection_ops / record_state_changed
专用），仅在 grammar_note writer 调用，且在原 publish transaction 内、event 写入前
执行。失败时抛出 ValueError，调用方的事务会 rollback，layer INSERT、event INSERT、
sequence 增量全部回滚。

参考设计: ``docs/architecture/reader-orchestration.md``
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# 硬限制（fail-closed；不静默截断）
# ---------------------------------------------------------------------------

MAX_PAYLOAD_BYTES = 16 * 1024  # 16 KB 序列化 JSON
MAX_INSERTIONS = 64
MAX_ITEM_IDS_PER_INSERTION = 64
MAX_KEY_LENGTH = 256

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# operation 白名单：rev2 收窄，仅允许 insert_after_anchor
ALLOWED_OPERATIONS: frozenset[str] = frozenset({"insert_after_anchor"})

# insertions[].kind 白名单：P2b 只处理 grammar_note；sentence_analysis 留给
ALLOWED_INSERTION_KINDS: frozenset[str] = frozenset({"grammar_note"})

# 顶层字段白名单：
#   - 7 个基础字段（所有 writer 一致）
#   - 3 个扩展字段（schema_version / operation / insertions）
#   - 3 个 window publisher 兼容字段（source / plan_id / window_id）
ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        # 7 个基础字段
        "record_id",
        "base_id",
        "layer_id",
        "layer_type",
        "target_scope",
        "target_key",
        "generation",
        # 3 个扩展字段
        "schema_version",
        "operation",
        "insertions",
        # 3 个 window 兼容字段
        "source",
        "plan_id",
        "window_id",
    }
)

# ---------------------------------------------------------------------------
# Forbidden payload keys — 脱敏 fail-closed
# ---------------------------------------------------------------------------

# 禁止用户内容、raw output、正文、认证、note、selected_text 或完整 layer output 等
# forbidden key（exact match，case-insensitive）
FORBIDDEN_EXACT_KEYS: frozenset[str] = frozenset(
    {
        "raw_output",
        "output_json",
        "note",
        "selected_text",
        "grammar_point",
        "pattern",
        "spans",
        "text",
        "content",
        "body",
        "auth",
        "token",
        "password",
        "secret",
        "api_key",
        "credentials",
    }
)

# 子串匹配（case-insensitive）——任何 key 含有这些子串都拒绝
FORBIDDEN_KEY_SUBSTRINGS: tuple[str, ...] = (
    "note",
    "text",
    "content",
    "body",
    "auth",
    "token",
    "secret",
    "password",
    "credential",
)

# 明确允许的 key 例外（尽管含 "key" 或 "_id"，但属于稳定 identity 字段）
# 这些 key 即使匹配 FORBIDDEN_EXACT_KEYS / FORBIDDEN_KEY_SUBSTRINGS 也允许
_KEY_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "target_key",
        "record_id",
        "base_id",
        "layer_id",
        "anchor_segment_id",
        "item_ids",
    }
)


def validate_grammar_layer_published_payload(payload: dict[str, Any]) -> None:
    """Validate grammar_note layer_published extended payload.

    在 grammar_note writer 的 publish transaction 内、event 写入前调用。
    失败时抛出 ValueError，调用方的事务会 rollback。

    Raises:
        ValueError: on any validation failure.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")

    # === 校验顺序（spec 1-10） ===

    # 1. 基础字段一致性：record_id / base_id / layer_id / layer_type /
    #    target_scope / target_key / generation
    record_id = payload.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise ValueError(f"record_id must be non-empty str, got {record_id!r}")

    base_id = payload.get("base_id")
    if not isinstance(base_id, str) or not base_id:
        raise ValueError(f"base_id must be non-empty str, got {base_id!r}")

    layer_id = payload.get("layer_id")
    if not isinstance(layer_id, str) or not layer_id:
        raise ValueError(f"layer_id must be non-empty str, got {layer_id!r}")

    layer_type = payload.get("layer_type")
    if layer_type != "grammar_note":
        raise ValueError(
            f"layer_type must be 'grammar_note', got {layer_type!r}"
        )

    target_scope = payload.get("target_scope")
    if target_scope != "unit":
        raise ValueError(f"target_scope must be 'unit', got {target_scope!r}")

    target_key = payload.get("target_key")
    if not isinstance(target_key, str) or not target_key:
        raise ValueError(f"target_key must be non-empty str, got {target_key!r}")

    generation = payload.get("generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        raise ValueError(f"generation must be int > 0, got {generation!r}")

    # 3. schema_version（如出现必须为 1）
    if "schema_version" in payload:
        if payload["schema_version"] != 1:
            raise ValueError(
                f"schema_version must be 1, got {payload['schema_version']!r}"
            )

    # 2. operation：如出现必须在 allowlist；若为 insert_after_anchor，
    #    insertions 必须存在且非空
    if "operation" in payload:
        operation = payload["operation"]
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError(
                f"operation {operation!r} not in allowlist "
                f"{sorted(ALLOWED_OPERATIONS)}"
            )
        if "insertions" not in payload:
            raise ValueError(
                "operation is insert_after_anchor but insertions is missing"
            )
        insertions_raw = payload["insertions"]
        if not isinstance(insertions_raw, list) or len(insertions_raw) == 0:
            raise ValueError(
                "operation is insert_after_anchor but insertions is empty"
            )

    # 4. insertions 数量校验（1-64 descriptors）
    if "insertions" in payload:
        insertions = payload["insertions"]
        if not isinstance(insertions, list):
            raise ValueError(
                f"insertions must be a list, got {type(insertions).__name__}"
            )
        if len(insertions) < 1 or len(insertions) > MAX_INSERTIONS:
            raise ValueError(
                f"insertions count {len(insertions)} out of range "
                f"[1, {MAX_INSERTIONS}]"
            )

        # 5-8. 每个 descriptor 校验（含 duplicate anchor / item_id / consistency）
        seen_anchors: set[str] = set()
        seen_item_ids: set[str] = set()
        for idx, desc in enumerate(insertions):
            _validate_descriptor(
                desc,
                idx,
                target_key=target_key,
                layer_id=layer_id,
                layer_type=layer_type,
                seen_anchors=seen_anchors,
                seen_item_ids=seen_item_ids,
            )

    # 9. Forbidden keys 脱敏（递归检查顶层 + 嵌套 dict / list）
    _check_forbidden_keys_recursive(payload, path="payload")

    # 顶层白名单校验（任何不在 allowlist 的 key → reject）
    extra_keys = set(payload.keys()) - ALLOWED_TOP_LEVEL_KEYS
    if extra_keys:
        raise ValueError(
            f"unexpected top-level keys: {sorted(extra_keys)}"
        )

    # 10. 序列化 payload 不超过 16KB
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"payload size {len(serialized)} bytes exceeds limit "
            f"{MAX_PAYLOAD_BYTES}"
        )


def _validate_descriptor(
    desc: Any,
    idx: int,
    *,
    target_key: str,
    layer_id: str,
    layer_type: str,
    seen_anchors: set[str],
    seen_item_ids: set[str],
) -> None:
    """校验单个 insertion descriptor（spec check #5-8）。"""
    if not isinstance(desc, Mapping):
        raise ValueError(
            f"insertions[{idx}] must be a mapping, got {type(desc).__name__}"
        )

    # unit_id：非空 str，必须等于 payload target_key
    unit_id = desc.get("unit_id")
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError(
            f"insertions[{idx}].unit_id must be non-empty str, got {unit_id!r}"
        )
    if unit_id != target_key:
        raise ValueError(
            f"insertions[{idx}].unit_id {unit_id!r} != payload target_key "
            f"{target_key!r}"
        )

    # anchor_segment_id：非空 str，max 256 chars
    anchor_segment_id = desc.get("anchor_segment_id")
    if not isinstance(anchor_segment_id, str) or not anchor_segment_id:
        raise ValueError(
            f"insertions[{idx}].anchor_segment_id must be non-empty str, "
            f"got {anchor_segment_id!r}"
        )
    if len(anchor_segment_id) > MAX_KEY_LENGTH:
        raise ValueError(
            f"insertions[{idx}].anchor_segment_id length "
            f"{len(anchor_segment_id)} exceeds limit {MAX_KEY_LENGTH}"
        )

    # 6. 无重复 anchor（insertions[].anchor_segment_id 唯一）
    if anchor_segment_id in seen_anchors:
        raise ValueError(
            f"duplicate anchor_segment_id {anchor_segment_id!r} "
            f"in insertions[{idx}]"
        )
    seen_anchors.add(anchor_segment_id)

    # kind：必须在 allowlist，且必须等于 payload layer_type
    kind = desc.get("kind")
    if kind not in ALLOWED_INSERTION_KINDS:
        raise ValueError(
            f"insertions[{idx}].kind {kind!r} not in allowlist "
            f"{sorted(ALLOWED_INSERTION_KINDS)}"
        )
    if kind != layer_type:
        raise ValueError(
            f"insertions[{idx}].kind {kind!r} != payload layer_type "
            f"{layer_type!r}"
        )

    # layer_id：非空 str，必须等于 payload layer_id
    desc_layer_id = desc.get("layer_id")
    if not isinstance(desc_layer_id, str) or not desc_layer_id:
        raise ValueError(
            f"insertions[{idx}].layer_id must be non-empty str, "
            f"got {desc_layer_id!r}"
        )
    if desc_layer_id != layer_id:
        raise ValueError(
            f"insertions[{idx}].layer_id {desc_layer_id!r} != payload layer_id "
            f"{layer_id!r}"
        )

    # item_ids：list of 1-64 items，每项非空 str max 256 chars
    item_ids = desc.get("item_ids")
    if not isinstance(item_ids, list):
        raise ValueError(
            f"insertions[{idx}].item_ids must be a list, "
            f"got {type(item_ids).__name__}"
        )
    if len(item_ids) < 1 or len(item_ids) > MAX_ITEM_IDS_PER_INSERTION:
        raise ValueError(
            f"insertions[{idx}].item_ids count {len(item_ids)} out of range "
            f"[1, {MAX_ITEM_IDS_PER_INSERTION}]"
        )
    for item_id in item_ids:
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(
                f"insertions[{idx}].item_ids each must be non-empty str, "
                f"got {item_id!r}"
            )
        if len(item_id) > MAX_KEY_LENGTH:
            raise ValueError(
                f"insertions[{idx}].item_ids length {len(item_id)} "
                f"exceeds limit {MAX_KEY_LENGTH}"
            )
        # 7. 无重复 item ID（跨所有 descriptor 的 item_ids 唯一）
        if item_id in seen_item_ids:
            raise ValueError(
                f"duplicate item_id {item_id!r} in insertions[{idx}]"
            )
        seen_item_ids.add(item_id)


def _check_forbidden_keys_recursive(value: Any, *, path: str) -> None:
    """递归检查 dict 中所有 key 是否为 forbidden（spec check #9）。

    递归进入嵌套 dict 和 list 中的 dict，确保所有 key 都通过脱敏检查。
    """
    if isinstance(value, Mapping):
        for key in value.keys():
            if not isinstance(key, str):
                raise ValueError(f"{path} has non-str key {key!r}")
            # 例外：明确允许的 identity 字段（如 target_key、record_id 等）
            if key in _KEY_EXCEPTIONS:
                continue
            key_lower = key.lower()
            # exact match
            if key_lower in FORBIDDEN_EXACT_KEYS:
                raise ValueError(f"forbidden key {key!r} in {path}")
            # substring match
            for sub in FORBIDDEN_KEY_SUBSTRINGS:
                if sub in key_lower:
                    raise ValueError(
                        f"forbidden key {key!r} (contains {sub!r}) in {path}"
                    )
        # 递归检查嵌套 dict 和 list
        for key, nested in value.items():
            if isinstance(nested, (Mapping, list)):
                _check_forbidden_keys_recursive(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, (Mapping, list)):
                _check_forbidden_keys_recursive(item, path=f"{path}[{idx}]")
