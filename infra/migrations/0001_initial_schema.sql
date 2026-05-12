CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 用户与认证
-- ============================================================

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deleted')),
  display_name TEXT,
  avatar_url TEXT,
  locale TEXT NOT NULL DEFAULT 'zh-CN',
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  cumulative_article_count INTEGER NOT NULL DEFAULT 0,
  last_active_at TIMESTAMPTZ,
  settings_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_login_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  provider_user_id TEXT NOT NULL,
  unionid TEXT,
  app_id TEXT,
  auth_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_user_identities_provider_user UNIQUE (provider, provider_user_id)
);

CREATE INDEX idx_user_identities_user_id ON user_identities(user_id);
CREATE INDEX idx_user_identities_unionid ON user_identities(unionid) WHERE unionid IS NOT NULL;

CREATE TABLE user_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_token_hash TEXT NOT NULL UNIQUE,
  refresh_token_hash TEXT UNIQUE,
  client_platform TEXT NOT NULL DEFAULT 'wechat_miniprogram',
  device_id TEXT,
  device_name TEXT,
  app_version TEXT,
  ip_address INET,
  user_agent TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'expired')),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL,
  refresh_expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_sessions_user_id_status ON user_sessions(user_id, status);
CREATE INDEX idx_user_sessions_expires_at ON user_sessions(expires_at);

-- ============================================================
-- 文章分析
-- ============================================================

CREATE TABLE analysis_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_record_id TEXT,
  source_type TEXT NOT NULL DEFAULT 'user_input' CHECK (source_type IN ('user_input', 'daily_article', 'imported', 'ocr')),
  title TEXT,
  source_text TEXT NOT NULL,
  source_text_hash TEXT NOT NULL,
  reading_goal TEXT,
  reading_variant TEXT,
  extended BOOLEAN NOT NULL DEFAULT FALSE,
  user_facing_state TEXT,
  analysis_status TEXT NOT NULL DEFAULT 'ready' CHECK (analysis_status IN (
    'queued', 'running', 'finalizing',
    'ready', 'partial', 'failed',
    'deleted', 'cancelled', 'expired'
  )),
  deleted_at TIMESTAMPTZ,
  deleted_by UUID REFERENCES users(id) ON DELETE SET NULL,
  last_opened_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_analysis_records_client_record UNIQUE (user_id, client_record_id)
);

CREATE INDEX idx_analysis_records_user_created_at
  ON analysis_records(user_id, created_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_analysis_records_user_updated_at
  ON analysis_records(user_id, updated_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_analysis_records_source_hash ON analysis_records(source_text_hash);

CREATE TABLE analysis_results (
  record_id UUID PRIMARY KEY REFERENCES analysis_records(id) ON DELETE CASCADE,
  render_scene_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  page_state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  workflow_version TEXT,
  schema_version TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analysis_results_created ON analysis_results(created_at);

CREATE TABLE analysis_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  analysis_record_id UUID NOT NULL REFERENCES analysis_records(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'finalizing', 'succeeded', 'failed', 'cancelled', 'expired')),
  worker_token TEXT,
  queue_name TEXT NOT NULL DEFAULT 'default',
  attempt_no INTEGER NOT NULL DEFAULT 1,
  failure_code TEXT,
  failure_message TEXT,
  usage_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  quota_cost_points INTEGER NOT NULL DEFAULT 0,
  queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_analysis_tasks_user_active
  ON analysis_tasks(user_id)
  WHERE status IN ('queued', 'running', 'finalizing');
CREATE UNIQUE INDEX uq_analysis_tasks_record ON analysis_tasks(analysis_record_id);
CREATE INDEX idx_analysis_tasks_user_status ON analysis_tasks(user_id, status);
CREATE INDEX idx_analysis_tasks_status_queued_at ON analysis_tasks(status, queued_at);

CREATE TABLE analysis_task_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID NOT NULL REFERENCES analysis_tasks(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  event_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_task_events_task_created ON analysis_task_events(task_id, created_at);

CREATE TABLE analysis_audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  record_id UUID NOT NULL REFERENCES analysis_records(id) ON DELETE CASCADE,
  task_id UUID REFERENCES analysis_tasks(id) ON DELETE SET NULL,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  request_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  usage_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  cost_points INTEGER NOT NULL DEFAULT 0,
  processing_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analysis_audit_logs_record ON analysis_audit_logs(record_id);
CREATE INDEX idx_analysis_audit_logs_user ON analysis_audit_logs(user_id, created_at DESC);

-- ============================================================
-- 积分系统
-- ============================================================

CREATE TABLE user_credit_accounts (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  daily_free_points INTEGER NOT NULL DEFAULT 1000,
  daily_used_points INTEGER NOT NULL DEFAULT 0,
  bonus_points INTEGER NOT NULL DEFAULT 0,
  last_reset_on DATE NOT NULL DEFAULT CURRENT_DATE,
  policy_version TEXT NOT NULL DEFAULT 'v1',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_credit_ledger (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  task_id UUID REFERENCES analysis_tasks(id) ON DELETE SET NULL,
  entry_type TEXT NOT NULL
    CHECK (entry_type IN (
      'daily_grant', 'bonus_grant', 'analysis_deduct',
      'manual_adjust', 'refund', 'feedback_reward'
    )),
  points INTEGER NOT NULL,
  bucket_type TEXT NOT NULL DEFAULT 'daily_free'
    CHECK (bucket_type IN ('daily_free', 'bonus')),
  balance_after INTEGER NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_credit_ledger_user_created ON user_credit_ledger(user_id, created_at DESC);
CREATE INDEX idx_credit_ledger_task ON user_credit_ledger(task_id) WHERE task_id IS NOT NULL;

CREATE TABLE anonymous_quotas (
  anonymous_id TEXT PRIMARY KEY,
  trial_count INTEGER NOT NULL DEFAULT 0,
  last_trial_at DATE NOT NULL DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 用户资产（收藏 / 生词本 / 批注）
-- ============================================================

CREATE TABLE favorite_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type TEXT NOT NULL CHECK (target_type IN ('analysis_record', 'sentence', 'paragraph', 'phrase', 'vocab')),
  target_key TEXT NOT NULL,
  analysis_record_id UUID REFERENCES analysis_records(id) ON DELETE CASCADE,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  note TEXT,
  deleted_at TIMESTAMPTZ,
  deleted_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_favorite_records_target UNIQUE (user_id, target_type, target_key)
);

CREATE INDEX idx_favorite_records_user_created_at
  ON favorite_records(user_id, created_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_favorite_records_analysis_record_id
  ON favorite_records(analysis_record_id)
  WHERE analysis_record_id IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE vocabulary_book (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  lemma TEXT NOT NULL,
  display_word TEXT NOT NULL,
  phonetic TEXT,
  part_of_speech TEXT,
  short_meaning TEXT NOT NULL,
  meanings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  exchange TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  source_provider TEXT NOT NULL DEFAULT 'tecd3',
  dict_entry_id BIGINT,
  source_sentence TEXT,
  source_context TEXT,
  mastery_status TEXT NOT NULL DEFAULT 'new' CHECK (mastery_status IN ('new', 'learning', 'review', 'mastered', 'archived')),
  review_count INTEGER NOT NULL DEFAULT 0,
  last_reviewed_at TIMESTAMPTZ,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_vocabulary_book_user_lemma_lower ON vocabulary_book(user_id, LOWER(lemma));
CREATE INDEX idx_vocabulary_book_user_created_at ON vocabulary_book(user_id, created_at DESC);
CREATE INDEX idx_vocabulary_book_user_mastery_status ON vocabulary_book(user_id, mastery_status);
CREATE INDEX IF NOT EXISTS idx_vocabulary_book_dict_entry_id ON vocabulary_book(dict_entry_id) WHERE dict_entry_id IS NOT NULL;

CREATE TABLE user_annotations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    analysis_record_id UUID REFERENCES analysis_records(id) ON DELETE CASCADE,
    annotation_type TEXT NOT NULL DEFAULT 'highlight'
        CHECK (annotation_type IN ('highlight', 'note')),
    anchor_type TEXT NOT NULL DEFAULT 'sentence'
        CHECK (anchor_type IN ('sentence', 'paragraph', 'text_range')),
    target_key TEXT NOT NULL,
    paragraph_id TEXT,
    sentence_id TEXT,
    selected_text TEXT NOT NULL,
    start_offset INTEGER,
    end_offset INTEGER,
    text_hash TEXT,
    color TEXT NOT NULL DEFAULT 'soft_green'
        CHECK (color IN ('soft_green', 'soft_blue', 'soft_purple', 'warm_yellow', 'sage_green')),
    note TEXT,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_annotations_target UNIQUE (user_id, target_key)
);

CREATE INDEX idx_user_annotations_record_created
    ON user_annotations(user_id, analysis_record_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_user_annotations_sentence
    ON user_annotations(user_id, analysis_record_id, sentence_id)
    WHERE sentence_id IS NOT NULL AND deleted_at IS NULL;

-- ============================================================
-- 反馈系统
-- ============================================================

CREATE TABLE feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  feedback_scope TEXT NOT NULL CHECK (feedback_scope IN (
    'analysis_result', 'annotation', 'sentence', 'dictionary', 'app'
  )),
  target_id TEXT NOT NULL,
  analysis_record_id UUID REFERENCES analysis_records(id) ON DELETE CASCADE,
  sentiment TEXT NOT NULL CHECK (sentiment IN ('positive', 'negative', 'neutral')),
  feedback_type TEXT NOT NULL,
  annotation_type TEXT,
  content TEXT,
  context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  app_version TEXT,
  client_platform TEXT NOT NULL DEFAULT 'wechat_miniprogram',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending', 'adopted', 'resolved', 'dismissed'
  )),
  reward_points INTEGER NOT NULL DEFAULT 0,
  reward_granted_at TIMESTAMPTZ,
  admin_note TEXT,
  reviewed_at TIMESTAMPTZ,
  reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
  rag_harvested BOOLEAN NOT NULL DEFAULT FALSE,
  rag_harvested_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_feedback_user_target_type UNIQUE (user_id, target_id, feedback_type)
);

CREATE INDEX idx_feedback_user_created ON feedback(user_id, created_at DESC);
CREATE INDEX idx_feedback_scope_type ON feedback(feedback_scope, feedback_type);
CREATE INDEX idx_feedback_record ON feedback(analysis_record_id)
  WHERE analysis_record_id IS NOT NULL;
CREATE INDEX idx_feedback_annotation_type ON feedback(annotation_type)
  WHERE annotation_type IS NOT NULL;
CREATE INDEX idx_feedback_sentiment ON feedback(sentiment, feedback_scope);
CREATE INDEX idx_feedback_status ON feedback(status)
  WHERE status = 'pending';
CREATE INDEX idx_feedback_rag_harvested ON feedback(rag_harvested)
  WHERE rag_harvested = FALSE AND feedback_scope IN ('annotation', 'dictionary');
CREATE INDEX idx_feedback_context ON feedback USING GIN(context_json);

-- ============================================================
-- 每日精读
-- ============================================================

CREATE TABLE daily_readers (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  subtitle TEXT,
  source TEXT NOT NULL,
  source_url TEXT NOT NULL,
  publish_date DATE NOT NULL,
  difficulty TEXT NOT NULL CHECK (difficulty IN ('A2', 'B1', 'B2', 'C1')),
  read_time_minutes INTEGER NOT NULL,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  cover_image_url TEXT,
  cover_theme TEXT NOT NULL DEFAULT 'editorial_warm',
  body_json JSONB NOT NULL,
  highlights_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  footer_analysis_json JSONB NOT NULL,
  paragraph_notes_json JSONB NOT NULL DEFAULT '{}',
  takeaways_json JSONB NOT NULL DEFAULT '{}',
  original_text TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
  score REAL,
  content_sec_check JSONB NOT NULL DEFAULT '{}'::jsonb,
  original_text_hash TEXT,
  pipeline_source TEXT,
  pipeline_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_daily_readers_status_date ON daily_readers(status, publish_date DESC);
CREATE INDEX idx_daily_readers_published ON daily_readers(publish_date DESC)
  WHERE status = 'published';
CREATE INDEX idx_daily_readers_original_text_hash ON daily_readers(original_text_hash)
  WHERE original_text_hash IS NOT NULL;

-- ============================================================
-- Pipeline 执行记录
-- ============================================================

CREATE TABLE pipeline_runs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  stage TEXT NOT NULL DEFAULT 'init'
    CHECK (stage IN (
      'init', 'discovery', 'extraction', 'scoring',
      'selection', 'workflow', 'cover_download', 'storing', 'done'
    )),
  stage_detail JSONB NOT NULL DEFAULT '{}'::jsonb,
  candidates_found INTEGER NOT NULL DEFAULT 0,
  candidates_extracted INTEGER NOT NULL DEFAULT 0,
  candidates_scored INTEGER NOT NULL DEFAULT 0,
  articles_generated INTEGER NOT NULL DEFAULT 0,
  errors JSONB NOT NULL DEFAULT '[]'::jsonb,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX idx_pipeline_runs_created ON pipeline_runs(created_at DESC);

-- ============================================================
-- 词典数据（TECD3 — 重置开发库时保留）
-- ============================================================

CREATE TABLE IF NOT EXISTS dict_entries (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'tecd3',
  source_entry_key TEXT NOT NULL,
  entry_kind TEXT NOT NULL CHECK (entry_kind IN ('entry', 'fragment')),
  display_headword TEXT NOT NULL,
  base_headword TEXT,
  homograph_no INTEGER,
  phonetic TEXT,
  meanings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  examples_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  phrases_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  sections_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw_html TEXT,
  parse_version TEXT NOT NULL DEFAULT 'tecd3_v2',
  exam_tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dict_entries_source_entry_key ON dict_entries(source, source_entry_key);
CREATE INDEX IF NOT EXISTS idx_dict_entries_display_headword_lower ON dict_entries(LOWER(display_headword));
CREATE INDEX IF NOT EXISTS idx_dict_entries_base_headword_lower ON dict_entries(LOWER(base_headword));

CREATE TABLE IF NOT EXISTS dict_lookup_targets (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'tecd3',
  normalized_form TEXT NOT NULL,
  lookup_type TEXT NOT NULL DEFAULT 'word' CHECK (lookup_type IN ('word', 'phrase')),
  lookup_label TEXT NOT NULL,
  entry_id BIGINT NOT NULL REFERENCES dict_entries(id) ON DELETE CASCADE,
  target_label TEXT NOT NULL,
  target_pos TEXT,
  preview_text TEXT,
  rank INTEGER NOT NULL DEFAULT 0,
  match_kind TEXT NOT NULL CHECK (match_kind IN ('headword', 'alias', 'disamb', 'redirect', 'nlp', 'phrase', 'phrase_template')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_dict_lookup_targets UNIQUE (source, normalized_form, entry_id, match_kind)
);

CREATE INDEX IF NOT EXISTS idx_dict_lookup_targets_form_rank ON dict_lookup_targets(source, normalized_form, rank);
CREATE INDEX IF NOT EXISTS idx_dict_lookup_targets_entry_id ON dict_lookup_targets(entry_id);

CREATE TABLE IF NOT EXISTS dict_redirects (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'tecd3',
  redirect_key TEXT NOT NULL,
  target_entry_key TEXT NOT NULL,
  redirect_kind TEXT NOT NULL CHECK (redirect_kind IN ('mdx_link', 'normalized_alias')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_dict_redirects UNIQUE (source, redirect_key, target_entry_key, redirect_kind)
);

CREATE INDEX IF NOT EXISTS idx_dict_redirects_key ON dict_redirects(source, redirect_key);
CREATE INDEX IF NOT EXISTS idx_dict_redirects_target ON dict_redirects(source, target_entry_key);

DO $$
BEGIN
  ALTER TABLE vocabulary_book
    ADD CONSTRAINT fk_vocabulary_book_dict_entry
    FOREIGN KEY (dict_entry_id) REFERENCES dict_entries(id) ON DELETE SET NULL;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END;
$$;

-- ============================================================
-- COMMENT
-- ============================================================

COMMENT ON TABLE users IS '用户主表，保存应用内部用户档案与基础偏好设置。';
COMMENT ON COLUMN users.id IS '用户主键，使用 UUID。';
COMMENT ON COLUMN users.status IS '用户状态，支持 active、disabled、deleted。';
COMMENT ON COLUMN users.display_name IS '用户展示名称。';
COMMENT ON COLUMN users.avatar_url IS '用户头像地址。';
COMMENT ON COLUMN users.locale IS '用户语言区域设置，例如 zh-CN。';
COMMENT ON COLUMN users.timezone IS '用户时区标识，例如 Asia/Shanghai。';
COMMENT ON COLUMN users.cumulative_article_count IS '用户自注册以来累计成功解析的文章总数，删除历史记录不减少。';
COMMENT ON COLUMN users.last_active_at IS '用户最近一次活跃（如发起解析）的时间。';
COMMENT ON COLUMN users.settings_json IS '用户设置的结构化 JSON 数据。';
COMMENT ON COLUMN users.metadata_json IS '用户附加元数据 JSON。';
COMMENT ON COLUMN users.last_login_at IS '最近一次登录时间。';
COMMENT ON COLUMN users.created_at IS '记录创建时间。';
COMMENT ON COLUMN users.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE user_identities IS '用户身份绑定表，保存第三方登录提供方与应用用户之间的映射。';
COMMENT ON COLUMN user_identities.id IS '身份记录主键，使用 UUID。';
COMMENT ON COLUMN user_identities.user_id IS '关联的应用用户 ID。';
COMMENT ON COLUMN user_identities.provider IS '身份提供方标识，例如 wechat。';
COMMENT ON COLUMN user_identities.provider_user_id IS '第三方平台中的用户唯一标识。';
COMMENT ON COLUMN user_identities.unionid IS '微信等平台的 unionid，用于跨应用归并用户。';
COMMENT ON COLUMN user_identities.app_id IS '第三方应用 ID。';
COMMENT ON COLUMN user_identities.auth_payload_json IS '认证返回的原始或扩展载荷 JSON。';
COMMENT ON COLUMN user_identities.created_at IS '记录创建时间。';
COMMENT ON COLUMN user_identities.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE user_sessions IS '用户会话表，保存登录态、刷新令牌与设备信息。';
COMMENT ON COLUMN user_sessions.id IS '会话主键，使用 UUID。';
COMMENT ON COLUMN user_sessions.user_id IS '关联的应用用户 ID。';
COMMENT ON COLUMN user_sessions.session_token_hash IS '访问令牌哈希值。';
COMMENT ON COLUMN user_sessions.refresh_token_hash IS '刷新令牌哈希值。';
COMMENT ON COLUMN user_sessions.client_platform IS '客户端平台标识，例如 wechat_miniprogram。';
COMMENT ON COLUMN user_sessions.device_id IS '客户端设备 ID。';
COMMENT ON COLUMN user_sessions.device_name IS '设备名称或机型描述。';
COMMENT ON COLUMN user_sessions.app_version IS '客户端应用版本号。';
COMMENT ON COLUMN user_sessions.ip_address IS '最近访问的 IP 地址。';
COMMENT ON COLUMN user_sessions.user_agent IS '客户端 User-Agent 信息。';
COMMENT ON COLUMN user_sessions.status IS '会话状态，支持 active、revoked、expired。';
COMMENT ON COLUMN user_sessions.last_seen_at IS '最近活跃时间。';
COMMENT ON COLUMN user_sessions.expires_at IS '访问令牌过期时间。';
COMMENT ON COLUMN user_sessions.refresh_expires_at IS '刷新令牌过期时间。';
COMMENT ON COLUMN user_sessions.revoked_at IS '会话被撤销的时间。';
COMMENT ON COLUMN user_sessions.metadata_json IS '会话附加元数据 JSON。';
COMMENT ON COLUMN user_sessions.created_at IS '记录创建时间。';
COMMENT ON COLUMN user_sessions.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE analysis_records IS '文章分析记录表，保存用户输入文本、分析核心元数据。结果快照保存在 analysis_results。';
COMMENT ON COLUMN analysis_records.id IS '分析记录主键，使用 UUID。';
COMMENT ON COLUMN analysis_records.user_id IS '所属用户 ID。';
COMMENT ON COLUMN analysis_records.client_record_id IS '客户端侧生成的记录 ID，用于回放或去重。';
COMMENT ON COLUMN analysis_records.source_type IS '文本来源类型，例如 user_input、daily_article、imported、ocr。';
COMMENT ON COLUMN analysis_records.title IS '文章标题。';
COMMENT ON COLUMN analysis_records.source_text IS '原始输入文本。';
COMMENT ON COLUMN analysis_records.source_text_hash IS '原始文本的哈希值，用于去重与检索。';
COMMENT ON COLUMN analysis_records.reading_goal IS '阅读目标，例如 exam、daily_reading、academic。';
COMMENT ON COLUMN analysis_records.reading_variant IS '阅读变体，例如 cet、ielts_toefl。';
COMMENT ON COLUMN analysis_records.extended IS '是否开启深度篇章分析。';
COMMENT ON COLUMN analysis_records.user_facing_state IS '面向用户的结果状态，例如 normal、degraded_light。';
COMMENT ON COLUMN analysis_records.analysis_status IS '分析记录状态，支持 queued、running、finalizing、ready、partial、failed、deleted、cancelled、expired。';
COMMENT ON COLUMN analysis_records.last_opened_at IS '最近一次打开该记录的时间。';
COMMENT ON COLUMN analysis_records.created_at IS '记录创建时间。';
COMMENT ON COLUMN analysis_records.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE analysis_results IS '文章分析结果详情表，保存渲染场景 JSON 快照。';
COMMENT ON COLUMN analysis_results.record_id IS '关联的分析记录 ID。';
COMMENT ON COLUMN analysis_results.render_scene_json IS '前端渲染场景 JSON 快照。';
COMMENT ON COLUMN analysis_results.page_state_json IS '页面状态与过程信息 JSON。';
COMMENT ON COLUMN analysis_results.workflow_version IS '分析工作流版本号。';
COMMENT ON COLUMN analysis_results.schema_version IS '渲染结果 schema 版本号。';

COMMENT ON TABLE analysis_tasks IS '分析任务执行控制表，负责排队、并发控制、失败重试与额度结算。';
COMMENT ON COLUMN analysis_tasks.id IS '任务主键，UUID。';
COMMENT ON COLUMN analysis_tasks.user_id IS '所属用户 ID。';
COMMENT ON COLUMN analysis_tasks.analysis_record_id IS '关联的分析记录 ID，1:1 关系。';
COMMENT ON COLUMN analysis_tasks.status IS '任务状态：queued, running, finalizing, succeeded, failed, cancelled, expired。';
COMMENT ON COLUMN analysis_tasks.worker_token IS '执行器标识，用于多实例场景下的任务认领。';
COMMENT ON COLUMN analysis_tasks.queue_name IS '队列名称，默认 default。';
COMMENT ON COLUMN analysis_tasks.attempt_no IS '当前执行尝试次数。';
COMMENT ON COLUMN analysis_tasks.failure_code IS '失败错误码。';
COMMENT ON COLUMN analysis_tasks.failure_message IS '失败错误信息。';
COMMENT ON COLUMN analysis_tasks.usage_summary_json IS '聚合 token 使用量与模型信息 JSON。';
COMMENT ON COLUMN analysis_tasks.quota_cost_points IS '本次任务实际扣减的积分数。';
COMMENT ON COLUMN analysis_tasks.queued_at IS '入队时间。';
COMMENT ON COLUMN analysis_tasks.started_at IS '开始执行时间。';
COMMENT ON COLUMN analysis_tasks.finished_at IS '执行完成时间。';

COMMENT ON TABLE analysis_task_events IS '分析任务过程审计日志，append-only。';
COMMENT ON COLUMN analysis_task_events.task_id IS '关联的任务 ID。';
COMMENT ON COLUMN analysis_task_events.event_type IS '事件类型，如 task_submitted, task_started, task_succeeded 等。';
COMMENT ON COLUMN analysis_task_events.event_payload_json IS '事件载荷 JSON。';

COMMENT ON TABLE analysis_audit_logs IS '分析任务审计日志表，保存 Token 消耗、原始请求载荷与性能指标。';
COMMENT ON COLUMN analysis_audit_logs.record_id IS '关联的分析记录 ID。';
COMMENT ON COLUMN analysis_audit_logs.task_id IS '关联的任务 ID。';
COMMENT ON COLUMN analysis_audit_logs.user_id IS '所属用户 ID。';
COMMENT ON COLUMN analysis_audit_logs.request_payload_json IS '原始分析请求参数 JSON。';
COMMENT ON COLUMN analysis_audit_logs.usage_summary_json IS 'Token 使用详情快照。';
COMMENT ON COLUMN analysis_audit_logs.cost_points IS '消耗积分。';
COMMENT ON COLUMN analysis_audit_logs.processing_ms IS '后端处理耗时（毫秒）。';

COMMENT ON TABLE user_credit_accounts IS '用户积分账户快照，每用户一行。';
COMMENT ON COLUMN user_credit_accounts.daily_free_points IS '每日免费额度（默认 1000 积分，1 积分 = 1000 加权 token）。';
COMMENT ON COLUMN user_credit_accounts.daily_used_points IS '今日已使用积分。';
COMMENT ON COLUMN user_credit_accounts.bonus_points IS '活动赠送/人工补偿/邀请码奖励等长期积分。';
COMMENT ON COLUMN user_credit_accounts.last_reset_on IS '最近一次每日积分重置日期。';
COMMENT ON COLUMN user_credit_accounts.policy_version IS '积分策略版本号。';

COMMENT ON TABLE user_credit_ledger IS '积分流水账本，append-only，所有积分变动均记录。';
COMMENT ON COLUMN user_credit_ledger.entry_type IS '流水类型：daily_grant, bonus_grant, analysis_deduct, manual_adjust, refund, feedback_reward。';
COMMENT ON COLUMN user_credit_ledger.points IS '变动积分数（正为增加，负为扣减）。';
COMMENT ON COLUMN user_credit_ledger.bucket_type IS '积分桶类型：daily_free 或 bonus。';
COMMENT ON COLUMN user_credit_ledger.balance_after IS '变动后余额。';
COMMENT ON COLUMN user_credit_ledger.metadata_json IS '扩展元数据 JSON，如 { input_tokens, output_tokens, multiplier_input, multiplier_output }。';

COMMENT ON TABLE anonymous_quotas IS '匿名/游客试用额度表，用于限制未登录状态下的试用次数。';
COMMENT ON COLUMN anonymous_quotas.anonymous_id IS '匿名用户标识，例如设备 ID 或客户端生成 UUID。';
COMMENT ON COLUMN anonymous_quotas.trial_count IS '累计试用次数。';
COMMENT ON COLUMN anonymous_quotas.last_trial_at IS '最近一次试用日期。';
COMMENT ON COLUMN anonymous_quotas.created_at IS '记录创建时间。';
COMMENT ON COLUMN anonymous_quotas.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE favorite_records IS '收藏记录表，保存用户对文章、句子、短语或词汇的收藏。';
COMMENT ON COLUMN favorite_records.id IS '收藏记录主键，使用 UUID。';
COMMENT ON COLUMN favorite_records.user_id IS '所属用户 ID。';
COMMENT ON COLUMN favorite_records.target_type IS '收藏目标类型，例如 analysis_record、sentence、phrase、vocab。';
COMMENT ON COLUMN favorite_records.target_key IS '收藏目标的逻辑键，用于唯一定位收藏对象。';
COMMENT ON COLUMN favorite_records.analysis_record_id IS '关联的分析记录 ID，可为空。';
COMMENT ON COLUMN favorite_records.payload_json IS '收藏附加信息 JSON。';
COMMENT ON COLUMN favorite_records.note IS '用户自定义备注。';
COMMENT ON COLUMN favorite_records.created_at IS '记录创建时间。';
COMMENT ON COLUMN favorite_records.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE vocabulary_book IS '用户生词本表，保存词汇快照、掌握状态与复习信息。';
COMMENT ON COLUMN vocabulary_book.id IS '生词记录主键，使用 UUID。';
COMMENT ON COLUMN vocabulary_book.user_id IS '所属用户 ID。';
COMMENT ON COLUMN vocabulary_book.lemma IS '词元或归一化词形，用于唯一去重。';
COMMENT ON COLUMN vocabulary_book.display_word IS '向用户展示的单词原形或表面形。';
COMMENT ON COLUMN vocabulary_book.phonetic IS '音标。';
COMMENT ON COLUMN vocabulary_book.part_of_speech IS '词性。';
COMMENT ON COLUMN vocabulary_book.short_meaning IS '生词快照中的简短释义文本。';
COMMENT ON COLUMN vocabulary_book.meanings_json IS '完整释义结构 JSON。';
COMMENT ON COLUMN vocabulary_book.tags IS '词汇标签数组。';
COMMENT ON COLUMN vocabulary_book.exchange IS '词形变化数组。';
COMMENT ON COLUMN vocabulary_book.source_provider IS '词汇来源提供方，例如 tecd3。';
COMMENT ON COLUMN vocabulary_book.dict_entry_id IS '关联的词典词条 ID，用于详情页按需加载完整释义、短语、例句等。';
COMMENT ON COLUMN vocabulary_book.source_sentence IS '最近一次来源句子文本。';
COMMENT ON COLUMN vocabulary_book.source_context IS '最近一次来源上下文文本。';
COMMENT ON COLUMN vocabulary_book.mastery_status IS '掌握状态，支持 new、learning、review、mastered、archived。';
COMMENT ON COLUMN vocabulary_book.review_count IS '累计复习次数。';
COMMENT ON COLUMN vocabulary_book.last_reviewed_at IS '最近一次复习时间。';
COMMENT ON COLUMN vocabulary_book.payload_json IS '生词附加元数据 JSON，承载 source_refs（多语境来源）、collected_forms（收藏形态）、audio_url（音频缓存）。';
COMMENT ON COLUMN vocabulary_book.created_at IS '记录创建时间。';
COMMENT ON COLUMN vocabulary_book.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE user_annotations IS '用户批注表，保存高亮标注与笔记。';
COMMENT ON COLUMN user_annotations.id IS '批注主键，使用 UUID。';
COMMENT ON COLUMN user_annotations.user_id IS '所属用户 ID。';
COMMENT ON COLUMN user_annotations.analysis_record_id IS '关联的分析记录 ID。';
COMMENT ON COLUMN user_annotations.annotation_type IS '批注类型：highlight（高亮标注）或 note（笔记）。';
COMMENT ON COLUMN user_annotations.anchor_type IS '锚点类型：sentence（句子）、paragraph（段落）、text_range（文本范围）。';
COMMENT ON COLUMN user_annotations.target_key IS '批注目标的逻辑键，用于唯一定位批注对象。';
COMMENT ON COLUMN user_annotations.paragraph_id IS '段落 ID。';
COMMENT ON COLUMN user_annotations.sentence_id IS '句子 ID。';
COMMENT ON COLUMN user_annotations.selected_text IS '用户选中的文本内容。';
COMMENT ON COLUMN user_annotations.start_offset IS '选区起始偏移量。';
COMMENT ON COLUMN user_annotations.end_offset IS '选区结束偏移量。';
COMMENT ON COLUMN user_annotations.text_hash IS '选中文本的哈希值。';
COMMENT ON COLUMN user_annotations.color IS '标注颜色，支持 soft_green、soft_blue、soft_purple、warm_yellow、sage_green。';
COMMENT ON COLUMN user_annotations.note IS '用户笔记内容。';
COMMENT ON COLUMN user_annotations.payload_json IS '批注附加元数据 JSON。';
COMMENT ON COLUMN user_annotations.created_at IS '记录创建时间。';
COMMENT ON COLUMN user_annotations.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE feedback IS '用户反馈表，统一存储结果页整体反馈、批注级反馈、句子级反馈、词典反馈和应用功能反馈。';
COMMENT ON COLUMN feedback.feedback_scope IS '反馈作用域：analysis_result（结果页整体）、annotation（批注级）、sentence（句子级）、dictionary（词典）、app（应用功能）。';
COMMENT ON COLUMN feedback.target_id IS '反馈目标标识：analysis_result 为 record_id，annotation 为 mark.id/sentence_entry.id，dictionary 为 dict_entry_id 或 word，app 为功能区域标识。';
COMMENT ON COLUMN feedback.sentiment IS '情感倾向：positive（正面）、negative（负面）、neutral（中性）。dictionary 作用域仅允许 negative。';
COMMENT ON COLUMN feedback.feedback_type IS '结构化反馈分类，含义随 feedback_scope 变化。';
COMMENT ON COLUMN feedback.annotation_type IS '标注类型，仅 annotation 作用域有值。';
COMMENT ON COLUMN feedback.context_json IS '反馈时的上下文快照 JSON，用于 RAG 训练数据提取。';
COMMENT ON COLUMN feedback.status IS '处理状态：pending（待处理）、adopted（已采纳，触发奖励）、resolved（已解决）、dismissed（已关闭）。';
COMMENT ON COLUMN feedback.reward_points IS '因反馈被采纳而发放的奖励积分数，0 表示未发放。';
COMMENT ON COLUMN feedback.rag_harvested IS '是否已被用于 RAG 训练数据提取。';

COMMENT ON TABLE daily_readers IS '每日精读文章表，存储预生成的精读内容 payload。每天最多 3 篇已发布文章，由应用层保证，数据库不做 UNIQUE 约束。';
COMMENT ON COLUMN daily_readers.id IS '文章 ID，格式 daily_{YYYY}_{MM}_{DD}_{NNN}。';
COMMENT ON COLUMN daily_readers.title IS '文章标题。';
COMMENT ON COLUMN daily_readers.subtitle IS '副标题/摘要。';
COMMENT ON COLUMN daily_readers.source IS '来源媒体名称，如 The Guardian、BBC News。';
COMMENT ON COLUMN daily_readers.source_url IS '原文链接，用于版权标注和引导用户访问。';
COMMENT ON COLUMN daily_readers.publish_date IS '发布日期（UTC+8），用于按天查询今日精读。';
COMMENT ON COLUMN daily_readers.difficulty IS 'CEFR 难度等级。';
COMMENT ON COLUMN daily_readers.read_time_minutes IS '预估阅读时长（分钟）。';
COMMENT ON COLUMN daily_readers.tags IS '文章主题标签数组。';
COMMENT ON COLUMN daily_readers.cover_image_url IS '封面图 URL，优先使用文章自带图。';
COMMENT ON COLUMN daily_readers.cover_theme IS '封面氛围主题，用于无封面图时的渐变色渲染。';
COMMENT ON COLUMN daily_readers.body_json IS '正文段落数据，包含段落文本和高亮锚点。';
COMMENT ON COLUMN daily_readers.highlights_json IS '正文高亮标注数据，vocab_highlight/phrase_gloss/context_gloss。';
COMMENT ON COLUMN daily_readers.footer_analysis_json IS '文末解析数据，summary/structure/key_expressions/full_analysis/discussion_questions。';
COMMENT ON COLUMN daily_readers.paragraph_notes_json IS '段落透读与译文：article_summary, reading_focus, notes[{paragraph_id, focus_question, micro_summary, translation}]';
COMMENT ON COLUMN daily_readers.takeaways_json IS '精读收束：article_takeaway, key_expressions, sentence_notes, writing_moves, discussion_questions';
COMMENT ON COLUMN daily_readers.original_text IS '原文全文，用于 retry workflow 重新生成解析内容。仅在 pipeline 存储时写入，历史数据为 NULL。';
COMMENT ON COLUMN daily_readers.status IS '文章状态：draft（草稿）、published（已发布）、archived（已归档）。';
COMMENT ON COLUMN daily_readers.score IS 'AI 评分（4 维综合，满分 10）。';
COMMENT ON COLUMN daily_readers.content_sec_check IS '微信内容安全检测结果，含 trace_id、suggest、label 等。';
COMMENT ON COLUMN daily_readers.original_text_hash IS '原文 SHA256，用于去重校验。';
COMMENT ON COLUMN daily_readers.pipeline_source IS '拉取来源标识，如 guardian_api、bbc_rss。';
COMMENT ON COLUMN daily_readers.pipeline_meta IS 'Pipeline 运行元数据，含评分详情、提取日志、workflow 审核记录等。';

COMMENT ON TABLE pipeline_runs IS '每日精读 pipeline 执行记录，用于追踪异步任务进度。';
COMMENT ON COLUMN pipeline_runs.stage IS '当前执行阶段。';
COMMENT ON COLUMN pipeline_runs.stage_detail IS '阶段详情，如发现的来源、评分分布等。';
COMMENT ON COLUMN pipeline_runs.errors IS '错误列表，每项含 stage + message。';

COMMENT ON TABLE dict_entries IS '词典词条详情表，保存 TECD3 的正式词条或可保留的 fragment 详情。';
COMMENT ON COLUMN dict_entries.id IS '词条主键，自增 bigint。';
COMMENT ON COLUMN dict_entries.source IS '词典来源标识，当前为 tecd3。';
COMMENT ON COLUMN dict_entries.source_entry_key IS '词典原生词条键，用于唯一标识一个入口。';
COMMENT ON COLUMN dict_entries.entry_kind IS '词条类型，entry 表示正式词条，fragment 表示片段词条。';
COMMENT ON COLUMN dict_entries.display_headword IS '展示给用户的词头，保留同形编号。';
COMMENT ON COLUMN dict_entries.base_headword IS '去掉同形编号后的基础词头。';
COMMENT ON COLUMN dict_entries.homograph_no IS '同形词编号，例如 1、2。';
COMMENT ON COLUMN dict_entries.phonetic IS '主音标。';
COMMENT ON COLUMN dict_entries.meanings_json IS '完整义项结构 JSON。';
COMMENT ON COLUMN dict_entries.examples_json IS '例句结构 JSON。';
COMMENT ON COLUMN dict_entries.phrases_json IS '短语结构 JSON。';
COMMENT ON COLUMN dict_entries.sections_json IS '词条分段摘要 JSON，用于调试或扩展展示。';
COMMENT ON COLUMN dict_entries.raw_html IS '词条原始 HTML 内容。';
COMMENT ON COLUMN dict_entries.parse_version IS '导入解析器版本号。';
COMMENT ON COLUMN dict_entries.exam_tags IS '词汇所属考试标签数组：gaokao, cet4, cet6, tem4, tem8, kaoyan, ielts, toefl';
COMMENT ON COLUMN dict_entries.created_at IS '记录创建时间。';
COMMENT ON COLUMN dict_entries.updated_at IS '记录最后更新时间。';

COMMENT ON TABLE dict_lookup_targets IS '词典查询映射表，保存归一化查询词到一个或多个候选词条的关系。';
COMMENT ON COLUMN dict_lookup_targets.id IS '查询映射主键，自增 bigint。';
COMMENT ON COLUMN dict_lookup_targets.source IS '词典来源标识，当前为 tecd3。';
COMMENT ON COLUMN dict_lookup_targets.normalized_form IS '归一化后的查询词。';
COMMENT ON COLUMN dict_lookup_targets.lookup_type IS '查询目标类型，word 表示单词查找，phrase 表示短语查找。';
COMMENT ON COLUMN dict_lookup_targets.lookup_label IS '查询结果页显示的查找标签。';
COMMENT ON COLUMN dict_lookup_targets.entry_id IS '关联的词条详情 ID。';
COMMENT ON COLUMN dict_lookup_targets.target_label IS '候选词条展示标签。';
COMMENT ON COLUMN dict_lookup_targets.target_pos IS '候选词条词性。';
COMMENT ON COLUMN dict_lookup_targets.preview_text IS '候选词条预览释义。';
COMMENT ON COLUMN dict_lookup_targets.rank IS '候选排序值，越小越靠前。';
COMMENT ON COLUMN dict_lookup_targets.match_kind IS '匹配来源类型，例如 headword、disamb、redirect、nlp、phrase、phrase_template。';
COMMENT ON COLUMN dict_lookup_targets.created_at IS '记录创建时间。';

COMMENT ON TABLE dict_redirects IS '词典重定向关系表，保存 MDX 链接跳转与归一化别名到词条键的映射。';
COMMENT ON COLUMN dict_redirects.id IS '重定向记录主键，自增 bigint。';
COMMENT ON COLUMN dict_redirects.source IS '词典来源标识，当前为 tecd3。';
COMMENT ON COLUMN dict_redirects.redirect_key IS '重定向查找键。';
COMMENT ON COLUMN dict_redirects.target_entry_key IS '重定向目标词条键。';
COMMENT ON COLUMN dict_redirects.redirect_kind IS '重定向类型，例如 mdx_link、normalized_alias。';
COMMENT ON COLUMN dict_redirects.created_at IS '记录创建时间。';

-- ============================================================
-- TRIGGER
-- ============================================================

CREATE TRIGGER trg_users_set_updated_at
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_identities_set_updated_at
BEFORE UPDATE ON user_identities
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_sessions_set_updated_at
BEFORE UPDATE ON user_sessions
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_analysis_records_set_updated_at
BEFORE UPDATE ON analysis_records
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_analysis_tasks_set_updated_at
BEFORE UPDATE ON analysis_tasks
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_credit_accounts_set_updated_at
BEFORE UPDATE ON user_credit_accounts
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_anonymous_quotas_set_updated_at
BEFORE UPDATE ON anonymous_quotas
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_favorite_records_set_updated_at
BEFORE UPDATE ON favorite_records
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_vocabulary_book_set_updated_at
BEFORE UPDATE ON vocabulary_book
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_annotations_set_updated_at
BEFORE UPDATE ON user_annotations
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_dict_entries_set_updated_at
BEFORE UPDATE ON dict_entries
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_feedback_set_updated_at
BEFORE UPDATE ON feedback
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_daily_readers_set_updated_at
BEFORE UPDATE ON daily_readers
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
