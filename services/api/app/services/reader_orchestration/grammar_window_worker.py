"""GrammarWindowWorkerService: grammar-window grammar window worker.

Design source:
  docs/architecture/reader-orchestration.md
  - §8.2 Window claim / preflight (pending → running state transition)
  - §8.3 LLM call (skeleton; full prompt + PydanticAI wiring deferred to C5)
  - §8.6 Heartbeat (asyncio task renewing the lease every ~30s)

This worker consumes ``build_grammar_bundle_window`` reader_jobs. The
preflight step is the critical fix: it must transition ``analysis_windows``
from ``pending`` to ``running`` BEFORE the LLM call, otherwise the publish
phase (window_locked.status == 'running' fence) rejects the output.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Literal, Protocol
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_ai import Agent

from app.config.settings import Settings, get_settings
from app.contracts.annotation import (
    compute_text_range_hash,
    slice_by_utf16_offsets,
    utf16_code_unit_length,
)
from app.database import connection as db_connection
from app.llm.agent_runner import extract_run_usage, run_reader_scoped_agent
from app.llm.call_guard import assert_real_llm_allowed
from app.llm.router import build_model_for_route
from app.llm.routes import MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE
from app.schemas.reader_orchestration import (
    ReaderTextRangeAnchor,
)
from app.services.ai_usage import (
    BILLING_MODE_INTERNAL_ONLY,
    CAPABILITY_READER_GRAMMAR_BUNDLE,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_SYSTEM_INTERNAL,
)
from app.services.model_execution_journal import (
    CapturedReceipt,
    CaptureEnvelopeConflictError,
    ExecutionIdentity,
    PayloadContractError,
    decode_resume_payload,
    decode_usage_event_draft,
    prepare_capture_envelope,
)
from app.services.model_execution_journal.service import ModelExecutionJournalService
from app.services.prompting.prompt_loader import (
    get_prompt_version,
    load_agent_instructions,
)
from app.services.reader_orchestration.automatic_layer_policy import (
    SemanticFenceError,
    is_semantic_fence_failure_code,
    validate_automatic_job_semantic_fence,
)
from app.services.reader_orchestration.grammar_candidate_policy import (
    validate_dedup_hint,
)
from app.services.reader_orchestration.grammar_worker import (
    FAKE_GRAMMAR_MODEL_NAME,
    FAKE_GRAMMAR_MODEL_PROFILE,
    FAKE_GRAMMAR_MODEL_PROVIDER,
    FAKE_GRAMMAR_PROMPT_VERSION,
    GRAMMAR_PROMPT_AGENT_NAME,
    GRAMMAR_WORKFLOW_VERSION,
    MAX_GRAMMAR_DEDUP_HINT_LENGTH,
)
from app.services.reader_orchestration.job_runtime import (
    CapturedResumeClaim,
    ClaimResult,
    FenceViolationError,
    IllegalTransitionError,
    LeaseExpiredError,
    LeaseTokenMismatchError,
    ReaderJobRuntime,
)
from app.services.reader_orchestration.lease_heartbeat import LeaseHeartbeat
from app.services.reader_orchestration.reading_strategy import (
    ReaderStrategyResolverError,
    resolve_reader_variant_strategy,
)
from app.services.reader_orchestration.usage_attribution import (
    ReaderUsageAttributionService,
)
from app.services.reader_orchestration.window_selector import CandidateItem

logger = logging.getLogger(__name__)

# Strategy metadata keys written by grammar_window_bootstrap into reader_jobs.input_json
# (via _build_strategy_metadata). _load_window_context reads them back and
# cross-validates against the live resolver output. Fail-closed contract:
# missing metadata or hash mismatch never falls back to a default strategy.
_WINDOW_STRATEGY_INPUT_KEYS: tuple[str, ...] = (
    "reading_goal",
    "reading_variant",
    "strategy_version",
    "strategy_hash",
    "layer_policy_hash",
)
_WINDOW_GRAMMAR_LAYER_NAME = "grammar_bundle"
_WINDOW_STRATEGY_METADATA_MISSING_CODE = "strategy_metadata_missing"
_WINDOW_STRATEGY_HASH_MISMATCH_CODE = "strategy_hash_mismatch"
_WINDOW_LAYER_POLICY_HASH_MISMATCH_CODE = "layer_policy_hash_mismatch"
_WINDOW_STRATEGY_VERSION_MISMATCH_CODE = "strategy_version_mismatch"


def _resolve_window_strategy(input_data: Any) -> dict[str, Any]:
    """Read strategy metadata from window job ``input_json`` and validate.

    Mirrors ``grammar_worker._validate_grammar_strategy_metadata`` but
    raises :class:`GrammarWindowExecutionError` on failure so the window
    worker's error handling path applies. Fail-closed contract: missing
    metadata or hash mismatch never falls back to a default strategy.

    Returns a dict with ``reading_goal`` / ``reading_variant`` /
    ``strategy_version`` / ``strategy_hash`` / ``layer_policy_hash`` /
    ``grammar_prompt_lines``.
    """
    if not isinstance(input_data, Mapping):
        raise GrammarWindowExecutionError(
            "window job input_json is not a mapping; "
            "strategy metadata cannot be read",
            retryable=False,
            failure_class="validation",
            failure_code=_WINDOW_STRATEGY_METADATA_MISSING_CODE,
        )

    missing: list[str] = []
    for key in _WINDOW_STRATEGY_INPUT_KEYS:
        value = input_data.get(key)
        if not isinstance(value, str) or not value:
            missing.append(key)
    if missing:
        raise GrammarWindowExecutionError(
            "window job input_json is missing strategy metadata: "
            + ", ".join(missing),
            retryable=False,
            failure_class="validation",
            failure_code=_WINDOW_STRATEGY_METADATA_MISSING_CODE,
        )

    reading_goal = str(input_data["reading_goal"])
    reading_variant = str(input_data["reading_variant"])
    expected_strategy_version = str(input_data["strategy_version"])
    expected_strategy_hash = str(input_data["strategy_hash"])
    expected_layer_policy_hash = str(input_data["layer_policy_hash"])

    try:
        strategy = resolve_reader_variant_strategy(reading_goal, reading_variant)
    except ReaderStrategyResolverError as exc:
        raise GrammarWindowExecutionError(
            f"window strategy resolver rejected pair "
            f"({reading_goal!r}, {reading_variant!r}): {exc}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        ) from exc

    if strategy.strategy_version != expected_strategy_version:
        raise GrammarWindowExecutionError(
            f"window strategy_version mismatch: input_json has "
            f"{expected_strategy_version!r} but resolver produced "
            f"{strategy.strategy_version!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_WINDOW_STRATEGY_VERSION_MISMATCH_CODE,
        )

    if strategy.strategy_hash != expected_strategy_hash:
        raise GrammarWindowExecutionError(
            f"window strategy_hash mismatch: input_json has "
            f"{expected_strategy_hash!r} but resolver produced "
            f"{strategy.strategy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_WINDOW_STRATEGY_HASH_MISMATCH_CODE,
        )

    layer = strategy.layers.get(_WINDOW_GRAMMAR_LAYER_NAME)
    if layer is None:
        raise GrammarWindowExecutionError(
            f"resolved strategy has no layer {_WINDOW_GRAMMAR_LAYER_NAME!r}",
            retryable=False,
            failure_class="strategy_resolution",
            failure_code="strategy_resolver_error",
        )

    if layer.policy_hash != expected_layer_policy_hash:
        raise GrammarWindowExecutionError(
            f"window layer_policy_hash mismatch: input_json has "
            f"{expected_layer_policy_hash!r} but resolver produced "
            f"{layer.policy_hash!r}",
            retryable=False,
            failure_class="validation",
            failure_code=_WINDOW_LAYER_POLICY_HASH_MISMATCH_CODE,
        )

    return {
        "reading_goal": reading_goal,
        "reading_variant": reading_variant,
        "strategy_version": strategy.strategy_version,
        "strategy_hash": strategy.strategy_hash,
        "layer_policy_hash": layer.policy_hash,
        "grammar_prompt_lines": list(layer.prompt_lines),
    }


class PreflightResult(Enum):
    """Outcome of ``preflight_window_job`` (§8.2)."""

    PROCEED = "proceed"
    ALREADY_TERMINAL = "already_terminal"


# analysis_windows.status values that count as terminal per §8.2.
_TERMINAL_WINDOW_STATUSES: frozenset[str] = frozenset({
    "completed", "no_op", "failed",
})


class GrammarWindowExecutionError(RuntimeError):
    """Raised when the grammar window executor is not configured or fails.

    Mirrors :class:`GrammarExecutionError` so the pipeline runner can route
    retryable vs non-retryable failures to ``retry_later`` vs
    ``failed_terminal`` without guessing. Configuration / route /
    validation errors are non-retryable (code bug); provider / agent.run
    errors are retryable (transient).
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_class: str,
        failure_code: str,
        rationale_code: str | None = None,
        prompt_version: str | None = None,
        model_route: str = MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE,
        model_profile: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.rationale_code = rationale_code or failure_code
        self.prompt_version = prompt_version
        self.model_route = model_route
        self.model_profile = model_profile
        self.model_provider = model_provider
        self.model_name = model_name


@dataclass(frozen=True, slots=True)
class GrammarWindowExecutionResult:
    """Result of a successful window-scoped LLM call.

    Carries the candidate list plus the usage / model metadata needed by
    the pipeline runner to record ``ai_usage_events`` and end the
    ``worker_tick`` span with token / model fields (requirement 6).
    """

    candidates: list[CandidateItem]
    usage_data: dict[str, Any] | None = None
    prompt_version: str | None = FAKE_GRAMMAR_PROMPT_VERSION
    model_route: str = MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE
    model_profile: str | None = FAKE_GRAMMAR_MODEL_PROFILE
    model_provider: str | None = FAKE_GRAMMAR_MODEL_PROVIDER
    model_name: str | None = FAKE_GRAMMAR_MODEL_NAME


class GrammarWindowExecutorProtocol(Protocol):
    """Protocol for grammar-window grammar window LLM executors."""

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        """Generate candidates + usage/model metadata from a window context."""
        ...


class UnconfiguredGrammarWindowExecutor:
    """Default executor that raises when no real executor is configured."""

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        del context
        raise GrammarWindowExecutionError(
            "GrammarWindowWorkerService has no executor configured. "
            "Pass an executor= parameter to the constructor.",
            retryable=False,
            failure_class="configuration",
            failure_code="grammar_window_executor_unconfigured",
        )


# design §8.3 中 ``_GRAMMAR_LAYER_NAME`` 的字符串常量，用于从 resolver
# 取出 grammar_bundle layer 的 policy_hash + prompt_lines。
_GRAMMAR_LAYER_NAME = "grammar_bundle"


# ---------------------------------------------------------------------------
# §8.3 window-scoped LLM output schemas (single call covers all units)
# ---------------------------------------------------------------------------


class _WindowGrammarSpan(BaseModel):
    """LLM output: span within a target anchor (uses selected_text for reliability)."""

    model_config = ConfigDict(extra="forbid")

    anchor_segment_id: str = Field(min_length=1)
    selected_text: str = Field(min_length=1, max_length=240)


class _WindowGrammarNoteCandidate(BaseModel):
    """LLM output: window-scoped grammar_note candidate with self-rating."""

    model_config = ConfigDict(extra="forbid")

    item_type: Literal["grammar_note"] = "grammar_note"
    anchor_segment_id: str = Field(min_length=1)
    spans: list[_WindowGrammarSpan] = Field(min_length=1, max_length=4)
    grammar_point: str = Field(min_length=1, max_length=120)
    pattern: str | None = Field(default=None, max_length=120)
    note: str = Field(
        min_length=1,
        max_length=360,
        description=(
            "简体中文 Markdown string。允许 **加粗**、`inline code`、短无序列表；"
            "禁止 raw HTML 和 Markdown 标题（# / ## / ###）。前端会把 Markdown "
            "反序列化为 Plate children 渲染。"
        ),
    )
    # Self-rating: three required fields consumed by window selector.
    # See grammar_worker.GrammarNoteCandidateItem for the contract.
    quality_score: int = Field(ge=1, le=5)
    reading_blocker: bool
    dedup_hint: str = Field(min_length=1, max_length=MAX_GRAMMAR_DEDUP_HINT_LENGTH)

    @field_validator("dedup_hint")
    @classmethod
    def _validate_and_normalize_dedup_hint(cls, value: str) -> str:
        # reader-grammar-candidate-selection: trim + normalize + 非空/≤120
        # 校验在 schema boundary 完成，返回 normalized hint 供下游
        # scoped_dedup_key 直接使用（idempotent）。
        return validate_dedup_hint(value)


class _WindowSentenceChunk(BaseModel):
    """LLM output: sentence analysis chunk."""

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    label: str = Field(min_length=1)
    text: str = Field(min_length=1)


class _WindowSentenceAnalysisCandidate(BaseModel):
    """LLM output: window-scoped sentence_analysis candidate with self-rating."""

    model_config = ConfigDict(extra="forbid")

    item_type: Literal["sentence_analysis"] = "sentence_analysis"
    anchor_segment_id: str = Field(min_length=1)
    selected_text: str = Field(min_length=1, max_length=640)
    label: str = Field(min_length=1, max_length=120)
    analysis: str = Field(
        min_length=1,
        max_length=360,
        description=(
            "简体中文 Markdown string。允许 **加粗**、`inline code`、短无序列表；"
            "禁止 raw HTML 和 Markdown 标题（# / ## / ###）。讲解结构关系和阅读"
            "顺序，不要逐块复述 chunks。前端会把 Markdown 反序列化为 Plate "
            "children 渲染。"
        ),
    )
    chunks: list[_WindowSentenceChunk] = Field(min_length=1, max_length=8)
    # Self-rating: three required fields consumed by window selector.
    # See grammar_worker.SentenceAnalysisCandidateItem for the contract.
    quality_score: int = Field(ge=1, le=5)
    reading_blocker: bool
    dedup_hint: str = Field(min_length=1, max_length=MAX_GRAMMAR_DEDUP_HINT_LENGTH)

    @field_validator("dedup_hint")
    @classmethod
    def _validate_and_normalize_dedup_hint(cls, value: str) -> str:
        # reader-grammar-candidate-selection: trim + normalize + 非空/≤120
        # 校验在 schema boundary 完成，返回 normalized hint 供下游
        # scoped_dedup_key 直接使用（idempotent）。
        return validate_dedup_hint(value)


class _WindowGrammarCandidateOutput(BaseModel):
    """LLM output: window-scoped grammar analysis (single call covers all units)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    grammar_notes: list[_WindowGrammarNoteCandidate] = Field(default_factory=list)
    sentence_analyses: list[_WindowSentenceAnalysisCandidate] = Field(
        default_factory=list
    )


# Window-only operational rules (target/context fencing, budget, self-rating).
# Teaching semantics come from the shared agent instructions
# (reader_layer_grammar_bundle.yaml) so per-unit / batch / window stay aligned.
_WINDOW_GRAMMAR_OPERATIONAL_RULES = """\
你是 Claread Reader 的窗口级语法分析 agent。

你会在一次调用中收到一个阅读窗口内所有 target anchor（跨多个 unit），需要为其中最有价值的 anchor 产出 grammar_note 和 sentence_analysis candidate。

教学选点、grammar_note / sentence_analysis 职责、同点竞争、非模板化与 Markdown 合同、self-rating 字段合同，遵循下方「共享教学合同」（与 per-unit / batch 同源）。

## 窗口操作规则

1. 输出 anchor 约束：每个输出 item 的 anchor_segment_id 必须是 [TARGET] anchor 之一。禁止编造 anchor id。

2. 上下文 anchor：标记为 [CONTEXT_ONLY] 的 anchor 仅用于理解上下文。禁止对 context-only anchor 输出 item。

3. 预算约束：[WINDOW_BUDGET] 段指定了 grammar_note 和 sentence_analysis 的数量上限。超出预算是校验错误。无值得标注的内容时返回空数组是合法结果。

4. 空输出合法：如果没有任何 anchor 值得标注，返回空数组。这是成功结果，不是失败。

5. 质量优先于数量：只标注真正有理解/学习价值的点；跳过透明基础结构与低价值 anchor。不要为凑满预算而输出。

6. 同 unit span 约束：单条 grammar_note item 内所有 span 必须属于同一个 unit_id。跨 unit span 会被拒绝。

## 输出格式

返回符合 schema 的结构化输出。grammar_note 每个 span 的 selected_text 必须逐字复制自 target anchor 的原文；sentence_analysis 的 selected_text 必须逐字复制自 target anchor 的原文。
"""


def get_window_grammar_system_prompt() -> str:
    """Compose window operational rules + shared grammar teaching contract.

    Shared teaching text is loaded from ``reader_layer_grammar_bundle`` so
    window stays in lockstep with per-unit / batch agent instructions.
    """
    shared = load_agent_instructions(GRAMMAR_PROMPT_AGENT_NAME).strip()
    return (
        f"{_WINDOW_GRAMMAR_OPERATIONAL_RULES.strip()}\n\n"
        f"# 共享教学合同（与 per-unit / batch 同源）\n\n"
        f"{shared}\n"
    )


# Back-compat name used by existing tests; always reflects the composed prompt.
# Evaluated lazily on first access via module-level helper in tests should call
# get_window_grammar_system_prompt() for the full string.
_WINDOW_GRAMMAR_SYSTEM_PROMPT = _WINDOW_GRAMMAR_OPERATIONAL_RULES


def _find_unique_utf16_occurrences(
    haystack: str,
    needle: str,
) -> list[tuple[int, int]]:
    """Find all UTF-16 code unit offset pairs of ``needle`` in ``haystack``.

    Returns a list of (start_offset, end_offset) tuples in UTF-16 code units.
    Used by ``_ground_span`` to resolve LLM-produced ``selected_text`` to
    concrete offsets within an anchor's ``source_text``.
    """
    occurrences: list[tuple[int, int]] = []
    search_start = 0
    needle_length = len(needle)
    while True:
        index = haystack.find(needle, search_start)
        if index < 0:
            break
        start_offset = utf16_code_unit_length(haystack[:index])
        end_offset = start_offset + utf16_code_unit_length(needle)
        occurrences.append((start_offset, end_offset))
        search_start = index + max(1, needle_length)
    return occurrences


class PydanticAIGrammarWindowExecutor:
    """Window-scoped grammar analysis executor (§8.3 single-call design).

    实现 ``GrammarWindowExecutorProtocol.generate(context) -> list[CandidateItem]``。
    对整个 window 的所有 target anchor 发起 **一次** PydanticAI ``agent.run()``
    调用，产出 grammar_note / sentence_analysis candidate 并自带 self-rating。
    LLM 输出中的 ``selected_text`` 会被 ground 到具体的 UTF-16 offset +
    text_hash，转换为 ``CandidateItem``。

    设计目标（§8.3 / §6.2 / §6.3）：
      - 单次 LLM 调用覆盖 window 内所有 unit（BBC: 37 → 3-5 calls）
      - target / context anchor 分离（context 仅作理解，不可标注）
      - window budget 约束（grammar_note + sentence_analysis 上限）
      - self-rating 字段（quality_score / reading_blocker / dedup_hint）由
        LLM 直接产出，三条路径统一消费
      - 失败必须 raise（不吞掉），触发 reader_jobs → retry_later/failed_terminal

    context dict 结构（由 ``GrammarWindowWorkerService._load_window_context``
    返回）：
      - ``target_anchors``: list[dict]，每个 dict 含 anchor_segment_id /
        unit_id / unit_order_index / base_start_utf16 / base_end_utf16 /
        unit_base_start_utf16 / unit_base_end_utf16 / source_text
      - ``base_id`` / ``reading_record_id`` / ``job_id``: 用于构造
        GrammarJobContext 的 placeholder 字段（executor 不依赖 reader_jobs
        行，只读取 reading_records / reading_bases / reading_units /
        anchor_segments）
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._pool = pool
        self._settings = settings
        self._last_usage_data: dict[str, Any] | None = None

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    async def generate(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        """Window-scoped single LLM call covering all target anchors.

        构建包含所有 target anchor + context anchor 的 window prompt，发起
        **一次** PydanticAI ``agent.run()`` 调用，将 LLM 输出的
        ``selected_text`` ground 到 UTF-16 offset + text_hash，转换为
        ``CandidateItem`` 列表。LLM 失败时 raise（不吞掉），触发
        reader_jobs → retry_later/failed_terminal。

        Returns a :class:`GrammarWindowExecutionResult` carrying the
        candidate list plus ``usage_data`` / ``prompt_version`` /
        ``model_route`` / ``model_profile`` / ``model_provider`` /
        ``model_name`` so the pipeline runner can record
        ``ai_usage_events`` and end the ``worker_tick`` span with token
        + model fields (requirement 6).
        """
        target_anchors: list[dict[str, Any]] = list(
            context.get("target_anchors", [])
        )
        prompt_version = get_prompt_version()

        if not target_anchors:
            # No-op window: return an empty result with model metadata
            # so ai_usage_events can still be recorded for observability.
            return GrammarWindowExecutionResult(
                candidates=[],
                usage_data=None,
                prompt_version=prompt_version,
            )

        settings = self._settings or get_settings()
        if not str(settings.reader_grammar_bundle_model_profile or "").strip():
            raise GrammarWindowExecutionError(
                "grammar window executor is not configured; set "
                "reader_grammar_bundle_model_profile or inject settings "
                "with a configured model profile",
                retryable=False,
                failure_class="configuration",
                failure_code="grammar_window_executor_unconfigured",
                prompt_version=prompt_version,
            )

        model, model_config = build_model_for_route(
            settings,
            MODEL_ROUTE_READER_LAYER_GRAMMAR_BUNDLE,
        )
        if model is None:
            raise GrammarWindowExecutionError(
                "reader_layer_grammar_bundle model route is not configured",
                retryable=False,
                failure_class="configuration",
                failure_code="model_route_unavailable",
                prompt_version=prompt_version,
            )

        assert_real_llm_allowed(
            "app.services.reader_orchestration.grammar_window_worker."
            "PydanticAIGrammarWindowExecutor",
            model_config=model_config,
        )

        prompt = self._build_window_prompt(context)
        agent = self._build_window_agent(model=model)
        try:
            result = await self._run_agent(agent, prompt)
        except GrammarWindowExecutionError:
            raise
        except Exception as exc:
            raise GrammarWindowExecutionError(
                f"window grammar agent execution failed: {exc}",
                retryable=True,
                failure_class="provider",
                failure_code=type(exc).__name__,
                prompt_version=prompt_version,
                model_profile=(
                    str(model_config.profile_name)
                    if model_config is not None
                    else None
                ),
                model_provider=(
                    str(model_config.provider)
                    if model_config is not None
                    else None
                ),
                model_name=(
                    str(model_config.model_name)
                    if model_config is not None
                    else None
                ),
            ) from exc

        try:
            candidate_output = _WindowGrammarCandidateOutput.model_validate(
                result.output
            )
        except ValidationError as exc:
            raise GrammarWindowExecutionError(
                f"window grammar agent produced invalid structured output: {exc}",
                retryable=False,
                failure_class="validation",
                failure_code="model_output_invalid",
                prompt_version=prompt_version,
                model_profile=(
                    str(model_config.profile_name)
                    if model_config is not None
                    else None
                ),
                model_provider=(
                    str(model_config.provider)
                    if model_config is not None
                    else None
                ),
                model_name=(
                    str(model_config.model_name)
                    if model_config is not None
                    else None
                ),
            ) from exc

        usage_data = extract_run_usage(result)
        self._last_usage_data = usage_data

        candidates = self._ground_and_convert_candidates(
            candidate_output=candidate_output,
            context=context,
        )
        return GrammarWindowExecutionResult(
            candidates=candidates,
            usage_data=usage_data,
            prompt_version=prompt_version,
            model_profile=(
                str(model_config.profile_name)
                if model_config is not None
                else None
            ),
            model_provider=(
                str(model_config.provider)
                if model_config is not None
                else None
            ),
            model_name=(
                str(model_config.model_name)
                if model_config is not None
                else None
            ),
        )

    def _build_window_prompt(self, context: dict[str, Any]) -> str:
        """构建 window-scoped prompt，包含 target + context anchor。

        Target anchor 标记为 ``[TARGET]``（可标注），context anchor 标记为
        ``[CONTEXT_ONLY]``（仅用于理解上下文，不可标注）。Window budget
        约束写入 ``[WINDOW_BUDGET]`` 段。

        ``<reader_strategy>`` section 注入 variant-first policy lines
        (reading_goal / reading_variant / strategy_hash / layer_policy_hash
        / prompt_lines)，使 LLM 能按 variant 调整 grammar_note /
        sentence_analysis 的产出风格和密度。

        Budget key 从 ``max_grammar_notes`` / ``max_sentence_analyses``
        修正为 ``grammar_note.count`` / ``sentence_analysis.count``，与
        grammar_window_bootstrap 写入格式和 grammar_window_publisher 读取格式对齐。
        """
        target_anchors: list[dict[str, Any]] = list(
            context.get("target_anchors", [])
        )
        context_prev: list[dict[str, Any]] = list(
            context.get("context_anchor_prev", [])
        )
        context_next: list[dict[str, Any]] = list(
            context.get("context_anchor_next", [])
        )
        window_budget: dict[str, Any] = dict(
            context.get("window_budget", {})
        )
        # Grammar_window_bootstrap writes {"grammar_note": {"count": N},
        # "sentence_analysis": {"count": M}}. The old keys
        # "max_grammar_notes" / "max_sentence_analyses" never matched, so
        # the worker always fell back to 4/3 while the selector/publisher
        # enforced the real 2/1 cap — LLM output was silently truncated.
        grammar_note_budget = window_budget.get("grammar_note", {})
        sentence_analysis_budget = window_budget.get("sentence_analysis", {})
        max_grammar_notes = int(grammar_note_budget.get("count", 4))
        max_sentence_analyses = int(sentence_analysis_budget.get("count", 3))

        lines: list[str] = []
        lines.append("# READING WINDOW")
        lines.append("")
        lines.append("## [WINDOW_BUDGET]")
        lines.append(f"- max_grammar_notes: {max_grammar_notes}")
        lines.append(f"- max_sentence_analyses: {max_sentence_analyses}")
        lines.append("")

        # Inject variant strategy section so the LLM can vary output
        # by reading_goal / reading_variant. Mirrors the
        # <reader_strategy> section in grammar_worker._format_grammar_strategy_section.
        prompt_lines: list[str] = list(
            context.get("grammar_prompt_lines", [])
        )
        if prompt_lines:
            reading_goal = str(context.get("reading_goal", ""))
            reading_variant = str(context.get("reading_variant", ""))
            strategy_version = str(context.get("strategy_version", ""))
            strategy_hash = str(context.get("strategy_hash", ""))
            layer_policy_hash = str(context.get("layer_policy_hash", ""))
            lines.append("<reader_strategy>")
            lines.append(f"reading_goal: {reading_goal}")
            lines.append(f"reading_variant: {reading_variant}")
            lines.append(f"strategy_version: {strategy_version}")
            lines.append(f"strategy_hash: {strategy_hash}")
            lines.append(f"layer_policy_hash: {layer_policy_hash}")
            lines.append("<policy_lines>")
            for policy_line in prompt_lines:
                lines.append(f"- {policy_line}")
            lines.append("</policy_lines>")
            lines.append("</reader_strategy>")
            lines.append("")

        lines.append("## [TARGET] Anchors (you may annotate these)")
        lines.append("")
        for anchor in target_anchors:
            lines.append(
                f"### anchor_segment_id: {anchor['anchor_segment_id']}"
            )
            lines.append(f"- unit_id: {anchor['unit_id']}")
            lines.append(
                f"- unit_order_index: {anchor.get('unit_order_index', 0)}"
            )
            lines.append(f"- source_text: {anchor['source_text']}")
            lines.append("")

        if context_prev:
            lines.append(
                "## [CONTEXT_ONLY] Previous anchors (do NOT annotate)"
            )
            lines.append("")
            for anchor in context_prev:
                lines.append(
                    f"### anchor_segment_id: {anchor['anchor_segment_id']}"
                )
                lines.append(f"- unit_id: {anchor['unit_id']}")
                lines.append(f"- source_text: {anchor['source_text']}")
                lines.append("")

        if context_next:
            lines.append(
                "## [CONTEXT_ONLY] Next anchors (do NOT annotate)"
            )
            lines.append("")
            for anchor in context_next:
                lines.append(
                    f"### anchor_segment_id: {anchor['anchor_segment_id']}"
                )
                lines.append(f"- unit_id: {anchor['unit_id']}")
                lines.append(f"- source_text: {anchor['source_text']}")
                lines.append("")

        return "\n".join(lines)

    def _build_window_agent(self, *, model: Any) -> Agent:
        """构建 window-scoped PydanticAI Agent。

        System instructions = window operational rules + shared teaching
        contract from ``reader_layer_grammar_bundle.yaml`` (same source as
        per-unit / batch).
        """
        return Agent(
            model=model,
            output_type=_WindowGrammarCandidateOutput,
            instructions=get_window_grammar_system_prompt(),
            name="reader_layer_grammar_window_agent",
            retries={"tools": 1, "output": 2},
        )

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        """执行 Reader-scoped agent.run（可被 mock 替换用于测试）。"""
        return await run_reader_scoped_agent(agent, prompt)

    def _ground_and_convert_candidates(
        self,
        *,
        candidate_output: _WindowGrammarCandidateOutput,
        context: dict[str, Any],
    ) -> list[CandidateItem]:
        """将 LLM 输出 ground 到 UTF-16 offset + text_hash，转为 CandidateItem。

        对每个 grammar_note / sentence_analysis candidate：
        1. 校验 anchor_segment_id 在 target_anchors 中（拒绝 context-only）
        2. 将 selected_text ground 到 anchor source_text 中的 UTF-16 offset
        3. 构建 ReaderTextRangeAnchor（含 text_hash）
        4. 转换为 CandidateItem（携带 self-rating 字段）

        无法 ground 的 candidate 被跳过（不 raise，best-effort）。
        """
        target_anchors: list[dict[str, Any]] = list(
            context.get("target_anchors", [])
        )
        anchors_by_id: dict[str, dict[str, Any]] = {
            str(a["anchor_segment_id"]): a for a in target_anchors
        }

        candidates: list[CandidateItem] = []

        for note in candidate_output.grammar_notes:
            if note.anchor_segment_id not in anchors_by_id:
                continue
            spans: list[dict[str, Any]] = []
            for span in note.spans:
                span_anchor = anchors_by_id.get(span.anchor_segment_id)
                if span_anchor is None:
                    continue
                grounded = self._ground_span(
                    anchor=span_anchor,
                    selected_text=span.selected_text,
                    context=context,
                )
                if grounded is not None:
                    spans.append(grounded.model_dump())
            if not spans:
                continue
            dedup_key = self._compute_dedup_key(
                note.grammar_point, note.dedup_hint
            )
            candidates.append(
                CandidateItem(
                    item_type="grammar_note",
                    anchor_segment_id=note.anchor_segment_id,
                    spans=spans,
                    semantic_dedup_key=dedup_key,
                    pattern_key=note.pattern,
                    quality_score=int(note.quality_score),
                    reading_blocker=note.reading_blocker,
                    dedup_hint=note.dedup_hint,
                    grammar_point=note.grammar_point,
                    pattern=note.pattern,
                    note=note.note,
                )
            )

        for analysis in candidate_output.sentence_analyses:
            if analysis.anchor_segment_id not in anchors_by_id:
                continue
            anchor = anchors_by_id[analysis.anchor_segment_id]
            grounded = self._ground_span(
                anchor=anchor,
                selected_text=analysis.selected_text,
                context=context,
            )
            if grounded is None:
                continue
            dedup_key = self._compute_dedup_key(
                analysis.label, analysis.dedup_hint
            )
            chunks: list[dict[str, Any]] = [
                {
                    "order": ch.order,
                    "label": ch.label,
                    "text": ch.text,
                }
                for ch in analysis.chunks
            ]
            candidates.append(
                CandidateItem(
                    item_type="sentence_analysis",
                    anchor_segment_id=analysis.anchor_segment_id,
                    spans=[grounded.model_dump()],
                    semantic_dedup_key=dedup_key,
                    pattern_key=None,
                    quality_score=int(analysis.quality_score),
                    reading_blocker=analysis.reading_blocker,
                    dedup_hint=analysis.dedup_hint,
                    label=analysis.label,
                    analysis=analysis.analysis,
                    chunks=chunks,
                )
            )

        return candidates

    def _ground_span(
        self,
        *,
        anchor: dict[str, Any],
        selected_text: str,
        context: dict[str, Any],
    ) -> ReaderTextRangeAnchor | None:
        """将 LLM 产出的 selected_text ground 到 UTF-16 offset + text_hash。

        在 anchor 的 source_text 中查找 selected_text 的唯一出现位置，
        计算 unit-relative UTF-16 offset，构建 ReaderTextRangeAnchor。
        如果出现 0 次或 >1 次，返回 None（无法 ground）。
        """
        source_text = str(anchor.get("source_text", ""))
        if not source_text or not selected_text:
            return None

        occurrences = _find_unique_utf16_occurrences(source_text, selected_text)
        if len(occurrences) != 1:
            return None

        start_offset, end_offset = occurrences[0]
        # ReaderTextRangeAnchor offsets are unit-relative. Anchor metadata keeps
        # base-relative positions for slicing ``source_text``, so convert the
        # anchor start back into the unit coordinate system before grounding.
        anchor_unit_start = int(anchor.get("base_start_utf16", 0)) - int(
            anchor.get("unit_base_start_utf16", 0)
        )
        unit_start = anchor_unit_start + start_offset
        unit_end = anchor_unit_start + end_offset

        try:
            return ReaderTextRangeAnchor(
                base_id=str(context.get("base_id", "")),
                unit_id=str(anchor.get("unit_id", "")),
                anchor_segment_id=str(anchor["anchor_segment_id"]),
                sentence_id=str(anchor["anchor_segment_id"]),
                segment_type="sentence",
                start_offset=unit_start,
                end_offset=unit_end,
                selected_text=selected_text,
                text_hash=compute_text_range_hash(selected_text),
            )
        except Exception:
            return None

    @staticmethod
    def _compute_dedup_key(*parts: str) -> str:
        """从多个字符串字段计算稳定的 semantic dedup key。

        使用 sha1 前 16 字符作为 dedup key，足够区分不同 grammar_point /
        note 组合，避免长字符串污染 ledger。
        """
        raw = "|".join(parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class GrammarWindowWorkerService:
    """grammar-window grammar window worker.

    Responsibilities:
      1. ``preflight_window_job`` — §8.2 pending → running transition.
      2. ``_heartbeat_loop`` — §8.6 lease renewal during the LLM call.
      3. ``process_window_job`` — orchestrates preflight → context load →
         LLM (with heartbeat) → return candidates. The pipeline runner
         wires the publisher after ``candidates_ready`` is returned.

    The LLM call (``_call_llm``) delegates to an injected executor that
    implements ``GrammarWindowExecutorProtocol``. The default
    ``UnconfiguredGrammarWindowExecutor`` raises so a real executor must be
    passed for end-to-end processing. Context loading
    (``_load_window_context``) JOINs anchor_segments + reading_units +
    reading_bases to slice ``source_text`` for each target anchor.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool | None = None,
        job_runtime: ReaderJobRuntime | None = None,
        lease_duration: timedelta = timedelta(seconds=120),
        heartbeat_interval: timedelta = timedelta(seconds=30),
        executor: GrammarWindowExecutorProtocol | None = None,
        journal_service: ModelExecutionJournalService | None = None,
    ) -> None:
        self._pool = pool
        self._job_runtime = job_runtime or ReaderJobRuntime(pool=pool)
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._executor: GrammarWindowExecutorProtocol = (
            executor or UnconfiguredGrammarWindowExecutor()
        )
        self._journal_service = journal_service or ModelExecutionJournalService(
            pool=pool
        )

    def get_pool(self) -> asyncpg.Pool:
        pool = self._pool or db_connection.DB_POOL
        if pool is None:
            raise RuntimeError("Database pool not initialized")
        return pool

    # ------------------------------------------------------------------
    # §8.2 preflight: pending → running
    # ------------------------------------------------------------------

    async def preflight_window_job(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> PreflightResult:
        """Transition ``analysis_windows.status`` from ``pending`` to ``running``.

        Must run after ``claim_next_job`` and before the LLM call. The publish
        phase requires ``window.status == 'running'`` (window_locked fence),
        so skipping this step causes publish to fail.

        Branches (§8.2):
          - ``pending`` → UPDATE to ``running``, write ``started_at`` + ``job_id``.
          - ``running`` + same ``job_id`` → retry of the same job; allow.
          - ``running`` + different ``job_id`` → raise ``IllegalTransitionError``.
          - ``completed`` / ``no_op`` / ``failed`` → ``ALREADY_TERMINAL``.
          - any other status → raise ``IllegalTransitionError`` (defensive).

        ``lease_token`` is accepted for symmetry with the runtime contract but
        is not re-validated here; lease validity is enforced by ``heartbeat``
        and ``transition``.
        """
        del lease_token, lease_duration  # enforced upstream; not re-checked here

        async with self.get_pool().acquire() as conn:
            async with conn.transaction():
                # 1. Lock the reader_jobs row for fence context.
                job_row = await conn.fetchrow(
                    "SELECT * FROM reader_jobs WHERE id = $1 FOR UPDATE",
                    job_id,
                )
                if job_row is None:
                    raise LookupError(f"reader job {job_id} not found")

                # 2. Resolve window_id from input_json.
                input_json: Any = job_row["input_json"]
                if isinstance(input_json, str):
                    input_json = json.loads(input_json)
                window_id = UUID(str(input_json["window_id"]))

                # 3. Lock the analysis_windows row.
                window_row = await conn.fetchrow(
                    "SELECT * FROM analysis_windows WHERE id = $1 FOR UPDATE",
                    window_id,
                )
                if window_row is None:
                    raise LookupError(f"analysis window {window_id} not found")

                status: str = window_row["status"]

                # 4. Dispatch on §8.2 status branches.
                if status == "pending":
                    # analysis_windows has no updated_at column (only
                    # created_at / started_at / completed_at), so we only
                    # touch status / started_at / job_id here.
                    await conn.execute(
                        """
                        UPDATE analysis_windows
                        SET status = 'running',
                            started_at = NOW(),
                            job_id = $2
                        WHERE id = $1
                        """,
                        window_id, job_id,
                    )
                    return PreflightResult.PROCEED

                if status == "running":
                    stored_job_id = window_row["job_id"]
                    if stored_job_id != job_id:
                        raise IllegalTransitionError(
                            f"window {window_id} is running by job "
                            f"{stored_job_id}, current job is {job_id}"
                        )
                    return PreflightResult.PROCEED

                if status in _TERMINAL_WINDOW_STATUSES:
                    return PreflightResult.ALREADY_TERMINAL

                raise IllegalTransitionError(
                    f"unexpected analysis_window status {status!r} for "
                    f"window {window_id}"
                )

    # ------------------------------------------------------------------
    # process_window_job: preflight → LLM (with heartbeat) → publish
    # ------------------------------------------------------------------

    async def process_window_job(
        self,
        *,
        claim: ClaimResult,
    ) -> dict[str, Any]:
        """Run preflight and one journaled provider invocation (no publish)."""
        preflight = await self.preflight_window_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=self._lease_duration,
        )
        if preflight == PreflightResult.ALREADY_TERMINAL:
            return {"status": "already_terminal"}

        try:
            context = await self._load_window_context(claim.job_id)
        except GrammarWindowExecutionError as exc:
            if is_semantic_fence_failure_code(exc.failure_code):
                await self._mark_semantic_fence_superseded(claim, exc)
                return {
                    "status": "superseded",
                    "failure_code": exc.failure_code,
                    "executor_calls": 0,
                }
            raise

        identity = self._execution_identity(claim)
        try:
            begin = await self._journal_service.begin_execution(
                identity=identity,
                invocation_kind="reader.grammar_window",
            )
        except Exception as exc:
            await self._pause_model_execution_claim(
                claim,
                rationale_code="model_execution_begin_unconfirmed",
                failure_code="journal_begin_failed",
                failure_message=str(exc),
            )
            return {"status": "paused"}
        if not begin.provider_call_allowed:
            captured = begin.capture_state == "captured"
            await self._pause_model_execution_claim(
                claim,
                rationale_code=(
                    "model_execution_captured_resume_required"
                    if captured
                    else "model_execution_ambiguous"
                ),
                failure_code=(
                    "post_provider_resume_required"
                    if captured
                    else "provider_outcome_ambiguous"
                ),
            )
            return {"status": "paused"}

        heartbeat = LeaseHeartbeat(
            job_runtime=self._job_runtime,
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=self._lease_duration,
            heartbeat_interval=self._heartbeat_interval,
        )
        await heartbeat.start()
        try:
            execution = await self._call_llm(context)
            try:
                event_id = await self._capture_execution(
                    identity=identity,
                    claim=claim,
                    context=context,
                    execution=execution,
                )
            except CaptureEnvelopeConflictError as exc:
                await self._pause_model_execution_claim(
                    claim,
                    rationale_code="model_execution_capture_conflict",
                    failure_code="capture_envelope_conflict",
                    failure_message=str(exc),
                )
                return {"status": "paused"}
            except Exception as exc:
                await self._pause_model_execution_claim(
                    claim,
                    rationale_code="model_execution_ambiguous",
                    failure_code="provider_outcome_ambiguous",
                    failure_message=str(exc),
                )
                return {"status": "paused"}
            try:
                await heartbeat.verify_ownership()
            except (
                FenceViolationError,
                IllegalTransitionError,
                LeaseExpiredError,
                LeaseTokenMismatchError,
                LookupError,
            ):
                return {"status": "retry_later"}
        finally:
            await heartbeat.stop()
        return self._candidates_ready_result(execution, event_id)

    async def process_captured_window_job(
        self,
        *,
        resume: CapturedResumeClaim,
    ) -> dict[str, Any]:
        """Return captured candidates without granting provider capability."""
        claim = resume.claim
        preflight = await self.preflight_window_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=self._lease_duration,
        )
        if preflight == PreflightResult.ALREADY_TERMINAL:
            return {"status": "already_terminal"}
        try:
            if len(resume.receipts) != 1:
                raise PayloadContractError("grammar_window_receipt_count_invalid")
            receipt = resume.receipts[0]
            execution = self._execution_from_captured_receipt(receipt)
            await self._load_window_context(claim.job_id)
        except GrammarWindowExecutionError as exc:
            if is_semantic_fence_failure_code(exc.failure_code):
                await self._mark_semantic_fence_superseded(claim, exc)
                return {"status": "superseded", "failure_code": exc.failure_code}
            await self._pause_invalid_or_incomplete_resume(claim, exc)
            return {"status": "paused"}
        except Exception as exc:
            await self._pause_invalid_or_incomplete_resume(claim, exc)
            return {"status": "paused"}

        event_id = await self._materialize_captured_usage(receipt)
        heartbeat = LeaseHeartbeat(
            job_runtime=self._job_runtime,
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=self._lease_duration,
            heartbeat_interval=self._heartbeat_interval,
        )
        await heartbeat.start()
        try:
            try:
                await heartbeat.verify_ownership()
            except (
                FenceViolationError,
                IllegalTransitionError,
                LeaseExpiredError,
                LeaseTokenMismatchError,
                LookupError,
            ):
                return {"status": "retry_later"}
        finally:
            await heartbeat.stop()
        return self._candidates_ready_result(execution, event_id)

    @staticmethod
    def _execution_identity(claim: ClaimResult) -> ExecutionIdentity:
        execution_slot = 1
        return ExecutionIdentity(
            invocation_key=(
                f"reader:{CAPABILITY_READER_GRAMMAR_BUNDLE}:"
                f"{claim.job_id}:{claim.attempt_count}:{execution_slot}"
            ),
            reader_job_id=claim.job_id,
            reader_run_id=claim.run_id,
            attempt_ordinal=claim.attempt_count,
            execution_slot=execution_slot,
        )

    async def _capture_execution(
        self,
        *,
        identity: ExecutionIdentity,
        claim: ClaimResult,
        context: dict[str, Any],
        execution: GrammarWindowExecutionResult,
    ) -> UUID | None:
        prepared = prepare_capture_envelope(
            invocation_kind="reader.grammar_window",
            resume_payload_kind="reader.grammar_window.result",
            resume_payload_schema_version=1,
            usage_event_draft_schema_version=1,
            normalized_payload={
                "candidates": [
                    self._candidate_payload(candidate)
                    for candidate in execution.candidates
                ]
            },
            usage_event_draft={
                "usage_scope": USAGE_SCOPE_SYSTEM_INTERNAL,
                "capability_code": CAPABILITY_READER_GRAMMAR_BUNDLE,
                "billing_mode": BILLING_MODE_INTERNAL_ONLY,
                "status": STATUS_SUCCEEDED,
                "user_id": claim.user_id,
                "reading_record_id": claim.reading_record_id,
                "reader_run_id": claim.run_id,
                "reader_job_id": claim.job_id,
                "workflow_name": "reader_orchestration",
                "workflow_version": GRAMMAR_WORKFLOW_VERSION,
                "prompt_version": execution.prompt_version,
                "model_route": execution.model_route,
                "model_profile_id": execution.model_profile,
                "model_profile": execution.model_profile,
                "model_provider": execution.model_provider,
                "model_name": execution.model_name,
                "planner_kind": "llm_worker",
                "usage_data": execution.usage_data,
                "operation_fingerprint": claim.operation_fingerprint.split(":", 1)[0],
                "metadata_json": {
                    "plan_id": str(context["plan_id"]),
                    "window_id": str(context["window_id"]),
                    "window_index": context["window_index"],
                    "target_unit_ids": context["target_unit_ids"],
                    "target_anchor_ids": context["target_anchor_ids"],
                    "candidate_count": len(execution.candidates),
                    "pre_publish_no_candidates": not execution.candidates,
                    "invocation_key": identity.invocation_key,
                    "attempt_ordinal": identity.attempt_ordinal,
                    "execution_slot": identity.execution_slot,
                },
            },
        )
        receipt = await self._journal_service.capture_execution(
            identity=identity,
            prepared=prepared,
        )
        return await self._materialize_captured_usage(receipt)

    @staticmethod
    def _candidate_payload(candidate: CandidateItem) -> dict[str, Any]:
        return {
            "item_type": candidate.item_type,
            "anchor_segment_id": candidate.anchor_segment_id,
            "spans": candidate.spans,
            "semantic_dedup_key": candidate.semantic_dedup_key,
            "pattern_key": candidate.pattern_key,
            "quality_score": candidate.quality_score,
            "reading_blocker": candidate.reading_blocker,
            "dedup_hint": candidate.dedup_hint,
            "grammar_point": candidate.grammar_point,
            "pattern": candidate.pattern,
            "note": candidate.note,
            "label": candidate.label,
            "analysis": candidate.analysis,
            "chunks": candidate.chunks,
        }

    @staticmethod
    def _execution_from_captured_receipt(
        receipt: CapturedReceipt,
    ) -> GrammarWindowExecutionResult:
        if receipt.invocation_kind != "reader.grammar_window":
            raise PayloadContractError("grammar_window_invocation_kind_invalid")
        payload = decode_resume_payload(
            kind=receipt.resume_payload_kind,
            schema_version=receipt.resume_payload_schema_version,
            payload=receipt.normalized_payload,
        )
        usage = decode_usage_event_draft(
            schema_version=receipt.usage_event_draft_schema_version,
            payload=receipt.usage_event_draft,
        )
        return GrammarWindowExecutionResult(
            candidates=[
                CandidateItem(**candidate.model_dump(mode="python"))
                for candidate in payload.candidates
            ],
            usage_data=usage.usage_data,
            prompt_version=usage.prompt_version,
            model_route=usage.model_route,
            model_profile=usage.model_profile,
            model_provider=usage.model_provider,
            model_name=usage.model_name,
        )

    async def _materialize_captured_usage(
        self,
        receipt: CapturedReceipt,
    ) -> UUID | None:
        return await self._reconcile_captured_usage(
            invocation_key=receipt.identity.invocation_key,
            fallback_event_id=receipt.ai_usage_event_id,
        )

    async def reconcile_published_usage(
        self,
        *,
        claim: ClaimResult,
        fallback_event_id: UUID | None,
    ) -> UUID | None:
        return await self._reconcile_captured_usage(
            invocation_key=self._execution_identity(claim).invocation_key,
            fallback_event_id=fallback_event_id,
        )

    async def _reconcile_captured_usage(
        self,
        *,
        invocation_key: str,
        fallback_event_id: UUID | None,
    ) -> UUID | None:
        try:
            await ReaderUsageAttributionService(
                journal_service=self._journal_service
            ).materialize_and_reconcile(
                invocation_key=invocation_key
            )
            receipt = await self._journal_service.load_captured_receipt(
                invocation_key=invocation_key
            )
        except Exception as exc:
            logger.warning(
                "grammar_window_usage_delivery_deferred: invocation_key=%s error=%s",
                invocation_key,
                type(exc).__name__,
            )
            return fallback_event_id
        return receipt.ai_usage_event_id or fallback_event_id

    @staticmethod
    def _candidates_ready_result(
        execution: GrammarWindowExecutionResult,
        event_id: UUID | None,
    ) -> dict[str, Any]:
        return {
            "status": "candidates_ready",
            "candidates": execution.candidates,
            "usage_data": execution.usage_data,
            "prompt_version": execution.prompt_version,
            "model_route": execution.model_route,
            "model_profile": execution.model_profile,
            "model_provider": execution.model_provider,
            "model_name": execution.model_name,
            "ai_usage_event_id": event_id,
            "execution_captured": True,
        }

    async def _pause_invalid_or_incomplete_resume(
        self,
        claim: ClaimResult,
        exc: Exception,
    ) -> None:
        invalid = isinstance(exc, PayloadContractError)
        await self._pause_model_execution_claim(
            claim,
            rationale_code=(
                "model_execution_receipt_invalid"
                if invalid
                else "model_execution_captured_resume_required"
            ),
            failure_code=(
                "receipt_payload_invalid" if invalid else "post_provider_resume_required"
            ),
            failure_message=str(exc),
        )

    async def pause_captured_claim(
        self,
        claim: ClaimResult,
        exc: Exception,
        *,
        invalid_receipt: bool = False,
    ) -> None:
        await self._pause_model_execution_claim(
            claim,
            rationale_code=(
                "model_execution_receipt_invalid"
                if invalid_receipt
                else "model_execution_captured_resume_required"
            ),
            failure_code=(
                "receipt_payload_invalid"
                if invalid_receipt
                else "post_provider_resume_required"
            ),
            failure_message=str(exc),
        )

    async def _pause_model_execution_claim(
        self,
        claim: ClaimResult,
        *,
        rationale_code: str,
        failure_code: str,
        failure_message: str | None = None,
    ) -> None:
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status="paused",
            lease_token=claim.lease_token,
            pause_owner="system",
            failure_class="model_execution",
            failure_code=failure_code,
            failure_message=failure_message,
            rationale_code=rationale_code,
        )

    async def _mark_semantic_fence_superseded(
        self,
        claim: ClaimResult,
        exc: GrammarWindowExecutionError,
    ) -> None:
        await self._job_runtime.transition(
            job_id=claim.job_id,
            target_status="superseded",
            lease_token=claim.lease_token,
            rationale_code=exc.failure_code,
        )
        async with self.get_pool().acquire() as conn:
            await conn.execute(
                """
                UPDATE reader_runs
                SET status = 'superseded',
                    failure_class = $2,
                    failure_code = $3,
                    finished_at = $4
                WHERE id = $1
                """,
                claim.run_id,
                exc.failure_class,
                exc.failure_code,
                datetime.now(UTC),
            )

    # ------------------------------------------------------------------
    # §8.6 heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(
        self,
        *,
        job_id: UUID,
        lease_token: UUID,
    ) -> None:
        """Renew the lease every ``heartbeat_interval`` (compat wrapper).

        Delegates to the shared :class:`LeaseHeartbeat` renewal
        loop so the window and batch workers share ONE implementation.
        Runs until cancelled, mirroring the pre- behavior.
        """
        await LeaseHeartbeat(
            job_runtime=self._job_runtime,
            job_id=job_id,
            lease_token=lease_token,
            lease_duration=self._lease_duration,
            heartbeat_interval=self._heartbeat_interval,
        ).run_forever()

    # ------------------------------------------------------------------
    # §8.3 context loading
    # ------------------------------------------------------------------

    async def _load_window_context(self, job_id: UUID) -> dict[str, Any]:
        """Load window target anchors + context anchors + source text.

        JOINs ``anchor_segments`` + ``reading_units`` + ``reading_bases`` to
        slice ``source_text`` for each target anchor using UTF-16 code unit
        offsets (mirrors ``grammar_worker._load_job_context``). Context
        anchors (prev/next) are loaded with the same metadata structure.

        Also resolves the variant-first strategy (reading_goal
        reading_variant / strategy_hash / layer_policy_hash) from
        ``input_json`` and cross-validates against the live resolver. The
        resolved ``grammar_prompt_lines`` are placed in the context dict so
        ``_build_window_prompt`` can inject them into the LLM prompt. Fail-
        closed: missing metadata or hash mismatch raises
        ``GrammarWindowExecutionError``; there is no default fallback.
        """
        async with self.get_pool().acquire() as conn:
            job_row = await conn.fetchrow(
                """
                SELECT job.input_json,
                       job.base_id,
                       job.reading_record_id,
                       job.expected_generation,
                       job.operation_fingerprint,
                       base.text AS base_text
                FROM reader_jobs job
                JOIN reading_bases base
                  ON base.id = job.base_id
                 AND base.reading_record_id = job.reading_record_id
                WHERE job.id = $1
                """,
                job_id,
            )
            if job_row is None:
                raise LookupError(f"reader job {job_id} not found")

            input_data: Any = job_row["input_json"]
            if isinstance(input_data, str):
                input_data = json.loads(input_data)

            base_id = job_row["base_id"]
            base_text = str(job_row["base_text"])

            # Semantic fence before model: target unit metadata vs job fence.
            target_unit_ids = list(input_data.get("target_unit_ids") or [])
            if target_unit_ids:
                unit_meta_rows = await conn.fetch(
                    """
                    SELECT unit_id, metadata_json
                    FROM reading_units
                    WHERE base_id = $1
                      AND unit_id = ANY($2::text[])
                    """,
                    base_id,
                    target_unit_ids,
                )
                meta_list: list[dict[str, Any]] = []
                for ur in unit_meta_rows:
                    um = ur["metadata_json"]
                    if hasattr(um, "keys"):
                        um = dict(um)
                    elif not isinstance(um, dict):
                        um = {}
                    meta_list.append(um)
                try:
                    validate_automatic_job_semantic_fence(
                        job_input=input_data if isinstance(input_data, dict) else {},
                        layer="grammar_note",
                        layers_any=("grammar_note", "sentence_analysis"),
                        unit_metadata_list=meta_list,
                        operation_fingerprint=str(
                            job_row["operation_fingerprint"] or ""
                        ),
                        trusted_record_id=str(job_row["reading_record_id"]),
                        trusted_base_id=str(job_row["base_id"]),
                        trusted_generation=int(job_row["expected_generation"]),
                    )
                except SemanticFenceError as exc:
                    raise GrammarWindowExecutionError(
                        str(exc),
                        retryable=False,
                        failure_class="validation",
                        failure_code=exc.code,
                    ) from exc

            target_anchor_ids: list[str] = list(
                input_data.get("target_anchor_ids", [])
            )
            context_anchor_prev_ids: list[str] = list(
                input_data.get("context_anchor_prev", [])
            )
            context_anchor_next_ids: list[str] = list(
                input_data.get("context_anchor_next", [])
            )

            target_anchors = await self._load_anchor_rows(
                conn,
                base_id=base_id,
                anchor_ids=target_anchor_ids,
                base_text=base_text,
            )
            context_anchor_prev = await self._load_anchor_rows(
                conn,
                base_id=base_id,
                anchor_ids=context_anchor_prev_ids,
                base_text=base_text,
            )
            context_anchor_next = await self._load_anchor_rows(
                conn,
                base_id=base_id,
                anchor_ids=context_anchor_next_ids,
                base_text=base_text,
            )

        # Resolve + validate variant strategy from input_json metadata.
        strategy_info = _resolve_window_strategy(input_data)

        return {
            "job_id": job_id,
            "window_id": UUID(str(input_data["window_id"])),
            "base_id": base_id,
            "reading_record_id": job_row["reading_record_id"],
            "plan_id": str(input_data["plan_id"]),
            "window_index": int(input_data["window_index"]),
            "target_anchors": target_anchors,
            "context_anchor_prev": context_anchor_prev,
            "context_anchor_next": context_anchor_next,
            "window_budget": input_data.get("window_budget", {}),
            "target_unit_ids": list(input_data.get("target_unit_ids", [])),
            "target_anchor_ids": target_anchor_ids,
            # Strategy fields consumed by _build_window_prompt.
            "reading_goal": strategy_info["reading_goal"],
            "reading_variant": strategy_info["reading_variant"],
            "strategy_version": strategy_info["strategy_version"],
            "strategy_hash": strategy_info["strategy_hash"],
            "layer_policy_hash": strategy_info["layer_policy_hash"],
            "grammar_prompt_lines": strategy_info["grammar_prompt_lines"],
        }

    async def _load_anchor_rows(
        self,
        conn: asyncpg.Connection,
        *,
        base_id: UUID,
        anchor_ids: list[str],
        base_text: str,
    ) -> list[dict[str, Any]]:
        """Load anchor segment + unit metadata and slice source_text.

        JOINs ``anchor_segments`` + ``reading_units`` to get both the anchor
        range (``base_start_utf16`` / ``base_end_utf16``) and the unit range
        (``unit_base_start_utf16`` / ``unit_base_end_utf16``). ``source_text``
        is sliced from ``reading_bases.text`` using UTF-16 code unit offsets.
        """
        if not anchor_ids:
            return []
        rows = await conn.fetch(
            """
            SELECT seg.anchor_segment_id,
                   seg.unit_id,
                   seg.unit_order_index,
                   seg.base_start_utf16,
                   seg.base_end_utf16,
                   unit.base_start_utf16 AS unit_base_start_utf16,
                   unit.base_end_utf16 AS unit_base_end_utf16
            FROM anchor_segments seg
            JOIN reading_units unit
              ON unit.base_id = seg.base_id
             AND unit.unit_id = seg.unit_id
            WHERE seg.base_id = $1
              AND seg.anchor_segment_id = ANY($2::text[])
            ORDER BY seg.unit_order_index ASC
            """,
            base_id,
            anchor_ids,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            source_text = slice_by_utf16_offsets(
                base_text,
                int(row["base_start_utf16"]),
                int(row["base_end_utf16"]),
            )
            result.append({
                "anchor_segment_id": str(row["anchor_segment_id"]),
                "unit_id": str(row["unit_id"]),
                "unit_order_index": int(row["unit_order_index"]),
                "base_start_utf16": int(row["base_start_utf16"]),
                "base_end_utf16": int(row["base_end_utf16"]),
                "unit_base_start_utf16": int(row["unit_base_start_utf16"]),
                "unit_base_end_utf16": int(row["unit_base_end_utf16"]),
                "source_text": source_text or "",
            })
        return result

    # ------------------------------------------------------------------
    # §8.3 LLM call (delegates to injected executor)
    # ------------------------------------------------------------------

    async def _call_llm(
        self, context: dict[str, Any]
    ) -> GrammarWindowExecutionResult:
        """Call the grammar window executor and return the execution result.

        Delegates to ``self._executor.generate(context)``. The executor is
        responsible for building the prompt, invoking the LLM, and parsing
        the structured output into ``CandidateItem`` objects, plus
        returning usage/model metadata for observability.

        Raises ``GrammarWindowExecutionError`` when no executor is
        configured (the default ``UnconfiguredGrammarWindowExecutor``).
        """
        return await self._executor.generate(context)
