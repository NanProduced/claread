import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

function createRouter() {
  const routes = [];
  return {
    routes,
    get(path, handler) {
      routes.push({ method: "GET", path, handler });
    },
  };
}

test("reader-orch registers four read-only routes", async () => {
  const mod = await import(pathToFileURL(resolve(HERE, "index.js")).href);
  const router = createRouter();
  const rows = [{ id: "span-1", trace_id: "t1" }];
  const database = {
    async raw() {
      return rows;
    },
  };
  mod.default(router, { database });
  assert.deepEqual(
    router.routes.map((r) => r.path).sort(),
    [
      "/dashboard",
      "/record/:record_id/summary",
      "/run/:run_id",
      "/trace/:trace_id",
    ].sort(),
  );
});

test("reader-orch requires authentication", async () => {
  const mod = await import(pathToFileURL(resolve(HERE, "index.js")).href);
  const router = createRouter();
  mod.default(router, {
    database: {
      async raw() {
        return [];
      },
    },
  });
  const route = router.routes.find((r) => r.path === "/trace/:trace_id");
  let status = null;
  let body = null;
  await route.handler(
    { accountability: null, params: { trace_id: "t1" }, query: {} },
    {
      status(code) {
        status = code;
        return this;
      },
      json(payload) {
        body = payload;
      },
    },
    () => {
      throw new Error("should not next on forbidden");
    },
  );
  assert.equal(status, 403);
  assert.equal(body.errors[0].extensions.code, "FORBIDDEN");
});

test("reader-orch returns data for authenticated call", async () => {
  const mod = await import(pathToFileURL(resolve(HERE, "index.js")).href);
  const router = createRouter();
  const rows = [{ id: "span-1", trace_id: "t1", status: "succeeded" }];
  mod.default(router, {
    database: {
      async raw() {
        return rows;
      },
    },
  });
  const route = router.routes.find((r) => r.path === "/trace/:trace_id");
  let body = null;
  await route.handler(
    {
      accountability: { user: "admin-user" },
      params: { trace_id: "t1" },
      query: {},
    },
    {
      status() {
        return this;
      },
      json(payload) {
        body = payload;
      },
    },
    (err) => {
      throw err || new Error("unexpected next");
    },
  );
  assert.deepEqual(body, { data: rows });
});
