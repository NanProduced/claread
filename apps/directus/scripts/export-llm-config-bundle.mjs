#!/usr/bin/env node

/**
 * Export LLM Config Bundle from Directus
 *
 * Reads active LLM config records from Directus and exports them as 3 JSON files
 * aligned with services/api schema:
 *   - model-profiles.json  (providers / models / profiles)
 *   - model-presets.json   (presets)
 *   - reader-ask-model-options.json (ask options + billing/runtime defaults)
 *
 * Usage:
 *   node export-llm-config-bundle.mjs [--output DIR]
 *
 * Default output: apps/directus/.runtime/llm-config-export/
 */

import { mkdir, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { validateLlmConfigBundle, formatValidationIssues } from "./validate-llm-config-bundle.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DIRECTUS_ENV_FILE = resolve(SCRIPT_DIR, "../.env");
const DEFAULT_OUTPUT_DIR = resolve(SCRIPT_DIR, "../.runtime/llm-config-export");

loadDotEnv(DIRECTUS_ENV_FILE);

const DIRECTUS_URL = process.env.DIRECTUS_URL ?? "http://127.0.0.1:8055";
const DIRECTUS_EMAIL = process.env.DIRECTUS_EMAIL ?? process.env.ADMIN_EMAIL ?? "admin@claread.dev";
const DIRECTUS_PASSWORD = process.env.DIRECTUS_PASSWORD ?? process.env.ADMIN_PASSWORD ?? "";
const DIRECTUS_TOKEN = process.env.DIRECTUS_TOKEN ?? process.env.ADMIN_TOKEN ?? "";

// Parse --output argument
const args = process.argv.slice(2);
let outputDir = DEFAULT_OUTPUT_DIR;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--output" && args[i + 1]) {
    outputDir = resolve(args[i + 1]);
    i++;
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
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} for ${url}: ${await resp.text().catch(() => "")}`);
  }
  return resp.json();
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

async function getItems(token, collection, params = {}) {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    searchParams.set(key, typeof value === "object" ? JSON.stringify(value) : String(value));
  }
  const url = joinUrl(DIRECTUS_URL, `/items/${collection}?${searchParams.toString()}`);
  const result = await fetchJson(url, { headers: authHeaders(token) });
  return result.data || [];
}

// ---------------------------------------------------------------------------
// Export logic
// ---------------------------------------------------------------------------

/**
 * Build model-profiles.json from Directus records.
 *
 * @param {Object} providers - Array of provider records
 * @param {Object} models - Array of model records (with provider slug resolved)
 * @param {Object} profiles - Array of profile records (with model slug resolved)
 * @returns {Object} Bundle matching ModelRegistryConfigDocument schema
 */
function buildProfilesBundle(providers, models, profiles) {
  const result = { providers: {}, models: {}, profiles: {} };

  for (const p of providers) {
    const entry = {
      adapter: p.adapter,
    };
    if (p.base_url) entry.base_url = p.base_url;
    if (p.api_key_env) entry.api_key_env = p.api_key_env;
    if (p.provider_options && Object.keys(p.provider_options).length > 0) {
      entry.provider_options = p.provider_options;
    }
    if (p.openai_profile) entry.openai_profile = p.openai_profile;
    if (p.model_settings) entry.model_settings = p.model_settings;

    result.providers[p.slug] = entry;
  }

  for (const m of models) {
    const providerSlug = m.provider?.slug ?? m.provider;
    const entry = {
      provider: providerSlug,
      model_name: m.model_name,
    };
    if (m.model_settings) entry.model_settings = m.model_settings;
    if (m.provider_options) entry.provider_options = m.provider_options;
    if (m.openai_profile) entry.openai_profile = m.openai_profile;

    result.models[m.slug] = entry;
  }

  for (const p of profiles) {
    const modelSlug = p.model?.slug ?? p.model;
    const entry = {
      model: modelSlug,
    };
    if (p.model_settings) entry.model_settings = p.model_settings;

    result.profiles[p.slug] = entry;
  }

  return result;
}

/**
 * Build model-presets.json from Directus records.
 */
function buildPresetsBundle(presets) {
  const result = {};

  for (const p of presets) {
    const entry = {};

    if (p.base_preset?.slug) {
      entry.preset = p.base_preset.slug;
    }
    if (p.default_profile?.slug) {
      entry.default_profile = p.default_profile.slug;
    }
    if (p.routes && Object.keys(p.routes).length > 0) {
      entry.routes = p.routes;
    }

    // Only include if there's something besides empty
    if (Object.keys(entry).length > 0) {
      result[p.slug] = entry;
    }
  }

  return result;
}

/**
 * Build reader-ask-model-options.json from Directus records.
 */
function buildAskOptionsBundle(askOptions) {
  const result = {
    default_option: "",
    billing_defaults: {
      multiplier_input: 1,
      multiplier_output: 5,
      tokens_per_point: 1000,
      price_multiplier: 1.0,
      reserved_points: 10,
      billing_policy_version: "analysis_weighted_tokens_v1",
    },
    runtime_defaults: {
      max_input_tokens: 24000,
      max_output_tokens: 3200,
      prompt_buffer_tokens: 800,
    },
    options: {},
  };

  for (const opt of askOptions) {
    const entry = {
      label: opt.label,
    };
    if (opt.description) entry.description = opt.description;
    if (opt.selection) entry.selection = opt.selection;
    if (opt.price_multiplier !== undefined && opt.price_multiplier !== 1.0) {
      entry.price_multiplier = Number(opt.price_multiplier);
    }
    if (opt.runtime_budget) entry.runtime_budget = opt.runtime_budget;
    entry.enabled = opt.enabled;

    result.options[opt.slug] = entry;

    // First enabled option becomes default if none specified
    if (opt.enabled && !result.default_option) {
      result.default_option = opt.slug;
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  console.log("Exporting LLM config bundle from Directus...");
  console.log(`  Directus URL: ${DIRECTUS_URL}`);
  console.log(`  Output dir:   ${outputDir}`);

  const token = await login();
  console.log("  Logged in successfully.");

  // Fetch active records with related slug fields
  const [providers, models, profiles, presets, askOptions] = await Promise.all([
    getItems(token, "llm_providers", {
      "filter[status]": "active",
      "fields": ["*"],
      "sort": "sort",
    }),
    getItems(token, "llm_models", {
      "filter[status]": "active",
      "fields": ["*", "provider.slug"],
      "sort": "sort",
    }),
    getItems(token, "llm_profiles", {
      "filter[status]": "active",
      "fields": ["*", "model.slug"],
      "sort": "sort",
    }),
    getItems(token, "llm_presets", {
      "filter[status]": "active",
      "fields": ["*", "base_preset.slug", "default_profile.slug"],
      "sort": "sort",
    }),
    getItems(token, "llm_ask_options", {
      "filter[enabled]": "true",
      "fields": ["*"],
      "sort": "sort",
    }),
  ]);

  console.log(`  Fetched: ${providers.length} providers, ${models.length} models, ${profiles.length} profiles, ${presets.length} presets, ${askOptions.length} ask options`);

  // Build bundles
  const profilesBundle = buildProfilesBundle(providers, models, profiles);
  const presetsBundle = buildPresetsBundle(presets);
  const askOptionsBundle = buildAskOptionsBundle(askOptions);

  // Validate
  const { issues, valid } = validateLlmConfigBundle({
    profilesBundle,
    presetsBundle,
    askOptionsBundle,
  });

  if (issues.length > 0) {
    console.log("\nValidation issues:");
    console.log(formatValidationIssues(issues));
  }

  if (!valid) {
    console.error("\nExport aborted due to validation errors. Fix the issues above and retry.");
    process.exit(1);
  }

  // Write output files
  await mkdir(outputDir, { recursive: true });

  const profilesPath = resolve(outputDir, "model-profiles.json");
  const presetsPath = resolve(outputDir, "model-presets.json");
  const askOptionsPath = resolve(outputDir, "reader-ask-model-options.json");

  await writeFile(profilesPath, JSON.stringify(profilesBundle, null, 2) + "\n", "utf-8");
  await writeFile(presetsPath, JSON.stringify(presetsBundle, null, 2) + "\n", "utf-8");
  await writeFile(askOptionsPath, JSON.stringify(askOptionsBundle, null, 2) + "\n", "utf-8");

  console.log(`\nExport complete:`);
  console.log(`  ${profilesPath}`);
  console.log(`  ${presetsPath}`);
  console.log(`  ${askOptionsPath}`);

  if (issues.length > 0) {
    console.log(`\n  (${issues.length} warning(s) — review above)`);
  }
}

main().catch((err) => {
  console.error("Export failed:", err);
  process.exit(1);
});
