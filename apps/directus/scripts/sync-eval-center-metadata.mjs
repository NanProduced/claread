import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DIRECTUS_URL = process.env.DIRECTUS_URL ?? "http://127.0.0.1:8055";
const DIRECTUS_CONTAINER = process.env.DIRECTUS_CONTAINER ?? "claread-directus";
const POSTGRES_CONTAINER = process.env.DIRECTUS_POSTGRES_CONTAINER ?? "claread-postgres";
const POSTGRES_DB = process.env.DIRECTUS_POSTGRES_DB ?? "claread";
const POSTGRES_USER = process.env.DIRECTUS_POSTGRES_USER ?? "claread";
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const EVAL_CONTROL_MIGRATION = resolve(
  SCRIPT_DIR,
  "../../../infra/migrations/0003_eval_control_tables.sql",
);

const DIRECTUS_EMAIL = process.env.DIRECTUS_EMAIL ?? process.env.ADMIN_EMAIL ?? "admin@claread.dev";
const DIRECTUS_PASSWORD = process.env.DIRECTUS_PASSWORD ?? process.env.ADMIN_PASSWORD ?? "";
const DIRECTUS_TOKEN = process.env.DIRECTUS_TOKEN ?? process.env.ADMIN_TOKEN ?? "";

const MODULE_BAR_ITEMS = [
  { type: "module", id: "claread-eval-center", enabled: true },
];
const DEPRECATED_MODULE_IDS = new Set(["claread-grammar-prompt-lab"]);

const COLLECTIONS = [
  {
    collection: "eval_node_probe_runs",
    icon: "science",
    color: "#2563EB",
    note: "Eval Center Node Probe 手动保存记录。正式 workflow eval artifact 仍保存在 evals/runs。",
    display_template: "{{ node_name }} {{ reading_variant }} {{ prompt_mode }}",
    sort_field: "date_created",
    sort: 30,
  },
  {
    collection: "eval_prompt_variant_drafts",
    icon: "edit_note",
    color: "#7C3AED",
    note: "Eval-only prompt variant drafts. 不直接修改业务 prompt YAML。",
    display_template: "{{ variant_id }} {{ status }}",
    sort_field: "date_updated",
    sort: 31,
  },
  {
    collection: "eval_workflow_run_requests",
    icon: "pending_actions",
    color: "#0F766E",
    note: "Runner bridge 请求队列。Directus 只创建请求，执行由外部 worker 完成。",
    display_template: "{{ run_id }} {{ status }}",
    sort_field: "date_created",
    sort: 32,
  },
  {
    collection: "eval_judge_run_requests",
    icon: "fact_check",
    color: "#4338CA",
    note: "LLM-as-a-Judge request queue. Directus 只创建/查看请求，执行由 evals judge worker 完成。",
    display_template: "{{ run_id }} {{ judge_run_id }} {{ status }}",
    sort_field: "date_created",
    sort: 33,
  },
  {
    collection: "eval_review_notes",
    icon: "rate_review",
    color: "#B45309",
    note: "Eval Center human review notes linked to workflow runs, node probe runs, cases, A/B reports, or prompt variants. Artifacts remain immutable.",
    display_template: "{{ target_type }} {{ target_id }} {{ verdict }}",
    sort_field: "date_created",
    sort: 34,
  },
];

const FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 10,
      options: {
        choices: [
          { text: "Succeeded", value: "succeeded" },
          { text: "Failed", value: "failed" },
          { text: "Timeout", value: "timeout" },
        ],
      },
    },
  ],
  [
    "node_name",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 11,
      options: {
        choices: [
          { text: "Grammar", value: "grammar" },
          { text: "Vocabulary", value: "vocabulary" },
          { text: "Translation", value: "translation" },
        ],
      },
    },
  ],
  ["dry_run", { interface: "boolean", width: "half", sort: 12 }],
  ["reading_goal", { interface: "input", width: "half", sort: 13 }],
  ["reading_variant", { interface: "input", width: "half", sort: 14 }],
  ["source_type", { interface: "input", width: "half", sort: 15 }],
  ["input_text_hash", { interface: "input", readonly: true, width: "half", sort: 16 }],
  ["input_excerpt", { interface: "input-multiline", width: "full", sort: 17 }],
  ["input_text", { interface: "input-rich-text-md", width: "full", sort: 18 }],
  [
    "prompt_mode",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 20,
      options: {
        choices: [
          { text: "Baseline", value: "baseline" },
          { text: "No Few-shot", value: "no_few_shot" },
          { text: "Variant", value: "variant" },
        ],
      },
    },
  ],
  ["prompt_variant_id", { interface: "input", width: "half", sort: 21 }],
  ["prompt_preview", { interface: "input-rich-text-md", width: "full", sort: 22 }],
  ["agent_instructions", { interface: "input-rich-text-md", width: "full", sort: 23 }],
  [
    "human_verdict",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 30,
      options: {
        allowNone: true,
        choices: [
          { text: "Good", value: "good" },
          { text: "Bad", value: "bad" },
          { text: "Mixed", value: "mixed" },
          { text: "Needs Review", value: "needs_review" },
        ],
      },
    },
  ],
  ["human_notes", { interface: "input-rich-text-md", width: "full", sort: 31 }],
  ["promote_candidate", { interface: "boolean", width: "half", sort: 32 }],
  ["tags", { interface: "tags", width: "half", sort: 33 }],
  ["prompt_identity_json", jsonMeta(40, "Prompt identity")],
  ["model_profile", { interface: "input", width: "half", sort: 41 }],
  ["model_identity_json", jsonMeta(42, "Model identity")],
  ["workflow_identity_json", jsonMeta(43, "Workflow identity")],
  ["schema_identity_json", jsonMeta(44, "Schema identity")],
  ["prepared_sentences_json", jsonMeta(45, "Prepared sentences")],
  ["example_summary_json", jsonMeta(46, "Example summary")],
  ["preprocess_summary_json", jsonMeta(47, "Preprocess summary")],
  ["node_output_json", jsonMeta(48, "Node output")],
  ["rag_debug_json", jsonMeta(49, "Sanitized RAG/debug evidence")],
  ["warnings_json", jsonMeta(50, "Warnings")],
  ["runtime_summary_json", jsonMeta(51, "Runtime summary")],
  ["trace_refs_json", jsonMeta(52, "Trace refs")],
  ["error_json", jsonMeta(53, "Error")],
];

const PROMPT_VARIANT_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3 }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["variant_id", { interface: "input", width: "half", sort: 10 }],
  ["target", { interface: "input", width: "half", sort: 11, readonly: true }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 12,
      options: {
        choices: [
          { text: "Draft", value: "draft" },
          { text: "Ready for Eval", value: "ready_for_eval" },
          { text: "Archived", value: "archived" },
        ],
      },
    },
  ],
  [
    "scope",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 13,
      options: {
        choices: [
          { text: "Workflow Eval", value: "workflow_eval" },
          { text: "Node Probe", value: "node_probe" },
        ],
      },
    },
  ],
  [
    "few_shot_mode",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 14,
      options: {
        choices: [
          { text: "Off", value: "off" },
          { text: "Baseline", value: "baseline" },
          { text: "Variant", value: "variant" },
          { text: "Settings", value: "settings" },
        ],
      },
    },
  ],
  ["snapshot_hash", { interface: "input", readonly: true, width: "half", sort: 15 }],
  ["notes", { interface: "input-rich-text-md", width: "full", sort: 16 }],
  ["policies_json", jsonMeta(20, "Policy overrides")],
  ["examples_json", jsonMeta(21, "Variant examples")],
  ["manifest_json", jsonMeta(22, "Normalized manifest snapshot")],
];

const WORKFLOW_RUN_REQUEST_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3 }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["run_id", { interface: "input", width: "half", sort: 10 }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 11,
      options: {
        choices: [
          { text: "Queued", value: "queued" },
          { text: "Running", value: "running" },
          { text: "Succeeded", value: "succeeded" },
          { text: "Failed", value: "failed" },
          { text: "Cancelled", value: "cancelled" },
        ],
      },
    },
  ],
  ["dataset_id", { interface: "input", width: "half", sort: 12 }],
  ["eval_purpose", { interface: "input", width: "half", sort: 13 }],
  ["adapter_kind", { interface: "input", width: "half", sort: 14 }],
  ["runner_kind", { interface: "input", width: "half", sort: 15, readonly: true }],
  ["prompt_variant_id", { interface: "input", width: "half", sort: 16 }],
  ["prompt_variant_snapshot_hash", { interface: "input", width: "half", sort: 17 }],
  ["artifact_run_id", { interface: "input", width: "half", sort: 18 }],
  ["artifact_path", { interface: "input", width: "half", sort: 19 }],
  ["source_request_id", { interface: "input", width: "half", sort: 20, readonly: true }],
  ["attempt_no", { interface: "input", width: "half", sort: 21, readonly: true }],
  ["max_attempts", { interface: "input", width: "half", sort: 22, readonly: true }],
  ["retry_reason", { interface: "input-multiline", width: "full", sort: 23 }],
  ["max_concurrency", { interface: "input", width: "half", sort: 24 }],
  ["lease_owner", { interface: "input", width: "half", sort: 25 }],
  ["lease_until", { interface: "datetime", width: "half", sort: 26 }],
  ["heartbeat_at", { interface: "datetime", width: "half", sort: 27 }],
  ["started_at", { interface: "datetime", width: "half", sort: 28 }],
  ["finished_at", { interface: "datetime", width: "half", sort: 29 }],
  ["notes", { interface: "input-rich-text-md", width: "full", sort: 30 }],
  ["tags", { interface: "tags", width: "half", sort: 31 }],
  ["config_json", jsonMeta(40, "Runner config snapshot")],
  ["error_json", jsonMeta(41, "Runner error summary")],
];

const JUDGE_RUN_REQUEST_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3 }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["judge_run_id", { interface: "input", width: "half", sort: 10 }],
  ["run_id", { interface: "input", width: "half", sort: 11 }],
  ["rubric_id", { interface: "input", width: "half", sort: 12 }],
  ["rubric_version", { interface: "input", width: "half", sort: 13, readonly: true }],
  [
    "status",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 14,
      options: {
        choices: [
          { text: "Queued", value: "queued" },
          { text: "Running", value: "running" },
          { text: "Succeeded", value: "succeeded" },
          { text: "Failed", value: "failed" },
          { text: "Cancelled", value: "cancelled" },
        ],
      },
    },
  ],
  [
    "judge_adapter_kind",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 15,
      options: {
        choices: [
          { text: "Fake", value: "fake" },
          { text: "LLM", value: "llm" },
        ],
      },
    },
  ],
  ["artifact_path", { interface: "input", width: "half", sort: 16, readonly: true }],
  ["source_request_id", { interface: "input", width: "half", sort: 17, readonly: true }],
  ["attempt_no", { interface: "input", width: "half", sort: 18, readonly: true }],
  ["max_attempts", { interface: "input", width: "half", sort: 19, readonly: true }],
  ["retry_reason", { interface: "input-multiline", width: "full", sort: 20 }],
  ["lease_owner", { interface: "input", width: "half", sort: 21 }],
  ["lease_until", { interface: "datetime", width: "half", sort: 22 }],
  ["heartbeat_at", { interface: "datetime", width: "half", sort: 23 }],
  ["started_at", { interface: "datetime", width: "half", sort: 24 }],
  ["finished_at", { interface: "datetime", width: "half", sort: 25 }],
  ["notes", { interface: "input-rich-text-md", width: "full", sort: 30 }],
  ["tags", { interface: "tags", width: "half", sort: 31 }],
  ["config_json", jsonMeta(40, "Judge config snapshot")],
  ["error_json", jsonMeta(41, "Judge worker error summary")],
];

const REVIEW_NOTES_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  [
    "target_type",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 10,
      options: {
        choices: [
          { text: "Workflow Run", value: "workflow_run" },
          { text: "Node Probe Run", value: "node_probe_run" },
          { text: "Case Artifact", value: "case_artifact" },
          { text: "A/B Report", value: "ab_report" },
          { text: "Prompt Variant", value: "prompt_variant" },
        ],
      },
    },
  ],
  ["target_id", { interface: "input", width: "half", sort: 11 }],
  ["run_id", { interface: "input", width: "half", sort: 12 }],
  ["case_id", { interface: "input", width: "half", sort: 13 }],
  ["ab_report_id", { interface: "input", width: "half", sort: 14 }],
  ["prompt_variant_id", { interface: "input", width: "half", sort: 15 }],
  [
    "verdict",
    {
      interface: "select-dropdown",
      width: "half",
      sort: 16,
      options: {
        allowNone: true,
        choices: [
          { text: "Good", value: "good" },
          { text: "Bad", value: "bad" },
          { text: "Mixed", value: "mixed" },
          { text: "Needs Review", value: "needs_review" },
          { text: "Win", value: "win" },
          { text: "Loss", value: "loss" },
          { text: "Tie", value: "tie" },
          { text: "Blocked", value: "blocked" },
        ],
      },
    },
  ],
  ["promote_candidate", { interface: "boolean", width: "half", sort: 17 }],
  ["tags", { interface: "tags", width: "half", sort: 18 }],
  ["note", { interface: "input-rich-text-md", width: "full", sort: 19 }],
];

const FIELD_METADATA_BY_COLLECTION = {
  eval_node_probe_runs: FIELD_METADATA,
  eval_prompt_variant_drafts: PROMPT_VARIANT_FIELD_METADATA,
  eval_workflow_run_requests: WORKFLOW_RUN_REQUEST_FIELD_METADATA,
  eval_judge_run_requests: JUDGE_RUN_REQUEST_FIELD_METADATA,
  eval_review_notes: REVIEW_NOTES_FIELD_METADATA,
};

function jsonMeta(sort, note) {
  return {
    interface: "input-code",
    options: { language: "json", template: "{}" },
    width: "full",
    sort,
    note,
  };
}

function sqlLiteral(value) {
  if (value == null) return "NULL";
  return `'${String(value).replace(/'/g, "''")}'`;
}

function runSql(sql) {
  execFileSync(
    "docker",
    [
      "exec",
      POSTGRES_CONTAINER,
      "psql",
      "-U",
      POSTGRES_USER,
      "-d",
      POSTGRES_DB,
      "-v",
      "ON_ERROR_STOP=1",
      "-c",
      sql,
    ],
    { stdio: "pipe" },
  );
}

function readEvalControlMigrationSql() {
  return readFileSync(EVAL_CONTROL_MIGRATION, "utf8");
}

function restartDirectus() {
  execFileSync("docker", ["restart", DIRECTUS_CONTAINER], { stdio: "pipe" });
}

async function waitForDirectusReady() {
  for (let index = 0; index < 30; index += 1) {
    try {
      const response = await fetch(`${DIRECTUS_URL}/server/ping`);
      if (response.ok) return;
    } catch {
      // Directus is still restarting.
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error("Directus did not become ready after metadata sync restart.");
}

function buildCollectionMetadataSql() {
  return `
    INSERT INTO directus_collections (collection, accountability, collapse)
    VALUES ${COLLECTIONS.map((item) => `(${sqlLiteral(item.collection)}, 'all', 'open')`).join(", ")}
    ON CONFLICT (collection) DO NOTHING;
  `;
}

function joinUrl(baseUrl, path) {
  return `${String(baseUrl).replace(/\/+$/, "")}/${String(path).replace(/^\/+/, "")}`;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(joinUrl(DIRECTUS_URL, path), options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.errors?.[0]?.message || payload?.message || response.statusText;
    throw new Error(`${options.method || "GET"} ${path} failed: ${message}`);
  }
  return payload;
}

async function login() {
  if (DIRECTUS_TOKEN) return DIRECTUS_TOKEN;
  if (!DIRECTUS_PASSWORD) {
    throw new Error(
      "Directus metadata sync requires DIRECTUS_TOKEN/ADMIN_TOKEN or DIRECTUS_PASSWORD/ADMIN_PASSWORD.",
    );
  }
  const payload = await fetchJson("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: DIRECTUS_EMAIL,
      password: DIRECTUS_PASSWORD,
    }),
  });
  const token = payload?.data?.access_token;
  if (!token) throw new Error("Directus login did not return an access token.");
  return token;
}

async function request(token, method, path, body) {
  return fetchJson(path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: body == null ? undefined : JSON.stringify(body),
  });
}

function upsertModuleBarItems(current, additions) {
  const byId = new Map();
  for (const item of current) {
    if (item?.id && !DEPRECATED_MODULE_IDS.has(item.id)) byId.set(item.id, item);
  }
  for (const item of additions) {
    byId.set(item.id, { ...byId.get(item.id), ...item });
  }
  return Array.from(byId.values());
}

async function syncCollections(token) {
  for (const item of COLLECTIONS) {
    await request(token, "PATCH", `/collections/${item.collection}`, {
      meta: {
        icon: item.icon,
        color: item.color,
        note: item.note,
        display_template: item.display_template,
        sort_field: item.sort_field,
        sort: item.sort,
        hidden: false,
        singleton: false,
        accountability: "all",
        collapse: "open",
      },
    });
  }
}

async function syncFields(token) {
  for (const [collection, fields] of Object.entries(FIELD_METADATA_BY_COLLECTION)) {
    for (const [field, meta] of fields) {
      await request(token, "PATCH", `/fields/${collection}/${field}`, {
        meta,
      });
    }
  }
}

async function syncModuleBar(token) {
  const settings = await request(token, "GET", "/settings");
  const currentModuleBar = Array.isArray(settings?.data?.module_bar)
    ? settings.data.module_bar
    : [];
  const nextModuleBar = upsertModuleBarItems(currentModuleBar, MODULE_BAR_ITEMS);

  await request(token, "PATCH", "/settings", { module_bar: nextModuleBar });
}

runSql(readEvalControlMigrationSql());
runSql(buildCollectionMetadataSql());
restartDirectus();
await waitForDirectusReady();

const token = await login();
await syncCollections(token);
await syncFields(token);
await syncModuleBar(token);

console.log(
  `Eval Center metadata synced. Enabled modules: ${MODULE_BAR_ITEMS.map((item) => item.id).join(", ")}; collections: ${COLLECTIONS.map((item) => item.collection).join(", ")}`,
);
