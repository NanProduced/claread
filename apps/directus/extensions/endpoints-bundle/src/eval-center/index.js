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
  return summarizeRun(run, report, {
    case_artifact_count: await countJsonFiles(path.join(dir, "cases")),
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
  const abReportIds = await listJsonIds(path.join(dir, "ab"));

  return {
    summary: summarizeRun(run, report, {
      case_artifact_count: caseSummaries.length,
      ab_report_count: abReportIds.length,
    }),
    run,
    report,
    case_artifacts: caseSummaries,
    ab_reports: abReportIds.map((id) => ({
      id,
      href: `/eval-center/runs/${encodeURIComponent(runId)}/ab/${encodeURIComponent(id)}`,
    })),
  };
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

const VALID_ADAPTER_KINDS = ["fake", "in_process"];
const VALID_EVAL_PURPOSES = ["dataset_regression", "prompt_experiment", "manual_debug"];
const VALID_RAG_MODES = ["off", "baseline", "rag", "rag_fallback", "settings"];

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
    lines.push(`prompt_variant_path: ../prompt-variants/article-analysis/${config.prompt_variant_id}/manifest.yaml`);
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
  if (body.run_id && !isSafeFileId(body.run_id)) {
    errors.push({ field: "run_id", message: "run_id contains unsafe characters." });
  }
  return errors;
}

async function sendWorkflowRequest(req, res, env) {
  const evalsRoot = resolveEvalsRoot(env);
  const config = req.body || {};

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

    let patchedYaml = yamlContent.replace(/^run_id:\s*.+$/m, `run_id: "${runId}"`);

    const configFileName = `ui-${runId}.yaml`;
    const cliCommand = `cd evals && uv run python -m claread_eval.runner.entrypoint --config run-configs/${configFileName}`;

    res.status(201).json({
      data: {
        status: "pending_manual_execution",
        run_id: runId,
        preset_id: config.preset_id,
        config: null,
        yaml_content: patchedYaml,
        config_file: `evals/run-configs/${configFileName}`,
        recommended_cli_command: cliCommand,
        message: "Config generated. Save the YAML content to the path below, then run the CLI command. This will NOT auto-execute.",
      },
    });
    return;
  }

  const validationErrors = validateWorkflowRunRequest(config);
  if (config.prompt_variant_id) {
    validationErrors.push({
      field: "prompt_variant_id",
      message: "Custom config v1 does not support prompt_variant_id. Use a preset (e.g. article-analysis-no-few-shot-fake) or edit the YAML manually.",
    });
  }
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

  const fullConfig = {
    run_id: runId,
    dataset_id: config.dataset_id,
    mode: "workflow",
    eval_purpose: config.eval_purpose || "dataset_regression",
    adapter_kind: config.adapter_kind || "fake",
    prompt_variant_id: config.prompt_variant_id || null,
    rag_mode: config.rag_mode || "off",
    trace_scope: config.trace_scope || "off",
    timeout_seconds: config.timeout_seconds || 120,
  };

  const yamlContent = buildYamlContent(fullConfig);
  const configFileName = `ui-${runId}.yaml`;
  const cliCommand = `cd evals && uv run python -m claread_eval.runner.entrypoint --config run-configs/${configFileName}`;

  res.status(201).json({
    data: {
      status: "pending_manual_execution",
      run_id: runId,
      preset_id: null,
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

  router.post("/workflow-runs/requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      await sendWorkflowRequest(req, res, env);
    } catch (error) {
      next(error);
    }
  });
};
