import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  attachPromptVariantSnapshot,
  buildAuthGuard,
  buildRetryJudgeRunId,
  buildRetryRunId,
  buildRetryWorkflowRequestConfig,
  buildWorkflowLabCompareReport,
  cancelJudgeRunRequest,
  createWorkflowLabCompare,
  isSafeFileId,
  inferRunTopologyMode,
  isJudgeRunRequestCancelable,
  isJudgeRunRequestRetryable,
  isWorkflowRunRequestCancelable,
  isWorkflowRunRequestRetryable,
  judgeRequestRow,
  judgeRunRequestSummary,
  promptVariantSnapshotFromRow,
  retryJudgeRunRequest,
  retryWorkflowRunRequest,
  validateWorkflowRunRequest,
  workflowConfigWithPromptVariantSnapshot,
  workflowRequestRow,
  workflowRunRequestSummary,
} from "./index.js";
import {
  attachStandaloneCompareTrialToSession,
  assertNoDuplicateSessionCompareTrial,
  buildNodeLabJudgeWorkerDatabaseUrl,
  buildNodeLabJudgeWorkerLaunch,
  dispatchNodeLabJudgeWorker,
  findDuplicateRunHistoryTrial,
} from "./node-lab.js";

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

function createJudgeRequestDb(initialRows) {
  const rows = initialRows.map((row) => ({ ...row }));

  function database(tableName) {
    assert.equal(tableName, "eval_judge_run_requests");
    const state = { where: {}, whereIn: null };
    const builder = {
      select() {
        return builder;
      },
      where(criteria) {
        Object.assign(state.where, criteria);
        return builder;
      },
      whereIn(field, values) {
        state.whereIn = { field, values };
        return builder;
      },
      first() {
        return rows.find((row) => matches(row, state)) || null;
      },
      update(patch) {
        let count = 0;
        for (const row of rows) {
          if (!matches(row, state)) continue;
          Object.assign(row, patch);
          count += 1;
        }
        return Promise.resolve(count);
      },
      insert(row) {
        rows.push({
          id: `judge-req-${rows.length + 1}`,
          date_created: "2026-05-31T00:00:00.000Z",
          ...row,
        });
        return Promise.resolve(1);
      },
    };
    return builder;
  }

  database.fn = {
    now() {
      return "NOW";
    },
  };
  database.rows = rows;
  return database;
}

function createNodeLabTrialsDb(initialRows) {
  const rows = initialRows.map((row) => ({ ...row }));

  function database(tableName) {
    assert.equal(tableName, "eval_node_lab_trials");
    const state = { where: {} };
    const builder = {
      where(criteria) {
        Object.assign(state.where, criteria);
        return builder;
      },
      select() {
        return Promise.resolve(rows.filter((row) => matches(row, state)));
      },
    };
    return builder;
  }

  return database;
}

function createNodeLabRunHistoryDb(initialTables) {
  const tables = {
    eval_node_lab_trials: [],
    eval_node_lab_sessions: [],
    eval_node_lab_judge_requests: [],
    ...Object.fromEntries(
      Object.entries(initialTables).map(([tableName, rows]) => [
        tableName,
        rows.map((row) => ({ ...row })),
      ]),
    ),
  };

  function database(tableName) {
    const rows = tables[tableName];
    assert.ok(rows, `Unexpected table: ${tableName}`);
    const state = { where: {}, orderBy: null };
    const apply = () => {
      let result = rows.filter((row) => matches(row, state));
      if (state.orderBy) {
        const { field, direction } = state.orderBy;
        result = result.slice().sort((left, right) => {
          const compare = String(left[field] || "").localeCompare(String(right[field] || ""));
          return direction === "desc" ? -compare : compare;
        });
      }
      return result;
    };
    const builder = {
      where(criteria) {
        Object.assign(state.where, criteria);
        return builder;
      },
      select() {
        return Promise.resolve(apply());
      },
      first() {
        return Promise.resolve(apply()[0] || null);
      },
      orderBy(field, direction = "asc") {
        state.orderBy = { field, direction };
        return builder;
      },
      update(patch) {
        let count = 0;
        for (const row of rows) {
          if (!matches(row, state)) continue;
          Object.assign(row, patch);
          count += 1;
        }
        return Promise.resolve(count);
      },
      insert(row) {
        rows.push({ ...row });
        return Promise.resolve(1);
      },
      then(resolve, reject) {
        return Promise.resolve(apply()).then(resolve, reject);
      },
    };
    return builder;
  }

  database.transaction = async (callback) => callback(database);
  database.tables = tables;
  return database;
}

function matches(row, state) {
  const whereMatches = Object.entries(state.where).every(([key, value]) => row[key] === value);
  if (!whereMatches) return false;
  if (state.whereIn) {
    return state.whereIn.values.includes(row[state.whereIn.field]);
  }
  return true;
}

function createResponseProbe() {
  return {
    statusCode: 200,
    payload: null,
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(payload) {
      this.payload = payload;
      return this;
    },
  };
}

function workflowCaseArtifact({
  runId,
  caseId = "case-1",
  topology = "learning",
  hardFailures = 0,
  softFailures = 0,
  promptVariantId = "baseline",
  modelName = "fake-model",
} = {}) {
  return {
    case_id: caseId,
    run_id: runId,
    adapter_status: "succeeded",
    workflow_identity: {
      workflow_name: "article_analysis",
      workflow_version: "3.0.0",
      topology_mode: topology,
    },
    schema_identity: {
      schema_version: "3.0.0",
      topology_mode: topology,
    },
    prompt_identity: {
      prompt_version: null,
      prompt_snapshot_hash: promptVariantId,
      prompt_variant_id: promptVariantId,
    },
    model_identity: {
      route: "annotation_generation",
      model_name: modelName,
    },
    translations: [],
    inline_marks: [],
    sentence_entries: [],
    warnings: [],
    drop_log: [],
    usage_summary: { total_tokens: 0 },
    grader_results: [
      ...Array.from({ length: hardFailures }, (_, index) => ({
        grader_name: `hard-${index}`,
        verdict: "fail",
        severity: "hard",
      })),
      ...Array.from({ length: softFailures }, (_, index) => ({
        grader_name: `soft-${index}`,
        verdict: "fail",
        severity: "soft",
      })),
    ],
  };
}

function writeWorkflowRun(root, {
  runId,
  datasetId = "article-analysis-v1",
  topology = "learning",
  artifacts = [workflowCaseArtifact({ runId, topology })],
  reportTotalCases = artifacts.length,
} = {}) {
  const runPath = join(root, runId);
  mkdirSync(join(runPath, "cases"), { recursive: true });
  writeFileSync(
    join(runPath, "run.json"),
    JSON.stringify({
      run_id: runId,
      dataset_id: datasetId,
      mode: "workflow",
      eval_purpose: "prompt_experiment",
      rag_mode: "off",
      trace_scope: "off",
      created_at: "2026-06-03T00:00:00.000Z",
    }),
  );
  writeFileSync(
    join(runPath, "report.json"),
    JSON.stringify({
      run_id: runId,
      dataset_id: datasetId,
      total_cases: reportTotalCases,
      passed: reportTotalCases,
      failed: 0,
      errored: 0,
      created_at: "2026-06-03T00:00:00.000Z",
    }),
  );
  for (const artifact of artifacts) {
    writeFileSync(
      join(runPath, "cases", `${artifact.case_id}.json`),
      JSON.stringify(artifact),
    );
  }
  writeFileSync(
    join(runPath, "case-index.json"),
    JSON.stringify({
      schema_version: "eval-case-index-v1",
      run_id: runId,
      dataset_id: datasetId,
      total_cases: artifacts.length,
      cases: artifacts.map((artifact) => ({
        case_id: artifact.case_id,
        run_id: artifact.run_id,
        workflow_identity: artifact.workflow_identity,
        schema_identity: artifact.schema_identity,
        prompt_identity: artifact.prompt_identity,
        model_identity: artifact.model_identity,
      })),
    }),
  );
  return runPath;
}

test("buildAuthGuard requires a Directus admin user", () => {
  const nonAdminResponse = createResponseProbe();
  const anonymousResponse = createResponseProbe();
  const adminResponse = createResponseProbe();

  assert.equal(
    buildAuthGuard(
      { accountability: { user: "00000000-0000-0000-0000-000000000001", admin: false } },
      nonAdminResponse,
    ),
    false,
  );
  assert.equal(buildAuthGuard({ accountability: null }, anonymousResponse), false);
  assert.equal(
    buildAuthGuard(
      { accountability: { user: "00000000-0000-0000-0000-000000000001", admin: true } },
      adminResponse,
    ),
    true,
  );
  assert.equal(nonAdminResponse.statusCode, 403);
  assert.equal(anonymousResponse.statusCode, 403);
  assert.equal(adminResponse.statusCode, 200);
});

test("isSafeFileId rejects traversal-looking values", () => {
  assert.equal(isSafeFileId("run.v1-2026_05"), true);
  assert.equal(isSafeFileId(".."), false);
  assert.equal(isSafeFileId("../run"), false);
  assert.equal(isSafeFileId("run..backup"), false);
  assert.equal(isSafeFileId(".env"), false);
  assert.equal(isSafeFileId("run/child"), false);
});

test("inferRunTopologyMode returns a stable learning topology", () => {
  assert.equal(
    inferRunTopologyMode([
      workflowCaseArtifact({ runId: "run-a", caseId: "case-1", topology: "learning" }),
      workflowCaseArtifact({ runId: "run-a", caseId: "case-2", topology: "learning" }),
    ]),
    "learning",
  );
  assert.equal(
    inferRunTopologyMode([
      workflowCaseArtifact({ runId: "run-a", caseId: "case-1", topology: "learning" }),
      workflowCaseArtifact({ runId: "run-a", caseId: "case-2", topology: "academic" }),
    ]),
    "mixed",
  );
});

test("buildWorkflowLabCompareReport compares shared learning cases", () => {
  const report = buildWorkflowLabCompareReport(
    {
      run_id: "baseline",
      dataset_id: "article-analysis-v1",
      report: { total_cases: 2 },
      artifacts: [
        workflowCaseArtifact({ runId: "baseline", caseId: "case-1" }),
        workflowCaseArtifact({ runId: "baseline", caseId: "case-2", hardFailures: 1 }),
      ],
    },
    {
      run_id: "candidate",
      dataset_id: "article-analysis-v1",
      report: { total_cases: 2 },
      artifacts: [
        workflowCaseArtifact({ runId: "candidate", caseId: "case-1", hardFailures: 1, promptVariantId: "candidate-v1" }),
        workflowCaseArtifact({ runId: "candidate", caseId: "case-2", promptVariantId: "candidate-v1" }),
      ],
    },
    new Date("2026-06-03T00:00:00.000Z"),
  );

  assert.equal(report.total_cases, 2);
  assert.equal(report.wins, 1);
  assert.equal(report.losses, 1);
  assert.deepEqual(report.regression_case_ids, ["case-1"]);
  assert.ok(report.comparisons[0].identity_delta.prompt_identity);
});

test("createWorkflowLabCompare writes immutable json and markdown reports", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-"));
  try {
    writeWorkflowRun(root, {
      runId: "baseline",
      artifacts: [workflowCaseArtifact({ runId: "baseline", caseId: "case-1" })],
    });
    writeWorkflowRun(root, {
      runId: "candidate",
      artifacts: [workflowCaseArtifact({ runId: "candidate", caseId: "case-1", hardFailures: 1, promptVariantId: "candidate-v1" })],
    });

    const created = await createWorkflowLabCompare(root, {
      baseline_run_id: "baseline",
      candidate_run_id: "candidate",
    });
    assert.equal(created.created, true);
    assert.equal(created.report_id, "vs-baseline");
    assert.equal(created.report.losses, 1);
    assert.equal(existsSync(join(root, "candidate", "ab", "vs-baseline.json")), true);
    assert.equal(existsSync(join(root, "candidate", "ab", "vs-baseline.md")), true);

    const existing = await createWorkflowLabCompare(root, {
      baseline_run_id: "baseline",
      candidate_run_id: "candidate",
    });
    assert.equal(existing.created, false);
    assert.equal(existing.report.losses, 1);
    const raw = JSON.parse(readFileSync(join(root, "candidate", "ab", "vs-baseline.json"), "utf8"));
    assert.equal(raw.candidate_run_id, "candidate");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabCompare rejects non-learning runs", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-"));
  try {
    writeWorkflowRun(root, {
      runId: "baseline",
      topology: "learning",
      artifacts: [workflowCaseArtifact({ runId: "baseline", caseId: "case-1", topology: "learning" })],
    });
    writeWorkflowRun(root, {
      runId: "candidate",
      topology: "academic",
      artifacts: [workflowCaseArtifact({ runId: "candidate", caseId: "case-1", topology: "academic" })],
    });

    await assert.rejects(
      () => createWorkflowLabCompare(root, {
        baseline_run_id: "baseline",
        candidate_run_id: "candidate",
      }),
      /only supports learning/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabCompare rejects runs without shared cases", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-"));
  try {
    writeWorkflowRun(root, {
      runId: "baseline",
      artifacts: [workflowCaseArtifact({ runId: "baseline", caseId: "case-a" })],
    });
    writeWorkflowRun(root, {
      runId: "candidate",
      artifacts: [workflowCaseArtifact({ runId: "candidate", caseId: "case-b", promptVariantId: "candidate-v1" })],
    });

    await assert.rejects(
      () => createWorkflowLabCompare(root, {
        baseline_run_id: "baseline",
        candidate_run_id: "candidate",
      }),
      /No shared case ids/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("validateWorkflowRunRequest rejects unsafe dataset ids", () => {
  const errors = validateWorkflowRunRequest({
    run_id: "safe-run",
    dataset_id: "../article-analysis-v1",
    adapter_kind: "fake",
  });

  assert.equal(errors[0].field, "dataset_id");
});

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

test("judgeRequestRow does not prefill artifact path before worker execution", () => {
  const row = judgeRequestRow(
    { accountability: { user: "00000000-0000-0000-0000-000000000001" } },
    {
      judge_run_id: "judge-001",
      run_id: "source-run",
      rubric_id: "language-quality-v1",
      rubric_version: "v1",
      judge_adapter_kind: "fake",
      config_json: { max_concurrency: 1 },
    },
  );

  assert.equal(row.status, "queued");
  assert.equal(row.artifact_path, null);
  assert.equal(row.judge_run_id, "judge-001");
  assert.equal(row.run_id, "source-run");
  assert.equal(row.judge_adapter_kind, "fake");
  assert.equal(row.config_json.max_concurrency, 1);
  assert.equal(row.attempt_no, 1);
  assert.equal(row.max_attempts, 1);
  assert.equal(row.source_request_id, null);
});

test("judgeRunRequestSummary exposes expected immutable artifact path", () => {
  const summary = judgeRunRequestSummary({
    id: "judge-req-1",
    judge_run_id: "judge-001",
    run_id: "source-run",
    rubric_id: "language-quality-v1",
    rubric_version: "v1",
    status: "queued",
    judge_adapter_kind: "fake",
    config_json: { source: "directus_eval_center", max_concurrency: 1 },
    artifact_path: null,
    source_request_id: "judge-req-parent",
    attempt_no: 2,
    max_attempts: 2,
    retry_reason: "manual retry",
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
  assert.equal(summary.expected_artifact_path, "evals/runs/source-run/judge/judge-001");
  assert.equal(summary.source_request_id, "judge-req-parent");
  assert.equal(summary.attempt_no, 2);
  assert.equal(summary.retry_reason, "manual retry");
  assert.equal(summary.cancelable, true);
  assert.equal(summary.retryable, false);
  assert.equal(summary.config_summary.source, "directus_eval_center");
});

test("isWorkflowRunRequestCancelable allows only queued and running", () => {
  assert.equal(isWorkflowRunRequestCancelable("queued"), true);
  assert.equal(isWorkflowRunRequestCancelable("running"), true);
  assert.equal(isWorkflowRunRequestCancelable("succeeded"), false);
  assert.equal(isWorkflowRunRequestCancelable("failed"), false);
  assert.equal(isWorkflowRunRequestCancelable("cancelled"), false);
});

test("isJudgeRunRequestCancelable allows only queued and running", () => {
  assert.equal(isJudgeRunRequestCancelable("queued"), true);
  assert.equal(isJudgeRunRequestCancelable("running"), true);
  assert.equal(isJudgeRunRequestCancelable("succeeded"), false);
  assert.equal(isJudgeRunRequestCancelable("failed"), false);
  assert.equal(isJudgeRunRequestCancelable("cancelled"), false);
});

test("isJudgeRunRequestRetryable allows only failed and cancelled", () => {
  assert.equal(isJudgeRunRequestRetryable("queued"), false);
  assert.equal(isJudgeRunRequestRetryable("running"), false);
  assert.equal(isJudgeRunRequestRetryable("succeeded"), false);
  assert.equal(isJudgeRunRequestRetryable("failed"), true);
  assert.equal(isJudgeRunRequestRetryable("cancelled"), true);
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

test("buildRetryJudgeRunId generates a safe new judge id", () => {
  assert.equal(
    buildRetryJudgeRunId("judge-source", new Date("2026-05-31T01:02:03.000Z"), "x1"),
    "judge-source-retry-20260531010203-x1",
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

test("cancelJudgeRunRequest cancels queued judge request without artifact mutation", async () => {
  const database = createJudgeRequestDb([
    {
      id: "judge-req-1",
      judge_run_id: "judge-001",
      run_id: "source-run",
      rubric_id: "language-quality-v1",
      rubric_version: "v1",
      status: "queued",
      judge_adapter_kind: "fake",
      artifact_path: null,
      error_json: null,
    },
  ]);

  const row = await cancelJudgeRunRequest(
    database,
    { accountability: { user: "00000000-0000-0000-0000-000000000001" } },
    "judge-req-1",
  );

  assert.equal(row.status, "cancelled");
  assert.equal(row.artifact_path, null);
  assert.equal(row.error_json, null);
});

test("retryJudgeRunRequest clones a failed judge request as a new queued request", async () => {
  const runsRoot = mkdtempSync(join(tmpdir(), "claread-judge-retry-"));
  try {
    mkdirSync(join(runsRoot, "source-run", "judge"), { recursive: true });
    const database = createJudgeRequestDb([
      {
        id: "judge-req-parent",
        judge_run_id: "judge-old",
        run_id: "source-run",
        rubric_id: "language-quality-v1",
        rubric_version: "v1",
        status: "failed",
        judge_adapter_kind: "fake",
        config_json: {
          source: "directus_eval_center",
          max_concurrency: 1,
          max_cases: 10,
        },
        attempt_no: 1,
        max_attempts: 1,
        artifact_path: null,
        error_json: { code: "Test", message: "failed" },
      },
    ]);

    const row = await retryJudgeRunRequest(
      database,
      { accountability: { user: "00000000-0000-0000-0000-000000000001" } },
      { CLAREAD_EVAL_RUNS_ROOT: runsRoot },
      "judge-req-parent",
      { judge_run_id: "judge-new", retry_reason: "fixed env" },
    );

    assert.equal(row.judge_run_id, "judge-new");
    assert.equal(row.run_id, "source-run");
    assert.equal(row.status, "queued");
    assert.equal(row.source_request_id, "judge-req-parent");
    assert.equal(row.attempt_no, 2);
    assert.equal(row.max_attempts, 2);
    assert.equal(row.retry_reason, "fixed env");
    assert.equal(row.artifact_path, null);
    assert.equal(row.config_json.retry_of_judge_run_id, "judge-old");
    assert.equal(row.config_json.max_cases, 10);
    assert.equal(database.rows.length, 2);
  } finally {
    rmSync(runsRoot, { recursive: true, force: true });
  }
});

test("buildNodeLabJudgeWorkerDatabaseUrl derives postgres url from Directus DB env", () => {
  const env = {
    DB_CLIENT: "pg",
    DB_HOST: "postgres",
    DB_PORT: "5432",
    DB_DATABASE: "claread",
    DB_USER: "directus user",
    DB_PASSWORD: "p@ss word",
  };

  const url = buildNodeLabJudgeWorkerDatabaseUrl(env, (source, key) => source[key]);

  assert.equal(url, "postgresql://directus%20user:p%40ss%20word@postgres:5432/claread");
});

test("buildNodeLabJudgeWorkerLaunch targets one request and node-lab artifact parent", () => {
  const env = {
    DATABASE_URL: "postgresql://claread:secret@postgres:5432/claread",
    CLAREAD_API_BASE_URL: "http://host.docker.internal:8000",
    CLAREAD_API_ADMIN_KEY: "admin-key",
    CLAREAD_NODE_LAB_JUDGE_WORKER_COMMAND: "uv run python -m claread_eval.node_lab_judge.worker",
  };

  const launch = buildNodeLabJudgeWorkerLaunch({
    env,
    readEnv: (source, key) => source[key],
    resolveEvalsRoot: () => "/directus/evals",
    resolveNodeLabArtifactsRoot: () => "/directus/runtime-evals/node-lab",
    judgeRequestId: "judge-001",
  });

  assert.equal(launch.command, "uv");
  assert.deepEqual(launch.args.slice(0, 4), ["run", "python", "-m", "claread_eval.node_lab_judge.worker"]);
  assert.equal(launch.args.includes("--once"), true);
  assert.equal(launch.args.includes("--request-id"), true);
  assert.equal(launch.args[launch.args.indexOf("--request-id") + 1], "judge-001");
  assert.equal(launch.args[launch.args.indexOf("--evals-root") + 1], "/directus/runtime-evals");
  assert.equal(launch.env.DATABASE_URL, env.DATABASE_URL);
  assert.equal(launch.env.CLAREAD_API_ADMIN_KEY, "admin-key");
  assert.match(launch.env.PYTHONPATH, /\/directus\/evals/);
});

test("dispatchNodeLabJudgeWorker starts detached one-shot worker", () => {
  const calls = [];
  const result = dispatchNodeLabJudgeWorker({
    env: {
      DATABASE_URL: "postgresql://claread:secret@postgres:5432/claread",
      CLAREAD_NODE_LAB_JUDGE_WORKER_COMMAND: "python -m claread_eval.node_lab_judge.worker",
    },
    readEnv: (source, key) => source[key],
    resolveEvalsRoot: () => "/directus/evals",
    resolveNodeLabArtifactsRoot: () => "/directus/runtime-evals/node-lab",
    judgeRequestId: "judge-002",
    spawnProcess(command, args, options) {
      calls.push({ command, args, options });
      return {
        on(eventName, handler) {
          calls[0].errorListener = { eventName, handler };
        },
        unref() {},
      };
    },
  });

  assert.equal(result.status, "dispatched");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, "python");
  assert.equal(calls[0].args.includes("--once"), true);
  assert.equal(calls[0].args[calls[0].args.indexOf("--request-id") + 1], "judge-002");
  assert.equal(calls[0].options.detached, true);
  assert.equal(calls[0].options.stdio, "ignore");
  assert.equal(calls[0].errorListener.eventName, "error");
  assert.equal(typeof calls[0].errorListener.handler, "function");
});

test("assertNoDuplicateSessionCompareTrial rejects same compare in one session", async () => {
  const database = createNodeLabTrialsDb([
    {
      trial_id: "trial-existing",
      session_id: "session-001",
      workspace_type: "baseline_compare",
      result_kind: "compare_result",
      input_text_hash: "input-a",
      baseline_snapshot_hash: "baseline-a",
      candidate_snapshot_hashes_json: ["candidate-a"],
    },
  ]);

  await assert.rejects(
    () => assertNoDuplicateSessionCompareTrial(database, {
      sessionId: "session-001",
      inputTextHash: "input-a",
      baselineSnapshotHash: "baseline-a",
      candidateHashes: ["candidate-a"],
    }),
    (error) => {
      assert.equal(error.code, "NODE_LAB_SESSION_DUPLICATE_COMPARE");
      assert.equal(error.existing_trial_id, "trial-existing");
      return true;
    },
  );
});

test("assertNoDuplicateSessionCompareTrial allows same compare in another session", async () => {
  const database = createNodeLabTrialsDb([
    {
      trial_id: "trial-existing",
      session_id: "session-001",
      workspace_type: "baseline_compare",
      result_kind: "compare_result",
      input_text_hash: "input-a",
      baseline_snapshot_hash: "baseline-a",
      candidate_snapshot_hashes_json: ["candidate-a"],
    },
  ]);

  await assertNoDuplicateSessionCompareTrial(database, {
    sessionId: "session-002",
    inputTextHash: "input-a",
    baselineSnapshotHash: "baseline-a",
    candidateHashes: ["candidate-a"],
  });
});

test("findDuplicateRunHistoryTrial finds existing standalone single run", async () => {
  const database = createNodeLabTrialsDb([
    {
      trial_id: "single-existing",
      session_id: null,
      node_name: "grammar",
      workspace_type: "single_run",
      result_kind: "single_run_result",
      input_text_hash: "input-a",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      baseline_snapshot_hash: "prompt-a",
      candidate_snapshot_hashes_json: ["prompt-a"],
    },
  ]);

  const duplicate = await findDuplicateRunHistoryTrial(database, {
    nodeName: "grammar",
    workspaceType: "single_run",
    resultKind: "single_run_result",
    inputTextHash: "input-a",
    readingGoal: "daily_reading",
    readingVariant: "intermediate_reading",
    baselineSnapshotHash: "prompt-a",
    candidateHashes: ["prompt-a"],
  });

  assert.equal(duplicate.trial_id, "single-existing");
});

test("attachStandaloneCompareTrialToSession migrates standalone compare instead of duplicating", async () => {
  const database = createNodeLabRunHistoryDb({
    eval_node_lab_trials: [
      {
        trial_id: "compare-standalone",
        session_id: null,
        node_name: "grammar",
        workspace_type: "baseline_compare",
        result_kind: "compare_result",
        input_text_hash: "input-a",
        input_excerpt: "Sentence.",
        reading_goal: "daily_reading",
        reading_variant: "intermediate_reading",
        baseline_snapshot_hash: "baseline-a",
        candidate_snapshot_hashes_json: ["candidate-a"],
        result_summary_json: {},
        date_created: "2026-06-03T01:00:00.000Z",
      },
    ],
    eval_node_lab_sessions: [
      {
        session_id: "session-002",
        node_name: "grammar",
        title: "Grammar session",
        goal: "Review grammar",
        status: "active",
        allowed_workspace_types_json: ["baseline_compare"],
        baseline_snapshot_json: {},
        candidate_registry_json: [],
        aggregate_summary_json: {},
        decision_summary_json: {},
        tags_json: [],
      },
    ],
    eval_node_lab_judge_requests: [
      {
        judge_request_id: "judge-001",
        trial_id: "compare-standalone",
        session_id: null,
      },
    ],
  });
  const artifactsRoot = mkdtempSync(join(tmpdir(), "node-lab-run-history-"));
  try {
    const migrated = await attachStandaloneCompareTrialToSession({
      database,
      req: { accountability: { user: "user-001" } },
      resolveNodeLabArtifactsRoot: () => artifactsRoot,
      env: {},
      sessionRow: database.tables.eval_node_lab_sessions[0],
      identity: {
        nodeName: "grammar",
        workspaceType: "baseline_compare",
        resultKind: "compare_result",
        inputTextHash: "input-a",
        readingGoal: "daily_reading",
        readingVariant: "intermediate_reading",
        baselineSnapshotHash: "baseline-a",
        candidateHashes: ["candidate-a"],
      },
    });

    assert.equal(migrated.trial_id, "compare-standalone");
    assert.equal(database.tables.eval_node_lab_trials.length, 1);
    assert.equal(database.tables.eval_node_lab_trials[0].session_id, "session-002");
    assert.equal(database.tables.eval_node_lab_judge_requests[0].session_id, "session-002");
    assert.deepEqual(
      JSON.parse(database.tables.eval_node_lab_sessions[0].aggregate_summary_json),
      {
        trial_count: 1,
        workspace_counts: { single_run: 0, baseline_compare: 1 },
        last_trial_id: "compare-standalone",
        last_trial_at: "2026-06-03T01:00:00.000Z",
      },
    );
  } finally {
    rmSync(artifactsRoot, { recursive: true, force: true });
  }
});
