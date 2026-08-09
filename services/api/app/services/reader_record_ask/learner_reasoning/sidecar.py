"""Turn-scoped learner-reasoning sidecar (ThinkingObserver + checkpoints)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from app.llm.types import ResolvedModelConfig
from app.services.reader_record_ask.learner_reasoning.buffer import (
    PrivateReasoningBuffer,
)
from app.services.reader_record_ask.learner_reasoning.projector import (
    ProjectorRunFn,
)
from app.services.reader_record_ask.learner_reasoning.router import (
    resolve_projector_route,
)
from app.services.reader_record_ask.learner_reasoning.schemas import (
    DEFAULT_FINALIZE_GRACE_SECONDS,
    LEARNER_REASONING_POLICY_VERSION,
    AdvanceRoundReason,
    FrozenCheckpoint,
    LearnerReasoningBasis,
    LearnerReasoningStage,
    ValidatedLearnerSummary,
    persistence_payload_from_summary,
)
from app.services.reader_record_ask.learner_reasoning.worker import (
    LearnerReasoningWorker,
)

logger = logging.getLogger(__name__)

EmitFn = Callable[[Any], None]


class LearnerReasoningSnapshotEvent:
    """Runtime event published onto the production event queue."""

    __slots__ = (
        "message_id",
        "thread_id",
        "turn_run_id",
        "sequence",
        "revision",
        "stage",
        "basis",
        "text",
        "policy_version",
        "generation_id",
        "type",
    )

    def __init__(
        self,
        *,
        message_id: str,
        thread_id: str,
        turn_run_id: str,
        sequence: int,
        revision: int,
        stage: LearnerReasoningStage,
        basis: tuple[LearnerReasoningBasis, ...],
        text: str,
        generation_id: int,
        policy_version: str = LEARNER_REASONING_POLICY_VERSION,
    ) -> None:
        self.type = "agentic_learner_reasoning_snapshot"
        self.message_id = message_id
        self.thread_id = thread_id
        self.turn_run_id = turn_run_id
        self.sequence = sequence
        self.revision = revision
        self.stage = stage
        self.basis = basis
        self.text = text
        self.generation_id = generation_id
        self.policy_version = policy_version

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "execution_version": "reader_record_ask_agentic_v2",
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "turn_run_id": self.turn_run_id,
            "sequence": self.sequence,
            "revision": self.revision,
            "stage": self.stage,
            "basis": list(self.basis),
            "text": self.text,
            "generation_id": self.generation_id,
            "policy_version": self.policy_version,
            "projection_policy_version": self.policy_version,
        }


class LearnerReasoningSidecar:
    """Aggregates buffer, checkpoint policy, and serial projector worker."""

    def __init__(
        self,
        *,
        emit: EmitFn,
        message_id: str,
        thread_id: str,
        turn_run_id: str,
        main_model_config: ResolvedModelConfig | None = None,
        run_fn: ProjectorRunFn | None = None,
        model: Any | None = None,
        enabled: bool = True,
        test_route: Any | None = None,
        finalize_grace_seconds: float = DEFAULT_FINALIZE_GRACE_SECONDS,
    ) -> None:
        self._emit = emit
        self._message_id = message_id
        self._thread_id = thread_id
        self._turn_run_id = turn_run_id
        self._enabled = enabled
        self._closed = False
        self._destroyed = False
        self._snapshot_frozen = False
        self._finalize_grace = finalize_grace_seconds

        self._buffer = PrivateReasoningBuffer()
        self._generation_id = 0
        self._revision = 0
        self._evidence_seen = False
        self._evidence_kinds: set[str] = set()
        self._cp1_done = False
        self._cp2_done = False
        self._cp3_done = False
        self._saw_real_thinking = False
        self._final_summary: ValidatedLearnerSummary | None = None
        self._published_stages: set[str] = set()
        self._published_sequence = 0

        route = test_route
        if route is None and enabled:
            route = resolve_projector_route(main_model_config)
        api_key = (
            (main_model_config.api_key if main_model_config is not None else "")
            or ""
        )
        if enabled and route is None and run_fn is None and model is None:
            self._enabled = False
            logger.warning(
                "reader_record_ask learner_reasoning disabled detail=route_missing"
            )

        self._worker = LearnerReasoningWorker(
            route=route,
            api_key=api_key,
            publish=self._on_summary,
            run_fn=run_fn,
            model=model,
        )

    # ----- ThinkingObserver surface -----

    def on_analysis_started(self) -> None:
        return None

    def on_reasoning_delta(self, text: str) -> None:
        if not self._enabled or self._closed or self._destroyed:
            return
        try:
            if text:
                self._saw_real_thinking = True
                self._buffer.append(text)
        except Exception:  # noqa: BLE001
            self._self_destruct()

    def on_analysis_finished(self) -> None:
        return None

    def on_reasoning_segment_end(self) -> None:
        if not self._enabled or self._closed or self._destroyed:
            return
        try:
            if not self._saw_real_thinking:
                return
            window, cursor = self._buffer.freeze_window()
            if not window.strip():
                return
            if not self._cp1_done:
                self._dispatch_frozen(
                    stage="analyzing",
                    basis=("general",),
                    kind="preliminary_analysis",
                    window=window,
                    cursor=cursor,
                )
                self._cp1_done = True
                return
            if self._evidence_seen and not self._cp2_done:
                stage, basis = self._evidence_stage_and_basis()
                self._dispatch_frozen(
                    stage=stage,
                    basis=basis,
                    kind="post_evidence",
                    window=window,
                    cursor=cursor,
                )
                self._cp2_done = True
        except Exception:  # noqa: BLE001
            self._self_destruct()

    def on_evidence_boundary(
        self,
        *,
        tool_name: str | None = None,
        is_retry: bool = False,
    ) -> None:
        if not self._enabled or self._closed or self._destroyed:
            return
        try:
            if is_retry:
                return
            name = (tool_name or "").lower()
            if name in {"search_web", "web_search"}:
                self._evidence_kinds.add("web")
            else:
                self._evidence_kinds.add("article")
            self._evidence_seen = True
        except Exception:  # noqa: BLE001
            self._self_destruct()

    def on_first_answer_delta(self) -> None:
        """CP3 best-effort: freeze pre-answer reasoning at first answer delta."""
        if not self._enabled or self._closed or self._destroyed:
            return
        try:
            if self._cp3_done:
                return
            window, cursor = self._buffer.freeze_window()
            if not window.strip():
                self._cp3_done = True
                return
            basis = self._current_basis()
            self._dispatch_frozen(
                stage="synthesizing",
                basis=basis,
                kind="pre_answer",
                window=window,
                cursor=cursor,
            )
            self._cp3_done = True
        except Exception:  # noqa: BLE001
            self._self_destruct()

    def advance_round(
        self,
        reason: AdvanceRoundReason = "normal_tool_result",
    ) -> None:
        """Generation boundary with explicit retry vs normal-tool semantics."""
        if not self._enabled or self._closed or self._destroyed:
            return
        try:
            old_gen = self._generation_id
            self._generation_id += 1
            self._buffer.clear_generation()
            self._saw_real_thinking = False
            is_retry = reason in {
                "tool_argument_retry",
                "output_validator_retry",
            }
            if is_retry:
                self._worker.invalidate_generation(old_gen)
                self._worker.note_generation(
                    self._generation_id, invalidate_older=True
                )
                # Drop unpublished snapshot from invalidated generation.
                if (
                    self._final_summary is not None
                    and self._final_summary.generation_id == old_gen
                ):
                    # Keep only if that stage was already counted published;
                    # unpublished means drop.
                    if self._final_summary.stage not in self._published_stages:
                        self._final_summary = None
                    # If it was published, keep it (already in published_stages).
                # Allow re-fire of stages that never successfully published.
                if "analyzing" not in self._published_stages:
                    self._cp1_done = False
                if "article" not in self._published_stages and "web" not in (
                    self._published_stages
                ):
                    # post-evidence stages
                    if not (
                        {"article", "web"} & self._published_stages
                    ):
                        self._cp2_done = False
                if "synthesizing" not in self._published_stages:
                    self._cp3_done = False
            else:
                # normal_tool_result: keep published summaries; drop pending only.
                self._worker.note_generation(
                    self._generation_id, invalidate_older=False
                )
        except Exception:  # noqa: BLE001
            self._self_destruct()

    # ----- Host finalizer surface -----

    def freeze_intake(self) -> None:
        self._closed = True
        self._worker.freeze_intake()

    async def finalize_for_persist(
        self,
        *,
        grace_seconds: float | None = None,
    ) -> None:
        """Freeze intake, drain in-flight within grace, freeze snapshot.

        CP3 is best-effort: if the in-flight projector does not finish
        within grace, only snapshots completed before the deadline are kept.
        """
        self.freeze_intake()
        grace = (
            self._finalize_grace if grace_seconds is None else grace_seconds
        )
        await self._worker.drain_inflight(grace)
        self._snapshot_frozen = True

    async def aclose(self) -> None:
        self._closed = True
        self._snapshot_frozen = True
        await self._worker.aclose()

    def persistence_payload(self) -> dict[str, Any] | None:
        if self._final_summary is None:
            return None
        return persistence_payload_from_summary(self._final_summary)

    def build_completed_event(self) -> None:
        return None

    @property
    def has_content(self) -> bool:
        return self._final_summary is not None

    @property
    def has_projected_snapshot(self) -> bool:
        """True when at least one safe snapshot was accepted for this turn."""
        return self._final_summary is not None or bool(self._published_stages)

    @property
    def dispatch_count(self) -> int:
        return self._worker.dispatch_count

    @property
    def generation_id(self) -> int:
        return self._generation_id

    # ----- internals -----

    def _current_basis(self) -> tuple[LearnerReasoningBasis, ...]:
        if "web" in self._evidence_kinds and "article" in self._evidence_kinds:
            return ("article", "web")
        if "web" in self._evidence_kinds:
            return ("web",)
        if "article" in self._evidence_kinds:
            return ("article",)
        return ("general",)

    def _evidence_stage_and_basis(
        self,
    ) -> tuple[LearnerReasoningStage, tuple[LearnerReasoningBasis, ...]]:
        if "web" in self._evidence_kinds and "article" not in self._evidence_kinds:
            return "web", ("web",)
        if "web" in self._evidence_kinds:
            return "article", ("article", "web")
        return "article", ("article",)

    def _dispatch_frozen(
        self,
        *,
        stage: LearnerReasoningStage,
        basis: tuple[LearnerReasoningBasis, ...],
        kind: Literal["preliminary_analysis", "post_evidence", "pre_answer"],
        window: str,
        cursor: int,
    ) -> None:
        if self._closed or not self._enabled or self._snapshot_frozen:
            return
        self._revision += 1
        cp = FrozenCheckpoint(
            stage=stage,
            basis=basis,
            revision=self._revision,
            generation_id=self._generation_id,
            window_text=window,
            cursor=cursor,
            checkpoint_kind=kind,
        )
        self._buffer.advance_to(cursor)
        self._worker.submit(cp)

    def _on_summary(self, summary: ValidatedLearnerSummary) -> None:
        if self._snapshot_frozen:
            return
        if self._destroyed:
            return
        # Reject invalidated generation results (retry).
        if summary.generation_id in self._worker._invalidated_generations:
            return
        self._final_summary = summary
        self._published_sequence = summary.sequence
        self._published_stages.add(summary.stage)
        try:
            self._emit(
                LearnerReasoningSnapshotEvent(
                    message_id=self._message_id,
                    thread_id=self._thread_id,
                    turn_run_id=self._turn_run_id,
                    sequence=summary.sequence,
                    revision=summary.revision,
                    stage=summary.stage,
                    basis=summary.basis,
                    text=summary.text,
                    generation_id=summary.generation_id,
                )
            )
        except Exception:  # noqa: BLE001
            logger.info("reader_record_ask learner_reasoning emit failed")

    def _self_destruct(self) -> None:
        self._destroyed = True
        self._enabled = False
        logger.info("reader_record_ask learner_reasoning sidecar_disabled")


def build_learner_reasoning_observer(
    *,
    emit: EmitFn,
    message_id: str,
    thread_id: str,
    turn_run_id: str,
    enabled: bool,
    main_model_config: ResolvedModelConfig | None = None,
    run_fn: ProjectorRunFn | None = None,
    model: Any | None = None,
    test_route: Any | None = None,
    finalize_grace_seconds: float = DEFAULT_FINALIZE_GRACE_SECONDS,
) -> Any:
    if not enabled:
        from app.services.reader_record_ask.reasoning_projection import (
            UserSafeReasoningObserver,
        )

        return UserSafeReasoningObserver(
            emit=emit,
            message_id=message_id,
            thread_id=thread_id,
            turn_run_id=turn_run_id,
        )
    return LearnerReasoningSidecar(
        emit=emit,
        message_id=message_id,
        thread_id=thread_id,
        turn_run_id=turn_run_id,
        main_model_config=main_model_config,
        run_fn=run_fn,
        model=model,
        enabled=True,
        test_route=test_route,
        finalize_grace_seconds=finalize_grace_seconds,
    )


__all__ = [
    "LearnerReasoningSidecar",
    "LearnerReasoningSnapshotEvent",
    "build_learner_reasoning_observer",
]
