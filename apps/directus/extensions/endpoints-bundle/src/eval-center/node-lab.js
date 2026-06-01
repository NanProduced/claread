import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";

const VALID_NODES = ["grammar", "vocabulary", "translation"];
const VALID_WORKSPACES = ["single_run", "baseline_compare", "judge_compare"];
const VALID_SESSION_STATUSES = ["drafting", "active", "paused", "reviewed", "archived"];
const VALID_TRIAL_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"];
const VALID_JUDGE_REQUEST_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"];
const VALID_JUDGE_MODES = [
  "rubric_score_only",
  "rubric_plus_pairwise",
  "persona_pairwise",
  "anti_template_probe",
  "raw",
];

function nowIso() {
  return new Date().toISOString();
}

function hashText(value) {
  return createHash("sha256").update(String(value || "").trim(), "utf8").digest("hex").slice(0, 16);
}

function shortHash(value) {
  return createHash("sha256").update(JSON.stringify(value), "utf8").digest("hex").slice(0, 16);
}

function stableJson(value) {
  if (Array.isArray(value)) return value.map(stableJson);
  if (!value || typeof value !== "object") return value;
  const output = {};
  for (const key of Object.keys(value).sort()) {
    output[key] = stableJson(value[key]);
  }
  return output;
}

function generateId(prefix) {
  return `${prefix}-${randomUUID().split("-")[0]}`;
}

function normalizeRowJson(row, key) {
  const value = row?.[key];
  if (!value) return null;
  if (typeof value === "object") return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function nodeLabSessionsRoot(resolveEvalsRoot, env) {
  return path.join(resolveEvalsRoot(env), "node-lab", "sessions");
}

function trialArtifactDir(resolveEvalsRoot, env, sessionId, trialId) {
  return path.join(nodeLabSessionsRoot(resolveEvalsRoot, env), sessionId, "trials", trialId);
}

function judgeArtifactDir(resolveEvalsRoot, env, sessionId, trialId, judgeRequestId) {
  return path.join(trialArtifactDir(resolveEvalsRoot, env, sessionId, trialId), "judge", judgeRequestId);
}

async function readJsonFile(filePath) {
  const raw = await readFile(filePath, "utf-8");
  return JSON.parse(raw);
}

async function fileExists(filePath) {
  try {
    await stat(filePath);
    return true;
  } catch {
    return false;
  }
}

async function writeJsonFile(filePath, payload) {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

function requestUserId(req) {
  return req?.accountability?.user || null;
}

function sanitizeError(errorPayload) {
  if (!errorPayload) return null;
  return typeof errorPayload === "object"
    ? errorPayload
    : { message: String(errorPayload) };
}

function buildNodeLabAuthConfig(readEnv, env) {
  const baseUrl = readEnv(env, "CLAREAD_API_BASE_URL");
  const adminKey =
    readEnv(env, "CLAREAD_API_ADMIN_KEY") ||
    readEnv(env, "DAILY_READER_ADMIN_API_KEY");
  return { baseUrl, adminKey };
}

async function callUpstreamJson({
  env,
  readEnv,
  joinUrl,
  parseUpstreamError,
  resolveRequestTimeoutMs,
  reqBody,
  upstreamPath,
}) {
  const { baseUrl, adminKey } = buildNodeLabAuthConfig(readEnv, env);
  if (!baseUrl || !adminKey) {
    const error = new Error("Eval proxy is not configured.");
    error.status = 503;
    error.code = "SERVICE_UNAVAILABLE";
    throw error;
  }

  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    resolveRequestTimeoutMs(env, reqBody),
  );

  try {
    const upstream = await fetch(joinUrl(baseUrl, upstreamPath), {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "x-admin-api-key": adminKey,
      },
      body: JSON.stringify(reqBody ?? {}),
      signal: controller.signal,
    });
    if (!upstream.ok) {
      const errorPayload = await parseUpstreamError(upstream);
      const error = new Error(
        errorPayload?.detail || errorPayload?.message || "Upstream request failed.",
      );
      error.status = upstream.status;
      error.code = "UPSTREAM_EVAL_ERROR";
      error.upstream_status = upstream.status;
      throw error;
    }
    return await upstream.json();
  } catch (error) {
    if (error?.name === "AbortError") {
      const timeoutError = new Error("Node Lab request timed out.");
      timeoutError.status = 504;
      timeoutError.code = "UPSTREAM_TIMEOUT";
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function candidateDraftSummary(row) {
  return {
    candidate_id: row.candidate_id,
    node_name: row.node_name,
    label: row.label,
    description: row.description || "",
    source_kind: row.source_kind,
    edit_mode: row.edit_mode,
    status: row.status,
    instruction_layer_json: normalizeRowJson(row, "instruction_layer_json") || {},
    policy_layer_json: normalizeRowJson(row, "policy_layer_json") || {},
    few_shot_layer_json: normalizeRowJson(row, "few_shot_layer_json") || {},
    model_layer_json: normalizeRowJson(row, "model_layer_json") || {},
    normalized_manifest_json: normalizeRowJson(row, "normalized_manifest_json") || {},
    draft_hash: row.draft_hash || null,
    notes: row.notes || "",
    tags_json: normalizeRowJson(row, "tags_json") || [],
    date_created: row.date_created,
    date_updated: row.date_updated,
  };
}

function sessionSummary(row) {
  return {
    session_id: row.session_id,
    node_name: row.node_name,
    title: row.title,
    goal: row.goal || "",
    status: row.status,
    allowed_workspace_types_json: normalizeRowJson(row, "allowed_workspace_types_json") || [],
    baseline_snapshot_json: normalizeRowJson(row, "baseline_snapshot_json") || {},
    baseline_snapshot_hash: row.baseline_snapshot_hash || null,
    candidate_registry_json: normalizeRowJson(row, "candidate_registry_json") || [],
    judge_config_snapshot_json: normalizeRowJson(row, "judge_config_snapshot_json") || null,
    judge_config_snapshot_hash: row.judge_config_snapshot_hash || null,
    aggregate_summary_json: normalizeRowJson(row, "aggregate_summary_json") || {},
    decision_summary_json: normalizeRowJson(row, "decision_summary_json") || {},
    notes: row.notes || "",
    tags_json: normalizeRowJson(row, "tags_json") || [],
    date_created: row.date_created,
    date_updated: row.date_updated,
  };
}

function trialSummary(row) {
  return {
    trial_id: row.trial_id,
    session_id: row.session_id,
    node_name: row.node_name,
    workspace_type: row.workspace_type,
    status: row.status,
    execution_mode: row.execution_mode,
    input_text_hash: row.input_text_hash,
    input_excerpt: row.input_excerpt,
    reading_goal: row.reading_goal,
    reading_variant: row.reading_variant,
    source_type: row.source_type,
    baseline_snapshot_hash: row.baseline_snapshot_hash || null,
    candidate_snapshot_hashes_json: normalizeRowJson(row, "candidate_snapshot_hashes_json") || [],
    judge_config_snapshot_hash: row.judge_config_snapshot_hash || null,
    result_kind: row.result_kind,
    result_summary_json: normalizeRowJson(row, "result_summary_json") || {},
    artifact_path: row.artifact_path || null,
    started_at: row.started_at,
    finished_at: row.finished_at,
    error_json: normalizeRowJson(row, "error_json"),
    review_state: row.review_state,
    decision_note: row.decision_note || "",
    date_created: row.date_created,
    date_updated: row.date_updated,
  };
}

function judgeConfigSummary(row) {
  return {
    judge_config_id: row.judge_config_id,
    node_name: row.node_name,
    label: row.label,
    description: row.description || "",
    judge_mode: row.judge_mode,
    rubric_source_json: normalizeRowJson(row, "rubric_source_json") || {},
    persona_json: normalizeRowJson(row, "persona_json") || null,
    prompt_templates_json: normalizeRowJson(row, "prompt_templates_json") || {},
    output_schema_json: normalizeRowJson(row, "output_schema_json") || {},
    parameters_json: normalizeRowJson(row, "parameters_json") || {},
    judger_models_json: normalizeRowJson(row, "judger_models_json") || [],
    normalized_config_json: normalizeRowJson(row, "normalized_config_json") || {},
    draft_hash: row.draft_hash || null,
    status: row.status,
    notes: row.notes || "",
    date_created: row.date_created,
    date_updated: row.date_updated,
  };
}

function judgeRequestSummary(row) {
  return {
    judge_request_id: row.judge_request_id,
    trial_id: row.trial_id,
    session_id: row.session_id,
    node_name: row.node_name,
    status: row.status,
    judge_config_snapshot_hash: row.judge_config_snapshot_hash || null,
    judge_config_snapshot_json: normalizeRowJson(row, "judge_config_snapshot_json") || {},
    participants_json: normalizeRowJson(row, "participants_json") || {},
    artifact_path: row.artifact_path || null,
    attempt_no: row.attempt_no,
    max_attempts: row.max_attempts,
    retry_reason: row.retry_reason || null,
    started_at: row.started_at,
    finished_at: row.finished_at,
    error_json: normalizeRowJson(row, "error_json"),
    notes: row.notes || "",
    tags_json: normalizeRowJson(row, "tags_json") || [],
    date_created: row.date_created,
    date_updated: row.date_updated,
  };
}

function validateNodeName(nodeName) {
  if (!VALID_NODES.includes(String(nodeName || ""))) {
    const error = new Error(`node_name must be one of: ${VALID_NODES.join(", ")}.`);
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "node_name";
    throw error;
  }
}

async function createSessionFromPayload(database, req, body, isSafeFileId) {
  const nodeName = String(body.node_name || "");
  validateNodeName(nodeName);
  validateStatusValue(body.status || "drafting", VALID_SESSION_STATUSES, "status");
  const now = nowIso();
  const sessionId = validateIdentifier(
    body.session_id || generateId("node-lab-session"),
    "session_id",
    isSafeFileId,
  );
  const row = {
    session_id: sessionId,
    node_name: nodeName,
    title: String(body.title || `${nodeName} session`).trim() || `${nodeName} session`,
    goal: String(body.goal || "").trim(),
    status: String(body.status || "drafting"),
    allowed_workspace_types_json: JSON.stringify(body.allowed_workspace_types_json || VALID_WORKSPACES),
    baseline_snapshot_json: JSON.stringify(stableJson(body.baseline_snapshot_json || {})),
    baseline_snapshot_hash: body.baseline_snapshot_hash || null,
    candidate_registry_json: JSON.stringify(stableJson(body.candidate_registry_json || [])),
    judge_config_snapshot_json: body.judge_config_snapshot_json
      ? JSON.stringify(stableJson(body.judge_config_snapshot_json))
      : null,
    judge_config_snapshot_hash: body.judge_config_snapshot_hash || null,
    aggregate_summary_json: JSON.stringify(stableJson(body.aggregate_summary_json || {})),
    decision_summary_json: JSON.stringify(stableJson(body.decision_summary_json || {})),
    notes: String(body.notes || "").trim() || null,
    tags_json: JSON.stringify(body.tags_json || []),
    user_created: requestUserId(req),
    user_updated: requestUserId(req),
    date_created: now,
    date_updated: now,
  };
  await database("eval_node_lab_sessions").insert(row);
  return database("eval_node_lab_sessions").where({ session_id: sessionId }).first();
}

async function updateSessionAggregate(database, sessionId) {
  const trials = await database("eval_node_lab_trials")
    .where({ session_id: sessionId })
    .orderBy("date_created", "desc");
  const aggregate = {
    trial_count: trials.length,
    workspace_counts: VALID_WORKSPACES.reduce((acc, workspace) => {
      acc[workspace] = trials.filter((trial) => trial.workspace_type === workspace).length;
      return acc;
    }, {}),
    last_trial_id: trials[0]?.trial_id || null,
    last_trial_at: trials[0]?.date_created || null,
  };
  await database("eval_node_lab_sessions")
    .where({ session_id: sessionId })
    .update({
      aggregate_summary_json: JSON.stringify(aggregate),
      date_updated: nowIso(),
    });
}

async function ensureSessionForPersist(database, req, payload, isSafeFileId) {
  if (payload.session_id) {
    validateIdentifier(payload.session_id, "session_id", isSafeFileId);
    const existing = await database("eval_node_lab_sessions")
      .where({ session_id: payload.session_id })
      .first();
    if (!existing) {
      const error = new Error("Node Lab session was not found.");
      error.status = 404;
      error.code = "NODE_LAB_SESSION_NOT_FOUND";
      throw error;
    }
    return existing;
  }
  return createSessionFromPayload(database, req, payload.session || {
    node_name: payload.request?.node_name,
    title: `${payload.request?.node_name || "node"} quick session`,
    goal: payload.workspace_type === "baseline_compare" ? "Compare baseline and candidate" : "Ad hoc node lab capture",
    baseline_snapshot_json: payload.baseline_snapshot_json || {},
    candidate_registry_json: payload.request?.candidate_override ? [payload.request.candidate_override] : [],
  }, isSafeFileId);
}

async function writeSessionArtifact(resolveEvalsRoot, env, sessionRow) {
  const summary = sessionSummary(sessionRow);
  const filePath = path.join(nodeLabSessionsRoot(resolveEvalsRoot, env), sessionRow.session_id, "session.json");
  await writeJsonFile(filePath, summary);
}

async function persistTrial({
  database,
  req,
  env,
  resolveEvalsRoot,
  isSafeFileId,
  workspaceType,
  requestPayload,
  resultPayload,
  sessionRow,
}) {
  const trialId = validateIdentifier(
    requestPayload.trial_id || generateId("node-lab-trial"),
    "trial_id",
    isSafeFileId,
  );
  const finishedAtDate = new Date();
  const totalLatencyMs = latencyMsForTrial(workspaceType, resultPayload);
  const startedAtDate = new Date(finishedAtDate.getTime() - Math.max(totalLatencyMs, 0));
  const startedAt = startedAtDate.toISOString();
  const finishedAt = finishedAtDate.toISOString();
  const artifactDir = trialArtifactDir(resolveEvalsRoot, env, sessionRow.session_id, trialId);
  const artifactPath = `evals/node-lab/sessions/${sessionRow.session_id}/trials/${trialId}/result.json`;
  const baselineSnapshotHash = resultPayload?.baseline?.prompt_identity?.prompt_snapshot_hash
    || resultPayload?.run?.prompt_identity?.prompt_snapshot_hash
    || requestPayload?.baseline_snapshot_hash
    || null;
  const candidateHashes = resultPayload?.candidate
    ? [resultPayload.candidate.prompt_identity?.prompt_snapshot_hash].filter(Boolean)
    : [resultPayload?.run?.prompt_identity?.prompt_snapshot_hash].filter(Boolean);
  const inputText = String(requestPayload.request?.text || "");
  const resultStatus = workspaceType === "baseline_compare"
    ? trialResultStatusForCompare(resultPayload)
    : {
        run_status: resultPayload?.run?.status || "unknown",
      };
  const status = workspaceType === "baseline_compare"
    ? trialExecutionStatusForCompare(resultPayload)
    : (resultPayload?.run?.status || resultPayload?.status || "succeeded");
  const summaryPayload = workspaceType === "baseline_compare"
    ? {
        result_status: resultStatus,
        compare_summary: resultPayload?.compare_summary || {},
      }
    : {
        result_status: resultStatus,
        participant_label: resultPayload?.run?.participant_label || "baseline",
      };

  const row = {
    trial_id: trialId,
    session_id: sessionRow.session_id,
    node_name: requestPayload.request.node_name,
    workspace_type: workspaceType,
    status,
    execution_mode: "sync",
    input_text_hash: hashText(inputText),
    input_excerpt: inputText.slice(0, 280),
    reading_goal: requestPayload.request.reading_goal,
    reading_variant: requestPayload.request.reading_variant,
    source_type: requestPayload.request.source_type || "user_input",
    baseline_snapshot_hash: baselineSnapshotHash,
    candidate_snapshot_hashes_json: JSON.stringify(candidateHashes),
    judge_config_snapshot_hash: null,
    result_kind: workspaceType === "baseline_compare" ? "compare_result" : "single_run_result",
    result_summary_json: JSON.stringify(stableJson(summaryPayload)),
    artifact_path: artifactPath,
    started_at: startedAt,
    finished_at: finishedAt,
    error_json: trialErrorJsonForResult(workspaceType, resultPayload)
      ? JSON.stringify(trialErrorJsonForResult(workspaceType, resultPayload))
      : null,
    review_state: "unreviewed",
    decision_note: null,
    user_created: requestUserId(req),
    user_updated: requestUserId(req),
    date_created: startedAt,
    date_updated: finishedAt,
  };

  await writeJsonFile(path.join(artifactDir, "trial.json"), {
    trial_id: trialId,
    session_id: sessionRow.session_id,
    workspace_type: workspaceType,
    request: requestPayload.request,
    summary: summaryPayload,
  });
  await writeJsonFile(path.join(artifactDir, "result.json"), resultPayload);
  await database.transaction(async (trx) => {
    await trx("eval_node_lab_trials").insert(row);
    await updateSessionAggregate(trx, sessionRow.session_id);
  });
  const storedTrial = await database("eval_node_lab_trials").where({ trial_id: trialId }).first();
  await writeSessionArtifact(
    resolveEvalsRoot,
    env,
    await database("eval_node_lab_sessions").where({ session_id: sessionRow.session_id }).first(),
  );
  return storedTrial;
}

async function createJudgeRequest(database, req, env, resolveEvalsRoot, body, isSafeFileId) {
  validateIdentifier(body.trial_id, "trial_id", isSafeFileId);
  const trialRow = await database("eval_node_lab_trials").where({ trial_id: body.trial_id }).first();
  if (!trialRow) {
    const error = new Error("Node Lab trial was not found.");
    error.status = 404;
    error.code = "NODE_LAB_TRIAL_NOT_FOUND";
    throw error;
  }
  let configSnapshot = body.judge_config_snapshot_json || null;
  if (!configSnapshot && body.judge_config_id) {
    const judgeConfig = await database("eval_node_lab_judge_configs")
      .where({ judge_config_id: body.judge_config_id })
      .first();
    if (!judgeConfig) {
      const error = new Error("Node Lab judge config was not found.");
      error.status = 404;
      error.code = "NODE_LAB_JUDGE_CONFIG_NOT_FOUND";
      throw error;
    }
    configSnapshot = normalizeRowJson(judgeConfig, "normalized_config_json") || judgeConfigSummary(judgeConfig);
  }
  if (!configSnapshot) {
    const error = new Error("judge_config_id or judge_config_snapshot_json is required.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "judge_config_id";
    throw error;
  }
  const judgeMode = String(configSnapshot.judge_mode || "");
  if (!VALID_JUDGE_MODES.includes(judgeMode)) {
    const error = new Error(`judge_mode must be one of: ${VALID_JUDGE_MODES.join(", ")}.`);
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "judge_mode";
    throw error;
  }
  const judgeRequestId = validateIdentifier(
    body.judge_request_id || generateId("node-lab-judge"),
    "judge_request_id",
    isSafeFileId,
  );
  const participants = body.participants_json || {
    baseline: { trial_id: body.trial_id, label: "baseline" },
    candidate: { trial_id: body.trial_id, label: "candidate" },
  };
  const artifactPath = `evals/node-lab/sessions/${trialRow.session_id}/trials/${trialRow.trial_id}/judge/${judgeRequestId}/judge-result.json`;
  const row = {
    judge_request_id: judgeRequestId,
    trial_id: trialRow.trial_id,
    session_id: trialRow.session_id,
    node_name: trialRow.node_name,
    status: "queued",
    judge_config_snapshot_json: JSON.stringify(stableJson(configSnapshot)),
    judge_config_snapshot_hash: shortHash(stableJson(configSnapshot)),
    participants_json: JSON.stringify(stableJson(participants)),
    artifact_path: artifactPath,
    attempt_no: 1,
    max_attempts: 1,
    retry_reason: null,
    lease_owner: null,
    lease_until: null,
    heartbeat_at: null,
    started_at: null,
    finished_at: null,
    error_json: null,
    notes: String(body.notes || "").trim() || null,
    tags_json: JSON.stringify(body.tags_json || []),
    user_created: requestUserId(req),
    user_updated: requestUserId(req),
    date_created: nowIso(),
    date_updated: nowIso(),
  };
  await database("eval_node_lab_judge_requests").insert(row);
  await writeJsonFile(
    path.join(judgeArtifactDir(resolveEvalsRoot, env, trialRow.session_id, trialRow.trial_id, judgeRequestId), "judge-config.json"),
    configSnapshot,
  );
  return database("eval_node_lab_judge_requests").where({ judge_request_id: judgeRequestId }).first();
}

async function loadSessionDetail(database, resolveEvalsRoot, env, sessionId) {
  const row = await database("eval_node_lab_sessions").where({ session_id: sessionId }).first();
  if (!row) {
    const error = new Error("Node Lab session was not found.");
    error.status = 404;
    error.code = "NODE_LAB_SESSION_NOT_FOUND";
    throw error;
  }
  const trials = await database("eval_node_lab_trials")
    .where({ session_id: sessionId })
    .orderBy("date_created", "desc");
  const judgeRequests = await database("eval_node_lab_judge_requests")
    .where({ session_id: sessionId })
    .orderBy("date_created", "desc");
  const sessionArtifactPath = path.join(nodeLabSessionsRoot(resolveEvalsRoot, env), sessionId, "session.json");
  return {
    session: sessionSummary(row),
    session_artifact: (await fileExists(sessionArtifactPath)) ? await readJsonFile(sessionArtifactPath) : null,
    trials: trials.map(trialSummary),
    judge_requests: judgeRequests.map(judgeRequestSummary),
  };
}

async function updateByKey(database, tableName, keyName, keyValue, patch) {
  const existing = await database(tableName).where({ [keyName]: keyValue }).first();
  if (!existing) return null;
  const row = {
    ...patch,
    user_updated: patch.user_updated,
    date_updated: nowIso(),
  };
  await database(tableName).where({ [keyName]: keyValue }).update(row);
  return database(tableName).where({ [keyName]: keyValue }).first();
}

function validationError(message, field) {
  const error = new Error(message);
  error.status = 422;
  error.code = "VALIDATION_ERROR";
  error.field = field;
  return error;
}

function validateIdentifier(value, field, isSafeFileId) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    throw validationError(`${field} is required.`, field);
  }
  if (!isSafeFileId(normalized)) {
    throw validationError(`${field} contains unsafe characters.`, field);
  }
  return normalized;
}

function validateStatusValue(value, validValues, field) {
  if (value === undefined || value === null || value === "") return;
  if (!validValues.includes(String(value))) {
    throw validationError(`${field} must be one of: ${validValues.join(", ")}.`, field);
  }
}

function trialExecutionStatusForCompare(resultPayload) {
  const baselineStatus = resultPayload?.baseline?.status || "failed";
  const candidateStatus = resultPayload?.candidate?.status || "failed";
  if (baselineStatus === "succeeded" && candidateStatus === "succeeded") {
    return "succeeded";
  }
  if (baselineStatus === "cancelled" || candidateStatus === "cancelled") {
    return "cancelled";
  }
  if (baselineStatus === "timeout" && candidateStatus === "timeout") {
    return "failed";
  }
  return "failed";
}

function trialResultStatusForCompare(resultPayload) {
  const baselineStatus = resultPayload?.baseline?.status || "failed";
  const candidateStatus = resultPayload?.candidate?.status || "failed";
  if (baselineStatus === "succeeded" && candidateStatus === "succeeded") {
    return {
      baseline_status: baselineStatus,
      candidate_status: candidateStatus,
      compare_status: "complete",
    };
  }
  if (baselineStatus === "succeeded" || candidateStatus === "succeeded") {
    return {
      baseline_status: baselineStatus,
      candidate_status: candidateStatus,
      compare_status: "partial_failure",
    };
  }
  return {
    baseline_status: baselineStatus,
    candidate_status: candidateStatus,
    compare_status: "total_failure",
  };
}

function trialErrorJsonForResult(workspaceType, resultPayload) {
  if (workspaceType !== "baseline_compare") {
    return resultPayload?.run?.error || resultPayload?.error || null;
  }
  const errors = {};
  if (resultPayload?.baseline?.error) errors.baseline = resultPayload.baseline.error;
  if (resultPayload?.candidate?.error) errors.candidate = resultPayload.candidate.error;
  return Object.keys(errors).length > 0 ? errors : null;
}

function latencyMsForTrial(workspaceType, resultPayload) {
  if (workspaceType !== "baseline_compare") {
    return Number(resultPayload?.run?.runtime_summary?.latency_ms || 0);
  }
  return Math.max(
    Number(resultPayload?.baseline?.runtime_summary?.latency_ms || 0),
    Number(resultPayload?.candidate?.runtime_summary?.latency_ms || 0),
  );
}

export function registerNodeLabRoutes(router, context, deps) {
  const env = context?.env;
  const database = context?.database;
  const {
    buildAuthGuard,
    clampLimit,
    joinUrl,
    parseUpstreamError,
    readEnv,
    resolveEvalsRoot,
    resolveRequestTimeoutMs,
    isSafeFileId,
  } = deps;

  router.post("/node-lab/baseline-config", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const payload = await callUpstreamJson({
        env,
        readEnv,
        joinUrl,
        parseUpstreamError,
        resolveRequestTimeoutMs,
        reqBody: req.body || {},
        upstreamPath: "/eval/article-analysis/node-lab/baseline",
      });
      res.json({ data: payload });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/node-lab/candidates", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const builder = database("eval_node_lab_candidate_drafts").orderBy("date_updated", "desc");
      if (req.query?.node_name) builder.where({ node_name: String(req.query.node_name) });
      const rows = await builder.limit(clampLimit(req.query?.limit));
      res.json({ data: rows.map(candidateDraftSummary) });
    } catch (error) {
      next(error);
    }
  });

  router.post("/node-lab/candidates", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      validateNodeName(body.node_name);
      const candidateId = validateIdentifier(
        body.candidate_id || generateId("node-lab-candidate"),
        "candidate_id",
        isSafeFileId,
      );
      validateStatusValue(body.status || "draft", ["draft", "ready", "archived"], "status");
      const normalizedManifest = stableJson(body.normalized_manifest_json || {
        instruction_layer: body.instruction_layer_json || {},
        policy_layer: body.policy_layer_json || {},
        few_shot_layer: body.few_shot_layer_json || {},
        model_layer: body.model_layer_json || {},
      });
      const row = {
        candidate_id: candidateId,
        node_name: body.node_name,
        label: String(body.label || candidateId).trim() || candidateId,
        description: String(body.description || "").trim() || null,
        source_kind: String(body.source_kind || "baseline_clone"),
        edit_mode: String(body.edit_mode || "structured"),
        instruction_layer_json: JSON.stringify(stableJson(body.instruction_layer_json || {})),
        policy_layer_json: JSON.stringify(stableJson(body.policy_layer_json || {})),
        few_shot_layer_json: JSON.stringify(stableJson(body.few_shot_layer_json || {})),
        model_layer_json: JSON.stringify(stableJson(body.model_layer_json || {})),
        normalized_manifest_json: JSON.stringify(normalizedManifest),
        draft_hash: shortHash(normalizedManifest),
        status: String(body.status || "draft"),
        notes: String(body.notes || "").trim() || null,
        tags_json: JSON.stringify(body.tags_json || []),
        user_created: requestUserId(req),
        user_updated: requestUserId(req),
        date_created: nowIso(),
        date_updated: nowIso(),
      };
      await database("eval_node_lab_candidate_drafts").insert(row);
      const stored = await database("eval_node_lab_candidate_drafts").where({ candidate_id: candidateId }).first();
      res.status(201).json({ data: candidateDraftSummary(stored) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.patch("/node-lab/candidates/:candidateId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      validateIdentifier(req.params.candidateId, "candidate_id", isSafeFileId);
      validateStatusValue(body.status, ["draft", "ready", "archived"], "status");
      const normalizedManifest = stableJson(body.normalized_manifest_json || {
        instruction_layer: body.instruction_layer_json || {},
        policy_layer: body.policy_layer_json || {},
        few_shot_layer: body.few_shot_layer_json || {},
        model_layer: body.model_layer_json || {},
      });
      const stored = await updateByKey(
        database,
        "eval_node_lab_candidate_drafts",
        "candidate_id",
        req.params.candidateId,
        {
          label: body.label,
          description: body.description,
          source_kind: body.source_kind,
          edit_mode: body.edit_mode,
          instruction_layer_json: JSON.stringify(stableJson(body.instruction_layer_json || {})),
          policy_layer_json: JSON.stringify(stableJson(body.policy_layer_json || {})),
          few_shot_layer_json: JSON.stringify(stableJson(body.few_shot_layer_json || {})),
          model_layer_json: JSON.stringify(stableJson(body.model_layer_json || {})),
          normalized_manifest_json: JSON.stringify(normalizedManifest),
          draft_hash: shortHash(normalizedManifest),
          status: body.status,
          notes: body.notes,
          tags_json: JSON.stringify(body.tags_json || []),
          user_updated: requestUserId(req),
        },
      );
      if (!stored) throw Object.assign(new Error("Candidate was not found."), { status: 404, code: "NODE_LAB_CANDIDATE_NOT_FOUND" });
      res.json({ data: candidateDraftSummary(stored) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/node-lab/sessions", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const builder = database("eval_node_lab_sessions").orderBy("date_updated", "desc");
      if (req.query?.node_name) builder.where({ node_name: String(req.query.node_name) });
      const rows = await builder.limit(clampLimit(req.query?.limit));
      res.json({ data: rows.map(sessionSummary) });
    } catch (error) {
      next(error);
    }
  });

  router.post("/node-lab/sessions", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateStatusValue((req.body || {}).status || "drafting", VALID_SESSION_STATUSES, "status");
      const row = await createSessionFromPayload(database, req, req.body || {}, isSafeFileId);
      await writeSessionArtifact(resolveEvalsRoot, env, row);
      res.status(201).json({ data: sessionSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/node-lab/sessions/:sessionId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.sessionId, "session_id", isSafeFileId);
      res.json({ data: await loadSessionDetail(database, resolveEvalsRoot, env, req.params.sessionId) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.patch("/node-lab/sessions/:sessionId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      validateIdentifier(req.params.sessionId, "session_id", isSafeFileId);
      validateStatusValue(body.status, VALID_SESSION_STATUSES, "status");
      const stored = await updateByKey(
        database,
        "eval_node_lab_sessions",
        "session_id",
        req.params.sessionId,
        {
          title: body.title,
          goal: body.goal,
          status: body.status,
          allowed_workspace_types_json: JSON.stringify(body.allowed_workspace_types_json || VALID_WORKSPACES),
          baseline_snapshot_json: JSON.stringify(stableJson(body.baseline_snapshot_json || {})),
          baseline_snapshot_hash: body.baseline_snapshot_hash || null,
          candidate_registry_json: JSON.stringify(stableJson(body.candidate_registry_json || [])),
          judge_config_snapshot_json: body.judge_config_snapshot_json
            ? JSON.stringify(stableJson(body.judge_config_snapshot_json))
            : null,
          judge_config_snapshot_hash: body.judge_config_snapshot_hash || null,
          aggregate_summary_json: JSON.stringify(stableJson(body.aggregate_summary_json || {})),
          decision_summary_json: JSON.stringify(stableJson(body.decision_summary_json || {})),
          notes: body.notes,
          tags_json: JSON.stringify(body.tags_json || []),
          user_updated: requestUserId(req),
        },
      );
      if (!stored) throw Object.assign(new Error("Session was not found."), { status: 404, code: "NODE_LAB_SESSION_NOT_FOUND" });
      await writeSessionArtifact(resolveEvalsRoot, env, stored);
      res.json({ data: sessionSummary(stored) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/node-lab/run", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      const payload = await callUpstreamJson({
        env,
        readEnv,
        joinUrl,
        parseUpstreamError,
        resolveRequestTimeoutMs,
        reqBody: body.request || {},
        upstreamPath: "/eval/article-analysis/node-lab/run",
      });
      let persisted = null;
      let session = null;
      if (body.persist_trial) {
        session = await ensureSessionForPersist(database, req, body, isSafeFileId);
        persisted = await persistTrial({
          database,
          req,
          env,
          resolveEvalsRoot,
          isSafeFileId,
          workspaceType: "single_run",
          requestPayload: body,
          resultPayload: payload,
          sessionRow: session,
        });
      }
      res.json({
        data: {
          result: payload,
          session: session ? sessionSummary(session) : null,
          trial: persisted ? trialSummary(persisted) : null,
        },
      });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/node-lab/compare", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      const payload = await callUpstreamJson({
        env,
        readEnv,
        joinUrl,
        parseUpstreamError,
        resolveRequestTimeoutMs,
        reqBody: body.request || {},
        upstreamPath: "/eval/article-analysis/node-lab/compare",
      });
      let persisted = null;
      let session = null;
      if (body.persist_trial) {
        session = await ensureSessionForPersist(database, req, body, isSafeFileId);
        persisted = await persistTrial({
          database,
          req,
          env,
          resolveEvalsRoot,
          isSafeFileId,
          workspaceType: "baseline_compare",
          requestPayload: body,
          resultPayload: payload,
          sessionRow: session,
        });
      }
      res.json({
        data: {
          result: payload,
          session: session ? sessionSummary(session) : null,
          trial: persisted ? trialSummary(persisted) : null,
        },
      });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/node-lab/trials", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const builder = database("eval_node_lab_trials").orderBy("date_created", "desc");
      if (req.query?.session_id) builder.where({ session_id: String(req.query.session_id) });
      if (req.query?.node_name) builder.where({ node_name: String(req.query.node_name) });
      const rows = await builder.limit(clampLimit(req.query?.limit));
      res.json({ data: rows.map(trialSummary) });
    } catch (error) {
      next(error);
    }
  });

  router.get("/node-lab/trials/:trialId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.trialId, "trial_id", isSafeFileId);
      const row = await database("eval_node_lab_trials").where({ trial_id: req.params.trialId }).first();
      if (!row) throw Object.assign(new Error("Trial was not found."), { status: 404, code: "NODE_LAB_TRIAL_NOT_FOUND" });
      const absoluteArtifactPath = row.artifact_path
        ? path.join(resolveEvalsRoot(env), row.artifact_path.replace(/^evals[\\/]/, ""))
        : null;
      const result = absoluteArtifactPath && await fileExists(absoluteArtifactPath)
        ? await readJsonFile(absoluteArtifactPath)
        : null;
      res.json({ data: { trial: trialSummary(row), result } });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/node-lab/judge-configs", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const builder = database("eval_node_lab_judge_configs").orderBy("date_updated", "desc");
      if (req.query?.node_name) builder.where({ node_name: String(req.query.node_name) });
      const rows = await builder.limit(clampLimit(req.query?.limit));
      res.json({ data: rows.map(judgeConfigSummary) });
    } catch (error) {
      next(error);
    }
  });

  router.post("/node-lab/judge-configs", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      validateNodeName(body.node_name);
      const mode = String(body.judge_mode || "rubric_plus_pairwise");
      if (!VALID_JUDGE_MODES.includes(mode)) throw validationError(`judge_mode must be one of: ${VALID_JUDGE_MODES.join(", ")}.`, "judge_mode");
      const judgerModels = Array.isArray(body.judger_models_json) ? body.judger_models_json : [];
      if (judgerModels.length > 3) {
        throw validationError("judger_models_json cannot contain more than 3 judgers.", "judger_models_json");
      }
      const judgeConfigId = validateIdentifier(
        body.judge_config_id || generateId("node-lab-judge-config"),
        "judge_config_id",
        isSafeFileId,
      );
      validateStatusValue(body.status || "draft", ["draft", "ready", "archived"], "status");
      const normalizedConfig = stableJson(body.normalized_config_json || {
        judge_mode: mode,
        rubric_source_json: body.rubric_source_json || {},
        persona_json: body.persona_json || null,
        prompt_templates_json: body.prompt_templates_json || {},
        output_schema_json: body.output_schema_json || {},
        parameters_json: body.parameters_json || {},
        judger_models_json: judgerModels,
      });
      const row = {
        judge_config_id: judgeConfigId,
        node_name: body.node_name,
        label: String(body.label || judgeConfigId).trim() || judgeConfigId,
        description: String(body.description || "").trim() || null,
        judge_mode: mode,
        rubric_source_json: JSON.stringify(stableJson(body.rubric_source_json || {})),
        persona_json: body.persona_json ? JSON.stringify(stableJson(body.persona_json)) : null,
        prompt_templates_json: JSON.stringify(stableJson(body.prompt_templates_json || {})),
        output_schema_json: JSON.stringify(stableJson(body.output_schema_json || {})),
        parameters_json: JSON.stringify(stableJson(body.parameters_json || {})),
        judger_models_json: JSON.stringify(stableJson(judgerModels)),
        normalized_config_json: JSON.stringify(normalizedConfig),
        draft_hash: shortHash(normalizedConfig),
        status: String(body.status || "draft"),
        notes: String(body.notes || "").trim() || null,
        user_created: requestUserId(req),
        user_updated: requestUserId(req),
        date_created: nowIso(),
        date_updated: nowIso(),
      };
      await database("eval_node_lab_judge_configs").insert(row);
      const stored = await database("eval_node_lab_judge_configs").where({ judge_config_id: judgeConfigId }).first();
      res.status(201).json({ data: judgeConfigSummary(stored) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.patch("/node-lab/judge-configs/:judgeConfigId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      validateIdentifier(req.params.judgeConfigId, "judge_config_id", isSafeFileId);
      validateStatusValue(body.status, ["draft", "ready", "archived"], "status");
      if (body.judge_mode && !VALID_JUDGE_MODES.includes(String(body.judge_mode))) {
        throw validationError(`judge_mode must be one of: ${VALID_JUDGE_MODES.join(", ")}.`, "judge_mode");
      }
      const judgerModels = Array.isArray(body.judger_models_json) ? body.judger_models_json : [];
      if (judgerModels.length > 3) {
        throw validationError("judger_models_json cannot contain more than 3 judgers.", "judger_models_json");
      }
      const normalizedConfig = stableJson(body.normalized_config_json || {
        judge_mode: body.judge_mode,
        rubric_source_json: body.rubric_source_json || {},
        persona_json: body.persona_json || null,
        prompt_templates_json: body.prompt_templates_json || {},
        output_schema_json: body.output_schema_json || {},
        parameters_json: body.parameters_json || {},
        judger_models_json: judgerModels,
      });
      const stored = await updateByKey(
        database,
        "eval_node_lab_judge_configs",
        "judge_config_id",
        req.params.judgeConfigId,
        {
          label: body.label,
          description: body.description,
          judge_mode: body.judge_mode,
          rubric_source_json: JSON.stringify(stableJson(body.rubric_source_json || {})),
          persona_json: body.persona_json ? JSON.stringify(stableJson(body.persona_json)) : null,
          prompt_templates_json: JSON.stringify(stableJson(body.prompt_templates_json || {})),
          output_schema_json: JSON.stringify(stableJson(body.output_schema_json || {})),
          parameters_json: JSON.stringify(stableJson(body.parameters_json || {})),
          judger_models_json: JSON.stringify(stableJson(judgerModels)),
          normalized_config_json: JSON.stringify(normalizedConfig),
          draft_hash: shortHash(normalizedConfig),
          status: body.status,
          notes: body.notes,
          user_updated: requestUserId(req),
        },
      );
      if (!stored) throw Object.assign(new Error("Judge config was not found."), { status: 404, code: "NODE_LAB_JUDGE_CONFIG_NOT_FOUND" });
      res.json({ data: judgeConfigSummary(stored) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/node-lab/judge-requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const builder = database("eval_node_lab_judge_requests").orderBy("date_created", "desc");
      if (req.query?.session_id) builder.where({ session_id: String(req.query.session_id) });
      if (req.query?.trial_id) builder.where({ trial_id: String(req.query.trial_id) });
      const rows = await builder.limit(clampLimit(req.query?.limit));
      res.json({ data: rows.map(judgeRequestSummary) });
    } catch (error) {
      next(error);
    }
  });

  router.post("/node-lab/judge-requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const row = await createJudgeRequest(database, req, env, resolveEvalsRoot, req.body || {}, isSafeFileId);
      res.status(201).json({ data: judgeRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.get("/node-lab/judge-requests/:requestId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.requestId, "judge_request_id", isSafeFileId);
      const row = await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .first();
      if (!row) throw Object.assign(new Error("Judge request was not found."), { status: 404, code: "NODE_LAB_JUDGE_REQUEST_NOT_FOUND" });
      const absoluteArtifactPath = row.artifact_path
        ? path.join(resolveEvalsRoot(env), row.artifact_path.replace(/^evals[\\/]/, ""))
        : null;
      const artifact = absoluteArtifactPath && await fileExists(absoluteArtifactPath)
        ? await readJsonFile(absoluteArtifactPath)
        : null;
      res.json({ data: { request: judgeRequestSummary(row), result: artifact } });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/node-lab/judge-requests/:requestId/cancel", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.requestId, "judge_request_id", isSafeFileId);
      const current = await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .first();
      if (!current) throw Object.assign(new Error("Judge request was not found."), { status: 404, code: "NODE_LAB_JUDGE_REQUEST_NOT_FOUND" });
      if (!["queued", "running"].includes(current.status)) {
        throw Object.assign(new Error("Only queued or running requests can be cancelled."), { status: 409, code: "NODE_LAB_JUDGE_REQUEST_NOT_CANCELABLE" });
      }
      await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .update({
          status: "cancelled",
          finished_at: nowIso(),
          user_updated: requestUserId(req),
          date_updated: nowIso(),
        });
      const row = await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .first();
      res.json({ data: judgeRequestSummary(row) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code } }],
        });
      } else {
        next(error);
      }
    }
  });

  router.post("/node-lab/judge-requests/:requestId/retry", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.requestId, "judge_request_id", isSafeFileId);
      const current = await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .first();
      if (!current) throw Object.assign(new Error("Judge request was not found."), { status: 404, code: "NODE_LAB_JUDGE_REQUEST_NOT_FOUND" });
      if (!["failed", "cancelled"].includes(current.status)) {
        throw Object.assign(new Error("Only failed or cancelled requests can be retried."), { status: 409, code: "NODE_LAB_JUDGE_REQUEST_NOT_RETRYABLE" });
      }
      const judgeRequestId = validateIdentifier(
        req.body?.judge_request_id || generateId("node-lab-judge"),
        "judge_request_id",
        isSafeFileId,
      );
      const row = {
        ...current,
        id: undefined,
        judge_request_id: judgeRequestId,
        status: "queued",
        source_request_id: current.id,
        attempt_no: Number(current.attempt_no || 1) + 1,
        max_attempts: Math.max(Number(current.max_attempts || 1), Number(current.attempt_no || 1) + 1),
        retry_reason: String(req.body?.retry_reason || "").trim() || null,
        lease_owner: null,
        lease_until: null,
        heartbeat_at: null,
        started_at: null,
        finished_at: null,
        error_json: null,
        user_created: requestUserId(req),
        user_updated: requestUserId(req),
        date_created: nowIso(),
        date_updated: nowIso(),
      };
      delete row.id;
      await database("eval_node_lab_judge_requests").insert(row);
      const stored = await database("eval_node_lab_judge_requests").where({ judge_request_id: judgeRequestId }).first();
      res.status(201).json({ data: judgeRequestSummary(stored) });
    } catch (error) {
      if (error?.status) {
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code } }],
        });
      } else {
        next(error);
      }
    }
  });
}
