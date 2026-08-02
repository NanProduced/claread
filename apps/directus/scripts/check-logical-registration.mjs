#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function loadJson(rel) {
  return JSON.parse(readFileSync(resolve(ROOT, rel), "utf8"));
}

const FORBIDDEN_MODULE_ENTRIES = new Set([
  "claread-eval-center",
  "claread-render-scene-inspector",
  "claread-ai-rag-generator-interface",
  "claread-output-fragment-editor",
  "claread-inspector-launcher",
  "claread-inspector-launcher-interface",
  "claread-observability-groups",
]);

const FORBIDDEN_ENDPOINT_ENTRIES = new Set([
  "parse-run-observability",
  "eval-center",
]);

const FORBIDDEN_PANEL_PREFIXES = [
  "claread-parse-run-",
];

const REQUIRED_ENDPOINT_ENTRIES = new Set(["reader-orch"]);
const REQUIRED_MODULE_ENTRIES = new Set(["claread-llm-config"]);

const errors = [];

const modules = loadJson("extensions/modules-bundle/package.json");
const panels = loadJson("extensions/panels-bundle/package.json");
const endpoints = loadJson("extensions/endpoints-bundle/package.json");
const hooksSrc = readFileSync(
  resolve(ROOT, "extensions/hooks-bundle/src/index.js"),
  "utf8",
);

const moduleNames = (modules["directus:extension"]?.entries ?? []).map((e) => e.name);
const panelNames = (panels["directus:extension"]?.entries ?? []).map((e) => e.name);
const endpointNames = (endpoints["directus:extension"]?.entries ?? []).map((e) => e.name);

for (const name of moduleNames) {
  if (FORBIDDEN_MODULE_ENTRIES.has(name)) {
    errors.push(`modules still registers forbidden entry: ${name}`);
  }
}
for (const name of endpointNames) {
  if (FORBIDDEN_ENDPOINT_ENTRIES.has(name)) {
    errors.push(`endpoints still registers forbidden entry: ${name}`);
  }
}
for (const name of panelNames) {
  if (FORBIDDEN_PANEL_PREFIXES.some((p) => name.startsWith(p))) {
    errors.push(`panels still registers forbidden entry: ${name}`);
  }
}

for (const required of REQUIRED_ENDPOINT_ENTRIES) {
  if (!endpointNames.includes(required)) {
    errors.push(`endpoints missing required entry: ${required}`);
  }
}
for (const required of REQUIRED_MODULE_ENTRIES) {
  if (!moduleNames.includes(required)) {
    errors.push(`modules missing required entry: ${required}`);
  }
}

if (panelNames.length !== 0) {
  errors.push(
    `panels must have zero registered entries after Logical cutover; found: ${panelNames.join(", ") || "(empty)"}`,
  );
}

if (/eval_example_lab_entries|filter\(|action\(/.test(hooksSrc)) {
  errors.push("hooks-bundle still appears to register Example Lab filters/actions");
}

// Sync scripts must refuse to revive old surfaces.
for (const rel of [
  "scripts/sync-parse-run-observability-metadata.mjs",
  "scripts/sync-eval-center-metadata.mjs",
]) {
  const body = readFileSync(resolve(ROOT, rel), "utf8");
  if (!body.includes("[retired]") && !body.includes("process.exit(1)")) {
    errors.push(`${rel} is not retired/no-op; would re-register old metadata`);
  }
}

// reader-orch source must expose the four read-only routes (relative to endpoint name).
const readerOrch = readFileSync(
  resolve(ROOT, "extensions/endpoints-bundle/src/reader-orch/index.js"),
  "utf8",
);
for (const route of [
  '"/trace/:trace_id"',
  '"/run/:run_id"',
  '"/record/:record_id/summary"',
  '"/dashboard"',
]) {
  if (!readerOrch.includes(route)) {
    errors.push(`reader-orch missing route fragment ${route}`);
  }
}

if (errors.length) {
  console.error("registration:check FAILED");
  for (const err of errors) console.error(` - ${err}`);
  process.exit(1);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      modules: moduleNames,
      endpoints: endpointNames,
      panels: panelNames,
      hooks: "no-op",
    },
    null,
    2,
  ),
);
