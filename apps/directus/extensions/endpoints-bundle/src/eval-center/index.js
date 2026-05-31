import { createHash } from "node:crypto";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";

function buildAuthGuard(req, res) {
  const accountability = req.accountability;
  if (!accountability?.user && accountability?.admin !== true) {
    res.status(403).json({
      errors: [
        {
          message: "Authentication required.",
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
  return Math.min(parsed, 180000);
}

function resolveRequestTimeoutMs(env, body) {
  const proxyTimeoutMs = resolveTimeoutMs(env);
  const requestTimeoutSeconds = Number(body?.timeout_seconds);
  if (!Number.isFinite(requestTimeoutSeconds) || requestTimeoutSeconds <= 0) {
    return proxyTimeoutMs;
  }
  return Math.min(Math.max(proxyTimeoutMs, requestTimeoutSeconds * 1000 + 10000), 180000);
}

function clampLimit(value) {
  const parsed = Number.parseInt(String(value ?? "30"), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return 30;
  return Math.min(parsed, 100);
}

function isSafeFileId(value) {
  return typeof value === "string" && /^[A-Za-z0-9._-]+$/.test(value);
}

function resolveRunsRoot(env) {
  return readEnv(env, "CLAREAD_EVAL_RUNS_ROOT") || "/directus/evals/runs";
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

function abReportPath(root, candidateRunId, reportId) {
  if (!isSafeFileId(reportId)) {
    const error = new Error("Invalid A/B report id.");
    error.status = 400;
    error.code = "INVALID_AB_REPORT_ID";
    throw error;
  }
  return path.join(runDir(root, candidateRunId), "ab", `${reportId}.json`);
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

function summarizeRun(run, report, counts) {
  const runId = run.run_id || report?.run_id;
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
    ab_report_count: counts.ab_report_count,
  };
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

async function loadRunSummary(root, runId) {
  const dir = runDir(root, runId);
  const run = await readJsonFile(path.join(dir, "run.json"));
  const reportPath = path.join(dir, "report.json");
  const report = (await fileExists(reportPath)) ? await readJsonFile(reportPath) : null;
  const caseIndex = await loadCaseIndex(root, runId);
  return summarizeRun(run, report, {
    case_artifact_count: caseIndex?.total_cases ?? await countJsonFiles(path.join(dir, "cases")),
    ab_report_count: await countJsonFiles(path.join(dir, "ab")),
  });
}

async function listRuns(root, limit) {
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }

  const summaries = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !isSafeFileId(entry.name)) continue;
    try {
      summaries.push(await loadRunSummary(root, entry.name));
    } catch {
      // Invalid or partial run directories are ignored in list view and can be inspected manually.
    }
  }

  return summaries
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
    .slice(0, limit);
}

async function loadRunDetail(root, runId) {
  const dir = runDir(root, runId);
  const run = await readJsonFile(path.join(dir, "run.json"));
  const reportPath = path.join(dir, "report.json");
  const report = (await fileExists(reportPath)) ? await readJsonFile(reportPath) : null;
  const caseIndex = await loadCaseIndex(root, runId);
  const caseSummaries = caseIndex?.cases ?? await loadCaseArtifactSummaries(root, runId, dir);
  const abReportIds = await listJsonIds(path.join(dir, "ab"));

  return {
    summary: summarizeRun(run, report, {
      case_artifact_count: caseSummaries.length,
      ab_report_count: abReportIds.length,
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
    ab_reports: abReportIds.map((id) => ({
      id,
      href: `/eval-center/runs/${encodeURIComponent(runId)}/ab/${encodeURIComponent(id)}`,
    })),
  };
}

async function loadCaseIndex(root, runId) {
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

function summarizePayload(payload) {
  if (!payload || typeof payload !== "object") return payload;
  return {
    detail: payload.detail,
    message: payload.message,
    errors: Array.isArray(payload.errors)
      ? payload.errors.slice(0, 3).map((error) => ({
          message: error?.message,
          extensions: error?.extensions
            ? {
                code: error.extensions.code,
                field: error.extensions.field,
              }
            : undefined,
        }))
      : undefined,
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

const VALID_ADAPTER_KINDS = ["fake", "in_process", "http"];
const VALID_EVAL_PURPOSES = ["dataset_regression", "prompt_experiment", "manual_debug"];
const VALID_RAG_MODES = ["off", "baseline", "rag", "rag_fallback", "settings"];
const VALID_TRACE_SCOPES = ["off", "isolated", "inherit"];
const VALID_EXECUTION_MODES = ["manual", "runner_bridge"];
const VALID_WORKFLOW_REQUEST_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"];

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

function resolveEvalsRoot(env) {
  const configured = readEnv(env, "CLAREAD_EVALS_ROOT");
  if (configured) return configured;
  const runsRoot = resolveRunsRoot(env);
  return path.dirname(runsRoot);
}

function datasetsDir(evalsRoot) {
  return path.join(evalsRoot, "datasets");
}

function runConfigsDir(evalsRoot) {
  return path.join(evalsRoot, "run-configs");
}

async function listDirectories(dirPath) {
  try {
    const entries = await readdir(dirPath, { withFileTypes: true });
    return entries.filter((e) => e.isDirectory()).map((e) => e.name);
  } catch {
    return [];
  }
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
  lines.push(`trace_project: claread-eval`);
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

function promptVariantManifestFromRow(row) {
  const storedManifest = normalizeJsonObject(row?.manifest_json);
  if (hasObjectKeys(storedManifest)) {
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
  return {
    draft_id: row.id,
    variant_id: manifest.variant_id,
    target: manifest.target,
    status: row.status,
    scope: row.scope,
    few_shot_mode: manifest.few_shot_mode,
    snapshot_hash: snapshotHash,
    manifest_json: manifest,
    prompt_override: {
      ...manifest,
      prompt_snapshot_hash: snapshotHash,
    },
    recommended_manifest_path: `evals/prompt-variants/article-analysis/${manifest.variant_id}/manifest.yaml`,
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
  if (!body.dataset_id || typeof body.dataset_id !== "string") {
    errors.push({ field: "dataset_id", message: "dataset_id is required." });
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
    runner_kind: "external_worker",
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
    expected_artifact_path: row.run_id ? `evals/runs/${row.run_id}` : null,
    source_request_id: row.source_request_id || null,
    attempt_no: row.attempt_no || 1,
    max_attempts: row.max_attempts || row.attempt_no || 1,
    retry_reason: row.retry_reason || null,
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

async function createWorkflowRunRequest(database, req, config) {
  if (!database) {
    const error = new Error("Directus database handle is unavailable.");
    error.status = 503;
    error.code = "EVAL_RUNNER_BRIDGE_UNAVAILABLE";
    throw error;
  }
  const row = workflowRequestRow(req, config);
  await database("eval_workflow_run_requests").insert(row);
  return row;
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
    .where({ status: "ready_for_eval", scope: "workflow_eval" })
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
      scope: "workflow_eval",
    })
    .first();

  if (!row) {
    const error = new Error(
      `Prompt variant "${config.prompt_variant_id}" is not ready_for_eval for workflow_eval.`,
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
    config_file: `evals/run-configs/ui-${runId}.yaml`,
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

  const runDirPath = path.join(resolveRunsRoot(env), runId);
  if (await fileExists(runDirPath)) {
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
  return database("eval_workflow_run_requests").where({ run_id: runId }).first();
}

export {
  attachPromptVariantSnapshot,
  buildRetryRunId,
  buildRetryWorkflowRequestConfig,
  isWorkflowRunRequestCancelable,
  isWorkflowRunRequestRetryable,
  listReadyPromptVariantSnapshots,
  patchYamlRunId,
  promptVariantSnapshotFromRow,
  retryWorkflowRunRequest,
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
    const runDirPath = path.join(resolveRunsRoot(env), runId);
    const runDirExists = await fileExists(runDirPath);
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
      config_file: `evals/run-configs/${configFileName}`,
      yaml_content: patchedYaml,
    };
    const bridgeRequest = executionMode === "runner_bridge"
      ? await createWorkflowRunRequest(database, req, requestConfig)
      : null;

    res.status(201).json({
      data: {
        status: bridgeRequest ? "queued_for_runner_bridge" : "pending_manual_execution",
        run_id: runId,
        preset_id: config.preset_id,
        config: null,
        execution_mode: executionMode,
        prompt_variant_id: requestConfig.prompt_variant_id,
        prompt_variant_snapshot_hash: requestConfig.prompt_variant_snapshot_hash || null,
        runner_bridge_request: bridgeRequest
          ? { run_id: bridgeRequest.run_id, status: bridgeRequest.status }
          : null,
        yaml_content: patchedYaml,
        config_file: `evals/run-configs/${configFileName}`,
        recommended_cli_command: cliCommand,
        message: bridgeRequest
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

  const runDirPath = path.join(resolveRunsRoot(env), runId);
  const runDirExists = await fileExists(runDirPath);
  if (runDirExists) {
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
  const configFileName = `ui-${runId}.yaml`;
  const cliCommand = `cd evals && uv run python -m claread_eval.runner.entrypoint --config run-configs/${configFileName}`;
  const requestConfig = {
    ...fullConfig,
    config_file: `evals/run-configs/${configFileName}`,
    yaml_content: yamlContent,
  };
  const bridgeRequest = executionMode === "runner_bridge"
    ? await createWorkflowRunRequest(database, req, requestConfig)
    : null;

  res.status(201).json({
    data: {
      status: bridgeRequest ? "queued_for_runner_bridge" : "pending_manual_execution",
      run_id: runId,
      preset_id: null,
      execution_mode: executionMode,
      prompt_variant_id: requestConfig.prompt_variant_id,
      prompt_variant_snapshot_hash: requestConfig.prompt_variant_snapshot_hash || null,
      runner_bridge_request: bridgeRequest
        ? { run_id: bridgeRequest.run_id, status: bridgeRequest.status }
        : null,
      config: fullConfig,
      yaml_content: yamlContent,
      config_file: `evals/run-configs/${configFileName}`,
      recommended_cli_command: cliCommand,
      message: "Config generated. Save the YAML content to the path below, then run the CLI command. This will NOT auto-execute.",
    },
  });
}

export default (router, context) => {
  const env = context?.env;
  const database = context?.database;

  router.post("/article-analysis/node-probe", async (req, res, next) => {
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

    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      resolveRequestTimeoutMs(env, req.body),
    );

    try {
      const upstream = await fetch(
        joinUrl(baseUrl, "/eval/article-analysis/node-probe"),
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "x-admin-api-key": adminKey,
          },
          body: JSON.stringify(req.body ?? {}),
          signal: controller.signal,
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
      if (error?.name === "AbortError") {
        res.status(504).json({
          errors: [
            {
              message: "Eval node probe timed out.",
              extensions: { code: "UPSTREAM_TIMEOUT" },
            },
          ],
        });
        return;
      }
      next(error);
    } finally {
      clearTimeout(timeout);
    }
  });

  router.get("/runs", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const root = resolveRunsRoot(env);
      const runs = await listRuns(root, clampLimit(req.query?.limit));
      res.json({
        data: {
          runs_root_configured: Boolean(root),
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
      const root = resolveRunsRoot(env);
      res.json({ data: await loadRunDetail(root, req.params.runId) });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/runs/:runId/cases/:caseId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const root = resolveRunsRoot(env);
      const artifact = await readJsonFile(
        caseArtifactPath(root, req.params.runId, req.params.caseId),
      );
      res.json({ data: artifact });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/runs/:runId/ab", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const root = resolveRunsRoot(env);
      const reportIds = await listJsonIds(path.join(runDir(root, req.params.runId), "ab"));
      res.json({
        data: reportIds.map((id) => ({
          id,
          href: `/eval-center/runs/${encodeURIComponent(req.params.runId)}/ab/${encodeURIComponent(id)}`,
        })),
      });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/runs/:runId/ab/:reportId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const root = resolveRunsRoot(env);
      const report = await readJsonFile(
        abReportPath(root, req.params.runId, req.params.reportId),
      );
      res.json({ data: report });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/ab/compare", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const baselineRunId = String(req.query?.baseline_run_id || "");
      const candidateRunId = String(req.query?.candidate_run_id || "");
      if (!baselineRunId || !candidateRunId) {
        res.status(400).json({
          errors: [
            {
              message: "baseline_run_id and candidate_run_id are required.",
              extensions: { code: "BAD_REQUEST" },
            },
          ],
        });
        return;
      }

      const root = resolveRunsRoot(env);
      const reportId = `vs-${baselineRunId}`;
      const report = await readJsonFile(abReportPath(root, candidateRunId, reportId));
      res.json({ data: report });
    } catch (error) {
      if (error?.status) sendArtifactError(res, error);
      else next(error);
    }
  });

  router.get("/workflow-runs/datasets", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const evalsRoot = resolveEvalsRoot(env);
      const dsDir = datasetsDir(evalsRoot);
      const datasetIds = await listDirectories(dsDir);
      const datasets = [];
      for (const id of datasetIds) {
        const yamlPath = path.join(dsDir, id, "dataset.yaml");
        const exists = await fileExists(yamlPath);
        datasets.push({ id, has_dataset_yaml: exists });
      }
      res.json({ data: datasets });
    } catch (error) {
      next(error);
    }
  });

  router.get("/workflow-runs/config-presets", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const evalsRoot = resolveEvalsRoot(env);
      const configsDir = runConfigsDir(evalsRoot);
      const configIds = await listYamlIds(configsDir);
      const presets = [];
      for (const id of configIds) {
        const yamlPath = path.join(configsDir, `${id}.yaml`);
        const ymlPath = path.join(configsDir, `${id}.yml`);
        const exists = (await fileExists(yamlPath)) || (await fileExists(ymlPath));
        if (exists) {
          presets.push({ id, href: `/eval-center/workflow-runs/config-presets/${id}` });
        }
      }
      res.json({ data: presets });
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
          snapshot_hash: snapshotHash,
          yaml_content: renderPromptVariantYaml(manifest, snapshotHash),
          recommended_manifest_path: `evals/prompt-variants/article-analysis/${manifest.variant_id}/manifest.yaml`,
          message: "Preview only. Save/export the manifest explicitly before using it for workflow eval.",
        },
      });
    } catch (error) {
      next(error);
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
