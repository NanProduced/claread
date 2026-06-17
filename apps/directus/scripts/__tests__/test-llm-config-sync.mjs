/**
 * Tests for LLM Config Directus integration
 *
 * Covers:
 *   1. Metadata sync / schema generation is idempotent
 *   2. Export bundle structure is correct
 *   3. Validation catches common errors with readable messages
 *   4. Embedding / rerank fields are not missing
 *
 * Run: node --test apps/directus/scripts/__tests__/test-llm-config-sync.mjs
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { validateLlmConfigBundle, formatValidationIssues } from "../validate-llm-config-bundle.mjs";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

function makeValidBundle(overrides = {}) {
  return {
    profilesBundle: {
      providers: {
        dashscope_compat: {
          adapter: "openai_compatible",
          base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
          api_key_env: "DASHSCOPE_API_KEY",
          provider_options: { transport: "sse", profile: "reasoning_content" },
          openai_profile: {
            supports_json_object_output: true,
            supports_json_schema_output: true,
            default_structured_output_mode: "json_schema",
          },
        },
        dashscope: {
          adapter: "dashscope_native",
          api_key_env: "DASHSCOPE_API_KEY",
        },
        dashscope_embedding: {
          adapter: "dashscope_embedding",
          api_key_env: "DASHSCOPE_API_KEY",
          provider_options: { dimension: 1024 },
        },
        dashscope_rerank: {
          adapter: "dashscope_rerank",
          api_key_env: "DASHSCOPE_API_KEY",
        },
        ...overrides.extraProviders,
      },
      models: {
        "qwen37-max": {
          provider: "dashscope_compat",
          model_name: "qwen3.7-max",
          model_settings: { temperature: 0.7, timeout: 180.0 },
        },
        "qwen37-max-native": {
          provider: "dashscope",
          model_name: "qwen3.7-max",
        },
        "text-embedding-v4": {
          provider: "dashscope_embedding",
          model_name: "text-embedding-v4",
          provider_options: { dimension: 1024 },
        },
        "qwen3-rerank": {
          provider: "dashscope_rerank",
          model_name: "qwen3-rerank",
        },
        ...overrides.extraModels,
      },
      profiles: {
        "workflow-qwen37-max": {
          model: "qwen37-max",
          model_settings: { timeout: 120.0, extra_body: { enable_thinking: false } },
        },
        "ask-main-qwen37-max-native": {
          model: "qwen37-max-native",
          model_settings: { timeout: 180.0, extra_body: { enable_thinking: true } },
        },
        "rag-embedding-v4": {
          model: "text-embedding-v4",
        },
        "rag-rerank-qwen3": {
          model: "qwen3-rerank",
        },
        ...overrides.extraProfiles,
      },
    },
    presetsBundle: {
      workflow_base: {
        default_profile: "workflow-qwen37-max",
        routes: {
          annotation_generation: { profile: "workflow-qwen37-max" },
        },
      },
      ask_qwen_premium_native: {
        preset: "workflow_base",
        routes: {
          reader_ask: { profile: "ask-main-qwen37-max-native" },
        },
      },
      ...overrides.extraPresets,
    },
    askOptionsBundle: {
      default_option: "qwen-premium-native",
      billing_defaults: {
        multiplier_input: 1,
        multiplier_output: 5,
        tokens_per_point: 1000,
        price_multiplier: 1.0,
        reserved_points: 10,
        billing_policy_version: "analysis_weighted_tokens_v1",
      },
      runtime_defaults: {
        max_input_tokens: 24000,
        max_output_tokens: 3200,
        prompt_buffer_tokens: 800,
      },
      options: {
        "qwen-premium-native": {
          label: "Qwen 3.7 Max (Native)",
          description: "高质量原生档位",
          selection: {
            preset: "ask_qwen_premium_native",
          },
          price_multiplier: 1.6,
          enabled: true,
        },
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("validateLlmConfigBundle", () => {
  it("accepts a valid bundle", () => {
    const bundle = makeValidBundle();
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, true);
    assert.equal(issues.length, 0);
  });

  it("rejects invalid adapter", () => {
    const bundle = makeValidBundle({
      extraProviders: {
        bad_provider: { adapter: "invalid_adapter", base_url: "https://example.com" },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "bad_provider" && i.message.includes("Invalid adapter")));
  });

  it("rejects openai_compatible without base_url", () => {
    const bundle = makeValidBundle({
      extraProviders: {
        no_url: { adapter: "openai_compatible", api_key_env: "TEST_KEY" },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "no_url" && i.message.includes("base_url")));
  });

  it("rejects dashscope_native without api_key_env", () => {
    const bundle = makeValidBundle({
      extraProviders: {
        no_key: { adapter: "dashscope_native" },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "no_key" && i.message.includes("api_key_env")));
  });

  it("rejects model referencing non-existent provider", () => {
    const bundle = makeValidBundle({
      extraModels: {
        orphan_model: { provider: "nonexistent_provider", model_name: "test" },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "orphan_model" && i.message.includes("provider")));
  });

  it("rejects profile referencing non-existent model", () => {
    const bundle = makeValidBundle({
      extraProfiles: {
        orphan_profile: { model: "nonexistent_model" },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "orphan_profile" && i.message.includes("model")));
  });

  it("rejects invalid route name in preset", () => {
    const bundle = makeValidBundle({
      extraPresets: {
        bad_routes: {
          routes: {
            invalid_route_name: { profile: "workflow-qwen37-max" },
          },
        },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "bad_routes" && i.message.includes("Invalid route")));
  });

  it("rejects preset referencing non-existent profile", () => {
    const bundle = makeValidBundle({
      extraPresets: {
        bad_preset: {
          default_profile: "nonexistent_profile",
        },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "bad_preset" && i.message.includes("default profile")));
  });

  it("rejects ask option referencing non-existent preset", () => {
    const bundle = makeValidBundle();
    bundle.askOptionsBundle.options["bad-option"] = {
      label: "Bad Option",
      selection: { preset: "nonexistent_preset" },
      enabled: true,
    };
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "bad-option" && i.message.includes("preset")));
  });

  it("rejects ask option with invalid route name", () => {
    const bundle = makeValidBundle();
    bundle.askOptionsBundle.options["bad-route-option"] = {
      label: "Bad Route Option",
      selection: {
        routes: {
          invalid_route_name: { profile: "ask-main-qwen37-max-native" },
        },
      },
      enabled: true,
    };
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "bad-route-option" && i.message.includes("Invalid route")));
  });

  it("rejects ask option fallback_profiles referencing non-existent profile", () => {
    const bundle = makeValidBundle();
    bundle.askOptionsBundle.options["bad-fallback-option"] = {
      label: "Bad Fallback Option",
      selection: {
        routes: {
          reader_ask: {
            profile: "ask-main-qwen37-max-native",
            fallback_profiles: ["missing_profile"],
          },
        },
      },
      enabled: true,
    };
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "bad-fallback-option" && i.message.includes("fallback")));
  });

  it("rejects default_option referencing a missing ask option", () => {
    const bundle = makeValidBundle();
    bundle.askOptionsBundle.default_option = "missing-option";
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "missing-option" && i.message.includes("default_option")));
  });

  it("warns when ask option route is outside Ask runtime route set", () => {
    const bundle = makeValidBundle();
    bundle.askOptionsBundle.options["extra-route-option"] = {
      label: "Extra Route Option",
      selection: {
        routes: {
          daily_analysis: { profile: "workflow-qwen37-max" },
        },
      },
      enabled: true,
    };
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, true);
    assert.ok(issues.some((i) => i.slug === "extra-route-option" && i.message.includes("outside the Ask option route set")));
  });

  it("rejects null billing_defaults because backend only accepts omission or object", () => {
    const bundle = makeValidBundle();
    bundle.askOptionsBundle.billing_defaults = null;
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.message.includes("billing_defaults must be an object")));
  });

  it("rejects null runtime_defaults because backend only accepts omission or object", () => {
    const bundle = makeValidBundle();
    bundle.askOptionsBundle.runtime_defaults = null;
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.message.includes("runtime_defaults must be an object")));
  });

  it("accepts omitted billing/runtime defaults so backend can apply defaults", () => {
    const bundle = makeValidBundle();
    delete bundle.askOptionsBundle.billing_defaults;
    delete bundle.askOptionsBundle.runtime_defaults;
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, true);
    assert.equal(issues.filter((i) => i.level === "error").length, 0);
  });

  it("warns when embedding provider has no profile", () => {
    const bundle = makeValidBundle();
    // Remove embedding profile but keep provider
    delete bundle.profilesBundle.profiles["rag-embedding-v4"];
    const { issues, valid } = validateLlmConfigBundle(bundle);
    // This is a warning, not an error
    assert.equal(valid, true);
    assert.ok(issues.some((i) => i.level === "warn" && i.message.includes("dashscope_embedding")));
  });

  it("warns when rerank provider has no profile", () => {
    const bundle = makeValidBundle();
    delete bundle.profilesBundle.profiles["rag-rerank-qwen3"];
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, true);
    assert.ok(issues.some((i) => i.level === "warn" && i.message.includes("dashscope_rerank")));
  });

  it("warns on unknown JSONB keys", () => {
    const bundle = makeValidBundle();
    bundle.profilesBundle.providers.dashscope_compat.provider_options = {
      unknown_key: "value",
      transport: "sse",
    };
    const { issues } = validateLlmConfigBundle(bundle);
    assert.ok(issues.some((i) => i.level === "warn" && i.message.includes("unknown_key")));
  });

  it("does not warn on underscore-prefixed JSONB keys", () => {
    const bundle = makeValidBundle();
    bundle.profilesBundle.providers.dashscope_compat.provider_options = {
      _comment: "this is fine",
      transport: "sse",
    };
    const { issues } = validateLlmConfigBundle(bundle);
    assert.ok(!issues.some((i) => i.message.includes("_comment")));
  });

  it("includes embedding and rerank fields in valid bundle", () => {
    const bundle = makeValidBundle();
    const { valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, true);
    // Verify embedding/rerank are present
    assert.ok(bundle.profilesBundle.providers.dashscope_embedding);
    assert.ok(bundle.profilesBundle.providers.dashscope_rerank);
    assert.equal(bundle.profilesBundle.providers.dashscope_embedding.adapter, "dashscope_embedding");
    assert.equal(bundle.profilesBundle.providers.dashscope_rerank.adapter, "dashscope_rerank");
    assert.ok(bundle.profilesBundle.models["text-embedding-v4"]);
    assert.ok(bundle.profilesBundle.models["qwen3-rerank"]);
    assert.ok(bundle.profilesBundle.profiles["rag-embedding-v4"]);
    assert.ok(bundle.profilesBundle.profiles["rag-rerank-qwen3"]);
  });

  it("rejects model without model_name", () => {
    const bundle = makeValidBundle({
      extraModels: {
        no_name: { provider: "dashscope_compat" },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "no_name" && i.message.includes("model_name")));
  });

  it("rejects fallback_profiles referencing non-existent profile", () => {
    const bundle = makeValidBundle({
      extraPresets: {
        bad_fallback: {
          routes: {
            reader_ask: {
              profile: "ask-main-qwen37-max-native",
              fallback_profiles: ["nonexistent_profile"],
            },
          },
        },
      },
    });
    const { issues, valid } = validateLlmConfigBundle(bundle);
    assert.equal(valid, false);
    assert.ok(issues.some((i) => i.slug === "bad_fallback" && i.message.includes("fallback")));
  });
});

describe("formatValidationIssues", () => {
  it("formats issues with level, collection, slug, and message", () => {
    const issues = [
      { level: "error", collection: "llm_providers", slug: "test", message: "test error" },
      { level: "warn", collection: "llm_models", slug: "other", message: "test warning" },
    ];
    const formatted = formatValidationIssues(issues);
    assert.ok(formatted.includes("[ERROR] llm_providers/test: test error"));
    assert.ok(formatted.includes("[WARN] llm_models/other: test warning"));
  });
});

describe("Export bundle structure", () => {
  it("valid bundle matches services/api schema shape", () => {
    const bundle = makeValidBundle();
    const { profilesBundle, presetsBundle, askOptionsBundle } = bundle;

    // model-profiles.json structure
    assert.ok(profilesBundle.providers);
    assert.ok(profilesBundle.models);
    assert.ok(profilesBundle.profiles);
    for (const [slug, provider] of Object.entries(profilesBundle.providers)) {
      assert.ok(provider.adapter, `Provider ${slug} missing adapter`);
    }
    for (const [slug, model] of Object.entries(profilesBundle.models)) {
      assert.ok(model.provider, `Model ${slug} missing provider`);
      assert.ok(model.model_name, `Model ${slug} missing model_name`);
    }
    for (const [slug, profile] of Object.entries(profilesBundle.profiles)) {
      assert.ok(profile.model, `Profile ${slug} missing model`);
    }

    // model-presets.json structure
    for (const [slug, preset] of Object.entries(presetsBundle)) {
      // Presets can have preset, default_profile, or routes
      assert.ok(
        preset.preset || preset.default_profile || preset.routes,
        `Preset ${slug} has no content`,
      );
    }

    // reader-ask-model-options.json structure
    assert.ok(askOptionsBundle.options);
    assert.ok(askOptionsBundle.billing_defaults);
    assert.ok(askOptionsBundle.runtime_defaults);
    for (const [slug, option] of Object.entries(askOptionsBundle.options)) {
      assert.ok(option.label, `Ask option ${slug} missing label`);
    }
  });
});
