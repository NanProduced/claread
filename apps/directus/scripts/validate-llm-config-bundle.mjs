/**
 * LLM Config Bundle Validator
 *
 * Validates an exported LLM config bundle against the backend Pydantic schema rules.
 * This module is designed to be called by export-llm-config-bundle.mjs or used standalone.
 *
 * Rules are aligned with:
 *   - services/api/app/llm/types.py (ModelAdapter, ModelProviderConfig, etc.)
 *   - services/api/app/llm/routes.py (ModelRoute Literal)
 *
 * Does NOT introduce a separate validation rule set — all rules mirror the backend.
 */

// Must match ModelAdapter Literal in services/api/app/llm/types.py
const VALID_ADAPTERS = new Set([
  "openai_compatible",
  "dashscope_native",
  "dashscope_embedding",
  "dashscope_rerank",
]);

// Must match ModelRoute Literal in services/api/app/llm/routes.py
const VALID_ROUTES = new Set([
  "annotation_generation",
  "dict_ai",
  "reader_ask",
  "reader_ask_planner",
  "reader_ask_replan",
  "daily_annotation",
  "daily_analysis",
  "daily_review",
  "rag_embedding",
  "rag_rerank",
]);

// Known top-level keys for JSONB fields (mirrors Pydantic model fields)
const KNOWN_MODEL_SETTINGS_KEYS = new Set([
  "max_tokens", "temperature", "top_p", "timeout",
  "parallel_tool_calls", "seed", "presence_penalty", "frequency_penalty",
  "stop_sequences", "extra_headers", "extra_body",
]);

const KNOWN_OPENAI_PROFILE_KEYS = new Set([
  "supports_json_object_output", "supports_json_schema_output",
  "default_structured_output_mode",
  "openai_supports_tool_choice_required", "openai_supports_strict_tool_definition",
  "openai_chat_thinking_field", "openai_chat_send_back_thinking_parts",
]);

const KNOWN_PROVIDER_OPTIONS_KEYS = new Set([
  "transport", "profile", "dimension", "tier", "notes",
]);

/**
 * @typedef {Object} ValidationIssue
 * @property {"error"|"warn"} level
 * @property {string} collection
 * @property {string} slug
 * @property {string} message
 */

/**
 * Validate a complete LLM config bundle.
 *
 * @param {Object} bundle
 * @param {Object} bundle.profilesBundle - { providers, models, profiles }
 * @param {Object} bundle.presetsBundle - { [presetSlug]: {...} }
 * @param {Object} bundle.askOptionsBundle - { default_option, billing_defaults, runtime_defaults, options }
 * @returns {{ issues: ValidationIssue[], valid: boolean }}
 */
export function validateLlmConfigBundle(bundle) {
  const issues = [];
  const { profilesBundle, presetsBundle, askOptionsBundle } = bundle;

  const providerSlugs = new Set(Object.keys(profilesBundle.providers || {}));
  const modelSlugs = new Set(Object.keys(profilesBundle.models || {}));
  const profileSlugs = new Set(Object.keys(profilesBundle.profiles || {}));
  const presetSlugs = new Set(Object.keys(presetsBundle || {}));

  // ---- Provider validation ----
  for (const [slug, provider] of Object.entries(profilesBundle.providers || {})) {
    if (!VALID_ADAPTERS.has(provider.adapter)) {
      issues.push({
        level: "error",
        collection: "llm_providers",
        slug,
        message: `Invalid adapter "${provider.adapter}". Must be one of: ${[...VALID_ADAPTERS].join(", ")}`,
      });
    }

    if (provider.adapter === "openai_compatible" && !provider.base_url) {
      issues.push({
        level: "error",
        collection: "llm_providers",
        slug,
        message: "openai_compatible adapter requires base_url",
      });
    }

    if (
      (provider.adapter === "dashscope_native" ||
        provider.adapter === "dashscope_embedding" ||
        provider.adapter === "dashscope_rerank") &&
      !provider.api_key_env
    ) {
      issues.push({
        level: "error",
        collection: "llm_providers",
        slug,
        message: `${provider.adapter} adapter requires api_key_env`,
      });
    }

    // JSONB key checks (warn only)
    checkJsonbKeys(issues, "llm_providers", slug, "provider_options", provider.provider_options, KNOWN_PROVIDER_OPTIONS_KEYS);
    checkJsonbKeys(issues, "llm_providers", slug, "openai_profile", provider.openai_profile, KNOWN_OPENAI_PROFILE_KEYS);
    checkJsonbKeys(issues, "llm_providers", slug, "model_settings", provider.model_settings, KNOWN_MODEL_SETTINGS_KEYS);
  }

  // ---- Model validation ----
  for (const [slug, model] of Object.entries(profilesBundle.models || {})) {
    if (!providerSlugs.has(model.provider)) {
      issues.push({
        level: "error",
        collection: "llm_models",
        slug,
        message: `References provider "${model.provider}" which does not exist`,
      });
    }

    if (!model.model_name) {
      issues.push({
        level: "error",
        collection: "llm_models",
        slug,
        message: "model_name is required",
      });
    }

    checkJsonbKeys(issues, "llm_models", slug, "provider_options", model.provider_options, KNOWN_PROVIDER_OPTIONS_KEYS);
    checkJsonbKeys(issues, "llm_models", slug, "openai_profile", model.openai_profile, KNOWN_OPENAI_PROFILE_KEYS);
    checkJsonbKeys(issues, "llm_models", slug, "model_settings", model.model_settings, KNOWN_MODEL_SETTINGS_KEYS);
  }

  // ---- Profile validation ----
  for (const [slug, profile] of Object.entries(profilesBundle.profiles || {})) {
    if (!modelSlugs.has(profile.model)) {
      issues.push({
        level: "error",
        collection: "llm_profiles",
        slug,
        message: `References model "${profile.model}" which does not exist`,
      });
    }

    checkJsonbKeys(issues, "llm_profiles", slug, "model_settings", profile.model_settings, KNOWN_MODEL_SETTINGS_KEYS);
  }

  // ---- Preset validation ----
  for (const [slug, preset] of Object.entries(presetsBundle || {})) {
    if (preset.preset && !presetSlugs.has(preset.preset)) {
      issues.push({
        level: "error",
        collection: "llm_presets",
        slug,
        message: `References base preset "${preset.preset}" which does not exist`,
      });
    }

    if (preset.default_profile && !profileSlugs.has(preset.default_profile)) {
      issues.push({
        level: "error",
        collection: "llm_presets",
        slug,
        message: `References default profile "${preset.default_profile}" which does not exist`,
      });
    }

    if (preset.routes && typeof preset.routes === "object") {
      for (const [routeName, routeSelection] of Object.entries(preset.routes)) {
        if (!VALID_ROUTES.has(routeName)) {
          issues.push({
            level: "error",
            collection: "llm_presets",
            slug,
            message: `Invalid route "${routeName}" in routes. Must be one of: ${[...VALID_ROUTES].join(", ")}`,
          });
        }

        if (routeSelection.profile && !profileSlugs.has(routeSelection.profile)) {
          issues.push({
            level: "error",
            collection: "llm_presets",
            slug,
            message: `Route "${routeName}" references profile "${routeSelection.profile}" which does not exist`,
          });
        }

        if (Array.isArray(routeSelection.fallback_profiles)) {
          for (const fbSlug of routeSelection.fallback_profiles) {
            if (!profileSlugs.has(fbSlug)) {
              issues.push({
                level: "error",
                collection: "llm_presets",
                slug,
                message: `Route "${routeName}" fallback references profile "${fbSlug}" which does not exist`,
              });
            }
          }
        }
      }
    }
  }

  // ---- Ask option validation ----
  const options = askOptionsBundle?.options || {};
  for (const [slug, option] of Object.entries(options)) {
    if (option.selection && typeof option.selection === "object") {
      if (option.selection.preset && !presetSlugs.has(option.selection.preset)) {
        issues.push({
          level: "error",
          collection: "llm_ask_options",
          slug,
          message: `References preset "${option.selection.preset}" which does not exist`,
        });
      }

      if (option.selection.default_profile && !profileSlugs.has(option.selection.default_profile)) {
        issues.push({
          level: "error",
          collection: "llm_ask_options",
          slug,
          message: `References default_profile "${option.selection.default_profile}" which does not exist`,
        });
      }

      if (option.selection.routes && typeof option.selection.routes === "object") {
        for (const [routeName, routeSelection] of Object.entries(option.selection.routes)) {
          if (routeSelection.profile && !profileSlugs.has(routeSelection.profile)) {
            issues.push({
              level: "error",
              collection: "llm_ask_options",
              slug,
              message: `Route "${routeName}" references profile "${routeSelection.profile}" which does not exist`,
            });
          }
        }
      }
    }
  }

  // ---- Embedding / Rerank coverage check ----
  const embeddingProviders = Object.entries(profilesBundle.providers || {})
    .filter(([, p]) => p.adapter === "dashscope_embedding");
  const rerankProviders = Object.entries(profilesBundle.providers || {})
    .filter(([, p]) => p.adapter === "dashscope_rerank");

  if (embeddingProviders.length > 0) {
    const embeddingProviderSlugs = new Set(embeddingProviders.map(([s]) => s));
    const hasEmbeddingProfile = Object.values(profilesBundle.profiles || {}).some((p) => {
      const model = profilesBundle.models?.[p.model];
      return model && embeddingProviderSlugs.has(model.provider);
    });

    if (!hasEmbeddingProfile) {
      issues.push({
        level: "warn",
        collection: "llm_providers",
        slug: embeddingProviders[0][0],
        message: "dashscope_embedding provider exists but no active profile uses it. rag_embedding route may not resolve.",
      });
    }
  }

  if (rerankProviders.length > 0) {
    const rerankProviderSlugs = new Set(rerankProviders.map(([s]) => s));
    const hasRerankProfile = Object.values(profilesBundle.profiles || {}).some((p) => {
      const model = profilesBundle.models?.[p.model];
      return model && rerankProviderSlugs.has(model.provider);
    });

    if (!hasRerankProfile) {
      issues.push({
        level: "warn",
        collection: "llm_providers",
        slug: rerankProviders[0][0],
        message: "dashscope_rerank provider exists but no active profile uses it. rag_rerank route may not resolve.",
      });
    }
  }

  const valid = !issues.some((i) => i.level === "error");
  return { issues, valid };
}

/**
 * Check JSONB field keys against known keys and warn on unknown ones.
 */
function checkJsonbKeys(issues, collection, slug, fieldName, value, knownKeys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;

  for (const key of Object.keys(value)) {
    if (key.startsWith("_")) continue; // comment keys are allowed
    if (!knownKeys.has(key)) {
      issues.push({
        level: "warn",
        collection,
        slug,
        message: `${fieldName} has unknown key "${key}". Known keys: ${[...knownKeys].join(", ")}`,
      });
    }
  }
}

/**
 * Format validation issues for display.
 *
 * @param {ValidationIssue[]} issues
 * @returns {string}
 */
export function formatValidationIssues(issues) {
  return issues
    .map((i) => `[${i.level.toUpperCase()}] ${i.collection}/${i.slug}: ${i.message}`)
    .join("\n");
}
