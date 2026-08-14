"""ASK-LEARNER-REASONING-PROJECTOR- focused gates (no real providers)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import Settings
from app.llm.types import ResolvedModelConfig
from app.services.reader_record_ask.history_projection import (
    _safe_reasoning_projection,
)
from app.services.reader_record_ask.learner_reasoning.buffer import (
    PrivateReasoningBuffer,
)
from app.services.reader_record_ask.learner_reasoning.capacity import (
    NonBlockingCapacityLimiter,
    reset_global_projector_limiter_for_tests,
)
from app.services.reader_record_ask.learner_reasoning.projector import (
    build_projector_model_settings,
    build_projector_prompt,
    run_learner_reasoning_projector,
)
from app.services.reader_record_ask.learner_reasoning.router import (
    ProjectorRoute,
    resolve_projector_route,
)
from app.services.reader_record_ask.learner_reasoning.schemas import (
    LEARNER_REASONING_POLICY_VERSION,
    FrozenCheckpoint,
    ValidatedLearnerSummary,
    persistence_payload_from_summary,
)
from app.services.reader_record_ask.learner_reasoning.scrub import (
    scrub_private_reasoning_for_projector,
)
from app.services.reader_record_ask.learner_reasoning.sidecar import (
    LearnerReasoningSidecar,
    LearnerReasoningSnapshotEvent,
)
from app.services.reader_record_ask.learner_reasoning.validator import (
    validate_cold_learner_payload,
    validate_learner_text_zh,
)
from app.services.reader_record_ask.learner_reasoning.worker import (
    LearnerReasoningWorker,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def _product_settings() -> Settings:
    profiles = json.loads(
        (_CONFIG_DIR / "model-profiles.example.json").read_text(encoding="utf-8")
    )

    def strip_notes(value):
        if isinstance(value, dict):
            return {
                key: strip_notes(item)
                for key, item in value.items()
                if not key.startswith("_")
            }
        if isinstance(value, list):
            return [strip_notes(item) for item in value]
        return value

    return Settings(
        model_profiles_json=json.dumps(strip_notes(profiles)),
        reader_ask_model_options_json=(
            _CONFIG_DIR / "reader-ask-model-options.json"
        ).read_text(encoding="utf-8"),
    )

# ---------------------------------------------------------------------------
# Buffer ring
# ---------------------------------------------------------------------------


def test_buffer_ring_accepts_past_cap_and_returns_newest_window() -> None:
    buf = PrivateReasoningBuffer(turn_cap=100, window_limit=20)
    buf.append("OLD" * 40)  # 120 chars → compacted to 100
    assert buf.retained_chars <= 100
    buf.append("NEW_TAIL_MARKER_XXXX")
    window, cursor = buf.freeze_window()
    assert "NEW_TAIL" in window
    assert len(window) <= 20
    assert cursor > 0


def test_buffer_single_append_over_cap_keeps_newest() -> None:
    """Single 13K append must retain newest 12K, not wipe the buffer."""
    buf = PrivateReasoningBuffer(turn_cap=12_000, window_limit=2_000)
    body = ("A" * 1000) + ("Z" * 12_000)  # 13K total, newest are Z
    buf.append(body)
    assert buf.retained_chars == 12_000
    joined = buf.joined()
    assert joined == "Z" * 12_000
    window, _ = buf.freeze_window()
    assert set(window) == {"Z"}
    assert len(window) == 2_000


def test_buffer_cursor_monotonic() -> None:
    buf = PrivateReasoningBuffer()
    buf.append("a" * 10)
    _, c1 = buf.freeze_window()
    buf.append("b" * 10)
    _, c2 = buf.freeze_window()
    assert c2 > c1


# ---------------------------------------------------------------------------
# Generation / retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gen0_inflight_retry_gen1_zero_publish() -> None:
    """gen0 in-flight → retry invalidate → gen0 return must not publish."""
    reset_global_projector_limiter_for_tests(limit=8)
    events: list[Any] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run(_w: str) -> str | None:
        started.set()
        await release.wait()
        return "旧世代不该发布的摘要"

    sc = LearnerReasoningSidecar(
        emit=events.append,
        message_id="m1",
        thread_id="t1",
        turn_run_id="r1",
        run_fn=slow_run,
        enabled=True,
    )
    sc.on_reasoning_delta("第一代分析内容足够长了。")
    sc.on_reasoning_segment_end()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    # ModelRetry path
    sc.advance_round("output_validator_retry")
    release.set()
    await asyncio.sleep(0.15)
    await sc.aclose()
    snaps = [e for e in events if isinstance(e, LearnerReasoningSnapshotEvent)]
    assert snaps == []
    assert sc.persistence_payload() is None


@pytest.mark.asyncio
async def test_normal_tool_keeps_published_and_budget() -> None:
    reset_global_projector_limiter_for_tests(limit=8)
    events: list[Any] = []

    async def run_fn(_w: str) -> str | None:
        return "正在整理当前思路"

    sc = LearnerReasoningSidecar(
        emit=events.append,
        message_id="m1",
        thread_id="t1",
        turn_run_id="r1",
        run_fn=run_fn,
        enabled=True,
    )
    sc.on_reasoning_delta("初步分析用户问题的核心意图。")
    sc.on_reasoning_segment_end()
    await asyncio.sleep(0.2)
    assert any(isinstance(e, LearnerReasoningSnapshotEvent) for e in events)
    sc.advance_round("normal_tool_result")
    sc.on_evidence_boundary(tool_name="search_current_article")
    sc.on_reasoning_delta("结合证据继续分析文章内容。")
    sc.on_reasoning_segment_end()
    await asyncio.sleep(0.2)
    await sc.aclose()
    assert sc.dispatch_count <= 3
    assert sc.persistence_payload() is not None


@pytest.mark.asyncio
async def test_retry_allows_restage_within_budget() -> None:
    reset_global_projector_limiter_for_tests(limit=8)
    calls = 0

    async def run_fn(_w: str) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(10)
            return "不应发布"
        return "重试后的思路摘要文本"

    sc = LearnerReasoningSidecar(
        emit=lambda e: None,
        message_id="m1",
        thread_id="t1",
        turn_run_id="r1",
        run_fn=run_fn,
        enabled=True,
        finalize_grace_seconds=0.05,
    )
    sc.on_reasoning_delta("第一代分析内容足够长了。")
    sc.on_reasoning_segment_end()
    await asyncio.sleep(0.05)
    sc.advance_round("tool_argument_retry")
    sc.on_reasoning_delta("第二代重新分析问题要点。")
    sc.on_reasoning_segment_end()
    await asyncio.sleep(0.2)
    await sc.finalize_for_persist(grace_seconds=0.3)
    # dispatch counted both attempts
    assert sc.dispatch_count <= 3
    await sc.aclose()


# ---------------------------------------------------------------------------
# Finalize grace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_grace_completes_inflight() -> None:
    reset_global_projector_limiter_for_tests(limit=8)
    events: list[Any] = []

    async def run_fn(_w: str) -> str | None:
        await asyncio.sleep(0.05)
        return "宽限期内完成的摘要"

    sc = LearnerReasoningSidecar(
        emit=events.append,
        message_id="m1",
        thread_id="t1",
        turn_run_id="r1",
        run_fn=run_fn,
        enabled=True,
        finalize_grace_seconds=0.5,
    )
    sc.on_reasoning_delta("分析内容分析内容分析")
    sc.on_first_answer_delta()  # CP3 best-effort
    await sc.finalize_for_persist(grace_seconds=0.4)
    assert sc.persistence_payload() is not None
    assert sc.persistence_payload()["text"] == "宽限期内完成的摘要"
    await sc.aclose()


@pytest.mark.asyncio
async def test_finalize_grace_timeout_keeps_prior_only() -> None:
    reset_global_projector_limiter_for_tests(limit=8)
    events: list[Any] = []
    n = 0

    async def run_fn(_w: str) -> str | None:
        nonlocal n
        n += 1
        if n == 1:
            return "已完成的前序摘要文本"
        await asyncio.sleep(2.0)
        return "超时后才完成的摘要"

    sc = LearnerReasoningSidecar(
        emit=events.append,
        message_id="m1",
        thread_id="t1",
        turn_run_id="r1",
        run_fn=run_fn,
        enabled=True,
    )
    sc.on_reasoning_delta("初步分析内容足够长。")
    sc.on_reasoning_segment_end()
    await asyncio.sleep(0.15)
    sc.on_reasoning_delta("更多分析内容用于最终阶段。")
    sc.on_first_answer_delta()
    await sc.finalize_for_persist(grace_seconds=0.05)
    payload = sc.persistence_payload()
    assert payload is not None
    assert payload["text"] == "已完成的前序摘要文本"
    await sc.aclose()


@pytest.mark.asyncio
async def test_slow_projector_submit_nonblocking() -> None:
    reset_global_projector_limiter_for_tests(limit=8)

    async def slow(_w: str) -> str | None:
        await asyncio.sleep(0.5)
        return "慢摘要文本内容"

    worker = LearnerReasoningWorker(
        route=None,
        api_key="",
        publish=lambda s: None,
        run_fn=slow,
    )
    t0 = time.perf_counter()
    worker.submit(
        FrozenCheckpoint(
            stage="analyzing",
            basis=("general",),
            revision=1,
            generation_id=0,
            window_text="分析内容" * 5,
            cursor=1,
            checkpoint_kind="preliminary_analysis",
        )
    )
    assert time.perf_counter() - t0 < 0.05
    await worker.aclose()


# ---------------------------------------------------------------------------
# Router authority
# ---------------------------------------------------------------------------


def test_router_qwen_not_deepseek_default() -> None:
    """Actual main model Qwen must not pick DeepSeek projector."""
    qwen = ResolvedModelConfig(
        route="reader_ask",
        profile_name="ask-main-qwen",
        provider="dashscope",
        adapter="openai_compatible",
        model_name="qwen3.7-max",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test",
    )
    route = resolve_projector_route(qwen)
    assert route is not None
    assert route.family == "qwen_flash"
    assert route.model_name == "qwen-flash"
    assert "deepseek" not in route.base_url


def test_router_deepseek_exact_host_allowlist() -> None:
    cfg = ResolvedModelConfig(
        route="reader_ask",
        profile_name="ask-main-ds",
        provider="deepseek",
        adapter="openai_compatible",
        model_name="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
    )
    route = resolve_projector_route(cfg)
    assert route is not None
    assert route.family == "deepseek_flash"

    # Substring trap: host contains deepseek but is not allowlisted.
    bad = ResolvedModelConfig(
        route="reader_ask",
        profile_name="evil",
        provider="evil",
        adapter="openai_compatible",
        model_name="deepseek-v4-pro",
        base_url="https://not-deepseek.example.com/v1",
        api_key="sk-test",
    )
    assert resolve_projector_route(bad) is None


def test_router_dashscope_cn_intl_us_preserve_region() -> None:
    def _qwen(base: str) -> ResolvedModelConfig:
        return ResolvedModelConfig(
            route="reader_ask",
            profile_name="ask-qwen",
            provider="dashscope",
            adapter="openai_compatible",
            model_name="qwen3.7-max",
            base_url=base,
            api_key="sk-test",
        )

    cn = resolve_projector_route(
        _qwen("https://dashscope.aliyuncs.com/compatible-mode/v1")
    )
    assert cn is not None
    assert cn.region == "cn-beijing"
    assert cn.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    intl = resolve_projector_route(
        _qwen("https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    )
    assert intl is not None
    assert intl.region == "intl"
    assert intl.base_url == (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )

    us = resolve_projector_route(
        _qwen("https://dashscope-us.aliyuncs.com/compatible-mode/v1")
    )
    assert us is not None
    assert us.region == "us"
    assert us.base_url == "https://dashscope-us.aliyuncs.com/compatible-mode/v1"

    # Unknown / subdomain / camouflage → fail-closed (exact host only)
    assert (
        resolve_projector_route(
            _qwen("https://unknown-llm.example.com/v1")
        )
        is None
    )
    assert (
        resolve_projector_route(
            _qwen("https://unlisted.dashscope-intl.aliyuncs.com/v1")
        )
        is None
    )
    assert (
        resolve_projector_route(
            _qwen("https://proxy.api.deepseek.com/v1")
        )
        is None
    )
    assert (
        resolve_projector_route(
            _qwen("https://user:pass@dashscope.aliyuncs.com/v1")
        )
        is None
    )
    # no scheme
    assert (
        resolve_projector_route(
            ResolvedModelConfig(
                route="reader_ask",
                profile_name="x",
                provider="dashscope",
                adapter="openai_compatible",
                model_name="qwen",
                base_url="dashscope.aliyuncs.com/compatible-mode/v1",
                api_key="sk-test",
            )
        )
        is None
    )

    # Native without authority_endpoint → fail-closed
    native = ResolvedModelConfig(
        route="reader_ask",
        profile_name="ask-qwen-native",
        provider="dashscope_native",
        adapter="dashscope_native",
        model_name="qwen3.7-max",
        base_url="",
        api_key="sk-test",
        authority_endpoint="",
    )
    assert resolve_projector_route(native) is None


def test_router_product_qwen37_max_uses_dashscope_authority() -> None:
    """The product Qwen option routes to the allowlisted DashScope host."""
    from app.llm.router import resolve_model_config
    from app.llm.routes import MODEL_ROUTE_READER_ASK
    from app.llm.types import ModelSelection

    sel = ModelSelection.model_validate(
        {"routes": {"reader_ask": {"profile": "ask-main-qwen37-max"}}}
    )
    cfg = resolve_model_config(_product_settings(), MODEL_ROUTE_READER_ASK, sel)
    assert cfg is not None
    assert cfg.adapter == "openai_compatible"
    assert "dashscope.aliyuncs.com" in cfg.base_url
    keyed = cfg.model_copy(update={"api_key": cfg.api_key or "sk-test"})
    route = resolve_projector_route(keyed)
    assert route is not None
    assert route.family == "qwen_flash"
    assert route.model_name == "qwen-flash"
    assert route.region == "cn-beijing"
    assert "deepseek" not in route.base_url


def test_dashscope_native_authority_credential_env_match_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sibling authority reuse requires both api_key_env non-empty and equal.

    Decision table (never reads/compares/logs actual secrets):
    - both non-empty equal → reuse sibling base_url as authority_endpoint
    - either empty → fail-closed (no authority_endpoint)
    - both non-empty unequal → fail-closed
    Product DASHSCOPE_API_KEY config still resolves to qwen-flash.
    """
    import json

    from app.config.settings import Settings
    from app.llm.router import resolve_model_config
    from app.llm.routes import MODEL_ROUTE_READER_ASK
    from app.llm.types import ModelSelection

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key-not-compared")

    def _settings(
        *,
        native_env: str,
        sibling_env: str,
        sibling_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) -> Settings:
        # api_key keeps providers configured when env name is empty so we can
        # exercise fail-closed credential *name* matching (secrets never read).
        return Settings(
            ask_claread_profile="ask-native",
            model_profiles_json=json.dumps(
                {
                    "providers": {
                        "dashscope": {
                            "adapter": "openai_compatible",
                            "base_url": sibling_url,
                            "api_key_env": sibling_env,
                            "api_key": "sibling-placeholder",
                        },
                        "dashscope_native": {
                            "adapter": "dashscope_native",
                            "api_key_env": native_env,
                            "api_key": "native-placeholder",
                        },
                    },
                    "models": {
                        "qwen37-max-native": {
                            "provider": "dashscope_native",
                            "model_name": "qwen3.7-max",
                        },
                    },
                    "profiles": {
                        "ask-native": {"model": "qwen37-max-native"},
                    },
                }
            ),
        )

    sel = ModelSelection(default_profile="ask-native")

    # equal non-empty → authority filled
    cfg = resolve_model_config(
        _settings(native_env="DASHSCOPE_API_KEY", sibling_env="DASHSCOPE_API_KEY"),
        MODEL_ROUTE_READER_ASK,
        sel,
    )
    assert cfg is not None
    assert cfg.authority_endpoint == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )

    # native empty → fail-closed
    cfg = resolve_model_config(
        _settings(native_env="", sibling_env="DASHSCOPE_API_KEY"),
        MODEL_ROUTE_READER_ASK,
        sel,
    )
    assert cfg is not None
    assert (cfg.authority_endpoint or "") == ""

    # sibling empty → fail-closed
    cfg = resolve_model_config(
        _settings(native_env="DASHSCOPE_API_KEY", sibling_env=""),
        MODEL_ROUTE_READER_ASK,
        sel,
    )
    assert cfg is not None
    assert (cfg.authority_endpoint or "") == ""

    # unequal → fail-closed
    cfg = resolve_model_config(
        _settings(native_env="DASHSCOPE_API_KEY", sibling_env="OTHER_KEY_ENV"),
        MODEL_ROUTE_READER_ASK,
        sel,
    )
    assert cfg is not None
    assert (cfg.authority_endpoint or "") == ""

    # Product path uses the checked-in example catalog; no provider is called.
    product_sel = ModelSelection.model_validate(
        {"routes": {"reader_ask": {"profile": "ask-main-qwen37-max"}}}
    )
    product = resolve_model_config(
        _product_settings(), MODEL_ROUTE_READER_ASK, product_sel
    )
    assert product is not None
    assert product.adapter == "openai_compatible"
    assert "dashscope.aliyuncs.com" in product.base_url
    route = resolve_projector_route(
        product.model_copy(update={"api_key": product.api_key or "sk-test"})
    )
    assert route is not None
    assert route.model_name == "qwen-flash"


def test_router_missing_fail_closed() -> None:
    cfg = ResolvedModelConfig(
        route="reader_ask",
        profile_name="openai",
        provider="openai",
        adapter="openai_compatible",
        model_name="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
    )
    assert resolve_projector_route(cfg) is None


# ---------------------------------------------------------------------------
# Provider settings separation
# ---------------------------------------------------------------------------


def test_deepseek_and_qwen_settings_not_mixed() -> None:
    ds = ProjectorRoute(
        family="deepseek_flash",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
        credential_domain="deepseek|api.deepseek.com|keyed",
        main_dialect="deepseek_direct",
    )
    qw = ProjectorRoute(
        family="qwen_flash",
        model_name="qwen-flash",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        credential_domain="dashscope|dashscope.aliyuncs.com|keyed",
        main_dialect="dashscope_qwen",
        region="cn-beijing",
    )
    ds_s = build_projector_model_settings(ds)
    qw_s = build_projector_model_settings(qw)
    ds_body = dict(ds_s.get("extra_body") or {})  # type: ignore[arg-type]
    qw_body = dict(qw_s.get("extra_body") or {})  # type: ignore[arg-type]
    # ModelSettings may be mapping-like
    if not ds_body:
        ds_body = getattr(ds_s, "extra_body", {}) or {}
    if not qw_body:
        qw_body = getattr(qw_s, "extra_body", {}) or {}
    assert "thinking" in ds_body
    assert "enable_thinking" not in ds_body
    assert "enable_thinking" in qw_body
    assert "thinking" not in qw_body


def test_prompt_not_user_attribution() -> None:
    p = build_projector_prompt(
        scrubbed_window="窗口",
        previous_safe_summary=None,
    )
    assert "学习者当前的思考方向" not in p
    assert "AI 回答" in p or "分析和组织" in p


# ---------------------------------------------------------------------------
# Privacy / cold
# ---------------------------------------------------------------------------


def test_scrub_and_validator_counters() -> None:
    raw = (
        "Looking at https://evil.example/x with Bearer abcdefghijklmnop "
        "and evh_deadbeefcafebabe"
    )
    out = scrub_private_reasoning_for_projector(raw)
    assert "https://" not in out
    assert "Bearer" not in out
    assert validate_learner_text_zh("see https://evil.example") is None
    assert validate_learner_text_zh("[点击](/api/private)") is None
    assert validate_learner_text_zh("<b>注入</b>") is None
    assert validate_learner_text_zh("正在梳理问题要点") is not None


def test_cold_restore_rejects_evil_payloads() -> None:
    good = {
        "projection_policy_version": LEARNER_REASONING_POLICY_VERSION,
        "schema": 1,
        "text": "正在核对文章证据",
        "stage": "article",
        "basis": ["article"],
        "revision": 1,
        "sequence": 1,
    }
    text, stage, basis = validate_cold_learner_payload(good)
    assert text == "正在核对文章证据"
    assert stage == "article"

    evil_url = {**good, "text": "见 https://evil.example 详情"}
    assert validate_cold_learner_payload(evil_url)[0] is None

    evil_stage = {**good, "stage": "hacking"}
    assert validate_cold_learner_payload(evil_stage)[0] is None

    evil_policy = {**good, "projection_policy_version": "reasoning_projection_v1"}
    assert validate_cold_learner_payload(evil_policy)[0] is None

    evil_md = {**good, "text": "[点击](/api/private)继续"}
    assert validate_cold_learner_payload(evil_md)[0] is None

    # history_projection path
    t, trunc, st = _safe_reasoning_projection(
        {"reasoning_projection_json": good}
    )
    assert t == "正在核对文章证据"
    assert st == "article"
    t2, _, _ = _safe_reasoning_projection(
        {"reasoning_projection_json": evil_policy}
    )
    assert t2 is None


def test_persistence_has_generation_id() -> None:
    summary = ValidatedLearnerSummary(
        text="安全摘要文本内容",
        stage="analyzing",
        basis=("general",),
        revision=1,
        sequence=1,
        generation_id=0,
    )
    payload = persistence_payload_from_summary(summary)
    assert payload["generation_id"] == 0
    assert payload["projection_policy_version"] == LEARNER_REASONING_POLICY_VERSION


# ---------------------------------------------------------------------------
# Capacity / instrument
# ---------------------------------------------------------------------------


def test_limiter_nonblocking() -> None:
    lim = NonBlockingCapacityLimiter(limit=1)
    assert lim.try_acquire() is True
    assert lim.try_acquire() is False
    lim.release()
    assert lim.try_acquire() is True


@pytest.mark.asyncio
async def test_projector_agent_instrument_false(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAgent:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("no real run")

    import app.services.reader_record_ask.learner_reasoning.projector as proj

    monkeypatch.setattr(proj, "Agent", FakeAgent)
    route = ProjectorRoute(
        family="deepseek_flash",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
        credential_domain="x",
        main_dialect="deepseek_direct",
    )
    text, detail = await run_learner_reasoning_projector(
        raw_window="分析内容分析内容分析",
        previous_safe_summary=None,
        route=route,
        api_key="k",
        model=object(),
        timeout_seconds=0.2,
    )
    assert text is None
    assert detail == "provider_error"
    assert captured.get("instrument") is False
    assert captured.get("tools") == []


@pytest.mark.asyncio
async def test_analysis_finished_not_checkpoint() -> None:
    sc = LearnerReasoningSidecar(
        emit=lambda e: None,
        message_id="m1",
        thread_id="t1",
        turn_run_id="r1",
        run_fn=lambda w: asyncio.sleep(0, result="x"),
        enabled=True,
    )
    sc.on_reasoning_delta("内容")
    sc.on_analysis_finished()
    assert sc.dispatch_count == 0
    await sc.aclose()
