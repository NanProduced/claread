/**
 * T4.2a-PUX-R4-R2.1D-Gate-R1 — Dual dev server global setup.
 *
 * Starts TWO Next.js dev servers with isolated environments:
 *
 * - spike-enabled (port 3100): CLAREAD_ENABLE_E2E_SPIKE=1 — spike
 *   routes return 200, all spike E2E and non-spike E2E run here.
 * - spike-disabled (port 3101): no CLAREAD_ENABLE_E2E_SPIKE — spike
 *   routes return 404, gate-disabled spec runs here.
 *
 * Why globalSetup instead of Playwright's webServer:
 * - Playwright 1.60.0's per-project webServer never started (tests
 *   proceeded immediately with ERR_CONNECTION_REFUSED).
 * - Top-level webServer with reuseExistingServer:true also failed to
 *   start (same symptom).
 * - Top-level webServer with reuseExistingServer:false conflicts with
 *   globalSetup because globalSetup runs before webServer, and the
 *   gate-disabled server process triggers a port conflict detection.
 * - globalSetup runs before webServer and gives full control over
 *   process lifecycle, env vars, and cleanup.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createConnection } from "node:net";

const ENABLED_PORT = 3200;
const DISABLED_PORT = 3201;
const HOST = "127.0.0.1";
const ENABLED_URL = `http://${HOST}:${ENABLED_PORT}`;
const DISABLED_URL = `http://${HOST}:${DISABLED_PORT}`;
const READY_TIMEOUT_MS = 120_000;
const POLL_INTERVAL_MS = 1_000;

/**
 * Check if a TCP port is accepting connections. Uses createConnection
 * instead of createServer().listen() because the latter gives false
 * positives on Windows TIME_WAIT ports.
 */
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

function startServer(
  command: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  label: string,
): ChildProcess {
  console.log(`[${label}] Starting: ${command} ${args.join(" ")}`);
  const executable = process.platform === "win32" ? `${command}.cmd` : command;
  // Node 20.12+/25 enforces .cmd spawn security (CVE-2024-27980): spawning
  // .cmd/.bat without shell:true throws EINVAL. Use shell:true on win32.
  const child = spawn(executable, args, {
    cwd: process.cwd(),
    env,
    shell: process.platform === "win32",
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

  child.on("exit", (code: number | null, signal: NodeJS.Signals | null) => {
    if (code !== null && code !== 0) {
      console.error(`[${label}] Server exited with code ${code}`);
    }
  });

  return child;
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const children: ChildProcess[] = [];
  const phoneAuthProvider =
    process.env.CLAREAD_E2E_REAL_PRODUCT === "1" ? "fastapi" : "mock";
  const cleanup = async () => {
    for (const child of children) {
      console.log(`[dual-server] Shutting down PID ${child.pid} ...`);
      if (process.platform === "win32") {
        try {
          spawn("taskkill", ["/T", "/F", "/PID", String(child.pid)], {
            shell: true,
            stdio: "ignore",
          });
        } catch {
          // Best-effort cleanup.
        }
      } else {
        child.kill("SIGTERM");
      }
    }
  };
  try {
  // --- Spike-enabled server (port 3100) ---
  if (await isPortAcceptingConnections(ENABLED_PORT, HOST)) {
    throw new Error(
      `[spike-enabled] Test port ${ENABLED_PORT} is already in use; refusing to reuse or stop another process.`,
    );
  }
  const enabled = startServer(
    "pnpm",
    ["--filter=@claread/web", "dev:spike-test"],
    {
      ...process.env,
      CLAREAD_PHONE_AUTH_PROVIDER: phoneAuthProvider,
      CLAREAD_ENABLE_E2E_SPIKE: "1",
      CLAREAD_E2E_SPIKE_TEST: "1",
    },
    "spike-enabled",
  );
  children.push(enabled);
  await waitForUrl(ENABLED_URL, READY_TIMEOUT_MS, "spike-enabled");
  console.log(`[spike-enabled] Server ready at ${ENABLED_URL}`);
  if (process.env.CLAREAD_E2E_ONLY_ENABLED !== "1") {
    // --- Spike-disabled server (port 3101) ---
    if (await isPortAcceptingConnections(DISABLED_PORT, HOST)) {
      throw new Error(
        `[spike-disabled] Test port ${DISABLED_PORT} is already in use; refusing to reuse or stop another process.`,
      );
    }
    const disabled = startServer(
      "pnpm",
      ["--filter=@claread/web", "dev:gate-test"],
      {
        ...process.env,
        CLAREAD_PHONE_AUTH_PROVIDER: phoneAuthProvider,
        CLAREAD_E2E_GATE_TEST: "1",
      },
      "spike-disabled",
    );
    children.push(disabled);
    await waitForUrl(DISABLED_URL, READY_TIMEOUT_MS, "spike-disabled");
    console.log(`[spike-disabled] Server ready at ${DISABLED_URL}`);
  }
  } catch (error) {
    await cleanup();
    throw error;
  }

  // Teardown only the child processes started by this setup.
  return cleanup;
}
