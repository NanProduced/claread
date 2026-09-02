/**
 * Shared real-product session provisioning for the web E2E suite.
 *
 * The real-product specs need a REAL FastAPI session (real BFF -> FastAPI ->
 * PostgreSQL chain) without driving the login UI. This helper provisions an
 * isolated email identity + email/web session through the API-side test-only
 * fixture (`services/api/tests/web_real_product_session_fixture.py`), which
 * reuses the production identity/session primitives (DB stores only the token
 * hash; the plaintext token is returned to this process only).
 */
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { promisify } from "node:util";

import type { Page } from "@playwright/test";

const execFileAsync = promisify(execFile);

export interface ProvisionedSession {
  email: string;
  sessionToken: string;
  userId: string;
}

export function repoRoot(): string {
  const configuredRoot = process.env.CLAREAD_E2E_API_REPO_ROOT?.trim();
  const candidates = [
    configuredRoot ? resolve(configuredRoot) : null,
    resolve(process.cwd()),
    resolve(process.cwd(), "..", ".."),
  ].filter((candidate): candidate is string => Boolean(candidate));
  const root = candidates.find((candidate) =>
    existsSync(resolve(candidate, "services", "api", "pyproject.toml")),
  );
  if (!root) {
    throw new Error(
      "Unable to locate the Claread API worktree; set CLAREAD_E2E_API_REPO_ROOT",
    );
  }
  return root;
}

export function fixtureEmail(): string {
  return `claread-e2e-${randomUUID().replace(/-/g, "")}@example.invalid`;
}

export function apiPythonPath(): string {
  const apiRoot = resolve(repoRoot(), "services", "api");
  return resolve(
    apiRoot,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
}

async function runSessionFixture(args: string[]): Promise<Record<string, unknown>> {
  const apiRoot = resolve(repoRoot(), "services", "api");
  const helper = resolve(apiRoot, "tests", "web_real_product_session_fixture.py");
  let stdout: string;
  try {
    ({ stdout } = await execFileAsync(apiPythonPath(), [helper, ...args], {
      cwd: apiRoot,
      env: { ...process.env },
      timeout: 120_000,
      maxBuffer: 4 * 1024 * 1024,
    }));
  } catch {
    throw new Error("real-product session fixture process failed");
  }
  const output = stdout.trim();
  if (!output) {
    throw new Error("real-product session fixture returned no output");
  }
  try {
    return JSON.parse(output) as Record<string, unknown>;
  } catch {
    throw new Error("real-product session fixture returned invalid output");
  }
}

function asString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`real-product session fixture ${label} must be a non-empty string`);
  }
  return value;
}

export async function provisionRealProductSession(email: string): Promise<ProvisionedSession> {
  const result = await runSessionFixture(["provision", "--email", email]);
  if (result.status !== "PASS") {
    throw new Error("real-product session provision failed");
  }
  return {
    email,
    sessionToken: asString(result.session_token, "session_token"),
    userId: asString(result.user_id, "user_id"),
  };
}

export async function installRealProductSession(
  page: Page,
  email: string,
  nextPath: string,
): Promise<ProvisionedSession> {
  const session = await provisionRealProductSession(email);
  await page.goto("/");
  await page.context().addCookies([
    {
      name: "claread_web_session",
      value: session.sessionToken,
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
      secure: false,
    },
  ]);
  await page.goto(nextPath);
  return session;
}

export interface CleanupResult {
  residualTotal: number;
  deletedUser: boolean;
}

export async function cleanupRealProductSession(
  email: string,
  recordId?: string,
): Promise<CleanupResult> {
  const args = ["cleanup", "--email", email];
  if (recordId) {
    args.push("--record-id", recordId);
  }
  const result = await runSessionFixture(args);
  return {
    residualTotal: Number(result.residual_total),
    deletedUser: Boolean(result.deleted_user),
  };
}
