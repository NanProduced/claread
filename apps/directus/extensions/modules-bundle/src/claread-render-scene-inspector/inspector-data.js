function normalizeItem(item) {
  return item && typeof item === "object" ? item : null;
}

function normalizeArray(items) {
  return Array.isArray(items) ? items.filter((item) => item && typeof item === "object") : [];
}

function flattenQuery(prefix, value, target) {
  if (value == null) return;

  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      flattenQuery(`${prefix}[${index}]`, value[index], target);
    }
    return;
  }

  if (typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      flattenQuery(prefix ? `${prefix}[${key}]` : key, child, target);
    }
    return;
  }

  target.push([prefix, String(value)]);
}

function buildQueryString(query) {
  const entries = [];
  flattenQuery("", query, entries);
  const params = new URLSearchParams();

  for (const [key, value] of entries) {
    params.append(key, value);
  }

  return params.toString();
}

async function fetchJson(path) {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.status}`);
  }

  const payload = await response.json();
  return payload?.data ?? null;
}

async function fetchItem(collection, id, fields) {
  if (!id) return null;
  const query = buildQueryString({ fields: fields.join(",") });
  return normalizeItem(await fetchJson(`/items/${collection}/${encodeURIComponent(id)}?${query}`));
}

async function fetchCollection(collection, { fields, filter, sort, limit = 100 }) {
  const query = {
    fields: fields.join(","),
    limit,
  };

  if (filter && Object.keys(filter).length > 0) {
    query.filter = filter;
  }

  if (sort) {
    query.sort = Array.isArray(sort) ? sort.join(",") : sort;
  }

  return normalizeArray(await fetchJson(`/items/${collection}?${buildQueryString(query)}`));
}

function parseOverviewHint(pageStateJson) {
  if (!pageStateJson || typeof pageStateJson !== "object") return null;
  const derived = pageStateJson.derived;
  if (!derived || typeof derived !== "object") return null;
  const payload = derived.overview_hint;
  return payload && typeof payload === "object" ? payload : null;
}

function selectTask(tasks, snapshots, preferredTaskId) {
  if (preferredTaskId) {
    const matchedTask = tasks.find((item) => String(item.id) === String(preferredTaskId));
    if (matchedTask) return matchedTask;
  }

  const firstSnapshot = snapshots[0];
  if (firstSnapshot?.task_id) {
    const matchedTask = tasks.find((item) => String(item.id) === String(firstSnapshot.task_id));
    if (matchedTask) return matchedTask;
  }

  return tasks[0] ?? null;
}

function selectSnapshot(snapshots, selectedTask) {
  if (!selectedTask) return snapshots[0] ?? null;
  return snapshots.find((item) => String(item.task_id) === String(selectedTask.id)) ?? null;
}

export async function loadInspectorBundle({ recordId, resultId, taskId }) {
  const targetRecordId = recordId || resultId;
  if (!targetRecordId) {
    return {
      record: null,
      result: null,
      tasks: [],
      taskEvents: [],
      snapshots: [],
      snapshot: null,
      usageEvents: [],
      overviewTasks: [],
      overviewTaskEvents: [],
      overviewTask: null,
      overviewHint: null,
    };
  }

  const recordFields = [
    "id",
    "title",
    "client_record_id",
    "source_type",
    "source_text",
    "request_payload_json",
    "reading_goal",
    "reading_variant",
    "analysis_status",
    "user_facing_state",
    "last_opened_at",
    "created_at",
    "updated_at",
    "result.record_id",
    "result.workflow_version",
    "result.schema_version",
  ];
  const resultFields = [
    "record_id",
    "workflow_version",
    "schema_version",
    "created_at",
    "render_scene_json",
    "page_state_json",
  ];
  const taskFields = [
    "id",
    "analysis_record_id",
    "status",
    "failure_code",
    "failure_message",
    "queued_at",
    "started_at",
    "finished_at",
    "usage_summary_json",
    "quota_cost_points",
  ];
  const taskEventFields = ["id", "task_id", "event_type", "event_payload_json", "created_at"];
  const snapshotFields = [
    "id",
    "record_id",
    "task_id",
    "workflow_name",
    "workflow_version",
    "schema_version",
    "prompt_version",
    "task_status",
    "user_facing_state",
    "failure_code",
    "failure_message",
    "preprocess_summary_json",
    "normalize_summary_json",
    "drop_log_summary_json",
    "runtime_summary_json",
    "academic_quality_json",
    "rag_debug_json",
    "trace_refs_json",
    "created_at",
    "updated_at",
  ];
  const overviewTaskFields = [
    "id",
    "analysis_record_id",
    "status",
    "failure_code",
    "failure_message",
    "queued_at",
    "started_at",
    "finished_at",
    "usage_summary_json",
    "created_at",
    "updated_at",
  ];
  const usageFields = [
    "id",
    "record_id",
    "task_id",
    "capability_code",
    "status",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "billed_points",
    "latency_ms",
    "workflow_name",
    "workflow_version",
    "schema_version",
    "prompt_version",
    "model_provider",
    "model_name",
    "error_code",
    "error_message",
    "metadata_json",
    "created_at",
  ];

  const [record, result, tasks, snapshots, overviewTasks, usageEvents] = await Promise.all([
    fetchItem("analysis_records", targetRecordId, recordFields),
    fetchItem("analysis_results", targetRecordId, resultFields).catch(() => null),
    fetchCollection("analysis_tasks", {
      fields: taskFields,
      filter: { analysis_record_id: { _eq: targetRecordId } },
      sort: ["-queued_at"],
      limit: 20,
    }),
    fetchCollection("analysis_debug_snapshots", {
      fields: snapshotFields,
      filter: { record_id: { _eq: targetRecordId } },
      sort: ["-created_at"],
      limit: 20,
    }),
    fetchCollection("analysis_overview_tasks", {
      fields: overviewTaskFields,
      filter: { analysis_record_id: { _eq: targetRecordId } },
      sort: ["-queued_at"],
      limit: 20,
    }),
    fetchCollection("ai_usage_events", {
      fields: usageFields,
      filter: { record_id: { _eq: targetRecordId } },
      sort: ["-created_at"],
      limit: 50,
    }),
  ]);

  const selectedTask = selectTask(tasks, snapshots, taskId);
  const selectedSnapshot = selectSnapshot(snapshots, selectedTask);
  const selectedOverviewTask = overviewTasks[0] ?? null;
  const overviewHint = parseOverviewHint(result?.page_state_json);

  const [taskEvents, overviewTaskEvents] = await Promise.all([
    selectedTask
      ? fetchCollection("analysis_task_events", {
          fields: taskEventFields,
          filter: { task_id: { _eq: selectedTask.id } },
          sort: ["created_at"],
          limit: 100,
        })
      : Promise.resolve([]),
    selectedOverviewTask
      ? fetchCollection("analysis_overview_task_events", {
          fields: taskEventFields,
          filter: { task_id: { _eq: selectedOverviewTask.id } },
          sort: ["created_at"],
          limit: 100,
        })
      : Promise.resolve([]),
  ]);

  return {
    record,
    result,
    tasks,
    selectedTask,
    taskEvents,
    snapshots,
    snapshot: selectedSnapshot,
    usageEvents,
    overviewTasks,
    overviewTaskEvents,
    overviewTask: selectedOverviewTask,
    overviewHint,
  };
}
