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

const ASK_OPTION_ROUTES = new Set([
  "reader_ask",
  "reader_ask_planner",
  "reader_ask_replan",
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

const KNOWN_SELECTION_KEYS = new Set([
  "preset",
  "default_profile",
  "routes",
]);

const KNOWN_ROUTE_SELECTION_KEYS = new Set([
  "profile",
  "fallback_profiles",
  "model_settings",
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

    validateSelectionRoutes({
      issues,
      collection: "llm_presets",
      slug,
      routes: preset.routes,
      profileSlugs,
    });
  }

  // ---- Ask option validation ----
  const options = askOptionsBundle?.options || {};
  if (askOptionsBundle?.default_option && !options[askOptionsBundle.default_option]) {
    issues.push({
      level: "error",
      collection: "llm_ask_options",
      slug: askOptionsBundle.default_option,
      message: "default_option references an option key that does not exist",
    });
  }

  if (
    Object.prototype.hasOwnProperty.call(askOptionsBundle || {}, "billing_defaults") &&
    !isPlainObject(askOptionsBundle.billing_defaults)
  ) {
    issues.push({
      level: "error",
      collection: "llm_ask_options",
      slug: "__bundle__",
      message: "billing_defaults must be an object when provided; use omission to fall back to backend defaults",
    });
  } else if (isPlainObject(askOptionsBundle?.billing_defaults)) {
    const bd = askOptionsBundle.billing_defaults;
    if (bd.tokens_per_point !== undefined && (typeof bd.tokens_per_point !== "number" || bd.tokens_per_point < 1)) {
      issues.push({ level: "error", collection: "llm_ask_options", slug: "__bundle__", message: "billing_defaults.tokens_per_point must be a number >= 1" });
    }
    if (bd.reserved_points !== undefined && (typeof bd.reserved_points !== "number" || bd.reserved_points < 0)) {
      issues.push({ level: "error", collection: "llm_ask_options", slug: "__bundle__", message: "billing_defaults.reserved_points must be a number >= 0" });
    }
    if (bd.billing_policy_version !== undefined && typeof bd.billing_policy_version !== "string") {
      issues.push({ level: "error", collection: "llm_ask_options", slug: "__bundle__", message: "billing_defaults.billing_policy_version must be a string" });
    }
  }

  if (
    Object.prototype.hasOwnProperty.call(askOptionsBundle || {}, "runtime_defaults") &&
    !isPlainObject(askOptionsBundle.runtime_defaults)
  ) {
    issues.push({
      level: "error",
      collection: "llm_ask_options",
      slug: "__bundle__",
      message: "runtime_defaults must be an object when provided; use omission to fall back to backend defaults",
    });
  } else if (isPlainObject(askOptionsBundle?.runtime_defaults)) {
    const rd = askOptionsBundle.runtime_defaults;
    if (rd.max_input_tokens !== undefined && (typeof rd.max_input_tokens !== "number" || rd.max_input_tokens < 1)) {
      issues.push({ level: "error", collection: "llm_ask_options", slug: "__bundle__", message: "runtime_defaults.max_input_tokens must be a number >= 1" });
    }
    if (rd.max_output_tokens !== undefined && (typeof rd.max_output_tokens !== "number" || rd.max_output_tokens < 1)) {
      issues.push({ level: "error", collection: "llm_ask_options", slug: "__bundle__", message: "runtime_defaults.max_output_tokens must be a number >= 1" });
    }
    if (rd.prompt_buffer_tokens !== undefined && (typeof rd.prompt_buffer_tokens !== "number" || rd.prompt_buffer_tokens < 0)) {
      issues.push({ level: "error", collection: "llm_ask_options", slug: "__bundle__", message: "runtime_defaults.prompt_buffer_tokens must be a number >= 0" });
    }
  }

  for (const [slug, option] of Object.entries(options)) {
    // Per-option field validation
    if (option.price_multiplier !== undefined && (typeof option.price_multiplier !== "number" || option.price_multiplier <= 0)) {
      issues.push({ level: "error", collection: "llm_ask_options", slug, message: "price_multiplier must be a number > 0" });
    }
    if (isPlainObject(option.runtime_budget)) {
      const rb = option.runtime_budget;
      if (rb.max_input_tokens !== undefined && (typeof rb.max_input_tokens !== "number" || rb.max_input_tokens < 1)) {
        issues.push({ level: "error", collection: "llm_ask_options", slug, message: "runtime_budget.max_input_tokens must be a number >= 1" });
      }
      if (rb.max_output_tokens !== undefined && (typeof rb.max_output_tokens !== "number" || rb.max_output_tokens < 1)) {
        issues.push({ level: "error", collection: "llm_ask_options", slug, message: "runtime_budget.max_output_tokens must be a number >= 1" });
      }
      if (rb.prompt_buffer_tokens !== undefined && (typeof rb.prompt_buffer_tokens !== "number" || rb.prompt_buffer_tokens < 0)) {
        issues.push({ level: "error", collection: "llm_ask_options", slug, message: "runtime_budget.prompt_buffer_tokens must be a number >= 0" });
      }
    }

    if (option.selection && typeof option.selection === "object") {
      validateSelectionShape({
        issues,
        collection: "llm_ask_options",
        slug,
        selection: option.selection,
        profileSlugs,
        presetSlugs,
        allowedRoutes: ASK_OPTION_ROUTES,
      });

      if (option.enabled) {
        for (const routeName of ASK_OPTION_ROUTES) {
          const resolvedProfile = resolveSelectionProfile(routeName, option.selection, presetsBundle);
          if (!resolvedProfile) {
            issues.push({
              level: "warn",
              collection: "llm_ask_options",
              slug,
              message: `Enabled Ask option does not resolve ${routeName} from its own selection/preset and will fall back to backend route defaults`,
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

function validateSelectionShape({
  issues,
  collection,
  slug,
  selection,
  profileSlugs,
  presetSlugs,
  allowedRoutes = null,
}) {
  warnUnknownKeys(issues, collection, slug, "selection", selection, KNOWN_SELECTION_KEYS);

  if (selection.preset && !presetSlugs.has(selection.preset)) {
    issues.push({
      level: "error",
      collection,
      slug,
      message: `References preset "${selection.preset}" which does not exist`,
    });
  }

  if (selection.default_profile && !profileSlugs.has(selection.default_profile)) {
    issues.push({
      level: "error",
      collection,
      slug,
      message: `References default_profile "${selection.default_profile}" which does not exist`,
    });
  }

  validateSelectionRoutes({
    issues,
    collection,
    slug,
    routes: selection.routes,
    profileSlugs,
    allowedRoutes,
  });
}

function validateSelectionRoutes({
  issues,
  collection,
  slug,
  routes,
  profileSlugs,
  allowedRoutes = null,
}) {
  if (!routes || typeof routes !== "object" || Array.isArray(routes)) return;

  for (const [routeName, routeSelection] of Object.entries(routes)) {
    if (!VALID_ROUTES.has(routeName)) {
      issues.push({
        level: "error",
        collection,
        slug,
        message: `Invalid route "${routeName}" in routes. Must be one of: ${[...VALID_ROUTES].join(", ")}`,
      });
      continue;
    }

    if (allowedRoutes && !allowedRoutes.has(routeName)) {
      issues.push({
        level: "warn",
        collection,
        slug,
        message: `Route "${routeName}" is outside the Ask option route set and will be ignored by Ask runtime`,
      });
    }

    if (!routeSelection || typeof routeSelection !== "object" || Array.isArray(routeSelection)) {
      issues.push({
        level: "error",
        collection,
        slug,
        message: `Route "${routeName}" must be an object`,
      });
      continue;
    }

    warnUnknownKeys(
      issues,
      collection,
      slug,
      `routes.${routeName}`,
      routeSelection,
      KNOWN_ROUTE_SELECTION_KEYS,
    );

    if (routeSelection.profile && !profileSlugs.has(routeSelection.profile)) {
      issues.push({
        level: "error",
        collection,
        slug,
        message: `Route "${routeName}" references profile "${routeSelection.profile}" which does not exist`,
      });
    }

    if (Array.isArray(routeSelection.fallback_profiles)) {
      for (const fbSlug of routeSelection.fallback_profiles) {
        if (!profileSlugs.has(fbSlug)) {
          issues.push({
            level: "error",
            collection,
            slug,
            message: `Route "${routeName}" fallback references profile "${fbSlug}" which does not exist`,
          });
        }
      }
    }

    checkJsonbKeys(
      issues,
      collection,
      slug,
      `routes.${routeName}.model_settings`,
      routeSelection.model_settings,
      KNOWN_MODEL_SETTINGS_KEYS,
    );
  }
}

function warnUnknownKeys(issues, collection, slug, fieldName, value, knownKeys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;

  for (const key of Object.keys(value)) {
    if (key.startsWith("_")) continue;
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

function isPlainObject(value) {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function resolveSelectionProfile(routeName, selection, presetsBundle, seenPresets = new Set()) {
  if (!selection || typeof selection !== "object") return null;

  const preset = loadPresetSelection(selection.preset, presetsBundle, seenPresets);
  const presetRoute = preset?.routes?.[routeName] ?? null;
  const routeOverride = selection.routes?.[routeName] ?? null;

  if (routeOverride?.profile) return routeOverride.profile;
  if (presetRoute?.profile) return presetRoute.profile;
  if (selection.default_profile) return selection.default_profile;
  if (preset?.default_profile) return preset.default_profile;
  return null;
}

function loadPresetSelection(presetSlug, presetsBundle, seenPresets = new Set()) {
  if (!presetSlug) return null;
  if (seenPresets.has(presetSlug)) return null;

  const preset = presetsBundle?.[presetSlug];
  if (!preset) return null;

  seenPresets.add(presetSlug);
  const basePreset = loadPresetSelection(preset.preset, presetsBundle, seenPresets);
  return {
    default_profile: preset.default_profile ?? basePreset?.default_profile ?? null,
    routes: {
      ...(basePreset?.routes ?? {}),
      ...(preset.routes ?? {}),
    },
  };
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
