import { execFileSync } from "node:child_process";

const directusContainer = process.env.DIRECTUS_MCP_CONTAINER ?? "claread-directus";
const loginUrl = process.env.DIRECTUS_MCP_LOGIN_URL ?? "http://127.0.0.1:8055/auth/login";
const staticToken = process.env.DIRECTUS_MCP_ACCESS_TOKEN?.trim();

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function readContainerEnv(name) {
  try {
    return execFileSync("docker", ["exec", directusContainer, "printenv", name], {
      stdio: "pipe",
      encoding: "utf8",
    }).trim();
  } catch {
    return "";
  }
}

const email =
  process.env.DIRECTUS_MCP_EMAIL?.trim() ||
  process.env.DIRECTUS_EMAIL?.trim() ||
  process.env.ADMIN_EMAIL?.trim() ||
  readContainerEnv("ADMIN_EMAIL");
const password =
  process.env.DIRECTUS_MCP_PASSWORD?.trim() ||
  process.env.DIRECTUS_PASSWORD?.trim() ||
  process.env.ADMIN_PASSWORD?.trim() ||
  readContainerEnv("ADMIN_PASSWORD");

if (staticToken) {
  process.stdout.write(JSON.stringify({ Authorization: `Bearer ${staticToken}` }));
  process.exit(0);
}

if (!email || !password) {
  fail("Directus MCP auth helper requires DIRECTUS_* / ADMIN_* credentials or a running local Directus container.");
}

const response = await fetch(loginUrl, {
  method: "POST",
  headers: {
    "content-type": "application/json",
  },
  body: JSON.stringify({
    email,
    password,
  }),
});

if (!response.ok) {
  const text = await response.text();
  fail(`Directus MCP login failed: ${response.status} ${text}`);
}

const payload = await response.json();
const accessToken = payload?.data?.access_token;

if (!accessToken || typeof accessToken !== "string") {
  fail("Directus MCP login succeeded but no access token was returned.");
}

process.stdout.write(JSON.stringify({ Authorization: `Bearer ${accessToken}` }));
