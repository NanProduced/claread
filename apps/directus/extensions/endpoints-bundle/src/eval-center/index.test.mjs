import assert from "node:assert/strict";
import test from "node:test";

import {
  attachPromptVariantSnapshot,
  buildRetryRunId,
  buildRetryWorkflowRequestConfig,
  isWorkflowRunRequestCancelable,
  isWorkflowRunRequestRetryable,
  promptVariantSnapshotFromRow,
  retryWorkflowRunRequest,
  workflowConfigWithPromptVariantSnapshot,
  workflowRequestRow,
  workflowRunRequestSummary,
} from "./index.js";

function createWorkflowRequestDb(initialRows) {
  const rows = initialRows.map((row) => ({ ...row }));

  function database(tableName) {
    assert.equal(tableName, "eval_workflow_run_requests");
    const state = { where: {} };
    const builder = {
      select() {
        return builder;
      },
      where(criteria) {
        Object.assign(state.where, criteria);
        return builder;
      },
      first() {
        return rows.find((row) => Object.entries(state.where).every(([key, value]) => row[key] === value)) || null;
      },
      insert(row) {
        rows.push({
          id: `req-${rows.length + 1}`,
          date_created: "2026-05-31T00:00:00.000Z",
          ...row,
        });
        return Promise.resolve(1);
      },
    };
    return builder;
  }

  database.rows = rows;
  return database;
}

function createPromptVariantDb(initialRows) {
  const rows = initialRows.map((row) => ({ ...row }));

  function database(tableName) {
    assert.equal(tableName, "eval_prompt_variant_drafts");
    const state = { where: {} };
    const builder = {
      select() {
        return builder;
      },
      where(criteria) {
        Object.assign(state.where, criteria);
        return builder;
      },
      first() {
        return rows.find((row) => Object.entries(state.where).every(([key, value]) => row[key] === value)) || null;
      },
    };
    return builder;
  }

  return database;
}

test("workflowRequestRow does not prefill artifact fields before execution", () => {
  const row = workflowRequestRow(
    { accountability: { user: "00000000-0000-0000-0000-000000000001" } },
    {
      run_id: "bridge-test-run",
      dataset_id: "article-analysis-v1",
      adapter_kind: "fake",
      rag_mode: "off",
      trace_scope: "off",
    },
  );

  assert.equal(row.status, "queued");
  assert.equal(row.artifact_run_id, null);
  assert.equal(row.artifact_path, null);
  assert.equal(row.max_concurrency, 1);
  assert.equal(row.attempt_no, 1);
  assert.equal(row.max_attempts, 1);
  assert.equal(row.source_request_id, null);
  assert.equal(row.config_json.run_id, "bridge-test-run");
});

test("workflowRequestRow records retry lineage without artifact fields", () => {
  const row = workflowRequestRow(
    { accountability: { user: "00000000-0000-0000-0000-000000000001" } },
    {
      run_id: "bridge-test-run-retry",
      dataset_id: "article-analysis-v1",
      adapter_kind: "fake",
    },
    {
      source_request_id: "req-parent",
      attempt_no: 2,
      max_attempts: 2,
      retry_reason: "manual retry",
    },
  );

  assert.equal(row.status, "queued");
  assert.equal(row.artifact_run_id, null);
  assert.equal(row.artifact_path, null);
  assert.equal(row.source_request_id, "req-parent");
  assert.equal(row.attempt_no, 2);
  assert.equal(row.max_attempts, 2);
  assert.equal(row.retry_reason, "manual retry");
});

test("workflowRunRequestSummary exposes expected artifact path separately", () => {
  const summary = workflowRunRequestSummary({
    id: "req-1",
    run_id: "bridge-summary-run",
    status: "queued",
    dataset_id: "article-analysis-v1",
    mode: "workflow",
    eval_purpose: "dataset_regression",
    adapter_kind: "fake",
    runner_kind: "external_worker",
    config_json: {
      preset_id: "smoke-fake",
      rag_mode: "off",
      trace_scope: "off",
      timeout_seconds: 120,
      config_file: "evals/run-configs/ui-bridge-summary-run.yaml",
    },
    prompt_variant_id: null,
    prompt_variant_snapshot_hash: null,
    artifact_run_id: null,
    artifact_path: null,
    source_request_id: "req-parent",
    attempt_no: 2,
    max_attempts: 2,
    retry_reason: "manual retry",
    max_concurrency: 1,
    lease_owner: null,
    lease_until: null,
    heartbeat_at: null,
    started_at: null,
    finished_at: null,
    date_created: "2026-05-31T00:00:00.000Z",
    date_updated: null,
    error_json: null,
  });

  assert.equal(summary.artifact_path, null);
  assert.equal(summary.expected_artifact_path, "evals/runs/bridge-summary-run");
  assert.equal(summary.source_request_id, "req-parent");
  assert.equal(summary.attempt_no, 2);
  assert.equal(summary.retryable, false);
  assert.equal(summary.config_summary.preset_id, "smoke-fake");
});

test("isWorkflowRunRequestCancelable allows only queued and running", () => {
  assert.equal(isWorkflowRunRequestCancelable("queued"), true);
  assert.equal(isWorkflowRunRequestCancelable("running"), true);
  assert.equal(isWorkflowRunRequestCancelable("succeeded"), false);
  assert.equal(isWorkflowRunRequestCancelable("failed"), false);
  assert.equal(isWorkflowRunRequestCancelable("cancelled"), false);
});

test("isWorkflowRunRequestRetryable allows only failed and cancelled", () => {
  assert.equal(isWorkflowRunRequestRetryable("queued"), false);
  assert.equal(isWorkflowRunRequestRetryable("running"), false);
  assert.equal(isWorkflowRunRequestRetryable("succeeded"), false);
  assert.equal(isWorkflowRunRequestRetryable("failed"), true);
  assert.equal(isWorkflowRunRequestRetryable("cancelled"), true);
});

test("buildRetryRunId generates a safe new run id", () => {
  assert.equal(
    buildRetryRunId("source-run", new Date("2026-05-31T01:02:03.000Z"), "x1"),
    "source-run-retry-20260531010203-x1",
  );
});

test("buildRetryWorkflowRequestConfig patches cloned run id surfaces", () => {
  const config = buildRetryWorkflowRequestConfig(
    {
      run_id: "old-run",
      dataset_id: "article-analysis-v1",
      eval_purpose: "prompt_experiment",
      adapter_kind: "fake",
      config_json: {
        run_id: "old-run",
        dataset_id: "article-analysis-v1",
        preset_id: "smoke-fake",
        config_file: "evals/run-configs/ui-old-run.yaml",
        yaml_content: 'run_id: "old-run"\ndataset_id: "article-analysis-v1"\nadapter_kind: fake\n',
      },
    },
    "new-run",
  );

  assert.equal(config.run_id, "new-run");
  assert.equal(config.config_file, "evals/run-configs/ui-new-run.yaml");
  assert.match(config.yaml_content, /^run_id: "new-run"$/m);
  assert.doesNotMatch(config.yaml_content, /^run_id: "old-run"$/m);
  assert.equal(config.preset_id, "smoke-fake");
});

test("promptVariantSnapshotFromRow builds immutable prompt override payload", () => {
  const snapshot = promptVariantSnapshotFromRow({
    id: "draft-1",
    variant_id: "minimal-diverse-v1",
    target: "article_analysis",
    status: "ready_for_eval",
    scope: "workflow_eval",
    few_shot_mode: "off",
    manifest_json: {
      variant_id: "minimal-diverse-v1",
      target: "article_analysis",
      description: "Less template-like grammar notes.",
      few_shot_mode: "off",
      policies: { grammar: { default: ["Prefer concise notes."] } },
      examples: {},
    },
    notes: "Ready for eval",
  });

  assert.equal(snapshot.variant_id, "minimal-diverse-v1");
  assert.equal(snapshot.prompt_override.variant_id, "minimal-diverse-v1");
  assert.equal(snapshot.prompt_override.prompt_snapshot_hash, snapshot.snapshot_hash);
  assert.equal(snapshot.manifest_json.policies.grammar.default[0], "Prefer concise notes.");
});

test("workflowConfigWithPromptVariantSnapshot embeds prompt override into workflow config", () => {
  const config = workflowConfigWithPromptVariantSnapshot(
    {
      run_id: "variant-run",
      dataset_id: "article-analysis-v1",
      adapter_kind: "fake",
      rag_mode: "off",
      trace_scope: "off",
      prompt_variant_id: "minimal-diverse-v1",
    },
    {
      id: "draft-1",
      variant_id: "minimal-diverse-v1",
      target: "article_analysis",
      status: "ready_for_eval",
      scope: "workflow_eval",
      few_shot_mode: "off",
      policies_json: {},
      examples_json: {},
      manifest_json: {
        variant_id: "minimal-diverse-v1",
        target: "article_analysis",
        few_shot_mode: "off",
        policies: {},
        examples: {},
      },
    },
  );

  assert.equal(config.prompt_variant_id, "minimal-diverse-v1");
  assert.equal(config.prompt_variant_snapshot_hash, config.prompt_override.prompt_snapshot_hash);
  assert.deepEqual(config.prompt_variant_manifest, {
    variant_id: "minimal-diverse-v1",
    target: "article_analysis",
    description: "",
    few_shot_mode: "off",
    policies: {},
    examples: {},
  });
});

test("attachPromptVariantSnapshot loads only ready workflow variants", async () => {
  const database = createPromptVariantDb([
    {
      id: "draft-1",
      variant_id: "ready-variant",
      target: "article_analysis",
      status: "ready_for_eval",
      scope: "workflow_eval",
      few_shot_mode: "off",
      policies_json: {},
      examples_json: {},
      manifest_json: {
        variant_id: "ready-variant",
        target: "article_analysis",
        few_shot_mode: "off",
        policies: {},
        examples: {},
      },
    },
  ]);

  const config = await attachPromptVariantSnapshot(database, {
    run_id: "variant-run",
    dataset_id: "article-analysis-v1",
    adapter_kind: "fake",
    rag_mode: "off",
    prompt_variant_id: "ready-variant",
  });

  assert.equal(config.prompt_variant_id, "ready-variant");
  assert.equal(config.prompt_variant_snapshot_hash, config.prompt_override.prompt_snapshot_hash);
  assert.deepEqual(config.prompt_override.policies, {});
});

test("attachPromptVariantSnapshot rejects prompt variants with rag enabled", async () => {
  const database = createPromptVariantDb([]);

  await assert.rejects(
    attachPromptVariantSnapshot(database, {
      run_id: "variant-run",
      dataset_id: "article-analysis-v1",
      adapter_kind: "fake",
      rag_mode: "rag",
      prompt_variant_id: "ready-variant",
    }),
    /requires rag_mode=off/,
  );
});

test("retryWorkflowRunRequest clones a failed request as a new queued request", async () => {
  const database = createWorkflowRequestDb([
    {
      id: "req-parent",
      run_id: "old-run",
      status: "failed",
      dataset_id: "article-analysis-v1",
      eval_purpose: "prompt_experiment",
      adapter_kind: "fake",
      config_json: {
        run_id: "old-run",
        dataset_id: "article-analysis-v1",
        config_file: "evals/run-configs/ui-old-run.yaml",
        yaml_content: 'run_id: "old-run"\ndataset_id: "article-analysis-v1"\nadapter_kind: fake\n',
      },
      prompt_variant_id: null,
      prompt_variant_snapshot_hash: null,
      artifact_run_id: "old-run",
      artifact_path: "evals/runs/old-run",
      attempt_no: 1,
      max_attempts: 1,
    },
  ]);

  const row = await retryWorkflowRunRequest(
    database,
    { accountability: { user: "00000000-0000-0000-0000-000000000001" } },
    { CLAREAD_EVAL_RUNS_ROOT: "C:/tmp/claread-eval-center-test-runs" },
    "req-parent",
    { run_id: "new-run", retry_reason: "fixed env" },
  );

  assert.equal(row.run_id, "new-run");
  assert.equal(row.status, "queued");
  assert.equal(row.source_request_id, "req-parent");
  assert.equal(row.attempt_no, 2);
  assert.equal(row.max_attempts, 2);
  assert.equal(row.retry_reason, "fixed env");
  assert.equal(row.artifact_run_id, null);
  assert.equal(row.artifact_path, null);
  assert.match(row.config_json.yaml_content, /^run_id: "new-run"$/m);
  assert.equal(database.rows.length, 2);
});
