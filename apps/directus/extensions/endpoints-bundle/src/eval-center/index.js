import { createHash, randomUUID } from "node:crypto";
import { mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { registerNodeLabRoutes } from "./node-lab.js";
import { registerExampleLabRoutes } from "./example-lab.js";

function buildAuthGuard(req, res) {
  const accountability = req.accountability;
  if (!accountability?.user || accountability?.admin !== true) {
    res.status(403).json({
      errors: [
        {
          message: "Directus admin access required.",
          extensions: { code: "FORBIDDEN" },
        },
      ],
    });
    return false;
  }
  return true;
}

function readEnv(env, key) {
  return env?.[key] ?? process.env[key] ?? "";
}

function joinUrl(baseUrl, path) {
  return `${String(baseUrl).replace(/\/+$/, "")}/${String(path).replace(/^\/+/, "")}`;
}

function resolveTimeoutMs(env) {
  const parsed = Number.parseInt(readEnv(env, "CLAREAD_EVAL_PROXY_TIMEOUT_MS") || "60000", 10);
  if (!Number.isFinite(parsed) || parsed < 1000) return 60000;
  return Math.min(parsed, 600000);
}

function resolveRequestTimeoutMs(env, body) {
  const proxyTimeoutMs = resolveTimeoutMs(env);
  const requestTimeoutSeconds = Number(body?.timeout_seconds);
  if (!Number.isFinite(requestTimeoutSeconds) || requestTimeoutSeconds <= 0) {
    return proxyTimeoutMs;
  }
  return Math.min(Math.max(proxyTimeoutMs, requestTimeoutSeconds * 1000 + 10000), 600000);
}

function clampLimit(value) {
  const parsed = Number.parseInt(String(value ?? "30"), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return 30;
  return Math.min(parsed, 100);
}

function isSafeFileId(value) {
  return (
    typeof value === "string"
    && value.length > 0
    && value.length <= 160
    && /^[A-Za-z0-9._-]+$/.test(value)
    && !value.includes("..")
    && !value.startsWith(".")
    && !value.endsWith(".")
  );
}

function resolveRunsRoot(env) {
  return readEnv(env, "CLAREAD_EVAL_RUNS_ROOT") || "/directus/evals/runs";
}

function resolveWorkflowRuntimeRunsRoot(env) {
  const explicit = readEnv(env, "CLAREAD_WORKFLOW_RUNTIME_RUNS_ROOT");
  if (explicit) return explicit;
  const nodeLabRoot = resolveNodeLabArtifactsRoot(env);
  return path.join(path.dirname(nodeLabRoot), "workflow-runs");
}

function resolveWorkflowCompareRuntimeRoot(env) {
  const explicit = readEnv(env, "CLAREAD_WORKFLOW_COMPARE_RUNTIME_ROOT");
  if (explicit) return explicit;
  const workflowRunsRoot = resolveWorkflowRuntimeRunsRoot(env);
  return path.join(path.dirname(workflowRunsRoot), "workflow-compares");
}

function uniquePaths(items) {
  return [...new Set(items.filter(Boolean).map((item) => path.resolve(item)))];
}

function resolveWorkflowRunRoots(env) {
  return uniquePaths([
    resolveWorkflowRuntimeRunsRoot(env),
    resolveRunsRoot(env),
  ]);
}

function workflowRunArtifactPrefix(root, runtimeRoot) {
  const resolvedRuntimeRoot = path.resolve(runtimeRoot);
  const candidateRoot = path.resolve(root);
  if (candidateRoot === resolvedRuntimeRoot) {
    return "runtime-evals/workflow-runs";
  }
  return "evals/runs";
}

function workflowCompareArtifactPrefix(root, runtimeRoot) {
  const resolvedRuntimeRoot = path.resolve(runtimeRoot);
  const candidateRoot = path.resolve(root);
  if (candidateRoot === resolvedRuntimeRoot) {
    return "runtime-evals/workflow-compares";
  }
  return "runtime-evals/workflow-compares";
}

function runDir(root, runId) {
  if (!isSafeFileId(runId)) {
    const error = new Error("Invalid run id.");
    error.status = 400;
    error.code = "INVALID_RUN_ID";
    throw error;
  }
  return path.join(root, runId);
}

function caseArtifactPath(root, runId, caseId) {
  if (!isSafeFileId(caseId)) {
    const error = new Error("Invalid case id.");
    error.status = 400;
    error.code = "INVALID_CASE_ID";
    throw error;
  }
  return path.join(runDir(root, runId), "cases", `${caseId}.json`);
}

function caseIndexPath(root, runId) {
  return path.join(runDir(root, runId), "case-index.json");
}

function compareDir(root, compareId) {
  if (!isSafeFileId(compareId)) {
    const error = new Error("Invalid compare id.");
    error.status = 400;
    error.code = "INVALID_COMPARE_ID";
    throw error;
  }
  return path.join(root, compareId);
}

function compareArtifactPath(root, compareId, filename) {
  return path.join(compareDir(root, compareId), filename);
}

function compareJudgeArtifactDir(root, compareId, judgeRunId) {
  if (!isSafeFileId(judgeRunId)) {
    const error = new Error("Invalid workflow compare judge_run_id.");
    error.status = 400;
    error.code = "INVALID_WORKFLOW_COMPARE_JUDGE_RUN_ID";
    throw error;
  }
  return path.join(compareDir(root, compareId), "judge", judgeRunId);
}



function judgeArtifactDir(root, runId, judgeRunId) {
  if (!isSafeFileId(judgeRunId)) {
    const error = new Error("Invalid judge run id.");
    error.status = 400;
    error.code = "INVALID_JUDGE_RUN_ID";
    throw error;
  }
  return path.join(runDir(root, runId), "judge", judgeRunId);
}

async function readJsonFile(filePath) {
  try {
    const data = await readFile(filePath, "utf8");
    const parsed = JSON.parse(data);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      const error = new Error("JSON file must contain an object.");
      error.status = 422;
      error.code = "INVALID_EVAL_ARTIFACT";
      throw error;
    }
    return parsed;
  } catch (error) {
    if (error?.code === "ENOENT") {
      const notFound = new Error("Eval artifact not found.");
      notFound.status = 404;
      notFound.code = "EVAL_ARTIFACT_NOT_FOUND";
      throw notFound;
    }
    throw error;
  }
}

async function fileExists(filePath) {
  try {
    await stat(filePath);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function removePathIfExists(targetPath) {
  if (!targetPath) return;
  try {
    await rm(targetPath, { recursive: true, force: true });
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

async function writeJsonFile(filePath, payload) {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

async function countJsonFiles(dirPath) {
  try {
    const entries = await readdir(dirPath, { withFileTypes: true });
    return entries.filter((entry) => entry.isFile() && entry.name.endsWith(".json")).length;
  } catch (error) {
    if (error?.code === "ENOENT") return 0;
    throw error;
  }
}

async function listJsonIds(dirPath) {
  try {
    const entries = await readdir(dirPath, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
      .map((entry) => entry.name.slice(0, -5))
      .sort();
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

async function findExistingRunRoot(roots, runId) {
  const candidates = Array.isArray(roots) ? roots : [roots];
  for (const root of candidates) {
    if (!root) continue;
    if (await fileExists(runDir(root, runId))) {
      return root;
    }
  }
  return null;
}

async function resolveRunRootOrThrow(roots, runId) {
  const root = await findExistingRunRoot(roots, runId);
  if (root) return root;
  const error = new Error("Eval artifact not found.");
  error.status = 404;
  error.code = "EVAL_ARTIFACT_NOT_FOUND";
  throw error;
}

function summarizeRun(run, report, counts) {
  const runId = run.run_id || report?.run_id;
  const usageSummary = run.usage_summary || null;
  return {
    run_id: runId,
    dataset_id: run.dataset_id || report?.dataset_id || null,
    mode: run.mode || "workflow",
    eval_purpose: run.eval_purpose || null,
    created_at: report?.created_at || run.created_at || null,
    prompt_version: run.prompt_version || null,
    prompt_variant_id: run.prompt_variant_id || null,
    workflow_version: run.workflow_version || null,
    rag_mode: run.rag_mode || null,
    trace_scope: run.trace_scope || null,
    topology_mode: counts.topology_mode || null,
    has_report: Boolean(report),
    total_cases: report?.total_cases ?? counts.case_artifact_count,
    passed: report?.passed ?? null,
    failed: report?.failed ?? null,
    errored: report?.errored ?? null,
    hard_failure_count: Array.isArray(report?.hard_failure_case_ids)
      ? report.hard_failure_case_ids.length
      : null,
    soft_failure_count: Array.isArray(report?.soft_failure_case_ids)
      ? report.soft_failure_case_ids.length
      : null,
    regression_count: Array.isArray(report?.regression_list)
      ? report.regression_list.length
      : null,
    case_artifact_count: counts.case_artifact_count,
    learning_case_count: counts.learning_case_count ?? null,
    mode: run.mode || "workflow",
    judge_report_count: counts.judge_report_count ?? 0,
    model_identity: run.model_identity || null,
    model_name: run.model_identity?.model_name || run.model_name || null,
    model_profile: run.model_identity?.profile_name || run.model_profile || null,
    latency_seconds: run.latency_seconds ?? report?.latency_seconds ?? null,
    total_tokens: run.total_tokens ?? usageSummary?.total_tokens ?? report?.total_tokens ?? null,
    input_tokens: usageSummary?.input_tokens ?? null,
    output_tokens: usageSummary?.output_tokens ?? null,
    usage_summary: usageSummary,
    custom_title: run.custom_title || null,
  };
}

function workflowHistoryStatus(summary) {
  if (!summary?.has_report) return "failed";
  const totalCases = Number(summary.total_cases || 0);
  const errored = Number(summary.errored || 0);
  const hardFailures = Number(summary.hard_failure_count || 0);
  const softFailures = Number(summary.soft_failure_count || 0);
  const regressions = Number(summary.regression_count || 0);
  if (totalCases > 0 && errored >= totalCases) return "failed";
  if (errored > 0 || hardFailures > 0 || softFailures > 0 || regressions > 0) return "partial_failure";
  return "complete";
}

function workflowHistoryRecord(summary) {
  const promptVariantId = summary?.prompt_variant_id || "";
  return {
    source: "workflow",
    record_id: summary?.run_id || "",
    run_id: summary?.run_id || "",
    status: workflowHistoryStatus(summary),
    workspace_type: summary?.mode === "workflow_single_run" ? "workflow_single_run" : "workflow_dataset_run",
    result_kind: summary?.mode === "workflow_single_run" ? "workflow_single_run_result" : "workflow_run_result",
    dataset_id: summary?.dataset_id || null,
    prompt_variant_id: promptVariantId || null,
    topology_mode: summary?.topology_mode || null,
    total_cases: summary?.total_cases ?? 0,
    learning_case_count: summary?.learning_case_count ?? 0,
    hard_failure_count: summary?.hard_failure_count ?? 0,
    soft_failure_count: summary?.soft_failure_count ?? 0,
    regression_count: summary?.regression_count ?? 0,
    judge_report_count: summary?.judge_report_count ?? 0,
    created_at: summary?.created_at || null,
    date_created: summary?.created_at || null,
    display_title: summary?.custom_title || (summary?.mode === "workflow_single_run"
      ? `${promptVariantId || "baseline"} · single run`
      : `${promptVariantId || "baseline"} · ${summary?.dataset_id || "dataset"}`),
    display_excerpt: [
      summary?.mode === "workflow_single_run" ? "single run" : null,
      summary?.topology_mode || null,
      Number(summary?.learning_case_count || 0) > 0 ? `${summary.learning_case_count} learning` : null,
      Number(summary?.judge_report_count || 0) > 0 ? `${summary.judge_report_count} judge` : null,
    ].filter(Boolean).join(" · "),
    custom_title: summary?.custom_title || null,
    model_name: summary?.model_identity?.model_name || summary?.model_name || null,
    model_profile: summary?.model_identity?.profile_name || summary?.model_profile || null,
    latency_seconds: summary?.latency_seconds ?? null,
    total_tokens: summary?.total_tokens ?? summary?.usage_summary?.total_tokens ?? null,
  };
}

function topologyFromIdentity(identity) {
  const topologyMode = identity?.topology_mode;
  return typeof topologyMode === "string" && topologyMode ? topologyMode : null;
}

function topologyFromCaseArtifact(artifact) {
  return (
    topologyFromIdentity(artifact?.workflow_identity)
    || topologyFromIdentity(artifact?.schema_identity)
    || null
  );
}

function inferRunTopologyMode(caseArtifacts) {
  if (!Array.isArray(caseArtifacts) || caseArtifacts.length === 0) return null;
  const topologies = new Set(
    caseArtifacts
      .map((artifact) => topologyFromCaseArtifact(artifact))
      .filter(Boolean),
  );
  if (topologies.size === 1) return Array.from(topologies)[0];
  if (topologies.size > 1) return "mixed";
  return null;
}

function countRunTopologies(caseArtifacts) {
  const items = Array.isArray(caseArtifacts) ? caseArtifacts : [];
  let learning = 0;
  for (const artifact of items) {
    if (topologyFromCaseArtifact(artifact) === "learning") learning += 1;
  }
  return {
    learning_case_count: learning,
  };
}

function filterLearningArtifacts(caseArtifacts) {
  return (Array.isArray(caseArtifacts) ? caseArtifacts : []).filter(
    (artifact) => topologyFromCaseArtifact(artifact) === "learning",
  );
}

function summarizeCaseArtifact(artifact) {
  const graderResults = Array.isArray(artifact.grader_results) ? artifact.grader_results : [];
  const failedGraders = graderResults.filter((result) => result?.verdict === "fail");
  const hardFailures = failedGraders.filter((result) => result?.severity === "hard").length;
  const softFailures = failedGraders.filter((result) => result?.severity === "soft").length;
  return {
    case_id: artifact.case_id,
    run_id: artifact.run_id,
    adapter_status: artifact.adapter_status,
    user_facing_state: artifact.user_facing_state ?? null,
    error: artifact.error
      ? {
          code: artifact.error.code,
          message: artifact.error.message,
        }
      : null,
    warning_count: Array.isArray(artifact.warnings) ? artifact.warnings.length : 0,
    drop_count: Array.isArray(artifact.drop_log) ? artifact.drop_log.length : 0,
    hard_failures: hardFailures,
    soft_failures: softFailures,
    grader_count: graderResults.length,
    failed_grader_count: failedGraders.length,
    grader_summaries: graderResults.map((result) => ({
      grader_name: result?.grader_name ?? null,
      verdict: result?.verdict ?? null,
      severity: result?.severity ?? null,
      metric: result?.metric ?? null,
      evidence: result?.evidence ?? result?.reason ?? result?.message ?? null,
    })),
    translation_count: Array.isArray(artifact.translations) ? artifact.translations.length : 0,
    inline_mark_count: Array.isArray(artifact.inline_marks) ? artifact.inline_marks.length : 0,
    sentence_entry_count: Array.isArray(artifact.sentence_entries) ? artifact.sentence_entries.length : 0,
    latency_seconds: artifact.latency_seconds ?? null,
    total_tokens: artifact.usage_summary?.total_tokens ?? null,
    input_tokens: artifact.usage_summary?.input_tokens ?? null,
    output_tokens: artifact.usage_summary?.output_tokens ?? null,
    workflow_identity: artifact.workflow_identity ?? null,
    schema_identity: artifact.schema_identity ?? null,
    prompt_identity: artifact.prompt_identity ?? null,
    model_identity: artifact.model_identity ?? null,
  };
}

async function loadRunSummary(roots, runId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const dir = runDir(root, runId);
  const run = await readJsonFile(path.join(dir, "run.json"));
  const reportPath = path.join(dir, "report.json");
  const report = (await fileExists(reportPath)) ? await readJsonFile(reportPath) : null;
  const caseIndex = await loadCaseIndex(root, runId);
  const caseSummaries = caseIndex?.cases || [];
  const topologyCounts = countRunTopologies(caseSummaries);
  return summarizeRun(run, report, {
    case_artifact_count: caseIndex?.total_cases ?? await countJsonFiles(path.join(dir, "cases")),
    judge_report_count: await countJudgeArtifactDirs(path.join(dir, "judge")),
    topology_mode: inferRunTopologyMode(caseSummaries),
    ...topologyCounts,
  });
}

async function listRuns(roots, limit) {
  const candidates = Array.isArray(roots) ? roots : [roots];
  const summaries = [];
  const seen = new Set();

  for (const root of candidates) {
    let entries;
    try {
      entries = await readdir(root, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT") continue;
      throw error;
    }

    for (const entry of entries) {
      if (!entry.isDirectory() || !isSafeFileId(entry.name) || seen.has(entry.name)) continue;
      try {
        summaries.push(await loadRunSummary(root, entry.name));
        seen.add(entry.name);
      } catch {
        // Invalid or partial run directories are ignored in list view and can be inspected manually.
      }
    }
  }

  return summaries
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
    .slice(0, limit);
}

async function loadRunDetail(roots, runId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const dir = runDir(root, runId);
  const run = await readJsonFile(path.join(dir, "run.json"));
  const reportPath = path.join(dir, "report.json");
  const report = (await fileExists(reportPath)) ? await readJsonFile(reportPath) : null;
  const caseIndex = await loadCaseIndex(root, runId);
  const caseSummaries = caseIndex?.cases ?? await loadCaseArtifactSummaries(root, runId, dir);
  const judgeReports = await listJudgeArtifacts(root, runId);
  const topologyCounts = countRunTopologies(caseSummaries);

  return {
    summary: summarizeRun(run, report, {
      case_artifact_count: caseSummaries.length,
      judge_report_count: judgeReports.length,
      topology_mode: inferRunTopologyMode(caseSummaries),
      ...topologyCounts,
    }),
    run,
    report,
    case_index: caseIndex
      ? {
          schema_version: caseIndex.schema_version,
          generated_at: caseIndex.generated_at,
          total_cases: caseIndex.total_cases,
        }
      : null,
    case_artifacts: caseSummaries,
    judge_reports: judgeReports,
  };
}

async function loadWorkflowRunHistoryRecords(roots, limit) {
  const runs = await listRuns(roots, limit);
  return runs.map((summary) => workflowHistoryRecord(summary));
}

async function loadWorkflowRunHistoryDetail(roots, runId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const detail = await loadRunDetail(roots, runId);
  const fullCaseArtifacts = detail.summary?.mode === "workflow_single_run"
    ? await loadRunCaseArtifacts(roots, runId)
    : [];
  const judgeReports = [];
  for (const judgeReport of detail.judge_reports || []) {
    try {
      judgeReports.push(await loadJudgeArtifact(roots, runId, judgeReport.judge_run_id || judgeReport.id));
    } catch {
      judgeReports.push({ summary: judgeReport, report: null, case_results: null, packets: [] });
    }
  }
  return {
    source: "workflow",
    record: workflowHistoryRecord(detail.summary),
    summary: detail.summary,
    run: detail.run,
    report: detail.report,
    case_index: detail.case_index,
    case_artifacts: detail.case_artifacts || [],
    full_case_artifacts: fullCaseArtifacts,
    judge_reports: judgeReports,
  };
}

async function deleteWorkflowRunCascade(database, roots, runId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const runPath = runDir(root, runId);

  const workflowRequestRows = await database("eval_workflow_run_requests")
    .where({ run_id: runId })
    .orWhere({ artifact_run_id: runId })
    .select("id", "run_id");
  const workflowRequestIds = workflowRequestRows.map((row) => row.id).filter(Boolean);
  if (workflowRequestIds.length) {
    await database("eval_workflow_run_requests")
      .whereIn("source_request_id", workflowRequestIds)
      .update({ source_request_id: null });
  }

  const judgeRequestRows = await database("eval_judge_run_requests")
    .where({ run_id: runId })
    .select("id", "judge_run_id");
  const judgeRequestIds = judgeRequestRows.map((row) => row.id).filter(Boolean);
  if (judgeRequestIds.length) {
    await database("eval_judge_run_requests")
      .whereIn("source_request_id", judgeRequestIds)
      .update({ source_request_id: null });
  }

  await database("eval_review_notes")
    .where({ run_id: runId })
    .orWhere((builder) => builder.where({ target_type: "workflow_run", target_id: runId }))
    .del();

  if (judgeRequestRows.length) {
    await database("eval_judge_run_requests").where({ run_id: runId }).del();
  }
  if (workflowRequestRows.length) {
    await database("eval_workflow_run_requests")
      .where({ run_id: runId })
      .orWhere({ artifact_run_id: runId })
      .del();
  }

  await removePathIfExists(runPath);
  return {
    run_id: runId,
    deleted_workflow_request_count: workflowRequestRows.length,
    deleted_judge_request_count: judgeRequestRows.length,
    deleted_artifact_path: path.basename(runPath),
  };
}

async function loadRunCaseArtifacts(roots, runId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const dir = runDir(root, runId);
  const caseIds = await listJsonIds(path.join(dir, "cases"));
  if (caseIds.length === 0) {
    const error = new Error(`Run "${runId}" has no case artifacts.`);
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_ERROR";
    throw error;
  }

  const artifacts = [];
  const seen = new Set();
  for (const caseId of caseIds) {
    const artifact = await readJsonFile(caseArtifactPath(root, runId, caseId));
    if (artifact.case_id !== caseId) {
      const error = new Error(`Case artifact filename mismatch: ${caseId} != ${artifact.case_id || "<missing>"}.`);
      error.status = 422;
      error.code = "WORKFLOW_LAB_COMPARE_ERROR";
      throw error;
    }
    if (artifact.run_id !== runId) {
      const error = new Error(`Case artifact run_id mismatch: ${artifact.run_id || "<missing>"} != ${runId}.`);
      error.status = 422;
      error.code = "WORKFLOW_LAB_COMPARE_ERROR";
      throw error;
    }
    if (seen.has(caseId)) {
      const error = new Error(`Duplicate case artifact: ${caseId}.`);
      error.status = 422;
      error.code = "WORKFLOW_LAB_COMPARE_ERROR";
      throw error;
    }
    seen.add(caseId);
    artifacts.push(artifact);
  }
  return artifacts;
}

async function loadRunForWorkflowLabCompare(roots, runId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const dir = runDir(root, runId);
  const run = await readJsonFile(path.join(dir, "run.json"));
  const reportPath = path.join(dir, "report.json");
  if (!(await fileExists(reportPath))) {
    const error = new Error(`Run "${runId}" is missing report.json.`);
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_ERROR";
    throw error;
  }
  const report = await readJsonFile(reportPath);
  const artifacts = await loadRunCaseArtifacts(root, runId);
  const learningArtifacts = filterLearningArtifacts(artifacts);
  if (learningArtifacts.length === 0) {
    const topologyMode = inferRunTopologyMode(artifacts);
    const error = new Error(`Workflow Lab compare only supports runs with learning cases. "${runId}" topology is ${topologyMode || "unknown"}.`);
    error.status = 422;
    error.code = "WORKFLOW_LAB_TOPOLOGY_UNSUPPORTED";
    throw error;
  }
  return {
    dir,
    run_id: run.run_id || runId,
    dataset_id: run.dataset_id || report.dataset_id || null,
    run,
    report,
    artifacts: learningArtifacts,
    topology_mode: inferRunTopologyMode(artifacts),
  };
}

function workflowLabFailureCounts(artifact) {
  const failed = Array.isArray(artifact?.grader_results)
    ? artifact.grader_results.filter((result) => result?.verdict === "fail")
    : [];
  return {
    hard: failed.filter((result) => result?.severity === "hard").length,
    soft: failed.filter((result) => result?.severity === "soft").length,
  };
}

function workflowLabIdentitySnapshot(artifact) {
  return {
    workflow_identity: artifact?.workflow_identity || {},
    schema_identity: artifact?.schema_identity || {},
    prompt_identity: artifact?.prompt_identity || {},
    model_identity: artifact?.model_identity || {},
  };
}

function stableCompareJson(value) {
  return JSON.stringify(value ?? null);
}

function workflowLabIdentityDelta(baselineArtifact, candidateArtifact) {
  const baseline = workflowLabIdentitySnapshot(baselineArtifact);
  const candidate = workflowLabIdentitySnapshot(candidateArtifact);
  const delta = {};
  for (const key of Object.keys(baseline)) {
    if (stableCompareJson(baseline[key]) !== stableCompareJson(candidate[key])) {
      delta[key] = {
        baseline: baseline[key],
        candidate: candidate[key],
      };
    }
  }
  return Object.keys(delta).length ? delta : null;
}

function compareWorkflowLabCaseArtifacts(baselineArtifact, candidateArtifact) {
  const baselineFailures = workflowLabFailureCounts(baselineArtifact);
  const candidateFailures = workflowLabFailureCounts(candidateArtifact);
  const reasons = [];

  if (candidateArtifact.adapter_status !== baselineArtifact.adapter_status) {
    reasons.push(`adapter_status changed: ${baselineArtifact.adapter_status} -> ${candidateArtifact.adapter_status}`);
  }

  let verdict = "tie";
  if (candidateFailures.hard > baselineFailures.hard) {
    verdict = "loss";
    reasons.push(`hard failures increased: ${baselineFailures.hard} -> ${candidateFailures.hard}`);
  } else if (candidateFailures.hard < baselineFailures.hard) {
    verdict = "win";
    reasons.push(`hard failures decreased: ${baselineFailures.hard} -> ${candidateFailures.hard}`);
  } else if (candidateFailures.soft > baselineFailures.soft) {
    verdict = "loss";
    reasons.push(`soft failures increased: ${baselineFailures.soft} -> ${candidateFailures.soft}`);
  } else if (candidateFailures.soft < baselineFailures.soft) {
    verdict = "win";
    reasons.push(`soft failures decreased: ${baselineFailures.soft} -> ${candidateFailures.soft}`);
  } else if (candidateArtifact.error && !baselineArtifact.error) {
    verdict = "loss";
    reasons.push("candidate introduced adapter error");
  } else if (baselineArtifact.error && !candidateArtifact.error) {
    verdict = "win";
    reasons.push("candidate fixed adapter error");
  } else if (candidateArtifact.adapter_status === "timeout" && baselineArtifact.adapter_status !== "timeout") {
    verdict = "loss";
    reasons.push("candidate introduced timeout");
  }

  if (reasons.length === 0) reasons.push("no deterministic delta");

  return {
    case_id: baselineArtifact.case_id,
    comparison_kind: "artifact",
    source_case_id: baselineArtifact.case_id,
    verdict,
    baseline_hard_failures: baselineFailures.hard,
    candidate_hard_failures: candidateFailures.hard,
    baseline_soft_failures: baselineFailures.soft,
    candidate_soft_failures: candidateFailures.soft,
    baseline_status: baselineArtifact.adapter_status || null,
    candidate_status: candidateArtifact.adapter_status || null,
    identity_delta: workflowLabIdentityDelta(baselineArtifact, candidateArtifact),
    reasons,
  };
}

function workflowSceneFromArtifact(artifact) {
  if (artifact?.render_scene && typeof artifact.render_scene === "object") return artifact.render_scene;
  return artifact && typeof artifact === "object" ? artifact : {};
}

function artifactSentenceTextMap(artifact) {
  const map = new Map();
  const scene = workflowSceneFromArtifact(artifact);
  const candidates = [
    scene?.article?.sentences,
    artifact?.output?.article?.sentences,
    artifact?.input_snapshot?.article?.sentences,
    artifact?.prepared_sentences,
    artifact?.input_snapshot?.prepared_sentences,
  ];
  for (const items of candidates) {
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      const sid = item?.sentence_id;
      const text = item?.text || item?.source_text || item?.original_text || "";
      if (sid != null && text && !map.has(String(sid))) {
        map.set(String(sid), String(text));
      }
    }
  }
  return map;
}

function artifactTranslationMap(artifact) {
  const map = new Map();
  const scene = workflowSceneFromArtifact(artifact);
  const items = Array.isArray(scene?.translations) ? scene.translations : Array.isArray(artifact?.translations) ? artifact.translations : [];
  for (const item of items) {
    const sid = item?.sentence_id;
    const text = item?.translation_zh || item?.text || "";
    if (sid != null && text && !map.has(String(sid))) {
      map.set(String(sid), String(text));
    }
  }
  return map;
}

function normalizeInlineMarkText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function inlineMarkAnchorText(mark) {
  if (!mark || typeof mark !== "object") return "";
  const anchor = mark.anchor;
  if (typeof anchor === "string" && anchor.trim()) return anchor.trim();
  if (anchor && typeof anchor === "object") {
    if (anchor.kind === "multi_text" && Array.isArray(anchor.parts)) {
      const parts = anchor.parts
        .map((item) => normalizeInlineMarkText(item?.anchor_text || item?.anchorText || item?.text))
        .filter(Boolean);
      if (parts.length) return parts.join(" / ");
    }
    const single = normalizeInlineMarkText(anchor.anchor_text || anchor.anchorText || anchor.text);
    if (single) return single;
  }
  return normalizeInlineMarkText(mark.anchor_text || mark.anchorText);
}

function inlineMarkTitleText(mark) {
  if (!mark || typeof mark !== "object") return "";
  const title = normalizeInlineMarkText(
    mark.title
    || mark.lookup_text
    || mark.lookupText
    || mark.text,
  );
  return title || inlineMarkAnchorText(mark);
}

function inlineMarkLookupKind(mark) {
  if (!mark || typeof mark !== "object") return "";
  const glossary = mark.glossary && typeof mark.glossary === "object" ? mark.glossary : null;
  return normalizeInlineMarkText(
    mark.lookup_kind
    || mark.lookupKind
    || glossary?.phrase_type
    || glossary?.phraseType,
  );
}

function inlineMarkExtraText(mark) {
  if (!mark || typeof mark !== "object") return "";
  const glossary = mark.glossary && typeof mark.glossary === "object" ? mark.glossary : null;
  return normalizeInlineMarkText(
    mark.extra
    || glossary?.zh
    || glossary?.gloss
    || glossary?.reason
    || glossary?.phrase_type
    || glossary?.phraseType,
  );
}

function normalizeSentenceMark(mark) {
  return {
    title: inlineMarkTitleText(mark),
    anchor: inlineMarkAnchorText(mark),
    type: normalizeInlineMarkText(mark?.annotation_type || mark?.visual_tone || mark?.type),
    lookup_kind: inlineMarkLookupKind(mark),
    extra: inlineMarkExtraText(mark),
  };
}

function artifactMarkMap(artifact) {
  const map = new Map();
  const scene = workflowSceneFromArtifact(artifact);
  const items = Array.isArray(scene?.inline_marks) ? scene.inline_marks : Array.isArray(artifact?.inline_marks) ? artifact.inline_marks : [];
  for (const item of items) {
    const sid = item?.anchor?.sentence_id || item?.sentence_id;
    if (sid == null) continue;
    const key = String(sid);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(normalizeSentenceMark(item));
  }
  return map;
}

function normalizeSentenceEntry(entry) {
  return {
    label: String(entry?.label || entry?.entry_type || ""),
    content: String(entry?.content || entry?.title || entry?.note_zh || entry?.analysis_zh || ""),
  };
}

function artifactEntryMap(artifact) {
  const map = new Map();
  const scene = workflowSceneFromArtifact(artifact);
  const items = Array.isArray(scene?.sentence_entries) ? scene.sentence_entries : Array.isArray(artifact?.sentence_entries) ? artifact.sentence_entries : [];
  for (const item of items) {
    const sid = item?.sentence_id;
    if (sid == null) continue;
    const key = String(sid);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(normalizeSentenceEntry(item));
  }
  return map;
}

function sentenceIdsFromArtifact(artifact) {
  const ids = new Set();
  for (const map of [
    artifactSentenceTextMap(artifact),
    artifactTranslationMap(artifact),
    artifactMarkMap(artifact),
    artifactEntryMap(artifact),
  ]) {
    for (const sid of map.keys()) ids.add(sid);
  }
  return [...ids].sort();
}

function sentenceCaseId(sourceCaseId, sentenceId, singleSourceCase = false) {
  if (singleSourceCase) return String(sentenceId);
  return `${sourceCaseId}__${sentenceId}`;
}

function hasSentenceOutput(sentence) {
  return Boolean(
    sentence.translation
    || (Array.isArray(sentence.marks) && sentence.marks.length)
    || (Array.isArray(sentence.entries) && sentence.entries.length),
  );
}

function buildSentenceComparisonReasons(baselineSentence, candidateSentence) {
  const reasons = [];
  if (baselineSentence.translation !== candidateSentence.translation) reasons.push("translation changed");
  if (stableCompareJson(baselineSentence.marks) !== stableCompareJson(candidateSentence.marks)) reasons.push("inline marks changed");
  if (stableCompareJson(baselineSentence.entries) !== stableCompareJson(candidateSentence.entries)) reasons.push("sentence entries changed");
  return reasons;
}

function compareWorkflowLabSentenceOutputs(
  baselineArtifact,
  candidateArtifact,
  {
    sourceCaseId,
    singleSourceCase,
  } = {},
) {
  const baselineTexts = artifactSentenceTextMap(baselineArtifact);
  const candidateTexts = artifactSentenceTextMap(candidateArtifact);
  const baselineTranslations = artifactTranslationMap(baselineArtifact);
  const candidateTranslations = artifactTranslationMap(candidateArtifact);
  const baselineMarks = artifactMarkMap(baselineArtifact);
  const candidateMarks = artifactMarkMap(candidateArtifact);
  const baselineEntries = artifactEntryMap(baselineArtifact);
  const candidateEntries = artifactEntryMap(candidateArtifact);
  const sentenceIds = new Set([
    ...baselineTexts.keys(),
    ...candidateTexts.keys(),
    ...baselineTranslations.keys(),
    ...candidateTranslations.keys(),
    ...baselineMarks.keys(),
    ...candidateMarks.keys(),
    ...baselineEntries.keys(),
    ...candidateEntries.keys(),
  ]);
  const comparisons = [];
  for (const sid of [...sentenceIds].sort()) {
    const baselineSentence = {
      sentence_id: sid,
      sentence_text: baselineTexts.get(sid) || candidateTexts.get(sid) || "",
      translation: baselineTranslations.get(sid) || null,
      marks: baselineMarks.get(sid) || [],
      entries: baselineEntries.get(sid) || [],
    };
    const candidateSentence = {
      sentence_id: sid,
      sentence_text: candidateTexts.get(sid) || baselineTexts.get(sid) || "",
      translation: candidateTranslations.get(sid) || null,
      marks: candidateMarks.get(sid) || [],
      entries: candidateEntries.get(sid) || [],
    };
    const reasons = buildSentenceComparisonReasons(baselineSentence, candidateSentence);
    if (!reasons.length) continue;
    const baselineHasOutput = hasSentenceOutput(baselineSentence);
    const candidateHasOutput = hasSentenceOutput(candidateSentence);
    let verdict = "manual_review";
    if (candidateHasOutput && !baselineHasOutput) {
      verdict = "win";
      reasons.unshift("candidate added sentence-level output");
    } else if (!candidateHasOutput && baselineHasOutput) {
      verdict = "loss";
      reasons.unshift("candidate removed sentence-level output");
    }
    comparisons.push({
      case_id: sentenceCaseId(sourceCaseId, sid, singleSourceCase),
      comparison_kind: "sentence",
      source_case_id: sourceCaseId,
      sentence_id: sid,
      sentence_text: candidateSentence.sentence_text || baselineSentence.sentence_text || null,
      verdict,
      baseline_hard_failures: 0,
      candidate_hard_failures: 0,
      baseline_soft_failures: 0,
      candidate_soft_failures: 0,
      baseline_status: baselineArtifact.adapter_status || null,
      candidate_status: candidateArtifact.adapter_status || null,
      identity_delta: workflowLabIdentityDelta(baselineArtifact, candidateArtifact),
      reasons,
    });
  }
  return comparisons;
}

function appendWorkflowLabInternalIdentityWarnings(warnings, side, artifacts) {
  if (!artifacts.length) return;
  const first = workflowLabIdentitySnapshot(artifacts[0]);
  if (artifacts.slice(1).some((artifact) => stableCompareJson(workflowLabIdentitySnapshot(artifact)) !== stableCompareJson(first))) {
    warnings.push(`${side} identity varies across shared cases`);
  }
}

function workflowLabIdentityWarnings(baseline, candidate, sharedCaseIds, baselineById, candidateById) {
  const warnings = [];
  if (baseline.dataset_id !== candidate.dataset_id) {
    warnings.push(`hard warning: dataset_id differs: ${baseline.dataset_id || "<missing>"} -> ${candidate.dataset_id || "<missing>"}`);
  }
  if (baseline.report?.total_cases !== undefined && baseline.report.total_cases !== baseline.artifacts.length) {
    warnings.push(`baseline report total_cases differs from artifacts: ${baseline.report.total_cases} != ${baseline.artifacts.length}`);
  }
  if (candidate.report?.total_cases !== undefined && candidate.report.total_cases !== candidate.artifacts.length) {
    warnings.push(`candidate report total_cases differs from artifacts: ${candidate.report.total_cases} != ${candidate.artifacts.length}`);
  }

  const baselineOnly = baseline.artifacts.map((artifact) => artifact.case_id).filter((caseId) => !candidateById.has(caseId)).sort();
  const candidateOnly = candidate.artifacts.map((artifact) => artifact.case_id).filter((caseId) => !baselineById.has(caseId)).sort();
  if (baselineOnly.length) warnings.push(`baseline-only cases ignored: ${baselineOnly.join(", ")}`);
  if (candidateOnly.length) warnings.push(`candidate-only cases ignored: ${candidateOnly.join(", ")}`);

  appendWorkflowLabInternalIdentityWarnings(
    warnings,
    "baseline",
    sharedCaseIds.map((caseId) => baselineById.get(caseId)),
  );
  appendWorkflowLabInternalIdentityWarnings(
    warnings,
    "candidate",
    sharedCaseIds.map((caseId) => candidateById.get(caseId)),
  );

  for (const key of ["workflow_identity", "schema_identity"]) {
    if (sharedCaseIds.some((caseId) => {
      const baselineSnapshot = workflowLabIdentitySnapshot(baselineById.get(caseId));
      const candidateSnapshot = workflowLabIdentitySnapshot(candidateById.get(caseId));
      return stableCompareJson(baselineSnapshot[key]) !== stableCompareJson(candidateSnapshot[key]);
    })) {
      warnings.push(`${key} differs between baseline and candidate`);
    }
  }
  if (sharedCaseIds.every((caseId) => {
    const baselineSnapshot = workflowLabIdentitySnapshot(baselineById.get(caseId));
    const candidateSnapshot = workflowLabIdentitySnapshot(candidateById.get(caseId));
    return stableCompareJson(baselineSnapshot.prompt_identity) === stableCompareJson(candidateSnapshot.prompt_identity);
  })) {
    warnings.push("prompt_identity is identical; comparison may be replay/model/RAG delta");
  }
  if (sharedCaseIds.some((caseId) => {
    const baselineSnapshot = workflowLabIdentitySnapshot(baselineById.get(caseId));
    const candidateSnapshot = workflowLabIdentitySnapshot(candidateById.get(caseId));
    return stableCompareJson(baselineSnapshot.model_identity) !== stableCompareJson(candidateSnapshot.model_identity);
  })) {
    warnings.push("model_identity differs between baseline and candidate");
  }
  return warnings;
}

function assertWorkflowLabCompareInputConsistency(baseline, candidate) {
  // 两侧 run 都必须有 reading_goal / reading_variant / source_type;任一缺失就拒绝,
  // 避免 case_id 一致但上下文不一致的伪 compare
  const fields = ["reading_goal", "reading_variant", "source_type"];
  for (const side of ["baseline", "candidate"]) {
    const run = side === "baseline" ? baseline.run : candidate.run;
    for (const field of fields) {
      if (!run || !run[field]) {
        const error = new Error(
          `Run "${side === "baseline" ? baseline.run_id : candidate.run_id}" is missing required compare input field "${field}".`,
        );
        error.status = 422;
        error.code = "WORKFLOW_LAB_COMPARE_INPUT_MISSING";
        // Set error.field to the offending side + field so the FE can
        // route the toast to the right form input. Single-run-compare
        // doesn't expose per-side inputs, so the FE is expected to
        // surface the message generically when field is null.
        error.field = `${side}.${field}`;
        throw error;
      }
    }
  }
  // input_hash 必须一致:用 (text + reading_goal + reading_variant + source_type) 的稳定哈希校验,
  // 防止不同文章的 single run 共享 "single-run" 形式的 case_id 而被错误允许 compare
  const baselineInput = baselineArtifactsInputSnapshot(baseline);
  const candidateInput = candidateArtifactsInputSnapshot(candidate);
  if (!baselineInput || !candidateInput) {
    const error = new Error("Both runs must expose input_snapshot for run-driven compare.");
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_INPUT_MISSING";
    // No single field to attribute to; leaving field null keeps the
    // envelope honest.
    throw error;
  }
  if (baselineInput.input_hash !== candidateInput.input_hash) {
    const error = new Error(
      `Baseline and candidate have different input contexts (input_hash ${baselineInput.input_hash} != ${candidateInput.input_hash}).`,
    );
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_INPUT_MISMATCH";
    error.field = "input_hash";
    throw error;
  }
}

function baselineArtifactsInputSnapshot(loadedRun) {
  const artifact = Array.isArray(loadedRun?.artifacts) ? loadedRun.artifacts[0] : null;
  return artifactInputSnapshot(artifact, loadedRun?.run || {});
}

function candidateArtifactsInputSnapshot(loadedRun) {
  return baselineArtifactsInputSnapshot(loadedRun);
}

function artifactInputSnapshot(artifact, run = {}) {
  if (artifact?.input_snapshot && typeof artifact.input_snapshot === "object") {
    return computeInputHash(artifact.input_snapshot);
  }
  const fallback = {
    text: String(run?.source_text || run?.text || "").trim(),
    reading_goal: run?.reading_goal || "daily_reading",
    reading_variant: run?.reading_variant || "intermediate_reading",
    source_type: run?.source_type || "user_input",
  };
  return computeInputHash(fallback);
}

function computeInputHash(inputSnapshot) {
  const text = String(inputSnapshot?.text || "").trim();
  const readingGoal = inputSnapshot?.reading_goal || "daily_reading";
  const readingVariant = inputSnapshot?.reading_variant || "intermediate_reading";
  const sourceType = inputSnapshot?.source_type || "user_input";
  const digest = createHash("sha1")
    .update(stableJson({ text, reading_goal: readingGoal, reading_variant: readingVariant, source_type: sourceType }))
    .digest("hex")
    .slice(0, 16);
  return {
    text_length: text.length,
    reading_goal: readingGoal,
    reading_variant: readingVariant,
    source_type: sourceType,
    input_hash: digest,
  };
}

// experiment fingerprint (stable)
//
// Captures the conditions under which an experiment is executed:
//   - input_hash (text + reading context)
//   - reading_goal / reading_variant / source_type
//   - baseline + candidate prompt snapshot identity
//   - model profile / model identity
//
// The fingerprint is intentionally stable for a given (article, prompt
// configuration, model profile) so it can be used for grouping /
// cross-experiment analysis. It MUST NOT be used to deduplicate runs or
// compares — re-running the same conditions is a separate experiment and
// must always produce a fresh run_id / compare_id.
function buildWorkflowLabExperimentFingerprint(inputContext, baselineSnapshot, candidateSnapshot) {
  const inputHash = computeInputHash(inputContext || {}).input_hash;
  const baselineIdentity = normalizePromptSnapshotIdentity(baselineSnapshot);
  const candidateIdentity = normalizePromptSnapshotIdentity(candidateSnapshot);
  const raw = stableJson({
    input_hash: inputHash,
    reading_goal: inputContext?.reading_goal || "daily_reading",
    reading_variant: inputContext?.reading_variant || "intermediate_reading",
    source_type: inputContext?.source_type || "user_input",
    baseline_prompt: baselineIdentity,
    candidate_prompt: candidateIdentity,
  });
  const digest = createHash("sha1").update(raw).digest("hex").slice(0, 16);
  return {
    schema_version: "workflow-experiment-fingerprint-v1",
    experiment_fingerprint: digest,
    input_hash: inputHash,
    reading_goal: inputContext?.reading_goal || "daily_reading",
    reading_variant: inputContext?.reading_variant || "intermediate_reading",
    source_type: inputContext?.source_type || "user_input",
    baseline_prompt_identity: baselineIdentity,
    candidate_prompt_identity: candidateIdentity,
  };
}

function normalizePromptSnapshotIdentity(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return null;
  return {
    prompt_variant_id: snapshot.prompt_variant_id || null,
    prompt_snapshot_hash: snapshot.prompt_snapshot_hash || null,
    model_profile: snapshot.model_profile || null,
    model_name: snapshot.model_name || null,
  };
}

function buildWorkflowLabCompareReport(baseline, candidate, now = new Date()) {
  const baselineById = new Map(baseline.artifacts.map((artifact) => [artifact.case_id, artifact]));
  const candidateById = new Map(candidate.artifacts.map((artifact) => [artifact.case_id, artifact]));
  const sharedCaseIds = Array.from(baselineById.keys())
    .filter((caseId) => candidateById.has(caseId))
    .sort();
  if (sharedCaseIds.length === 0) {
    const error = new Error("No shared case ids to compare.");
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_ERROR";
    throw error;
  }
  const singleSourceCase = sharedCaseIds.length === 1;
  let comparisons = sharedCaseIds.flatMap((caseId) => (
    compareWorkflowLabSentenceOutputs(
      baselineById.get(caseId),
      candidateById.get(caseId),
      { sourceCaseId: caseId, singleSourceCase },
    )
  ));
  if (comparisons.length === 0) {
    comparisons = sharedCaseIds.map((caseId) => (
      compareWorkflowLabCaseArtifacts(baselineById.get(caseId), candidateById.get(caseId))
    ));
  }
  return {
    baseline_run_id: baseline.run_id,
    candidate_run_id: candidate.run_id,
    baseline_dataset_id: baseline.dataset_id,
    candidate_dataset_id: candidate.dataset_id,
    created_at: now instanceof Date ? now.toISOString() : new Date(now).toISOString(),
    total_cases: comparisons.length,
    wins: comparisons.filter((item) => item.verdict === "win").length,
    losses: comparisons.filter((item) => item.verdict === "loss").length,
    ties: comparisons.filter((item) => item.verdict === "tie").length,
    manual_review: comparisons.filter((item) => item.verdict === "manual_review").length,
    regression_case_ids: comparisons.filter((item) => item.verdict === "loss").map((item) => item.case_id),
    identity_warnings: workflowLabIdentityWarnings(baseline, candidate, sharedCaseIds, baselineById, candidateById),
    comparisons,
  };
}

// Derive the canonical compare status from the loaded baseline / candidate
// runs and the freshly-built report. The same string is written to the
// compare.json artifact, the eval_workflow_compares DB row, and (via the
// history detail loaders) the history / detail view, so the three views
// cannot disagree.
//
// Signals used (in order of precedence):
//   1. Both sides run-failed (run.status === "failed" with no usable
//      case artifacts) → "failed". One side failed and the other did not
//      is still a useful compare: the verdict is informative, so we keep
//      "complete" / "partial_failure" instead of throwing the verdict
//      away.
//   2. The report has zero shared cases (paired comparisons) → "failed".
//   3. Any case where both sides had hard failures is "uninformative":
//      the verdict is meaningless on that case. We still keep the
//      compare but mark partial_failure when this happens.
//   4. Any case-level hard or soft failure on either side →
//      "partial_failure". A "clean" compare (no failures anywhere) is
//      the only path to "complete".
function deriveWorkflowCompareStatus({ baseline, candidate, report }) {
  const reasons = [];
  const comparisons = Array.isArray(report?.comparisons) ? report.comparisons : [];
  const baselineArtifacts = Array.isArray(baseline?.artifacts) ? baseline.artifacts : [];
  const candidateArtifacts = Array.isArray(candidate?.artifacts) ? candidate.artifacts : [];

  const baselineRunFailed = String(baseline?.run?.status || "").toLowerCase() === "failed";
  const candidateRunFailed = String(candidate?.run?.status || "").toLowerCase() === "failed";

  if (baselineRunFailed && baselineArtifacts.length === 0) {
    reasons.push("baseline run failed with no usable cases");
  }
  if (candidateRunFailed && candidateArtifacts.length === 0) {
    reasons.push("candidate run failed with no usable cases");
  }
  if (
    (baselineRunFailed && baselineArtifacts.length === 0)
    || (candidateRunFailed && candidateArtifacts.length === 0)
  ) {
    return { dbStatus: "failed", reportStatus: "failed", reasons };
  }

  if (comparisons.length === 0) {
    reasons.push("no paired cases between baseline and candidate");
    return { dbStatus: "failed", reportStatus: "failed", reasons };
  }

  let uninformativeCaseCount = 0;
  let hardFailureCaseCount = 0;
  let softFailureCaseCount = 0;
  for (const item of comparisons) {
    const baselineHard = Number(item?.baseline_hard_failures || 0);
    const candidateHard = Number(item?.candidate_hard_failures || 0);
    const baselineSoft = Number(item?.baseline_soft_failures || 0);
    const candidateSoft = Number(item?.candidate_soft_failures || 0);
    if (baselineHard > 0 && candidateHard > 0) {
      uninformativeCaseCount += 1;
    }
    if (baselineHard > 0 || candidateHard > 0) {
      hardFailureCaseCount += 1;
    }
    if (baselineSoft > 0 || candidateSoft > 0) {
      softFailureCaseCount += 1;
    }
  }

  if (uninformativeCaseCount > 0) {
    reasons.push(`${uninformativeCaseCount} case(s) had hard failures on both sides and are uninformative`);
  }
  if (hardFailureCaseCount > 0) {
    reasons.push(`${hardFailureCaseCount} case(s) had at least one hard failure`);
  }
  if (softFailureCaseCount > 0) {
    reasons.push(`${softFailureCaseCount} case(s) had at least one soft failure`);
  }

  if (reasons.length === 0) {
    return { dbStatus: "complete", reportStatus: "complete", reasons: [] };
  }
  // The compare ran and produced a verdict, but with non-fatal issues
  // somewhere — surface that distinction at both the JSON and DB level
  // so Run History / list views (which read row.status) reflect the
  // same truth the JSON artifact carries.
  return { dbStatus: "partial_failure", reportStatus: "partial_failure", reasons };
}

function renderWorkflowLabCompareMarkdown(report) {
  const lines = [
    `# A/B Report: ${report.baseline_run_id} vs ${report.candidate_run_id}`,
    "",
    `- Created: ${report.created_at}`,
    `- Baseline dataset: \`${report.baseline_dataset_id || "<missing>"}\``,
    `- Candidate dataset: \`${report.candidate_dataset_id || "<missing>"}\``,
    `- Total paired cases: ${report.total_cases}`,
    `- Wins: ${report.wins}`,
    `- Losses: ${report.losses}`,
    `- Ties: ${report.ties}`,
    `- Manual review: ${report.manual_review}`,
    "",
  ];
  if (report.identity_warnings?.length) {
    lines.push("## Identity Warnings", "");
    for (const warning of report.identity_warnings) lines.push(`- ${warning}`);
    lines.push("");
  }
  if (report.regression_case_ids?.length) {
    lines.push("## Regression Cases", "");
    for (const caseId of report.regression_case_ids) lines.push(`- \`${caseId}\``);
    lines.push("");
  }
  lines.push("## Case Comparisons", "");
  lines.push("| Case ID | Verdict | Baseline Hard/Soft | Candidate Hard/Soft | Reasons |");
  lines.push("|---------|---------|--------------------|---------------------|---------|");
  for (const comparison of report.comparisons || []) {
    lines.push(
      `| \`${comparison.case_id}\` | ${comparison.verdict} | `
      + `${comparison.baseline_hard_failures}/${comparison.baseline_soft_failures} | `
      + `${comparison.candidate_hard_failures}/${comparison.candidate_soft_failures} | `
      + `${(comparison.reasons || []).join("<br>")} |`,
    );
  }
  lines.push("");
  return lines.join("\n");
}

function workflowCompareIdForRunPair(baselineRunId, candidateRunId) {
  // Legacy helper kept for backwards-compatible ID generation in places that
  // still need a deterministic-from-pair form (e.g. retry/cancel path on
  // already-persisted compare rows). The Workflow Lab main path no longer
  // uses this to deduplicate: see createOrReuseWorkflowCompare + the
  // experiment-fingerprint comment there.
  const digest = createHash("sha1")
    .update(stableJson({ baseline_run_id: baselineRunId, candidate_run_id: candidateRunId }))
    .digest("hex")
    .slice(0, 12);
  return `workflow-compare-${digest}`;
}

function newWorkflowCompareId() {
  // Unique per creation. Each compare is its own experiment and must never
  // be silently collapsed onto a previous compare with the same run pair.
  const ts = new Date().toISOString().replace(/[^0-9]/g, "").slice(0, 14); // YYYYMMDDHHMMSS
  const suffix = randomUUID().replace(/-/g, "").slice(0, 12);
  return `workflow-compare-${ts}-${suffix}`;
}

function buildWorkflowCompareEvidenceIndex(report, baselineRunId, candidateRunId, inputHash = null) {
  return {
    schema_version: "workflow-compare-evidence-index-v1",
    baseline_run_id: baselineRunId,
    candidate_run_id: candidateRunId,
    input_hash: inputHash,
    case_ids: Array.isArray(report?.comparisons) ? report.comparisons.map((item) => item.case_id) : [],
    comparisons: Array.isArray(report?.comparisons)
        ? report.comparisons.map((item) => ({
            case_id: item.case_id,
            comparison_kind: item.comparison_kind || "artifact",
            source_case_id: item.source_case_id || item.case_id,
            sentence_id: item.sentence_id || null,
            verdict: item.verdict,
            baseline_run_id: baselineRunId,
            candidate_run_id: candidateRunId,
          }))
      : [],
  };
}

function workflowCompareSummaryFromRow(row, detail = {}) {
  const baselineRunSummary = detail.baseline_run_summary?.summary || detail.baseline_run_summary || null;
  const candidateRunSummary = detail.candidate_run_summary?.summary || detail.candidate_run_summary || null;
  const compareMeta = detail.compare_json && typeof detail.compare_json === "object"
    ? detail.compare_json
    : {};
  const report = detail.report && typeof detail.report === "object"
    ? detail.report
    : {};
  const candidatePromptVariantId = compareMeta.candidate_prompt_variant_id
    || candidateRunSummary?.prompt_variant_id
    || null;
  const status = row?.status || compareMeta.status || "complete";
  return {
    source: "workflow",
    workspace_type: "workflow_compare",
    compare_id: row.compare_id,
    record_id: row.compare_id,
    status,
    source_kind: row.source_kind,
    baseline_run_id: row.baseline_run_id,
    candidate_run_id: row.candidate_run_id,
    input_hash: row.input_hash || compareMeta.input_hash || null,
    reading_goal: row.reading_goal || compareMeta.reading_goal || null,
    reading_variant: row.reading_variant || compareMeta.reading_variant || null,
    source_type: row.source_type || compareMeta.source_type || null,
    artifact_path: row.artifact_path,
    report_id: row.report_id,
    case_count: Number(row.case_count || report.total_cases || 0),
    wins: Number(row.wins || report.wins || 0),
    losses: Number(row.losses || report.losses || 0),
    ties: Number(row.ties || report.ties || 0),
    prompt_variant_id: candidatePromptVariantId,
    created_at: row.date_created || compareMeta.created_at || null,
    date_created: row.date_created || compareMeta.created_at || null,
    date_updated: row.date_updated || compareMeta.updated_at || null,
    // Surface the richer status + reasons from the compare.json artifact
    // when the row only carries the SQL-allowed subset (complete / failed).
    // This keeps history / detail views consistent with what
    // createOrReuseWorkflowCompare wrote.
    report_status: compareMeta.report_status || row?.status || "complete",
    status_reasons: Array.isArray(compareMeta.status_reasons) ? compareMeta.status_reasons : [],
    display_title: row.custom_title || `${candidatePromptVariantId || "baseline"} · compare`,
    display_excerpt: `${row.baseline_run_id} vs ${row.candidate_run_id}`,
    custom_title: row.custom_title || null,
    baseline_model: baselineRunSummary?.model_name || baselineRunSummary?.model_identity?.model_name || null,
    baseline_model_profile: baselineRunSummary?.model_profile || baselineRunSummary?.model_identity?.profile_name || null,
    baseline_latency_seconds: baselineRunSummary?.latency_seconds ?? null,
    baseline_total_tokens: baselineRunSummary?.total_tokens ?? baselineRunSummary?.usage_summary?.total_tokens ?? null,
    baseline_input_tokens: baselineRunSummary?.input_tokens ?? baselineRunSummary?.usage_summary?.input_tokens ?? null,
    baseline_output_tokens: baselineRunSummary?.output_tokens ?? baselineRunSummary?.usage_summary?.output_tokens ?? null,
    candidate_model: candidateRunSummary?.model_name || candidateRunSummary?.model_identity?.model_name || null,
    candidate_model_profile: candidateRunSummary?.model_profile || candidateRunSummary?.model_identity?.profile_name || null,
    candidate_latency_seconds: candidateRunSummary?.latency_seconds ?? null,
    candidate_total_tokens: candidateRunSummary?.total_tokens ?? candidateRunSummary?.usage_summary?.total_tokens ?? null,
    candidate_input_tokens: candidateRunSummary?.input_tokens ?? candidateRunSummary?.usage_summary?.input_tokens ?? null,
    candidate_output_tokens: candidateRunSummary?.output_tokens ?? candidateRunSummary?.usage_summary?.output_tokens ?? null,
  };
}

async function loadWorkflowCompareArtifactPayload(env, compareId) {
  const root = resolveWorkflowCompareRuntimeRoot(env);
  const compareJson = await readJsonFile(compareArtifactPath(root, compareId, "compare.json"));
  const report = await readJsonFile(compareArtifactPath(root, compareId, "report.json"));
  const evidenceIndex = await readJsonFile(compareArtifactPath(root, compareId, "evidence-index.json"));
  return {
    root,
    compare_json: compareJson,
    report,
    evidence_index: evidenceIndex,
  };
}

async function loadWorkflowCompareUnderlyingRunSummary(env, runId) {
  if (!runId) return null;
  try {
    return await loadRunSummary(resolveWorkflowRunRoots(env), runId);
  } catch {
    return null;
  }
}

async function createOrReuseWorkflowCompare({ database, env, baselineRunId, candidateRunId, sourceKind = "single_run_compare", reqUser = null }) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "WORKFLOW_COMPARE_DB_UNAVAILABLE";
    throw error;
  }
  if (!isSafeFileId(baselineRunId) || !isSafeFileId(candidateRunId)) {
    const error = new Error("baseline_run_id and candidate_run_id must be safe ids.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    throw error;
  }
  if (baselineRunId === candidateRunId) {
    const error = new Error("baseline_run_id and candidate_run_id must differ.");
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_ERROR";
    throw error;
  }

  // Reuse was removed: every call mints a fresh compare_id. Same run pair
  // submitted twice => two compare rows, each with their own
  // experiment_fingerprint. Stable grouping across those rows comes from
  // the fingerprint, not from collapsing the rows.
  const compareId = newWorkflowCompareId();

  const roots = resolveWorkflowRunRoots(env);
  const baseline = await loadRunForWorkflowLabCompare(roots, baselineRunId);
  const candidate = await loadRunForWorkflowLabCompare(roots, candidateRunId);
  assertWorkflowLabCompareInputConsistency(baseline, candidate);
  const report = buildWorkflowLabCompareReport(baseline, candidate);
  const inputHash = computeInputHash(artifactInputSnapshot(baseline.artifacts[0], baseline.run)).input_hash;
  const fingerprint = buildWorkflowLabExperimentFingerprint(
    {
      text: baseline.run?.source_text || baseline.run?.text || "",
      reading_goal: baseline.run?.reading_goal,
      reading_variant: baseline.run?.reading_variant,
      source_type: baseline.run?.source_type,
    },
    {
      prompt_variant_id: baseline.artifacts?.[0]?.prompt_identity?.prompt_variant_id || baseline.run?.prompt_variant_id || null,
      prompt_snapshot_hash: baseline.artifacts?.[0]?.prompt_identity?.prompt_snapshot_hash || null,
      model_profile: baseline.artifacts?.[0]?.model_identity?.profile_name || null,
      model_name: baseline.artifacts?.[0]?.model_identity?.model_name || null,
    },
    {
      prompt_variant_id: candidate.artifacts?.[0]?.prompt_identity?.prompt_variant_id || candidate.run?.prompt_variant_id || null,
      prompt_snapshot_hash: candidate.artifacts?.[0]?.prompt_identity?.prompt_snapshot_hash || null,
      model_profile: candidate.artifacts?.[0]?.model_identity?.profile_name || null,
      model_name: candidate.artifacts?.[0]?.model_identity?.model_name || null,
    },
  );
  const compareRoot = resolveWorkflowCompareRuntimeRoot(env);
  const comparePath = compareDir(compareRoot, compareId);
  // Derive a single status from the actual baseline / candidate / report
  // signals — see deriveWorkflowCompareStatus for the rule. The compare.json
  // artifact carries the richer "partial_failure" vs "complete" signal
  // while the DB row stores the same value (the SQL CHECK now allows
  // partial_failure alongside complete / failed).
  const statusDerived = deriveWorkflowCompareStatus({ baseline, candidate, report });
  const compareMeta = {
    schema_version: "workflow-compare-v1",
    compare_id: compareId,
    source_kind: sourceKind,
    status: statusDerived.dbStatus,
    report_status: statusDerived.reportStatus,
    status_reasons: statusDerived.reasons,
    baseline_run_id: baselineRunId,
    candidate_run_id: candidateRunId,
    input_hash: inputHash,
    reading_goal: baseline.run?.reading_goal || null,
    reading_variant: baseline.run?.reading_variant || null,
    source_type: baseline.run?.source_type || null,
    artifact_path: `${workflowCompareArtifactPrefix(compareRoot, compareRoot)}/${compareId}`,
    report_id: compareId,
    case_count: report.total_cases || 0,
    wins: report.wins || 0,
    losses: report.losses || 0,
    ties: report.ties || 0,
    identity_warnings: Array.isArray(report.identity_warnings) ? report.identity_warnings : [],
    candidate_prompt_variant_id:
      candidate.artifacts?.[0]?.prompt_identity?.prompt_variant_id
      || candidate.run?.prompt_variant_id
      || null,
    experiment_fingerprint: fingerprint.experiment_fingerprint,
    fingerprint_payload: fingerprint,
    created_at: new Date().toISOString(),
  };
  const evidenceIndex = buildWorkflowCompareEvidenceIndex(report, baselineRunId, candidateRunId, inputHash);
  await mkdir(comparePath, { recursive: true });
  await writeJsonFile(compareArtifactPath(compareRoot, compareId, "compare.json"), compareMeta);
  await writeJsonFile(compareArtifactPath(compareRoot, compareId, "report.json"), report);
  await writeFile(compareArtifactPath(compareRoot, compareId, "report.md"), renderWorkflowLabCompareMarkdown(report), "utf8");
  await writeJsonFile(compareArtifactPath(compareRoot, compareId, "evidence-index.json"), evidenceIndex);
  await database("eval_workflow_compares").insert({
    compare_id: compareId,
    source_kind: sourceKind,
    baseline_run_id: baselineRunId,
    candidate_run_id: candidateRunId,
    status: statusDerived.dbStatus,
    input_hash: inputHash,
    reading_goal: baseline.run?.reading_goal || null,
    reading_variant: baseline.run?.reading_variant || null,
    source_type: baseline.run?.source_type || null,
    artifact_path: compareMeta.artifact_path,
    report_id: compareId,
    case_count: report.total_cases || 0,
    wins: report.wins || 0,
    losses: report.losses || 0,
    ties: report.ties || 0,
    identity_warnings: Array.isArray(report.identity_warnings) ? report.identity_warnings : [],
    experiment_fingerprint: fingerprint.experiment_fingerprint,
    user_created: reqUser || null,
  });
  return {
    created: true,
    compare_id: compareId,
    detail: await loadWorkflowCompareHistoryDetail(database, env, compareId),
  };
}

async function listWorkflowCompareHistoryRecords(database, env, limit = 30) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "WORKFLOW_COMPARE_DB_UNAVAILABLE";
    throw error;
  }
  const rows = await database("eval_workflow_compares")
    .select([
      "compare_id",
      "source_kind",
      "baseline_run_id",
      "candidate_run_id",
      "status",
      "input_hash",
      "reading_goal",
      "reading_variant",
      "source_type",
      "artifact_path",
      "report_id",
      "case_count",
      "wins",
      "losses",
      "ties",
      "identity_warnings",
      "date_created",
      "date_updated",
      "custom_title",
    ])
    .orderBy("date_created", "desc")
    .limit(limit);
  const records = [];
  for (const row of rows) {
    let detail = {};
    try {
      const artifact = await loadWorkflowCompareArtifactPayload(env, row.compare_id);
      const [baselineRunSummary, candidateRunSummary] = await Promise.all([
        loadWorkflowCompareUnderlyingRunSummary(env, row.baseline_run_id),
        loadWorkflowCompareUnderlyingRunSummary(env, row.candidate_run_id),
      ]);
      detail = {
        compare_json: artifact.compare_json,
        report: artifact.report,
        baseline_run_summary: baselineRunSummary,
        candidate_run_summary: candidateRunSummary,
      };
    } catch {
      detail = {};
    }
    records.push(workflowCompareSummaryFromRow(row, detail));
  }
  return records;
}

async function loadWorkflowCompareHistoryDetail(database, env, compareId) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "WORKFLOW_COMPARE_DB_UNAVAILABLE";
    throw error;
  }
  if (!isSafeFileId(compareId)) {
    const error = new Error("Invalid compare id.");
    error.status = 400;
    error.code = "INVALID_COMPARE_ID";
    throw error;
  }
  const row = await database("eval_workflow_compares")
    .where({ compare_id: compareId })
    .first();
  if (!row) {
    const error = new Error("Workflow compare record not found.");
    error.status = 404;
    error.code = "WORKFLOW_COMPARE_NOT_FOUND";
    throw error;
  }
  const artifact = await loadWorkflowCompareArtifactPayload(env, compareId);
  const [baselineRunSummary, candidateRunSummary] = await Promise.all([
    loadWorkflowCompareUnderlyingRunSummary(env, row.baseline_run_id),
    loadWorkflowCompareUnderlyingRunSummary(env, row.candidate_run_id),
  ]);
  const judgeRows = await listWorkflowCompareJudgeRequests(database, compareId, { status: "all", limit: 50 });
  return {
    source: "workflow",
    record: workflowCompareSummaryFromRow(row, {
      compare_json: artifact.compare_json,
      report: artifact.report,
      baseline_run_summary: baselineRunSummary,
      candidate_run_summary: candidateRunSummary,
    }),
    compare: {
      ...artifact.compare_json,
      compare_id: compareId,
      status: row.status,
      baseline_run_id: row.baseline_run_id,
      candidate_run_id: row.candidate_run_id,
      source_kind: row.source_kind,
      artifact_path: row.artifact_path,
      report_id: row.report_id,
    },
    report: artifact.report,
    evidence_index: artifact.evidence_index,
    compare_judge_requests: judgeRows.map((item) => workflowCompareJudgeRequestSummary(item)),
    baseline_run_summary: baselineRunSummary?.summary || baselineRunSummary || null,
    candidate_run_summary: candidateRunSummary?.summary || candidateRunSummary || null,
  };
}

async function loadWorkflowCompareCaseEvidence(database, env, compareId, caseId) {
  const detail = await loadWorkflowCompareHistoryDetail(database, env, compareId);
  const comparison = Array.isArray(detail.report?.comparisons)
    ? detail.report.comparisons.find((item) => item.case_id === caseId)
    : null;
  if (!comparison) {
    const error = new Error(`Case "${caseId}" was not found in compare "${compareId}".`);
    error.status = 404;
    error.code = "WORKFLOW_COMPARE_CASE_NOT_FOUND";
    throw error;
  }
  const roots = resolveWorkflowRunRoots(env);
  const baselineRoot = await resolveRunRootOrThrow(roots, detail.compare.baseline_run_id);
  const candidateRoot = await resolveRunRootOrThrow(roots, detail.compare.candidate_run_id);
  const sourceCaseId = comparison.source_case_id || caseId;
  return {
    compare_id: compareId,
    case_id: caseId,
    comparison,
    baseline_artifact: await readJsonFile(caseArtifactPath(baselineRoot, detail.compare.baseline_run_id, sourceCaseId)),
    candidate_artifact: await readJsonFile(caseArtifactPath(candidateRoot, detail.compare.candidate_run_id, sourceCaseId)),
  };
}

async function deleteWorkflowCompareCascade(database, env, compareId) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "WORKFLOW_COMPARE_DB_UNAVAILABLE";
    throw error;
  }
  const row = await database("eval_workflow_compares")
    .where({ compare_id: compareId })
    .first();
  if (!row) {
    const error = new Error("Workflow compare record not found.");
    error.status = 404;
    error.code = "WORKFLOW_COMPARE_NOT_FOUND";
    throw error;
  }
  const compareRoot = resolveWorkflowCompareRuntimeRoot(env);
  const artifactDir = compareDir(compareRoot, compareId);
  const roots = resolveWorkflowRunRoots(env);
  const deletedRuns = [];
  for (const runId of [row.baseline_run_id, row.candidate_run_id]) {
    if (!runId || deletedRuns.includes(runId)) continue;
    if (await findExistingRunRoot(roots, runId)) {
      await deleteWorkflowRunCascade(database, roots, runId);
      deletedRuns.push(runId);
    }
  }
  await database("eval_review_notes")
    .where({ target_type: "workflow_compare", target_id: compareId })
    .del();
  await database("eval_workflow_compare_judge_requests")
    .where({ compare_id: compareId })
    .del();
  await database("eval_workflow_compares")
    .where({ compare_id: compareId })
    .del();
  await removePathIfExists(artifactDir);
  return {
    deleted: true,
    compare_id: compareId,
    removed_runs: deletedRuns,
    removed_artifact_path: artifactDir,
  };
}

function workflowCompareJudgeRequestRow(req, config, options = {}) {
  const attemptNo = Number.parseInt(String(options.attempt_no || 1), 10);
  const safeAttemptNo = Number.isFinite(attemptNo) && attemptNo > 0 ? attemptNo : 1;
  const maxAttempts = Number.parseInt(String(options.max_attempts || safeAttemptNo), 10);
  return {
    judge_run_id: config.judge_run_id,
    compare_id: config.compare_id,
    baseline_run_id: config.baseline_run_id,
    candidate_run_id: config.candidate_run_id,
    rubric_id: config.rubric_id,
    rubric_version: config.rubric_version,
    status: "queued",
    judge_adapter_kind: config.judge_adapter_kind || "fake",
    config_json: config.config_json || {},
    artifact_path: null,
    source_request_id: options.source_request_id || null,
    attempt_no: safeAttemptNo,
    max_attempts: Number.isFinite(maxAttempts) && maxAttempts >= safeAttemptNo
      ? maxAttempts
      : safeAttemptNo,
    retry_reason: options.retry_reason || null,
    user_created: req.accountability?.user || null,
  };
}

function workflowCompareJudgeRequestSummary(row) {
  const config = row?.config_json && typeof row.config_json === "object" ? row.config_json : {};
  const errorJson = row?.error_json && typeof row.error_json === "object" ? row.error_json : null;
  return {
    id: row.id,
    judge_run_id: row.judge_run_id,
    compare_id: row.compare_id,
    baseline_run_id: row.baseline_run_id,
    candidate_run_id: row.candidate_run_id,
    rubric_id: row.rubric_id,
    rubric_version: row.rubric_version,
    status: row.status,
    judge_adapter_kind: row.judge_adapter_kind,
    artifact_path: row.artifact_path,
    expected_artifact_path: row.compare_id && row.judge_run_id
      ? `runtime-evals/workflow-compares/${row.compare_id}/judge/${row.judge_run_id}`
      : null,
    source_request_id: row.source_request_id || null,
    attempt_no: row.attempt_no || 1,
    max_attempts: row.max_attempts || row.attempt_no || 1,
    retry_reason: row.retry_reason || null,
    cancelable: isJudgeRunRequestCancelable(row.status),
    retryable: isJudgeRunRequestRetryable(row.status),
    lease_owner: row.lease_owner,
    lease_until: row.lease_until,
    heartbeat_at: row.heartbeat_at,
    started_at: row.started_at,
    finished_at: row.finished_at,
    date_created: row.date_created,
    date_updated: row.date_updated,
    error: errorJson
      ? { code: errorJson.code || null, message: errorJson.message || null }
      : null,
    config_summary: {
      source: config.source || null,
      max_concurrency: config.max_concurrency || 1,
      max_cases: config.max_cases || null,
      source_text_char_limit: config.source_text_char_limit || null,
      output_item_limit: config.output_item_limit || null,
    },
  };
}

function compareJudgeCaseVerdict(comparison) {
  if (comparison?.verdict === "win") return "candidate_preferred";
  if (comparison?.verdict === "loss") return "baseline_preferred";
  if (comparison?.verdict === "tie") return "tie";
  return "needs_review";
}

function compareJudgeCaseScore(verdict) {
  if (verdict === "candidate_preferred") return 1;
  if (verdict === "tie") return 0.5;
  if (verdict === "baseline_preferred") return 0;
  return null;
}

function buildWorkflowCompareJudgeArtifacts({ requestRow, compare, caseResults, judgeMeta = null }) {
  const results = Array.isArray(caseResults) ? caseResults : [];
  const normalizedJudgeMeta = judgeMeta && typeof judgeMeta === "object" ? judgeMeta : {};
  const summary = {
    total_cases: results.length,
    candidate_preferred: results.filter((item) => item.verdict === "candidate_preferred").length,
    baseline_preferred: results.filter((item) => item.verdict === "baseline_preferred").length,
    tie: results.filter((item) => item.verdict === "tie").length,
    needs_review: results.filter((item) => item.verdict === "needs_review" && item.status !== "error").length,
    errored: results.filter((item) => item.status === "error" || item.verdict === "error").length,
  };
  const judgeRun = {
    schema_version: "workflow-compare-judge-run-v1",
    judge_run_id: requestRow.judge_run_id,
    compare_id: requestRow.compare_id,
    baseline_run_id: requestRow.baseline_run_id,
    candidate_run_id: requestRow.candidate_run_id,
    rubric_id: requestRow.rubric_id,
    rubric_version: requestRow.rubric_version,
    judge_adapter_kind: requestRow.judge_adapter_kind,
    created_at: new Date().toISOString(),
    config_json: requestRow.config_json || {},
    attempt_no: requestRow.attempt_no || 1,
    max_attempts: requestRow.max_attempts || 1,
    retry_reason: requestRow.retry_reason || null,
    model_identity: normalizedJudgeMeta.model_identity || null,
    runtime_summary: normalizedJudgeMeta.runtime_summary || null,
  };
  const caseResultsPayload = {
    schema_version: "workflow-compare-judge-case-results-v1",
    judge_run_id: requestRow.judge_run_id,
    compare_id: requestRow.compare_id,
    generated_at: new Date().toISOString(),
    cases: results,
  };
  const judgeReport = {
    schema_version: "workflow-compare-judge-report-v1",
    judge_run_id: requestRow.judge_run_id,
    compare_id: requestRow.compare_id,
    baseline_run_id: compare.baseline_run_id,
    candidate_run_id: compare.candidate_run_id,
    rubric_id: requestRow.rubric_id,
    rubric_version: requestRow.rubric_version,
    judge_adapter_kind: requestRow.judge_adapter_kind,
    created_at: new Date().toISOString(),
    judge_model_name: normalizedJudgeMeta.judge_model_name || null,
    judge_model_profile: normalizedJudgeMeta.judge_model_profile || null,
    judge_provider: normalizedJudgeMeta.judge_provider || null,
    latency_seconds: normalizedJudgeMeta.latency_seconds ?? null,
    input_tokens: normalizedJudgeMeta.input_tokens ?? null,
    output_tokens: normalizedJudgeMeta.output_tokens ?? null,
    total_tokens: normalizedJudgeMeta.total_tokens ?? null,
    ...summary,
    notes: [
      "Workflow compare judge evaluates sentence-level compare cases anchored to the persisted compare_id.",
      "Use it as review evidence; final promotion decisions still require human verification.",
    ],
    case_summaries: results.map((item) => ({
      case_id: item.case_id,
      status: item.status,
      verdict: item.verdict,
      preferred_side: item.preferred_side,
      overall_score: item.overall_score,
    })),
  };
  const reportMd = [
    `# Workflow Compare Judge: ${compare.compare_id}`,
    "",
    `- Judge Run: ${requestRow.judge_run_id}`,
    `- Rubric: ${requestRow.rubric_id}@${requestRow.rubric_version}`,
    `- Adapter: ${requestRow.judge_adapter_kind}`,
    normalizedJudgeMeta.judge_model_name ? `- Judge Model: ${normalizedJudgeMeta.judge_model_name}` : null,
    normalizedJudgeMeta.judge_model_profile ? `- Judge Profile: ${normalizedJudgeMeta.judge_model_profile}` : null,
    Number.isFinite(normalizedJudgeMeta.latency_seconds) ? `- Judge Latency: ${normalizedJudgeMeta.latency_seconds.toFixed(2)}s` : null,
    Number.isFinite(normalizedJudgeMeta.total_tokens) ? `- Judge Tokens: ${normalizedJudgeMeta.total_tokens}` : null,
    `- Total Cases: ${summary.total_cases}`,
    `- Candidate Preferred: ${summary.candidate_preferred}`,
    `- Baseline Preferred: ${summary.baseline_preferred}`,
    `- Tie: ${summary.tie}`,
    `- Needs Review: ${summary.needs_review}`,
    "",
  ].filter(Boolean).join("\n");
  return { judgeRun, caseResultsPayload, judgeReport, reportMd };
}

function buildWorkflowCompareFakeJudgeCaseResults(report) {
  const comparisons = Array.isArray(report?.comparisons) ? report.comparisons : [];
  return comparisons.map((comparison) => {
    const verdict = compareJudgeCaseVerdict(comparison);
    const score = compareJudgeCaseScore(verdict);
    return {
      case_id: comparison.case_id,
      status: "succeeded",
      verdict,
      preferred_side: verdict === "candidate_preferred"
        ? "candidate"
        : verdict === "baseline_preferred"
          ? "baseline"
          : null,
      overall_score: score,
      summary: (comparison.reasons || []).join("; ") || "No additional deterministic reason.",
      baseline_hard_failures: comparison.baseline_hard_failures ?? 0,
      baseline_soft_failures: comparison.baseline_soft_failures ?? 0,
      candidate_hard_failures: comparison.candidate_hard_failures ?? 0,
      candidate_soft_failures: comparison.candidate_soft_failures ?? 0,
      reasons: Array.isArray(comparison.reasons) ? comparison.reasons : [],
    };
  });
}

function summarizeCompareJudgeSentenceOutput(artifact, comparison) {
  const scene = workflowSceneFromArtifact(artifact);
  const sentenceId = comparison?.sentence_id || comparison?.case_id;
  const sentenceText = artifactSentenceTextMap(artifact).get(String(sentenceId)) || comparison?.sentence_text || "";
  const translation = artifactTranslationMap(artifact).get(String(sentenceId)) || null;
  const marks = artifactMarkMap(artifact).get(String(sentenceId)) || [];
  const entries = artifactEntryMap(artifact).get(String(sentenceId)) || [];
  const rawWarnings = Array.isArray(scene?.warnings)
    ? scene.warnings
    : Array.isArray(artifact?.warnings)
      ? artifact.warnings
      : [];
  return {
    user_facing_state: scene?.user_facing_state || artifact?.user_facing_state || null,
    sentence_id: sentenceId,
    sentence_text: truncateJudgeText(sentenceText, 240),
    translation: truncateJudgeText(translation, 240) || null,
    inline_marks: compactCompareJudgeInlineMarks(marks),
    sentence_entries: compactCompareJudgeSentenceEntries(entries),
    // The API-side WorkflowLabCompareJudgeSidePayload schema requires
    // warnings: list[str]. The Directus-side artifact / render_scene carries
    // warnings as list[dict] (e.g. {code, message, sentence_id, ...}); we
    // flatten them into a readable single-line string so the request body
    // no longer fails Pydantic validation with
    // "Input should be a valid string" on body.packets.0.baseline.warnings.0.
    warnings: normalizeWarningsToStringList(rawWarnings).slice(0, 4),
    drop_log: compactCompareJudgeDropLog(artifact?.drop_log),
  };
}

function normalizeWarningsToStringList(rawWarnings) {
  if (!Array.isArray(rawWarnings)) return [];
  const out = [];
  for (const item of rawWarnings) {
    if (item == null) continue;
    if (typeof item === "string") {
      const trimmed = item.trim();
      if (trimmed) out.push(trimmed);
      continue;
    }
    if (typeof item === "object") {
      // Prefer a small, readable subset over a raw JSON dump.
      const code = typeof item.code === "string" ? item.code.trim() : "";
      const message = typeof item.message === "string" ? item.message.trim() : "";
      const detail = typeof item.detail === "string" ? item.detail.trim() : "";
      const sentenceId = item.sentence_id != null ? String(item.sentence_id).trim() : "";
      const parts = [];
      if (code) parts.push(`[${code}]`);
      if (message) parts.push(message);
      if (sentenceId) parts.push(`sentence_id=${sentenceId}`);
      if (detail) parts.push(detail);
      const flattened = parts.join(" ").replace(/\s+/g, " ").trim();
      if (flattened) out.push(flattened.slice(0, 500));
    }
  }
  return out;
}

function truncateJudgeText(value, maxChars = 320) {
  if (typeof value !== "string") return "";
  return value.replace(/\s+/g, " ").trim().slice(0, maxChars);
}

function pickJudgeEntrySummary(entry) {
  const candidates = [
    entry?.summary,
    entry?.text,
    entry?.content,
    entry?.description,
    entry?.explanation,
    entry?.gloss,
    entry?.note,
    entry?.analysis_summary,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return truncateJudgeText(candidate, 360);
    }
  }
  return "";
}

function compactCompareJudgeSentenceEntry(entry) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
  const compact = {
    type: entry.type || entry.entry_type || entry.kind || null,
    label: entry.label || entry.title || entry.name || null,
    summary: pickJudgeEntrySummary(entry) || null,
  };
  if (typeof entry.source_text === "string" && entry.source_text.trim()) {
    compact.source_text = truncateJudgeText(entry.source_text, 180);
  }
  if (typeof entry.anchor_text === "string" && entry.anchor_text.trim()) {
    compact.anchor_text = truncateJudgeText(entry.anchor_text, 180);
  }
  if (Array.isArray(entry.chunks) && entry.chunks.length > 0) {
    compact.chunks = entry.chunks.slice(0, 4).map((chunk) => ({
      label: chunk?.label || chunk?.type || null,
      text: truncateJudgeText(String(chunk?.text || ""), 120) || null,
    }));
  }
  if (compact.type === "sentence_analysis" && !compact.summary) {
    compact.summary = "sentence_analysis";
  }
  return compact;
}

function compactCompareJudgeInlineMarks(marks) {
  if (!Array.isArray(marks)) return [];
  return marks.slice(0, 6).map((mark) => {
    if (!mark || typeof mark !== "object" || Array.isArray(mark)) return null;
    const compact = {
      title: truncateJudgeText(inlineMarkTitleText(mark), 80) || null,
      anchor: truncateJudgeText(inlineMarkAnchorText(mark), 80) || null,
      type: truncateJudgeText(mark.type || mark.annotation_type || mark.visual_tone || "", 40) || null,
      lookup_kind: truncateJudgeText(inlineMarkLookupKind(mark), 40) || null,
      extra: truncateJudgeText(inlineMarkExtraText(mark) || mark.zh || mark.gloss || mark.label || "", 80) || null,
    };
    return Object.fromEntries(Object.entries(compact).filter(([, value]) => value));
  }).filter(Boolean);
}

function compactCompareJudgeSentenceEntries(entries) {
  if (!Array.isArray(entries)) return [];
  return entries.map((entry) => compactCompareJudgeSentenceEntry(entry)).filter(Boolean).slice(0, 4);
}

function compactCompareJudgeDropLog(rawDropLog) {
  if (!Array.isArray(rawDropLog)) return [];
  return rawDropLog.slice(0, 3).map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return { message: truncateJudgeText(String(item || ""), 180) || "drop" };
    }
    return {
      code: item.code || null,
      reason: truncateJudgeText(String(item.reason || item.message || ""), 180) || null,
      sentence_id: item.sentence_id ? String(item.sentence_id) : null,
    };
  });
}

async function requestOpenAICompatibleJudge({ env, model, packet }) {
  // DEPRECATED: This helper used to call /chat/completions directly from
  // Directus. Compare-level LLM judging now lives in services/api
  // (app.eval_adapter.workflow_lab_compare_judge + the
  // /eval/article-analysis/workflow-lab/compare-judge route). Directus only
  // proxies to the API; it must not read CLAREAD_EVAL_JUDGE_* env vars
  // or issue /chat/completions directly. Kept as a non-functional stub so
  // any external import does not crash; the body raises if it is reached.
  const error = new Error(
    "requestOpenAICompatibleJudge is no longer used. Compare LLM judge goes through services/api."
  );
  error.status = 501;
  error.code = "WORKFLOW_COMPARE_JUDGE_LEGACY_CLIENT_REMOVED";
  throw error;
}

function _summarizeCompareJudgeFailure(comparison, error) {
  return {
    case_id: comparison.case_id,
    status: "error",
    verdict: "needs_review",
    preferred_side: null,
    overall_score: null,
    summary: "LLM judge execution failed.",
    baseline_hard_failures: comparison.baseline_hard_failures ?? 0,
    baseline_soft_failures: comparison.baseline_soft_failures ?? 0,
    candidate_hard_failures: comparison.candidate_hard_failures ?? 0,
    candidate_soft_failures: comparison.candidate_soft_failures ?? 0,
    reasons: [String(error?.message || error)],
    error: {
      code: error?.code || "WORKFLOW_COMPARE_JUDGE_LLM_ERROR",
      message: String(error?.message || error).slice(0, 500),
    },
  };
}

async function buildWorkflowCompareLlmJudgeCaseResults(env, requestRow, compareDetail) {
  // judge_model_profile is the primary control-plane source. judger_model_name
  // is retained as debug-only metadata in the request row but is no longer
  // used to short-circuit or override the model_profile-driven API call.
  const judgeModelProfile = String(
    requestRow?.config_json?.judger_model_profile || ""
  ).trim();
  const debugModelName = String(
    requestRow?.config_json?.judger_model_name || ""
  ).trim();
  const comparisons = Array.isArray(compareDetail?.report?.comparisons)
    ? compareDetail.report.comparisons
    : [];

  // No model_profile → API cannot resolve a model. Surface this for every
  // case so the artifact + run row record a clear configuration error.
  if (!judgeModelProfile) {
    const message = "judger_model_profile is required for LLM judge.";
    return {
      caseResults: comparisons.map((comparison) =>
        _summarizeCompareJudgeFailure(comparison, {
          code: "WORKFLOW_COMPARE_JUDGE_LLM_NOT_CONFIGURED",
          message,
        })
      ),
      judgeMeta: {
        judge_model_name: debugModelName || null,
        judge_model_profile: judgeModelProfile || null,
      },
    };
  }

  // Build all sentence-level compare packets up-front, then hand them to the
  // API in a single request. The API owns model resolution + LLM execution.
  const packetEntries = [];
  const roots = resolveWorkflowRunRoots(env);
  for (const comparison of comparisons) {
    const sourceCaseId = comparison.source_case_id || comparison.case_id;
    const baselineRoot = await resolveRunRootOrThrow(
      roots,
      compareDetail.compare.baseline_run_id
    );
    const candidateRoot = await resolveRunRootOrThrow(
      roots,
      compareDetail.compare.candidate_run_id
    );
    const baselineArtifact = await readJsonFile(
      caseArtifactPath(baselineRoot, compareDetail.compare.baseline_run_id, sourceCaseId)
    );
    const candidateArtifact = await readJsonFile(
      caseArtifactPath(candidateRoot, compareDetail.compare.candidate_run_id, sourceCaseId)
    );
    packetEntries.push({
      comparison,
      packet: {
        compare_id: compareDetail.compare.compare_id,
        case_id: comparison.case_id,
        sentence_id: comparison.sentence_id || null,
        sentence_text: comparison.sentence_text || "",
        reading_goal: compareDetail.compare.reading_goal || null,
        reading_variant: compareDetail.compare.reading_variant || null,
        baseline: summarizeCompareJudgeSentenceOutput(baselineArtifact, comparison),
        candidate: summarizeCompareJudgeSentenceOutput(candidateArtifact, comparison),
      },
    });
  }

  let apiPayload;
  try {
    // Scale the Directus -> API judge request timeout with the number of
    // packets the API will iterate through. A flat 60s would overrun for any
    // sentence-level compare with more than a handful of cases.
    const totalTimeoutMs = resolveWorkflowCompareJudgeTotalTimeoutMs(packetEntries.length);
    // Per-packet LLM call timeout: bounded by the total budget so a single
    // slow case cannot eat the entire request. With many packets we shrink it
    // proportionally; with few packets we let the API default (30s) handle it.
    const perPacketTimeoutSeconds = Math.min(
      30,
      Math.max(5, Math.floor((totalTimeoutMs / 1000) / Math.max(1, packetEntries.length))),
    );
    // Add a small safety margin so the API can finish cleanly and return a
    // partial_failure result instead of being cut off by the Directus proxy
    // at the exact same moment it tries to respond.
    const directusProxyTimeoutMs = totalTimeoutMs + 5000;
    apiPayload = await callEvalUpstreamJson({
      env,
      path: "/eval/article-analysis/workflow-lab/compare-judge",
      body: {
        schema_version: "workflow-compare-judge-v1",
        judge_run_id: requestRow.judge_run_id,
        compare_id: requestRow.compare_id,
        rubric_id: requestRow.rubric_id,
        rubric_version: requestRow.rubric_version || null,
        judge_model_profile: judgeModelProfile,
        // per-packet LLM call timeout (clamped to total budget server-side)
        timeout_seconds: perPacketTimeoutSeconds,
        // overall wall-clock budget for the entire request — the API uses
        // this to self-short-circuit and return a clean partial_failure
        // before the Directus proxy cuts the connection.
        total_timeout_seconds: totalTimeoutMs / 1000,
        packets: packetEntries.map((entry) => entry.packet),
      },
      timeoutMs: directusProxyTimeoutMs,
    });
  } catch (error) {
    return {
      caseResults: packetEntries.map(({ comparison }) =>
        _summarizeCompareJudgeFailure(comparison, error)
      ),
      judgeMeta: {
        judge_model_name: debugModelName || null,
        judge_model_profile: judgeModelProfile || null,
      },
    };
  }

  const resultsByCaseId = new Map();
  for (const item of apiPayload?.results || []) {
    if (item && typeof item.case_id === "string") {
      resultsByCaseId.set(item.case_id, item);
    }
  }

  const caseResults = packetEntries.map(({ comparison }) => {
    const judged = resultsByCaseId.get(comparison.case_id);
    if (!judged) {
      return _summarizeCompareJudgeFailure(comparison, {
        code: "WORKFLOW_COMPARE_JUDGE_RESULT_MISSING",
        message: "Compare judge API did not return a result for this case.",
      });
    }
    if (judged.status === "error") {
      return _summarizeCompareJudgeFailure(comparison, {
        code: judged.error?.code || "WORKFLOW_COMPARE_JUDGE_LLM_ERROR",
        message: judged.error?.message || "LLM judge execution failed.",
      });
    }
    const verdict = ["candidate_preferred", "baseline_preferred", "tie", "needs_review"].includes(judged.verdict)
      ? judged.verdict
      : "needs_review";
    const rawScore = Number(judged.overall_score);
    return {
      case_id: comparison.case_id,
      status: "succeeded",
      verdict,
      preferred_side:
        verdict === "candidate_preferred"
          ? "candidate"
          : verdict === "baseline_preferred"
            ? "baseline"
            : null,
      overall_score: Number.isFinite(rawScore)
        ? Math.max(0, Math.min(1, rawScore))
        : compareJudgeCaseScore(verdict),
      summary: String(judged.summary || "").slice(0, 1000) || "LLM judge returned no summary.",
      baseline_hard_failures: comparison.baseline_hard_failures ?? 0,
      baseline_soft_failures: comparison.baseline_soft_failures ?? 0,
      candidate_hard_failures: comparison.candidate_hard_failures ?? 0,
      candidate_soft_failures: comparison.candidate_soft_failures ?? 0,
      reasons: Array.isArray(judged.reasons)
        ? judged.reasons.map((item) => String(item).slice(0, 300))
        : [],
    };
  });
  return {
    caseResults,
    judgeMeta: {
      judge_model_name: apiPayload?.model_name || debugModelName || null,
      judge_model_profile: apiPayload?.profile_name || judgeModelProfile || null,
      judge_provider: apiPayload?.provider || null,
      latency_seconds: Number.isFinite(Number(apiPayload?.latency_seconds)) ? Number(apiPayload.latency_seconds) : null,
      input_tokens: Number.isFinite(Number(apiPayload?.input_tokens)) ? Number(apiPayload.input_tokens) : null,
      output_tokens: Number.isFinite(Number(apiPayload?.output_tokens)) ? Number(apiPayload.output_tokens) : null,
      total_tokens: Number.isFinite(Number(apiPayload?.total_tokens)) ? Number(apiPayload.total_tokens) : null,
      model_identity: {
        model_name: apiPayload?.model_name || debugModelName || null,
        profile_name: apiPayload?.profile_name || judgeModelProfile || null,
        provider: apiPayload?.provider || null,
      },
      runtime_summary: {
        latency_seconds: Number.isFinite(Number(apiPayload?.latency_seconds)) ? Number(apiPayload.latency_seconds) : null,
        input_tokens: Number.isFinite(Number(apiPayload?.input_tokens)) ? Number(apiPayload.input_tokens) : null,
        output_tokens: Number.isFinite(Number(apiPayload?.output_tokens)) ? Number(apiPayload.output_tokens) : null,
        total_tokens: Number.isFinite(Number(apiPayload?.total_tokens)) ? Number(apiPayload.total_tokens) : null,
      },
    },
  };
}

async function executeWorkflowCompareJudgeDirect(database, env, requestRow, compareDetail) {
  const judgeDir = compareJudgeArtifactDir(resolveWorkflowCompareRuntimeRoot(env), requestRow.compare_id, requestRow.judge_run_id);
  await database("eval_workflow_compare_judge_requests")
    .where({ id: requestRow.id })
    .update({
      status: "running",
      started_at: database.fn.now(),
      date_updated: database.fn.now(),
      lease_owner: "directus_inline",
      heartbeat_at: database.fn.now(),
      error_json: null,
    });
  try {
    const execution = requestRow.judge_adapter_kind === "llm"
      ? await buildWorkflowCompareLlmJudgeCaseResults(env, requestRow, compareDetail)
      : { caseResults: buildWorkflowCompareFakeJudgeCaseResults(compareDetail.report), judgeMeta: null };
    const caseResults = execution.caseResults;
    const artifacts = buildWorkflowCompareJudgeArtifacts({
      requestRow,
      compare: compareDetail.compare,
      caseResults,
      judgeMeta: execution.judgeMeta,
    });
    await writeJsonFile(path.join(judgeDir, "judge-run.json"), artifacts.judgeRun);
    await writeJsonFile(path.join(judgeDir, "case-results.json"), artifacts.caseResultsPayload);
    await writeJsonFile(path.join(judgeDir, "report.json"), artifacts.judgeReport);
    await writeFile(path.join(judgeDir, "report.md"), `${artifacts.reportMd}\n`, "utf8");
    const artifactPath = `runtime-evals/workflow-compares/${requestRow.compare_id}/judge/${requestRow.judge_run_id}`;
    const requestStatus = classifyWorkflowCompareJudgeRequestStatus(caseResults);
    const requestErrorJson = requestStatus === "succeeded"
      ? null
      : buildWorkflowCompareJudgeRequestErrorJson(caseResults);
    await database("eval_workflow_compare_judge_requests")
      .where({ id: requestRow.id })
      .update({
        status: requestStatus,
        artifact_path: artifactPath,
        finished_at: database.fn.now(),
        date_updated: database.fn.now(),
        heartbeat_at: database.fn.now(),
        error_json: requestErrorJson,
      });
  } catch (error) {
    await database("eval_workflow_compare_judge_requests")
      .where({ id: requestRow.id })
      .update({
        status: "failed",
        finished_at: database.fn.now(),
        date_updated: database.fn.now(),
        error_json: workflowRunRequestErrorJson(error),
      });
    throw error;
  }
  return database("eval_workflow_compare_judge_requests")
    .where({ id: requestRow.id })
    .first();
}

function validateWorkflowCompareJudgeRequest(body) {
  const errors = [];
  if (!body.compare_id || !isSafeFileId(body.compare_id)) {
    errors.push({ field: "compare_id", message: "compare_id is required and must be safe." });
  }
  if (!body.rubric_id || !isSafeFileId(body.rubric_id)) {
    errors.push({ field: "rubric_id", message: "rubric_id is required and must be safe." });
  }
  if (body.judge_run_id && !isSafeFileId(body.judge_run_id)) {
    errors.push({ field: "judge_run_id", message: "judge_run_id contains unsafe characters." });
  }
  if (body.judge_adapter_kind && !VALID_JUDGE_ADAPTER_KINDS.includes(body.judge_adapter_kind)) {
    errors.push({
      field: "judge_adapter_kind",
      message: `judge_adapter_kind must be one of: ${VALID_JUDGE_ADAPTER_KINDS.join(", ")}.`,
    });
  }
  if (body.config_json && (typeof body.config_json !== "object" || Array.isArray(body.config_json))) {
    errors.push({ field: "config_json", message: "config_json must be a JSON object." });
  }
  return errors;
}

async function listWorkflowCompareJudgeRequests(database, compareId, query = {}) {
  const limit = clampLimit(query?.limit);
  const status = String(query?.status || "all");
  if (status !== "all" && !VALID_JUDGE_REQUEST_STATUSES.includes(status)) {
    const error = new Error(`status must be one of: all, ${VALID_JUDGE_REQUEST_STATUSES.join(", ")}.`);
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    throw error;
  }
  const builder = database("eval_workflow_compare_judge_requests")
    .select([
      "id",
      "date_created",
      "date_updated",
      "judge_run_id",
      "compare_id",
      "baseline_run_id",
      "candidate_run_id",
      "rubric_id",
      "rubric_version",
      "status",
      "judge_adapter_kind",
      "config_json",
      "artifact_path",
      "source_request_id",
      "attempt_no",
      "max_attempts",
      "retry_reason",
      "lease_owner",
      "lease_until",
      "heartbeat_at",
      "started_at",
      "finished_at",
      "error_json",
    ])
    .orderBy("date_created", "desc")
    .limit(limit);
  if (compareId) builder.where({ compare_id: compareId });
  if (status !== "all") builder.where({ status });
  return builder;
}

async function createWorkflowCompareJudgeRequest(database, req, env, compareId, body = {}) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_JUDGE_QUEUE_UNAVAILABLE";
    throw error;
  }
  const payload = { ...body, compare_id: compareId };
  const validationErrors = validateWorkflowCompareJudgeRequest(payload);
  if (validationErrors.length > 0) {
    const error = new Error(validationErrors[0].message);
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = validationErrors[0].field;
    error.validationErrors = validationErrors;
    throw error;
  }
  const compare = await database("eval_workflow_compares")
    .where({ compare_id: compareId })
    .first();
  if (!compare) {
    const error = new Error(`Compare "${compareId}" was not found.`);
    error.status = 404;
    error.code = "WORKFLOW_COMPARE_NOT_FOUND";
    throw error;
  }
  if (!(await fileExists(compareArtifactPath(resolveWorkflowCompareRuntimeRoot(env), compareId, "report.json")))) {
    const error = new Error(`Compare "${compareId}" does not have a complete report artifact.`);
    error.status = 422;
    error.code = "WORKFLOW_COMPARE_INCOMPLETE";
    throw error;
  }
  const rubric = await findRubricSummary(env, payload.rubric_id);
  if (!rubric) {
    const error = new Error(`Rubric "${payload.rubric_id}" was not found.`);
    error.status = 422;
    error.code = "JUDGE_RUBRIC_NOT_FOUND";
    error.field = "rubric_id";
    throw error;
  }
  const judgeRunId = payload.judge_run_id || generateRunId("workflow-compare-judge");
  const existing = await database("eval_workflow_compare_judge_requests")
    .where({ compare_id: compareId, judge_run_id: judgeRunId })
    .first();
  if (existing) {
    const error = new Error(`Judge request "${judgeRunId}" already exists for compare "${compareId}".`);
    error.status = 409;
    error.code = "JUDGE_REQUEST_CONFLICT";
    throw error;
  }
  const artifactDir = compareJudgeArtifactDir(resolveWorkflowCompareRuntimeRoot(env), compareId, judgeRunId);
  if (await fileExists(artifactDir)) {
    const error = new Error(`Judge artifact directory "${judgeRunId}" already exists for compare "${compareId}".`);
    error.status = 409;
    error.code = "JUDGE_ARTIFACT_CONFLICT";
    throw error;
  }
  const row = workflowCompareJudgeRequestRow(req, {
    judge_run_id: judgeRunId,
    compare_id: compareId,
    baseline_run_id: compare.baseline_run_id,
    candidate_run_id: compare.candidate_run_id,
    rubric_id: rubric.id,
    rubric_version: rubric.version,
    judge_adapter_kind: payload.judge_adapter_kind || "fake",
    config_json: {
      source: "workflow_compare",
      max_concurrency: 1,
      ...(payload.config_json || {}),
    },
  });
  await database("eval_workflow_compare_judge_requests").insert(row);
  const inserted = await database("eval_workflow_compare_judge_requests")
    .where({ compare_id: compareId, judge_run_id: judgeRunId })
    .first();
  const compareDetail = await loadWorkflowCompareHistoryDetail(database, env, compareId);
  return executeWorkflowCompareJudgeDirect(database, env, inserted, compareDetail);
}

async function cancelWorkflowCompareJudgeRequest(database, req, compareId, requestId) {
  const current = await database("eval_workflow_compare_judge_requests")
    .select(["id", "status"])
    .where({ id: requestId, compare_id: compareId })
    .first();
  if (!current) {
    const error = new Error("Workflow compare judge request not found.");
    error.status = 404;
    error.code = "WORKFLOW_COMPARE_JUDGE_REQUEST_NOT_FOUND";
    throw error;
  }
  if (!isJudgeRunRequestCancelable(current.status)) {
    const error = new Error("Only queued or running compare judge requests can be cancelled.");
    error.status = 409;
    error.code = "WORKFLOW_COMPARE_JUDGE_REQUEST_NOT_CANCELABLE";
    throw error;
  }
  const updatedCount = await database("eval_workflow_compare_judge_requests")
    .where({ id: requestId, compare_id: compareId })
    .whereIn("status", ["queued", "running"])
    .update({
      status: "cancelled",
      finished_at: database.fn.now(),
      date_updated: database.fn.now(),
      user_updated: req.accountability?.user || null,
      error_json: null,
    });
  if (!updatedCount) {
    const error = new Error("Workflow compare judge request changed before it could be cancelled.");
    error.status = 409;
    error.code = "WORKFLOW_COMPARE_JUDGE_REQUEST_NOT_CANCELABLE";
    throw error;
  }
  return database("eval_workflow_compare_judge_requests").where({ id: requestId }).first();
}

async function retryWorkflowCompareJudgeRequest(database, req, env, compareId, requestId, body = {}) {
  const current = await database("eval_workflow_compare_judge_requests")
    .where({ id: requestId, compare_id: compareId })
    .first();
  if (!current) {
    const error = new Error("Workflow compare judge request not found.");
    error.status = 404;
    error.code = "WORKFLOW_COMPARE_JUDGE_REQUEST_NOT_FOUND";
    throw error;
  }
  if (!isJudgeRunRequestRetryable(current.status)) {
    const error = new Error("Only failed or cancelled compare judge requests can be retried.");
    error.status = 409;
    error.code = "WORKFLOW_COMPARE_JUDGE_REQUEST_NOT_RETRYABLE";
    throw error;
  }
  let judgeRunId = String(body?.judge_run_id || "").trim();
  if (!judgeRunId) {
    judgeRunId = buildRetryJudgeRunId(current.judge_run_id);
  }
  if (!isSafeFileId(judgeRunId)) {
    const error = new Error("judge_run_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "judge_run_id";
    throw error;
  }
  const existing = await database("eval_workflow_compare_judge_requests")
    .where({ compare_id: compareId, judge_run_id: judgeRunId })
    .first();
  if (existing) {
    const error = new Error(`Judge request "${judgeRunId}" already exists for compare "${compareId}".`);
    error.status = 409;
    error.code = "JUDGE_REQUEST_CONFLICT";
    throw error;
  }
  const artifactDir = compareJudgeArtifactDir(resolveWorkflowCompareRuntimeRoot(env), compareId, judgeRunId);
  if (await fileExists(artifactDir)) {
    const error = new Error(`Judge artifact directory "${judgeRunId}" already exists for compare "${compareId}".`);
    error.status = 409;
    error.code = "JUDGE_ARTIFACT_CONFLICT";
    throw error;
  }
  const previousAttemptNo = Number.parseInt(String(current.attempt_no || 1), 10);
  const attemptNo = (Number.isFinite(previousAttemptNo) && previousAttemptNo > 0 ? previousAttemptNo : 1) + 1;
  const previousMaxAttempts = Number.parseInt(String(current.max_attempts || 1), 10);
  const maxAttempts = Number.isFinite(previousMaxAttempts) ? Math.max(previousMaxAttempts, attemptNo) : attemptNo;
  const retryReason = String(body?.retry_reason || body?.reason || "").trim().slice(0, 1000) || null;
  const row = workflowCompareJudgeRequestRow(req, {
    judge_run_id: judgeRunId,
    compare_id: compareId,
    baseline_run_id: current.baseline_run_id,
    candidate_run_id: current.candidate_run_id,
    rubric_id: current.rubric_id,
    rubric_version: current.rubric_version,
    judge_adapter_kind: current.judge_adapter_kind || "fake",
    config_json: {
      ...normalizeConfigJson(current.config_json),
      source: "workflow_compare",
      max_concurrency: 1,
      retry_of_judge_run_id: current.judge_run_id,
      retry_reason: retryReason,
    },
  }, {
    source_request_id: current.id,
    attempt_no: attemptNo,
    max_attempts: maxAttempts,
    retry_reason: retryReason,
  });
  await database("eval_workflow_compare_judge_requests").insert(row);
  return database("eval_workflow_compare_judge_requests")
    .where({ compare_id: compareId, judge_run_id: judgeRunId })
    .first();
}

async function loadWorkflowCompareJudgeArtifact(env, compareId, judgeRunId) {
  const dir = compareJudgeArtifactDir(resolveWorkflowCompareRuntimeRoot(env), compareId, judgeRunId);
  const report = await readJsonFile(path.join(dir, "report.json"));
  const caseResults = await readJsonFile(path.join(dir, "case-results.json"));
  const judgeRun = await readJsonFile(path.join(dir, "judge-run.json"));
  const packetIds = await listJsonIds(path.join(dir, "packets"));
  return {
    summary: {
      ...workflowCompareJudgeRequestSummary({
        id: judgeRun?.request_id || judgeRunId,
        judge_run_id: judgeRun?.judge_run_id || judgeRunId,
        compare_id: compareId,
        baseline_run_id: judgeRun?.baseline_run_id || report?.baseline_run_id || null,
        candidate_run_id: judgeRun?.candidate_run_id || report?.candidate_run_id || null,
        rubric_id: report?.rubric_id || null,
        rubric_version: report?.rubric_version || null,
        status: report?.status || "succeeded",
        judge_adapter_kind: report?.judge_adapter_kind || null,
        artifact_path: `runtime-evals/workflow-compares/${compareId}/judge/${judgeRunId}`,
        config_json: judgeRun?.config_json || {},
        attempt_no: judgeRun?.attempt_no || 1,
        max_attempts: judgeRun?.max_attempts || 1,
        retry_reason: judgeRun?.retry_reason || null,
        date_created: report?.created_at || null,
        date_updated: report?.created_at || null,
        error_json: null,
      }),
      total_cases: report?.total_cases ?? null,
      candidate_preferred: report?.candidate_preferred ?? null,
      baseline_preferred: report?.baseline_preferred ?? null,
      tie: report?.tie ?? null,
      needs_review: report?.needs_review ?? null,
      errored: report?.errored ?? null,
      judge_model_name: report?.judge_model_name || judgeRun?.model_identity?.model_name || null,
      judge_model_profile: report?.judge_model_profile || judgeRun?.model_identity?.profile_name || null,
      judge_latency_seconds: report?.latency_seconds ?? judgeRun?.runtime_summary?.latency_seconds ?? null,
      judge_total_tokens: report?.total_tokens ?? judgeRun?.runtime_summary?.total_tokens ?? null,
      judge_input_tokens: report?.input_tokens ?? judgeRun?.runtime_summary?.input_tokens ?? null,
      judge_output_tokens: report?.output_tokens ?? judgeRun?.runtime_summary?.output_tokens ?? null,
    },
    judge_run: judgeRun,
    report,
    case_results: caseResults,
    packets: packetIds.map((id) => ({ id, href: `packets/${id}.json` })),
  };
}

async function createWorkflowLabCompare(database, env, body) {
  const baselineRunId = String(body?.baseline_run_id || "");
  const candidateRunId = String(body?.candidate_run_id || "");
  if (!isSafeFileId(baselineRunId) || !isSafeFileId(candidateRunId)) {
    const error = new Error("baseline_run_id and candidate_run_id are required safe ids.");
    error.status = 400;
    error.code = "BAD_REQUEST";
    throw error;
  }
  if (baselineRunId === candidateRunId) {
    const error = new Error("baseline_run_id and candidate_run_id must be different.");
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_ERROR";
    throw error;
  }
  const created = await createOrReuseWorkflowCompare({
    database,
    env,
    baselineRunId,
    candidateRunId,
    sourceKind: "history_compare",
  });
  return {
    created: created.created,
    compare_id: created.compare_id,
    detail: created.detail,
  };
}

async function loadCaseIndex(roots, runId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const indexPath = caseIndexPath(root, runId);
  if (!(await fileExists(indexPath))) return null;
  const index = await readJsonFile(indexPath);
  if (!Array.isArray(index.cases)) return null;
  return {
    schema_version: index.schema_version ?? null,
    run_id: index.run_id ?? runId,
    dataset_id: index.dataset_id ?? null,
    generated_at: index.generated_at ?? null,
    total_cases: Number.isFinite(index.total_cases) ? index.total_cases : index.cases.length,
    cases: index.cases,
  };
}

async function loadCaseArtifactSummaries(root, runId, dir) {
  const caseIds = await listJsonIds(path.join(dir, "cases"));
  const caseSummaries = [];
  for (const caseId of caseIds) {
    try {
      const artifact = await readJsonFile(caseArtifactPath(root, runId, caseId));
      caseSummaries.push(summarizeCaseArtifact(artifact));
    } catch {
      caseSummaries.push({
        case_id: caseId,
        run_id: runId,
        adapter_status: "unreadable",
        user_facing_state: null,
        error: null,
        warning_count: 0,
        drop_count: 0,
        hard_failures: 0,
        soft_failures: 0,
        grader_count: 0,
        failed_grader_count: 0,
        grader_summaries: [],
        translation_count: 0,
        inline_mark_count: 0,
        sentence_entry_count: 0,
        latency_seconds: null,
        total_tokens: null,
        input_tokens: null,
        output_tokens: null,
        workflow_identity: null,
        schema_identity: null,
        prompt_identity: null,
        model_identity: null,
      });
    }
  }
  return caseSummaries;
}

async function countJudgeArtifactDirs(dirPath) {
  try {
    const entries = await readdir(dirPath, { withFileTypes: true });
    return entries.filter((entry) => entry.isDirectory() && isSafeFileId(entry.name)).length;
  } catch (error) {
    if (error?.code === "ENOENT") return 0;
    throw error;
  }
}

async function listJudgeArtifacts(root, runId) {
  const dirPath = path.join(runDir(root, runId), "judge");
  let entries;
  try {
    entries = await readdir(dirPath, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
  const reports = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !isSafeFileId(entry.name)) continue;
    try {
      const report = await readJsonFile(path.join(dirPath, entry.name, "report.json"));
      reports.push(summarizeJudgeArtifact(runId, entry.name, report));
    } catch {
      reports.push({
        id: entry.name,
        judge_run_id: entry.name,
        run_id: runId,
        status: "unreadable",
        href: `/eval-center/runs/${encodeURIComponent(runId)}/judge/${encodeURIComponent(entry.name)}`,
      });
    }
  }
  return reports.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
}

async function loadJudgeArtifact(roots, runId, judgeRunId) {
  const root = await resolveRunRootOrThrow(roots, runId);
  const dir = judgeArtifactDir(root, runId, judgeRunId);
  const report = await readJsonFile(path.join(dir, "report.json"));
  const caseResults = await readJsonFile(path.join(dir, "case-results.json"));
  const judgeRun = await readJsonFile(path.join(dir, "judge-run.json"));
  const packetIds = await listJsonIds(path.join(dir, "packets"));
  return {
    summary: summarizeJudgeArtifact(runId, judgeRunId, report),
    judge_run: judgeRun,
    report,
    case_results: caseResults,
    packets: packetIds.map((id) => ({ id, href: `packets/${id}.json` })),
  };
}

function summarizeJudgeArtifact(runId, judgeRunId, report) {
  return {
    id: judgeRunId,
    judge_run_id: report?.judge_run_id || judgeRunId,
    run_id: report?.run_id || runId,
    rubric_id: report?.rubric_id || null,
    rubric_version: report?.rubric_version || null,
    judge_adapter_kind: report?.judge_adapter_kind || null,
    created_at: report?.created_at || null,
    total_cases: report?.total_cases ?? null,
    passed: report?.passed ?? null,
    failed: report?.failed ?? null,
    needs_review: report?.needs_review ?? null,
    errored: report?.errored ?? null,
    average_score: report?.average_score ?? null,
    href: `/eval-center/runs/${encodeURIComponent(runId)}/judge/${encodeURIComponent(judgeRunId)}`,
  };
}

function summarizePayload(payload) {
  if (!payload || typeof payload !== "object") return payload;
  const detail =
    Array.isArray(payload.detail)
      ? payload.detail
        .slice(0, 5)
        .map((entry) => {
          if (!entry || typeof entry !== "object") return String(entry);
          const location = Array.isArray(entry.loc) ? entry.loc.join(".") : undefined;
          return [location, entry.msg || entry.type].filter(Boolean).join(": ");
        })
        .filter(Boolean)
        .join(" | ")
      : payload.detail;
  const errors =
    Array.isArray(payload.errors)
      ? payload.errors.slice(0, 5).map((error) => ({
          message: error?.message,
          extensions: error?.extensions
            ? {
                code: error.extensions.code,
                field: error.extensions.field,
              }
            : undefined,
        }))
      : undefined;
  const messageFromErrors =
    Array.isArray(errors) && errors.length > 0
      ? errors.map((item) => item.message).filter(Boolean).join(" | ")
      : undefined;
  return {
    detail,
    message: payload.message || messageFromErrors,
    errors,
  };
}

function sendArtifactError(res, error) {
  const status = Number.isInteger(error?.status) ? error.status : 500;
  res.status(status).json({
    errors: [
      {
        message: status === 500 ? "Eval artifact reader failed." : error.message,
        extensions: {
          code: error?.code || "EVAL_ARTIFACT_READER_ERROR",
        },
      },
    ],
  });
}

async function parseUpstreamError(response) {
  const text = await response.text();
  if (!text) return { message: `Upstream request failed: ${response.status}` };
  try {
    return summarizePayload(JSON.parse(text));
  } catch {
    return { message: text.slice(0, 500) };
  }
}

async function callEvalUpstreamJson({ env, path: upstreamPath, body, timeoutMs }) {
  const baseUrl = readEnv(env, "CLAREAD_API_BASE_URL");
  const adminKey =
    readEnv(env, "CLAREAD_API_ADMIN_KEY") ||
    readEnv(env, "DAILY_READER_ADMIN_API_KEY");
  if (!baseUrl || !adminKey) {
    const error = new Error("Eval proxy is not configured.");
    error.status = 503;
    error.code = "SERVICE_UNAVAILABLE";
    throw error;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs || resolveTimeoutMs(env));
  try {
    const upstream = await fetch(joinUrl(baseUrl, upstreamPath), {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "x-admin-api-key": adminKey,
      },
      body: JSON.stringify(body ?? {}),
      signal: controller.signal,
    });
    if (!upstream.ok) {
      const errorPayload = await parseUpstreamError(upstream);
      const error = new Error(errorPayload?.detail || errorPayload?.message || "Upstream request failed.");
      error.status = upstream.status;
      error.code = "UPSTREAM_EVAL_ERROR";
      error.upstream_status = upstream.status;
      throw error;
    }
    return upstream.json();
  } finally {
    clearTimeout(timeout);
  }
}

const VALID_ADAPTER_KINDS = ["fake", "in_process", "http"];
const VALID_EVAL_PURPOSES = ["dataset_regression", "prompt_experiment", "manual_debug"];
const VALID_RAG_MODES = ["off", "baseline", "rag", "rag_fallback", "settings"];
const VALID_TRACE_SCOPES = ["off", "inherit"];
const VALID_EXECUTION_MODES = ["manual", "runner_bridge", "directus_async"];
const VALID_WORKFLOW_REQUEST_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"];
const VALID_JUDGE_ADAPTER_KINDS = ["fake", "llm"];
const VALID_JUDGE_REQUEST_STATUSES = ["queued", "running", "succeeded", "partial_failure", "failed", "cancelled"];

const CONFIG_PRESETS = [
  {
    id: "article-analysis-baseline-fake",
    label: "Baseline Fake（无 LLM，验证流程）",
    file: "article-analysis-baseline-fake.yaml",
  },
  {
    id: "article-analysis-no-few-shot-fake",
    label: "No-Few-Shot Fake（无 LLM，关闭 few-shot）",
    file: "article-analysis-no-few-shot-fake.yaml",
  },
  {
    id: "smoke-fake",
    label: "Smoke Fake（冒烟测试）",
    file: "smoke-fake.yaml",
  },
];

function generateRunId(prefix) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const rand = Math.random().toString(36).slice(2, 6);
  return `${prefix || "eval"}-${ts}-${rand}`;
}

function buildRetryRunId(sourceRunId, now = new Date(), suffix = Math.random().toString(36).slice(2, 6)) {
  const date = now instanceof Date ? now : new Date(now);
  const timestamp = Number.isNaN(date.getTime())
    ? new Date().toISOString()
    : date.toISOString();
  const compactTimestamp = timestamp.replace(/[-:.TZ]/g, "").slice(0, 14);
  const safeSource = isSafeFileId(sourceRunId) ? sourceRunId : "eval";
  const safeSuffix = String(suffix || "retry").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 8) || "retry";
  return `${safeSource}-retry-${compactTimestamp}-${safeSuffix}`;
}

function buildRetryJudgeRunId(sourceJudgeRunId, now = new Date(), suffix = Math.random().toString(36).slice(2, 6)) {
  return buildRetryRunId(sourceJudgeRunId, now, suffix);
}

function resolveEvalsRoot(env) {
  const configured = readEnv(env, "CLAREAD_EVALS_ROOT");
  if (configured) return configured;
  const runsRoot = resolveRunsRoot(env);
  return path.dirname(runsRoot);
}

function resolveNodeLabArtifactsRoot(env) {
  return readEnv(env, "CLAREAD_NODE_LAB_ARTIFACTS_ROOT") || "/directus/runtime-evals/node-lab";
}

function datasetsDir(evalsRoot) {
  return path.join(evalsRoot, "datasets");
}

function runConfigsDir(evalsRoot) {
  return path.join(evalsRoot, "run-configs");
}

function rubricsDir(evalsRoot) {
  return path.join(evalsRoot, "rubrics");
}

async function listDirectories(dirPath) {
  try {
    const entries = await readdir(dirPath, { withFileTypes: true });
    return entries.filter((e) => e.isDirectory()).map((e) => e.name);
  } catch {
    return [];
  }
}

function yamlListValues(yamlContent, fieldName) {
  const lines = String(yamlContent || "").split(/\r?\n/);
  const values = [];
  const fieldPattern = new RegExp(`^${fieldName}:\\s*$`);
  let inField = false;
  for (const line of lines) {
    if (!inField) {
      if (fieldPattern.test(line.trim())) {
        inField = true;
      }
      continue;
    }
    const listMatch = line.match(/^\s*-\s*(.+?)\s*$/);
    if (listMatch) {
      values.push(listMatch[1].trim().replace(/^["']|["']$/g, ""));
      continue;
    }
    if (!line.trim()) continue;
    break;
  }
  return values;
}

async function readWorkflowDatasetSummary(env, datasetId) {
  if (!isSafeFileId(datasetId)) return null;
  const evalsRoot = resolveEvalsRoot(env);
  const datasetPath = path.join(datasetsDir(evalsRoot), datasetId);
  const yamlPath = path.join(datasetPath, "dataset.yaml");
  const hasDatasetYaml = await fileExists(yamlPath);
  const yamlContent = hasDatasetYaml ? await readFile(yamlPath, "utf-8") : "";
  const caseIds = await listJsonIds(path.join(datasetPath, "cases"));
  return {
    id: datasetId,
    has_dataset_yaml: hasDatasetYaml,
    target: simpleYamlValue(yamlContent, "target", "article_analysis"),
    description: simpleYamlValue(yamlContent, "description", ""),
    tags: yamlListValues(yamlContent, "tags"),
    case_count: caseIds.length,
    case_ids: caseIds,
  };
}

async function listWorkflowDatasets(env) {
  const evalsRoot = resolveEvalsRoot(env);
  const dsDir = datasetsDir(evalsRoot);
  const datasetIds = await listDirectories(dsDir);
  const datasets = [];
  for (const id of datasetIds.sort()) {
    const summary = await readWorkflowDatasetSummary(env, id);
    if (summary) datasets.push(summary);
  }
  return datasets;
}

function normalizeTextList(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean);
  }
  if (typeof value === "string") {
    return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

function workflowDatasetYaml({ datasetId, description = "", tags = [] }) {
  const rows = [
    `id: ${datasetId}`,
    "schema_version: eval-dataset-v1",
    "target: article_analysis",
    `description: ${JSON.stringify(String(description || ""))}`,
    "case_globs:",
    "  - cases/*.json",
    "tags:",
  ];
  const cleanTags = normalizeTextList(tags);
  if (cleanTags.length === 0) {
    rows.push("  - prompt");
    rows.push("  - learning-workflow");
  } else {
    for (const tag of cleanTags) {
      rows.push(`  - ${JSON.stringify(tag)}`);
    }
  }
  return `${rows.join("\n")}\n`;
}

function workflowDatasetCaseId({ caseId, text, readingVariant }) {
  if (caseId && isSafeFileId(caseId)) return caseId;
  const prefix = isSafeFileId(readingVariant || "") ? readingVariant : "case";
  const digest = createHash("sha1").update(String(text || "").trim()).digest("hex").slice(0, 8);
  return `${prefix}-${digest}`;
}

function buildWorkflowDatasetCase({ request = {}, result = {}, seed = {} }) {
  const text = String(request?.text || "").trim();
  if (!text) {
    const error = new Error("Single run request text is required to create a dataset case.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "request.text";
    throw error;
  }
  const sceneRequest = result?.render_scene?.request && typeof result.render_scene.request === "object"
    ? result.render_scene.request
    : {};
  const readingGoal = String(request?.reading_goal || sceneRequest.reading_goal || "daily_reading");
  const readingVariant = String(request?.reading_variant || sceneRequest.reading_variant || "intermediate_reading");
  const caseId = workflowDatasetCaseId({
    caseId: seed?.case_id || "",
    text,
    readingVariant,
  });
  if (!isSafeFileId(caseId)) {
    const error = new Error("case_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "case_id";
    throw error;
  }
  return {
    id: caseId,
    origin: "dataset",
    text,
    reading_goal: readingGoal,
    reading_variant: readingVariant,
    source_type: String(request?.source_type || "user_input"),
    tags: normalizeTextList(seed?.tags),
    difficulty: seed?.difficulty ? String(seed.difficulty).trim() : null,
    target_phenomena: normalizeTextList(seed?.target_phenomena),
    expected: {
      min_translation_coverage: 0,
      allowed_warning_codes: [],
      tolerated_warning_codes: ["LOW_ENGLISH_RATIO", "TEXT_TYPE_NEEDS_CARE"],
      max_warning_count: null,
      max_drop_ratio: null,
    },
    reference_notes: seed?.reference_notes ? String(seed.reference_notes).trim() : null,
    extended: Boolean(seed?.extended),
  };
}

async function createWorkflowDataset({ env, body = {} }) {
  const datasetId = String(body.dataset_id || "").trim();
  if (!isSafeFileId(datasetId)) {
    const error = new Error("dataset_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "dataset_id";
    throw error;
  }
  const datasetPath = path.join(datasetsDir(resolveEvalsRoot(env)), datasetId);
  const yamlPath = path.join(datasetPath, "dataset.yaml");
  if (await fileExists(yamlPath)) {
    const error = new Error(`Dataset "${datasetId}" already exists.`);
    error.status = 409;
    error.code = "CONFLICT";
    error.field = "dataset_id";
    throw error;
  }
  let caseRecord = null;
  if (body.initial_case && typeof body.initial_case === "object") {
    caseRecord = buildWorkflowDatasetCase({
      request: body.initial_case.request || {},
      result: body.initial_case.result || {},
      seed: body.initial_case,
    });
  }
  await mkdir(path.join(datasetPath, "cases"), { recursive: true });
  await writeFile(
    yamlPath,
    workflowDatasetYaml({
      datasetId,
      description: body.description || "",
      tags: body.tags || [],
    }),
    "utf8",
  );

  let caseInfo = null;
  if (caseRecord) {
    const casePath = path.join(datasetPath, "cases", `${caseRecord.id}.json`);
    if (await fileExists(casePath)) {
      const error = new Error(`Case "${caseRecord.id}" already exists in dataset "${datasetId}".`);
      error.status = 409;
      error.code = "CONFLICT";
      error.field = "initial_case.case_id";
      throw error;
    }
    await writeFile(casePath, `${JSON.stringify(caseRecord, null, 2)}\n`, "utf8");
    caseInfo = { case_id: caseRecord.id };
  }

  return {
    dataset: await readWorkflowDatasetSummary(env, datasetId),
    ...(caseInfo ? { case: caseInfo, initial_case: caseInfo } : {}),
  };
}

async function appendWorkflowDatasetCase({ env, datasetId, body = {} }) {
  if (!isSafeFileId(datasetId)) {
    const error = new Error("dataset_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "dataset_id";
    throw error;
  }
  const datasetPath = path.join(datasetsDir(resolveEvalsRoot(env)), datasetId);
  const yamlPath = path.join(datasetPath, "dataset.yaml");
  if (!(await fileExists(yamlPath))) {
    const error = new Error(`Dataset "${datasetId}" was not found.`);
    error.status = 404;
    error.code = "NOT_FOUND";
    error.field = "dataset_id";
    throw error;
  }
  const caseRecord = buildWorkflowDatasetCase({
    request: body.request || {},
    result: body.result || {},
    seed: body,
  });
  const casePath = path.join(datasetPath, "cases", `${caseRecord.id}.json`);
  if (await fileExists(casePath)) {
    const error = new Error(`Case "${caseRecord.id}" already exists in dataset "${datasetId}".`);
    error.status = 409;
    error.code = "CONFLICT";
    error.field = "case_id";
    throw error;
  }
  await mkdir(path.join(datasetPath, "cases"), { recursive: true });
  await writeFile(casePath, `${JSON.stringify(caseRecord, null, 2)}\n`, "utf8");
  return {
    dataset: await readWorkflowDatasetSummary(env, datasetId),
    case: { case_id: caseRecord.id },
  };
}

async function listYamlIds(dirPath) {
  try {
    const entries = await readdir(dirPath, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isFile() && (entry.name.endsWith(".yaml") || entry.name.endsWith(".yml")))
      .map((entry) => entry.name.replace(/\.(yaml|yml)$/, ""))
      .sort();
  } catch {
    return [];
  }
}

async function listJudgeRubrics(env) {
  const dirPath = rubricsDir(resolveEvalsRoot(env));
  const ids = await listYamlIds(dirPath);
  const rubrics = [];
  for (const fileId of ids) {
    const yamlPath = await findYamlFile(dirPath, fileId);
    if (!yamlPath) continue;
    const content = await readFile(yamlPath, "utf-8");
    const id = simpleYamlValue(content, "id", fileId);
    if (!id || !isSafeFileId(id)) continue;
    rubrics.push({
      id,
      file_id: fileId,
      version: simpleYamlValue(content, "version", ""),
      target: simpleYamlValue(content, "target", ""),
      description: simpleYamlValue(content, "description", ""),
      criteria_count: countYamlListItems(content, "criteria"),
      path: `evals/rubrics/${path.basename(yamlPath)}`,
    });
  }
  return rubrics.sort((a, b) => a.id.localeCompare(b.id));
}

async function findYamlFile(dirPath, fileId) {
  const yamlPath = path.join(dirPath, `${fileId}.yaml`);
  if (await fileExists(yamlPath)) return yamlPath;
  const ymlPath = path.join(dirPath, `${fileId}.yml`);
  if (await fileExists(ymlPath)) return ymlPath;
  return null;
}

function countYamlListItems(yamlContent, fieldName) {
  const lines = String(yamlContent || "").split(/\r?\n/);
  const startIndex = lines.findIndex((line) => line.match(new RegExp(`^${fieldName}:\\s*$`)));
  if (startIndex < 0) return 0;
  let count = 0;
  for (const line of lines.slice(startIndex + 1)) {
    if (/^\S/.test(line)) break;
    if (/^\s*-\s+/.test(line)) count += 1;
  }
  return count;
}

function buildYamlContent(config) {
  const lines = [];
  lines.push(`run_id: "${config.run_id}"`);
  lines.push(`dataset_id: "${config.dataset_id}"`);
  lines.push(`mode: ${config.mode || "workflow"}`);
  lines.push(`eval_purpose: ${config.eval_purpose || "dataset_regression"}`);
  lines.push(`adapter_kind: ${config.adapter_kind || "fake"}`);
  lines.push(`prompt_version: null`);
  if (config.prompt_variant_id) {
    lines.push(`prompt_variant_id: "${config.prompt_variant_id}"`);
    if (config.prompt_override) {
      lines.push(`prompt_variant_path: null`);
      lines.push(`prompt_override: ${JSON.stringify(config.prompt_override)}`);
    } else {
      lines.push(`prompt_variant_path: ../prompt-variants/article-analysis/${config.prompt_variant_id}/manifest.yaml`);
    }
  } else {
    lines.push(`prompt_variant_id: null`);
  }
  lines.push(`workflow_version: null`);
  lines.push(`model_selection: {}`);
  lines.push(`rag_mode: ${config.rag_mode || "off"}`);
  lines.push(`trace_scope: ${config.trace_scope || "off"}`);
  // trace_project is a deprecated no-op. Backend ignores it; emit null so
  // YAML snapshots stop suggesting a per-call project switch is available.
  // See docs/operations/langsmith.md.
  lines.push(`trace_project: null`);
  lines.push(`timeout_seconds: ${config.timeout_seconds || 120}`);
  lines.push(`runs_root: ../runs`);
  lines.push(`datasets_root: ../datasets`);
  lines.push(`fake_latency_seconds: 0.0`);
  return lines.join("\n") + "\n";
}

function patchYamlRunId(yamlContent, runId) {
  const runIdLine = `run_id: "${runId}"`;
  const content = String(yamlContent || "");
  if (!content.trim()) return `${runIdLine}\n`;
  if (/^run_id:\s*.+$/m.test(content)) {
    return content.replace(/^run_id:\s*.+$/m, runIdLine);
  }
  return `${runIdLine}\n${content.endsWith("\n") ? content : `${content}\n`}`;
}

function stableJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function promptVariantSnapshotHash(manifest, baselinePromptVersion = null) {
  const raw = stableJson({
    baseline_prompt_version: baselinePromptVersion,
    manifest,
  });
  return createHash("sha256").update(raw, "utf8").digest("hex").slice(0, 16);
}

function normalizeJsonObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value;
}

function hasObjectKeys(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0);
}

function buildPromptVariantManifest(draft) {
  const storedManifest = normalizeJsonObject(draft?.manifest_json);
  if (storedManifest.schema_version === "workflow-prompt-bundle-v1") {
    return promptVariantManifestFromBundle(storedManifest, draft);
  }
  return {
    variant_id: draft.variant_id,
    target: draft.target || "article_analysis",
    description: draft.description || draft.notes || "",
    few_shot_mode: draft.few_shot_mode || "off",
    policies: normalizeJsonObject(draft.policies_json ?? draft.policies),
    examples: normalizeJsonObject(draft.examples_json ?? draft.examples),
  };
}

function renderPromptVariantYaml(manifest, snapshotHash) {
  const lines = [];
  lines.push(`variant_id: ${JSON.stringify(manifest.variant_id)}`);
  lines.push(`target: ${JSON.stringify(manifest.target)}`);
  lines.push(`description: ${JSON.stringify(manifest.description || "")}`);
  lines.push(`few_shot_mode: ${JSON.stringify(manifest.few_shot_mode || "off")}`);
  lines.push(`policies: ${JSON.stringify(manifest.policies || {})}`);
  lines.push(`examples: ${JSON.stringify(manifest.examples || {})}`);
  lines.push(`# prompt_snapshot_hash: ${snapshotHash}`);
  return lines.join("\n") + "\n";
}

function normalizeWorkflowAgentLayer(agentName, rawLayer = {}) {
  const layer = normalizeJsonObject(rawLayer);
  const examples = Array.isArray(layer.examples)
    ? layer.examples.filter((entry) => entry && typeof entry === "object" && !Array.isArray(entry))
    : [];
  const policyLines = Array.isArray(layer.policy_lines)
    ? layer.policy_lines.map((line) => String(line ?? "")).filter((line) => line.trim())
    : [];
  return {
    agent_name: layer.agent_name || agentName,
    label: layer.label || agentName,
    instructions: String(layer.instructions || ""),
    policy_name: layer.policy_name || null,
    policy_focus: layer.policy_focus || null,
    policy_variant: layer.policy_variant || null,
    policy_lines: policyLines,
    examples,
    prompt_template: String(layer.prompt_template || ""),
  };
}

function workflowBundleAgents(rawAgents) {
  const agents = normalizeJsonObject(rawAgents);
  return ["vocabulary", "grammar", "translation", "repair"].reduce((acc, agentName) => {
    acc[agentName] = normalizeWorkflowAgentLayer(agentName, agents[agentName]);
    return acc;
  }, {});
}

function promptVariantManifestFromBundle(bundle, row = {}) {
  const manifest = normalizeJsonObject(bundle);
  const variantId = manifest.variant_id || row.variant_id;
  const agents = workflowBundleAgents(manifest.agents);
  return {
    schema_version: "workflow-prompt-bundle-v1",
    variant_id: variantId,
    target: manifest.target || row.target || "article_analysis",
    description: manifest.description || row.description || row.notes || "",
    reading_goal: manifest.reading_goal || "daily_reading",
    reading_variant: manifest.reading_variant || "intermediate_reading",
    prompt_version: manifest.prompt_version || null,
    prompt_profile: manifest.prompt_profile || null,
    topology_mode: manifest.topology_mode || "learning",
    few_shot_mode: manifest.few_shot_mode || row.few_shot_mode || "baseline",
    agents,
    baseline_agents: workflowBundleAgents(manifest.baseline_agents || manifest.agents),
  };
}

function workflowBundlePromptOverride(manifest) {
  const instructions = {};
  const policies = {};
  const examples = {};

  for (const layer of Object.values(workflowBundleAgents(manifest.agents))) {
    const agentName = layer.agent_name;
    if (layer.instructions.trim()) {
      instructions[agentName] = layer.instructions;
    }
    if (layer.policy_name && layer.policy_focus) {
      const policyName = layer.policy_name;
      const focus = layer.policy_focus;
      const variant = layer.policy_variant || manifest.reading_variant;
      if (!variant) continue;
      policies[policyName] = normalizeJsonObject(policies[policyName]);
      policies[policyName][focus] = {
        ...(normalizeJsonObject(policies[policyName][focus])),
        [variant]: layer.policy_lines,
      };
    }
    if (Array.isArray(layer.examples) && layer.examples.length > 0) {
      const exampleVariant = layer.policy_variant || manifest.reading_variant;
      if (!exampleVariant) continue;
      examples[agentName] = normalizeJsonObject(examples[agentName]);
      examples[agentName][exampleVariant] = layer.examples;
    }
  }

  return {
    variant_id: manifest.variant_id,
    target: manifest.target || "article_analysis",
    description: manifest.description || "",
    few_shot_mode: manifest.few_shot_mode || "baseline",
    instructions,
    policies,
    examples,
  };
}

function workflowBundleSummary(manifest) {
  if (manifest.schema_version !== "workflow-prompt-bundle-v1") return null;
  const agents = workflowBundleAgents(manifest.agents);
  return {
    schema_version: manifest.schema_version,
    reading_goal: manifest.reading_goal || null,
    reading_variant: manifest.reading_variant || null,
    topology_mode: manifest.topology_mode || null,
    prompt_version: manifest.prompt_version || null,
    prompt_profile: manifest.prompt_profile || null,
    agents: Object.fromEntries(Object.entries(agents).map(([agentName, layer]) => ([
      agentName,
      {
        label: layer.label,
        instructions_chars: layer.instructions.length,
        policy_lines_count: layer.policy_lines.length,
        examples_count: layer.examples.length,
      },
    ]))),
  };
}

function promptVariantManifestFromRow(row) {
  const storedManifest = normalizeJsonObject(row?.manifest_json);
  if (hasObjectKeys(storedManifest)) {
    if (storedManifest.schema_version === "workflow-prompt-bundle-v1") {
      return promptVariantManifestFromBundle(storedManifest, row);
    }
    return {
      variant_id: storedManifest.variant_id || row.variant_id,
      target: storedManifest.target || row.target || "article_analysis",
      description: storedManifest.description || "",
      few_shot_mode: storedManifest.few_shot_mode || row.few_shot_mode || "off",
      policies: normalizeJsonObject(storedManifest.policies),
      examples: normalizeJsonObject(storedManifest.examples),
    };
  }
  return buildPromptVariantManifest(row || {});
}

function promptVariantSnapshotFromRow(row, baselinePromptVersion = null) {
  const manifest = promptVariantManifestFromRow(row);
  const snapshotHash = promptVariantSnapshotHash(manifest, baselinePromptVersion);
  const isWorkflowBundle = manifest.schema_version === "workflow-prompt-bundle-v1";
  const promptOverride = isWorkflowBundle
    ? workflowBundlePromptOverride(manifest)
    : { ...manifest };
  return {
    draft_id: row.id,
    variant_id: manifest.variant_id,
    target: manifest.target,
    status: row.status,
    scope: row.scope,
    few_shot_mode: manifest.few_shot_mode,
    snapshot_hash: snapshotHash,
    manifest_json: manifest,
    prompt_bundle_summary: workflowBundleSummary(manifest),
    prompt_override: {
      ...promptOverride,
      prompt_snapshot_hash: snapshotHash,
    },
    date_updated: row.date_updated || null,
    notes: row.notes || "",
  };
}

function workflowConfigWithPromptVariantSnapshot(config, row) {
  const snapshot = promptVariantSnapshotFromRow(row, config.prompt_version || null);
  return {
    ...config,
    prompt_variant_id: snapshot.variant_id,
    prompt_variant_snapshot_hash: snapshot.snapshot_hash,
    prompt_variant_manifest: snapshot.manifest_json,
    prompt_override: snapshot.prompt_override,
  };
}

function validatePromptVariantDraft(body) {
  const errors = [];
  if (!body.variant_id || !isSafeFileId(body.variant_id)) {
    errors.push({ field: "variant_id", message: "variant_id is required and may only contain letters, numbers, dots, underscores, and dashes." });
  }
  if (body.target && body.target !== "article_analysis") {
    errors.push({ field: "target", message: "target v1 must be article_analysis." });
  }
  if (body.few_shot_mode && !["off", "baseline", "variant", "settings"].includes(body.few_shot_mode)) {
    errors.push({ field: "few_shot_mode", message: "few_shot_mode must be one of: off, baseline, variant, settings." });
  }
  return errors;
}

function validateWorkflowRunRequest(body) {
  const errors = [];
  if (!body.dataset_id || !isSafeFileId(body.dataset_id)) {
    errors.push({ field: "dataset_id", message: "dataset_id is required and must be safe." });
  }
  if (body.adapter_kind && !VALID_ADAPTER_KINDS.includes(body.adapter_kind)) {
    errors.push({ field: "adapter_kind", message: `adapter_kind must be one of: ${VALID_ADAPTER_KINDS.join(", ")}.` });
  }
  if (body.eval_purpose && !VALID_EVAL_PURPOSES.includes(body.eval_purpose)) {
    errors.push({ field: "eval_purpose", message: `eval_purpose must be one of: ${VALID_EVAL_PURPOSES.join(", ")}.` });
  }
  if (body.rag_mode && !VALID_RAG_MODES.includes(body.rag_mode)) {
    errors.push({ field: "rag_mode", message: `rag_mode must be one of: ${VALID_RAG_MODES.join(", ")}.` });
  }
  if (body.trace_scope && !VALID_TRACE_SCOPES.includes(body.trace_scope)) {
    errors.push({ field: "trace_scope", message: `trace_scope must be one of: ${VALID_TRACE_SCOPES.join(", ")}.` });
  }
  if (body.execution_mode && !VALID_EXECUTION_MODES.includes(body.execution_mode)) {
    errors.push({ field: "execution_mode", message: `execution_mode must be one of: ${VALID_EXECUTION_MODES.join(", ")}.` });
  }
  if (body.model_selection && (typeof body.model_selection !== "object" || Array.isArray(body.model_selection))) {
    errors.push({ field: "model_selection", message: "model_selection must be a JSON object." });
  }
  if (body.run_id && !isSafeFileId(body.run_id)) {
    errors.push({ field: "run_id", message: "run_id contains unsafe characters." });
  }
  if (body.prompt_variant_id && !isSafeFileId(body.prompt_variant_id)) {
    errors.push({ field: "prompt_variant_id", message: "prompt_variant_id contains unsafe characters." });
  }
  if (body.prompt_variant_id && body.rag_mode && body.rag_mode !== "off") {
    errors.push({ field: "rag_mode", message: "Prompt variant snapshot v1 requires rag_mode=off." });
  }
  return errors;
}

function validateWorkflowSingleRunRequest(body) {
  const errors = [];
  const text = String(body?.text || "").trim();
  if (!text) {
    errors.push({ field: "text", message: "text is required." });
  }
  if (body.reading_goal === "academic") {
    errors.push({ field: "reading_goal", message: "eval-center v1 only supports learning topology; academic should use a dedicated academic lab/workflow" });
  }
  if (body.rag_mode && !VALID_RAG_MODES.includes(body.rag_mode)) {
    errors.push({ field: "rag_mode", message: `rag_mode must be one of: ${VALID_RAG_MODES.join(", ")}.` });
  }
  if (body.trace_scope && !VALID_TRACE_SCOPES.includes(body.trace_scope)) {
    errors.push({ field: "trace_scope", message: `trace_scope must be one of: ${VALID_TRACE_SCOPES.join(", ")}.` });
  }
  if (body.model_selection && (typeof body.model_selection !== "object" || Array.isArray(body.model_selection))) {
    errors.push({ field: "model_selection", message: "model_selection must be a JSON object." });
  }
  if (body.prompt_variant_id && !isSafeFileId(body.prompt_variant_id)) {
    errors.push({ field: "prompt_variant_id", message: "prompt_variant_id contains unsafe characters." });
  }
  if (body.prompt_variant_id && body.rag_mode && body.rag_mode !== "off") {
    errors.push({ field: "rag_mode", message: "Prompt variant snapshot v1 requires rag_mode=off." });
  }
  return errors;
}

async function createWorkflowLabSingleRun({
  database,
  env,
  body,
  callUpstream = callEvalUpstreamJson,
}) {
  const validationErrors = validateWorkflowSingleRunRequest(body || {});
  if (validationErrors.length > 0) {
    const error = new Error(validationErrors[0].message);
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = validationErrors[0].field;
    error.validation_errors = validationErrors;
    throw error;
  }

  const config = await attachPromptVariantSnapshot(database, {
    text: String(body.text || "").trim(),
    reading_goal: body.reading_goal || "daily_reading",
    reading_variant: body.reading_variant || "intermediate_reading",
    source_type: body.source_type || "user_input",
    extended: Boolean(body.extended),
    model_selection: normalizeJsonObject(body.model_selection),
    rag_mode: body.rag_mode || "off",
    trace_scope: body.trace_scope || "off",
    // trace_project is a deprecated no-op (see docs/operations/langsmith.md).
    // Kept on the config row for backwards compatibility with persisted
    // snapshots; never read by the backend and not surfaced to UI.
    trace_project: body.trace_project || null,
    timeout_seconds: body.timeout_seconds || 120,
    prompt_variant_id: body.prompt_variant_id || null,
  });

  const upstreamBody = {
    text: config.text,
    reading_goal: config.reading_goal,
    reading_variant: config.reading_variant,
    source_type: config.source_type,
    extended: config.extended,
    model_selection: config.model_selection,
    rag_mode: config.rag_mode,
    trace_scope: config.trace_scope,
    trace_project: config.trace_project,
    timeout_seconds: config.timeout_seconds,
    prompt_variant_id: config.prompt_variant_id,
    prompt_override: config.prompt_override || null,
  };

  const upstreamResult = await callUpstream({
    env,
    path: "/eval/article-analysis/workflow",
    body: upstreamBody,
    timeoutMs: resolveRequestTimeoutMs(env, upstreamBody),
  });

  const renderScene = upstreamResult?.render_scene
    && typeof upstreamResult.render_scene === "object"
    && !Array.isArray(upstreamResult.render_scene)
    ? upstreamResult.render_scene
    : upstreamResult
      && typeof upstreamResult === "object"
      && !Array.isArray(upstreamResult)
      && (
        Array.isArray(upstreamResult.translations)
        || Array.isArray(upstreamResult.inline_marks)
        || Array.isArray(upstreamResult.sentence_entries)
      )
      ? upstreamResult
      : {};
  const rawStatus = upstreamResult?.status || (Object.keys(renderScene).length > 0 ? "succeeded" : "failed");
  const status = ["succeeded", "failed", "timeout"].includes(rawStatus) ? rawStatus : "failed";
  const promptSnapshotHash = config.prompt_variant_snapshot_hash
    || config.prompt_override?.prompt_snapshot_hash
    || upstreamResult?.prompt_identity?.prompt_snapshot_hash
    || null;

  return {
    status,
    prompt_identity: {
      prompt_variant_id: upstreamResult?.prompt_identity?.prompt_variant_id || config.prompt_variant_id || null,
      prompt_snapshot_hash: promptSnapshotHash,
    },
    model_identity: upstreamResult?.model_identity || null,
    runtime_summary: upstreamResult?.runtime_summary || null,
    workflow_identity: upstreamResult?.workflow_identity || null,
    schema_identity: upstreamResult?.schema_identity || null,
    render_scene: renderScene,
    warnings: Array.isArray(renderScene?.warnings) ? renderScene.warnings : [],
    error: upstreamResult?.error || null,
  };
}

function workflowSingleRunHistoryRunId() {
  // Unique per execution. Re-running the same article + same configuration
  // is a NEW experiment and must always produce a new run_id, never reuse
  // the previous one. The stable grouping key lives in
  // buildWorkflowLabExperimentFingerprint, not in the run id.
  const ts = new Date().toISOString().replace(/[^0-9]/g, "").slice(0, 14); // YYYYMMDDHHMMSS
  const suffix = randomUUID().replace(/-/g, "").slice(0, 12);
  return `workflow-single-${ts}-${suffix}`;
}

// single run case_id 必须随 input / reading 上下文绑定;
// 不能用 "single-run" 字面量,否则 compare engine 会对所有 single run 共享同一 case_id,
// 产出一份毫无意义的伪 compare 报告
function workflowSingleRunCaseId(body = {}) {
  const text = String(body?.text || "").trim();
  const readingGoal = body?.reading_goal || "daily_reading";
  const readingVariant = body?.reading_variant || "intermediate_reading";
  const sourceType = body?.source_type || "user_input";
  const raw = stableJson({ text, reading_goal: readingGoal, reading_variant: readingVariant, source_type: sourceType });
  const digest = createHash("sha1").update(raw).digest("hex").slice(0, 12);
  return `single-run-${digest}`;
}

// 把一次 single-run 执行结果包装成 case artifact,供 in-memory compare 复用;
// 形状与 buildWorkflowSingleRunHistoryArtifact 内部 caseArtifact 一致,
// 区别是 run_id 在比较时尚未落盘,使用 synthetic id;落盘时由 history 路径替换
function buildSingleRunCaseArtifact({ body, result, runId }) {
  const renderScene = result?.render_scene && typeof result.render_scene === "object" && !Array.isArray(result.render_scene)
    ? result.render_scene
    : {};
  const workflowIdentity = result?.workflow_identity && typeof result.workflow_identity === "object"
    ? { topology_mode: "learning", ...result.workflow_identity }
    : { topology_mode: "learning" };
  const schemaIdentity = result?.schema_identity && typeof result.schema_identity === "object"
    ? { topology_mode: "learning", ...result.schema_identity }
    : { topology_mode: "learning" };
  const runtimeSummary = result?.runtime_summary && typeof result.runtime_summary === "object"
    ? result.runtime_summary
    : {};
  const aggregate = runtimeSummary?.aggregate && typeof runtimeSummary.aggregate === "object"
    ? runtimeSummary.aggregate
    : {};
  return {
    case_id: workflowSingleRunCaseId(body),
    run_id: runId,
    adapter_status: result?.status || "failed",
    user_facing_state: renderScene?.user_facing_state || null,
    error: result?.error || null,
    warnings: Array.isArray(result?.warnings) ? result.warnings : (Array.isArray(renderScene.warnings) ? renderScene.warnings : []),
    drop_log: Array.isArray(renderScene.drop_log) ? renderScene.drop_log : [],
    drop_log_summary: renderScene?.drop_log_summary || { total_drop_count: Array.isArray(renderScene.drop_log) ? renderScene.drop_log.length : 0 },
    grader_results: [],
    translations: Array.isArray(renderScene.translations) ? renderScene.translations : [],
    inline_marks: Array.isArray(renderScene.inline_marks) ? renderScene.inline_marks : [],
    sentence_entries: Array.isArray(renderScene.sentence_entries) ? renderScene.sentence_entries : [],
    latency_seconds: Number(runtimeSummary?.latency_ms || 0) / 1000,
    usage_summary: {
      total_tokens: aggregate?.total_tokens ?? runtimeSummary?.total_tokens ?? null,
      input_tokens: aggregate?.input_tokens ?? runtimeSummary?.input_tokens ?? null,
      output_tokens: aggregate?.output_tokens ?? runtimeSummary?.output_tokens ?? null,
    },
    workflow_identity: workflowIdentity,
    schema_identity: schemaIdentity,
    prompt_identity: result?.prompt_identity || null,
    model_identity: result?.model_identity || null,
    runtime_summary: runtimeSummary,
    render_scene: renderScene,
    input_snapshot: {
      text: String(body?.text || "").trim(),
      reading_goal: body?.reading_goal || "daily_reading",
      reading_variant: body?.reading_variant || "intermediate_reading",
      source_type: body?.source_type || "user_input",
    },
  };
}

// 在 in-memory single-run compare 上下文中,baseline 与 candidate 各自需要一个
// 临时 run_id 让 compare engine 接受;这里基于 input + prompt context 生成
function syntheticSingleRunCompareRunId({ body, side, promptVariantId }) {
  const raw = stableJson({
    text: String(body?.text || "").trim(),
    reading_goal: body?.reading_goal || "daily_reading",
    reading_variant: body?.reading_variant || "intermediate_reading",
    source_type: body?.source_type || "user_input",
    side,
    prompt_variant_id: promptVariantId || null,
  });
  const digest = createHash("sha1").update(raw).digest("hex").slice(0, 8);
  return `single-compare-${side}-${digest}`;
}

// Detect degenerate single-run-compare inputs: the case where both sides
// would resolve to the same experiment configuration, so any downstream
// compare report is meaningless. We treat the two sides as "the same
// experiment" when every compare identity signal is equal (or both
// missing). The signals, in order of strength:
//
//   1. prompt_variant_id (request-level, after null normalization).
//   2. prompt_snapshot_hash (post-run, from upstream result).
//   3. model_identity.profile_name (post-run, from upstream result).
//   4. model_identity.model_name (post-run, from upstream result).
//
// Returns a structured reason object when the inputs are degenerate, or
// null when there is at least one real difference. We do NOT compare
// the upstream text / reading_variant / source_type / model_selection /
// rag_mode — those are already equal by construction of the
// single-run-compare endpoint (shared body fields).
function workflowCompareIdentityDegenerateReason({
  baselineRequest,
  candidateRequest,
  baselineResult,
  candidateResult,
}) {
  // Bypass the identity-equality check when either side's run actually
  // failed. Comparing "null === null" in that state would surface as a
  // 422 input error and hide the real run failure, which is what the
  // downstream compare-status derivation is for. Let that path do its
  // job instead.
  //
  // We also bypass when either side is missing the identity surface
  // entirely (no prompt_identity AND no model_identity) — same
  // reasoning: a missing identity is a run-side failure signal, not a
  // "the user picked two identical experiments" signal.
  const baselineRunFailed = String(baselineResult?.status || "").toLowerCase() === "failed";
  const candidateRunFailed = String(candidateResult?.status || "").toLowerCase() === "failed";
  if (baselineRunFailed || candidateRunFailed) {
    return null;
  }
  const baselineHasIdentitySurface = Boolean(
    baselineResult?.prompt_identity || baselineResult?.model_identity,
  );
  const candidateHasIdentitySurface = Boolean(
    candidateResult?.prompt_identity || candidateResult?.model_identity,
  );
  if (!baselineHasIdentitySurface || !candidateHasIdentitySurface) {
    return null;
  }

  const baselinePromptVariant = baselineRequest?.prompt_variant_id
    || baselineResult?.prompt_identity?.prompt_variant_id
    || null;
  const candidatePromptVariant = candidateRequest?.prompt_variant_id
    || candidateResult?.prompt_identity?.prompt_variant_id
    || null;
  const baselineSnapshot = baselineResult?.prompt_identity?.prompt_snapshot_hash || null;
  const candidateSnapshot = candidateResult?.prompt_identity?.prompt_snapshot_hash || null;
  const baselineProfile = baselineResult?.model_identity?.profile_name
    || baselineResult?.model_identity?.provider
    || null;
  const candidateProfile = candidateResult?.model_identity?.profile_name
    || candidateResult?.model_identity?.provider
    || null;
  const baselineModelName = baselineResult?.model_identity?.model_name || null;
  const candidateModelName = candidateResult?.model_identity?.model_name || null;

  // Pre-call gate: if both prompt_variant_id are present, the earlier
  // check already rejected equality. This block catches "both null
  // (or absent) at request level AND identical post-run identity".
  if (
    (baselinePromptVariant === candidatePromptVariant)
    && (baselineSnapshot === candidateSnapshot)
    && (baselineProfile === candidateProfile)
    && (baselineModelName === candidateModelName)
  ) {
    return {
      message:
        "single-run-compare requires baseline and candidate to differ on at least one of: "
        + "prompt_variant_id, prompt_snapshot_hash, model profile, model name. "
        + "Both sides resolved to the same compare identity.",
      // The most actionable field is the candidate prompt_variant_id,
      // since the launcher always carries a candidate.
      field: "candidate.prompt_variant_id",
    };
  }
  return null;
}

// 双跑 single-run compare:同一篇文章并发跑 baseline + candidate,
// 直接物化成 compare-first workspace;底层 run artifact 私有化,UI 只消费 persisted compare。
async function createWorkflowLabSingleRunCompare({
  database,
  env,
  body,
  callUpstream = callEvalUpstreamJson,
}) {
  // 共享输入参数
  const sharedFields = {
    text: String(body?.text || "").trim(),
    reading_goal: body?.reading_goal || "daily_reading",
    reading_variant: body?.reading_variant || "intermediate_reading",
    source_type: body?.source_type || "user_input",
    model_selection: normalizeJsonObject(body?.model_selection),
    rag_mode: body?.rag_mode || "off",
    trace_scope: body?.trace_scope || "off",
    timeout_seconds: body?.timeout_seconds || 120,
  };
  // 校验:必填 text
  if (!sharedFields.text) {
    const error = new Error("text is required for single-run compare.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "text";
    throw error;
  }
  // 校验:baseline / candidate prompt_variant_id(任一可空,空时走 baseline)
  const baselinePromptId = body?.baseline?.prompt_variant_id || null;
  const candidatePromptId = body?.candidate?.prompt_variant_id || null;
  if (baselinePromptId && !isSafeFileId(baselinePromptId)) {
    const error = new Error("baseline.prompt_variant_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "baseline.prompt_variant_id";
    throw error;
  }
  if (candidatePromptId && !isSafeFileId(candidatePromptId)) {
    const error = new Error("candidate.prompt_variant_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "candidate.prompt_variant_id";
    throw error;
  }
  if (baselinePromptId && candidatePromptId && baselinePromptId === candidatePromptId) {
    // 同一个 prompt variant 不构成 compare
    const error = new Error("baseline.prompt_variant_id and candidate.prompt_variant_id must differ when both are provided.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "candidate.prompt_variant_id";
    throw error;
  }

  // 构造两侧 payload
  const baselineBody = {
    ...sharedFields,
    prompt_variant_id: baselinePromptId,
  };
  const candidateBody = {
    ...sharedFields,
    prompt_variant_id: candidatePromptId,
  };

  // 并发跑两侧;任一失败整体回滚
  let baselineResult;
  let candidateResult;
  try {
    [baselineResult, candidateResult] = await Promise.all([
      createWorkflowLabSingleRun({ database, env, body: baselineBody, callUpstream }),
      createWorkflowLabSingleRun({ database, env, body: candidateBody, callUpstream }),
    ]);
  } catch (error) {
    // 已经有 status / code 透传
    throw error;
  }

  // Defend against degenerate input: if the upstream run pipeline could
  // not surface any compare identity difference (no prompt_variant_id
  // difference, no prompt_snapshot_hash difference, no model_identity
  // difference), the two runs are the same experiment and the
  // downstream compare report is meaningless. The previous gate only
  // fired when both prompt_variant_id were non-null and equal; the
  // launcher requirement is "candidate is required", but the FE
  // requirement is not mirrored here. Close the gap.
  const identityReason = workflowCompareIdentityDegenerateReason({
    baselineRequest: baselineBody,
    candidateRequest: candidateBody,
    baselineResult,
    candidateResult,
  });
  if (identityReason) {
    const error = new Error(identityReason.message);
    error.status = 422;
    error.code = "WORKFLOW_LAB_COMPARE_DEGENERATE_INPUT";
    error.field = identityReason.field;
    throw error;
  }

  const savedBaseline = await saveWorkflowLabSingleRunToHistory({
    env,
    body: {
      request: baselineBody,
      result: baselineResult,
    },
  });
  const savedCandidate = await saveWorkflowLabSingleRunToHistory({
    env,
    body: {
      request: candidateBody,
      result: candidateResult,
    },
  });
  const compareCreate = await createOrReuseWorkflowCompare({
    database,
    env,
    baselineRunId: savedBaseline.record.run_id,
    candidateRunId: savedCandidate.record.run_id,
    sourceKind: "single_run_compare",
  });
  const detail = compareCreate.detail;
  const firstCaseId = detail?.report?.comparisons?.[0]?.case_id || null;
  const firstEvidence = firstCaseId
    ? await loadWorkflowCompareCaseEvidence(database, env, compareCreate.compare_id, firstCaseId)
    : null;
  const inputHash = computeInputHash({
    text: sharedFields.text,
    reading_goal: sharedFields.reading_goal,
    reading_variant: sharedFields.reading_variant,
    source_type: sharedFields.source_type,
  });
  const fingerprint = buildWorkflowLabExperimentFingerprint(
    {
      text: sharedFields.text,
      reading_goal: sharedFields.reading_goal,
      reading_variant: sharedFields.reading_variant,
      source_type: sharedFields.source_type,
    },
    {
      prompt_variant_id: baselineResult?.prompt_identity?.prompt_variant_id || baselineBody.prompt_variant_id || null,
      prompt_snapshot_hash: baselineResult?.prompt_identity?.prompt_snapshot_hash || null,
      model_profile: baselineResult?.model_identity?.profile_name || null,
      model_name: baselineResult?.model_identity?.model_name || null,
    },
    {
      prompt_variant_id: candidateResult?.prompt_identity?.prompt_variant_id || candidateBody.prompt_variant_id || null,
      prompt_snapshot_hash: candidateResult?.prompt_identity?.prompt_snapshot_hash || null,
      model_profile: candidateResult?.model_identity?.profile_name || null,
      model_name: candidateResult?.model_identity?.model_name || null,
    },
  );

  return {
    source: "persisted-compare",
    compare_id: compareCreate.compare_id,
    baseline: {
      result: baselineResult,
      case_artifact: firstEvidence?.baseline_artifact || null,
      run_id: savedBaseline.record.run_id,
    },
    candidate: {
      result: candidateResult,
      case_artifact: firstEvidence?.candidate_artifact || null,
      run_id: savedCandidate.record.run_id,
    },
    compare: {
      ...detail.compare,
      report: detail.report,
      evidence_index: detail.evidence_index,
      baseline_artifact: firstEvidence?.baseline_artifact || null,
      candidate_artifact: firstEvidence?.candidate_artifact || null,
      created: compareCreate.created,
    },
    input_snapshot: {
      text: sharedFields.text,
      reading_goal: sharedFields.reading_goal,
      reading_variant: sharedFields.reading_variant,
      source_type: sharedFields.source_type,
      input_hash: inputHash.input_hash,
    },
    experiment_fingerprint: fingerprint.experiment_fingerprint,
  };
}

function buildWorkflowSingleRunHistoryArtifact({ body, result, runId }) {
  const now = new Date().toISOString();
  const runtimeSummary = result?.runtime_summary && typeof result.runtime_summary === "object"
    ? result.runtime_summary
    : {};
  const aggregate = runtimeSummary?.aggregate && typeof runtimeSummary.aggregate === "object"
    ? runtimeSummary.aggregate
    : {};
  const renderScene = result?.render_scene && typeof result.render_scene === "object" && !Array.isArray(result.render_scene)
    ? result.render_scene
    : {};
  const workflowIdentity = result?.workflow_identity && typeof result.workflow_identity === "object"
    ? { topology_mode: "learning", ...result.workflow_identity }
    : { topology_mode: "learning" };
  const schemaIdentity = result?.schema_identity && typeof result.schema_identity === "object"
    ? { topology_mode: "learning", ...result.schema_identity }
    : { topology_mode: "learning" };
  const translations = Array.isArray(renderScene.translations) ? renderScene.translations : [];
  const inlineMarks = Array.isArray(renderScene.inline_marks) ? renderScene.inline_marks : [];
  const sentenceEntries = Array.isArray(renderScene.sentence_entries) ? renderScene.sentence_entries : [];
  const warnings = Array.isArray(result?.warnings)
    ? result.warnings
    : Array.isArray(renderScene.warnings)
      ? renderScene.warnings
      : [];
  const dropLog = Array.isArray(renderScene.drop_log) ? renderScene.drop_log : [];
  // case_id 必须随 input / reading 上下文绑定,否则所有 single run 共享 "single-run" 字面量,
  // compare engine 会把两条不同输入的 single run 误判为有共享 case;这里用 (text + reading context) 的稳定哈希
  const caseArtifact = {
    case_id: workflowSingleRunCaseId(body),
    run_id: runId,
    adapter_status: result?.status || "failed",
    user_facing_state: renderScene?.user_facing_state || null,
    error: result?.error || null,
    warnings,
    drop_log: dropLog,
    drop_log_summary: renderScene?.drop_log_summary || {
      total_drop_count: dropLog.length,
    },
    grader_results: [],
    translations,
    inline_marks: inlineMarks,
    sentence_entries: sentenceEntries,
    latency_seconds: Number(runtimeSummary?.latency_ms || 0) / 1000,
    usage_summary: {
      total_tokens: aggregate?.total_tokens ?? runtimeSummary?.total_tokens ?? null,
      input_tokens: aggregate?.input_tokens ?? runtimeSummary?.input_tokens ?? null,
      output_tokens: aggregate?.output_tokens ?? runtimeSummary?.output_tokens ?? null,
    },
    workflow_identity: workflowIdentity,
    schema_identity: schemaIdentity,
    prompt_identity: result?.prompt_identity || null,
    model_identity: result?.model_identity || null,
    runtime_summary: runtimeSummary,
    render_scene: renderScene,
    input_snapshot: {
      text: String(body?.text || "").trim(),
      reading_goal: body?.reading_goal || "daily_reading",
      reading_variant: body?.reading_variant || "intermediate_reading",
      source_type: body?.source_type || "user_input",
    },
    created_at: now,
  };
  const report = {
    created_at: now,
    total_cases: 1,
    passed: result?.status === "succeeded" ? 1 : 0,
    failed: result?.status === "succeeded" ? 0 : 1,
    errored: result?.status === "succeeded" ? 0 : 1,
    hard_failure_case_ids: [],
    soft_failure_case_ids: [],
    regression_list: [],
  };
  const run = {
    run_id: runId,
    created_at: now,
    dataset_id: "workflow-single-run",
    mode: "workflow_single_run",
    prompt_variant_id: result?.prompt_identity?.prompt_variant_id || null,
    prompt_snapshot_hash: result?.prompt_identity?.prompt_snapshot_hash || null,
    workflow_version: workflowIdentity?.workflow_version || null,
    rag_mode: body?.rag_mode || "off",
    trace_scope: body?.trace_scope || "off",
    reading_goal: body?.reading_goal || "daily_reading",
    reading_variant: body?.reading_variant || "intermediate_reading",
    source_type: body?.source_type || "user_input",
    model_identity: result?.model_identity || null,
    model_name: result?.model_identity?.model_name || null,
    model_profile: result?.model_identity?.profile_name || null,
    latency_seconds: caseArtifact.latency_seconds,
    total_tokens: caseArtifact.usage_summary?.total_tokens ?? null,
    usage_summary: caseArtifact.usage_summary,
    runtime_summary: runtimeSummary,
  };
  return {
    run,
    report,
    caseIndex: {
      schema_version: "1.0.0",
      generated_at: now,
      total_cases: 1,
      cases: [summarizeWorkflowCaseForIndex(caseArtifact)],
    },
    caseArtifact,
  };
}

async function saveWorkflowLabSingleRunToHistory({ env, body = {} }) {
  const request = body?.request && typeof body.request === "object" ? body.request : null;
  const result = body?.result && typeof body.result === "object" ? body.result : null;
  if (!request || !result) {
    const error = new Error("request and result are required.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    throw error;
  }
  // Always mint a fresh run_id. Two runs over the same article + same
  // configuration are still two separate experiments; reusing the old
  // run_id would silently swallow one of them.
  const runId = workflowSingleRunHistoryRunId();
  const roots = resolveWorkflowRunRoots(env);
  const artifact = buildWorkflowSingleRunHistoryArtifact({ body: request, result, runId });
  const runsRoot = resolveWorkflowRuntimeRunsRoot(env);
  const dir = runDir(runsRoot, runId);
  await mkdir(path.join(dir, "cases"), { recursive: true });
  await writeFile(path.join(dir, "run.json"), `${JSON.stringify(artifact.run, null, 2)}\n`, "utf8");
  await writeFile(path.join(dir, "report.json"), `${JSON.stringify(artifact.report, null, 2)}\n`, "utf8");
  await writeFile(path.join(dir, "case-index.json"), `${JSON.stringify(artifact.caseIndex, null, 2)}\n`, "utf8");
  // 文件名也用 case_id(已绑定 input / context),不再写死 "single-run.json"
  const singleRunCaseFile = `${isSafeFileId(artifact.caseArtifact.case_id) ? artifact.caseArtifact.case_id : "single-run"}.json`;
  await writeFile(path.join(dir, "cases", singleRunCaseFile), `${JSON.stringify(artifact.caseArtifact, null, 2)}\n`, "utf8");
  const summary = await loadRunSummary(roots, runId);
  return { record: workflowHistoryRecord(summary), duplicate: false };
}

function simpleYamlValue(yamlContent, fieldName, fallback = null) {
  const pattern = new RegExp(`^${fieldName}:\\s*(.+?)\\s*$`, "m");
  const match = String(yamlContent || "").match(pattern);
  if (!match) return fallback;
  const value = match[1].trim();
  if (value === "null" || value === "~") return null;
  return value.replace(/^["']|["']$/g, "");
}

function workflowRequestRow(req, config, options = {}) {
  const attemptNo = Number.parseInt(String(options.attempt_no || 1), 10);
  const safeAttemptNo = Number.isFinite(attemptNo) && attemptNo > 0 ? attemptNo : 1;
  const maxAttempts = Number.parseInt(String(options.max_attempts || safeAttemptNo), 10);
  return {
    run_id: config.run_id,
    status: "queued",
    dataset_id: config.dataset_id,
    mode: "workflow",
    eval_purpose: config.eval_purpose || "dataset_regression",
    adapter_kind: config.adapter_kind || "in_process",
    runner_kind: config.runner_kind || "external_worker",
    config_json: config,
    prompt_variant_id: config.prompt_variant_id || null,
    prompt_variant_snapshot_hash: config.prompt_variant_snapshot_hash || null,
    artifact_run_id: null,
    artifact_path: null,
    source_request_id: options.source_request_id || null,
    attempt_no: safeAttemptNo,
    max_attempts: Number.isFinite(maxAttempts) && maxAttempts >= safeAttemptNo
      ? maxAttempts
      : safeAttemptNo,
    retry_reason: options.retry_reason || null,
    max_concurrency: 1,
    user_created: req.accountability?.user || null,
  };
}

function workflowRunRequestSummary(row) {
  const config = row?.config_json && typeof row.config_json === "object"
    ? row.config_json
    : {};
  const errorJson = row?.error_json && typeof row.error_json === "object"
    ? row.error_json
    : null;
  return {
    id: row.id,
    run_id: row.run_id,
    status: row.status,
    dataset_id: row.dataset_id,
    mode: row.mode,
    eval_purpose: row.eval_purpose,
    adapter_kind: row.adapter_kind,
    runner_kind: row.runner_kind,
    prompt_variant_id: row.prompt_variant_id,
    prompt_variant_snapshot_hash: row.prompt_variant_snapshot_hash,
    artifact_run_id: row.artifact_run_id,
    artifact_path: row.artifact_path,
    expected_artifact_path: row.run_id
      ? row.runner_kind === "directus_async"
        ? `runtime-evals/workflow-runs/${row.run_id}`
        : `evals/runs/${row.run_id}`
      : null,
    source_request_id: row.source_request_id || null,
    attempt_no: row.attempt_no || 1,
    max_attempts: row.max_attempts || row.attempt_no || 1,
    retry_reason: row.retry_reason || null,
    cancelable: isWorkflowRunRequestCancelable(row.status),
    retryable: isWorkflowRunRequestRetryable(row.status),
    max_concurrency: row.max_concurrency,
    lease_owner: row.lease_owner,
    lease_until: row.lease_until,
    heartbeat_at: row.heartbeat_at,
    started_at: row.started_at,
    finished_at: row.finished_at,
    date_created: row.date_created,
    date_updated: row.date_updated,
    error: errorJson
      ? {
          code: errorJson.code || null,
          message: errorJson.message || null,
        }
      : null,
    config_summary: {
      execution_mode: config.execution_mode || "runner_bridge",
      preset_id: config.preset_id || null,
      rag_mode: config.rag_mode || null,
      trace_scope: config.trace_scope || null,
      timeout_seconds: config.timeout_seconds || null,
      config_file: config.config_file || null,
    },
  };
}

function isWorkflowRunRequestCancelable(status) {
  return ["queued", "running"].includes(status);
}

function isWorkflowRunRequestRetryable(status) {
  return ["failed", "cancelled"].includes(status);
}

function judgeRequestRow(req, config) {
  const attemptNo = Number.parseInt(String(config.attempt_no || 1), 10);
  const safeAttemptNo = Number.isFinite(attemptNo) && attemptNo > 0 ? attemptNo : 1;
  const maxAttempts = Number.parseInt(String(config.max_attempts || safeAttemptNo), 10);
  return {
    judge_run_id: config.judge_run_id,
    run_id: config.run_id,
    rubric_id: config.rubric_id,
    rubric_version: config.rubric_version,
    status: "queued",
    judge_adapter_kind: config.judge_adapter_kind || "fake",
    config_json: config.config_json || {},
    artifact_path: null,
    source_request_id: config.source_request_id || null,
    attempt_no: safeAttemptNo,
    max_attempts: Number.isFinite(maxAttempts) && maxAttempts >= safeAttemptNo
      ? maxAttempts
      : safeAttemptNo,
    retry_reason: config.retry_reason || null,
    user_created: req.accountability?.user || null,
  };
}

function judgeRunRequestSummary(row) {
  const config = row?.config_json && typeof row.config_json === "object"
    ? row.config_json
    : {};
  const errorJson = row?.error_json && typeof row.error_json === "object"
    ? row.error_json
    : null;
  return {
    id: row.id,
    judge_run_id: row.judge_run_id,
    run_id: row.run_id,
    rubric_id: row.rubric_id,
    rubric_version: row.rubric_version,
    status: row.status,
    judge_adapter_kind: row.judge_adapter_kind,
    artifact_path: row.artifact_path,
    expected_artifact_path: row.run_id && row.judge_run_id
      ? `evals/runs/${row.run_id}/judge/${row.judge_run_id}`
      : null,
    source_request_id: row.source_request_id || null,
    attempt_no: row.attempt_no || 1,
    max_attempts: row.max_attempts || row.attempt_no || 1,
    retry_reason: row.retry_reason || null,
    cancelable: isJudgeRunRequestCancelable(row.status),
    retryable: isJudgeRunRequestRetryable(row.status),
    lease_owner: row.lease_owner,
    lease_until: row.lease_until,
    heartbeat_at: row.heartbeat_at,
    started_at: row.started_at,
    finished_at: row.finished_at,
    date_created: row.date_created,
    date_updated: row.date_updated,
    error: errorJson
      ? {
          code: errorJson.code || null,
          message: errorJson.message || null,
        }
      : null,
    config_summary: {
      source: config.source || null,
      max_concurrency: config.max_concurrency || 1,
      max_cases: config.max_cases || null,
      source_text_char_limit: config.source_text_char_limit || null,
      output_item_limit: config.output_item_limit || null,
    },
  };
}

function isJudgeRunRequestCancelable(status) {
  return ["queued", "running"].includes(status);
}

function isJudgeRunRequestRetryable(status) {
  return ["failed", "cancelled"].includes(status);
}

function validateJudgeRunRequest(body) {
  const errors = [];
  if (!body.run_id || !isSafeFileId(body.run_id)) {
    errors.push({ field: "run_id", message: "run_id is required and must be safe." });
  }
  if (!body.rubric_id || !isSafeFileId(body.rubric_id)) {
    errors.push({ field: "rubric_id", message: "rubric_id is required and must be safe." });
  }
  if (body.judge_run_id && !isSafeFileId(body.judge_run_id)) {
    errors.push({ field: "judge_run_id", message: "judge_run_id contains unsafe characters." });
  }
  if (body.judge_adapter_kind && !VALID_JUDGE_ADAPTER_KINDS.includes(body.judge_adapter_kind)) {
    errors.push({
      field: "judge_adapter_kind",
      message: `judge_adapter_kind must be one of: ${VALID_JUDGE_ADAPTER_KINDS.join(", ")}.`,
    });
  }
  if (body.config_json && (typeof body.config_json !== "object" || Array.isArray(body.config_json))) {
    errors.push({ field: "config_json", message: "config_json must be a JSON object." });
  }
  return errors;
}

async function createJudgeRunRequest(database, req, env, body) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_JUDGE_QUEUE_UNAVAILABLE";
    throw error;
  }
  const validationErrors = validateJudgeRunRequest(body);
  if (validationErrors.length > 0) {
    const error = new Error(validationErrors[0].message);
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = validationErrors[0].field;
    error.validationErrors = validationErrors;
    throw error;
  }

  const readableRoots = resolveWorkflowRunRoots(env);
  const sourceRoot = await findExistingRunRoot(readableRoots, body.run_id);
  const runPath = runDir(sourceRoot || readableRoots[0], body.run_id);
  if (!(await fileExists(path.join(runPath, "report.json"))) || !(await fileExists(path.join(runPath, "case-index.json")))) {
    const error = new Error(`Run "${body.run_id}" does not have complete report.json and case-index.json artifacts.`);
    error.status = 422;
    error.code = "JUDGE_SOURCE_RUN_INCOMPLETE";
    error.field = "run_id";
    throw error;
  }

  const rubric = await findRubricSummary(env, body.rubric_id);
  if (!rubric) {
    const error = new Error(`Rubric "${body.rubric_id}" was not found.`);
    error.status = 422;
    error.code = "JUDGE_RUBRIC_NOT_FOUND";
    error.field = "rubric_id";
    throw error;
  }
  if (body.rubric_version && body.rubric_version !== rubric.version) {
    const error = new Error(`Rubric version mismatch: request=${body.rubric_version}, file=${rubric.version}.`);
    error.status = 422;
    error.code = "JUDGE_RUBRIC_VERSION_MISMATCH";
    error.field = "rubric_version";
    throw error;
  }

  const judgeRunId = body.judge_run_id || generateRunId("judge");
  const artifactRoot = sourceRoot || resolveWorkflowRuntimeRunsRoot(env);
  const artifactDir = judgeArtifactDir(artifactRoot, body.run_id, judgeRunId);
  if (await fileExists(artifactDir)) {
    const error = new Error(`Judge artifact directory "${judgeRunId}" already exists for run "${body.run_id}".`);
    error.status = 409;
    error.code = "JUDGE_ARTIFACT_CONFLICT";
    error.field = "judge_run_id";
    throw error;
  }

  const existingRequest = await database("eval_judge_run_requests")
    .select(["id"])
    .where({ run_id: body.run_id, judge_run_id: judgeRunId })
    .first();
  if (existingRequest) {
    const error = new Error(`Judge request "${judgeRunId}" already exists for run "${body.run_id}".`);
    error.status = 409;
    error.code = "JUDGE_REQUEST_CONFLICT";
    error.field = "judge_run_id";
    throw error;
  }

  const config = {
    judge_run_id: judgeRunId,
    run_id: body.run_id,
    rubric_id: rubric.id,
    rubric_version: rubric.version,
    judge_adapter_kind: body.judge_adapter_kind || "fake",
    config_json: {
      source: "directus_eval_center",
      max_concurrency: 1,
      ...(body.config_json || {}),
    },
  };
  const row = judgeRequestRow(req, config);
  await database("eval_judge_run_requests").insert(row);
  return database("eval_judge_run_requests")
    .where({ run_id: body.run_id, judge_run_id: judgeRunId })
    .first();
}

async function findRubricSummary(env, rubricId) {
  const rubrics = await listJudgeRubrics(env);
  return rubrics.find((item) => item.id === rubricId || item.file_id === rubricId) || null;
}

async function listJudgeRunRequests(database, query) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_JUDGE_QUEUE_UNAVAILABLE";
    throw error;
  }

  const limit = clampLimit(query?.limit);
  const status = String(query?.status || "all");
  if (status !== "all" && !VALID_JUDGE_REQUEST_STATUSES.includes(status)) {
    const error = new Error(`status must be one of: all, ${VALID_JUDGE_REQUEST_STATUSES.join(", ")}.`);
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    throw error;
  }
  const runId = String(query?.run_id || "");
  if (runId && !isSafeFileId(runId)) {
    const error = new Error("run_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "run_id";
    throw error;
  }

  const builder = database("eval_judge_run_requests")
    .select([
      "id",
      "date_created",
      "date_updated",
      "judge_run_id",
      "run_id",
      "rubric_id",
      "rubric_version",
      "status",
      "judge_adapter_kind",
      "config_json",
      "artifact_path",
      "source_request_id",
      "attempt_no",
      "max_attempts",
      "retry_reason",
      "lease_owner",
      "lease_until",
      "heartbeat_at",
      "started_at",
      "finished_at",
      "error_json",
    ])
    .orderBy("date_created", "desc")
    .limit(limit);

  if (status !== "all") builder.where({ status });
  if (runId) builder.where({ run_id: runId });
  return builder;
}

async function ensureJudgeRunIdAvailable(database, env, runId, judgeRunId) {
  if (!isSafeFileId(judgeRunId)) {
    const error = new Error("judge_run_id contains unsafe characters.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "judge_run_id";
    throw error;
  }

  const existingRequest = await database("eval_judge_run_requests")
    .select(["id"])
    .where({ run_id: runId, judge_run_id: judgeRunId })
    .first();
  if (existingRequest) {
    const error = new Error(`Judge request "${judgeRunId}" already exists for run "${runId}".`);
    error.status = 409;
    error.code = "JUDGE_REQUEST_CONFLICT";
    error.field = "judge_run_id";
    throw error;
  }

  const artifactRoot = (await findExistingRunRoot(resolveWorkflowRunRoots(env), runId))
    || resolveWorkflowRuntimeRunsRoot(env);
  const artifactDir = judgeArtifactDir(artifactRoot, runId, judgeRunId);
  if (await fileExists(artifactDir)) {
    const error = new Error(`Judge artifact directory "${judgeRunId}" already exists for run "${runId}".`);
    error.status = 409;
    error.code = "JUDGE_ARTIFACT_CONFLICT";
    error.field = "judge_run_id";
    throw error;
  }
}

async function cancelJudgeRunRequest(database, req, requestId) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_JUDGE_QUEUE_UNAVAILABLE";
    throw error;
  }

  const current = await database("eval_judge_run_requests")
    .select(["id", "status"])
    .where({ id: requestId })
    .first();

  if (!current) {
    const error = new Error("Judge run request not found.");
    error.status = 404;
    error.code = "JUDGE_RUN_REQUEST_NOT_FOUND";
    throw error;
  }
  if (!isJudgeRunRequestCancelable(current.status)) {
    const error = new Error("Only queued or running judge run requests can be cancelled in v1.");
    error.status = 409;
    error.code = "JUDGE_RUN_REQUEST_NOT_CANCELABLE";
    throw error;
  }

  const updatedCount = await database("eval_judge_run_requests")
    .where({ id: requestId })
    .whereIn("status", ["queued", "running"])
    .update({
      status: "cancelled",
      finished_at: database.fn.now(),
      date_updated: database.fn.now(),
      user_updated: req.accountability?.user || null,
      error_json: null,
    });

  if (!updatedCount) {
    const error = new Error("Judge run request changed before it could be cancelled.");
    error.status = 409;
    error.code = "JUDGE_RUN_REQUEST_NOT_CANCELABLE";
    throw error;
  }

  return database("eval_judge_run_requests").where({ id: requestId }).first();
}

async function retryJudgeRunRequest(database, req, env, requestId, body = {}) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_JUDGE_QUEUE_UNAVAILABLE";
    throw error;
  }

  const current = await database("eval_judge_run_requests")
    .select([
      "id",
      "judge_run_id",
      "run_id",
      "rubric_id",
      "rubric_version",
      "status",
      "judge_adapter_kind",
      "config_json",
      "attempt_no",
      "max_attempts",
    ])
    .where({ id: requestId })
    .first();

  if (!current) {
    const error = new Error("Judge run request not found.");
    error.status = 404;
    error.code = "JUDGE_RUN_REQUEST_NOT_FOUND";
    throw error;
  }
  if (!isJudgeRunRequestRetryable(current.status)) {
    const error = new Error("Only failed or cancelled judge run requests can be retried in v1.");
    error.status = 409;
    error.code = "JUDGE_RUN_REQUEST_NOT_RETRYABLE";
    throw error;
  }

  let judgeRunId = String(body?.judge_run_id || "").trim();
  if (judgeRunId) {
    await ensureJudgeRunIdAvailable(database, env, current.run_id, judgeRunId);
  } else {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const candidateJudgeRunId = buildRetryJudgeRunId(current.judge_run_id);
      try {
        await ensureJudgeRunIdAvailable(database, env, current.run_id, candidateJudgeRunId);
        judgeRunId = candidateJudgeRunId;
        break;
      } catch (error) {
        if (error?.status !== 409) throw error;
      }
    }
    if (!judgeRunId) {
      const error = new Error("Could not generate an available judge_run_id.");
      error.status = 409;
      error.code = "JUDGE_REQUEST_CONFLICT";
      error.field = "judge_run_id";
      throw error;
    }
  }

  const previousAttemptNo = Number.parseInt(String(current.attempt_no || 1), 10);
  const attemptNo = (Number.isFinite(previousAttemptNo) && previousAttemptNo > 0
    ? previousAttemptNo
    : 1) + 1;
  const previousMaxAttempts = Number.parseInt(String(current.max_attempts || 1), 10);
  const maxAttempts = Number.isFinite(previousMaxAttempts)
    ? Math.max(previousMaxAttempts, attemptNo)
    : attemptNo;
  const retryReason = String(body?.retry_reason || body?.reason || "").trim().slice(0, 1000) || null;
  const originalConfig = normalizeConfigJson(current.config_json);
  const row = judgeRequestRow(req, {
    judge_run_id: judgeRunId,
    run_id: current.run_id,
    rubric_id: current.rubric_id,
    rubric_version: current.rubric_version,
    judge_adapter_kind: originalConfig.judge_adapter_kind || current.judge_adapter_kind || "fake",
    config_json: {
      ...originalConfig,
      source: originalConfig.source || "directus_eval_center",
      max_concurrency: originalConfig.max_concurrency || 1,
      retry_of_judge_run_id: current.judge_run_id,
      retry_reason: retryReason,
    },
    source_request_id: current.id,
    attempt_no: attemptNo,
    max_attempts: maxAttempts,
    retry_reason: retryReason,
  });

  await database("eval_judge_run_requests").insert(row);
  return database("eval_judge_run_requests")
    .where({ run_id: current.run_id, judge_run_id: judgeRunId })
    .first();
}

async function createWorkflowRunRequest(database, req, config) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_RUNNER_BRIDGE_UNAVAILABLE";
    throw error;
  }
  const row = workflowRequestRow(req, config);
  await database("eval_workflow_run_requests").insert(row);
  return database("eval_workflow_run_requests")
    .where({ run_id: config.run_id })
    .first();
}

async function listReadyPromptVariantSnapshots(database) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_PROMPT_VARIANTS_UNAVAILABLE";
    throw error;
  }

  const rows = await database("eval_prompt_variant_drafts")
    .select([
      "id",
      "date_updated",
      "variant_id",
      "target",
      "status",
      "scope",
      "few_shot_mode",
      "policies_json",
      "examples_json",
      "manifest_json",
      "snapshot_hash",
      "notes",
    ])
    .where({ status: "ready_for_eval", scope: "workflow_lab" })
    .orderBy("date_updated", "desc")
    .limit(100);
  return rows.map((row) => promptVariantSnapshotFromRow(row));
}

async function attachPromptVariantSnapshot(database, config) {
  if (!config.prompt_variant_id) return config;
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_PROMPT_VARIANTS_UNAVAILABLE";
    throw error;
  }
  if (config.rag_mode && config.rag_mode !== "off") {
    const error = new Error("Prompt variant snapshot v1 requires rag_mode=off.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "rag_mode";
    throw error;
  }

  const row = await database("eval_prompt_variant_drafts")
    .select([
      "id",
      "date_updated",
      "variant_id",
      "target",
      "status",
      "scope",
      "few_shot_mode",
      "policies_json",
      "examples_json",
      "manifest_json",
      "snapshot_hash",
      "notes",
    ])
    .where({
      variant_id: config.prompt_variant_id,
      status: "ready_for_eval",
      scope: "workflow_lab",
    })
    .first();

  if (!row) {
    const error = new Error(
      `Prompt variant "${config.prompt_variant_id}" is not ready_for_eval for workflow_lab.`,
    );
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "prompt_variant_id";
    throw error;
  }

  return workflowConfigWithPromptVariantSnapshot(config, row);
}

async function listWorkflowRunRequests(database, query) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_RUNNER_BRIDGE_UNAVAILABLE";
    throw error;
  }

  const limit = clampLimit(query?.limit);
  const status = String(query?.status || "all");
  if (status !== "all" && !VALID_WORKFLOW_REQUEST_STATUSES.includes(status)) {
    const error = new Error(
      `status must be one of: all, ${VALID_WORKFLOW_REQUEST_STATUSES.join(", ")}.`,
    );
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    throw error;
  }

  const builder = database("eval_workflow_run_requests")
    .select([
      "id",
      "date_created",
      "date_updated",
      "run_id",
      "status",
      "dataset_id",
      "mode",
      "eval_purpose",
      "adapter_kind",
      "runner_kind",
      "config_json",
      "prompt_variant_id",
      "prompt_variant_snapshot_hash",
      "artifact_run_id",
      "artifact_path",
      "source_request_id",
      "attempt_no",
      "max_attempts",
      "retry_reason",
      "max_concurrency",
      "lease_owner",
      "lease_until",
      "heartbeat_at",
      "started_at",
      "finished_at",
      "error_json",
    ])
    .orderBy("date_created", "desc")
    .limit(limit);

  if (status !== "all") builder.where({ status });
  return builder;
}

function workflowRequestError(message, status, code, field = null) {
  const error = new Error(message);
  error.status = status;
  error.code = code;
  if (field) error.field = field;
  return error;
}

function normalizeConfigJson(value) {
  if (!value) return {};
  if (typeof value === "object" && !Array.isArray(value)) return value;
  if (typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function workflowRunRequestErrorJson(error) {
  if (error && typeof error === "object" && error.code && error.message) {
    return { code: error.code, message: String(error.message).slice(0, 1000) };
  }
  return {
    code: error?.name || "WorkflowRunError",
    message: String(error?.message || "Workflow run failed.").slice(0, 1000),
  };
}

// Workflow compare judge request status is derived from the per-case results:
// every case errored -> failed, some -> partial_failure, none -> succeeded.
// We deliberately do NOT collapse "partial_failure" into "succeeded" so the UI
// does not show a green check when individual cases failed.
function classifyWorkflowCompareJudgeRequestStatus(caseResults) {
  const results = Array.isArray(caseResults) ? caseResults : [];
  if (results.length === 0) return "succeeded";
  const errored = results.filter((item) => item && item.status === "error").length;
  if (errored === results.length) return "failed";
  if (errored > 0) return "partial_failure";
  return "succeeded";
}

function buildWorkflowCompareJudgeRequestErrorJson(caseResults) {
  const results = Array.isArray(caseResults) ? caseResults : [];
  const errored = results.filter((item) => item && item.status === "error");
  if (errored.length === 0) return null;
  const codes = Array.from(new Set(errored.map((item) => item.error?.code || "WORKFLOW_COMPARE_JUDGE_CASE_ERROR")));
  const sample = errored.slice(0, 3).map((item) => item.error?.message || "case failed").join(" | ");
  return {
    code: errored.length === results.length ? "WORKFLOW_COMPARE_JUDGE_ALL_CASES_FAILED" : "WORKFLOW_COMPARE_JUDGE_PARTIAL_FAILURE",
    message: `${errored.length}/${results.length} cases failed. Codes: ${codes.join(", ")}. Sample: ${sample}`.slice(0, 1000),
    errored_cases: errored.length,
    total_cases: results.length,
    error_codes: codes,
  };
}

// Scale the Directus -> API compare judge timeout with packet count. A flat
// 60s for a sentence-level compare with a moderately slow model will overrun.
// Base 30s for warmup + first packet, +15s per additional packet, capped at
// 600s so the Directus proxy timeout (CLAREAD_EVAL_PROXY_TIMEOUT_MS, default
// 60s) does not get in the way — callers are expected to raise the proxy
// timeout when packet counts get large.
function resolveWorkflowCompareJudgeTotalTimeoutMs(packetCount) {
  const packets = Math.max(0, Number(packetCount) || 0);
  const totalSeconds = 30 + 15 * packets;
  const capped = Math.min(Math.max(totalSeconds, 60), 600);
  return Math.round(capped * 1000);
}

async function loadWorkflowDatasetCases(env, datasetId) {
  const dir = path.join(datasetsDir(resolveEvalsRoot(env)), datasetId, "cases");
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw workflowRequestError(
        `Dataset "${datasetId}" cases directory was not found.`,
        422,
        "VALIDATION_ERROR",
        "dataset_id",
      );
    }
    throw error;
  }

  const cases = [];
  for (const entry of entries.filter((item) => item.isFile() && item.name.endsWith(".json")).sort((a, b) => a.name.localeCompare(b.name))) {
    const casePayload = await readJsonFile(path.join(dir, entry.name));
    cases.push(casePayload);
  }
  return cases;
}

function workflowCaseRequestBody(casePayload, config) {
  return {
    text: String(casePayload.text || ""),
    reading_goal: String(casePayload.reading_goal || "daily_reading"),
    reading_variant: String(casePayload.reading_variant || "intermediate_reading"),
    source_type: String(casePayload.source_type || "user_input"),
    extended: Boolean(casePayload.extended),
    model_selection: normalizeJsonObject(config.model_selection),
    rag_mode: config.rag_mode || "off",
    prompt_variant_id: config.prompt_variant_id || null,
    prompt_override: config.prompt_override || null,
    trace_scope: config.trace_scope || "off",
    timeout_seconds: config.timeout_seconds || 120,
    source_metadata: {
      dataset_id: config.dataset_id,
      case_id: casePayload.id,
      run_id: config.run_id,
      dispatch_mode: "directus_async",
      eval_purpose: config.eval_purpose || "dataset_regression",
    },
  };
}

async function executeWorkflowEvalCase({ env, config, casePayload }) {
  try {
    const result = await callEvalUpstreamJson({
      env,
      path: "/eval/article-analysis/workflow",
      body: workflowCaseRequestBody(casePayload, config),
      timeoutMs: resolveRequestTimeoutMs(env, config),
    });
    return { result, error: null };
  } catch (error) {
    return { result: null, error };
  }
}

function buildWorkflowCaseArtifact(casePayload, config, evalResult, transportError = null) {
  const renderScene = evalResult?.render_scene && typeof evalResult.render_scene === "object"
    ? evalResult.render_scene
    : {};
  const runtimeSummary = evalResult?.runtime_summary && typeof evalResult.runtime_summary === "object"
    ? evalResult.runtime_summary
    : {};
  const aggregate = runtimeSummary?.aggregate && typeof runtimeSummary.aggregate === "object"
    ? runtimeSummary.aggregate
    : {};
  const error = transportError
    ? workflowRunRequestErrorJson(transportError)
    : (evalResult?.error && typeof evalResult.error === "object" ? evalResult.error : null);
  const adapterStatus = transportError
    ? "failed"
    : (["succeeded", "failed", "timeout"].includes(evalResult?.status) ? evalResult.status : "failed");
  return {
    case_id: casePayload.id,
    run_id: config.run_id,
    adapter_status: adapterStatus,
    input_snapshot: casePayload,
    run_config_snapshot: config,
    workflow_identity: evalResult?.workflow_identity || {},
    schema_identity: evalResult?.schema_identity || {},
    prompt_identity: {
      ...(evalResult?.prompt_identity || {}),
      prompt_variant_id: evalResult?.prompt_identity?.prompt_variant_id || config.prompt_variant_id || null,
    },
    output: renderScene,
    user_facing_state: renderScene?.user_facing_state || null,
    translations: Array.isArray(renderScene?.translations) ? renderScene.translations : [],
    inline_marks: Array.isArray(renderScene?.inline_marks) ? renderScene.inline_marks : [],
    sentence_entries: Array.isArray(renderScene?.sentence_entries) ? renderScene.sentence_entries : [],
    warnings: Array.isArray(evalResult?.warnings)
      ? evalResult.warnings
      : (Array.isArray(renderScene?.warnings) ? renderScene.warnings : []),
    drop_log: Array.isArray(evalResult?.drop_log) ? evalResult.drop_log : [],
    canonical_drop_log: Array.isArray(evalResult?.canonical_drop_log) ? evalResult.canonical_drop_log : [],
    annotation_stats: evalResult?.annotation_stats || null,
    preprocess_summary: evalResult?.preprocess_summary || null,
    normalize_summary: evalResult?.normalize_summary || null,
    drop_log_summary: evalResult?.drop_log_summary || null,
    runtime_summary: runtimeSummary,
    rag_debug: evalResult?.rag_debug || null,
    trace_refs: evalResult?.trace_refs || null,
    usage_summary: {
      total_tokens: aggregate.total_tokens ?? 0,
      input_tokens: aggregate.input_tokens ?? null,
      output_tokens: aggregate.output_tokens ?? null,
      per_agent: runtimeSummary?.per_agent && typeof runtimeSummary.per_agent === "object"
        ? runtimeSummary.per_agent
        : {},
    },
    model_identity: evalResult?.model_identity || {},
    grader_results: [],
    error,
    timeout: adapterStatus === "timeout",
    latency_seconds: Number.isFinite(runtimeSummary?.latency_ms)
      ? Math.round((runtimeSummary.latency_ms / 1000) * 1000) / 1000
      : null,
  };
}

function buildWorkflowGraderResults(casePayload, artifact) {
  const results = [];
  const output = artifact.output && typeof artifact.output === "object" ? artifact.output : {};
  const article = output.article && typeof output.article === "object" ? output.article : {};
  const sentences = Array.isArray(article.sentences) ? article.sentences : [];
  const warningCount = Array.isArray(artifact.warnings) ? artifact.warnings.length : 0;
  const totalDropCount = Number(artifact.drop_log_summary?.total_drop_count || 0);
  const allowedCodes = new Set(Array.isArray(casePayload?.expected?.allowed_warning_codes) ? casePayload.expected.allowed_warning_codes : []);
  const toleratedCodes = new Set(Array.isArray(casePayload?.expected?.tolerated_warning_codes) ? casePayload.expected.tolerated_warning_codes : ["LOW_ENGLISH_RATIO", "TEXT_TYPE_NEEDS_CARE"]);
  const disallowedWarnings = (Array.isArray(artifact.warnings) ? artifact.warnings : []).filter((warning) => !allowedCodes.has(warning?.code) && !toleratedCodes.has(warning?.code));
  const totalSentences = sentences.length || artifact.translations.length;
  const coverage = totalSentences > 0 ? artifact.translations.length / totalSentences : null;
  const minCoverage = Number(casePayload?.expected?.min_translation_coverage ?? 0);
  const maxWarningCount = casePayload?.expected?.max_warning_count;
  const maxDropRatio = casePayload?.expected?.max_drop_ratio;

  if (artifact.error) {
    results.push({
      grader_name: "schema_presence",
      case_id: casePayload.id,
      verdict: "fail",
      severity: "hard",
      metric: "output_present",
      value: false,
      expected: true,
      evidence: `Adapter returned error: ${artifact.error.message || artifact.error.code || "unknown error"}`,
    });
  } else if (!output || Object.keys(output).length === 0) {
    results.push({
      grader_name: "schema_presence",
      case_id: casePayload.id,
      verdict: "fail",
      severity: "hard",
      metric: "output_present",
      value: false,
      expected: true,
      evidence: "Output dict is empty",
    });
  } else {
    const requiredKeys = ["schema_version", "request", "article", "user_facing_state"];
    const missing = requiredKeys.filter((key) => !(key in output));
    if (missing.length > 0) {
      results.push({
        grader_name: "schema_presence",
        case_id: casePayload.id,
        verdict: "fail",
        severity: "hard",
        metric: "required_fields_present",
        value: false,
        expected: true,
        evidence: `Missing required fields: ${missing.join(", ")}`,
      });
    } else {
      results.push({
        grader_name: "schema_presence",
        case_id: casePayload.id,
        verdict: "pass",
        severity: "hard",
        metric: "schema_presence",
        value: true,
        expected: true,
        evidence: "All required fields present",
      });
    }
  }

  if (artifact.error) {
    results.push({
      grader_name: "status_error",
      case_id: casePayload.id,
      verdict: "fail",
      severity: "hard",
      metric: "adapter_error",
      value: artifact.error.message || artifact.error.code || "error",
      expected: null,
      evidence: `Adapter error: ${artifact.error.message || artifact.error.code || "error"}`,
    });
  } else if (artifact.timeout) {
    results.push({
      grader_name: "status_error",
      case_id: casePayload.id,
      verdict: "fail",
      severity: "hard",
      metric: "timeout",
      value: true,
      expected: false,
      evidence: "Case timed out",
    });
  } else if (artifact.user_facing_state === "degraded_heavy") {
    results.push({
      grader_name: "status_error",
      case_id: casePayload.id,
      verdict: "fail",
      severity: "hard",
      metric: "user_facing_state",
      value: artifact.user_facing_state,
      expected: "normal",
      evidence: "Heavy degraded state",
    });
  } else if (artifact.user_facing_state === "degraded_light") {
    results.push({
      grader_name: "status_error",
      case_id: casePayload.id,
      verdict: "fail",
      severity: "soft",
      metric: "user_facing_state",
      value: artifact.user_facing_state,
      expected: "normal",
      evidence: "Light degraded state",
    });
  } else {
    results.push({
      grader_name: "status_error",
      case_id: casePayload.id,
      verdict: "pass",
      severity: "hard",
      metric: "user_facing_state",
      value: artifact.user_facing_state,
      expected: "normal",
      evidence: "Normal state",
    });
  }

  if (artifact.error) {
    results.push({
      grader_name: "translation_coverage",
      case_id: casePayload.id,
      verdict: "skip",
      severity: "info",
      metric: "translation_coverage",
      value: null,
      expected: null,
      evidence: "Skipped due to adapter error",
    });
  } else if (!totalSentences) {
    results.push({
      grader_name: "translation_coverage",
      case_id: casePayload.id,
      verdict: "skip",
      severity: "info",
      metric: "translation_coverage",
      value: null,
      expected: null,
      evidence: "No sentences found to measure coverage",
    });
  } else {
    results.push({
      grader_name: "translation_coverage",
      case_id: casePayload.id,
      verdict: coverage >= minCoverage ? "pass" : "fail",
      severity: "hard",
      metric: "translation_coverage",
      value: Number(coverage.toFixed(4)),
      expected: `>=${minCoverage}`,
      evidence: `${artifact.translations.length}/${totalSentences} sentences translated (${(coverage * 100).toFixed(1)}%), threshold=${(minCoverage * 100).toFixed(1)}%`,
    });
  }

  const issues = [];
  if (disallowedWarnings.length > 0) {
    issues.push(`Disallowed warning codes: ${disallowedWarnings.map((warning) => warning?.code).filter(Boolean).join(", ")}`);
  }
  if (Number.isFinite(maxWarningCount) && warningCount > maxWarningCount) {
    issues.push(`Warning count ${warningCount} exceeds max ${maxWarningCount}`);
  }
  if (Number.isFinite(maxDropRatio) && totalSentences > 0 && totalDropCount / totalSentences > maxDropRatio) {
    issues.push(`Drop ratio ${((totalDropCount / totalSentences) * 100).toFixed(1)}% exceeds max ${(maxDropRatio * 100).toFixed(1)}%`);
  }
  results.push({
    grader_name: "warning_drop_summary",
    case_id: casePayload.id,
    verdict: issues.length > 0 ? "fail" : "pass",
    severity: issues.length > 0 ? "soft" : "info",
    metric: "warning_drop_summary",
    value: { warnings: warningCount, drops: totalDropCount },
    expected: {
      allowed_codes: [...allowedCodes],
      tolerated_codes: [...toleratedCodes],
      max_warning_count: maxWarningCount ?? null,
      max_drop_ratio: maxDropRatio ?? null,
    },
    evidence: issues.length > 0 ? issues.join("; ") : `Warnings: ${warningCount}, Drops: ${totalDropCount}`,
  });

  return results;
}

function summarizeWorkflowCaseForIndex(artifact) {
  const graderResults = Array.isArray(artifact.grader_results) ? artifact.grader_results : [];
  const failedGraders = graderResults.filter((result) => result?.verdict === "fail");
  const hardFailures = failedGraders.filter((result) => result?.severity === "hard").length;
  const softFailures = failedGraders.filter((result) => result?.severity === "soft").length;
  const dropCount = Number(artifact.drop_log_summary?.total_drop_count || (Array.isArray(artifact.drop_log) ? artifact.drop_log.length : 0));
  return {
    case_id: artifact.case_id,
    run_id: artifact.run_id,
    artifact_href: `cases/${artifact.case_id}.json`,
    adapter_status: artifact.adapter_status,
    user_facing_state: artifact.user_facing_state ?? null,
    error: artifact.error ? { code: artifact.error.code || null, message: artifact.error.message || null } : null,
    warning_count: Array.isArray(artifact.warnings) ? artifact.warnings.length : 0,
    drop_count: dropCount,
    hard_failures: hardFailures,
    soft_failures: softFailures,
    grader_count: graderResults.length,
    failed_grader_count: failedGraders.length,
    grader_summaries: graderResults.map((result) => ({
      grader_name: result?.grader_name ?? null,
      verdict: result?.verdict ?? null,
      severity: result?.severity ?? null,
      metric: result?.metric ?? null,
      evidence: result?.evidence ?? null,
    })),
    translation_count: Array.isArray(artifact.translations) ? artifact.translations.length : 0,
    inline_mark_count: Array.isArray(artifact.inline_marks) ? artifact.inline_marks.length : 0,
    sentence_entry_count: Array.isArray(artifact.sentence_entries) ? artifact.sentence_entries.length : 0,
    latency_seconds: artifact.latency_seconds ?? null,
    total_tokens: artifact.usage_summary?.total_tokens ?? null,
    input_tokens: artifact.usage_summary?.input_tokens ?? null,
    output_tokens: artifact.usage_summary?.output_tokens ?? null,
    workflow_identity: artifact.workflow_identity ?? null,
    schema_identity: artifact.schema_identity ?? null,
    prompt_identity: artifact.prompt_identity ?? null,
    model_identity: artifact.model_identity ?? null,
  };
}

function buildWorkflowRunReport(config, artifacts) {
  const caseSummaries = artifacts.map((artifact) => {
    const graderResults = Array.isArray(artifact.grader_results) ? artifact.grader_results : [];
    const hardFailures = graderResults.filter((result) => result?.verdict === "fail" && result?.severity === "hard").length;
    const softFailures = graderResults.filter((result) => result?.verdict === "fail" && result?.severity === "soft").length;
    const verdict = artifact.error
      ? "error"
      : hardFailures > 0
        ? "fail"
        : "pass";
    return {
      case_id: artifact.case_id,
      verdict,
      hard_failures: hardFailures,
      soft_failures: softFailures,
      error: artifact.error?.message || null,
    };
  });

  return {
    run_id: config.run_id,
    dataset_id: config.dataset_id,
    created_at: new Date().toISOString(),
    total_cases: caseSummaries.length,
    passed: caseSummaries.filter((item) => item.verdict === "pass").length,
    failed: caseSummaries.filter((item) => item.verdict === "fail").length,
    skipped: caseSummaries.filter((item) => item.verdict === "skip").length,
    errored: caseSummaries.filter((item) => item.verdict === "error").length,
    hard_failure_case_ids: caseSummaries.filter((item) => item.hard_failures > 0).map((item) => item.case_id),
    soft_failure_case_ids: caseSummaries.filter((item) => item.soft_failures > 0 && item.hard_failures === 0).map((item) => item.case_id),
    case_summaries: caseSummaries,
    runtime_aggregates: {
      total_warnings: artifacts.reduce((sum, artifact) => sum + (Array.isArray(artifact.warnings) ? artifact.warnings.length : 0), 0),
      total_drops: artifacts.reduce((sum, artifact) => sum + Number(artifact.drop_log_summary?.total_drop_count || 0), 0),
    },
    regression_list: [],
  };
}

function renderWorkflowRunReportMarkdown(report) {
  const lines = [
    `# Eval Report: ${report.run_id}`,
    "",
    `- Dataset: \`${report.dataset_id}\``,
    `- Created: ${report.created_at}`,
    `- Total cases: ${report.total_cases}`,
    `- Passed: ${report.passed}`,
    `- Failed: ${report.failed}`,
    `- Skipped: ${report.skipped}`,
    `- Errored: ${report.errored}`,
    "",
    "## Case Summaries",
    "",
    "| Case ID | Verdict | Hard | Soft | Error |",
    "|---------|---------|------|------|-------|",
  ];
  for (const summary of report.case_summaries || []) {
    lines.push(`| \`${summary.case_id}\` | ${summary.verdict} | ${summary.hard_failures} | ${summary.soft_failures} | ${summary.error || ""} |`);
  }
  lines.push("");
  return lines.join("\n");
}

async function writeWorkflowRunArtifacts({ env, config, artifacts, report }) {
  const runsRoot = resolveWorkflowRuntimeRunsRoot(env);
  const dir = runDir(runsRoot, config.run_id);
  if (await fileExists(dir)) {
    throw workflowRequestError(
      `Run directory "${config.run_id}" already exists in runtime artifacts.`,
      409,
      "WORKFLOW_RUN_REQUEST_ARTIFACT_CONFLICT",
      "run_id",
    );
  }

  await mkdir(path.join(dir, "cases"), { recursive: true });
  await writeJsonFile(path.join(dir, "run.json"), config);
  for (const artifact of artifacts) {
    await writeJsonFile(path.join(dir, "cases", `${artifact.case_id}.json`), artifact);
  }
  await writeJsonFile(path.join(dir, "case-index.json"), {
    schema_version: "eval-case-index-v1",
    run_id: config.run_id,
    dataset_id: config.dataset_id,
    generated_at: new Date().toISOString(),
    total_cases: artifacts.length,
    cases: artifacts.map((artifact) => summarizeWorkflowCaseForIndex(artifact)),
  });
  await writeJsonFile(path.join(dir, "report.json"), report);
  await writeFile(path.join(dir, "report.md"), renderWorkflowRunReportMarkdown(report), "utf8");
  return dir;
}

async function runWorkflowRequestDirectusAsync({
  database,
  env,
  req,
  requestId,
  bridgeId,
}) {
  const current = await database("eval_workflow_run_requests")
    .where({ id: requestId })
    .first();
  if (!current) return;

  const config = {
    ...normalizeConfigJson(current.config_json),
    run_id: current.run_id,
    dataset_id: current.dataset_id,
    eval_purpose: current.eval_purpose || "dataset_regression",
    adapter_kind: current.adapter_kind || "http",
    prompt_variant_id: current.prompt_variant_id || null,
    prompt_variant_snapshot_hash: current.prompt_variant_snapshot_hash || null,
  };
  const cases = await loadWorkflowDatasetCases(env, current.dataset_id);
  const artifacts = [];
  for (const casePayload of cases) {
    const { result, error } = await executeWorkflowEvalCase({
      env,
      config,
      casePayload,
    });
    const artifact = buildWorkflowCaseArtifact(casePayload, config, result, error);
    artifact.grader_results = buildWorkflowGraderResults(casePayload, artifact);
    artifacts.push(artifact);
  }

  const report = buildWorkflowRunReport(config, artifacts);
  const runDirPath = await writeWorkflowRunArtifacts({
    env,
    config,
    artifacts,
    report,
  });

  await database("eval_workflow_run_requests")
    .where({ id: requestId, lease_owner: bridgeId })
    .whereIn("status", ["running"])
    .update({
      status: "succeeded",
      artifact_run_id: config.run_id,
      artifact_path: `runtime-evals/workflow-runs/${config.run_id}`,
      finished_at: new Date().toISOString(),
      heartbeat_at: new Date().toISOString(),
      date_updated: new Date().toISOString(),
      error_json: null,
      user_updated: req.accountability?.user || null,
      lease_until: null,
    });

  return runDirPath;
}

async function dispatchWorkflowRunDirectusAsync({
  database,
  env,
  req,
  requestId,
}) {
  const bridgeId = `directus-workflow-run-${requestId}-${randomUUID()}`;
  const updatedCount = await database("eval_workflow_run_requests")
    .where({ id: requestId, status: "queued" })
    .update({
      status: "running",
      runner_kind: "directus_async",
      lease_owner: bridgeId,
      lease_until: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
      heartbeat_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      finished_at: null,
      error_json: null,
      date_updated: new Date().toISOString(),
      user_updated: req.accountability?.user || null,
    });
  if (!updatedCount) {
    throw workflowRequestError(
      "Workflow run request changed before it could be dispatched.",
      409,
      "WORKFLOW_RUN_REQUEST_CHANGED",
    );
  }

  setImmediate(() => {
    runWorkflowRequestDirectusAsync({
      database,
      env,
      req,
      requestId,
      bridgeId,
    }).catch(async (error) => {
      await database("eval_workflow_run_requests")
        .where({ id: requestId })
        .whereIn("status", ["queued", "running"])
        .update({
          status: "failed",
          runner_kind: "directus_async",
          finished_at: new Date().toISOString(),
          heartbeat_at: new Date().toISOString(),
          date_updated: new Date().toISOString(),
          user_updated: req.accountability?.user || null,
          error_json: workflowRunRequestErrorJson(error),
          lease_until: null,
        })
        .catch(() => {});
    });
  });

  return database("eval_workflow_run_requests").where({ id: requestId }).first();
}

function buildRetryWorkflowRequestConfig(row, runId) {
  const originalConfig = normalizeConfigJson(row?.config_json);
  const baseConfig = {
    ...originalConfig,
    run_id: runId,
    dataset_id: originalConfig.dataset_id || row.dataset_id,
    mode: "workflow",
    eval_purpose: originalConfig.eval_purpose || row.eval_purpose || "dataset_regression",
    adapter_kind: originalConfig.adapter_kind || row.adapter_kind || "fake",
    prompt_variant_id: originalConfig.prompt_variant_id || row.prompt_variant_id || null,
    prompt_variant_snapshot_hash:
      originalConfig.prompt_variant_snapshot_hash || row.prompt_variant_snapshot_hash || null,
    rag_mode: originalConfig.rag_mode || "off",
    trace_scope: originalConfig.trace_scope || "off",
    timeout_seconds: originalConfig.timeout_seconds || 120,
    execution_mode: originalConfig.execution_mode || "runner_bridge",
    runner_kind: originalConfig.runner_kind || "external_worker",
  };

  return {
    ...baseConfig,
    yaml_content: typeof originalConfig.yaml_content === "string"
      ? patchYamlRunId(originalConfig.yaml_content, runId)
      : buildYamlContent(baseConfig),
  };
}

async function ensureWorkflowRetryRunIdAvailable(database, env, runId) {
  if (!isSafeFileId(runId)) {
    throw workflowRequestError(
      "run_id contains unsafe characters.",
      422,
      "VALIDATION_ERROR",
      "run_id",
    );
  }

  const existingRequest = await database("eval_workflow_run_requests")
    .select(["id"])
    .where({ run_id: runId })
    .first();
  if (existingRequest) {
    throw workflowRequestError(
      `Workflow run request "${runId}" already exists. Choose a different run_id or let the system generate one.`,
      409,
      "WORKFLOW_RUN_REQUEST_RUN_ID_CONFLICT",
      "run_id",
    );
  }

  const conflictingRoot = await findExistingRunRoot(resolveWorkflowRunRoots(env), runId);
  if (conflictingRoot) {
    throw workflowRequestError(
      `Run directory "${runId}" already exists. Retry must use a new run_id.`,
      409,
      "WORKFLOW_RUN_REQUEST_ARTIFACT_CONFLICT",
      "run_id",
    );
  }
}

async function cancelWorkflowRunRequest(database, req, requestId) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_RUNNER_BRIDGE_UNAVAILABLE";
    throw error;
  }

  const current = await database("eval_workflow_run_requests")
    .select(["id", "status"])
    .where({ id: requestId })
    .first();

  if (!current) {
    const error = new Error("Workflow run request not found.");
    error.status = 404;
    error.code = "WORKFLOW_RUN_REQUEST_NOT_FOUND";
    throw error;
  }
  if (!isWorkflowRunRequestCancelable(current.status)) {
    const error = new Error("Only queued or running workflow run requests can be cancelled in v1.");
    error.status = 409;
    error.code = "WORKFLOW_RUN_REQUEST_NOT_CANCELABLE";
    throw error;
  }

  const updatedCount = await database("eval_workflow_run_requests")
    .where({ id: requestId })
    .whereIn("status", ["queued", "running"])
    .update({
      status: "cancelled",
      finished_at: database.fn.now(),
      date_updated: database.fn.now(),
      user_updated: req.accountability?.user || null,
      error_json: null,
    });

  if (!updatedCount) {
    const error = new Error("Workflow run request changed before it could be cancelled.");
    error.status = 409;
    error.code = "WORKFLOW_RUN_REQUEST_NOT_CANCELABLE";
    throw error;
  }

  return database("eval_workflow_run_requests").where({ id: requestId }).first();
}

async function retryWorkflowRunRequest(database, req, env, requestId, body = {}) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_RUNNER_BRIDGE_UNAVAILABLE";
    throw error;
  }

  const current = await database("eval_workflow_run_requests")
    .select([
      "id",
      "run_id",
      "status",
      "dataset_id",
      "eval_purpose",
      "adapter_kind",
      "config_json",
      "prompt_variant_id",
      "prompt_variant_snapshot_hash",
      "attempt_no",
      "max_attempts",
    ])
    .where({ id: requestId })
    .first();

  if (!current) {
    throw workflowRequestError(
      "Workflow run request not found.",
      404,
      "WORKFLOW_RUN_REQUEST_NOT_FOUND",
    );
  }
  if (!isWorkflowRunRequestRetryable(current.status)) {
    throw workflowRequestError(
      "Only failed or cancelled workflow run requests can be retried in v1.",
      409,
      "WORKFLOW_RUN_REQUEST_NOT_RETRYABLE",
    );
  }

  let runId = String(body?.run_id || "").trim();
  if (runId) {
    await ensureWorkflowRetryRunIdAvailable(database, env, runId);
  } else {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const candidateRunId = buildRetryRunId(current.run_id);
      try {
        await ensureWorkflowRetryRunIdAvailable(database, env, candidateRunId);
        runId = candidateRunId;
        break;
      } catch (error) {
        if (error?.status !== 409) throw error;
      }
    }
    if (!runId) {
      throw workflowRequestError(
        "Could not generate an available retry run_id.",
        409,
        "WORKFLOW_RUN_REQUEST_RUN_ID_CONFLICT",
        "run_id",
      );
    }
  }

  const previousAttemptNo = Number.parseInt(String(current.attempt_no || 1), 10);
  const attemptNo = (Number.isFinite(previousAttemptNo) && previousAttemptNo > 0
    ? previousAttemptNo
    : 1) + 1;
  const previousMaxAttempts = Number.parseInt(String(current.max_attempts || 1), 10);
  const maxAttempts = Number.isFinite(previousMaxAttempts)
    ? Math.max(previousMaxAttempts, attemptNo)
    : attemptNo;
  const retryReason = String(body?.retry_reason || body?.reason || "").trim().slice(0, 1000) || null;
  const retryConfig = buildRetryWorkflowRequestConfig(current, runId);
  const row = workflowRequestRow(req, retryConfig, {
    source_request_id: current.id,
    attempt_no: attemptNo,
    max_attempts: maxAttempts,
    retry_reason: retryReason,
  });

  await database("eval_workflow_run_requests").insert(row);
  const inserted = await database("eval_workflow_run_requests").where({ run_id: runId }).first();
  if (retryConfig.execution_mode === "directus_async") {
    return dispatchWorkflowRunDirectusAsync({
      database,
      env,
      req,
      requestId: inserted.id,
    });
  }
  return inserted;
}

export {
  attachPromptVariantSnapshot,
  appendWorkflowDatasetCase,
  buildAuthGuard,
  buildRetryJudgeRunId,
  buildWorkflowCompareLlmJudgeCaseResults,
  buildWorkflowCompareJudgeRequestErrorJson,
  summarizeCompareJudgeSentenceOutput,
  classifyWorkflowCompareJudgeRequestStatus,
  normalizeWarningsToStringList,
  resolveWorkflowCompareJudgeTotalTimeoutMs,
  buildRetryRunId,
  buildRetryWorkflowRequestConfig,
  createWorkflowDataset,
  buildWorkflowLabCompareReport,
  buildWorkflowLabExperimentFingerprint,
  newWorkflowCompareId,
  buildSingleRunCaseArtifact,
  workflowSingleRunHistoryRunId,
  deriveWorkflowCompareStatus,
  workflowCompareIdentityDegenerateReason,
  createOrReuseWorkflowCompare,
  createWorkflowCompareJudgeRequest,
  deleteWorkflowCompareCascade,
  loadWorkflowCompareCaseEvidence,
  loadWorkflowCompareHistoryDetail,
  listWorkflowCompareHistoryRecords,
  listWorkflowCompareJudgeRequests,
  syntheticSingleRunCompareRunId,
  cancelJudgeRunRequest,
  cancelWorkflowCompareJudgeRequest,
  createWorkflowLabSingleRun,
  createWorkflowLabSingleRunCompare,
  saveWorkflowLabSingleRunToHistory,
  createWorkflowLabCompare,
  createJudgeRunRequest,
  inferRunTopologyMode,
  isSafeFileId,
  isJudgeRunRequestCancelable,
  isJudgeRunRequestRetryable,
  isWorkflowRunRequestCancelable,
  isWorkflowRunRequestRetryable,
  judgeRequestRow,
  judgeRunRequestSummary,
  workflowCompareIdForRunPair,
  workflowCompareJudgeRequestRow,
  workflowCompareJudgeRequestSummary,
  listJudgeRunRequests,
  listJudgeRubrics,
  listReadyPromptVariantSnapshots,
  loadWorkflowCompareJudgeArtifact,
  patchYamlRunId,
  promptVariantSnapshotFromRow,
  readWorkflowDatasetSummary,
  retryJudgeRunRequest,
  retryWorkflowCompareJudgeRequest,
  retryWorkflowRunRequest,
  listWorkflowDatasets,
  validateWorkflowRunRequest,
  workflowConfigWithPromptVariantSnapshot,
  workflowRequestRow,
  workflowRunRequestSummary,
};

async function sendWorkflowRequest(req, res, env, database) {
  const evalsRoot = resolveEvalsRoot(env);
  const config = req.body || {};
  const executionMode = config.execution_mode || "manual";
  if (!VALID_EXECUTION_MODES.includes(executionMode)) {
    res.status(422).json({
      errors: [{
        message: `execution_mode must be one of: ${VALID_EXECUTION_MODES.join(", ")}.`,
        extensions: { code: "VALIDATION_ERROR", field: "execution_mode" },
      }],
    });
    return;
  }

  if (config.preset_id) {
    const preset = CONFIG_PRESETS.find((p) => p.id === config.preset_id);
    if (!preset) {
      res.status(422).json({
        errors: [{
          message: `Unknown preset "${config.preset_id}". Available: ${CONFIG_PRESETS.map((p) => p.id).join(", ")}`,
          extensions: { code: "VALIDATION_ERROR", field: "preset_id" },
        }],
      });
      return;
    }

    if (config.run_id && !isSafeFileId(config.run_id)) {
      res.status(422).json({
        errors: [{
          message: "run_id contains unsafe characters.",
          extensions: { code: "VALIDATION_ERROR", field: "run_id" },
        }],
      });
      return;
    }

    const yamlPath = path.join(runConfigsDir(evalsRoot), preset.file);
    let yamlContent;
    try {
      yamlContent = await readFile(yamlPath, "utf-8");
    } catch {
      res.status(422).json({
        errors: [{
          message: `Preset config file "${preset.file}" not found at ${yamlPath}.`,
          extensions: { code: "VALIDATION_ERROR", field: "preset_id" },
        }],
      });
      return;
    }

    const runId = config.run_id || generateRunId("fake");
    const runDirExists = await findExistingRunRoot(resolveWorkflowRunRoots(env), runId);
    if (runDirExists) {
      res.status(409).json({
        errors: [{
          message: `Run directory "${runId}" already exists. Choose a different run_id or let the system generate one.`,
          extensions: { code: "CONFLICT", field: "run_id" },
        }],
      });
      return;
    }

    const patchedYaml = patchYamlRunId(yamlContent, runId);

    const configFileName = `ui-${runId}.yaml`;
    const cliCommand = `cd evals && uv run python -m claread_eval.runner.entrypoint --config run-configs/${configFileName}`;
    const requestConfig = {
      run_id: runId,
      dataset_id: simpleYamlValue(patchedYaml, "dataset_id"),
      mode: "workflow",
      eval_purpose: simpleYamlValue(patchedYaml, "eval_purpose", "dataset_regression"),
      adapter_kind: simpleYamlValue(patchedYaml, "adapter_kind", "fake"),
      prompt_variant_id: simpleYamlValue(patchedYaml, "prompt_variant_id"),
      rag_mode: simpleYamlValue(patchedYaml, "rag_mode", "off"),
      trace_scope: simpleYamlValue(patchedYaml, "trace_scope", "off"),
      timeout_seconds: Number(simpleYamlValue(patchedYaml, "timeout_seconds", 120)) || 120,
      preset_id: config.preset_id,
      yaml_content: patchedYaml,
    };
    let requestRow = null;
    if (executionMode === "runner_bridge" || executionMode === "directus_async") {
      requestRow = await createWorkflowRunRequest(database, req, {
        ...requestConfig,
        execution_mode: executionMode,
        runner_kind: executionMode === "directus_async" ? "directus_async" : "external_worker",
      });
      if (executionMode === "directus_async") {
        requestRow = await dispatchWorkflowRunDirectusAsync({
          database,
          env,
          req,
          requestId: requestRow.id,
        });
      }
    }

    res.status(201).json({
      data: {
        status: requestRow
          ? executionMode === "directus_async"
            ? "running_directus_async"
            : "queued_for_runner_bridge"
          : "pending_manual_execution",
        run_id: runId,
        preset_id: config.preset_id,
        config: null,
        execution_mode: executionMode,
        prompt_variant_id: requestConfig.prompt_variant_id,
        prompt_variant_snapshot_hash: requestConfig.prompt_variant_snapshot_hash || null,
        runner_bridge_request: requestRow
          ? { run_id: requestRow.run_id, status: requestRow.status }
          : null,
        yaml_content: patchedYaml,
        recommended_cli_command: cliCommand,
        message: executionMode === "directus_async"
          ? "Workflow run has been dispatched inside Directus. Artifacts will be written to runtime eval storage."
          : requestRow
            ? "Runner bridge request queued. An external eval worker must execute it and write evals/runs artifacts."
          : "Config generated. Save the YAML content to the path below, then run the CLI command. This will NOT auto-execute.",
      },
    });
    return;
  }

  const validationErrors = validateWorkflowRunRequest(config);
  if (validationErrors.length > 0) {
    res.status(422).json({
      errors: validationErrors.map((e) => ({
        message: e.message,
        extensions: { code: "VALIDATION_ERROR", field: e.field },
      })),
    });
    return;
  }

  const runId = config.run_id || generateRunId(config.adapter_kind === "fake" ? "fake" : "eval");

  const dsDir = path.join(datasetsDir(evalsRoot), config.dataset_id);
  const datasetYaml = path.join(dsDir, "dataset.yaml");
  const datasetExists = await fileExists(datasetYaml);
  if (!datasetExists) {
    res.status(422).json({
      errors: [{
        message: `Dataset "${config.dataset_id}" not found at ${dsDir}.`,
        extensions: { code: "VALIDATION_ERROR", field: "dataset_id" },
      }],
    });
    return;
  }

  const conflictingRoot = await findExistingRunRoot(resolveWorkflowRunRoots(env), runId);
  if (conflictingRoot) {
    res.status(409).json({
      errors: [{
        message: `Run directory "${runId}" already exists. Choose a different run_id or let the system generate one.`,
        extensions: { code: "CONFLICT", field: "run_id" },
      }],
    });
    return;
  }

  let fullConfig = {
    run_id: runId,
    dataset_id: config.dataset_id,
    mode: "workflow",
    eval_purpose: config.eval_purpose || "dataset_regression",
    adapter_kind: config.adapter_kind || "fake",
    prompt_variant_id: config.prompt_variant_id || null,
    rag_mode: config.rag_mode || "off",
    trace_scope: config.trace_scope || "off",
    model_selection: normalizeJsonObject(config.model_selection),
    timeout_seconds: config.timeout_seconds || 120,
  };
  fullConfig = await attachPromptVariantSnapshot(database, fullConfig);

  const yamlContent = buildYamlContent(fullConfig);
  const requestConfig = {
    ...fullConfig,
    execution_mode: executionMode,
    runner_kind: executionMode === "directus_async" ? "directus_async" : "external_worker",
    yaml_content: yamlContent,
  };
  let requestRow = null;
  if (executionMode === "runner_bridge" || executionMode === "directus_async") {
    requestRow = await createWorkflowRunRequest(database, req, requestConfig);
    if (executionMode === "directus_async") {
      requestRow = await dispatchWorkflowRunDirectusAsync({
        database,
        env,
        req,
        requestId: requestRow.id,
      });
    }
  }

  res.status(201).json({
    data: {
      status: requestRow
        ? executionMode === "directus_async"
          ? "running_directus_async"
          : "queued_for_runner_bridge"
        : "pending_manual_execution",
      run_id: runId,
      preset_id: null,
      execution_mode: executionMode,
      prompt_variant_id: requestConfig.prompt_variant_id,
      prompt_variant_snapshot_hash: requestConfig.prompt_variant_snapshot_hash || null,
      runner_bridge_request: requestRow
        ? { run_id: requestRow.run_id, status: requestRow.status }
        : null,
      config: fullConfig,
      yaml_content: yamlContent,
      message: executionMode === "directus_async"
        ? "Workflow run has been dispatched inside Directus. Artifacts will be written to runtime eval storage."
        : executionMode === "runner_bridge"
          ? "Runner bridge request queued. An external eval worker must execute it and write evals/runs artifacts."
          : "Config generated. Save the YAML content to the path below, then run the CLI command. This will NOT auto-execute.",
    },
  });
}

export default (router, context) => {
  const env = context?.env;
  const database = context?.database;

  registerNodeLabRoutes(router, context, {
    buildAuthGuard,
    clampLimit,
    isSafeFileId,
    joinUrl,
    parseUpstreamError,
    readEnv,
    resolveEvalsRoot,
    resolveNodeLabArtifactsRoot,
    resolveRequestTimeoutMs,
  });

  registerExampleLabRoutes(router, context, {
    buildAuthGuard,
    readEnv,
  });

  router.get("/article-analysis/model-profiles", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    const baseUrl = readEnv(env, "CLAREAD_API_BASE_URL");
    const adminKey =
      readEnv(env, "CLAREAD_API_ADMIN_KEY") ||
      readEnv(env, "DAILY_READER_ADMIN_API_KEY");

    if (!baseUrl || !adminKey) {
      res.status(503).json({
        errors: [
          {
            message: "Eval proxy is not configured.",
            extensions: { code: "SERVICE_UNAVAILABLE" },
          },
        ],
      });
      return;
    }

    try {
      const upstream = await fetch(
        joinUrl(baseUrl, "/eval/article-analysis/model-profiles"),
        {
          method: "GET",
          headers: {
            Accept: "application/json",
            "x-admin-api-key": adminKey,
          },
        },
      );

      if (!upstream.ok) {
        const errorPayload = await parseUpstreamError(upstream);
        res.status(upstream.status).json({
          errors: [
            {
              message: errorPayload?.detail || errorPayload?.message || "Upstream request failed.",
              extensions: {
                code: "UPSTREAM_EVAL_ERROR",
                upstream_status: upstream.status,
              },
            },
          ],
        });
        return;
      }

      const payload = await upstream.json();
      res.json({ data: payload });
    } catch (error) {
      next(error);
    }
  });

  router.get("/runs", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const roots = resolveWorkflowRunRoots(env);
      const runs = await listRuns(roots, clampLimit(req.query?.limit));
      res.json({
        data: {
          runs_root_configured: roots.length > 0,
          runs,
        },
      });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/runs/:runId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const roots = resolveWorkflowRunRoots(env);
      res.json({ data: await loadRunDetail(roots, req.params.runId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/runs/:runId/cases/:caseId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const root = await resolveRunRootOrThrow(resolveWorkflowRunRoots(env), req.params.runId);
      const artifact = await readJsonFile(
        caseArtifactPath(root, req.params.runId, req.params.caseId),
      );
      res.json({ data: artifact });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/runs/:runId/judge", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const root = await resolveRunRootOrThrow(resolveWorkflowRunRoots(env), req.params.runId);
      res.json({ data: await listJudgeArtifacts(root, req.params.runId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/runs/:runId/judge/:judgeRunId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const roots = resolveWorkflowRunRoots(env);
      res.json({ data: await loadJudgeArtifact(roots, req.params.runId, req.params.judgeRunId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/workflow-lab/run-history", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const records = await listWorkflowCompareHistoryRecords(database, env, clampLimit(req.query?.limit));
      res.json({ data: { records } });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/workflow-lab/run-history/:compareId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await loadWorkflowCompareHistoryDetail(database, env, req.params.compareId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.delete("/workflow-lab/run-history/:compareId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await deleteWorkflowCompareCascade(database, env, req.params.compareId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/workflow-lab/compares", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const records = await listWorkflowCompareHistoryRecords(database, env, clampLimit(req.query?.limit));
      res.json({ data: { records } });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/workflow-lab/compares/:compareId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await loadWorkflowCompareHistoryDetail(database, env, req.params.compareId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/workflow-lab/compares/:compareId/cases/:caseId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await loadWorkflowCompareCaseEvidence(database, env, req.params.compareId, req.params.caseId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.delete("/workflow-lab/compares/:compareId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await deleteWorkflowCompareCascade(database, env, req.params.compareId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.patch("/workflow-lab/run-history/:compareId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const compareId = req.params?.compareId;
      if (!isSafeFileId(compareId)) {
        return res.status(400).json({ errors: [{ message: "Invalid compare id.", code: "INVALID_COMPARE_ID" }] });
      }
      const customTitle = req.body?.custom_title;
      if (customTitle !== undefined && customTitle !== null && typeof customTitle !== "string") {
        return res.status(422).json({ errors: [{ message: "custom_title must be a string or null.", code: "VALIDATION_ERROR" }] });
      }
      const trimmed = typeof customTitle === "string" ? customTitle.trim() : null;
      if (trimmed !== null && trimmed.length > 200) {
        return res.status(422).json({ errors: [{ message: "custom_title must be 200 characters or fewer.", code: "VALIDATION_ERROR" }] });
      }
      const updated = await database("eval_workflow_compares")
        .where({ compare_id: compareId })
        .update({ custom_title: trimmed || null, date_updated: new Date() })
        .returning(["compare_id", "custom_title"]);
      if (!updated || !updated.length) {
        return res.status(404).json({ errors: [{ message: "Workflow compare record not found.", code: "WORKFLOW_COMPARE_NOT_FOUND" }] });
      }
      res.json({ data: updated[0] });
    } catch (error) {
      next(error);
    }
  });

  router.post("/workflow-lab/compare", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const result = await createWorkflowLabCompare(database, env, req.body || {});
      res.status(result.created ? 201 : 200).json({ data: result });
    } catch (error) {
      if (error?.name === "AbortError") {
        res.status(504).json({
          errors: [
            {
              message: "Workflow Lab single run timed out.",
              extensions: { code: "UPSTREAM_TIMEOUT" },
            },
          ],
        });
        return;
      }
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_LAB_COMPARE_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-lab/baseline-bundle", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const payload = await callEvalUpstreamJson({
        env,
        path: "/eval/article-analysis/workflow-lab/baseline-bundle",
        body: req.body || {},
        timeoutMs: resolveRequestTimeoutMs(env, req.body),
      });
      res.json({ data: payload });
    } catch (error) {
      if (error?.name === "AbortError") {
        res.status(504).json({
          errors: [
            {
              message: "Workflow Lab baseline bundle request timed out.",
              extensions: { code: "UPSTREAM_TIMEOUT" },
            },
          ],
        });
        return;
      }
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_LAB_BASELINE_BUNDLE_ERROR",
                upstream_status: error.upstream_status,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-lab/single-run", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const payload = await createWorkflowLabSingleRun({
        database,
        env,
        body: req.body || {},
      });
      res.json({ data: payload });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_LAB_SINGLE_RUN_ERROR",
                field: error.field,
                upstream_status: error.upstream_status,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  // 双跑单篇验证 compare:同一篇文章并发跑 baseline + candidate,直接产出 compare workspace;
  // 主链入口;自动物化双侧 run artifact 与 compare record
  router.post("/workflow-lab/single-run-compare", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const payload = await createWorkflowLabSingleRunCompare({
        database,
        env,
        body: req.body || {},
      });
      res.json({ data: payload });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_LAB_SINGLE_RUN_COMPARE_ERROR",
                field: error.field,
                upstream_status: error.upstream_status,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/workflow-lab/compares/:compareId/judge-requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const rows = await listWorkflowCompareJudgeRequests(database, req.params.compareId, req.query || {});
      res.json({ data: rows.map((row) => workflowCompareJudgeRequestSummary(row)) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_COMPARE_JUDGE_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-lab/compares/:compareId/judge-requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await createWorkflowCompareJudgeRequest(database, req, env, req.params.compareId, req.body || {});
      res.status(201).json({ data: workflowCompareJudgeRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        const errors = Array.isArray(error.validationErrors)
          ? error.validationErrors.map((item) => ({
              message: item.message,
              extensions: { code: error.code || "VALIDATION_ERROR", field: item.field },
            }))
          : [
              {
                message: error.message,
                extensions: {
                  code: error.code || "WORKFLOW_COMPARE_JUDGE_REQUEST_ERROR",
                  field: error.field,
                },
              },
            ];
        res.status(error.status).json({ errors });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-lab/compares/:compareId/judge-requests/:requestId/cancel", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await cancelWorkflowCompareJudgeRequest(database, req, req.params.compareId, req.params.requestId);
      res.json({ data: workflowCompareJudgeRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_COMPARE_JUDGE_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-lab/compares/:compareId/judge-requests/:requestId/retry", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await retryWorkflowCompareJudgeRequest(database, req, env, req.params.compareId, req.params.requestId, req.body || {});
      res.status(201).json({ data: workflowCompareJudgeRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_COMPARE_JUDGE_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/workflow-lab/compares/:compareId/judge/:judgeRunId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await loadWorkflowCompareJudgeArtifact(env, req.params.compareId, req.params.judgeRunId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.post("/workflow-lab/run-history/single-run", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const result = await saveWorkflowLabSingleRunToHistory({
        env,
        body: req.body || {},
      });
      res.status(result.duplicate ? 200 : 201).json({ data: result });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_LAB_SINGLE_RUN_HISTORY_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.patch("/workflow-lab/run-history/single-run/:runId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const runId = req.params?.runId;
      if (!isSafeFileId(runId)) {
        return res.status(400).json({ errors: [{ message: "Invalid run id.", code: "INVALID_RUN_ID" }] });
      }
      const customTitle = req.body?.custom_title;
      if (customTitle !== undefined && customTitle !== null && typeof customTitle !== "string") {
        return res.status(422).json({ errors: [{ message: "custom_title must be a string or null.", code: "VALIDATION_ERROR" }] });
      }
      const trimmed = typeof customTitle === "string" ? customTitle.trim() : null;
      if (trimmed !== null && trimmed.length > 200) {
        return res.status(422).json({ errors: [{ message: "custom_title must be 200 characters or fewer.", code: "VALIDATION_ERROR" }] });
      }
      // Update the run.json artifact's custom_title field
      const roots = resolveWorkflowRunRoots(env);
      const root = await resolveRunRootOrThrow(roots, runId);
      const dir = runDir(root, runId);
      const runJsonPath = path.join(dir, "run.json");
      if (!(await fileExists(runJsonPath))) {
        return res.status(404).json({ errors: [{ message: "Run artifact not found.", code: "RUN_NOT_FOUND" }] });
      }
      const runJson = await readJsonFile(runJsonPath);
      runJson.custom_title = trimmed || undefined;
      await writeFile(runJsonPath, JSON.stringify(runJson, null, 2), "utf-8");
      res.json({ data: { run_id: runId, custom_title: trimmed || null } });
    } catch (error) {
      next(error);
    }
  });

  router.get("/workflow-runs/datasets", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await listWorkflowDatasets(env) });
    } catch (error) {
      next(error);
    }
  });

  router.post("/workflow-runs/datasets", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const result = await createWorkflowDataset({
        env,
        body: req.body || {},
      });
      res.status(201).json({ data: result });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_DATASET_CREATE_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-runs/datasets/:datasetId/cases", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const result = await appendWorkflowDatasetCase({
        env,
        datasetId: req.params.datasetId,
        body: req.body || {},
      });
      res.status(201).json({ data: result });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_DATASET_CASE_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/judge/rubrics", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      res.json({ data: await listJudgeRubrics(env) });
    } catch (error) {
      next(error);
    }
  });

  router.get("/prompt-variants/ready", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const snapshots = await listReadyPromptVariantSnapshots(database);
      res.json({ data: snapshots });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: { code: error.code || "PROMPT_VARIANT_ERROR" },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/prompt-variants/manifest-preview", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const body = req.body || {};
      const validationErrors = validatePromptVariantDraft(body);
      if (validationErrors.length > 0) {
        res.status(422).json({
          errors: validationErrors.map((e) => ({
            message: e.message,
            extensions: { code: "VALIDATION_ERROR", field: e.field },
          })),
        });
        return;
      }

      const manifest = buildPromptVariantManifest(body);
      const snapshotHash = promptVariantSnapshotHash(
        manifest,
        body.baseline_prompt_version || null,
      );
      res.json({
        data: {
          manifest_json: manifest,
          prompt_bundle_summary: workflowBundleSummary(manifest),
          snapshot_hash: snapshotHash,
          yaml_content: renderPromptVariantYaml(manifest, snapshotHash),
          message: "Preview only. Save/export the manifest explicitly before using it for workflow eval.",
        },
      });
    } catch (error) {
      next(error);
    }
  });

  router.get("/judge/requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const rows = await listJudgeRunRequests(database, req.query || {});
      res.json({ data: rows.map((row) => judgeRunRequestSummary(row)) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "JUDGE_RUN_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/judge/requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await createJudgeRunRequest(database, req, env, req.body || {});
      res.status(201).json({ data: judgeRunRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        const errors = Array.isArray(error.validationErrors)
          ? error.validationErrors.map((item) => ({
              message: item.message,
              extensions: { code: error.code || "VALIDATION_ERROR", field: item.field },
            }))
          : [
              {
                message: error.message,
                extensions: {
                  code: error.code || "JUDGE_RUN_REQUEST_ERROR",
                  field: error.field,
                },
              },
            ];
        res.status(error.status).json({ errors });
      } else {
        next(error);
      }
    }
  });

  router.post("/judge/requests/:requestId/cancel", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await cancelJudgeRunRequest(database, req, req.params.requestId);
      res.json({ data: judgeRunRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "JUDGE_RUN_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/judge/requests/:requestId/retry", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await retryJudgeRunRequest(database, req, env, req.params.requestId, req.body || {});
      res.status(201).json({ data: judgeRunRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "JUDGE_RUN_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/workflow-runs/requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const rows = await listWorkflowRunRequests(database, req.query || {});
      res.json({ data: rows.map((row) => workflowRunRequestSummary(row)) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_RUN_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-runs/requests/:requestId/cancel", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await cancelWorkflowRunRequest(database, req, req.params.requestId);
      res.json({ data: workflowRunRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_RUN_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-runs/requests/:requestId/retry", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const row = await retryWorkflowRunRequest(database, req, env, req.params.requestId, req.body || {});
      res.status(201).json({ data: workflowRunRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [
            {
              message: error.message,
              extensions: {
                code: error.code || "WORKFLOW_RUN_REQUEST_ERROR",
                field: error.field,
              },
            },
          ],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/workflow-runs/requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      await sendWorkflowRequest(req, res, env, database);
    } catch (error) {
      next(error);
    }
  });
};
