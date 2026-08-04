import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DIRECTUS_ENV_FILE = resolve(SCRIPT_DIR, "../.env");

loadDotEnv(DIRECTUS_ENV_FILE);

const DIRECTUS_URL = process.env.DIRECTUS_URL ?? "http://127.0.0.1:8055";
const DIRECTUS_CONTAINER = process.env.DIRECTUS_CONTAINER ?? "claread-directus";
const POSTGRES_CONTAINER = process.env.POSTGRES_CONTAINER ?? "claread-postgres";
const POSTGRES_DB = process.env.POSTGRES_DB ?? "claread";
const POSTGRES_USER = process.env.POSTGRES_USER ?? "claread";
const DIRECTUS_SKIP_RESTART = isTruthyEnv(process.env.DIRECTUS_SKIP_RESTART);

const DIRECTUS_EMAIL = process.env.DIRECTUS_EMAIL ?? process.env.ADMIN_EMAIL ?? "admin@claread.dev";
const DIRECTUS_PASSWORD = process.env.DIRECTUS_PASSWORD ?? process.env.ADMIN_PASSWORD ?? "";
const DIRECTUS_TOKEN = process.env.DIRECTUS_TOKEN ?? process.env.ADMIN_TOKEN ?? "";

const MODULE_BAR_ITEMS = [
  { type: "module", id: "claread-llm-config", enabled: true },
];

const DEPRECATED_MODULE_IDS = new Set();
const DEPRECATED_COLLECTION_IDS = [];
const DEPRECATED_FIELDS = [];
const LEGACY_COLLECTIONS = [];

const COLLECTIONS = [
  {
    collection: "llm_providers",
    icon: "dns",
    color: "#2563EB",
    note: "LLM provider definitions. Transport adapter and auth config.",
    display_template: "{{ slug }} {{ adapter }} {{ status }}",
    sort_field: "sort",
    sort: 41,
  },
  {
    collection: "llm_models",
    icon: "memory",
    color: "#7C3AED",
    note: "LLM model definitions. Remote model name under a provider.",
    display_template: "{{ slug }} {{ model_name }} {{ status }}",
    sort_field: "sort",
    sort: 42,
  },
  {
    collection: "llm_profiles",
    icon: "tune",
    color: "#059669",
    note: "LLM profile definitions. Binds a model to a business scenario with optional settings overrides.",
    display_template: "{{ slug }} {{ status }}",
    sort_field: "sort",
    sort: 43,
  },
  {
    collection: "llm_presets",
    icon: "playlist_add_check",
    color: "#D97706",
    note: "LLM preset definitions. Named set of route→profile mappings, optionally inheriting from a base preset.",
    display_template: "{{ slug }} {{ status }}",
    sort_field: "sort",
    sort: 44,
  },
  {
    collection: "llm_ask_options",
    icon: "smart_toy",
    color: "#DC2626",
    note: "Ask Claread model option definitions. User-selectable model tiers in the Ask panel.",
    display_template: "{{ slug }} {{ label }}",
    sort_field: "sort",
    sort: 45,
  },
  {
    collection: "llm_ask_config",
    icon: "settings",
    color: "#9333EA",
    note: "Ask Claread top-level configuration (singleton).",
    display_template: "Ask Config",
    sort_field: null,
    sort: 46,
    singleton: true,
  },
];

const PROVIDER_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["slug", { interface: "input", width: "half", sort: 10, required: true, options: { slug: true } }],
  ["adapter", {
    interface: "select-dropdown",
    width: "half",
    sort: 11,
    required: true,
    options: {
      choices: [
        { text: "OpenAI Compatible", value: "openai_compatible" },
        { text: "DashScope Native", value: "dashscope_native" },
        { text: "DashScope Embedding", value: "dashscope_embedding" },
        { text: "DashScope Rerank", value: "dashscope_rerank" },
      ],
    },
  }],
  ["base_url", { interface: "input", width: "full", sort: 12, options: { placeholder: "https://api.example.com/v1" } }],
  ["api_key_env", { interface: "input", width: "half", sort: 13, options: { placeholder: "DASHSCOPE_API_KEY" } }],
  ["provider_options", jsonMeta(14, "Provider-level extension options (dimension, transport, profile hint)")],
  ["openai_profile", jsonMeta(15, "OpenAI compatibility capability declaration")],
  ["model_settings", jsonMeta(16, "Provider-level default model settings")],
  ["note", { interface: "input", width: "full", sort: 17 }],
  ["sort", { interface: "input", width: "half", sort: 18, hidden: true }],
  ["status", {
    interface: "select-dropdown",
    width: "half",
    sort: 19,
    options: {
      choices: [
        { text: "Draft", value: "draft" },
        { text: "Active", value: "active" },
        { text: "Deprecated", value: "deprecated" },
      ],
    },
  }],
];

const MODEL_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["slug", { interface: "input", width: "half", sort: 10, required: true, options: { slug: true } }],
  ["provider", { interface: "select-dropdown-m2o", width: "half", sort: 11, required: true }],
  ["model_name", { interface: "input", width: "half", sort: 12, required: true }],
  ["model_settings", jsonMeta(13, "Model-level default settings (override provider)")],
  ["provider_options", jsonMeta(14, "Model-level extension options (override provider)")],
  ["openai_profile", jsonMeta(15, "Model-level OpenAI compatibility override")],
  ["note", { interface: "input", width: "full", sort: 16 }],
  ["sort", { interface: "input", width: "half", sort: 17, hidden: true }],
  ["status", {
    interface: "select-dropdown",
    width: "half",
    sort: 18,
    options: {
      choices: [
        { text: "Draft", value: "draft" },
        { text: "Active", value: "active" },
        { text: "Deprecated", value: "deprecated" },
      ],
    },
  }],
];

const PROFILE_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["slug", { interface: "input", width: "half", sort: 10, required: true, options: { slug: true } }],
  ["model", { interface: "select-dropdown-m2o", width: "half", sort: 11, required: true }],
  ["model_settings", jsonMeta(12, "Profile-level settings override (highest priority)")],
  ["note", { interface: "input", width: "full", sort: 13 }],
  ["sort", { interface: "input", width: "half", sort: 14, hidden: true }],
  ["status", {
    interface: "select-dropdown",
    width: "half",
    sort: 15,
    options: {
      choices: [
        { text: "Draft", value: "draft" },
        { text: "Active", value: "active" },
        { text: "Deprecated", value: "deprecated" },
      ],
    },
  }],
];

const PRESET_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["slug", { interface: "input", width: "half", sort: 10, required: true, options: { slug: true } }],
  ["base_preset", { interface: "select-dropdown-m2o", width: "half", sort: 11 }],
  ["default_profile", { interface: "select-dropdown-m2o", width: "half", sort: 12 }],
  ["routes", jsonMeta(13, "Route→selection mapping. Keys must match ModelRoute Literal.")],
  ["note", { interface: "input", width: "full", sort: 14 }],
  ["sort", { interface: "input", width: "half", sort: 15, hidden: true }],
  ["status", {
    interface: "select-dropdown",
    width: "half",
    sort: 16,
    options: {
      choices: [
        { text: "Draft", value: "draft" },
        { text: "Active", value: "active" },
        { text: "Deprecated", value: "deprecated" },
      ],
    },
  }],
];

const ASK_OPTION_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["slug", { interface: "input", width: "half", sort: 10, required: true, options: { slug: true } }],
  ["label", { interface: "input", width: "half", sort: 11, required: true }],
  ["description", { interface: "input-multiline", width: "full", sort: 12 }],
  ["selection", jsonMeta(13, "ModelSelection object (preset ref + routes). Null = use backend defaults.")],
  ["price_multiplier", { interface: "input", width: "half", sort: 14 }],
  ["runtime_budget", jsonMeta(15, "Per-option runtime budget overrides")],
  ["enabled", { interface: "boolean", width: "half", sort: 16 }],
  ["sort", { interface: "input", width: "half", sort: 17, hidden: true }],
];

const ASK_CONFIG_FIELD_METADATA = [
  ["id", { hidden: true, readonly: true, interface: "input", sort: 1 }],
  ["date_created", { readonly: true, interface: "datetime", width: "half", sort: 2 }],
  ["date_updated", { readonly: true, interface: "datetime", width: "half", sort: 3, hidden: true }],
  ["user_created", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 4, hidden: true }],
  ["user_updated", { readonly: true, interface: "select-dropdown-m2o", width: "half", sort: 5, hidden: true }],
  ["default_option", { interface: "input", width: "half", sort: 10, note: "Slug of the default ask option. If empty, first enabled option is used." }],
  ["billing_defaults", jsonMeta(11, "Billing defaults (reserved_points, tokens_per_point, billing_policy_version)")],
  ["runtime_defaults", jsonMeta(12, "Runtime defaults (max_input_tokens, max_output_tokens, prompt_buffer_tokens)")],
];

const FIELD_METADATA_BY_COLLECTION = {
  llm_providers: PROVIDER_FIELD_METADATA,
  llm_models: MODEL_FIELD_METADATA,
  llm_profiles: PROFILE_FIELD_METADATA,
  llm_presets: PRESET_FIELD_METADATA,
  llm_ask_options: ASK_OPTION_FIELD_METADATA,
  llm_ask_config: ASK_CONFIG_FIELD_METADATA,
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

function loadDotEnv(envFile) {
  try {
    const raw = readFileSync(envFile, "utf8").replace(/^\uFEFF/, "");
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eqIndex = trimmed.indexOf("=");
      if (eqIndex === -1) continue;
      const key = trimmed.slice(0, eqIndex).trim();
      if (!key || process.env[key] != null) continue;
      let value = trimmed.slice(eqIndex + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"'))
        || (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      process.env[key] = value;
    }
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

function isTruthyEnv(value) {
  return ["1", "true", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());
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

async function tryRequestWithToken(token, path = "/users/me") {
  const response = await fetch(joinUrl(DIRECTUS_URL, path), {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    },
  });
  return response.ok;
}

async function login() {
  if (DIRECTUS_TOKEN) {
    const tokenValid = await tryRequestWithToken(DIRECTUS_TOKEN);
    if (tokenValid) return DIRECTUS_TOKEN;
  }
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
        singleton: item.singleton ?? false,
        accountability: "all",
        collapse: "open",
      },
    });
  }

  for (const item of LEGACY_COLLECTIONS) {
    try {
      await request(token, "PATCH", `/collections/${item.collection}`, {
        meta: {
          note: item.note,
          hidden: true,
        },
      });
    } catch (error) {
      if (!String(error?.message || "").includes("404")) throw error;
    }
  }
}

async function syncFields(token) {
  for (const [collection, fields] of Object.entries(FIELD_METADATA_BY_COLLECTION)) {
    for (const [field, entry] of fields) {
      const { type: declaredType, schema: declaredSchema, ...meta } = entry;
      const body = { field, meta };
      if (meta.special?.includes("alias")) {
        body.type = "alias";
        body.schema = null;
      } else if (typeof declaredType === "string") {
        body.type = declaredType;
        body.schema = declaredSchema ?? { data_type: declaredType };
      }
      try {
        await request(token, "PATCH", `/fields/${collection}/${field}`, body);
      } catch (e) {
        if (String(e?.message || "").includes("404") || String(e?.message || "").includes("doesn't exist")) {
          await request(token, "POST", `/fields/${collection}`, body);
        } else {
          throw e;
        }
      }
    }
  }
}

async function cleanupDeprecatedMetadata(token) {
  if (DEPRECATED_COLLECTION_IDS.length) {
    for (const collection of DEPRECATED_COLLECTION_IDS) {
      try {
        await request(token, "DELETE", `/collections/${collection}`);
      } catch (error) {
        if (!String(error?.message || "").includes("404")) throw error;
      }
    }
  }
  for (const [collection, field] of DEPRECATED_FIELDS) {
    try {
      await request(token, "DELETE", `/fields/${collection}/${field}`);
    } catch (error) {
      if (!String(error?.message || "").includes("404")) throw error;
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

// DATA-SCHEMA-BASELINE D2: llm_* physical tables come from the single
// infra/migrations/0001_initial.sql baseline; this script is metadata-only
// (directus_collections rows for the LLM Config module).
runSql(buildCollectionMetadataSql());
if (!DIRECTUS_SKIP_RESTART) {
  restartDirectus();
  await waitForDirectusReady();
}

const token = await login();
await cleanupDeprecatedMetadata(token);
await syncCollections(token);
await syncFields(token);
await syncModuleBar(token);

console.log(
  `LLM Config metadata synced (metadata-only). Enabled modules: ${MODULE_BAR_ITEMS.map((item) => item.id).join(", ")}; collections: ${COLLECTIONS.map((item) => item.collection).join(", ")}; restart=${DIRECTUS_SKIP_RESTART ? "skipped" : "performed"}`,
);
