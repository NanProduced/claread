import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";

const VALID_NODES = ["grammar", "vocabulary", "translation"];
const VALID_WORKSPACES = ["single_run", "baseline_compare"];
const VALID_SESSION_STATUSES = ["drafting", "active", "paused", "reviewed", "archived"];
const VALID_TRIAL_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"];
const VALID_JUDGE_REQUEST_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"];
const VALID_JUDGE_MODES = [
  "rubric_score_only",
  "rubric_plus_pairwise",
  "anti_template_probe",
  "raw",
];

const JUDGE_MODE_ALIASES = {
  rubric_only: "rubric_score_only",
  rubric_score_only: "rubric_score_only",
  rubric_plus_pairwise: "rubric_plus_pairwise",
  anti_template_probe: "anti_template_probe",
  raw: "raw",
};

function normalizeIncomingJudgeMode(mode) {
  const normalized = JUDGE_MODE_ALIASES[String(mode || "").trim()];
  if (!normalized) {
    throw validationError(`judge_mode must be one of: ${VALID_JUDGE_MODES.join(", ")}.`, "judge_mode");
  }
  return normalized;
}

function judgeModeFromMethod(method) {
  if (method === "anti_template_probe") return "anti_template_probe";
  if (method === "raw") return "raw";
  if (method === "rubric_only") return "rubric_score_only";
  return "rubric_plus_pairwise";
}

function normalizeJudgeConfigSnapshot(configSnapshot, preset = null) {
  const normalized = stableJson(configSnapshot || {});
  const method = String(normalized.judge_method || preset?.method || "").trim();
  const strategy = String(normalized.judge_strategy || preset?.strategy || "").trim();
  const presetId = String(normalized.preset_id || preset?.preset_id || "").trim();
  return {
    ...normalized,
    preset_id: presetId || null,
    judge_method: method || null,
    judge_strategy: strategy || null,
    judge_mode: judgeModeFromMethod(method),
  };
}

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

function nodeLabSessionsRoot(resolveNodeLabArtifactsRoot, env) {
  return path.join(resolveNodeLabArtifactsRoot(env), "sessions");
}

function trialArtifactDir(resolveNodeLabArtifactsRoot, env, sessionId, trialId) {
  const sessionsRoot = nodeLabSessionsRoot(resolveNodeLabArtifactsRoot, env);
  const effectiveSessionId = sessionId || "_standalone";
  return path.join(sessionsRoot, effectiveSessionId, "trials", trialId);
}

function judgeArtifactDir(resolveNodeLabArtifactsRoot, env, sessionId, trialId, judgeRequestId) {
  return path.join(trialArtifactDir(resolveNodeLabArtifactsRoot, env, sessionId, trialId), "judge", judgeRequestId);
}

function nodeLabJudgeConfigRoot(resolveEvalsRoot, env) {
  return path.join(resolveEvalsRoot(env), "claread_eval", "node_lab_judge", "config");
}

function absoluteNodeLabArtifactPath(resolveNodeLabArtifactsRoot, env, artifactPath) {
  const relativePath = String(artifactPath || "")
    .replace(/^evals[\\/]node-lab[\\/]/, "")
    .replace(/^node-lab[\\/]/, "");
  return path.join(resolveNodeLabArtifactsRoot(env), relativePath);
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
  await writeFile(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

async function loadNodeLabJudgePresetCatalog(resolveEvalsRoot, env) {
  const presetPath = path.join(
    nodeLabJudgeConfigRoot(resolveEvalsRoot, env),
    "presets",
    "judge_presets_v1_zh.json",
  );
  const raw = await readJsonFile(presetPath);
  const presets = Array.isArray(raw?.presets) ? raw.presets : [];
  return presets.map((preset) => ({
    preset_id: preset.preset_id,
    title: preset.title,
    ui_label: preset.ui_label,
    node_name: preset.node_name,
    strategy: preset.strategy,
    method: preset.method,
    packet_policy: preset.packet_policy || {},
    rubric_bundle: preset.rubric_bundle || {},
    pairwise: preset.pairwise || null,
    output_mode: preset.output_mode || null,
    probe_appendix: preset.probe_appendix || null,
  }));
}

async function loadJudgeRequestArtifacts(resolveNodeLabArtifactsRoot, env, row) {
  if (!row?.artifact_path) return { result: null, artifacts: {} };
  const absoluteArtifactPath = absoluteNodeLabArtifactPath(resolveNodeLabArtifactsRoot, env, row.artifact_path);
  const judgeDir = path.dirname(absoluteArtifactPath);
  const artifactFiles = {
    result: path.join(judgeDir, "result.json"),
    judge_run: path.join(judgeDir, "judge-run.json"),
    judge_config: path.join(judgeDir, "judge-config.json"),
    rubric_packet: path.join(judgeDir, "rubric-packet.json"),
    pairwise_packet: path.join(judgeDir, "pairwise-packet.json"),
    probe_packet: path.join(judgeDir, "probe-packet.json"),
  };
  const artifacts = {};
  for (const [key, filePath] of Object.entries(artifactFiles)) {
    if (await fileExists(filePath)) {
      artifacts[key] = await readJsonFile(filePath);
    }
  }
  return {
    result: artifacts.result || null,
    artifacts,
  };
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
      const timeoutMs = resolveRequestTimeoutMs(env, reqBody);
      const timeoutError = new Error(`Node Lab request timed out after ${Math.round(timeoutMs / 1000)}s at the Directus proxy layer.`);
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
  const aggregate = normalizeRowJson(row, "aggregate_summary_json") || {};
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
    aggregate_summary_json: aggregate,
    judge_request_count: Number(row.judge_request_count || aggregate.judge_request_count || 0),
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
    session_title: row.session_title || null,
    node_name: row.node_name,
    workspace_type: row.workspace_type,
    source_kind: row.source_kind || (row.session_id ? "session" : "standalone"),
    status: row.status,
    execution_mode: row.execution_mode,
    input_text_hash: row.input_text_hash,
    input_excerpt: row.input_excerpt,
    display_excerpt: row.display_excerpt || row.input_excerpt || "",
    reading_goal: row.reading_goal,
    reading_variant: row.reading_variant,
    source_type: row.source_type,
    baseline_snapshot_hash: row.baseline_snapshot_hash || null,
    candidate_snapshot_hashes_json: normalizeRowJson(row, "candidate_snapshot_hashes_json") || [],
    judge_config_snapshot_hash: row.judge_config_snapshot_hash || null,
    judge_request_count: Number(row.judge_request_count || 0),
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

async function enrichSessionRows(database, rows) {
  if (!Array.isArray(rows) || rows.length === 0) return [];
  const sessionIds = [...new Set(rows.map((row) => row.session_id).filter(Boolean))];
  if (!sessionIds.length) return rows;

  const judgeCounts = await database("eval_node_lab_judge_requests")
    .whereIn("session_id", sessionIds)
    .select("session_id")
    .count({ judge_request_count: "*" })
    .groupBy("session_id");
  const judgeCountBySessionId = new Map();
  for (const item of judgeCounts) {
    judgeCountBySessionId.set(item.session_id, Number(item.judge_request_count || 0));
  }

  return rows.map((row) => {
    const aggregate = normalizeRowJson(row, "aggregate_summary_json") || {};
    return {
      ...row,
      judge_request_count: judgeCountBySessionId.get(row.session_id) || 0,
      aggregate_summary_json: JSON.stringify(stableJson({
        ...aggregate,
        judge_request_count: judgeCountBySessionId.get(row.session_id) || 0,
      })),
    };
  });
}

async function enrichTrialRows(database, rows) {
  if (!Array.isArray(rows) || rows.length === 0) return [];

  const sessionIds = [...new Set(rows.map((row) => row.session_id).filter(Boolean))];
  const trialIds = [...new Set(rows.map((row) => row.trial_id).filter(Boolean))];

  const sessionTitleById = new Map();
  const judgeCountByTrialId = new Map();

  if (sessionIds.length) {
    const sessions = await database("eval_node_lab_sessions")
      .whereIn("session_id", sessionIds)
      .select("session_id", "title");
    for (const session of sessions) {
      sessionTitleById.set(session.session_id, session.title || null);
    }
  }

  if (trialIds.length) {
    const counts = await database("eval_node_lab_judge_requests")
      .whereIn("trial_id", trialIds)
      .select("trial_id")
      .count({ judge_request_count: "*" })
      .groupBy("trial_id");
    for (const item of counts) {
      judgeCountByTrialId.set(item.trial_id, Number(item.judge_request_count || 0));
    }
  }

  return rows.map((row) => ({
    ...row,
    session_title: row.session_id ? sessionTitleById.get(row.session_id) || null : null,
    source_kind: row.session_id ? "session" : "standalone",
    judge_request_count: judgeCountByTrialId.get(row.trial_id) || 0,
    display_excerpt: row.input_excerpt || "",
  }));
}

function judgeConfigSummary(row) {
  const normalized = normalizeRowJson(row, "normalized_config_json") || {};
  return {
    judge_config_id: row.judge_config_id,
    node_name: row.node_name,
    label: row.label,
    description: row.description || "",
    judge_mode: row.judge_mode,
    preset_id: normalized.preset_id || null,
    judge_method: normalized.judge_method || null,
    judge_strategy: normalized.judge_strategy || null,
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

function sessionContextMatchError(field, expected, actual) {
  return {
    error: new Error(
      `当前实验上下文与 Session 不一致：${field} (session=${expected} vs request=${actual})。` +
      "请先在 Baseline Compare 中重新选择相同上下文，或新建一个 Session。"
    ),
    field,
  };
}

function assertTrialContextMatchesSession(sessionRow, requestPayload) {
  const request = requestPayload?.request || {};
  const mismatches = [];

  const sessionNode = String(sessionRow?.node_name || "").trim();
  const requestNode = String(request.node_name || "").trim();
  if (sessionNode && requestNode && sessionNode !== requestNode) {
    mismatches.push(sessionContextMatchError("node", sessionNode, requestNode));
  }

  const baselineSnapshotHash = String(sessionRow?.baseline_snapshot_hash || "").trim();
  const requestBaselineHash = String(
    requestPayload?.baseline_snapshot_hash
      || request?.baseline_snapshot_hash
      || request?.baseline_snapshot?.prompt_snapshot_hash
      || ""
  ).trim();
  if (baselineSnapshotHash && requestBaselineHash && baselineSnapshotHash !== requestBaselineHash) {
    mismatches.push(sessionContextMatchError("baseline_snapshot_hash", baselineSnapshotHash, requestBaselineHash));
  }

  const sessionBaseline = sessionRow?.baseline_snapshot_json || {};
  const sessionGoal = String(sessionBaseline.reading_goal || "").trim();
  const sessionVariant = String(sessionBaseline.reading_variant || "").trim();
  const requestGoal = String(request.reading_goal || "").trim();
  const requestVariant = String(request.reading_variant || "").trim();
  if (sessionGoal && requestGoal && sessionGoal !== requestGoal) {
    mismatches.push(sessionContextMatchError("reading_goal", sessionGoal, requestGoal));
  }
  if (sessionVariant && requestVariant && sessionVariant !== requestVariant) {
    mismatches.push(sessionContextMatchError("reading_variant", sessionVariant, requestVariant));
  }

  if (mismatches.length > 0) {
    const first = mismatches[0];
    const error = new Error(
      `当前实验上下文已变化：${first.field} 不匹配。` +
      "请创建新 Session 来记录这一轮新上下文下的 compare。"
    );
    error.status = 422;
    error.code = "NODE_LAB_SESSION_CONTEXT_MISMATCH";
    error.field = first.field;
    error.mismatches = mismatches.map((item) => item.field);
    throw error;
  }
}

async function ensureSessionForPersist(database, req, payload, isSafeFileId) {
  if (payload.workspace_type && payload.workspace_type !== "baseline_compare") {
    const error = new Error(
      `Session 仅接收 Baseline Compare 试验，不接受 ${payload.workspace_type}。` +
      "Single Run 不再写入 Session；若想保留单次结果，请使用未来 Run History 入口。"
    );
    error.status = 422;
    error.code = "NODE_LAB_SESSION_REJECTS_WORKSPACE";
    error.field = "workspace_type";
    throw error;
  }
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
    assertTrialContextMatchesSession(existing, payload);
    return existing;
  }
  const baselineSnapshot = payload.baseline_snapshot_json && Object.keys(payload.baseline_snapshot_json).length > 0
    ? payload.baseline_snapshot_json
    : { reading_goal: payload.request?.reading_goal, reading_variant: payload.request?.reading_variant };
  const sessionCandidateRegistry = Array.isArray(payload.session?.candidate_registry_json) && payload.session.candidate_registry_json.length > 0
    ? payload.session.candidate_registry_json
    : payload.request?.candidate_override
      ? [payload.request.candidate_override]
      : [];
  const created = await createSessionFromPayload(database, req, {
    ...(payload.session || {}),
    node_name: payload.request?.node_name,
    title: payload.session?.title || `Compare 实验记录本 · ${payload.request?.node_name || "node"} · ${baselineSnapshot.reading_goal || ""} · ${baselineSnapshot.reading_variant || ""}`.trim(),
    goal: payload.session?.goal || `Compare 实验记录本：${baselineSnapshot.reading_goal || ""} · ${baselineSnapshot.reading_variant || ""}`.trim(),
    baseline_snapshot_json: baselineSnapshot,
    baseline_snapshot_hash: payload.baseline_snapshot_hash || payload.request?.baseline_snapshot_hash || null,
    candidate_registry_json: sessionCandidateRegistry,
  }, isSafeFileId);
  return created;
}

async function writeSessionArtifact(resolveNodeLabArtifactsRoot, env, sessionRow) {
  const summary = sessionSummary(sessionRow);
  const filePath = path.join(nodeLabSessionsRoot(resolveNodeLabArtifactsRoot, env), sessionRow.session_id, "session.json");
  await writeJsonFile(filePath, summary);
}

async function persistTrial({
  database,
  req,
  env,
  resolveNodeLabArtifactsRoot,
  isSafeFileId,
  workspaceType,
  requestPayload,
  resultPayload,
  sessionRow,
}) {
  if (workspaceType !== "baseline_compare") {
    const error = new Error(
      `Session 不再接收 ${workspaceType || "未知类型"} 试验。` +
      "Session 是固定实验上下文的 compare 记录本，Single Run 请走未来 Run History 入口。"
    );
    error.status = 422;
    error.code = "NODE_LAB_SESSION_REJECTS_WORKSPACE";
    error.field = "workspace_type";
    throw error;
  }
  if (sessionRow) {
    assertTrialContextMatchesSession(sessionRow, requestPayload);
  }
  const sessionId = sessionRow?.session_id || null;
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
  const artifactDir = trialArtifactDir(resolveNodeLabArtifactsRoot, env, sessionId, trialId);
  const effectiveSessionId = sessionId || "_standalone";
  const artifactPath = `evals/node-lab/sessions/${effectiveSessionId}/trials/${trialId}/result.json`;
  const baselineSnapshotHash = resultPayload?.baseline?.prompt_identity?.prompt_snapshot_hash
    || resultPayload?.run?.prompt_identity?.prompt_snapshot_hash
    || requestPayload?.baseline_snapshot_hash
    || null;
  const candidateHashes = resultPayload?.candidate
    ? [resultPayload.candidate.prompt_identity?.prompt_snapshot_hash].filter(Boolean)
    : [resultPayload?.run?.prompt_identity?.prompt_snapshot_hash].filter(Boolean);
  const inputText = String(requestPayload.request?.text || "");
  const providedSourceTextHash = String(requestPayload.request?.source_text_hash || "").trim();
  const inputTextHash = providedSourceTextHash || hashText(inputText);
  const inputExcerpt = providedSourceTextHash
    ? (inputText ? inputText.slice(0, 280) : `[attached compare · hash=${providedSourceTextHash.slice(0, 16)}]`)
    : inputText.slice(0, 280);  const resultStatus = workspaceType === "baseline_compare"
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
    session_id: sessionId,
    node_name: requestPayload.request.node_name,
    workspace_type: workspaceType,
    status,
    execution_mode: "sync",
    input_text_hash: inputTextHash,
    input_excerpt: inputExcerpt,
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
    session_id: sessionId,
    workspace_type: workspaceType,
    request: requestPayload.request,
    summary: summaryPayload,
  });
  await writeJsonFile(path.join(artifactDir, "result.json"), resultPayload);
  await database.transaction(async (trx) => {
    await trx("eval_node_lab_trials").insert(row);
    if (sessionId) {
      await updateSessionAggregate(trx, sessionId);
    }
  });
  const storedTrial = await database("eval_node_lab_trials").where({ trial_id: trialId }).first();
  if (sessionId) {
    await writeSessionArtifact(
      resolveNodeLabArtifactsRoot,
      env,
      await database("eval_node_lab_sessions").where({ session_id: sessionId }).first(),
    );
  }
  return storedTrial;
}

async function createJudgeRequest(database, req, env, resolveNodeLabArtifactsRoot, resolveEvalsRoot, body, isSafeFileId) {
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
  if (String(trialRow.workspace_type || "") !== "baseline_compare") {
    const error = new Error("Judge Compare 只能基于已保存的 Baseline Compare trial 发起。");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "trial_id";
    throw error;
  }
  const presetCatalog = await loadNodeLabJudgePresetCatalog(resolveEvalsRoot, env);
  const presetId = String(configSnapshot.preset_id || "").trim();
  const preset = presetCatalog.find((item) => item.preset_id === presetId) || null;
  if (!preset) {
    const error = new Error("Judge preset was not found.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "preset_id";
    throw error;
  }
  if (preset.node_name !== trialRow.node_name) {
    const error = new Error("Judge preset is not compatible with this compare trial node.");
    error.status = 422;
    error.code = "VALIDATION_ERROR";
    error.field = "preset_id";
    throw error;
  }
  configSnapshot = normalizeJudgeConfigSnapshot(configSnapshot, preset);
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
  const effectiveSessionId = trialRow.session_id || "_standalone";
  const artifactPath = `evals/node-lab/sessions/${effectiveSessionId}/trials/${trialRow.trial_id}/judge/${judgeRequestId}/result.json`;
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
    path.join(
      judgeArtifactDir(resolveNodeLabArtifactsRoot, env, effectiveSessionId, trialRow.trial_id, judgeRequestId),
      "judge-config.json",
    ),
    configSnapshot,
  );
  return database("eval_node_lab_judge_requests").where({ judge_request_id: judgeRequestId }).first();
}

async function loadSessionDetail(database, resolveNodeLabArtifactsRoot, env, sessionId) {
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
  const enrichedTrials = await enrichTrialRows(database, trials);
  const judgeRequests = await database("eval_node_lab_judge_requests")
    .where({ session_id: sessionId })
    .orderBy("date_created", "desc");
  const sessionArtifactPath = path.join(nodeLabSessionsRoot(resolveNodeLabArtifactsRoot, env), sessionId, "session.json");
  return {
    session: sessionSummary(row),
    session_artifact: (await fileExists(sessionArtifactPath)) ? await readJsonFile(sessionArtifactPath) : null,
    trials: enrichedTrials.map(trialSummary),
    judge_requests: judgeRequests.map(judgeRequestSummary),
  };
}

async function deleteJudgeRequestArtifacts(resolveNodeLabArtifactsRoot, env, row) {
  if (!row?.artifact_path) return;
  const absoluteArtifactPath = absoluteNodeLabArtifactPath(resolveNodeLabArtifactsRoot, env, row.artifact_path);
  await removePathIfExists(path.dirname(absoluteArtifactPath));
}

async function deleteTrialArtifacts(resolveNodeLabArtifactsRoot, env, row) {
  if (!row?.artifact_path) return;
  const absoluteArtifactPath = absoluteNodeLabArtifactPath(resolveNodeLabArtifactsRoot, env, row.artifact_path);
  await removePathIfExists(path.dirname(absoluteArtifactPath));
}

async function deleteTrialCascade(database, resolveNodeLabArtifactsRoot, env, trialRow) {
  const judgeRows = await database("eval_node_lab_judge_requests")
    .where({ trial_id: trialRow.trial_id })
    .select("*");
  for (const judgeRow of judgeRows) {
    await deleteJudgeRequestArtifacts(resolveNodeLabArtifactsRoot, env, judgeRow);
  }
  if (judgeRows.length) {
    await database("eval_node_lab_judge_requests")
      .where({ trial_id: trialRow.trial_id })
      .del();
  }
  await deleteTrialArtifacts(resolveNodeLabArtifactsRoot, env, trialRow);
  await database("eval_node_lab_trials")
    .where({ trial_id: trialRow.trial_id })
    .del();
  if (trialRow.session_id) {
    await updateSessionAggregate(database, trialRow.session_id);
    const sessionRow = await database("eval_node_lab_sessions").where({ session_id: trialRow.session_id }).first();
    if (sessionRow) {
      await writeSessionArtifact(resolveNodeLabArtifactsRoot, env, sessionRow);
    }
  }
  return {
    trial_id: trialRow.trial_id,
    session_id: trialRow.session_id,
    deleted_judge_request_count: judgeRows.length,
  };
}

async function deleteSessionCascade(database, resolveNodeLabArtifactsRoot, env, sessionRow) {
  const trialRows = await database("eval_node_lab_trials")
    .where({ session_id: sessionRow.session_id })
    .select("*");
  const judgeRows = await database("eval_node_lab_judge_requests")
    .where({ session_id: sessionRow.session_id })
    .select("*");

  for (const judgeRow of judgeRows) {
    await deleteJudgeRequestArtifacts(resolveNodeLabArtifactsRoot, env, judgeRow);
  }
  for (const trialRow of trialRows) {
    await deleteTrialArtifacts(resolveNodeLabArtifactsRoot, env, trialRow);
  }

  if (judgeRows.length) {
    await database("eval_node_lab_judge_requests")
      .where({ session_id: sessionRow.session_id })
      .del();
  }
  if (trialRows.length) {
    await database("eval_node_lab_trials")
      .where({ session_id: sessionRow.session_id })
      .del();
  }
  await database("eval_node_lab_sessions")
    .where({ session_id: sessionRow.session_id })
    .del();
  await removePathIfExists(path.join(nodeLabSessionsRoot(resolveNodeLabArtifactsRoot, env), sessionRow.session_id));

  return {
    session_id: sessionRow.session_id,
    deleted_trial_count: trialRows.length,
    deleted_judge_request_count: judgeRows.length,
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
    resolveNodeLabArtifactsRoot,
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
      const enrichedRows = await enrichSessionRows(database, rows);
      res.json({ data: enrichedRows.map(sessionSummary) });
    } catch (error) {
      next(error);
    }
  });

  router.post("/node-lab/sessions", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      if (!req.body?.seed_compare_trial_id) {
        const error = new Error(
          "Node Lab 不支持空建 Session。请先在 Baseline Compare 跑出 compare，再使用“新建 Session 并加入”。"
        );
        error.status = 422;
        error.code = "NODE_LAB_SESSION_REQUIRES_COMPARE_SEED";
        error.field = "seed_compare_trial_id";
        throw error;
      }
      validateStatusValue((req.body || {}).status || "drafting", VALID_SESSION_STATUSES, "status");
      const row = await createSessionFromPayload(database, req, req.body || {}, isSafeFileId);
      await writeSessionArtifact(resolveNodeLabArtifactsRoot, env, row);
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

  router.delete("/node-lab/sessions/:sessionId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.sessionId, "session_id", isSafeFileId);
      const row = await database("eval_node_lab_sessions").where({ session_id: req.params.sessionId }).first();
      if (!row) throw Object.assign(new Error("Session was not found."), { status: 404, code: "NODE_LAB_SESSION_NOT_FOUND" });
      const deleted = await deleteSessionCascade(database, resolveNodeLabArtifactsRoot, env, row);
      res.json({ data: deleted });
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
      res.json({ data: await loadSessionDetail(database, resolveNodeLabArtifactsRoot, env, req.params.sessionId) });
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
      await writeSessionArtifact(resolveNodeLabArtifactsRoot, env, stored);
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
      if (body.persist_trial) {
        const error = new Error(
          "Single Run 不再写入 Session。Session 是固定实验上下文的 compare 记录本，" +
          "单次结果请保留在页面状态中，或在 Baseline Compare 中跑完后再选择加入 Session。"
        );
        error.status = 422;
        error.code = "NODE_LAB_SESSION_REJECTS_SINGLE_RUN";
        error.field = "persist_trial";
        res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
        return;
      }
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
      const body = { ...(req.body || {}), workspace_type: "baseline_compare" };
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
        if (body.persist_without_session) {
          persisted = await persistTrial({
            database,
            req,
            env,
            resolveNodeLabArtifactsRoot,
            isSafeFileId,
            workspaceType: "baseline_compare",
            requestPayload: body,
            resultPayload: payload,
            sessionRow: null,
          });
        } else {
          session = await ensureSessionForPersist(database, req, body, isSafeFileId);
          persisted = await persistTrial({
            database,
            req,
            env,
            resolveNodeLabArtifactsRoot,
            isSafeFileId,
            workspaceType: "baseline_compare",
            requestPayload: body,
            resultPayload: payload,
            sessionRow: session,
          });
        }
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

  router.post("/node-lab/compare/attach", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const body = req.body || {};
      const resultPayload = body.result;
      if (!resultPayload || !resultPayload.baseline || !resultPayload.candidate) {
        const error = new Error(
          "attach 请求必须提供完整的 compare result（包含 baseline / candidate）。"
        );
        error.status = 422;
        error.code = "VALIDATION_ERROR";
        error.field = "result";
        throw error;
      }
      const snapshot = resultPayload.request_snapshot;
      if (!snapshot || typeof snapshot !== "object") {
        const error = new Error(
          "compare result 缺少 request_snapshot，无法 attach。请重跑一次 compare 以获得完整快照。"
        );
        error.status = 422;
        error.code = "VALIDATION_ERROR";
        error.field = "result.request_snapshot";
        throw error;
      }
      const snapshotNode = String(snapshot.node_name || resultPayload.node_name || "").trim();
      const snapshotGoal = String(snapshot.reading_goal || "").trim();
      const snapshotVariant = String(snapshot.reading_variant || "").trim();
      const snapshotSourceHash = String(snapshot.source_text_hash || "").trim();
      if (!snapshotNode || !snapshotGoal || !snapshotVariant) {
        const error = new Error(
          "result.request_snapshot 必须包含 node_name / reading_goal / reading_variant。"
        );
        error.status = 422;
        error.code = "VALIDATION_ERROR";
        error.field = "result.request_snapshot";
        throw error;
      }
      if (body.request && body.request.node_name && String(body.request.node_name).trim() !== snapshotNode) {
        const error = new Error(
          `body.request.node_name (${body.request.node_name}) 与 result.request_snapshot.node_name (${snapshotNode}) 不一致。`
        );
        error.status = 422;
        error.code = "VALIDATION_ERROR";
        error.field = "request.node_name";
        throw error;
      }
      if (body.request && body.request.reading_goal && String(body.request.reading_goal).trim() !== snapshotGoal) {
        const error = new Error(
          `body.request.reading_goal (${body.request.reading_goal}) 与 result.request_snapshot.reading_goal (${snapshotGoal}) 不一致。`
        );
        error.status = 422;
        error.code = "VALIDATION_ERROR";
        error.field = "request.reading_goal";
        throw error;
      }
      if (body.request && body.request.reading_variant && String(body.request.reading_variant).trim() !== snapshotVariant) {
        const error = new Error(
          `body.request.reading_variant (${body.request.reading_variant}) 与 result.request_snapshot.reading_variant (${snapshotVariant}) 不一致。`
        );
        error.status = 422;
        error.code = "VALIDATION_ERROR";
        error.field = "request.reading_variant";
        throw error;
      }
      const canonicalRequest = {
        node_name: snapshotNode,
        reading_goal: snapshotGoal,
        reading_variant: snapshotVariant,
        source_type: snapshot.source_type || "user_input",
        source_text_hash: snapshotSourceHash,
        request_id: snapshot.request_id || null,
        char_count: snapshot.source_char_count || null,
      };
      const canonicalBody = {
        workspace_type: "baseline_compare",
        request: canonicalRequest,
        result: resultPayload,
        session_id: body.session_id || undefined,
        session: body.session || undefined,
        baseline_snapshot_hash: resultPayload.baseline?.prompt_identity?.prompt_snapshot_hash || null,
        baseline_snapshot_json: {
          reading_goal: snapshotGoal,
          reading_variant: snapshotVariant,
          node_name: snapshotNode,
          prompt_profile: resultPayload.baseline?.prompt_identity?.prompt_variant_id || null,
          source_text_hash: snapshotSourceHash,
        },
      };
      let persisted = null;
      let session = null;
      if (body.persist_without_session) {
        persisted = await persistTrial({
          database,
          req,
          env,
          resolveNodeLabArtifactsRoot,
          isSafeFileId,
          workspaceType: "baseline_compare",
          requestPayload: canonicalBody,
          resultPayload,
          sessionRow: null,
        });
      } else {
        session = await ensureSessionForPersist(database, req, canonicalBody, isSafeFileId);
        persisted = await persistTrial({
          database,
          req,
          env,
          resolveNodeLabArtifactsRoot,
          isSafeFileId,
          workspaceType: "baseline_compare",
          requestPayload: canonicalBody,
          resultPayload,
          sessionRow: session,
        });
      }
      res.status(201).json({
        data: {
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
      if (req.query?.workspace_type) builder.where({ workspace_type: String(req.query.workspace_type) });
      if (req.query?.reading_goal) builder.where({ reading_goal: String(req.query.reading_goal) });
      if (req.query?.reading_variant) builder.where({ reading_variant: String(req.query.reading_variant) });
      const rows = await builder.limit(clampLimit(req.query?.limit));
      const enrichedRows = await enrichTrialRows(database, rows);
      res.json({ data: enrichedRows.map(trialSummary) });
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
      const [enrichedRow] = await enrichTrialRows(database, [row]);
      const absoluteArtifactPath = row.artifact_path
        ? absoluteNodeLabArtifactPath(resolveNodeLabArtifactsRoot, env, row.artifact_path)
        : null;
      const result = absoluteArtifactPath && await fileExists(absoluteArtifactPath)
        ? await readJsonFile(absoluteArtifactPath)
        : null;
      res.json({ data: { trial: trialSummary(enrichedRow), result } });
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

  router.delete("/node-lab/trials/:trialId", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.trialId, "trial_id", isSafeFileId);
      const row = await database("eval_node_lab_trials").where({ trial_id: req.params.trialId }).first();
      if (!row) throw Object.assign(new Error("Trial was not found."), { status: 404, code: "NODE_LAB_TRIAL_NOT_FOUND" });
      const deleted = await deleteTrialCascade(database, resolveNodeLabArtifactsRoot, env, row);
      res.json({ data: deleted });
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
      const mode = normalizeIncomingJudgeMode(body.judge_mode || "rubric_plus_pairwise");
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
      const normalizedConfig = normalizeJudgeConfigSnapshot(body.normalized_config_json || {
        judge_mode: mode,
        judge_method: body.judge_method || null,
        judge_strategy: body.judge_strategy || null,
        preset_id: body.preset_id || null,
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
      const mode = body.judge_mode ? normalizeIncomingJudgeMode(body.judge_mode) : undefined;
      const judgerModels = Array.isArray(body.judger_models_json) ? body.judger_models_json : [];
      if (judgerModels.length > 3) {
        throw validationError("judger_models_json cannot contain more than 3 judgers.", "judger_models_json");
      }
      const normalizedConfig = normalizeJudgeConfigSnapshot(body.normalized_config_json || {
        judge_mode: mode,
        judge_method: body.judge_method || null,
        judge_strategy: body.judge_strategy || null,
        preset_id: body.preset_id || null,
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
          judge_mode: mode,
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

  router.get("/node-lab/judge-presets", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const nodeName = req.query?.node_name ? String(req.query.node_name) : "";
      if (nodeName) validateNodeName(nodeName);
      const presets = await loadNodeLabJudgePresetCatalog(resolveEvalsRoot, env);
      res.json({
        data: nodeName
          ? presets.filter((preset) => preset.node_name === nodeName)
          : presets,
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

  router.get("/node-lab/judge-requests", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      const builder = database("eval_node_lab_judge_requests").orderBy("date_created", "desc");
      if (req.query?.node_name) builder.where({ node_name: String(req.query.node_name) });
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
      const row = await createJudgeRequest(
        database,
        req,
        env,
        resolveNodeLabArtifactsRoot,
        resolveEvalsRoot,
        req.body || {},
        isSafeFileId,
      );
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
      const detail = await loadJudgeRequestArtifacts(resolveNodeLabArtifactsRoot, env, row);
      res.json({ data: { request: judgeRequestSummary(row), result: detail.result, artifacts: detail.artifacts } });
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

  router.post("/node-lab/judge-requests/:requestId/execute", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;
    try {
      validateIdentifier(req.params.requestId, "judge_request_id", isSafeFileId);
      const current = await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .first();
      if (!current) throw Object.assign(new Error("Judge request was not found."), { status: 404, code: "NODE_LAB_JUDGE_REQUEST_NOT_FOUND" });
      if (!["queued"].includes(current.status)) {
        throw Object.assign(new Error("Only queued requests can be executed."), { status: 409, code: "NODE_LAB_JUDGE_REQUEST_NOT_EXECUTABLE" });
      }
      const configSnapshot = normalizeRowJson(current, "judge_config_snapshot_json") || {};
      const trialRow = await database("eval_node_lab_trials")
        .where({ trial_id: current.trial_id })
        .first();
      if (!trialRow) throw Object.assign(new Error("Associated trial was not found."), { status: 404, code: "NODE_LAB_TRIAL_NOT_FOUND" });
      const trialArtifactPath = trialRow.artifact_path
        ? absoluteNodeLabArtifactPath(resolveNodeLabArtifactsRoot, env, trialRow.artifact_path)
        : null;
      const compareResult = trialArtifactPath && await fileExists(trialArtifactPath)
        ? await readJsonFile(trialArtifactPath)
        : null;
      if (!compareResult) throw Object.assign(new Error("Compare result artifact not found."), { status: 404, code: "NODE_LAB_COMPARE_ARTIFACT_NOT_FOUND" });

      await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .update({
          status: "running",
          started_at: nowIso(),
          user_updated: requestUserId(req),
          date_updated: nowIso(),
        });

      const judgeRequestBody = {
        node_name: current.node_name,
        trial_id: current.trial_id,
        session_id: current.session_id || null,
        judge_request_id: current.judge_request_id,
        judge_config_snapshot: configSnapshot,
        compare_result: compareResult,
        participants: normalizeRowJson(current, "participants_json") || {},
        timeout_seconds: Number(configSnapshot?.parameters_json?.timeout_seconds) > 0
          ? Number(configSnapshot.parameters_json.timeout_seconds)
          : 300,
      };

      let judgeResult;
      try {
        judgeResult = await callUpstreamJson({
          env,
          readEnv,
          joinUrl,
          parseUpstreamError,
          resolveRequestTimeoutMs,
          reqBody: judgeRequestBody,
          upstreamPath: "/eval/article-analysis/node-lab/judge-run",
        });
      } catch (upstreamError) {
        await database("eval_node_lab_judge_requests")
          .where({ judge_request_id: req.params.requestId })
          .update({
            status: "failed",
            finished_at: nowIso(),
            error_json: JSON.stringify(sanitizeError({
              code: upstreamError.code || "UPSTREAM_JUDGE_ERROR",
              message: upstreamError.message,
            })),
            user_updated: requestUserId(req),
            date_updated: nowIso(),
          });
        const failedRow = await database("eval_node_lab_judge_requests")
          .where({ judge_request_id: req.params.requestId })
          .first();
        const failedDetail = await loadJudgeRequestArtifacts(resolveNodeLabArtifactsRoot, env, failedRow);
        res.json({ data: { request: judgeRequestSummary(failedRow), result: failedDetail.result, artifacts: failedDetail.artifacts } });
        return;
      }

      const effectiveSessionId = current.session_id || "_standalone";
      const judgeDir = judgeArtifactDir(resolveNodeLabArtifactsRoot, env, effectiveSessionId, current.trial_id, req.params.requestId);
      await writeJsonFile(path.join(judgeDir, "result.json"), judgeResult);

      const stepRuns = judgeResult?.step_runs && typeof judgeResult.step_runs === "object"
        ? Object.values(judgeResult.step_runs)
        : [];
      const hasFailedStep = stepRuns.some((step) => step && step.status === "failed");
      const nextStatus = hasFailedStep ? "failed" : "succeeded";
      const errorJson = hasFailedStep
        ? JSON.stringify(sanitizeError({
          code: "NODE_LAB_JUDGE_PARTIAL_FAILURE",
          message: judgeResult?.pairwise_error?.message || "Judge partially failed. See result.step_runs for details.",
        }))
        : null;

      await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .update({
          status: nextStatus,
          finished_at: nowIso(),
          error_json: errorJson,
          user_updated: requestUserId(req),
          date_updated: nowIso(),
        });

      const updatedRow = await database("eval_node_lab_judge_requests")
        .where({ judge_request_id: req.params.requestId })
        .first();
      const detail = await loadJudgeRequestArtifacts(resolveNodeLabArtifactsRoot, env, updatedRow);
      res.json({ data: { request: judgeRequestSummary(updatedRow), result: detail.result, artifacts: detail.artifacts } });
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
