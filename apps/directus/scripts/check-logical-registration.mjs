#!/usr/bin/env node
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = resolve(ROOT, "../..");

function loadJson(rel) {
  return JSON.parse(readFileSync(resolve(ROOT, rel), "utf8"));
}

function readRepo(rel) {
  return readFileSync(resolve(REPO_ROOT, rel), "utf8");
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

const FORBIDDEN_PANEL_PREFIXES = ["claread-parse-run-"];

const REQUIRED_ENDPOINT_ENTRIES = new Set(["reader-orch"]);
const REQUIRED_MODULE_ENTRIES = new Set(["claread-llm-config"]);

const INIT_SCRIPT_REL = "infra/scripts/init-eval-center-dev.ps1";
const INIT_FORBIDDEN_SIDE_EFFECTS = [
  "docker cp",
  "docker exec",
  "drop_eval_center_tables",
  "0001_eval_center_control_plane.sql",
  "directus:eval-center:sync-metadata",
  "eval-center:sync-metadata",
  "reset-eval-center-data",
];

const errors = [];

const modules = loadJson("extensions/modules-bundle/package.json");
const panels = loadJson("extensions/panels-bundle/package.json");
const endpoints = loadJson("extensions/endpoints-bundle/package.json");
const hooksSrc = readFileSync(
  resolve(ROOT, "extensions/hooks-bundle/src/index.js"),
  "utf8",
);
const directusPkg = loadJson("package.json");
const rootPkg = JSON.parse(readRepo("package.json"));

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

// Example Lab data validation hook is KEEP: must still normalize/validate
// eval_example_lab_entries. UI/module/endpoint remain unregistered.
const hasExampleLabCollection = hooksSrc.includes("eval_example_lab_entries");
const hasExampleLabFilters =
  hooksSrc.includes("eval_example_lab_entries.items.create") ||
  hooksSrc.includes("${COLLECTION}.items.create");
const hasFilterRegistration = /filter\s*\(/.test(hooksSrc);
if (!hasExampleLabCollection || !hasExampleLabFilters || !hasFilterRegistration) {
  errors.push(
    "hooks-bundle must keep Example Lab validation hook for eval_example_lab_entries (normalization/validation KEEP/REHOME)",
  );
}
// Forbid non-Example-Lab legacy hook surfaces (Workflow/Node Lab etc.)
if (/eval_workflow_|eval_node_lab_|eval_judge_run|eval_prompt_variant/.test(hooksSrc)) {
  errors.push("hooks-bundle must not register non-Example-Lab legacy eval control-plane hooks");
}

// Retired sync scripts must be PHYSICALLY DELETED post-cutover. Earlier rounds
// sealed them in place (tombstone + process.exit(1)); physical deletion is the
// final state. Re-adding either script is a cutover regression.
for (const rel of [
  "scripts/sync-parse-run-observability-metadata.mjs",
  "scripts/sync-eval-center-metadata.mjs",
]) {
  if (existsSync(resolve(ROOT, rel))) {
    errors.push(`${rel} must be physically deleted after cutover (still present)`);
  }
}

// Physical cutover: legacy Console / Eval Center / parse-run / inspector SOURCE
// TREES must stay deleted, not merely unregistered. This guards against revival
// by re-adding the source directories behind an unregistered package.json.
const PHYSICALLY_DELETED_DIRS = [
  "extensions/modules-bundle/src/claread-eval-center",
  "extensions/modules-bundle/src/claread-render-scene-inspector",
  "extensions/modules-bundle/src/claread-ai-rag-generator-interface",
  "extensions/modules-bundle/src/claread-output-fragment-editor",
  "extensions/modules-bundle/src/claread-inspector-launcher",
  "extensions/modules-bundle/src/claread-inspector-launcher-interface",
  "extensions/modules-bundle/src/claread-observability-groups-layout",
  "extensions/endpoints-bundle/src/eval-center",
  "extensions/endpoints-bundle/src/parse-run-observability",
];
const PHYSICALLY_DELETED_FILES = [
  "extensions/modules-bundle/src/shared/inspector-launcher.js",
];
for (const rel of PHYSICALLY_DELETED_DIRS) {
  if (existsSync(resolve(ROOT, rel))) {
    errors.push(`${rel} must stay physically deleted (directory present)`);
  }
}
for (const rel of PHYSICALLY_DELETED_FILES) {
  if (existsSync(resolve(ROOT, rel))) {
    errors.push(`${rel} must stay physically deleted (file present)`);
  }
}

// Eval-center DATA reset scripts must be PHYSICALLY DELETED: the old
// reset_eval_center_tables.sql TRUNCATE'd eval_example_lab_entries (a KEEP
// table), so neither the PowerShell runner nor its SQL may remain on disk.
for (const rel of [
  "infra/scripts/reset-eval-center-data.ps1",
  "infra/scripts/reset_eval_center_tables.sql",
]) {
  if (existsSync(resolve(REPO_ROOT, rel))) {
    errors.push(`${rel} must be physically deleted (it could empty eval_example_lab_entries)`);
  }
}

const DROP_MANIFEST_REL = "infra/scripts/drop_eval_center_tables.sql";

// DATA-SCHEMA-BASELINE D2: the Data owner 12-table DROP manifest is
// obsolete and must stay physically deleted — the legacy tables are
// absent from infra/migrations/0001_initial.sql, so there is nothing to
// drop. eval_example_lab_entries (KEEP) lives on in the baseline.
if (existsSync(resolve(REPO_ROOT, DROP_MANIFEST_REL))) {
  errors.push(`${DROP_MANIFEST_REL} must stay physically deleted; legacy Eval tables are absent from the single baseline`);
}

// Single fresh baseline: infra/migrations contains exactly 0001_initial.sql,
// and docker-compose.local.yml mounts exactly that file into initdb.
const migrationsDir = resolve(REPO_ROOT, "infra/migrations");
if (!existsSync(migrationsDir)) {
  errors.push("infra/migrations missing");
} else {
  const migrationEntries = readdirSync(migrationsDir);
  if (!migrationEntries.includes("0001_initial.sql")) {
    errors.push("infra/migrations must contain 0001_initial.sql (single fresh baseline)");
  }
  const unexpectedEntries = migrationEntries.filter((entry) => entry !== "0001_initial.sql");
  if (unexpectedEntries.length > 0) {
    errors.push(`infra/migrations must contain ONLY 0001_initial.sql; unexpected: ${unexpectedEntries.join(", ")}`);
  }
}
const composeBody = readFileSync(
  resolve(REPO_ROOT, "infra/docker/docker-compose.local.yml"),
  "utf8",
);
const initdbMounts = composeBody
  .split(/\r?\n/)
  .filter((line) => line.includes("docker-entrypoint-initdb.d"));
if (initdbMounts.length !== 1 || !initdbMounts[0].includes("0001_initial.sql")) {
  errors.push("docker-compose.local.yml must mount exactly ../migrations/0001_initial.sql into initdb");
}

// Broad protection: NO executable SQL under infra/scripts may TRUNCATE, DROP
// or DELETE the protected eval_example_lab_entries table (existence checks in
// baseline guards are allowed; destructive statements are not). This
// guarantees no ops reset/drop script can empty or drop the KEEP table.
const infraScriptsDir = resolve(REPO_ROOT, "infra/scripts");
if (existsSync(infraScriptsDir)) {
  for (const entry of readdirSync(infraScriptsDir)) {
    if (!entry.endsWith(".sql")) continue;
    const executableSql = readFileSync(resolve(infraScriptsDir, entry), "utf8")
      .split(/\r?\n/)
      .filter((line) => !line.trim().startsWith("--"))
      .join("\n");
    for (const statement of executableSql.split(";")) {
      if (!/eval_example_lab_entries/.test(statement)) continue;
      if (/(TRUNCATE|DROP TABLE|DELETE FROM)/i.test(statement)) {
        errors.push(`infra/scripts/${entry} must not TRUNCATE/DROP/DELETE protected eval_example_lab_entries`);
      }
    }
  }
}

// package.json must not expose retired sync commands as normal ops entrypoints.
for (const cmd of ["parse-run:sync-metadata", "eval-center:sync-metadata"]) {
  if (directusPkg.scripts && Object.prototype.hasOwnProperty.call(directusPkg.scripts, cmd)) {
    errors.push(`apps/directus/package.json still exposes retired script: ${cmd}`);
  }
}
for (const cmd of [
  "directus:parse-run:sync-metadata",
  "directus:eval-center:sync-metadata",
]) {
  if (rootPkg.scripts && Object.prototype.hasOwnProperty.call(rootPkg.scripts, cmd)) {
    errors.push(`root package.json still exposes retired script: ${cmd}`);
  }
}

// DATA-SCHEMA-BASELINE D2: the retired init tombstone is physically
// deleted; the legacy Eval control-plane tables are simply not part of
// the single fresh baseline anymore.
if (existsSync(resolve(REPO_ROOT, INIT_SCRIPT_REL))) {
  errors.push(`${INIT_SCRIPT_REL} must stay physically deleted after DATA-SCHEMA-BASELINE D2`);
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
      hooks: "example-lab-validation-keep",
      retired_sync: "physically-deleted",
      physical_deletion: "enforced",
      eval_example_lab: "protected",
      init_eval_center: "physically-deleted",
    },
    null,
    2,
  ),
);
