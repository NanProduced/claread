/**
 * Canonical Playwright server setup for the Web Reader E2E suite.
 *
 * The suite owns one isolated Next dev server on port 3200. Individual specs
 * choose their boundary: current-product specs may use the deterministic
 * FastAPI handoff, while UI contract specs can keep their network mocks.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createConnection } from "node:net";

const PORT = 3200;
const HOST = "127.0.0.1";
const BASE_URL = `http://${HOST}:${PORT}`;
const READY_TIMEOUT_MS = 120_000;
const POLL_INTERVAL_MS = 1_000;

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

function waitForUrl(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;

  return new Promise((resolve, reject) => {
    const poll = async () => {
      if (Date.now() > deadline) {
        reject(new Error(`[e2e] Server not ready at ${url} within ${timeoutMs}ms`));
        return;
      }

      try {
        const response = await fetch(url, { signal: AbortSignal.timeout(2_000) });
        if (response.status > 0) {
          resolve();
          return;
        }
      } catch {
        // Next may still be compiling the first request.
      }

      setTimeout(poll, POLL_INTERVAL_MS);
    };

    poll();
  });
}

function waitForPortClosed(port: number, host: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;

  return new Promise((resolve, reject) => {
    const poll = async () => {
      if (!(await isPortAcceptingConnections(port, host))) {
        resolve();
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error(`[e2e] Port ${port} remained open after cleanup.`));
        return;
      }
      setTimeout(poll, 200);
    };

    poll();
  });
}

function startServer(env: NodeJS.ProcessEnv): ChildProcess {
  const command = "pnpm";
  const args = ["--filter=@claread/web", "dev:e2e"];
  const executable = process.platform === "win32" ? `${command}.cmd` : command;
  const child = spawn(executable, args, {
    cwd: process.cwd(),
    env,
    shell: process.platform === "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout?.on("data", (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.log(`[e2e:stdout] ${line}`);
  });
  child.stderr?.on("data", (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.error(`[e2e:stderr] ${line}`);
  });
  child.on("exit", (code: number | null) => {
    if (code !== null && code !== 0) {
      console.error(`[e2e] Server exited with code ${code}`);
    }
  });

  return child;
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  if (await isPortAcceptingConnections(PORT, HOST)) {
    throw new Error(
      `[e2e] Test port ${PORT} is already in use; refusing to reuse or stop another process.`,
    );
  }

  const phoneAuthProvider =
    process.env.CLAREAD_E2E_REAL_PRODUCT === "1" ? "fastapi" : "mock";
  const child = startServer({
    ...process.env,
    CLAREAD_PHONE_AUTH_PROVIDER: phoneAuthProvider,
    CLAREAD_E2E_TEST: "1",
  });

  try {
    await waitForUrl(BASE_URL, READY_TIMEOUT_MS);
    console.log(`[e2e] Server ready at ${BASE_URL} (pid=${child.pid})`);
  } catch (error) {
    await cleanupServer(child);
    throw error;
  }

  return async () => cleanupServer(child);
}

async function cleanupServer(child: ChildProcess): Promise<void> {
  if (process.platform === "win32") {
    if (child.pid !== undefined) {
      await new Promise<void>((resolve) => {
        const killer = spawn("taskkill", ["/T", "/F", "/PID", String(child.pid)], {
          shell: true,
          stdio: "ignore",
        });
        killer.once("error", () => resolve());
        killer.once("exit", () => resolve());
      });
    }
  } else if (child.exitCode === null) {
    child.kill("SIGTERM");
  }

  try {
    await waitForPortClosed(PORT, HOST, 15_000);
  } catch (error) {
    console.warn(`[e2e] Server cleanup did not close port ${PORT}: ${String(error)}`);
  }
}
