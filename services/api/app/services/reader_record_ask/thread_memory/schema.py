"""Thread memory snapshot schema（Pydantic v2 镜像 §6）。

真相源永远是 ``reader_ask_messages`` + ``reader_ask_turn_runs(final_status='ok')``。
本对象（``ThreadMemorySnapshot``）是派生只读视图，可凭 canonical messages
完全重建（ §4.2(e)）；丢失不造成任何事实损失。

严格 Typed JSON，无自由文本真相位——每个事实必须携带来源指针（冻结决策 #1）。
模型风格对齐 ``services/api/app/services/reader_ask/model_options.py:49-67``
（``ConfigDict(extra='forbid', frozen=True)``）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 排除内容标记（ §6 Episode.excluded_content_markers）：声明本 episode
# 依法排除了哪些类别，供审计与评测核对，不含任何被排除内容本身。
ExcludedContentMarker = Literal[
    "reasoning",
    "raw_tool_payload",
    "failed_drafts",
    "secrets",
    "evh_handles",
]


class TurnRange(BaseModel):
    """Canonical turn 序号闭区间（ §6 序号定义 H4）。

    canonical turn 序号 = user message 在 thread 内按 ``created_at ASC`` 的
    1-based 序号；retry 不增加序号（同一 user message 的多次 retry 共享同一
    序号，因 retry 复用原 user message）。``start`` / ``end`` 均为闭区间端点。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=1)
    end: int = Field(ge=1)


class SourceBinding(BaseModel):
    """来源绑定（ §6 SourceBinding）。

    仅由 Host 从 ok turn 的 ``resolved_evidence_json`` 派生（审计 ）；
    compactor 绝对无权创建 binding——防捏造 provenance 的结构性保证。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str = Field(min_length=1)
    source_type: Literal["article", "web"]
    # article: stable_document 指针族；web: canonical_url（TS 草案）。
    # 注：H6 映射有损，web 的 source_id 由 Host 映射层决定（见 mapping.py）。
    # 允许空串：web binding 无 handle_id 时 source_id 可为空（H6 有损映射）。
    source_id: str
    fence_type: Literal["reading_record", "stable_document", "base", "generation"]
    # fence 指针族（任一变更 ⇒ 绑定失效，§8.1）。
    fence_values: dict[str, Any]
    validity_check: dict[str, Any]


class StructuredFact(BaseModel):
    """结构化事实（ §6 StructuredFact）。

    冻结决策 #1：历史事实必须来源绑定，禁止自由文本摘要成为事实 truth。
    ``text`` 仅为人类可读标签（≤280 字符），真相 = ``source_ids`` 指向的
    canonical 原文。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str = Field(min_length=1)
    # 事实文本，≤280 字符。
    text: str = Field(max_length=280)
    # prior_mention：fence 失效的 fact 降级为此类型（ §4.2d 步骤 7 §8.1），
    # 禁作 citation truth，仅渲染"此前讨论过"。
    source_type: Literal[
        "article",
        "web",
        "user_correction",
        "user_question",
        "assistant_answer",
        "prior_mention",
    ]
    # 允许引用的 ID 全集 = 本 thread 的 message IDs ∪ ok 消息公开 citation IDs
    # ∪ ok turn resolved_evidence binding IDs（§4.2d allowlist）。
    # ≥1（user_question 指回提问 message_id）。
    source_ids: list[str] = Field(min_length=1)
    # provenance 强度：article+valid binding=high；user 陈述=medium；web=prior_context。
    confidence: Literal["high", "medium", "prior_context"]
    # 产生该事实的 canonical turn 序号——防漂移的回溯锚点。
    turn_origin: int = Field(ge=1)
    # 仅 user_correction 使用：指向被本纠正取代的 fact_id 列表。
    # 被取代 fact 不删除（episode 不可变），仅在注入渲染时标注 superseded。
    supersedes: list[str] | None = None
    # protected facts 不参与 recency 淘汰（ §4.2f）：
    # user_correction 与 unresolved_question 标记为 protected，收缩时全保留。
    protected: bool = False


class Episode(BaseModel):
    """Episode（ §6 Episode）——append-only，旧 episode 永不改写。

    防漂移核心约束（§4.2(f)）：压缩只为新 turn range 追加新 episode；
    旧 episode 的 ``structured_facts`` 永不重写。预算收缩只做 fact 淘汰 /
    并集合并，不做散文再合成。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1)
    # canonical turn 序号闭区间（见 TurnRange）。
    turn_range: TurnRange
    structured_facts: list[StructuredFact]
    # 仅 Host 从 ok turn 的 resolved_evidence_json 派生（审计 ）。
    source_bindings: list[SourceBinding]
    excluded_content_markers: list[ExcludedContentMarker]
    # 'none' = 纯确定性 emergency（无 LLM 调用）。
    compaction_model: Literal["deepseek-v4-flash", "none"]
    compaction_method: Literal["model", "emergency_deterministic", "hybrid"]
    compaction_timestamp: str = Field(min_length=1)
    # 本次压缩输入所依据的 canonical 序列前缀水印（重建一致性校验用）。
    # 首次 emergency 压缩无前缀水印，允许空串。
    compaction_input_watermark: str = ""


class ThreadMemorySnapshot(BaseModel):
    """Thread memory 派生只读视图（ §6 ThreadMemorySnapshot）。

    真相源 = ``reader_ask_messages`` + ``reader_ask_turn_runs(final_status='ok')``。
    本对象可凭 canonical messages 完全重建（ §4.2(e)）；watermark 按
    message_id 维度，``final_status`` 取 supersedes 链上最新 ok run（H2/H3）。
    作用域：仅此 thread（冻结决策 #2），禁止跨 thread 引用。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Schema 版本。破坏性变更必须升版；validator 拒绝异版（fail-closed，
    # 仿 validate_reasoning_snapshot 模式）。
    version: Literal["thread_memory_v1"]
    # CAS 水印：SHA-256(确定性序列化的 [(message_id, role|final_status)…])，
    # 覆盖 snapshot 所依据的全部 canonical messages。注入前 CAS 校验；
    # 失配 ⇒ snapshot 过期 ⇒ 从 canonical 重建。防并发轮/retry 竞争。
    watermark: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)  # ISO-8601 UTC
    # 最近一次压缩完成时刻（UI "上下文已压缩" 依据）。
    # None = 尚未压缩过（fresh thread，无 aged episode）。
    last_compacted_at: str | None = None
    # 压缩前后规模（UI 低权重展示；冻结决策 #7：仅数字，不含内容）。
    last_compaction_stats: dict[str, Any] | None = None
    # Episode 链：append-only。
    episodes: list[Episode]
