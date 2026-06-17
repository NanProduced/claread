#!/usr/bin/env node

/**
 * Tests for import-llm-config-core.mjs
 *
 * Directly tests the production import functions with an in-memory mock API.
 * Covers: first import, idempotent re-import, convergent sync (field clearing),
 * ask config round-trip, preset base_preset clearing.
 */

import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  importProviders,
  importModels,
  importProfiles,
  importPresets,
  importAskOptions,
  importAskConfig,
} from "../import-llm-config-core.mjs";

// ---------------------------------------------------------------------------
// In-memory mock Directus API
// ---------------------------------------------------------------------------

function createMockApi() {
  const store = {};
  let autoId = 1;

  function getMap(collection) {
    if (!store[collection]) store[collection] = new Map();
    return store[collection];
  }

  return {
    store,

    async getItems(collection, params = {}) {
      const map = getMap(collection);
      let items = Array.from(map.values());

      const slugFilter = params["filter[slug][_eq]"];
      if (slugFilter) {
        items = items.filter(item => item.slug === slugFilter);
      }

      const limit = params.limit ? parseInt(params.limit) : items.length;
      items = items.slice(0, limit);

      const fields = params.fields;
      if (fields) {
        const fieldList = Array.isArray(fields) ? fields : typeof fields === "string" ? fields.split(",") : [fields];
        items = items.map(item => {
          const result = {};
          for (const f of fieldList) {
            if (f === "*" || f === "id") result.id = item.id;
            if (f === "slug") result.slug = item.slug;
          }
          return result;
        });
      }

      return items;
    },

    async createItem(collection, data) {
      const map = getMap(collection);
      const id = String(autoId++);
      const item = { id, ...data };
      map.set(id, item);
      return item;
    },

    async updateItem(collection, id, data) {
      const map = getMap(collection);
      const existing = map.get(id);
      if (!existing) throw new Error(`Item ${id} not found in ${collection}`);
      const updated = { ...existing, ...data };
      map.set(id, updated);
      return updated;
    },
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getBySlug(api, collection, slug) {
  for (const item of api.store[collection]?.values() ?? []) {
    if (item.slug === slug) return item;
  }
  return null;
}

function getFirst(api, collection) {
  for (const item of api.store[collection]?.values() ?? []) return item;
  return null;
}

function countItems(api, collection) {
  return api.store[collection]?.size ?? 0;
}

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const minimalProfilesDoc = {
  providers: {
    test_provider: {
      adapter: "openai_compatible",
      base_url: "https://api.test.com/v1",
      api_key_env: "TEST_API_KEY",
    },
  },
  models: {
    test_model: {
      provider: "test_provider",
      model_name: "test-model-v1",
    },
  },
  profiles: {
    test_profile: {
      model: "test_model",
    },
  },
};

const minimalPresetsDoc = {
  test_preset: {
    default_profile: "test_profile",
    routes: { reader_ask: { profile: "test_profile" } },
  },
};

const minimalAskOptionsDoc = {
  default_option: "test-option",
  billing_defaults: {
    reserved_points: 10,
    tokens_per_point: 1000,
    billing_policy_version: "analysis_weighted_tokens_v1",
  },
  runtime_defaults: {
    max_input_tokens: 24000,
    max_output_tokens: 3200,
    prompt_buffer_tokens: 800,
  },
  options: {
    "test-option": {
      label: "Test Option",
      description: "A test option",
      selection: { routes: { reader_ask: { profile: "test_profile" } } },
      price_multiplier: 1.0,
      enabled: true,
    },
  },
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("import-llm-config-core", () => {
  let api;

  beforeEach(() => {
    api = createMockApi();
  });

  describe("first import", () => {
    it("creates providers", async () => {
      const ids = await importProviders(api, minimalProfilesDoc.providers);
      assert.equal(ids.size, 1);
      assert.equal(countItems(api, "llm_providers"), 1);
      const p = getBySlug(api, "llm_providers", "test_provider");
      assert.equal(p.adapter, "openai_compatible");
      assert.equal(p.base_url, "https://api.test.com/v1");
      assert.equal(p.status, "active");
    });

    it("creates models with FK resolved", async () => {
      const providerIds = await importProviders(api, minimalProfilesDoc.providers);
      const ids = await importModels(api, minimalProfilesDoc.models, providerIds);
      assert.equal(ids.size, 1);
      const m = getBySlug(api, "llm_models", "test_model");
      assert.equal(m.model_name, "test-model-v1");
      // provider FK should be resolved to UUID, not slug
      assert.notEqual(m.provider, "test_provider");
    });

    it("creates profiles with FK resolved", async () => {
      const providerIds = await importProviders(api, minimalProfilesDoc.providers);
      const modelIds = await importModels(api, minimalProfilesDoc.models, providerIds);
      const ids = await importProfiles(api, minimalProfilesDoc.profiles, modelIds);
      assert.equal(ids.size, 1);
      const p = getBySlug(api, "llm_profiles", "test_profile");
      assert.notEqual(p.model, "test_model");
    });

    it("creates presets with default_profile FK resolved", async () => {
      const providerIds = await importProviders(api, minimalProfilesDoc.providers);
      const modelIds = await importModels(api, minimalProfilesDoc.models, providerIds);
      const profileIds = await importProfiles(api, minimalProfilesDoc.profiles, modelIds);
      const ids = await importPresets(api, minimalPresetsDoc, profileIds);
      assert.equal(ids.size, 1);
      const p = getBySlug(api, "llm_presets", "test_preset");
      assert.notEqual(p.default_profile, "test_profile");
      assert.deepEqual(p.routes, { reader_ask: { profile: "test_profile" } });
    });

    it("creates ask options", async () => {
      await importAskOptions(api, minimalAskOptionsDoc);
      assert.equal(countItems(api, "llm_ask_options"), 1);
      const opt = getBySlug(api, "llm_ask_options", "test-option");
      assert.equal(opt.label, "Test Option");
      assert.equal(opt.description, "A test option");
      assert.equal(opt.enabled, true);
    });

    it("creates ask config singleton", async () => {
      await importAskConfig(api, minimalAskOptionsDoc);
      assert.equal(countItems(api, "llm_ask_config"), 1);
      const cfg = getFirst(api, "llm_ask_config");
      assert.equal(cfg.default_option, "test-option");
      assert.deepEqual(cfg.billing_defaults, minimalAskOptionsDoc.billing_defaults);
      assert.deepEqual(cfg.runtime_defaults, minimalAskOptionsDoc.runtime_defaults);
    });
  });

  describe("idempotent re-import", () => {
    it("does not duplicate providers", async () => {
      await importProviders(api, minimalProfilesDoc.providers);
      await importProviders(api, minimalProfilesDoc.providers);
      assert.equal(countItems(api, "llm_providers"), 1);
    });

    it("does not duplicate models", async () => {
      const pids = await importProviders(api, minimalProfilesDoc.providers);
      await importModels(api, minimalProfilesDoc.models, pids);
      await importModels(api, minimalProfilesDoc.models, pids);
      assert.equal(countItems(api, "llm_models"), 1);
    });

    it("does not duplicate profiles", async () => {
      const pids = await importProviders(api, minimalProfilesDoc.providers);
      const mids = await importModels(api, minimalProfilesDoc.models, pids);
      await importProfiles(api, minimalProfilesDoc.profiles, mids);
      await importProfiles(api, minimalProfilesDoc.profiles, mids);
      assert.equal(countItems(api, "llm_profiles"), 1);
    });

    it("does not duplicate presets", async () => {
      const pids = await importProviders(api, minimalProfilesDoc.providers);
      const mids = await importModels(api, minimalProfilesDoc.models, pids);
      const profIds = await importProfiles(api, minimalProfilesDoc.profiles, mids);
      await importPresets(api, minimalPresetsDoc, profIds);
      await importPresets(api, minimalPresetsDoc, profIds);
      assert.equal(countItems(api, "llm_presets"), 1);
    });

    it("does not duplicate ask options", async () => {
      await importAskOptions(api, minimalAskOptionsDoc);
      await importAskOptions(api, minimalAskOptionsDoc);
      assert.equal(countItems(api, "llm_ask_options"), 1);
    });

    it("does not duplicate ask config singleton", async () => {
      await importAskConfig(api, minimalAskOptionsDoc);
      await importAskConfig(api, minimalAskOptionsDoc);
      assert.equal(countItems(api, "llm_ask_config"), 1);
    });
  });

  describe("convergent sync (field clearing)", () => {
    it("clears provider base_url when removed from JSON", async () => {
      await importProviders(api, minimalProfilesDoc.providers);
      let p = getBySlug(api, "llm_providers", "test_provider");
      assert.equal(p.base_url, "https://api.test.com/v1");

      // Re-import without base_url
      const updated = {
        test_provider: {
          adapter: "openai_compatible",
          api_key_env: "TEST_API_KEY",
        },
      };
      await importProviders(api, updated);
      p = getBySlug(api, "llm_providers", "test_provider");
      assert.equal(p.base_url, "");
    });

    it("clears provider provider_options to empty object when removed from JSON", async () => {
      const withOptions = {
        test_provider: {
          adapter: "openai_compatible",
          base_url: "https://api.test.com/v1",
          api_key_env: "TEST_API_KEY",
          provider_options: { transport: "sse" },
        },
      };
      await importProviders(api, withOptions);
      let p = getBySlug(api, "llm_providers", "test_provider");
      assert.deepEqual(p.provider_options, { transport: "sse" });

      const withoutOptions = {
        test_provider: {
          adapter: "openai_compatible",
          base_url: "https://api.test.com/v1",
          api_key_env: "TEST_API_KEY",
        },
      };
      await importProviders(api, withoutOptions);
      p = getBySlug(api, "llm_providers", "test_provider");
      assert.deepEqual(p.provider_options, {});
    });

    it("clears provider openai_profile when removed from JSON", async () => {
      const withProfile = {
        test_provider: {
          adapter: "openai_compatible",
          openai_profile: { thinking: true },
        },
      };
      await importProviders(api, withProfile);
      let p = getBySlug(api, "llm_providers", "test_provider");
      assert.deepEqual(p.openai_profile, { thinking: true });

      const withoutProfile = {
        test_provider: {
          adapter: "openai_compatible",
        },
      };
      await importProviders(api, withoutProfile);
      p = getBySlug(api, "llm_providers", "test_provider");
      assert.equal(p.openai_profile, null);
    });

    it("clears model provider_options when removed from JSON", async () => {
      const pids = await importProviders(api, minimalProfilesDoc.providers);
      const withOpts = {
        test_model: {
          provider: "test_provider",
          model_name: "test-model-v1",
          provider_options: { dimension: 1024 },
        },
      };
      await importModels(api, withOpts, pids);
      let m = getBySlug(api, "llm_models", "test_model");
      assert.deepEqual(m.provider_options, { dimension: 1024 });

      const withoutOpts = {
        test_model: {
          provider: "test_provider",
          model_name: "test-model-v1",
        },
      };
      await importModels(api, withoutOpts, pids);
      m = getBySlug(api, "llm_models", "test_model");
      assert.equal(m.provider_options, null);
    });

    it("clears profile model_settings when removed from JSON", async () => {
      const pids = await importProviders(api, minimalProfilesDoc.providers);
      const mids = await importModels(api, minimalProfilesDoc.models, pids);
      const withSettings = {
        test_profile: {
          model: "test_model",
          model_settings: { temperature: 0.7 },
        },
      };
      await importProfiles(api, withSettings, mids);
      let p = getBySlug(api, "llm_profiles", "test_profile");
      assert.deepEqual(p.model_settings, { temperature: 0.7 });

      const withoutSettings = {
        test_profile: {
          model: "test_model",
        },
      };
      await importProfiles(api, withoutSettings, mids);
      p = getBySlug(api, "llm_profiles", "test_profile");
      assert.equal(p.model_settings, null);
    });

    it("clears ask option description when removed from JSON", async () => {
      await importAskOptions(api, minimalAskOptionsDoc);
      let opt = getBySlug(api, "llm_ask_options", "test-option");
      assert.equal(opt.description, "A test option");

      const withoutDesc = {
        ...minimalAskOptionsDoc,
        options: {
          "test-option": {
            label: "Test Option",
            selection: { routes: { reader_ask: { profile: "test_profile" } } },
            price_multiplier: 1.0,
            enabled: true,
          },
        },
      };
      await importAskOptions(api, withoutDesc);
      opt = getBySlug(api, "llm_ask_options", "test-option");
      assert.equal(opt.description, "");
    });

    it("clears ask option runtime_budget when removed from JSON", async () => {
      const withBudget = {
        default_option: "test-option",
        options: {
          "test-option": {
            label: "Test Option",
            runtime_budget: { max_input_tokens: 24000 },
            enabled: true,
          },
        },
      };
      await importAskOptions(api, withBudget);
      let opt = getBySlug(api, "llm_ask_options", "test-option");
      assert.deepEqual(opt.runtime_budget, { max_input_tokens: 24000 });

      const withoutBudget = {
        default_option: "test-option",
        options: {
          "test-option": {
            label: "Test Option",
            enabled: true,
          },
        },
      };
      await importAskOptions(api, withoutBudget);
      opt = getBySlug(api, "llm_ask_options", "test-option");
      assert.equal(opt.runtime_budget, null);
    });
  });

  describe("preset base_preset clearing", () => {
    it("clears base_preset FK when preset.preset is removed from JSON", async () => {
      const pids = await importProviders(api, minimalProfilesDoc.providers);
      const mids = await importModels(api, minimalProfilesDoc.models, pids);
      const profIds = await importProfiles(api, minimalProfilesDoc.profiles, mids);

      // First import: child_preset inherits from test_preset
      const withInheritance = {
        test_preset: {
          default_profile: "test_profile",
          routes: {},
        },
        child_preset: {
          preset: "test_preset",
          routes: { reader_ask: { profile: "test_profile" } },
        },
      };
      await importPresets(api, withInheritance, profIds);
      let child = getBySlug(api, "llm_presets", "child_preset");
      assert.notEqual(child.base_preset, null);

      // Re-import: child_preset no longer inherits
      const withoutInheritance = {
        test_preset: {
          default_profile: "test_profile",
          routes: {},
        },
        child_preset: {
          routes: { reader_ask: { profile: "test_profile" } },
        },
      };
      await importPresets(api, withoutInheritance, profIds);
      child = getBySlug(api, "llm_presets", "child_preset");
      assert.equal(child.base_preset, null);
    });

    it("clears default_profile FK when removed from JSON", async () => {
      const pids = await importProviders(api, minimalProfilesDoc.providers);
      const mids = await importModels(api, minimalProfilesDoc.models, pids);
      const profIds = await importProfiles(api, minimalProfilesDoc.profiles, mids);

      await importPresets(api, minimalPresetsDoc, profIds);
      let p = getBySlug(api, "llm_presets", "test_preset");
      assert.notEqual(p.default_profile, null);

      const withoutDefault = {
        test_preset: {
          routes: { reader_ask: { profile: "test_profile" } },
        },
      };
      await importPresets(api, withoutDefault, profIds);
      p = getBySlug(api, "llm_presets", "test_preset");
      assert.equal(p.default_profile, null);
    });
  });

  describe("ask config round-trip", () => {
    it("imports and reads back default_option", async () => {
      await importAskConfig(api, minimalAskOptionsDoc);
      const cfg = getFirst(api, "llm_ask_config");
      assert.equal(cfg.default_option, "test-option");
    });

    it("imports and reads back billing_defaults", async () => {
      await importAskConfig(api, minimalAskOptionsDoc);
      const cfg = getFirst(api, "llm_ask_config");
      assert.deepEqual(cfg.billing_defaults, {
        reserved_points: 10,
        tokens_per_point: 1000,
        billing_policy_version: "analysis_weighted_tokens_v1",
      });
    });

    it("imports and reads back runtime_defaults", async () => {
      await importAskConfig(api, minimalAskOptionsDoc);
      const cfg = getFirst(api, "llm_ask_config");
      assert.deepEqual(cfg.runtime_defaults, {
        max_input_tokens: 24000,
        max_output_tokens: 3200,
        prompt_buffer_tokens: 800,
      });
    });

    it("updates ask config on re-import", async () => {
      await importAskConfig(api, minimalAskOptionsDoc);
      const updated = {
        ...minimalAskOptionsDoc,
        default_option: "other-option",
        billing_defaults: { reserved_points: 20 },
        runtime_defaults: { max_input_tokens: 48000 },
      };
      await importAskConfig(api, updated);
      const cfg = getFirst(api, "llm_ask_config");
      assert.equal(cfg.default_option, "other-option");
      assert.deepEqual(cfg.billing_defaults, { reserved_points: 20 });
      assert.deepEqual(cfg.runtime_defaults, { max_input_tokens: 48000 });
      assert.equal(countItems(api, "llm_ask_config"), 1);
    });

    it("clears ask config fields when removed from JSON", async () => {
      await importAskConfig(api, minimalAskOptionsDoc);
      const empty = { options: {} };
      await importAskConfig(api, empty);
      const cfg = getFirst(api, "llm_ask_config");
      assert.equal(cfg.default_option, null);
      assert.equal(cfg.billing_defaults, null);
      assert.equal(cfg.runtime_defaults, null);
    });
  });

  describe("FK validation", () => {
    it("throws when model references unknown provider", async () => {
      await assert.rejects(
        () => importModels(api, { bad_model: { provider: "nonexistent", model_name: "x" } }, new Map()),
        /references provider "nonexistent" which was not imported/,
      );
    });

    it("throws when profile references unknown model", async () => {
      await assert.rejects(
        () => importProfiles(api, { bad_profile: { model: "nonexistent" } }, new Map()),
        /references model "nonexistent" which was not imported/,
      );
    });

    it("throws when preset references unknown default_profile", async () => {
      await assert.rejects(
        () => importPresets(api, { bad_preset: { default_profile: "nonexistent" } }, new Map()),
        /references default_profile "nonexistent" which was not imported/,
      );
    });

    it("throws when preset references unknown base_preset", async () => {
      await assert.rejects(
        () => importPresets(api, { bad_preset: { preset: "nonexistent" } }, new Map([["bad_preset", "id1"]])),
        /references base_preset "nonexistent" which was not imported/,
      );
    });
  });
});
