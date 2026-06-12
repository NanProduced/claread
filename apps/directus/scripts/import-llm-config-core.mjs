/**
 * Core import logic for LLM Config Bundle.
 *
 * This module exports all import functions with an injectable API adapter,
 * so tests can pass mock implementations instead of hitting a real Directus.
 *
 * API adapter shape:
 * {
 *   getItems(collection, params) → array,
 *   createItem(collection, data) → item,
 *   updateItem(collection, id, data) → item,
 * }
 */

/**
 * Import providers into llm_providers.
 * Returns Map<slug, id>.
 */
export async function importProviders(api, providers) {
  const slugToId = new Map();
  let sortIndex = 0;

  for (const [slug, provider] of Object.entries(providers)) {
    const data = {
      slug,
      adapter: provider.adapter,
      status: "active",
      sort: sortIndex++,
      // Convergent sync: map absent values to the storage-layer empty values
      // expected by Directus / PostgreSQL for non-nullable columns.
      base_url: provider.base_url ?? "",
      api_key_env: provider.api_key_env ?? "",
      provider_options: provider.provider_options ?? {},
      openai_profile: provider.openai_profile ?? null,
      model_settings: provider.model_settings ?? null,
      note: provider.note ?? "",
    };

    const id = await upsertBySlug(api, "llm_providers", slug, data);
    slugToId.set(slug, id);
  }

  return slugToId;
}

/**
 * Import models into llm_models.
 * Resolves provider FK by slug.
 * Returns Map<slug, id>.
 */
export async function importModels(api, models, providerIds) {
  const slugToId = new Map();
  let sortIndex = 0;

  for (const [slug, model] of Object.entries(models)) {
    const providerId = providerIds.get(model.provider);
    if (!providerId) {
      throw new Error(`Model "${slug}" references provider "${model.provider}" which was not imported`);
    }

    const data = {
      slug,
      provider: providerId,
      model_name: model.model_name,
      status: "active",
      sort: sortIndex++,
      model_settings: model.model_settings ?? null,
      provider_options: model.provider_options ?? null,
      openai_profile: model.openai_profile ?? null,
      note: model.note ?? "",
    };

    const id = await upsertBySlug(api, "llm_models", slug, data);
    slugToId.set(slug, id);
  }

  return slugToId;
}

/**
 * Import profiles into llm_profiles.
 * Resolves model FK by slug.
 * Returns Map<slug, id>.
 */
export async function importProfiles(api, profiles, modelIds) {
  const slugToId = new Map();
  let sortIndex = 0;

  for (const [slug, profile] of Object.entries(profiles)) {
    const modelId = modelIds.get(profile.model);
    if (!modelId) {
      throw new Error(`Profile "${slug}" references model "${profile.model}" which was not imported`);
    }

    const data = {
      slug,
      model: modelId,
      status: "active",
      sort: sortIndex++,
      model_settings: profile.model_settings ?? null,
      note: profile.note ?? "",
    };

    const id = await upsertBySlug(api, "llm_profiles", slug, data);
    slugToId.set(slug, id);
  }

  return slugToId;
}

/**
 * Import presets into llm_presets.
 * Resolves base_preset and default_profile FKs by slug.
 */
export async function importPresets(api, presets, profileIds) {
  const slugToId = new Map();
  let sortIndex = 0;

  // First pass: create all presets (base_preset deferred to second pass)
  for (const [slug, preset] of Object.entries(presets)) {
    const data = {
      slug,
      status: "active",
      sort: sortIndex++,
      default_profile: preset.default_profile ? (profileIds.get(preset.default_profile) ?? preset.default_profile) : null,
      routes: preset.routes ?? {},
      base_preset: null, // Resolved in second pass
      note: preset.note ?? "",
    };

    if (preset.default_profile && !profileIds.get(preset.default_profile)) {
      throw new Error(`Preset "${slug}" references default_profile "${preset.default_profile}" which was not imported`);
    }

    const id = await upsertBySlug(api, "llm_presets", slug, data);
    slugToId.set(slug, id);
  }

  // Second pass: resolve base_preset self-references (always update, even to null)
  for (const [slug, preset] of Object.entries(presets)) {
    const basePresetId = preset.preset ? slugToId.get(preset.preset) : null;
    if (preset.preset && !basePresetId) {
      throw new Error(`Preset "${slug}" references base_preset "${preset.preset}" which was not imported`);
    }
    const existing = await api.getItems("llm_presets", {
      "filter[slug][_eq]": slug,
      "fields": ["id"],
      "limit": 1,
    });
    if (existing.length > 0) {
      await api.updateItem("llm_presets", existing[0].id, { base_preset: basePresetId ?? null });
    }
  }

  return slugToId;
}

/**
 * Import ask options into llm_ask_options.
 */
export async function importAskOptions(api, askOptionsDoc) {
  const options = askOptionsDoc.options || {};
  let sortIndex = 0;

  for (const [slug, option] of Object.entries(options)) {
    const data = {
      slug,
      label: option.label,
      sort: sortIndex++,
      description: option.description ?? "",
      selection: option.selection ?? null,
      price_multiplier: option.price_multiplier !== undefined ? Number(option.price_multiplier) : 1.0,
      runtime_budget: option.runtime_budget ?? null,
      enabled: option.enabled !== undefined ? option.enabled : true,
    };

    await upsertBySlug(api, "llm_ask_options", slug, data);
  }
}

/**
 * Import Ask top-level config into llm_ask_config singleton.
 */
export async function importAskConfig(api, askOptionsDoc) {
  const data = {
    default_option: askOptionsDoc.default_option ?? null,
    billing_defaults: askOptionsDoc.billing_defaults ?? null,
    runtime_defaults: askOptionsDoc.runtime_defaults ?? null,
  };

  if (typeof api.upsertSingleton === "function") {
    await api.upsertSingleton("llm_ask_config", data);
    return;
  }

  const existing = await api.getItems("llm_ask_config", { limit: 1 });

  if (existing.length > 0) {
    await api.updateItem("llm_ask_config", existing[0].id, data);
  } else {
    await api.createItem("llm_ask_config", data);
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

async function upsertBySlug(api, collection, slug, data) {
  const existing = await api.getItems(collection, {
    "filter[slug][_eq]": slug,
    "fields": ["id"],
    "limit": 1,
  });

  if (existing.length > 0) {
    await api.updateItem(collection, existing[0].id, data);
    return existing[0].id;
  }

  const created = await api.createItem(collection, data);
  return created.id;
}
