export function dash(value, fallback = "-") {
  return value === null || value === undefined || value === "" ? fallback : value;
}

export function artifactTopologyMode(artifact) {
  return artifact?.workflow_identity?.topology_mode
    || artifact?.schema_identity?.topology_mode
    || null;
}

export function isLearningArtifact(artifact) {
  return artifactTopologyMode(artifact) === "learning";
}

export function groupCandidatesByStatus(candidates) {
  const items = Array.isArray(candidates) ? candidates : [];
  return {
    published: items.filter((candidate) => candidate?.status === "ready_for_eval"),
    drafts: items.filter((candidate) => candidate?.status !== "ready_for_eval"),
  };
}

export function normalizeWorkflowScene(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  if (
    payload.render_scene
    && typeof payload.render_scene === "object"
    && !Array.isArray(payload.render_scene)
  ) {
    return payload.render_scene;
  }
  if (
    Array.isArray(payload.translations)
    || Array.isArray(payload.inline_marks)
    || Array.isArray(payload.sentence_entries)
  ) {
    return payload;
  }
  if (
    payload.output
    && typeof payload.output === "object"
    && !Array.isArray(payload.output)
    && (
      Array.isArray(payload.output.translations)
      || Array.isArray(payload.output.inline_marks)
      || Array.isArray(payload.output.sentence_entries)
    )
  ) {
    return payload.output;
  }
  return null;
}

export function normalizeSingleRunPayload(payload) {
  const scene = normalizeWorkflowScene(payload);
  const status = payload?.status || (scene ? "succeeded" : "unknown");
  return {
    status,
    scene,
    promptIdentity: payload?.prompt_identity || null,
    modelIdentity: payload?.model_identity || null,
    runtimeSummary: payload?.runtime_summary || null,
    warnings: Array.isArray(payload?.warnings)
      ? payload.warnings
      : Array.isArray(scene?.warnings)
        ? scene.warnings
        : [],
    error: payload?.error || null,
    savedHistoryRunId: payload?.saved_history_run_id || null,
    raw: payload,
  };
}

export function sceneTranslations(scene) {
  return Array.isArray(scene?.translations) ? scene.translations : [];
}

export function sceneInlineMarks(scene) {
  return Array.isArray(scene?.inline_marks) ? scene.inline_marks : [];
}

export function sceneSentenceEntries(scene) {
  return Array.isArray(scene?.sentence_entries) ? scene.sentence_entries : [];
}

export function sceneWarnings(scene) {
  return Array.isArray(scene?.warnings) ? scene.warnings : [];
}

export function formatRunIdentity(run) {
  const variant = run?.prompt_variant_id || "baseline";
  const totalCases = run?.learning_case_count ?? run?.total_cases ?? 0;
  return `${variant} / ${totalCases} learning cases`;
}
