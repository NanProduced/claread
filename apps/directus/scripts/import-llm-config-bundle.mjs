#!/usr/bin/env node

/**
 * Import LLM Config Bundle into Directus
 *
 * Reads the 3 source-of-truth JSON files from services/api/config/ and
 * upserts them into the 6 Directus llm_* collections:
 *   - model-profiles.json  → llm_providers / llm_models / llm_profiles
 *   - model-presets.json   → llm_presets
 *   - reader-ask-model-options.json → llm_ask_options / llm_ask_config
 *
 * Usage:
 *   node import-llm-config-bundle.mjs [--input DIR] [--dry-run]
 *
 * Default input: services/api/config/
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { validateLlmConfigBundle, formatValidationIssues } from "./validate-llm-config-bundle.mjs";
import {
  importProviders,
  importModels,
  importProfiles,
  importPresets,
  importAskOptions,
  importAskConfig,
} from "./import-llm-config-core.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DIRECTUS_ENV_FILE = resolve(SCRIPT_DIR, "../.env");
const DEFAULT_INPUT_DIR = resolve(SCRIPT_DIR, "../../../services/api/config");

loadDotEnv(DIRECTUS_ENV_FILE);

const DIRECTUS_URL = process.env.DIRECTUS_URL ?? "http://127.0.0.1:8055";
const DIRECTUS_EMAIL = process.env.DIRECTUS_EMAIL ?? process.env.ADMIN_EMAIL ?? "admin@claread.dev";
const DIRECTUS_PASSWORD = process.env.DIRECTUS_PASSWORD ?? process.env.ADMIN_PASSWORD ?? "";
const DIRECTUS_TOKEN = process.env.DIRECTUS_TOKEN ?? process.env.ADMIN_TOKEN ?? "";

// Parse arguments
const args = process.argv.slice(2);
let inputDir = DEFAULT_INPUT_DIR;
let dryRun = false;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--input" && args[i + 1]) {
    inputDir = resolve(args[i + 1]);
    i++;
  }
  if (args[i] === "--dry-run") {
    dryRun = true;
  }
}

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------

function loadDotEnv(envFile) {
  try {
    const content = readFileSync(envFile, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eqIdx = trimmed.indexOf("=");
      if (eqIdx < 0) continue;
      const key = trimmed.slice(0, eqIdx).trim();
      const value = trimmed.slice(eqIdx + 1).trim();
      if (!process.env[key]) {
        process.env[key] = value;
      }
    }
  } catch {
    // .env not found is ok
  }
}

function joinUrl(base, path) {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

async function fetchJson(url, options = {}) {
  const resp = await fetch(url, options);
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = body?.errors?.[0]?.message || body?.message || resp.statusText;
    throw new Error(`HTTP ${resp.status} for ${url}: ${msg}`);
  }
  return body;
}

async function login() {
  if (DIRECTUS_TOKEN) return DIRECTUS_TOKEN;

  const resp = await fetchJson(joinUrl(DIRECTUS_URL, "/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: DIRECTUS_EMAIL, password: DIRECTUS_PASSWORD }),
  });
  return resp.data?.access_token;
}

function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

// ---------------------------------------------------------------------------
// Directus API adapter (production)
// ---------------------------------------------------------------------------

function createDirectusApi(token) {
  return {
    async getItems(collection, params = {}) {
      const searchParams = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (Array.isArray(value)) {
          searchParams.set(key, value.join(","));
        } else if (value != null && typeof value === "object") {
          searchParams.set(key, JSON.stringify(value));
        } else {
          searchParams.set(key, String(value));
        }
      }
      const url = joinUrl(DIRECTUS_URL, `/items/${collection}?${searchParams.toString()}`);
      const result = await fetchJson(url, { headers: authHeaders(token) });
      return result.data || [];
    },

    async createItem(collection, data) {
      const url = joinUrl(DIRECTUS_URL, `/items/${collection}`);
      const result = await fetchJson(url, {
        method: "POST",
        headers: authHeaders(token),
        body: JSON.stringify(data),
      });
      return result.data;
    },

    async updateItem(collection, id, data) {
      const url = joinUrl(DIRECTUS_URL, `/items/${collection}/${id}`);
      const result = await fetchJson(url, {
        method: "PATCH",
        headers: authHeaders(token),
        body: JSON.stringify(data),
      });
      return result.data;
    },

    async upsertSingleton(collection, data) {
      const url = joinUrl(DIRECTUS_URL, `/items/${collection}`);
      const result = await fetchJson(url, {
        method: "PATCH",
        headers: authHeaders(token),
        body: JSON.stringify(data),
      });
      return result.data;
    },
  };
}

// ---------------------------------------------------------------------------
// JSON loading
// ---------------------------------------------------------------------------

function loadJsonFile(filePath) {
  const raw = readFileSync(filePath, "utf-8").replace(/^\uFEFF/, "");
  return JSON.parse(raw);
}

// ---------------------------------------------------------------------------
// Validation helper
// ---------------------------------------------------------------------------

function buildBundleForValidation(profilesDoc, presetsDoc, askOptionsDoc) {
  const profilesBundle = {
    providers: {},
    models: {},
    profiles: {},
  };

  for (const [slug, provider] of Object.entries(profilesDoc.providers || {})) {
    profilesBundle.providers[slug] = { ...provider };
  }

  for (const [slug, model] of Object.entries(profilesDoc.models || {})) {
    profilesBundle.models[slug] = { ...model };
  }

  for (const [slug, profile] of Object.entries(profilesDoc.profiles || {})) {
    profilesBundle.profiles[slug] = { ...profile };
  }

  return { profilesBundle, presetsBundle: presetsDoc, askOptionsBundle: askOptionsDoc };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  console.log("Importing LLM config bundle into Directus...");
  console.log(`  Directus URL: ${DIRECTUS_URL}`);
  console.log(`  Input dir:    ${inputDir}`);
  if (dryRun) console.log("  *** DRY RUN — no data will be written ***");

  // Load source JSON files
  const profilesPath = resolve(inputDir, "model-profiles.json");
  const presetsPath = resolve(inputDir, "model-presets.json");
  const askOptionsPath = resolve(inputDir, "reader-ask-model-options.json");

  console.log("\n  Loading source JSON files...");
  const profilesDoc = loadJsonFile(profilesPath);
  const presetsDoc = loadJsonFile(presetsPath);
  const askOptionsDoc = loadJsonFile(askOptionsPath);

  console.log(`    providers:  ${Object.keys(profilesDoc.providers || {}).length}`);
  console.log(`    models:     ${Object.keys(profilesDoc.models || {}).length}`);
  console.log(`    profiles:   ${Object.keys(profilesDoc.profiles || {}).length}`);
  console.log(`    presets:    ${Object.keys(presetsDoc).length}`);
  console.log(`    ask options: ${Object.keys(askOptionsDoc.options || {}).length}`);

  // Validate before importing
  console.log("\n  Validating bundle...");
  const bundle = buildBundleForValidation(profilesDoc, presetsDoc, askOptionsDoc);
  const { issues, valid } = validateLlmConfigBundle(bundle);

  if (issues.length > 0) {
    console.log("\n  Validation issues:");
    console.log(formatValidationIssues(issues));
  }

  if (!valid) {
    console.error("\n  Import aborted due to validation errors. Fix the issues above and retry.");
    process.exit(1);
  }

  if (issues.length === 0) {
    console.log("  Validation passed.");
  }

  if (dryRun) {
    console.log("\n  Dry run complete. No data was written.");
    return;
  }

  // Login and create API adapter
  const token = await login();
  const api = createDirectusApi(token);
  console.log("  Logged in successfully.\n");

  // Import in FK order: providers → models → profiles → presets → ask options → ask config
  console.log("  Importing providers...");
  const providerIds = await importProviders(api, profilesDoc.providers || {});

  console.log("\n  Importing models...");
  const modelIds = await importModels(api, profilesDoc.models || {}, providerIds);

  console.log("\n  Importing profiles...");
  const profileIds = await importProfiles(api, profilesDoc.profiles || {}, modelIds);

  console.log("\n  Importing presets...");
  await importPresets(api, presetsDoc, profileIds);

  console.log("\n  Importing ask options...");
  await importAskOptions(api, askOptionsDoc);

  console.log("\n  Importing ask config...");
  await importAskConfig(api, askOptionsDoc);

  console.log("\n  Import complete.");
  console.log(`    Providers:    ${providerIds.size}`);
  console.log(`    Models:       ${modelIds.size}`);
  console.log(`    Profiles:     ${profileIds.size}`);
  console.log(`    Presets:      ${Object.keys(presetsDoc).length}`);
  console.log(`    Ask options:  ${Object.keys(askOptionsDoc.options || {}).length}`);
}

main().catch((err) => {
  console.error("Import failed:", err);
  process.exit(1);
});
