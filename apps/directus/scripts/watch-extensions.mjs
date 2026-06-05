import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import chokidar from "chokidar";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const directusRoot = path.resolve(workspaceRoot, "apps/directus");
const pnpmCommand = "pnpm";

const bundles = [
  {
    filter: "@claread/directus-modules",
    label: "modules",
    watchPath: path.join(directusRoot, "extensions/modules-bundle/src"),
  },
  {
    filter: "@claread/directus-panels",
    label: "panels",
    watchPath: path.join(directusRoot, "extensions/panels-bundle/src"),
  },
  {
    filter: "@claread/directus-endpoints",
    label: "endpoints",
    watchPath: path.join(directusRoot, "extensions/endpoints-bundle/src"),
  },
  {
    filter: "@claread/directus-hooks",
    label: "hooks",
    watchPath: path.join(directusRoot, "extensions/hooks-bundle/src"),
  },
];

const state = new Map(
  bundles.map((bundle) => [
    bundle.filter,
    {
      running: false,
      pending: false,
      timer: null,
    },
  ])
);

function runBuild(filter) {
  const bundle = bundles.find((item) => item.filter === filter);
  const status = state.get(filter);

  if (!bundle || !status) return;
  if (status.running) {
    status.pending = true;
    return;
  }

  status.running = true;
  status.pending = false;
  console.log(`[watch] building ${bundle.label}`);

  const command =
    process.platform === "win32"
      ? {
          file: "cmd.exe",
          args: ["/d", "/s", "/c", `${pnpmCommand} --filter ${filter} run build`],
        }
      : {
          file: pnpmCommand,
          args: ["--filter", filter, "run", "build"],
        };

  const child = spawn(command.file, command.args, {
    cwd: workspaceRoot,
    stdio: "inherit",
    shell: false,
  });

  child.on("exit", (code) => {
    status.running = false;

    if (code !== 0) {
      console.error(`[watch] build failed for ${bundle.label} with exit code ${code ?? "unknown"}`);
    } else {
      console.log(`[watch] build finished for ${bundle.label}`);
    }

    if (status.pending) {
      status.pending = false;
      runBuild(filter);
    }
  });
}

function scheduleBuild(filter) {
  const status = state.get(filter);
  if (!status) return;

  if (status.timer) {
    clearTimeout(status.timer);
  }

  status.timer = setTimeout(() => {
    status.timer = null;
    runBuild(filter);
  }, 250);
}

console.log("[watch] initial build");
for (const bundle of bundles) {
  runBuild(bundle.filter);
}

for (const bundle of bundles) {
  const watcher = chokidar.watch(bundle.watchPath, {
    ignoreInitial: true,
  });

  watcher.on("all", (_event, changedPath) => {
    console.log(`[watch] change detected in ${bundle.label}: ${path.relative(workspaceRoot, changedPath)}`);
    scheduleBuild(bundle.filter);
  });
}

console.log("[watch] watching Directus extension sources for changes");
