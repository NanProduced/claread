/**
 * ASK-RETRY-CONTRACT-R7 — exclusive free-port server setup.
 *
 * Scans 3410–3429 for the first free port (never kills another process).
 * Writes the base URL to `apps/web/.claread-r7-e2e-url` for tests.
 * DistDir is isolated so we do not collide with 3400 R2 harness builds.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createConnection, createServer } from "node:net";
import fs from "node:fs";
import path from "node:path";

const HOST = "127.0.0.1";
const PORT_RANGE_START = 3410;
const PORT_RANGE_END = 3429;
const READY_TIMEOUT_MS = 180_000;
const POLL_INTERVAL_MS = 1_000;
const URL_FILE = path.resolve(__dirname, "../../.claread-r7-e2e-url");

function isPortAcceptingConnections(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ port, host });
    const timer = setTimeout(() => {
      socket.destroy();
      resolve(false);
    }, 500);
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

function canBindPort(port: number, host: string): Promise<boolean> {
  return new Promise((resolve) => {
    const server = createServer();
    server.once("error", () => resolve(false));
    server.listen(port, host, () => {
      server.close(() => resolve(true));
    });
  });
}

async function pickFreePort(): Promise<number> {
  for (let port = PORT_RANGE_START; port <= PORT_RANGE_END; port += 1) {
    if (await isPortAcceptingConnections(port, HOST)) {
      continue;
    }
    if (await canBindPort(port, HOST)) {
      return port;
    }
  }
  throw new Error(
    `[ask-retry-r7] No free port in ${PORT_RANGE_START}-${PORT_RANGE_END}; ` +
      "refusing to kill or reuse foreign servers.",
  );
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
        // not ready
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
    await new Promise((r) => setTimeout(r, 100));
  }
  // Soft: log only — do not fail teardown hard if OS holds the port briefly.
  console.warn(`[ask-retry-r7] Port ${port} still open after cleanup wait.`);
}

function startServer(
  command: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  label: string,
  cwd: string,
): ChildProcess {
  console.log(`[${label}] Starting: ${command} ${args.join(" ")}`);
  const isWindows = process.platform === "win32";
  const executable = isWindows ? (process.env.ComSpec ?? "cmd.exe") : command;
  const spawnArgs = isWindows
    ? ["/d", "/s", "/c", [command, ...args].join(" ")]
    : args;
  const child = spawn(executable, spawnArgs, {
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
  return child;
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const children: ChildProcess[] = [];
  const webRoot = path.resolve(__dirname, "../..");
  // Fresh distDir avoids Turbopack lock/compaction collisions.
  const distDir = path.join(webRoot, ".next-e2e-ask-retry-r7-test");
  try {
    fs.rmSync(distDir, { recursive: true, force: true });
    console.log(`[ask-retry-r7] Cleared ${distDir}`);
  } catch (err) {
    console.warn(`[ask-retry-r7] dist clear skipped: ${String(err)}`);
  }

  const port = await pickFreePort();
  const baseUrl = `http://${HOST}:${port}`;
  fs.writeFileSync(URL_FILE, baseUrl, "utf8");
  process.env.CLAREAD_R7_BASE_URL = baseUrl;
  console.log(`[ask-retry-r7] Selected exclusive port ${port} → ${baseUrl}`);

  const cleanup = async () => {
    for (const child of children) {
      const pid = child.pid;
      if (!pid || pid <= 0) continue;
      console.log(`[ask-retry-r7] Shutting down PID ${pid}`);
      child.removeAllListeners("exit");
      if (process.platform === "win32") {
        const killer = spawn("taskkill.exe", ["/T", "/F", "/PID", String(pid)], {
          shell: false,
          stdio: "ignore",
          windowsHide: true,
        });
        await new Promise<void>((resolve) => {
          killer.once("exit", () => resolve());
          killer.once("error", () => resolve());
        });
      } else {
        child.kill("SIGTERM");
      }
    }
    await waitForPortClosed(port, HOST, 15_000);
    try {
      fs.unlinkSync(URL_FILE);
    } catch {
      // ignore
    }
  };

  try {
    const child = startServer(
      "pnpm",
      ["exec", "next", "dev", "--hostname", HOST, "--port", String(port)],
      {
        ...process.env,
        CLAREAD_PHONE_AUTH_PROVIDER: "mock",
        CLAREAD_ENABLE_E2E_SPIKE: "1",
        // Own distDir so Next 16 does not refuse when 3400 holds
        // .next-e2e-ask-activity-r2-test. Never kill foreign servers.
        CLAREAD_E2E_ASK_RETRY_R7: "1",
      },
      "ask-retry-r7",
      webRoot,
    );
    children.push(child);
    // Root + login both ready (avoids "手机号" timeout on cold compile).
    await waitForUrl(baseUrl, READY_TIMEOUT_MS, "ask-retry-r7");
    await waitForUrl(`${baseUrl}/login`, READY_TIMEOUT_MS, "ask-retry-r7-login");
    console.log(`[ask-retry-r7] Server ready at ${baseUrl} (pid=${child.pid})`);
  } catch (error) {
    await cleanup();
    throw error;
  }

  return cleanup;
}
