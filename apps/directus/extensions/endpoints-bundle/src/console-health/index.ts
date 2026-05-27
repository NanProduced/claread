import { defineEndpoint } from "@directus/extensions-sdk";

export default defineEndpoint((router) => {
  router.get("/", (_req, res) => {
    res.json({
      ok: true,
      service: "claread-console",
      mode: "bootstrap",
      endpoint: "console-health",
    });
  });
});
