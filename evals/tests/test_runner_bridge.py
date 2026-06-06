from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claread_eval.runner_bridge.materialize import materialize_runner_config
from claread_eval.runner_bridge.store import InMemoryWorkflowRunRequestStore
from claread_eval.runner_bridge.worker import BridgeWorker


def _write_dataset(evals_root: Path, *, dataset_id: str = "article-analysis-v1") -> None:
    dataset_dir = evals_root / "datasets" / dataset_id
    cases_dir = dataset_dir / "cases"
    cases_dir.mkdir(parents=True)
    (dataset_dir / "dataset.yaml").write_text(
        "\n".join(
            [
                f"id: {dataset_id}",
                "schema_version: eval-dataset-v1",
                "target: article_analysis",
                "case_globs:",
                "  - cases/*.json",
            ]
        ),
        encoding="utf-8",
    )
    (cases_dir / "case-001.json").write_text(
        json.dumps(
            {
                "id": "case-001",
                "text": "The quick brown fox jumps over the lazy dog.",
                "reading_goal": "daily_reading",
                "reading_variant": "intermediate_reading",
                "source_type": "user_input",
                "expected": {
                    "min_translation_coverage": 0.0,
                    "allowed_warning_codes": [],
                    "max_warning_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )


def _worker(store: InMemoryWorkflowRunRequestStore, evals_root: Path) -> BridgeWorker:
    return BridgeWorker(
        store=store,
        evals_root=evals_root,
        worker_id="worker-a",
        lease_seconds=60,
        heartbeat_interval=0.01,
    )


@pytest.mark.asyncio
async def test_claim_competing_workers_only_one_claims() -> None:
    store = InMemoryWorkflowRunRequestStore()
    queued = store.add_request(run_id="bridge-claim", dataset_id="article-analysis-v1")

    first = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)
    second = await store.claim_next_request(worker_id="worker-b", lease_seconds=60)

    assert first is not None
    assert first.id == queued.id
    assert second is None
    assert store.requests[0].status == "running"
    assert store.requests[0].lease_owner == "worker-a"


@pytest.mark.asyncio
async def test_heartbeat_wrong_owner_does_not_update() -> None:
    store = InMemoryWorkflowRunRequestStore()
    queued = store.add_request(run_id="bridge-heartbeat", dataset_id="article-analysis-v1")
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
async def test_completion_does_not_override_cancelled_or_lost_lease() -> None:
    store = InMemoryWorkflowRunRequestStore()
    queued = store.add_request(run_id="bridge-cancelled", dataset_id="article-analysis-v1")
    claimed = await store.claim_next_request(worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    store.requests[0].status = "cancelled"

    succeeded = await store.mark_succeeded(
        request_id=queued.id,
        worker_id="worker-a",
        artifact_run_id="bridge-cancelled",
        artifact_path="evals/runs/bridge-cancelled",
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
async def test_cancelled_running_request_skips_completion_writeback(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_dataset(evals_root)
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(
        run_id="bridge-running-cancel",
        dataset_id="article-analysis-v1",
        adapter_kind="fake",
        config_json={
            "adapter_kind": "fake",
            "rag_mode": "off",
            "trace_scope": "off",
            "fake_latency_seconds": 0.05,
        },
    )
    worker = BridgeWorker(
        store=store,
        evals_root=evals_root,
        worker_id="worker-a",
        lease_seconds=60,
        heartbeat_interval=0.001,
    )

    task = asyncio.create_task(worker.run_once())
    for _ in range(100):
        if store.requests[0].status == "running":
            break
        await asyncio.sleep(0.001)
    assert store.requests[0].status == "running"
    store.requests[0].status = "cancelled"

    claimed = await task

    assert claimed is True
    assert store.requests[0].status == "cancelled"
    assert store.requests[0].artifact_path is None
    assert store.requests[0].error_json is None
    assert (evals_root / "runs" / "bridge-running-cancel" / "report.json").is_file()


@pytest.mark.asyncio
async def test_recover_stale_complete_artifact_marks_succeeded(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    run_dir = evals_root / "runs" / "bridge-recovered"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    (run_dir / "case-index.json").write_text("{}", encoding="utf-8")
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(
        run_id="bridge-recovered",
        dataset_id="article-analysis-v1",
        status="running",
    )
    store.requests[0].lease_until = datetime.now(UTC) - timedelta(seconds=1)

    recovered = await store.recover_stale_requests(evals_root=evals_root)

    assert len(recovered) == 1
    assert store.requests[0].status == "succeeded"
    assert store.requests[0].artifact_run_id == "bridge-recovered"
    assert store.requests[0].artifact_path == "evals/runs/bridge-recovered"


@pytest.mark.asyncio
async def test_recover_stale_incomplete_artifact_marks_failed(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(
        run_id="bridge-incomplete",
        dataset_id="article-analysis-v1",
        status="running",
    )
    store.requests[0].lease_until = datetime.now(UTC) - timedelta(seconds=1)

    recovered = await store.recover_stale_requests(evals_root=evals_root)

    assert len(recovered) == 1
    assert store.requests[0].status == "failed"
    assert store.requests[0].error_json == {
        "code": "StaleRequest",
        "message": "Stale running request has no complete artifact.",
    }


@pytest.mark.asyncio
async def test_dry_run_claim_only_prints_request(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    evals_root = tmp_path / "evals"
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(run_id="bridge-dry-run", dataset_id="article-analysis-v1")

    claimed = await _worker(store, evals_root).run_once(dry_run_claim=True)

    output = capsys.readouterr().out
    assert claimed is True
    assert "bridge-dry-run" in output
    assert store.requests[0].status == "queued"
    assert not (evals_root / "runs").exists()


@pytest.mark.asyncio
async def test_worker_once_fake_adapter_writes_artifacts_and_marks_succeeded(
    tmp_path: Path,
) -> None:
    evals_root = tmp_path / "evals"
    _write_dataset(evals_root)
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(
        run_id="bridge-fake-success",
        dataset_id="article-analysis-v1",
        adapter_kind="fake",
        config_json={
            "adapter_kind": "fake",
            "eval_purpose": "dataset_regression",
            "rag_mode": "off",
            "trace_scope": "off",
            "fake_latency_seconds": 0.0,
        },
    )

    claimed = await _worker(store, evals_root).run_once()

    run_dir = evals_root / "runs" / "bridge-fake-success"
    assert claimed is True
    assert store.requests[0].status == "succeeded"
    assert store.requests[0].artifact_path == "evals/runs/bridge-fake-success"
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "case-index.json").is_file()
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "cases" / "case-001.json").is_file()


@pytest.mark.asyncio
async def test_worker_http_missing_admin_key_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CLAREAD_API_ADMIN_KEY", raising=False)
    evals_root = tmp_path / "evals"
    _write_dataset(evals_root)
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(
        run_id="bridge-http-fails",
        dataset_id="article-analysis-v1",
        adapter_kind="http",
        config_json={"adapter_kind": "http", "rag_mode": "off", "trace_scope": "off"},
    )

    claimed = await _worker(store, evals_root).run_once()

    assert claimed is True
    assert store.requests[0].status == "failed"
    assert store.requests[0].error_json is not None
    assert store.requests[0].error_json["code"] == "RuntimeError"
    assert "CLAREAD_API_ADMIN_KEY" in store.requests[0].error_json["message"]
    assert not (evals_root / "runs" / "bridge-http-fails").exists()


@pytest.mark.asyncio
async def test_worker_existing_run_dir_marks_failed_without_overwrite(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    _write_dataset(evals_root)
    run_dir = evals_root / "runs" / "bridge-existing"
    run_dir.mkdir(parents=True)
    marker = run_dir / "existing.txt"
    marker.write_text("preserve", encoding="utf-8")
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(
        run_id="bridge-existing",
        dataset_id="article-analysis-v1",
        adapter_kind="fake",
        config_json={"adapter_kind": "fake", "rag_mode": "off", "trace_scope": "off"},
    )

    claimed = await _worker(store, evals_root).run_once()

    assert claimed is True
    assert store.requests[0].status == "failed"
    assert store.requests[0].error_json is not None
    assert store.requests[0].error_json["code"] == "ArtifactWriteError"
    assert marker.read_text(encoding="utf-8") == "preserve"


@pytest.mark.asyncio
async def test_worker_missing_dataset_marks_failed(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    store = InMemoryWorkflowRunRequestStore()
    store.add_request(
        run_id="bridge-missing-dataset",
        dataset_id="missing-dataset",
        adapter_kind="fake",
        config_json={"adapter_kind": "fake", "rag_mode": "off", "trace_scope": "off"},
    )

    claimed = await _worker(store, evals_root).run_once()

    assert claimed is True
    assert store.requests[0].status == "failed"
    assert store.requests[0].error_json is not None
    assert store.requests[0].error_json["code"] == "DatasetLoadError"


def test_materialize_uses_embedded_yaml_without_writing_run_config(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    store = InMemoryWorkflowRunRequestStore()
    request = store.add_request(
        run_id="bridge-materialized",
        dataset_id="article-analysis-v1",
        adapter_kind="fake",
        config_json={
            "yaml_content": "\n".join(
                [
                    "run_id: old-run",
                    "dataset_id: article-analysis-v1",
                    "adapter_kind: fake",
                    "prompt_variant_id: variant-a",
                    "prompt_variant_path: "
                    "../prompt-variants/article-analysis/variant-a/manifest.yaml",
                    "model_selection: {}",
                    "rag_mode: off",
                    "trace_scope: off",
                    "runs_root: ../runs",
                    "datasets_root: ../datasets",
                    "fake_latency_seconds: 0.02",
                ]
            ),
        },
    )

    config = materialize_runner_config(request, evals_root=evals_root)

    assert config.run_config.run_id == "bridge-materialized"
    assert config.run_config.dataset_id == "article-analysis-v1"
    assert config.adapter_kind == "fake"
    assert config.fake_latency_seconds == 0.02
    assert config.runs_root == evals_root.resolve() / "runs"
    assert config.datasets_root == evals_root.resolve() / "datasets"
    assert config.prompt_variant_path == (
        evals_root.resolve()
        / "prompt-variants"
        / "article-analysis"
        / "variant-a"
        / "manifest.yaml"
    )
    assert not (evals_root / "run-configs").exists()


def test_materialize_preserves_embedded_prompt_override(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    store = InMemoryWorkflowRunRequestStore()
    request = store.add_request(
        run_id="bridge-embedded-variant",
        dataset_id="article-analysis-v1",
        adapter_kind="fake",
        config_json={
            "prompt_variant_id": "embedded-variant",
            "prompt_override": {
                "variant_id": "embedded-variant",
                "target": "article_analysis",
                "few_shot_mode": "off",
                "policies": {},
                "examples": {},
                "prompt_snapshot_hash": "abc123",
            },
            "adapter_kind": "fake",
            "rag_mode": "off",
            "trace_scope": "off",
        },
    )

    config = materialize_runner_config(request, evals_root=evals_root)

    assert config.run_config.prompt_variant_id == "embedded-variant"
    assert config.prompt_override == {
        "variant_id": "embedded-variant",
        "target": "article_analysis",
        "few_shot_mode": "off",
        "policies": {},
        "examples": {},
        "prompt_snapshot_hash": "abc123",
    }
