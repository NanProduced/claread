import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  attachPromptVariantSnapshot,
  appendWorkflowDatasetCase,
  buildAuthGuard,
  buildRetryJudgeRunId,
  buildRetryRunId,
  buildRetryWorkflowRequestConfig,
  buildWorkflowLabCompareReport,
  buildSingleRunCaseArtifact,
  syntheticSingleRunCompareRunId,
  cancelJudgeRunRequest,
  createWorkflowCompareJudgeRequest,
  createWorkflowDataset,
  createWorkflowLabSingleRun,
  createWorkflowLabSingleRunCompare,
  saveWorkflowLabSingleRunToHistory,
  createWorkflowLabCompare,
  isSafeFileId,
  inferRunTopologyMode,
  isJudgeRunRequestCancelable,
  isJudgeRunRequestRetryable,
  isWorkflowRunRequestCancelable,
  isWorkflowRunRequestRetryable,
  judgeRequestRow,
  judgeRunRequestSummary,
  listWorkflowDatasets,
  promptVariantSnapshotFromRow,
  readWorkflowDatasetSummary,
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
  recoverStaleDirectusAsyncJudgeRequests,
  updateSessionAggregate,
} from "./node-lab.js";

const REPO_ROOT = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "..", "..", "..",
);

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

function createNodeLabJudgeRequestsDb(initialRows) {
  const rows = initialRows.map((row) => ({ ...row }));

  function database(tableName) {
    assert.equal(tableName, "eval_node_lab_judge_requests");
    const state = { where: {} };
    const builder = {
      where(criteria) {
        Object.assign(state.where, criteria);
        return builder;
      },
      select() {
        return Promise.resolve(rows.filter((row) => matches(row, state)));
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
    };
    return builder;
  }

  database.rows = rows;
  return database;
}

function createNodeLabRunHistoryDb(initialTables) {
  const tables = {
    eval_node_lab_trials: [],
    eval_node_lab_sessions: [],
    eval_node_lab_judge_requests: [],
    eval_node_lab_review_notes: [],
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

function createEvalCenterDb(initialTables = {}) {
  const tables = Object.fromEntries(
    Object.entries({
      eval_prompt_variant_drafts: [],
      eval_workflow_compares: [],
      eval_workflow_compare_judge_requests: [],
      eval_review_notes: [],
      eval_workflow_run_requests: [],
      eval_judge_run_requests: [],
      ...initialTables,
    }).map(([tableName, rows]) => [
      tableName,
      Array.isArray(rows) ? rows.map((row) => ({ ...row })) : [],
    ]),
  );

  function database(tableName) {
    const rows = tables[tableName];
    assert.ok(rows, `Unexpected table: ${tableName}`);
    const state = { where: {}, whereIn: null, orderBy: null, limit: null };
    const apply = () => {
      let result = rows.filter((row) => matches(row, state));
      if (state.orderBy) {
        const { field, direction } = state.orderBy;
        result = result.slice().sort((left, right) => {
          const compare = String(left[field] || "").localeCompare(String(right[field] || ""));
          return direction === "desc" ? -compare : compare;
        });
      }
      if (state.limit != null) {
        result = result.slice(0, state.limit);
      }
      return result;
    };
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
        return Promise.resolve(apply()[0] || null);
      },
      orderBy(field, direction = "asc") {
        state.orderBy = { field, direction };
        return builder;
      },
      limit(value) {
        state.limit = value;
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
        rows.push({
          id: row.id || `${tableName}-${rows.length + 1}`,
          date_created: row.date_created || "2026-06-04T00:00:00.000Z",
          date_updated: row.date_updated || row.date_created || "2026-06-04T00:00:00.000Z",
          ...row,
        });
        return Promise.resolve(1);
      },
      del() {
        let count = 0;
        for (let index = rows.length - 1; index >= 0; index -= 1) {
          if (!matches(rows[index], state)) continue;
          rows.splice(index, 1);
          count += 1;
        }
        return Promise.resolve(count);
      },
      then(resolve, reject) {
        return Promise.resolve(apply()).then(resolve, reject);
      },
    };
    return builder;
  }

  database.fn = {
    now() {
      return "NOW";
    },
  };
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
  inputSnapshot = null,
} = {}) {
  const artifact = {
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
  if (inputSnapshot) {
    artifact.input_snapshot = inputSnapshot;
  }
  return artifact;
}

function writeWorkflowRun(root, {
  runId,
  datasetId = "article-analysis-v1",
  topology = "learning",
  artifacts = [workflowCaseArtifact({ runId, topology })],
  reportTotalCases = artifacts.length,
  readingGoal = "daily_reading",
  readingVariant = "intermediate_reading",
  sourceType = "user_input",
  mode = "workflow",
} = {}) {
  const runPath = join(root, runId);
  mkdirSync(join(runPath, "cases"), { recursive: true });
  writeFileSync(
    join(runPath, "run.json"),
    JSON.stringify({
      run_id: runId,
      dataset_id: datasetId,
      mode,
      eval_purpose: "prompt_experiment",
      rag_mode: "off",
      trace_scope: "off",
      reading_goal: readingGoal,
      reading_variant: readingVariant,
      source_type: sourceType,
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

function createWorkflowLabEnv(root) {
  return {
    CLAREAD_WORKFLOW_RUNTIME_RUNS_ROOT: root,
    CLAREAD_EVAL_RUNS_ROOT: root,
    CLAREAD_WORKFLOW_COMPARE_RUNTIME_ROOT: join(root, "workflow-compares"),
    CLAREAD_EVALS_ROOT: join(REPO_ROOT, "evals"),
  };
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

test("buildWorkflowLabCompareReport promotes single-run compare to sentence-level cases when render_scene differs", () => {
  const baselineArtifact = workflowCaseArtifact({ runId: "baseline", caseId: "single-run-case" });
  baselineArtifact.render_scene = {
    article: {
      sentences: [
        { sentence_id: "s1", text: "Sentence one." },
        { sentence_id: "s2", text: "Sentence two." },
      ],
    },
    translations: [
      { sentence_id: "s1", translation_zh: "基线译文一" },
      { sentence_id: "s2", translation_zh: "同样的译文" },
    ],
    inline_marks: [],
    sentence_entries: [],
    warnings: [],
  };
  const candidateArtifact = workflowCaseArtifact({ runId: "candidate", caseId: "single-run-case", promptVariantId: "candidate-v1" });
  candidateArtifact.render_scene = {
    article: {
      sentences: [
        { sentence_id: "s1", text: "Sentence one." },
        { sentence_id: "s2", text: "Sentence two." },
      ],
    },
    translations: [
      { sentence_id: "s1", translation_zh: "候选译文一" },
      { sentence_id: "s2", translation_zh: "同样的译文" },
    ],
    inline_marks: [],
    sentence_entries: [
      { sentence_id: "s1", label: "名词短语修饰层次", content: "针对 sentence one 的说明。" },
    ],
    warnings: [],
  };

  const report = buildWorkflowLabCompareReport(
    {
      run_id: "baseline",
      dataset_id: "article-analysis-v1",
      report: { total_cases: 1 },
      artifacts: [baselineArtifact],
    },
    {
      run_id: "candidate",
      dataset_id: "article-analysis-v1",
      report: { total_cases: 1 },
      artifacts: [candidateArtifact],
    },
    new Date("2026-06-03T00:00:00.000Z"),
  );

  assert.equal(report.total_cases, 1);
  assert.equal(report.comparisons.length, 1);
  assert.equal(report.comparisons[0].comparison_kind, "sentence");
  assert.equal(report.comparisons[0].sentence_id, "s1");
  assert.equal(report.comparisons[0].case_id, "s1");
  assert.equal(report.comparisons[0].source_case_id, "single-run-case");
});

test("createWorkflowLabCompare materializes a persisted workflow compare under workflow-compares root", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-"));
  try {
    const database = createEvalCenterDb();
    const env = createWorkflowLabEnv(root);
    writeWorkflowRun(root, {
      runId: "baseline",
      artifacts: [workflowCaseArtifact({ runId: "baseline", caseId: "case-1" })],
    });
    writeWorkflowRun(root, {
      runId: "candidate",
      artifacts: [workflowCaseArtifact({ runId: "candidate", caseId: "case-1", hardFailures: 1, promptVariantId: "candidate-v1" })],
    });

    const created = await createWorkflowLabCompare(database, env, {
      baseline_run_id: "baseline",
      candidate_run_id: "candidate",
    });
    assert.equal(created.created, true);
    assert.ok(created.compare_id.startsWith("workflow-compare-"));
    assert.equal(created.detail.report.losses, 1);
    assert.equal(existsSync(join(root, "workflow-compares", created.compare_id, "compare.json")), true);
    assert.equal(existsSync(join(root, "workflow-compares", created.compare_id, "report.json")), true);
    assert.equal(existsSync(join(root, "workflow-compares", created.compare_id, "report.md")), true);
    assert.equal(existsSync(join(root, "workflow-compares", created.compare_id, "evidence-index.json")), true);

    const existing = await createWorkflowLabCompare(database, env, {
      baseline_run_id: "baseline",
      candidate_run_id: "candidate",
    });
    assert.equal(existing.created, false);
    assert.equal(existing.compare_id, created.compare_id);
    assert.equal(existing.detail.report.losses, 1);
    const raw = JSON.parse(readFileSync(join(root, "workflow-compares", created.compare_id, "compare.json"), "utf8"));
    assert.equal(raw.candidate_run_id, "candidate");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabCompare reuses the same compare_id for an immutable run pair", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-reuse-"));
  try {
    const database = createEvalCenterDb();
    const env = createWorkflowLabEnv(root);
    writeWorkflowRun(root, {
      runId: "baseline",
      artifacts: [workflowCaseArtifact({ runId: "baseline", caseId: "case-1" })],
    });
    writeWorkflowRun(root, {
      runId: "candidate",
      artifacts: [workflowCaseArtifact({ runId: "candidate", caseId: "case-1", hardFailures: 1, promptVariantId: "candidate-v1" })],
    });

    const first = await createWorkflowLabCompare(database, env, {
      baseline_run_id: "baseline",
      candidate_run_id: "candidate",
    });
    const second = await createWorkflowLabCompare(database, env, {
      baseline_run_id: "baseline",
      candidate_run_id: "candidate",
    });

    assert.equal(first.created, true);
    assert.equal(second.created, false);
    assert.equal(second.compare_id, first.compare_id);
    assert.equal(database.tables.eval_workflow_compares.length, 1);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabCompare rejects non-learning runs", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-"));
  try {
    const database = createEvalCenterDb();
    const env = createWorkflowLabEnv(root);
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
      () => createWorkflowLabCompare(database, env, {
        baseline_run_id: "baseline",
        candidate_run_id: "candidate",
      }),
      /learning cases/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabCompare keeps only shared learning cases from mixed runs", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-"));
  try {
    const database = createEvalCenterDb();
    const env = createWorkflowLabEnv(root);
    writeWorkflowRun(root, {
      runId: "baseline",
      topology: "learning",
      artifacts: [
        workflowCaseArtifact({ runId: "baseline", caseId: "learning-a", topology: "learning" }),
        workflowCaseArtifact({ runId: "baseline", caseId: "academic-a", topology: "academic" }),
      ],
    });
    writeWorkflowRun(root, {
      runId: "candidate",
      topology: "learning",
      artifacts: [
        workflowCaseArtifact({ runId: "candidate", caseId: "learning-a", topology: "learning", promptVariantId: "candidate-v1" }),
        workflowCaseArtifact({ runId: "candidate", caseId: "academic-a", topology: "academic", promptVariantId: "candidate-v1" }),
      ],
    });

    const result = await createWorkflowLabCompare(database, env, {
      baseline_run_id: "baseline",
      candidate_run_id: "candidate",
    });

    assert.equal(result.detail.report.total_cases, 1);
    assert.equal(result.detail.report.comparisons[0].case_id, "learning-a");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabCompare rejects runs without shared cases", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-"));
  try {
    const database = createEvalCenterDb();
    const env = createWorkflowLabEnv(root);
    writeWorkflowRun(root, {
      runId: "baseline",
      artifacts: [workflowCaseArtifact({ runId: "baseline", caseId: "case-a" })],
    });
    writeWorkflowRun(root, {
      runId: "candidate",
      artifacts: [workflowCaseArtifact({ runId: "candidate", caseId: "case-b", promptVariantId: "candidate-v1" })],
    });

    await assert.rejects(
      () => createWorkflowLabCompare(database, env, {
        baseline_run_id: "baseline",
        candidate_run_id: "candidate",
      }),
      /No shared case ids/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// P1.1 回归:两条不同输入的 workflow single run 仍带字面量 case_id "single-run",
// 旧 compare engine 会假命中 shared case_id 并产出伪 compare 报告;
// case_id 已绑定 input / reading 上下文,且 compare 前加 input_hash 一致性校验,
// 这里两条不同 text 的 single run 应该被 422 拒绝
test("createWorkflowLabCompare rejects single runs whose input_snapshot does not match", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-single-input-"));
  try {
    const database = createEvalCenterDb();
    const env = createWorkflowLabEnv(root);
    writeWorkflowRun(root, {
      runId: "single-baseline-article-a",
      mode: "workflow_single_run",
      artifacts: [
        workflowCaseArtifact({
          runId: "single-baseline-article-a",
          caseId: "single-run-aaaa1111",
          promptVariantId: "workflow-ready",
          inputSnapshot: {
            text: "Article A full text body used by single run.",
            reading_goal: "daily_reading",
            reading_variant: "intermediate_reading",
            source_type: "user_input",
          },
        }),
      ],
    });
    writeWorkflowRun(root, {
      runId: "single-candidate-article-b",
      mode: "workflow_single_run",
      artifacts: [
        workflowCaseArtifact({
          runId: "single-candidate-article-b",
          caseId: "single-run-bbbb2222",
          promptVariantId: "workflow-ready",
          inputSnapshot: {
            text: "Article B different full text body used by single run.",
            reading_goal: "daily_reading",
            reading_variant: "intermediate_reading",
            source_type: "user_input",
          },
        }),
      ],
    });

    await assert.rejects(
      () => createWorkflowLabCompare(database, env, {
        baseline_run_id: "single-baseline-article-a",
        candidate_run_id: "single-candidate-article-b",
      }),
      (err) => {
        assert.equal(err.code, "WORKFLOW_LAB_COMPARE_INPUT_MISMATCH");
        assert.match(err.message, /input contexts/);
        return true;
      },
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabCompare allows two single runs over the same input", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-compare-single-same-"));
  try {
    const database = createEvalCenterDb();
    const env = createWorkflowLabEnv(root);
    const sharedSnapshot = {
      text: "Shared article body that both single runs feed in.",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      source_type: "user_input",
    };
    writeWorkflowRun(root, {
      runId: "single-baseline-same",
      mode: "workflow_single_run",
      artifacts: [
        workflowCaseArtifact({
          runId: "single-baseline-same",
          caseId: "single-run-cccc3333",
          promptVariantId: "workflow-baseline",
          inputSnapshot: sharedSnapshot,
        }),
      ],
    });
    writeWorkflowRun(root, {
      runId: "single-candidate-same",
      mode: "workflow_single_run",
      artifacts: [
        workflowCaseArtifact({
          runId: "single-candidate-same",
          caseId: "single-run-cccc3333",
          promptVariantId: "workflow-candidate",
          inputSnapshot: sharedSnapshot,
        }),
      ],
    });

    const result = await createWorkflowLabCompare(database, env, {
      baseline_run_id: "single-baseline-same",
      candidate_run_id: "single-candidate-same",
    });
    assert.equal(result.created, true);
    assert.equal(result.detail.report.total_cases, 1);
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

// Batch 3:Workflow Lab 主链是 compare-only；历史 run compare / 手动 archive / run-level judge 都必须退出主路径
test("WorkflowSingleRunLauncher submits a dual-run compare payload (baseline + candidate)", () => {
  const modulesRoot = resolve(fileURLToPath(import.meta.url), "../../../../modules-bundle/src/claread-eval-center/modes/workflow-lab");
  const launcherPath = join(modulesRoot, "components/WorkflowSingleRunLauncher.vue");
  const labModePath = join(modulesRoot, "WorkflowLabMode.vue");
  const resultPath = join(modulesRoot, "components/WorkflowSingleRunResult.vue");
  const judgePanelPath = join(modulesRoot, "components/WorkflowJudgePanel.vue");
  const launcher = readFileSync(launcherPath, "utf8");
  const labMode = readFileSync(labModePath, "utf8");
  const result = readFileSync(resultPath, "utf8");
  const judgePanel = readFileSync(judgePanelPath, "utf8");

  // launcher submit() emit 的 payload 形如 { ..., baseline, candidate }
  const submitFnMatch = launcher.match(/function\s+submit\s*\(\s*\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(submitFnMatch, "submit function not found in WorkflowSingleRunLauncher.vue");
  const submitFnBody = submitFnMatch[1];
  assert.match(
    submitFnBody,
    /emit\(\s*["']submit["']\s*,[\s\S]*?baseline\s*:[\s\S]*?candidate\s*:/,
    "submit must emit a payload containing both baseline and candidate keys (dual-run compare)",
  );
  assert.match(
    submitFnBody,
    /prompt_variant_id:\s*form\.value\.candidate_prompt_variant_id/,
    "submit must include candidate_prompt_variant_id in payload (the form exposes it for dual-run)",
  );

  // 表单里 candidate_prompt_variant_id 必填
  assert.match(
    launcher,
    /!form\.value\.candidate_prompt_variant_id/,
    "submit must disable when candidate_prompt_variant_id is empty (single-run compare needs a candidate)",
  );

  // launcher 文案必须把"双跑"语义摆出来
  assert.match(
    launcher,
    /双跑|baseline \/ candidate/i,
    "WorkflowSingleRunLauncher header / button copy must reflect dual-run semantics",
  );

  // WorkflowLabMode: single-run workspace 描述必须明示并发跑两侧
  const singleRunMatch = labMode.match(/id:\s*["']single_run["']\s*,\s*label:[^,]+,\s*desc:\s*["']([^"']+)["']/);
  assert.ok(singleRunMatch, "single_run workspace descriptor not found in WorkflowLabMode.vue");
  const singleRunDesc = singleRunMatch[1];
  assert.match(
    singleRunDesc,
    /baseline.*candidate|candidate.*baseline|双跑/,
    `single_run workspace desc must mention baseline / candidate or dual-run semantics; got: ${singleRunDesc}`,
  );

  // WorkflowLabMode: compare-only,不再暴露 history-compare / archive-side / WorkflowCompareBuilder
  assert.doesNotMatch(
    labMode,
    /history-compare|archiveSideToHistory|ensureBothSidesArchived|WorkflowCompareBuilder/,
    "WorkflowLabMode must not keep history-compare or archive-side branches in compare-only mode",
  );

  // 结果组件不再暴露 Run History 归档按钮
  assert.match(
    result,
    /Compare Workspace|单篇 baseline \/ candidate compare/,
    "WorkflowSingleRunResult must rebrand to compare workspace view",
  );
  assert.match(
    result,
    /Compare id|workflow compare|唯一公开历史对象/,
    "WorkflowSingleRunResult must describe compare as the only public history object",
  );

  assert.match(
    labMode,
    /@click="requestWorkspaceChange\(workspace\.id\)"/,
    "workspace nav buttons must route through requestWorkspaceChange instead of mutating activeWorkspace directly",
  );

  const nextFn = labMode.match(/async\s+function\s+goToNextWorkspace\s*\(\s*\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(nextFn, "goToNextWorkspace function not found in WorkflowLabMode.vue");
  assert.match(
    nextFn[1],
    /requestWorkspaceChange\(nextWorkspaceMeta\.value\.id\)/,
    "goToNextWorkspace must delegate compare_judge transitions through requestWorkspaceChange",
  );
  assert.match(
    judgePanel,
    /compareId|workflow-lab\/compares\/.+judge-requests|Compare 级评审|pairwise/,
    "WorkflowJudgePanel must pivot to compareId and compare-level judge routes/copy",
  );
  assert.doesNotMatch(
    judgePanel,
    /最大 case 数/,
    "WorkflowJudgePanel should not expose max case controls in the simplified single-article compare flow",
  );
  assert.match(
    judgePanel,
    /llm，调用真实 Judge|Judge 模型/,
    "WorkflowJudgePanel should expose a real llm judge path and model selector for compare validation",
  );
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
  assert.equal(summary.cancelable, true);
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
    scope: "workflow_lab",
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

test("promptVariantSnapshotFromRow expands workflow bundle manifests", () => {
  const snapshot = promptVariantSnapshotFromRow({
    id: "draft-bundle-1",
    variant_id: "workflow-bundle-v1",
    target: "article_analysis",
    status: "ready_for_eval",
    scope: "workflow_lab",
    few_shot_mode: "variant",
    manifest_json: {
      schema_version: "workflow-prompt-bundle-v1",
      variant_id: "workflow-bundle-v1",
      target: "article_analysis",
      description: "Bundle test",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      prompt_version: "prompts-v1",
      prompt_profile: "daily_intermediate",
      topology_mode: "learning",
      few_shot_mode: "variant",
      agents: {
        grammar: {
          agent_name: "grammar",
          label: "语法",
          instructions: "Grammar candidate instructions.",
          policy_name: "grammar",
          policy_focus: "balanced",
          policy_variant: "intermediate_reading",
          policy_lines: ["Prefer concise grammar notes."],
          examples: [{
            example_type: "grammar",
            sentence_text: "Variant sentence.",
            output_fragment: "{\"type\":\"grammar_note\"}",
          }],
        },
      },
    },
  });

  assert.equal(snapshot.prompt_bundle_summary.topology_mode, "learning");
  assert.equal(snapshot.prompt_override.instructions.grammar, "Grammar candidate instructions.");
  assert.deepEqual(
    snapshot.prompt_override.policies.grammar.balanced.intermediate_reading,
    ["Prefer concise grammar notes."],
  );
  assert.equal(snapshot.prompt_override.examples.grammar.intermediate_reading[0].sentence_text, "Variant sentence.");
  assert.equal(snapshot.prompt_override.prompt_snapshot_hash, snapshot.snapshot_hash);
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
      scope: "workflow_lab",
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
      scope: "workflow_lab",
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

test("createWorkflowLabSingleRun forwards baseline workflow request", async () => {
  let captured = null;
  const result = await createWorkflowLabSingleRun({
    database: createPromptVariantDb([]),
    env: {},
    body: {
      text: "Sentence one.",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      rag_mode: "off",
      trace_scope: "off",
      model_selection: { default_profile: "profile-a" },
      timeout_seconds: 90,
    },
    callUpstream: async (payload) => {
      captured = payload;
      return {
        workflow_identity: { workflow_name: "article_analysis" },
        render_scene: {
          schema_version: "3.0.0",
          user_facing_state: "normal",
          translations: [],
          inline_marks: [],
          sentence_entries: [],
          warnings: [],
        },
      };
    },
  });

  assert.equal(captured.path, "/eval/article-analysis/workflow");
  assert.equal(captured.body.text, "Sentence one.");
  assert.equal(captured.body.model_selection.default_profile, "profile-a");
  assert.equal(captured.body.prompt_variant_id, null);
  assert.equal(captured.body.prompt_override, null);
  assert.equal(result.status, "succeeded");
  assert.equal(result.prompt_identity.prompt_variant_id, null);
  assert.equal(result.render_scene.user_facing_state, "normal");
});

test("createWorkflowLabSingleRun attaches ready candidate snapshot", async () => {
  let upstreamBody = null;
  await createWorkflowLabSingleRun({
    database: createPromptVariantDb([
      {
        id: "draft-1",
        variant_id: "ready-workflow",
        target: "article_analysis",
        status: "ready_for_eval",
        scope: "workflow_lab",
        few_shot_mode: "baseline",
        manifest_json: {
          schema_version: "workflow-prompt-bundle-v1",
          variant_id: "ready-workflow",
          target: "article_analysis",
          reading_goal: "daily_reading",
          reading_variant: "intermediate_reading",
          topology_mode: "learning",
          few_shot_mode: "baseline",
          agents: {
            grammar: {
              agent_name: "grammar",
              instructions: "Candidate grammar instructions.",
              policy_name: "grammar",
              policy_focus: "balanced",
              policy_variant: "intermediate_reading",
              policy_lines: ["Candidate policy."],
              examples: [],
            },
          },
        },
      },
    ]),
    env: {},
    body: {
      text: "Sentence one.",
      prompt_variant_id: "ready-workflow",
      rag_mode: "off",
    },
    callUpstream: async ({ body }) => {
      upstreamBody = body;
      return { status: "succeeded" };
    },
  });

  assert.equal(upstreamBody.prompt_variant_id, "ready-workflow");
  assert.equal(upstreamBody.prompt_override.instructions.grammar, "Candidate grammar instructions.");
  assert.equal(upstreamBody.prompt_override.policies.grammar.balanced.intermediate_reading[0], "Candidate policy.");
  assert.ok(upstreamBody.prompt_override.prompt_snapshot_hash);
});

test("createWorkflowLabSingleRun rejects candidate with rag enabled", async () => {
  await assert.rejects(
    createWorkflowLabSingleRun({
      database: createPromptVariantDb([]),
      env: {},
      body: {
        text: "Sentence one.",
        prompt_variant_id: "ready-workflow",
        rag_mode: "rag",
      },
      callUpstream: async () => ({ status: "succeeded" }),
    }),
    /requires rag_mode=off/,
  );
});

// Batch 2 主路径入口:同一篇文章并发跑 baseline + candidate,直接产出 compare workspace
test("createWorkflowLabSingleRunCompare runs both sides concurrently and emits a compare workspace", async () => {
  const callOrder = [];
  const capturedBodies = [];
  const root = mkdtempSync(join(tmpdir(), "workflow-lab-single-run-compare-"));
  const database = createEvalCenterDb({
    eval_prompt_variant_drafts: [
      {
        id: "draft-ready-workflow",
        variant_id: "ready-workflow",
        target: "article_analysis",
        status: "ready_for_eval",
        scope: "workflow_lab",
        few_shot_mode: "baseline",
        manifest_json: {
          schema_version: "workflow-prompt-bundle-v1",
          variant_id: "ready-workflow",
          target: "article_analysis",
          description: "Candidate bundle",
          reading_goal: "daily_reading",
          reading_variant: "intermediate_reading",
          few_shot_mode: "baseline",
          topology_mode: "learning",
          agents: {
            grammar: {
              agent_name: "grammar",
              label: "语法",
              instructions: "Candidate grammar instructions.",
              policy_name: "grammar",
              policy_focus: "balanced",
              policy_variant: "intermediate_reading",
              policy_lines: ["Candidate policy."],
              examples: [],
            },
          },
          baseline_agents: {
            grammar: {
              agent_name: "grammar",
              label: "语法",
              instructions: "",
              policy_name: "grammar",
              policy_focus: "balanced",
              policy_variant: "intermediate_reading",
              policy_lines: [],
              examples: [],
            },
          },
        },
      },
    ],
  });
  const result = await createWorkflowLabSingleRunCompare({
    database,
    env: createWorkflowLabEnv(root),
    body: {
      text: "Concurrent dual-run article body for batch-2 single-run compare.",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      source_type: "user_input",
      rag_mode: "off",
      trace_scope: "off",
      timeout_seconds: 120,
      baseline: {},
      candidate: { prompt_variant_id: "ready-workflow" },
    },
    callUpstream: async ({ body, path }) => {
      callOrder.push(path);
      capturedBodies.push(body);
      return {
        status: "succeeded",
        prompt_identity: { prompt_variant_id: body.prompt_variant_id || null, prompt_snapshot_hash: "snap-stub" },
        model_identity: { profile_name: "qwen35-plus", model_name: "qwen35-plus" },
        render_scene: {
          schema_version: "3.0.0",
          user_facing_state: "normal",
          translations: [],
          inline_marks: [],
          sentence_entries: [],
          warnings: [],
        },
      };
    },
  });
  try {
    assert.equal(callOrder.length, 2);
    assert.ok(callOrder.every((path) => path === "/eval/article-analysis/workflow"));
    assert.ok(capturedBodies.every((body) => body.text.startsWith("Concurrent dual-run")));
    assert.ok(capturedBodies.every((body) => body.reading_goal === "daily_reading"));
    assert.equal(result.source, "persisted-compare");
    assert.ok(result.compare_id);
    assert.ok(result.baseline?.run_id);
    assert.ok(result.candidate?.run_id);
    assert.ok(result.compare?.report);
    assert.equal(result.compare.report.total_cases, 1);
    assert.ok(Array.isArray(result.compare.report.comparisons));
    assert.equal(result.compare.report.comparisons.length, 1);
    assert.ok(result.compare.input_hash);
    assert.equal(
      result.compare.baseline_artifact.case_id,
      result.compare.candidate_artifact.case_id,
    );
    assert.ok(result.compare.baseline_artifact.case_id.startsWith("single-run-"));
    assert.equal(result.input_snapshot.text, capturedBodies[0].text);
    assert.equal(database.tables.eval_workflow_compares.length, 1);
    assert.equal(existsSync(join(root, "workflow-compares", result.compare_id, "report.json")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("createWorkflowLabSingleRunCompare rejects when both sides resolve to the same prompt", async () => {
  await assert.rejects(
    createWorkflowLabSingleRunCompare({
      database: createPromptVariantDb([]),
      env: {},
      body: {
        text: "Same prompt on both sides should be rejected.",
        baseline: { prompt_variant_id: "ready-workflow" },
        candidate: { prompt_variant_id: "ready-workflow" },
        rag_mode: "off",
      },
      callUpstream: async () => ({ status: "succeeded" }),
    }),
    /must differ/,
  );
});

test("createWorkflowLabSingleRunCompare rejects when text is missing", async () => {
  await assert.rejects(
    createWorkflowLabSingleRunCompare({
      database: createPromptVariantDb([]),
      env: {},
      body: {
        text: "   ",
        baseline: {},
        candidate: { prompt_variant_id: "ready-workflow" },
        rag_mode: "off",
      },
      callUpstream: async () => ({ status: "succeeded" }),
    }),
    /text is required/,
  );
});

test("createWorkflowCompareJudgeRequest executes fake compare judge immediately and writes artifacts", async () => {
  const root = mkdtempSync(join(tmpdir(), "workflow-compare-judge-"));
  const database = createEvalCenterDb({
    eval_prompt_variant_drafts: [
      {
        id: "draft-ready-workflow",
        variant_id: "ready-workflow",
        target: "article_analysis",
        status: "ready_for_eval",
        scope: "workflow_lab",
        manifest_json: {
          schema_version: "workflow-prompt-bundle-v1",
          variant_id: "ready-workflow",
          reading_goal: "daily_reading",
          reading_variant: "intermediate_reading",
          topology_mode: "learning",
          agents: {
            grammar: {
              agent_name: "grammar",
              label: "语法",
              instructions: "Candidate grammar instructions.",
              policy_name: "grammar",
              policy_focus: "balanced",
              policy_variant: "intermediate_reading",
              policy_lines: ["Candidate policy."],
              examples: [],
            },
          },
          baseline_agents: {
            grammar: {
              agent_name: "grammar",
              label: "语法",
              instructions: "",
              policy_name: "grammar",
              policy_focus: "balanced",
              policy_variant: "intermediate_reading",
              policy_lines: [],
              examples: [],
            },
          },
        },
      },
    ],
  });

  const compareResult = await createWorkflowLabSingleRunCompare({
    database,
    env: createWorkflowLabEnv(root),
    body: {
      text: "Judge compare test article body.",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      source_type: "user_input",
      rag_mode: "off",
      trace_scope: "off",
      timeout_seconds: 120,
      baseline: {},
      candidate: { prompt_variant_id: "ready-workflow" },
    },
    callUpstream: async ({ body }) => ({
      status: "succeeded",
      prompt_identity: { prompt_variant_id: body.prompt_variant_id || null, prompt_snapshot_hash: "snap-stub" },
      model_identity: { profile_name: "qwen35-plus", model_name: "qwen35-plus" },
      render_scene: {
        schema_version: "3.0.0",
        user_facing_state: "normal",
        article: {
          sentences: [{ sentence_id: "s1", text: "Judge compare test article body." }],
        },
        translations: [{ sentence_id: "s1", translation_zh: body.prompt_variant_id ? "候选译文" : "基线译文" }],
        inline_marks: [],
        sentence_entries: [],
        warnings: [],
      },
    }),
  });

  const request = await createWorkflowCompareJudgeRequest(
    database,
    { accountability: { user: "00000000-0000-0000-0000-000000000001" } },
    createWorkflowLabEnv(root),
    compareResult.compare_id,
    {
      rubric_id: "article-analysis-language-quality-v1",
      judge_adapter_kind: "fake",
      config_json: { max_cases: 1 },
    },
  );

  try {
    assert.equal(request.status, "succeeded");
    const judgeDir = join(root, "workflow-compares", compareResult.compare_id, "judge", request.judge_run_id);
    assert.equal(existsSync(join(judgeDir, "judge-run.json")), true);
    assert.equal(existsSync(join(judgeDir, "case-results.json")), true);
    assert.equal(existsSync(join(judgeDir, "report.json")), true);
    const report = JSON.parse(readFileSync(join(judgeDir, "report.json"), "utf8"));
    assert.equal(report.total_cases, 1);
    assert.equal(
      report.candidate_preferred + report.baseline_preferred + report.tie + report.needs_review,
      1,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("syntheticSingleRunCompareRunId differs by side and by prompt context", () => {
  const body = { text: "Article", reading_goal: "daily_reading", reading_variant: "intermediate_reading" };
  const baselineId = syntheticSingleRunCompareRunId({ body, side: "baseline", promptVariantId: null });
  const candidateId = syntheticSingleRunCompareRunId({ body, side: "candidate", promptVariantId: "v1" });
  assert.notEqual(baselineId, candidateId);
  assert.ok(baselineId.startsWith("single-compare-baseline-"));
  assert.ok(candidateId.startsWith("single-compare-candidate-"));
});

test("buildSingleRunCaseArtifact binds case_id to input context", () => {
  const artifact = buildSingleRunCaseArtifact({
    body: {
      text: "Stable article body",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      source_type: "user_input",
    },
    result: { status: "succeeded", render_scene: { translations: [] } },
    runId: "synthetic-run",
  });
  assert.equal(artifact.case_id, "single-run-" + artifact.case_id.slice("single-run-".length));
  assert.ok(artifact.case_id.startsWith("single-run-"));
  assert.equal(artifact.run_id, "synthetic-run");
  assert.equal(artifact.adapter_status, "succeeded");
  assert.equal(artifact.input_snapshot.text, "Stable article body");
});

test("saveWorkflowLabSingleRunToHistory persists a standalone workflow run artifact", async () => {
  const runsRoot = mkdtempSync(join(tmpdir(), "workflow-single-history-"));
  const result = await saveWorkflowLabSingleRunToHistory({
    env: {
      CLAREAD_EVAL_RUNS_ROOT: runsRoot,
      CLAREAD_WORKFLOW_RUNTIME_RUNS_ROOT: runsRoot,
    },
    body: {
      request: {
        text: "Sentence one.",
        reading_goal: "daily_reading",
        reading_variant: "intermediate_reading",
        source_type: "user_input",
        rag_mode: "off",
        trace_scope: "off",
      },
      result: {
        status: "succeeded",
        prompt_identity: {
          prompt_variant_id: "workflow-ready",
          prompt_snapshot_hash: "snap-123",
        },
        model_identity: {
          profile_name: "qwen35-plus",
          model_name: "qwen35-plus",
        },
        runtime_summary: {
          latency_ms: 4321,
          aggregate: {
            total_tokens: 321,
            input_tokens: 123,
            output_tokens: 198,
          },
        },
        workflow_identity: { workflow_name: "article_analysis" },
        schema_identity: { schema_version: "workflow-artifact-v1" },
        render_scene: {
          user_facing_state: "normal",
          translations: [{ sentence_id: "s1", text: "译文" }],
          inline_marks: [],
          sentence_entries: [],
          warnings: [],
        },
      },
    },
  });

  assert.equal(result.duplicate, false);
  assert.equal(result.record.workspace_type, "workflow_single_run");
  assert.equal(result.record.prompt_variant_id, "workflow-ready");
  assert.ok(existsSync(join(runsRoot, result.record.run_id, "run.json")));
  assert.ok(existsSync(join(runsRoot, result.record.run_id, "report.json")));
  assert.ok(existsSync(join(runsRoot, result.record.run_id, "case-index.json")));
  // case_id 必须随 input / reading 上下文绑定,文件名也用绑定后的 case_id
  const caseDir = join(runsRoot, result.record.run_id, "cases");
  const caseFiles = readdirSync(caseDir);
  assert.equal(caseFiles.length, 1);
  assert.ok(caseFiles[0].startsWith("single-run-") && caseFiles[0].endsWith(".json"));
});

test("saveWorkflowLabSingleRunToHistory deduplicates the same single run payload", async () => {
  const runsRoot = mkdtempSync(join(tmpdir(), "workflow-single-history-dup-"));
  const payload = {
    request: {
      text: "Sentence one.",
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      source_type: "user_input",
      rag_mode: "off",
      trace_scope: "off",
    },
    result: {
      status: "succeeded",
      prompt_identity: {
        prompt_variant_id: "workflow-ready",
        prompt_snapshot_hash: "snap-123",
      },
      model_identity: { profile_name: "qwen35-plus" },
      runtime_summary: { latency_ms: 1111 },
      workflow_identity: { workflow_name: "article_analysis" },
      schema_identity: { schema_version: "workflow-artifact-v1" },
      render_scene: {
        user_facing_state: "normal",
        translations: [{ sentence_id: "s1", text: "译文" }],
        inline_marks: [],
        sentence_entries: [],
        warnings: [],
      },
    },
  };

  const first = await saveWorkflowLabSingleRunToHistory({
    env: {
      CLAREAD_EVAL_RUNS_ROOT: runsRoot,
      CLAREAD_WORKFLOW_RUNTIME_RUNS_ROOT: runsRoot,
    },
    body: payload,
  });
  const second = await saveWorkflowLabSingleRunToHistory({
    env: {
      CLAREAD_EVAL_RUNS_ROOT: runsRoot,
      CLAREAD_WORKFLOW_RUNTIME_RUNS_ROOT: runsRoot,
    },
    body: payload,
  });

  assert.equal(second.duplicate, true);
  assert.equal(first.record.run_id, second.record.run_id);
});

test("createWorkflowDataset writes dataset yaml and optional initial case from single run", async () => {
  const evalsRoot = mkdtempSync(join(tmpdir(), "workflow-datasets-create-"));
  const result = await createWorkflowDataset({
    env: { CLAREAD_EVALS_ROOT: evalsRoot },
    body: {
      dataset_id: "article-analysis-seeded",
      description: "Seeded from workflow single run.",
      tags: ["prompt", "learning-workflow"],
      initial_case: {
        request: {
          text: "A workflow single run sentence.",
          reading_goal: "daily_reading",
          reading_variant: "intermediate_reading",
          source_type: "user_input",
        },
        result: {
          render_scene: {
            request: {
              reading_goal: "daily_reading",
              reading_variant: "intermediate_reading",
            },
          },
        },
        case_id: "seed-case",
        tags: ["daily_reading", "intermediate_reading"],
        target_phenomena: ["phrase_gloss"],
        reference_notes: "Seed note.",
      },
    },
  });

  assert.equal(result.dataset.id, "article-analysis-seeded");
  assert.equal(result.dataset.case_count, 1);
  assert.equal(result.case.case_id, "seed-case");
  assert.ok(existsSync(join(evalsRoot, "datasets", "article-analysis-seeded", "dataset.yaml")));
  assert.ok(existsSync(join(evalsRoot, "datasets", "article-analysis-seeded", "cases", "seed-case.json")));
});

test("appendWorkflowDatasetCase adds a new dataset case and listWorkflowDatasets returns summaries", async () => {
  const evalsRoot = mkdtempSync(join(tmpdir(), "workflow-datasets-append-"));
  mkdirSync(join(evalsRoot, "datasets", "article-analysis-v1", "cases"), { recursive: true });
  writeFileSync(
    join(evalsRoot, "datasets", "article-analysis-v1", "dataset.yaml"),
    [
      "id: article-analysis-v1",
      "schema_version: eval-dataset-v1",
      "target: article_analysis",
      'description: "Existing dataset"',
      "case_globs:",
      "  - cases/*.json",
      "tags:",
      "  - prompt",
      "  - learning-workflow",
      "",
    ].join("\n"),
  );

  const appended = await appendWorkflowDatasetCase({
    env: { CLAREAD_EVALS_ROOT: evalsRoot },
    datasetId: "article-analysis-v1",
    body: {
      request: {
        text: "Another workflow sentence.",
        reading_goal: "exam",
        reading_variant: "kaoyan",
        source_type: "user_input",
      },
      result: {
        render_scene: {
          request: {
            reading_goal: "exam",
            reading_variant: "kaoyan",
          },
        },
      },
      case_id: "kaoyan-case-1",
      tags: ["exam", "kaoyan"],
    },
  });

  assert.equal(appended.case.case_id, "kaoyan-case-1");
  const summary = await readWorkflowDatasetSummary({ CLAREAD_EVALS_ROOT: evalsRoot }, "article-analysis-v1");
  assert.equal(summary.case_count, 1);
  assert.deepEqual(summary.tags, ["prompt", "learning-workflow"]);

  const datasets = await listWorkflowDatasets({ CLAREAD_EVALS_ROOT: evalsRoot });
  assert.equal(datasets.length, 1);
  assert.equal(datasets[0].description, "Existing dataset");
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

test("recoverStaleDirectusAsyncJudgeRequests marks stale running request succeeded when artifact is complete", async () => {
  const artifactsRoot = mkdtempSync(join(tmpdir(), "node-lab-stale-succeeded-"));
  const judgeDir = join(
    artifactsRoot,
    "sessions",
    "session-001",
    "trials",
    "trial-001",
    "judge",
    "judge-001",
  );
  mkdirSync(judgeDir, { recursive: true });
  writeFileSync(join(judgeDir, "judge-run.json"), JSON.stringify({ status: "complete" }));
  writeFileSync(join(judgeDir, "result.json"), JSON.stringify({ status: "succeeded" }));

  const database = createNodeLabJudgeRequestsDb([
    {
      judge_request_id: "judge-001",
      status: "running",
      lease_until: "2026-06-03T10:00:00.000Z",
      artifact_path: "evals/node-lab/sessions/session-001/trials/trial-001/judge/judge-001/result.json",
    },
  ]);

  try {
    const recovered = await recoverStaleDirectusAsyncJudgeRequests({
      database,
      env: {},
      resolveNodeLabArtifactsRoot: () => artifactsRoot,
      referenceTime: new Date("2026-06-03T11:00:00.000Z"),
    });

    assert.equal(recovered.length, 1);
    assert.equal(recovered[0].to_status, "succeeded");
    assert.equal(database.rows[0].status, "succeeded");
    assert.equal(database.rows[0].error_json, null);
    assert.ok(database.rows[0].finished_at);
  } finally {
    rmSync(artifactsRoot, { recursive: true, force: true });
  }
});

test("recoverStaleDirectusAsyncJudgeRequests marks stale running request failed when artifact is incomplete", async () => {
  const artifactsRoot = mkdtempSync(join(tmpdir(), "node-lab-stale-failed-"));
  const judgeDir = join(
    artifactsRoot,
    "sessions",
    "_standalone",
    "trials",
    "trial-002",
    "judge",
    "judge-002",
  );
  mkdirSync(judgeDir, { recursive: true });
  writeFileSync(join(judgeDir, "judge-run.json"), JSON.stringify({ status: "partial" }));

  const database = createNodeLabJudgeRequestsDb([
    {
      judge_request_id: "judge-002",
      status: "running",
      lease_until: null,
      artifact_path: "evals/node-lab/sessions/_standalone/trials/trial-002/judge/judge-002/result.json",
    },
  ]);

  try {
    const recovered = await recoverStaleDirectusAsyncJudgeRequests({
      database,
      env: {},
      resolveNodeLabArtifactsRoot: () => artifactsRoot,
      referenceTime: new Date("2026-06-03T11:00:00.000Z"),
    });

    assert.equal(recovered.length, 1);
    assert.equal(recovered[0].to_status, "failed");
    assert.equal(database.rows[0].status, "failed");
    assert.match(String(database.rows[0].error_json || ""), /StaleNodeLabJudgeRequest/);
    assert.ok(database.rows[0].finished_at);
  } finally {
    rmSync(artifactsRoot, { recursive: true, force: true });
  }
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
        review_note_count: 0,
        workspace_counts: { single_run: 0, baseline_compare: 1 },
        last_trial_id: "compare-standalone",
        last_trial_at: "2026-06-03T01:00:00.000Z",
        last_review_at: null,
      },
    );
  } finally {
    rmSync(artifactsRoot, { recursive: true, force: true });
  }
});

test("updateSessionAggregate promotes session to reviewed when session review notes exist", async () => {
  const database = createNodeLabRunHistoryDb({
    eval_node_lab_sessions: [
      {
        session_id: "session-001",
        status: "active",
        aggregate_summary_json: "{}",
      },
    ],
    eval_node_lab_trials: [
      {
        trial_id: "trial-001",
        session_id: "session-001",
        workspace_type: "baseline_compare",
        date_created: "2026-06-03T11:20:00.000Z",
      },
    ],
    eval_node_lab_review_notes: [
      {
        id: 1,
        target_type: "session",
        target_id: "session-001",
        date_created: "2026-06-03T11:30:00.000Z",
      },
    ],
  });

  await updateSessionAggregate(database, "session-001");

  assert.equal(database.tables.eval_node_lab_sessions[0].status, "reviewed");
  assert.deepEqual(
    JSON.parse(database.tables.eval_node_lab_sessions[0].aggregate_summary_json),
    {
      trial_count: 1,
      review_note_count: 1,
      workspace_counts: { single_run: 0, baseline_compare: 1 },
      last_trial_id: "trial-001",
      last_trial_at: "2026-06-03T11:20:00.000Z",
      last_review_at: "2026-06-03T11:30:00.000Z",
    },
  );
});

// ---------------------------------------------------------------------------
// Cross-layer enum drift guards (BE <-> FE useNodeLabConstants.ts).
//
// These tests read the BE-side source and assert that the constants are aligned
// with the FE-side expectations. If a BE constant changes, the test fails and
// forces the FE side (useNodeLabConstants.ts) to be updated together.
// FE file: apps/directus/extensions/modules-bundle/src/claread-eval-center/modes/node-lab/composables/useNodeLabConstants.ts
// ---------------------------------------------------------------------------

// endpoints-bundle/src/eval-center -> up 3 -> extensions/, then modules-bundle/...
const NODE_LAB_FE_CONSTANTS_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..",
  "modules-bundle", "src", "claread-eval-center", "modes", "node-lab", "composables", "useNodeLabConstants.ts",
);

function readFeConstantsSource() {
  return readFileSync(NODE_LAB_FE_CONSTANTS_PATH, "utf8");
}

function extractFeNodeIds(source) {
  // Matches `NODE_OPTIONS = [ { id: "grammar", ... }, ... ]` body
  const match = source.match(/NODE_OPTIONS\s*=\s*\[([\s\S]*?)\]\s*as const/);
  if (!match) throw new Error("NODE_OPTIONS block not found in FE constants");
  const ids = [];
  const idRe = /id:\s*"([a-z_]+)"/g;
  let m;
  while ((m = idRe.exec(match[1])) !== null) ids.push(m[1]);
  return ids;
}

function extractFeWorkspaceIds(source) {
  const match = source.match(/WORKSPACE_OPTIONS\s*=\s*\[([\s\S]*?)\]\s*as const/);
  if (!match) throw new Error("WORKSPACE_OPTIONS block not found in FE constants");
  const ids = [];
  const idRe = /id:\s*"([a-z_]+)"/g;
  let m;
  while ((m = idRe.exec(match[1])) !== null) ids.push(m[1]);
  return ids;
}

function extractFeJudgeModeIds(source) {
  const match = source.match(/JUDGE_MODES\s*=\s*\[([\s\S]*?)\]\s*as const/);
  if (!match) throw new Error("JUDGE_MODES block not found in FE constants");
  const ids = [];
  const idRe = /id:\s*"([a-z_]+)"/g;
  let m;
  while ((m = idRe.exec(match[1])) !== null) ids.push(m[1]);
  return ids;
}

function extractFeJudgeModeByNode(source) {
  // Pulls the whole JUDGE_MODES_BY_NODE block and extracts the id arrays.
  const match = source.match(/JUDGE_MODES_BY_NODE\s*=\s*\{([\s\S]*?)\}\s*as const/);
  if (!match) throw new Error("JUDGE_MODES_BY_NODE block not found in FE constants");
  const result = {};
  const keyRe = /(\w+):\s*\[([^\]]+)\]/g;
  let m;
  while ((m = keyRe.exec(match[1])) !== null) {
    const ids = [];
    const idRe = /"([a-z_]+)"/g;
    let im;
    while ((im = idRe.exec(m[2])) !== null) ids.push(im[1]);
    result[m[1]] = ids;
  }
  return result;
}

test("cross-layer: BE VALID_NODES is a subset of FE NODE_OPTIONS ids", () => {
  const feSource = readFeConstantsSource();
  const feIds = extractFeNodeIds(feSource);
  // Mirror the BE definition (single source of truth for the test is the BE constant).
  // If you change this array, also update modules-bundle/.../useNodeLabConstants.ts NODE_OPTIONS.
  const beValidNodes = ["grammar", "vocabulary", "translation"];

  for (const id of beValidNodes) {
    assert.ok(feIds.includes(id), `FE NODE_OPTIONS missing node id "${id}" — update useNodeLabConstants.ts to keep BE/FE aligned.`);
  }
  assert.deepEqual(beValidNodes.sort(), feIds.slice().sort(), "BE/FE node id list must stay in sync");
});

test("cross-layer: BE VALID_WORKSPACES is a subset of FE WORKSPACE_OPTIONS ids", () => {
  const feSource = readFeConstantsSource();
  const feIds = extractFeWorkspaceIds(feSource);
  // Mirror the BE definition. "sessions" is FE-only (UI entry) — see node-lab.js
  // VALID_WORKSPACES comment. The two are NOT a strict 1:1 match; we just assert
  // the BE list is fully present in the FE list.
  const beValidWorkspaces = ["single_run", "baseline_compare"];

  for (const id of beValidWorkspaces) {
    assert.ok(feIds.includes(id), `FE WORKSPACE_OPTIONS missing workspace id "${id}"`);
  }
  // "sessions" must be FE-only (not in BE).
  assert.ok(feIds.includes("sessions"), "FE WORKSPACE_OPTIONS must still expose 'sessions' as a UI entry");
  for (const id of beValidWorkspaces) {
    assert.ok(feIds.includes(id));
  }
});

test("cross-layer: BE VALID_JUDGE_MODES is a subset of FE JUDGE_MODES", () => {
  const feSource = readFeConstantsSource();
  const feJudgeModes = extractFeJudgeModeIds(feSource);
  // Mirror the BE definition. After 决策 1 (2026-06) persona_pairwise 已撤回，
  // BE/FE 都只允许 4 项；如未来要加回，需同时更新 BE / FE / worker 端 Pydantic Literal。
  const beValidJudgeModes = [
    "rubric_score_only",
    "rubric_plus_pairwise",
    "anti_template_probe",
    "raw",
  ];

  for (const id of beValidJudgeModes) {
    assert.ok(
      feJudgeModes.includes(id),
      `FE JUDGE_MODES missing judge_mode id "${id}" — update useNodeLabConstants.ts to keep BE/FE aligned.`,
    );
  }
  // persona_pairwise 应不在 BE / FE 任何一端（决策 1 已撤回）。
  assert.ok(
    !feJudgeModes.includes("persona_pairwise"),
    "FE JUDGE_MODES should no longer contain 'persona_pairwise' (decision 1.A — rolled back)",
  );
});

test("cross-layer: FE JUDGE_MODES_BY_NODE.grammar exposes 'raw' (BE VALID_JUDGE_MODES already allows it)", () => {
  const feSource = readFeConstantsSource();
  const byNode = extractFeJudgeModeByNode(feSource);
  assert.ok(
    byNode.grammar?.includes("raw"),
    "FE JUDGE_MODES_BY_NODE.grammar must include 'raw' so users can pick the mode BE accepts",
  );
});
