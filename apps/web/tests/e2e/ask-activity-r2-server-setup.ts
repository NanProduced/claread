/**
 * R2.5-only server setup for Agentic Ask Activity browser acceptance.
 *
 * Single spike-enabled server on port 3400 so this suite does not collide
 * with shared 3200/3201 spike servers used by other agents.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createConnection } from "node:net";
import fs from "node:fs";
import path from "node:path";

const ENABLED_PORT = 3400;
const HOST = "127.0.0.1";
const ENABLED_URL = `http://${HOST}:${ENABLED_PORT}`;
const READY_TIMEOUT_MS = 180_000;
const POLL_INTERVAL_MS = 1_000;
export const DIST_DIR_NAME = ".next-e2e-ask-activity-r2-test";
export const ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES = [
  `${DIST_DIR_NAME}/types/**/*.ts`,
  `${DIST_DIR_NAME}/dev/types/**/*.ts`,
] as const;

const OWN_NEXT_ENV_ROUTE_IMPORT = `./${DIST_DIR_NAME}/dev/types/routes.d.ts`;
const NEXT_ENV_ROUTE_IMPORT_RE =
  /^([ \t]*import[ \t]+["'])(\.\/\.next(?:-[^\/'"]+)?\/dev\/types\/routes\.d\.ts)(["'];?[ \t]*)$/gm;

export class CleanupConflictError extends Error {
  constructor(message: string) {
    super(`[ask-activity-r2] Cleanup conflict: ${message}`);
    this.name = "CleanupConflictError";
  }
}

type JsonObject = Record<string, unknown>;
type StringToken = { start: number; end: number; value: string };
type TextRange = { start: number; end: number };

function parseJsonObject(content: string, label: string): JsonObject {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    throw new CleanupConflictError(`${label} is not valid JSON; refusing to merge it.`);
  }
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new CleanupConflictError(`${label} is not a JSON object; refusing to merge it.`);
  }
  return parsed as JsonObject;
}

function parseStringInclude(content: string, label: string): string[] {
  const parsed = parseJsonObject(content, label);
  if (parsed.include === undefined) {
    return [];
  }
  if (
    !Array.isArray(parsed.include) ||
    parsed.include.some((entry) => typeof entry !== "string")
  ) {
    throw new CleanupConflictError(
      `${label}.include is not a string array; refusing to merge it.`,
    );
  }
  return parsed.include as string[];
}

function findMatchingArrayEnd(content: string, openIndex: number): number {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = openIndex; index < content.length; index += 1) {
    const character = content[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
      continue;
    }
    if (character === '"') {
      inString = true;
      continue;
    }
    if (character === "[") {
      depth += 1;
    } else if (character === "]") {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }
  throw new CleanupConflictError("tsconfig.include has no matching closing bracket.");
}

function findIncludeStringTokens(content: string): {
  openIndex: number;
  closeIndex: number;
  tokens: StringToken[];
} {
  const includeProperty = /"include"\s*:/g.exec(content);
  if (includeProperty == null) {
    throw new CleanupConflictError("tsconfig.include is missing from the current file.");
  }
  let openIndex = includeProperty.index + includeProperty[0].length;
  while (/\s/.test(content[openIndex] ?? "")) {
    openIndex += 1;
  }
  if (content[openIndex] !== "[") {
    throw new CleanupConflictError("tsconfig.include is not an array.");
  }
  const closeIndex = findMatchingArrayEnd(content, openIndex);
  const tokens: StringToken[] = [];
  let cursor = openIndex + 1;

  while (cursor < closeIndex) {
    while (cursor < closeIndex && (/[\s,]/.test(content[cursor] ?? ""))) {
      cursor += 1;
    }
    if (cursor >= closeIndex) {
      break;
    }
    if (content[cursor] !== '"') {
      throw new CleanupConflictError(
        "tsconfig.include contains a non-string entry; refusing to merge it.",
      );
    }
    const start = cursor;
    cursor += 1;
    let escaped = false;
    let closed = false;
    while (cursor < closeIndex) {
      const character = content[cursor];
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        cursor += 1;
        closed = true;
        break;
      }
      cursor += 1;
    }
    if (!closed) {
      throw new CleanupConflictError(
        "tsconfig.include contains an unterminated string; refusing to merge it.",
      );
    }
    let value: unknown;
    try {
      value = JSON.parse(content.slice(start, cursor));
    } catch {
      throw new CleanupConflictError(
        "tsconfig.include contains malformed JSON; refusing to merge it.",
      );
    }
    if (typeof value !== "string") {
      throw new CleanupConflictError(
        "tsconfig.include contains a non-string JSON value; refusing to merge it.",
      );
    }
    tokens.push({ start, end: cursor, value });
    while (cursor < closeIndex && /\s/.test(content[cursor] ?? "")) {
      cursor += 1;
    }
    if (cursor < closeIndex && content[cursor] !== ",") {
      throw new CleanupConflictError(
        "tsconfig.include has an unexpected token; refusing to merge it.",
      );
    }
    if (content[cursor] === ",") {
      cursor += 1;
    }
  }
  return { openIndex, closeIndex, tokens };
}

function findSeparatorComma(content: string, from: number, to: number): number {
  let cursor = from;
  while (cursor < to && /\s/.test(content[cursor] ?? "")) {
    cursor += 1;
  }
  if (cursor >= to || content[cursor] !== ",") {
    throw new CleanupConflictError(
      "tsconfig.include separators are ambiguous; refusing to merge it.",
    );
  }
  return cursor;
}

function removeIncludeTokens(
  content: string,
  tokens: StringToken[],
  removals: Map<string, number>,
): string {
  const removeIndexes = new Set<number>();
  for (let index = tokens.length - 1; index >= 0; index -= 1) {
    const token = tokens[index];
    const remaining = removals.get(token.value) ?? 0;
    if (remaining > 0) {
      removeIndexes.add(index);
      removals.set(token.value, remaining - 1);
    }
  }
  const ranges: TextRange[] = [];
  let index = 0;
  while (index < tokens.length) {
    if (!removeIndexes.has(index)) {
      index += 1;
      continue;
    }
    const first = index;
    while (index < tokens.length && removeIndexes.has(index)) {
      index += 1;
    }
    const last = index - 1;
    const previous = first > 0 ? tokens[first - 1] : null;
    const next = index < tokens.length ? tokens[index] : null;
    const start = previous
      ? findSeparatorComma(content, previous.end, tokens[first].start)
      : tokens[first].start;
    const end = next
      ? findSeparatorComma(content, tokens[last].end, next.start) + 1
      : tokens[last].end;
    ranges.push({ start, end });
  }

  let merged = content;
  for (let rangeIndex = ranges.length - 1; rangeIndex >= 0; rangeIndex -= 1) {
    const range = ranges[rangeIndex];
    merged = `${merged.slice(0, range.start)}${merged.slice(range.end)}`;
  }
  return merged;
}

function countValues(values: readonly string[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return counts;
}

export function mergeRunnerOwnedTsconfigInclude(
  baseline: string,
  current: string,
): string {
  if (baseline === current) {
    return current;
  }
  if (!ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES.some((entry) => current.includes(entry))) {
    return current;
  }

  const baselineInclude = parseStringInclude(baseline, "baseline tsconfig");
  const currentInclude = parseStringInclude(current, "current tsconfig");
  const baselineCounts = countValues(baselineInclude);
  const currentCounts = countValues(currentInclude);
  const removals = new Map<string, number>();
  for (const entry of ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES) {
    const extraCount =
      (currentCounts.get(entry) ?? 0) - (baselineCounts.get(entry) ?? 0);
    if (extraCount > 0) {
      removals.set(entry, extraCount);
    }
  }
  if (removals.size === 0) {
    return current;
  }

  const { tokens } = findIncludeStringTokens(current);
  const merged = removeIncludeTokens(current, tokens, removals);
  const mergedInclude = parseStringInclude(merged, "merged tsconfig");
  for (const entry of ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES) {
    if ((mergedInclude.filter((value) => value === entry).length) < (baselineCounts.get(entry) ?? 0)) {
      throw new CleanupConflictError(
        `cleanup would remove a baseline include entry: ${entry}`,
      );
    }
  }
  const mergedCounts = countValues(mergedInclude);
  for (const [value, count] of currentCounts) {
    if (
      !ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES.includes(
        value as (typeof ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES)[number],
      ) &&
      (mergedCounts.get(value) ?? 0) !== count
    ) {
      throw new CleanupConflictError(
        `cleanup changed a non-owned tsconfig include entry: ${value}`,
      );
    }
  }
  return merged;
}

function findNextEnvRouteImports(content: string): RegExpMatchArray[] {
  return [...content.matchAll(NEXT_ENV_ROUTE_IMPORT_RE)];
}

export function mergeRunnerOwnedNextEnv(baseline: string, current: string): string {
  if (baseline === current) {
    return current;
  }
  const baselineMatches = findNextEnvRouteImports(baseline);
  const baselineOwn = baselineMatches.some(
    (match) => match[2] === OWN_NEXT_ENV_ROUTE_IMPORT,
  );
  if (baselineOwn) {
    return current;
  }
  const currentOwn = current.includes(OWN_NEXT_ENV_ROUTE_IMPORT);
  if (!currentOwn) {
    return current;
  }
  if (baselineMatches.length !== 1) {
    throw new CleanupConflictError(
      "baseline next-env.d.ts does not have one provable generated route import.",
    );
  }
  const baselineMatch = baselineMatches[0];
  const expected = `${baseline.slice(0, baselineMatch.index ?? 0)}${baselineMatch[1]}${OWN_NEXT_ENV_ROUTE_IMPORT}${baselineMatch[3]}${baseline.slice((baselineMatch.index ?? 0) + baselineMatch[0].length)}`;
  if (current === expected) {
    return baseline;
  }
  throw new CleanupConflictError(
    "next-env.d.ts contains this runner's generated import plus another change.",
  );
}

function mergeRunnerOwnedFile(
  filePath: string,
  baseline: string,
  merge: (baselineContent: string, currentContent: string) => string,
): void {
  const current = fs.readFileSync(filePath, "utf8");
  const merged = merge(baseline, current);
  if (merged !== current) {
    fs.writeFileSync(filePath, merged, "utf8");
  }
}

function assertDedicatedDistPath(webRoot: string, candidate: string): string {
  const resolvedRoot = path.resolve(webRoot);
  const resolvedCandidate = path.resolve(candidate);
  if (
    path.dirname(resolvedCandidate) !== resolvedRoot ||
    path.basename(resolvedCandidate) !== DIST_DIR_NAME
  ) {
    throw new CleanupConflictError(
      `refusing to remove a non-dedicated dist path: ${resolvedCandidate}`,
    );
  }
  if (fs.existsSync(resolvedCandidate)) {
    const stat = fs.lstatSync(resolvedCandidate);
    if (!stat.isDirectory() && !stat.isSymbolicLink()) {
      throw new CleanupConflictError(
        `dedicated dist path is not a directory: ${resolvedCandidate}`,
      );
    }
    const realRoot = fs.realpathSync(resolvedRoot);
    const realCandidate = fs.realpathSync(resolvedCandidate);
    if (
      path.dirname(realCandidate) !== realRoot ||
      path.basename(realCandidate) !== DIST_DIR_NAME
    ) {
      throw new CleanupConflictError(
        `refusing to remove a dist path outside apps/web: ${resolvedCandidate}`,
      );
    }
  }
  return resolvedCandidate;
}

function removeDedicatedDist(webRoot: string, distDir: string): void {
  const validatedDistDir = assertDedicatedDistPath(webRoot, distDir);
  fs.rmSync(validatedDistDir, { recursive: true, force: true });
}

function isPortAcceptingConnections(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ port, host });
    const timer = setTimeout(() => {
      socket.destroy();
      resolve(false);
    }, 1_000);
    socket.once("connect", () => {
      clearTimeout(timer);
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}

function waitForUrl(url: string, timeoutMs: number, label: string): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = async () => {
      if (Date.now() > deadline) {
        reject(new Error(`[${label}] Server not ready at ${url} within ${timeoutMs}ms`));
        return;
      }
      try {
        const res = await fetch(url, { signal: AbortSignal.timeout(2_000) });
        if (res.status > 0) {
          resolve();
          return;
        }
      } catch {
        // Not ready yet.
      }
      setTimeout(poll, POLL_INTERVAL_MS);
    };
    poll();
  });
}

async function waitForPortClosed(port: number, host: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    if (!(await isPortAcceptingConnections(port, host))) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`[ask-activity-r2] Test port ${port} remained open after cleanup.`);
}

async function stopWindowsPortListener(port: number): Promise<void> {
  const script = [
    "$ErrorActionPreference = 'Stop'",
    `$listenerPids = Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique`,
    "foreach ($listenerPid in $listenerPids) {",
    "  if ($listenerPid -gt 0) {",
    "    Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue",
    "  }",
    "}",
  ].join("; ");
  const killer = spawn("pwsh", ["-NoProfile", "-NonInteractive", "-Command", script], {
    shell: false,
    stdio: "ignore",
    windowsHide: true,
  });
  await new Promise<void>((resolve) => {
    killer.once("exit", () => resolve());
    killer.once("error", () => resolve());
  });
}

function startServer(
  command: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  label: string,
  cwd: string,
): ChildProcess {
  console.log(`[${label}] Starting: ${command} ${args.join(" ")} (cwd=${cwd})`);
  const child = spawn(command, args, {
    cwd,
    env,
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout?.on("data", (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.log(`[${label}:stdout] ${line}`);
  });
  child.stderr?.on("data", (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.error(`[${label}:stderr] ${line}`);
  });

  child.on("exit", (code: number | null) => {
    if (code !== null && code !== 0) {
      console.error(`[${label}] Server exited with code ${code}`);
    }
  });

  return child;
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const children: ChildProcess[] = [];
  const webRoot = path.resolve(__dirname, "../..");
  const distDir = assertDedicatedDistPath(webRoot, path.join(webRoot, DIST_DIR_NAME));
  const nextEnvPath = path.join(webRoot, "next-env.d.ts");
  const tsconfigPath = path.join(webRoot, "tsconfig.json");
  const nextEnvBeforeTest = fs.readFileSync(nextEnvPath, "utf8");
  const tsconfigBeforeTest = fs.readFileSync(tsconfigPath, "utf8");

  const cleanup = async () => {
    const cleanupErrors: unknown[] = [];
    try {
      for (const child of children) {
        const pid = child.pid;
        if (!pid || pid <= 0) continue;
        console.log(`[ask-activity-r2] Shutting down PID ${pid} ...`);
        child.removeAllListeners("exit");
        child.kill(process.platform === "win32" ? undefined : "SIGTERM");
      }
      if (
        process.platform === "win32" &&
        (await isPortAcceptingConnections(ENABLED_PORT, HOST))
      ) {
        // Next dev delegates the listener to a worker process. Stop only the
        // listener on this suite's exclusively reserved port; startup refuses
        // to reuse a pre-existing listener, so this cannot target another suite.
        await stopWindowsPortListener(ENABLED_PORT);
      }
      await waitForPortClosed(ENABLED_PORT, HOST, 30_000);
    } catch (error) {
      cleanupErrors.push(error);
    }

    try {
      mergeRunnerOwnedFile(nextEnvPath, nextEnvBeforeTest, mergeRunnerOwnedNextEnv);
    } catch (error) {
      cleanupErrors.push(error);
    }
    try {
      mergeRunnerOwnedFile(
        tsconfigPath,
        tsconfigBeforeTest,
        mergeRunnerOwnedTsconfigInclude,
      );
    } catch (error) {
      cleanupErrors.push(error);
    }
    try {
      removeDedicatedDist(webRoot, distDir);
    } catch (error) {
      cleanupErrors.push(error);
    }

    if (cleanupErrors.length > 0) {
      throw new AggregateError(
        cleanupErrors,
        "[ask-activity-r2] Cleanup failed closed; shared files were not restored wholesale.",
      );
    }
    console.log(`[ask-activity-r2] Preserved concurrent config changes in ${nextEnvPath}`);
    console.log(`[ask-activity-r2] Preserved concurrent config changes in ${tsconfigPath}`);
    console.log(`[ask-activity-r2] Removed dedicated dist ${distDir}`);
  };

  try {
    if (await isPortAcceptingConnections(ENABLED_PORT, HOST)) {
      throw new Error(
        `[ask-activity-r2] Test port ${ENABLED_PORT} is already in use; refusing to reuse or stop another process.`,
      );
    }
    removeDedicatedDist(webRoot, distDir);

    const enabled = startServer(
      process.execPath,
      [
        path.resolve(webRoot, "node_modules/next/dist/bin/next"),
        "dev",
        "--hostname",
        HOST,
        "--port",
        String(ENABLED_PORT),
      ],
      {
        ...process.env,
        CLAREAD_PHONE_AUTH_PROVIDER: "mock",
        CLAREAD_ENABLE_E2E_SPIKE: "1",
        CLAREAD_ASK_ACTIVITY_R2_TEST: "1",
      },
      "ask-activity-r2",
      webRoot,
    );
    children.push(enabled);
    await waitForUrl(ENABLED_URL, READY_TIMEOUT_MS, "ask-activity-r2");
    console.log(`[ask-activity-r2] Server ready at ${ENABLED_URL} (pid=${enabled.pid})`);
  } catch (error) {
    await cleanup();
    throw error;
  }

  return cleanup;
}
