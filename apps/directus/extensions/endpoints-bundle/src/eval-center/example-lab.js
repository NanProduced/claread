/**
 * Example Lab endpoint routes.
 *
 * CRUD is handled by Directus native Collection (eval_example_lab_entries).
 * This module only provides:
 *   - AI / rule generation of RAG fields (proxied to Claread API)
 *   - Enum values for reference
 *
 * All generation requests are proxied to the Claread API, which resolves model
 * profiles when needed and handles the actual LLM invocation.
 *
 * Note: RAG-derived fields use open vocabulary. `grammar_tags` is a
 * normalized tag list maintained by the AI generator + hook, and the RAG
 * surface area is {grammar_tags, retrieval_text, derived_at, derived_by}
 * — nothing else.
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VALID_EXAMPLE_TYPES = ["vocab", "phrase", "context", "grammar", "sentence_analysis", "translation"];
const VALID_SOURCE_KINDS = ["manual", "run_capture", "yaml_import", "seed_import", "other"];
const VALID_TARGET_NODES = ["grammar", "vocabulary", "translation", "academic"];
const VALID_READING_VARIANTS = [
  "gaokao", "cet", "kaoyan", "tem", "ielts_toefl",
  "beginner_reading", "intermediate_reading", "intensive_reading", "academic_general",
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function validationError(message, field) {
  const error = new Error(message);
  error.status = 400;
  error.code = "VALIDATION_ERROR";
  error.field = field || null;
  return error;
}

function parseJsonObject(value, field) {
  if (value === null || value === undefined || value === "") return {};
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
    } catch {
    }
    throw validationError(`${field} must be a JSON object`, field);
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    return value;
  }
  throw validationError(`${field} must be a JSON object`, field);
}

function buildNodeLabAuthConfig(readEnv, env) {
  const baseUrl = readEnv(env, "CLAREAD_API_BASE_URL");
  const adminKey =
    readEnv(env, "CLAREAD_API_ADMIN_KEY") ||
    readEnv(env, "DAILY_READER_ADMIN_API_KEY");
  return { baseUrl, adminKey };
}

function joinUrl(baseUrl, path) {
  return `${String(baseUrl).replace(/\/+$/, "")}/${String(path).replace(/^\/+/, "")}`;
}

// ---------------------------------------------------------------------------
// Route registration
// ---------------------------------------------------------------------------

export function registerExampleLabRoutes(router, _context, deps) {
  const { buildAuthGuard, readEnv, parseUpstreamError } = deps;
  const env = _context?.env || process.env;

  // GET /example-lab/enums
  router.get("/example-lab/enums", (req, res) => {
    if (!buildAuthGuard(req, res)) return;
    res.json({
      data: {
        example_types: VALID_EXAMPLE_TYPES,
        source_kinds: VALID_SOURCE_KINDS,
        target_nodes: VALID_TARGET_NODES,
        reading_variants: VALID_READING_VARIANTS,
      },
    });
  });

  // GET /example-lab/model-profiles
  // Proxies to Claread API /eval/article-analysis/model-profiles (shared across eval tools)
  router.get("/example-lab/model-profiles", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    const { baseUrl, adminKey } = buildNodeLabAuthConfig(readEnv, env);
    if (!baseUrl || !adminKey) {
      return res.status(503).json({
        errors: [{ message: "Eval proxy is not configured.", extensions: { code: "SERVICE_UNAVAILABLE" } }],
      });
    }

    try {
      const upstream = await fetch(
        joinUrl(baseUrl, "/eval/article-analysis/model-profiles"),
        {
          method: "GET",
          headers: {
            Accept: "application/json",
            "x-admin-api-key": adminKey,
          },
        },
      );

      if (!upstream.ok) {
        const errorPayload = await parseUpstreamError(upstream);
        const error = new Error(
          errorPayload?.detail || errorPayload?.message || "Upstream request failed.",
        );
        error.status = upstream.status;
        error.code = "UPSTREAM_EVAL_ERROR";
        throw error;
      }

      const payload = await upstream.json();
      res.json({ data: payload });
    } catch (error) {
      if (error?.status) {
        return res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code } }],
        });
      }
      next(error);
    }
  });

  // POST /example-lab/ai-generate-rag-fields
  // Proxies to Claread API /eval/article-analysis/example-lab/generate-rag-fields
  router.post("/example-lab/ai-generate-rag-fields", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    const { baseUrl, adminKey } = buildNodeLabAuthConfig(readEnv, env);
    if (!baseUrl || !adminKey) {
      return res.status(503).json({
        errors: [{ message: "Eval proxy is not configured.", extensions: { code: "SERVICE_UNAVAILABLE" } }],
      });
    }

    try {
      const body = req.body || {};
      const sentenceText = String(body.sentence_text || "").trim();
      const modelProfile = String(body.model_profile || "").trim();
      const readingVariant = String(body.reading_variant || "").trim();

      if (!sentenceText) throw validationError("sentence_text is required", "sentence_text");
      if (!modelProfile) throw validationError("model_profile is required", "model_profile");
      if (!readingVariant) {
        throw validationError("reading_variant is required", "reading_variant");
      }
      if (readingVariant === "default") {
        throw validationError(
          "reading_variant 'default' is no longer supported; pick a concrete variant",
          "reading_variant",
        );
      }

      const upstreamBody = {
        sentence_text: sentenceText,
        output_fragment: parseJsonObject(body.output_fragment, "output_fragment"),
        reading_variant: readingVariant,
        timeout_seconds: body.timeout_seconds || 30,
      };
      upstreamBody.model_profile = modelProfile;

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 60000);

      try {
        const upstream = await fetch(
          joinUrl(baseUrl, "/eval/article-analysis/example-lab/generate-rag-fields"),
          {
            method: "POST",
            headers: {
              Accept: "application/json",
              "Content-Type": "application/json",
              "x-admin-api-key": adminKey,
            },
            body: JSON.stringify(upstreamBody),
            signal: controller.signal,
          },
        );

        if (!upstream.ok) {
          const errorPayload = await parseUpstreamError(upstream);
          const error = new Error(
            errorPayload?.detail || errorPayload?.message || "Upstream request failed.",
          );
          error.status = upstream.status;
          error.code = "UPSTREAM_EVAL_ERROR";
          throw error;
        }

        const payload = await upstream.json();
        res.json({ data: payload });
      } finally {
        clearTimeout(timeout);
      }
    } catch (error) {
      if (error?.name === "AbortError") {
        return res.status(504).json({
          errors: [{ message: "Example Lab AI generation timed out at the Directus proxy layer.", extensions: { code: "UPSTREAM_TIMEOUT" } }],
        });
      }
      if (error?.status) {
        return res.status(error.status).json({
          errors: [{ message: error.message, extensions: { code: error.code, field: error.field } }],
        });
      }
      next(error);
    }
  });
}
