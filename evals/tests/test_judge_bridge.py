from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claread_eval.judge_bridge.store import InMemoryJudgeRunRequestStore
from claread_eval.judge_bridge.worker import JudgeWorker


def _write_rubric(evals_root: Path) -> None:
    rubric_dir = evals_root / "rubrics"
    rubric_dir.mkdir(parents=True)
    (rubric_dir / "language-quality-v1.yaml").write_text(
        "\n".join(
            [
                "id: language-quality-v1",
                "version: v1",
                "target: article_analysis",
                "description: Language quality smoke rubric.",
                "criteria:",
                "  - id: clarity",
                "    label: Clarity",
                "    description: The explanation is clear and useful.",
                "    score_min: 1",
                "    score_max: 5",
                "    pass_score: 4",
                "    weight: 1.0",
            ]
        ),
        encoding="utf-8",
    )


def _write_run(evals_root: Path, *, run_id: str = "judge-source-run") -> None:
    run_dir = evals_root / "runs" / run_id
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "dataset_id": "article-analysis-v1"}),
        encoding="utf-8",
    )
    (run_dir / "report.json").write_text(
        json.dumps({"run_id": run_id, "total_cases": 1, "passed": 1}),
        encoding="utf-8",
    )
    (run_dir / "case-index.json").write_text(
        json.dumps({"run_id": run_id, "total_cases": 1, "cases": [{"case_id": "case-001"}]}),
        encoding="utf-8",
    )
    (cases_dir / "case-001.json").write_text(
        json.dumps(
            {
                "case_id": "case-001",
                "run_id": run_id,
                "adapter_status": "succeeded",
                "input_snapshot": {"text": "The quick brown fox jumps over the lazy dog."},
                "sentence_entries": [{"sentence_id": "s1", "text": "The quick brown fox."}],
                "translations": [{"sentence_id": "s1", "translation": "敏捷的棕色狐狸。"}],
            }
        ),
        encoding="utf-8",
    )


def _worker(store: InMemoryJudgeRunRequestStore, evals_root: Path) -> JudgeWorker:
    return JudgeWorker(
        store=store,
        evals_root=evals_root,
        worker_id="judge-worker-a",
        lease_seconds=60,
        heartbeat_interval=0.01,
    )


@pytest.mark.asyncio
async def test_claim_competing_judge_workers_only_one_claims() -> None:
    store = InMemoryJudgeRunRequestStore()
    queued = store.add_request(
        judge_run_id="judge-claim",
        run_id="source-run",
        rubric_id="language-quality-v1",
    )

    first = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)
    second = await store.claim_next_request(worker_id="worker-b", lease_seconds=60)

    assert first is not None
    assert first.id == queued.id
    assert second is None
    assert store.requests[0].status == "running"
    assert store.requests[0].lease_owner == "worker-a"


@pytest.mark.asyncio
async def test_judge_heartbeat_wrong_owner_does_not_update() -> None:
    store = InMemoryJudgeRunRequestStore()
    queued = store.add_request(
        judge_run_id="judge-heartbeat",
        run_id="source-run",
        rubric_id="language-quality-v1",
    )
    claimed = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    previous_lease = store.requests[0].lease_until

    ok = await store.touch_heartbeat(
        request_id=queued.id,
        worker_id="worker-b",
        lease_seconds=60,
    )

    assert ok is False
    assert store.requests[0].lease_until == previous_lease


@pytest.mark.asyncio
async def test_judge_completion_does_not_override_cancelled() -> None:
    store = InMemoryJudgeRunRequestStore()
    queued = store.add_request(
        judge_run_id="judge-cancel",
        run_id="source-run",
        rubric_id="language-quality-v1",
    )
    claimed = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    store.requests[0].status = "cancelled"

    succeeded = await store.mark_succeeded(
        request_id=queued.id,
        worker_id="worker-a",
        artifact_path="evals/runs/source-run/judge/judge-cancel",
    )
    failed = await store.mark_failed(
        request_id=queued.id,
        worker_id="worker-a",
        error_json={"code": "Test", "message": "should not write"},
    )

    assert succeeded is False
    assert failed is False
    assert store.requests[0].status == "cancelled"
    assert store.requests[0].artifact_path is None
    assert store.requests[0].error_json is None


@pytest.mark.asyncio
async def test_judge_recover_stale_complete_artifact_marks_succeeded(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    artifact_dir = evals_root / "runs" / "source-run" / "judge" / "judge-recovered"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "report.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "case-results.json").write_text("{}", encoding="utf-8")
    store = InMemoryJudgeRunRequestStore()
    store.add_request(
        judge_run_id="judge-recovered",
        run_id="source-run",
        rubric_id="language-quality-v1",
        status="running",
    )
    store.requests[0].lease_until = datetime.now(UTC) - timedelta(seconds=1)

    recovered = await store.recover_stale_requests(evals_root=evals_root)

    assert len(recovered) == 1
    assert store.requests[0].status == "succeeded"
    assert store.requests[0].artifact_path == "evals/runs/source-run/judge/judge-recovered"


@pytest.mark.asyncio
async def test_judge_recover_stale_incomplete_artifact_marks_failed(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    store = InMemoryJudgeRunRequestStore()
    store.add_request(
        judge_run_id="judge-incomplete",
        run_id="source-run",
        rubric_id="language-quality-v1",
        status="running",
    )
    store.requests[0].lease_until = datetime.now(UTC) - timedelta(seconds=1)

    recovered = await store.recover_stale_requests(evals_root=evals_root)

    assert len(recovered) == 1
    assert store.requests[0].status == "failed"
    assert store.requests[0].error_json == {
        "code": "StaleJudgeRequest",
        "message": "Stale running judge request has no complete artifact.",
    }


@pytest.mark.asyncio
async def test_judge_dry_run_claim_only_prints_request(capsys, tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    store = InMemoryJudgeRunRequestStore()
    store.add_request(
        judge_run_id="judge-dry-run",
        run_id="source-run",
        rubric_id="language-quality-v1",
    )

    claimed = await _worker(store, evals_root).run_once(dry_run_claim=True)

    output = capsys.readouterr().out
    assert claimed is True
    assert "judge-dry-run" in output
    assert store.requests[0].status == "queued"
    assert not (evals_root / "runs").exists()


@pytest.mark.asyncio
async def test_judge_worker_fake_adapter_writes_artifacts_and_marks_succeeded(
    tmp_path: Path,
) -> None:
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    _write_run(evals_root)
    store = InMemoryJudgeRunRequestStore()
    store.add_request(
        judge_run_id="judge-success",
        run_id="judge-source-run",
        rubric_id="language-quality-v1",
        rubric_version="v1",
        judge_adapter_kind="fake",
    )

    claimed = await _worker(store, evals_root).run_once()

    artifact_dir = evals_root / "runs" / "judge-source-run" / "judge" / "judge-success"
    assert claimed is True
    assert store.requests[0].status == "succeeded"
    assert store.requests[0].artifact_path == (
        "evals/runs/judge-source-run/judge/judge-success"
    )
    assert (artifact_dir / "report.json").is_file()
    assert (artifact_dir / "case-results.json").is_file()
    assert (artifact_dir / "packets" / "case-001.json").is_file()


@pytest.mark.asyncio
async def test_judge_worker_missing_llm_env_marks_failed_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CLAREAD_EVAL_JUDGE_BASE_URL", raising=False)
    monkeypatch.setenv("CLAREAD_EVAL_JUDGE_API_KEY", "super-secret")
    monkeypatch.delenv("CLAREAD_EVAL_JUDGE_MODEL", raising=False)
    evals_root = tmp_path / "evals"
    _write_rubric(evals_root)
    _write_run(evals_root)
    store = InMemoryJudgeRunRequestStore()
    store.add_request(
        judge_run_id="judge-llm-env-fails",
        run_id="judge-source-run",
        rubric_id="language-quality-v1",
        judge_adapter_kind="llm",
    )

    claimed = await _worker(store, evals_root).run_once()

    assert claimed is True
    assert store.requests[0].status == "failed"
    assert store.requests[0].error_json is not None
    assert store.requests[0].error_json["code"] == "JudgeAdapterConfigError"
    assert "CLAREAD_EVAL_JUDGE_BASE_URL" in store.requests[0].error_json["message"]
    assert "super-secret" not in store.requests[0].error_json["message"]
    assert not (evals_root / "runs" / "judge-source-run" / "judge" / "judge-llm-env-fails").exists()
