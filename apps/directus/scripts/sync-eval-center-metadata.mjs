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
  ["warnings_json", jsonMeta(49, "Warnings")],
  ["runtime_summary_json", jsonMeta(50, "Runtime summary")],
  ["trace_refs_json", jsonMeta(51, "Trace refs")],
  ["error_json", jsonMeta(52, "Error")],
];

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
  for (const [field, meta] of FIELD_METADATA) {
    await request(token, "PATCH", `/fields/eval_node_probe_runs/${field}`, {
      meta,
    });
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
