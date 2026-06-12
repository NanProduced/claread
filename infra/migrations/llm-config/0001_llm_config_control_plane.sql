CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- LLM Config / Directus 控制面表
-- 说明：
-- - 本文件只负责 LLM 配置控制面 PostgreSQL 物理表
-- - 不负责 Directus metadata（directus_collections / fields / permissions）
-- - 不负责 Claread 业务表
-- - 本文件描述 LLM 配置 authoring 控制面表
-- - Directus 是控制面，不是运行时真源；services/api 不直接 live 读这些表
-- - 通过 export-llm-config-bundle.mjs 导出为 JSON bundle 后供 services/api 使用
-- ============================================================

-- ------------------------------------------------------------
-- Providers: 供应商连接配置（transport / auth）
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_providers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9_]+$'),
    adapter TEXT NOT NULL CHECK (adapter IN (
        'openai_compatible',
        'dashscope_native',
        'dashscope_embedding',
        'dashscope_rerank'
    )),
    base_url TEXT NOT NULL DEFAULT '',
    api_key_env TEXT NOT NULL DEFAULT '',
    provider_options JSONB NOT NULL DEFAULT '{}'::jsonb,
    openai_profile JSONB,
    model_settings JSONB,
    note TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'deprecated'))
);

CREATE INDEX IF NOT EXISTS idx_llm_providers_status
    ON llm_providers (status, sort);

COMMENT ON TABLE llm_providers IS
    'LLM provider definitions. Control-plane authoring only; not read at runtime by services/api.';
COMMENT ON COLUMN llm_providers.slug IS
    'Unique key name used as JSON key when exporting. Must match [a-z0-9_]+.';
COMMENT ON COLUMN llm_providers.adapter IS
    'Transport adapter type. Must match ModelAdapter Literal in services/api/app/llm/types.py.';
COMMENT ON COLUMN llm_providers.base_url IS
    'Required for openai_compatible. Empty for dashscope_native/embedding/rerank.';
COMMENT ON COLUMN llm_providers.api_key_env IS
    'Environment variable name containing the API key.';
COMMENT ON COLUMN llm_providers.provider_options IS
    'Provider-level extension options (e.g. dimension, transport, profile hint).';
COMMENT ON COLUMN llm_providers.openai_profile IS
    'OpenAI compatibility capability declaration (json_object, thinking, tool_choice, etc.).';
COMMENT ON COLUMN llm_providers.model_settings IS
    'Provider-level default model settings (temperature, max_tokens, etc.).';
COMMENT ON COLUMN llm_providers.status IS
    'Lifecycle: draft → active → deprecated. Only active records are exported.';

-- ------------------------------------------------------------
-- Models: 远端模型定义
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9_-]+$'),
    provider UUID NOT NULL REFERENCES llm_providers(id) ON DELETE RESTRICT,
    model_name TEXT NOT NULL,
    model_settings JSONB,
    provider_options JSONB,
    openai_profile JSONB,
    note TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'deprecated'))
);

CREATE INDEX IF NOT EXISTS idx_llm_models_provider
    ON llm_models (provider, status, sort);
CREATE INDEX IF NOT EXISTS idx_llm_models_slug
    ON llm_models (slug);

COMMENT ON TABLE llm_models IS
    'LLM model definitions. Each model references a provider and declares the remote model name.';
COMMENT ON COLUMN llm_models.slug IS
    'Unique key name used as JSON key when exporting. Must match [a-z0-9_-]+.';
COMMENT ON COLUMN llm_models.provider IS
    'FK to llm_providers. Determines transport adapter and auth.';
COMMENT ON COLUMN llm_models.model_name IS
    'Remote model name as recognized by the provider API.';
COMMENT ON COLUMN llm_models.model_settings IS
    'Model-level default settings, override provider-level defaults.';
COMMENT ON COLUMN llm_models.provider_options IS
    'Model-level extension options, override provider-level options.';
COMMENT ON COLUMN llm_models.openai_profile IS
    'Model-level OpenAI compatibility override, takes precedence over provider-level.';

-- ------------------------------------------------------------
-- Profiles: 场景级配置
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9_-]+$'),
    model UUID NOT NULL REFERENCES llm_models(id) ON DELETE RESTRICT,
    model_settings JSONB,
    note TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'deprecated'))
);

CREATE INDEX IF NOT EXISTS idx_llm_profiles_model
    ON llm_profiles (model, status, sort);
CREATE INDEX IF NOT EXISTS idx_llm_profiles_slug
    ON llm_profiles (slug);

COMMENT ON TABLE llm_profiles IS
    'LLM profile definitions. Each profile binds a model to a business scenario with optional settings overrides.';
COMMENT ON COLUMN llm_profiles.slug IS
    'Unique key name used as JSON key when exporting. Must match [a-z0-9_-]+.';
COMMENT ON COLUMN llm_profiles.model IS
    'FK to llm_models. Determines which model this profile uses.';
COMMENT ON COLUMN llm_profiles.model_settings IS
    'Profile-level settings override, highest priority in the provider < model < profile chain.';

-- ------------------------------------------------------------
-- Presets: route → profile 映射集合
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_presets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9_]+$'),
    base_preset UUID REFERENCES llm_presets(id) ON DELETE SET NULL,
    default_profile UUID REFERENCES llm_profiles(id) ON DELETE SET NULL,
    routes JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'deprecated'))
);

CREATE INDEX IF NOT EXISTS idx_llm_presets_slug
    ON llm_presets (slug);

COMMENT ON TABLE llm_presets IS
    'LLM preset definitions. A preset is a named set of route→profile mappings, optionally inheriting from a base preset.';
COMMENT ON COLUMN llm_presets.slug IS
    'Unique key name used as JSON key when exporting.';
COMMENT ON COLUMN llm_presets.base_preset IS
    'Optional FK to another llm_presets entry for inheritance.';
COMMENT ON COLUMN llm_presets.default_profile IS
    'Default profile when a route is not explicitly specified in routes.';
COMMENT ON COLUMN llm_presets.routes IS
    'Route→selection mapping. Format: {route_name: {profile: slug, fallback_profiles: [...], model_settings: {...}}}. Route names must match ModelRoute Literal in services/api/app/llm/routes.py.';

-- ------------------------------------------------------------
-- Ask Options: Ask Claread 用户可选模型档位
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_ask_options (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date_created TIMESTAMPTZ NOT NULL DEFAULT now(),
    date_updated TIMESTAMPTZ,
    user_created UUID,
    user_updated UUID,

    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9_-]+$'),
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    selection JSONB,
    price_multiplier NUMERIC NOT NULL DEFAULT 1.0,
    runtime_budget JSONB,
    enabled BOOLEAN NOT NULL DEFAULT true,
    sort INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_ask_options_enabled
    ON llm_ask_options (enabled, sort);

COMMENT ON TABLE llm_ask_options IS
    'Ask Claread model option definitions. Each option represents a user-selectable model tier in the Ask panel.';
COMMENT ON COLUMN llm_ask_options.slug IS
    'Unique key name used as JSON key when exporting. Persisted to reader_ask_threads.selected_model_key.';
COMMENT ON COLUMN llm_ask_options.selection IS
    'ModelSelection object. Can reference a preset slug or define routes directly. Null means use backend defaults.';
COMMENT ON COLUMN llm_ask_options.price_multiplier IS
    'Multiplier applied to billing_defaults for this option.';
COMMENT ON COLUMN llm_ask_options.runtime_budget IS
    'Optional per-option runtime budget overrides (max_input_tokens, max_output_tokens, prompt_buffer_tokens).';
