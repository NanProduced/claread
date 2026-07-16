/**
 * R2.5-only server setup for Agentic Ask Activity browser acceptance.
 *
 * Single spike-enabled server on port 3400 so this suite does not collide
 * with shared 3200/3201 spike servers used by other agents.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createConnection } from "node:net";
import path from "node:path";

const ENABLED_PORT = 3400;
const HOST = "127.0.0.1";
const ENABLED_URL = `http://${HOST}:${ENABLED_PORT}`;
const READY_TIMEOUT_MS = 180_000;
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

function startServer(
  command: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  label: string,
  cwd: string,
): ChildProcess {
  console.log(`[${label}] Starting: ${command} ${args.join(" ")} (cwd=${cwd})`);
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

  const cleanup = async () => {
    for (const child of children) {
      const pid = child.pid;
      if (!pid || pid <= 0) continue;
      console.log(`[ask-activity-r2] Shutting down PID ${pid} ...`);
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
    await waitForPortClosed(ENABLED_PORT, HOST, 15_000);
  };

  try {
    if (await isPortAcceptingConnections(ENABLED_PORT, HOST)) {
      throw new Error(
        `[ask-activity-r2] Test port ${ENABLED_PORT} is already in use; refusing to reuse or stop another process.`,
      );
    }

    const enabled = startServer(
      "pnpm",
      ["exec", "next", "dev", "--hostname", HOST, "--port", String(ENABLED_PORT)],
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
