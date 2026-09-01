-- Claread single fresh-init baseline (DATA-SCHEMA-BASELINE).
-- Squashed from migrations 0001-0029 + LLM Config control plane +
-- Example Lab final schema. Legacy analysis tables, legacy Eval
-- control-plane tables, reader_ask_eval_traces and the confirmed
-- legacy columns on protected shared tables are NOT part of this
-- baseline. directus_* system tables are managed by Directus itself
-- and are intentionally absent.
--
-- dict_* trio and eval_example_lab_entries DDL is idempotent
-- (IF NOT EXISTS / guarded ALTERs) so reset_full_keep_dict.sql can
-- re-apply this file while dictionary and Example Lab data survive.

SET search_path = public, pg_catalog;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';

CREATE OR REPLACE FUNCTION initialize_reader_event_sequence() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  INSERT INTO reader_event_sequences (reading_record_id)
  VALUES (NEW.id)
  ON CONFLICT (reading_record_id) DO NOTHING;

  RETURN NEW;

END;

$$;

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = NOW();

  RETURN NEW;

END;

$$;

-- reading_records: pure reading-visibility stamps (last_opened_at,
-- recent_hidden_at) are not content changes. updated_at only advances
-- when some other column changes, so hide-from-recent and open stamps
-- keep their "must not touch updated_at" contract.
CREATE OR REPLACE FUNCTION reading_records_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  new_rest record;
  old_rest record;
BEGIN
  new_rest := NEW;
  old_rest := OLD;
  new_rest.last_opened_at := NULL;
  new_rest.recent_hidden_at := NULL;
  old_rest.last_opened_at := NULL;
  old_rest.recent_hidden_at := NULL;
  IF new_rest IS DISTINCT FROM old_rest OR NEW.updated_at IS DISTINCT FROM OLD.updated_at THEN
    NEW.updated_at := NOW();
  END IF;

  RETURN NEW;

END;

$$;

CREATE OR REPLACE FUNCTION utf16_code_unit_length(input_text text) RETURNS integer
    LANGUAGE plpgsql IMMUTABLE STRICT
    AS $$
DECLARE
  total INTEGER := 0;

  idx INTEGER := 1;

  char_count INTEGER := char_length(input_text);

  ch TEXT;

BEGIN
  IF input_text IS NULL THEN
    RETURN NULL;

  END IF;

  WHILE idx <= char_count LOOP
    ch := substring(input_text FROM idx FOR 1);

    total := total + CASE WHEN ascii(ch) > 65535 THEN 2 ELSE 1 END;

    idx := idx + 1;

  END LOOP;

  RETURN total;

END;

$$;

CREATE TABLE ai_usage_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    usage_scope text NOT NULL,
    capability_code text NOT NULL,
    billing_mode text NOT NULL,
    status text NOT NULL,
    user_id uuid,
    reading_record_id uuid,
    reader_run_id uuid,
    reader_job_id uuid,
    enhancement_layer_id uuid,
    daily_reader_article_id text,
    client_platform text,
    request_id text,
    invocation_key text,
    workflow_name text,
    workflow_version text,
    schema_version text,
    prompt_version text,
    model_route text,
    model_profile_id text,
    model_profile text,
    model_provider text,
    model_name text,
    planner_kind text,
    policy_version text,
    cache_hit boolean,
    cache_status text,
    cache_class text,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    cache_read_tokens integer DEFAULT 0 NOT NULL,
    cache_write_tokens integer DEFAULT 0 NOT NULL,
    cached_input_tokens integer,
    cache_miss_input_tokens integer,
    cache_creation_input_tokens integer,
    token_budget_before integer,
    token_budget_after integer,
    latency_ms integer,
    billed_points integer,
    billing_policy_version text,
    operation_fingerprint text,
    error_code text,
    error_message text,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ai_usage_events_billing_mode_check CHECK ((billing_mode = ANY (ARRAY['user_points'::text, 'internal_only'::text, 'trial'::text, 'no_charge'::text]))),
    CONSTRAINT ai_usage_events_usage_scope_check CHECK ((usage_scope = ANY (ARRAY['user_billed'::text, 'system_internal'::text, 'anonymous_trial'::text, 'eval_debug'::text])))
);

COMMENT ON TABLE ai_usage_events IS '统一 AI 使用审计事件表，记录用户计费、系统内部、匿名试用和调试评测等 AI 调用。';

COMMENT ON COLUMN ai_usage_events.usage_scope IS '调用作用域：user_billed、system_internal、anonymous_trial、eval_debug。';

COMMENT ON COLUMN ai_usage_events.capability_code IS '能力代码，如 analysis_full、dict_ai_lookup、reader_ask。';

COMMENT ON COLUMN ai_usage_events.billing_mode IS '结算模式：user_points、internal_only、trial、no_charge。';

COMMENT ON COLUMN ai_usage_events.status IS '事件状态，建议使用 succeeded、failed、fallback、skipped，并允许后续扩展。';

COMMENT ON COLUMN ai_usage_events.model_route IS '主要模型路由；多路由工作流的完整映射放入 metadata_json。';

COMMENT ON COLUMN ai_usage_events.metadata_json IS '扩展审计上下文 JSON，包括 usage 快照、entrypoint、对象标识和多模型映射。';

CREATE TABLE analysis_windows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    window_index integer NOT NULL,
    target_anchor_ids jsonb NOT NULL,
    context_anchor_prev jsonb DEFAULT '[]'::jsonb NOT NULL,
    context_anchor_next jsonb DEFAULT '[]'::jsonb NOT NULL,
    target_unit_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    target_block_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    char_count integer NOT NULL,
    anchor_count integer NOT NULL,
    window_budget jsonb NOT NULL,
    coverage jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text NOT NULL,
    job_id uuid,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT analysis_windows_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'completed'::text, 'no_op'::text, 'failed'::text])))
);

CREATE TABLE anchor_segments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    base_id uuid NOT NULL,
    unit_id text NOT NULL,
    anchor_segment_id text NOT NULL,
    sentence_id text,
    paragraph_id text,
    order_index integer NOT NULL,
    unit_order_index integer NOT NULL,
    segment_type text NOT NULL,
    base_start_utf16 integer NOT NULL,
    base_end_utf16 integer NOT NULL,
    unit_start_utf16 integer NOT NULL,
    unit_end_utf16 integer NOT NULL,
    text_hash text NOT NULL,
    boundary_quality text DEFAULT 'normal'::text NOT NULL,
    CONSTRAINT anchor_segments_boundary_quality_check CHECK ((boundary_quality = ANY (ARRAY['normal'::text, 'low'::text]))),
    CONSTRAINT anchor_segments_order_index_check CHECK ((order_index >= 1)),
    CONSTRAINT anchor_segments_segment_type_check CHECK ((segment_type = ANY (ARRAY['sentence'::text, 'clause'::text, 'fallback_window'::text]))),
    CONSTRAINT anchor_segments_text_hash_check CHECK ((text_hash ~ '^[0-9a-f]{8}$'::text)),
    CONSTRAINT anchor_segments_unit_order_index_check CHECK ((unit_order_index >= 1)),
    CONSTRAINT ck_anchor_segments_offsets CHECK (((base_start_utf16 >= 0) AND (base_end_utf16 > base_start_utf16) AND (unit_start_utf16 >= 0) AND (unit_end_utf16 > unit_start_utf16)))
);

CREATE TABLE anonymous_quotas (
    anonymous_id text NOT NULL,
    trial_count integer DEFAULT 0 NOT NULL,
    last_trial_at date DEFAULT CURRENT_DATE NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE anonymous_quotas IS '匿名/游客试用额度表，用于限制未登录状态下的试用次数。';

COMMENT ON COLUMN anonymous_quotas.anonymous_id IS '匿名用户标识，例如设备 ID 或客户端生成 UUID。';

COMMENT ON COLUMN anonymous_quotas.trial_count IS '累计试用次数。';

COMMENT ON COLUMN anonymous_quotas.last_trial_at IS '最近一次试用日期。';

COMMENT ON COLUMN anonymous_quotas.created_at IS '记录创建时间。';

COMMENT ON COLUMN anonymous_quotas.updated_at IS '记录最后更新时间。';

CREATE TABLE candidate_reading_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    user_id uuid NOT NULL,
    record_generation integer NOT NULL,
    title text,
    blocks_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    canonical_text_preview text DEFAULT ''::text NOT NULL,
    source_refs_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    quality_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'ready'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp with time zone,
    CONSTRAINT candidate_reading_documents_blocks_json_check CHECK ((jsonb_typeof(blocks_json) = 'array'::text)),
    CONSTRAINT candidate_reading_documents_quality_json_check CHECK ((jsonb_typeof(quality_json) = 'object'::text)),
    CONSTRAINT candidate_reading_documents_record_generation_check CHECK ((record_generation >= 1)),
    CONSTRAINT candidate_reading_documents_source_refs_json_check CHECK ((jsonb_typeof(source_refs_json) = 'object'::text)),
    CONSTRAINT candidate_reading_documents_status_check CHECK ((status = ANY (ARRAY['ready'::text, 'confirmed'::text, 'rejected'::text, 'superseded'::text]))),
    CONSTRAINT ck_candidate_reading_documents_confirmed_at CHECK ((((status = 'confirmed'::text) AND (confirmed_at IS NOT NULL)) OR (status <> 'confirmed'::text)))
);

CREATE TABLE confirmed_source_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    user_id uuid NOT NULL,
    record_generation integer NOT NULL,
    original_input_id uuid,
    markdown_text text NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    content_sha256 text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    edit_source text DEFAULT 'initial'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    frozen_at timestamp with time zone,
    CONSTRAINT ck_confirmed_source_documents_content_sha256 CHECK ((content_sha256 = encode(digest(markdown_text, 'sha256'::text), 'hex'::text))),
    CONSTRAINT ck_confirmed_source_documents_frozen_at CHECK ((((status = 'frozen'::text) AND (frozen_at IS NOT NULL)) OR ((status = 'draft'::text) AND (frozen_at IS NULL)))),
    CONSTRAINT confirmed_source_documents_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT confirmed_source_documents_edit_source_check CHECK ((edit_source = ANY (ARRAY['initial'::text, 'extraction'::text, 'wysiwyg'::text, 'source_mode'::text, 'content_check'::text]))),
    CONSTRAINT confirmed_source_documents_markdown_text_check CHECK ((markdown_text <> ''::text)),
    CONSTRAINT confirmed_source_documents_record_generation_check CHECK ((record_generation >= 1)),
    CONSTRAINT confirmed_source_documents_revision_check CHECK ((revision >= 1)),
    CONSTRAINT confirmed_source_documents_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'frozen'::text])))
);

-- R8 — immutable confirmed-source revision snapshots. The current row
-- (confirmed_source_documents) keeps optimistic-concurrency in-place
-- UPDATE (revision +1); every durable write additionally persists one
-- immutable snapshot row here (snapshot_reason: initial | save | restore)
-- inside the same transaction. Snapshots are never rewritten.
CREATE TABLE confirmed_source_revisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    confirmed_source_document_id uuid NOT NULL,
    reading_record_id uuid NOT NULL,
    user_id uuid NOT NULL,
    record_generation integer NOT NULL,
    revision integer NOT NULL,
    markdown_text text NOT NULL,
    content_sha256 text NOT NULL,
    snapshot_reason text NOT NULL,
    edit_source text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT confirmed_source_revisions_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT confirmed_source_revisions_edit_source_check CHECK ((edit_source = ANY (ARRAY['initial'::text, 'extraction'::text, 'wysiwyg'::text, 'source_mode'::text, 'content_check'::text]))),
    CONSTRAINT confirmed_source_revisions_markdown_text_check CHECK ((markdown_text <> ''::text)),
    CONSTRAINT confirmed_source_revisions_record_generation_check CHECK ((record_generation >= 1)),
    CONSTRAINT confirmed_source_revisions_revision_check CHECK ((revision >= 1)),
    CONSTRAINT confirmed_source_revisions_snapshot_reason_check CHECK ((snapshot_reason = ANY (ARRAY['initial'::text, 'save'::text, 'restore'::text]))),
    CONSTRAINT ck_confirmed_source_revisions_content_sha256 CHECK ((content_sha256 = encode(digest(markdown_text, 'sha256'::text), 'hex'::text)))
);

CREATE TABLE daily_readers (
    id text NOT NULL,
    title text NOT NULL,
    subtitle text,
    original_title text,
    subtitle_zh text,
    source text NOT NULL,
    source_url text NOT NULL,
    publish_date date NOT NULL,
    difficulty text NOT NULL,
    read_time_minutes integer NOT NULL,
    tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    cover_image_url text,
    cover_theme text DEFAULT 'editorial_warm'::text NOT NULL,
    body_json jsonb NOT NULL,
    highlights_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    footer_analysis_json jsonb NOT NULL,
    paragraph_notes_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    takeaways_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    original_text text,
    status text DEFAULT 'draft'::text NOT NULL,
    score real,
    content_sec_check jsonb DEFAULT '{}'::jsonb NOT NULL,
    original_text_hash text,
    pipeline_source text,
    pipeline_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone,
    review_status text DEFAULT 'pending'::text NOT NULL,
    reviewed_by text,
    reviewed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT daily_readers_difficulty_check CHECK ((difficulty = ANY (ARRAY['A2'::text, 'B1'::text, 'B2'::text, 'C1'::text]))),
    CONSTRAINT daily_readers_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'published'::text, 'archived'::text]))),
    CONSTRAINT daily_readers_review_status_check CHECK ((review_status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text])))
);

COMMENT ON TABLE daily_readers IS '每日精读文章表，存储预生成的精读内容 payload。每天最多 3 篇已发布文章，由应用层保证，数据库不做 UNIQUE 约束。';

COMMENT ON COLUMN daily_readers.id IS '文章 ID，格式 daily_{YYYY}_{MM}_{DD}_{NNN}。';

COMMENT ON COLUMN daily_readers.title IS '文章标题。A-3 起存中文主标题（takeaways.title_zh）；旧行为英文原题。';

COMMENT ON COLUMN daily_readers.subtitle IS '副标题/摘要（来源 description，英文）。';

COMMENT ON COLUMN daily_readers.original_title IS '英文原题（caption 级展示）。旧行由增量脚本回填为原 title 值。';

COMMENT ON COLUMN daily_readers.subtitle_zh IS '中文副标题（takeaways.subtitle_zh，一句话点题），可空。';

COMMENT ON COLUMN daily_readers.source IS '来源媒体名称，如 The Guardian、BBC News。';

COMMENT ON COLUMN daily_readers.source_url IS '原文链接，用于版权标注和引导用户访问。';

COMMENT ON COLUMN daily_readers.publish_date IS '发布日期（UTC+8），用于按天查询今日精读。';

COMMENT ON COLUMN daily_readers.difficulty IS 'CEFR 难度等级。';

COMMENT ON COLUMN daily_readers.read_time_minutes IS '预估阅读时长（分钟）。';

COMMENT ON COLUMN daily_readers.tags IS '文章主题标签数组。A-3 起存中文 tags（takeaways.tags_zh）；scorer tags 仅存 pipeline_meta.score_tags 作选题参考。';

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

COMMENT ON COLUMN daily_readers.content_sec_check IS 'DEPRECATED: 历史占位字段，pipeline 不再写入。列保留以免破坏旧行读取。';

COMMENT ON COLUMN daily_readers.review_status IS '日审状态：pending、approved、rejected。publish 时置 approved；retry 回 draft 时置 pending。';

COMMENT ON COLUMN daily_readers.reviewed_by IS '最近一次 publish/unpublish 的 operator 标识。旧已发布行回填 legacy。';

COMMENT ON COLUMN daily_readers.reviewed_at IS '最近一次 publish/unpublish 时间。';

COMMENT ON COLUMN daily_readers.original_text_hash IS '原文 SHA256，用于去重校验。';

COMMENT ON COLUMN daily_readers.pipeline_source IS '拉取来源标识，如 guardian_api、bbc_rss。';

COMMENT ON COLUMN daily_readers.pipeline_meta IS 'Pipeline 运行元数据，含评分详情、提取日志、workflow 审核记录等。';

CREATE TABLE dict_ai_candidate_entries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    query text NOT NULL,
    normalized_query text NOT NULL,
    query_type text NOT NULL,
    classification text NOT NULL,
    result_kind text NOT NULL,
    confidence text,
    generated_payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    context_sentence text NOT NULL,
    sentence_id text,
    usage_event_id uuid,
    review_status text DEFAULT 'pending'::text NOT NULL,
    review_note text,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reading_record_id uuid,
    base_id uuid,
    generation integer,
    CONSTRAINT dict_ai_candidate_entries_query_type_check CHECK ((query_type = ANY (ARRAY['word'::text, 'phrase'::text]))),
    CONSTRAINT dict_ai_candidate_entries_result_kind_check CHECK ((result_kind = ANY (ARRAY['ai_entry'::text, 'ai_unresolved'::text]))),
    CONSTRAINT dict_ai_candidate_entries_review_status_check CHECK ((review_status = ANY (ARRAY['pending'::text, 'accepted'::text, 'rejected'::text, 'ignored'::text])))
);

COMMENT ON TABLE dict_ai_candidate_entries IS '词典 AI 未收录兜底候选池。保存 AI 临时词条和放弃解释结果，供后续人工审核与词库补录参考。';

COMMENT ON COLUMN dict_ai_candidate_entries.generated_payload_json IS 'missing_fallback 结构化 AI 输出快照。';

COMMENT ON COLUMN dict_ai_candidate_entries.review_status IS '审核状态：pending、accepted、rejected、ignored。';

CREATE TABLE IF NOT EXISTS dict_entries (
    id bigint NOT NULL,
    source text DEFAULT 'tecd3'::text NOT NULL,
    source_entry_key text NOT NULL,
    entry_kind text NOT NULL,
    display_headword text NOT NULL,
    base_headword text,
    homograph_no integer,
    phonetic text,
    meanings_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    examples_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    phrases_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    sections_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    raw_html text,
    parse_version text DEFAULT 'tecd3_v2'::text NOT NULL,
    exam_tags text[] DEFAULT ARRAY[]::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dict_entries_entry_kind_check CHECK ((entry_kind = ANY (ARRAY['entry'::text, 'fragment'::text])))
);

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

CREATE SEQUENCE IF NOT EXISTS dict_entries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE dict_entries_id_seq OWNED BY dict_entries.id;

CREATE TABLE IF NOT EXISTS dict_lookup_targets (
    id bigint NOT NULL,
    source text DEFAULT 'tecd3'::text NOT NULL,
    normalized_form text NOT NULL,
    lookup_type text DEFAULT 'word'::text NOT NULL,
    lookup_label text NOT NULL,
    entry_id bigint NOT NULL,
    target_label text NOT NULL,
    target_pos text,
    preview_text text,
    rank integer DEFAULT 0 NOT NULL,
    match_kind text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dict_lookup_targets_lookup_type_check CHECK ((lookup_type = ANY (ARRAY['word'::text, 'phrase'::text]))),
    CONSTRAINT dict_lookup_targets_match_kind_check CHECK ((match_kind = ANY (ARRAY['headword'::text, 'alias'::text, 'disamb'::text, 'redirect'::text, 'nlp'::text, 'phrase'::text, 'phrase_template'::text])))
);

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

CREATE SEQUENCE IF NOT EXISTS dict_lookup_targets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE dict_lookup_targets_id_seq OWNED BY dict_lookup_targets.id;

CREATE TABLE IF NOT EXISTS dict_redirects (
    id bigint NOT NULL,
    source text DEFAULT 'tecd3'::text NOT NULL,
    redirect_key text NOT NULL,
    target_entry_key text NOT NULL,
    redirect_kind text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dict_redirects_redirect_kind_check CHECK ((redirect_kind = ANY (ARRAY['mdx_link'::text, 'normalized_alias'::text])))
);

COMMENT ON TABLE dict_redirects IS '词典重定向关系表，保存 MDX 链接跳转与归一化别名到词条键的映射。';

COMMENT ON COLUMN dict_redirects.id IS '重定向记录主键，自增 bigint。';

COMMENT ON COLUMN dict_redirects.source IS '词典来源标识，当前为 tecd3。';

COMMENT ON COLUMN dict_redirects.redirect_key IS '重定向查找键。';

COMMENT ON COLUMN dict_redirects.target_entry_key IS '重定向目标词条键。';

COMMENT ON COLUMN dict_redirects.redirect_kind IS '重定向类型，例如 mdx_link、normalized_alias。';

COMMENT ON COLUMN dict_redirects.created_at IS '记录创建时间。';

CREATE SEQUENCE IF NOT EXISTS dict_redirects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE dict_redirects_id_seq OWNED BY dict_redirects.id;

CREATE TABLE enhancement_layers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    base_id uuid NOT NULL,
    layer_type text NOT NULL,
    layer_subtype text,
    target_scope text NOT NULL,
    target_key text NOT NULL,
    generation integer NOT NULL,
    status text NOT NULL,
    operation_fingerprint text NOT NULL,
    schema_version integer NOT NULL,
    output_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    coverage_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    quality_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_run_id uuid,
    source_job_id uuid,
    published_at timestamp with time zone,
    superseded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT enhancement_layers_generation_check CHECK ((generation >= 1)),
    CONSTRAINT enhancement_layers_layer_type_check CHECK ((layer_type = ANY (ARRAY['translation'::text, 'vocabulary'::text, 'grammar_note'::text, 'sentence_analysis'::text, 'semantic_outline'::text]))),
    CONSTRAINT enhancement_layers_schema_version_check CHECK ((schema_version >= 1)),
    CONSTRAINT enhancement_layers_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'published'::text, 'superseded'::text, 'failed'::text, 'hidden'::text]))),
    CONSTRAINT enhancement_layers_target_scope_check CHECK ((target_scope = ANY (ARRAY['unit'::text, 'anchor_segment'::text, 'unit_range'::text, 'record'::text])))
);

COMMENT ON CONSTRAINT enhancement_layers_layer_type_check ON enhancement_layers IS 'Adds semantic_outline as a record-scoped optional enhancement layer.';

CREATE TABLE IF NOT EXISTS eval_example_lab_entries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_created timestamp with time zone DEFAULT now() NOT NULL,
    date_updated timestamp with time zone,
    user_created uuid,
    user_updated uuid,
    example_id text NOT NULL,
    example_type text NOT NULL,
    sentence_text text NOT NULL,
    output_fragment jsonb DEFAULT '{}'::jsonb NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    source_kind text DEFAULT 'manual'::text NOT NULL,
    source_ref text,
    reading_variant text,
    target_node text,
    grammar_tags jsonb DEFAULT '[]'::jsonb NOT NULL,
    retrieval_text text,
    derived_at timestamp with time zone,
    derived_by text,
    quality_score real DEFAULT 0.0 NOT NULL,
    approved boolean DEFAULT false NOT NULL,
    notes text,
    tags_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT eval_example_lab_entries_approved_rag_eligible_check CHECK (((approved = false) OR (example_type = ANY (ARRAY['grammar'::text, 'sentence_analysis'::text])))),
    CONSTRAINT eval_example_lab_entries_example_id_check CHECK ((example_id ~ '^[A-Za-z0-9._-]+$'::text)),
    CONSTRAINT eval_example_lab_entries_example_type_check CHECK ((example_type = ANY (ARRAY['vocab'::text, 'phrase'::text, 'context'::text, 'grammar'::text, 'sentence_analysis'::text, 'translation'::text]))),
    CONSTRAINT eval_example_lab_entries_fragment_type_check CHECK ((((output_fragment ->> 'type'::text) IS NULL) OR (output_fragment = '{}'::jsonb) OR ((example_type = 'grammar'::text) AND ((output_fragment ->> 'type'::text) = 'grammar_note'::text)) OR ((example_type = 'sentence_analysis'::text) AND ((output_fragment ->> 'type'::text) = 'sentence_analysis'::text)) OR ((example_type = 'vocab'::text) AND ((output_fragment ->> 'type'::text) = ANY (ARRAY['vocab_highlight'::text, 'term_note'::text, 'logic_note'::text]))) OR ((example_type = 'phrase'::text) AND ((output_fragment ->> 'type'::text) = 'phrase_gloss'::text)) OR ((example_type = 'context'::text) AND ((output_fragment ->> 'type'::text) = 'context_gloss'::text)) OR ((example_type = 'translation'::text) AND ((output_fragment ->> 'type'::text) = ANY (ARRAY['translation'::text, 'academic_translation'::text]))))),
    CONSTRAINT eval_example_lab_entries_quality_score_check CHECK (((quality_score >= (0.0)::double precision) AND (quality_score <= (1.0)::double precision))),
    CONSTRAINT eval_example_lab_entries_source_kind_check CHECK ((source_kind = ANY (ARRAY['manual'::text, 'run_capture'::text, 'yaml_import'::text, 'seed_import'::text, 'other'::text]))),
    CONSTRAINT eval_example_lab_entries_target_node_check CHECK ((target_node = ANY (ARRAY['grammar'::text, 'vocabulary'::text, 'translation'::text, 'academic'::text])))
);

COMMENT ON TABLE eval_example_lab_entries IS 'Example Lab few-shot example entries. Stores manually curated examples with RAG-ready metadata. Only grammar / sentence_analysis entries may be approved (approved=true requires example_type in that set).';

CREATE TABLE favorite_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    target_type text NOT NULL,
    target_key text NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT favorite_records_target_type_check CHECK ((target_type = ANY (ARRAY['daily_reader_article'::text, 'reading_record'::text])))
);

COMMENT ON TABLE favorite_records IS '收藏记录表，仅保存文章级收藏。';

COMMENT ON COLUMN favorite_records.id IS '收藏记录主键，使用 UUID。';

COMMENT ON COLUMN favorite_records.user_id IS '所属用户 ID。';

COMMENT ON COLUMN favorite_records.target_type IS '收藏目标类型，仅允许 reading_record 或 daily_reader_article。';

COMMENT ON COLUMN favorite_records.target_key IS '收藏目标的逻辑键，用于唯一定位收藏对象。';

COMMENT ON COLUMN favorite_records.payload_json IS '收藏附加信息 JSON。';

COMMENT ON COLUMN favorite_records.created_at IS '记录创建时间。';

COMMENT ON COLUMN favorite_records.updated_at IS '记录最后更新时间。';

CREATE TABLE feedback (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    feedback_scope text NOT NULL,
    target_id text NOT NULL,
    sentiment text NOT NULL,
    feedback_type text NOT NULL,
    content text,
    context_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    context_summary text,
    app_version text,
    client_platform text NOT NULL,
    client_surface text,
    entry_point text,
    status text DEFAULT 'pending'::text NOT NULL,
    reward_points integer DEFAULT 0 NOT NULL,
    reward_granted_at timestamp with time zone,
    admin_note text,
    reviewed_at timestamp with time zone,
    reviewed_by uuid,
    rag_harvested boolean DEFAULT false NOT NULL,
    rag_harvested_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT feedback_client_platform_check CHECK ((client_platform = ANY (ARRAY['web'::text, 'wechat_miniprogram'::text]))),
    CONSTRAINT feedback_feedback_scope_check CHECK ((feedback_scope = ANY (ARRAY['dictionary'::text, 'app'::text]))),
    CONSTRAINT feedback_sentiment_check CHECK ((sentiment = ANY (ARRAY['positive'::text, 'negative'::text, 'neutral'::text]))),
    CONSTRAINT feedback_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'adopted'::text, 'resolved'::text, 'dismissed'::text])))
);

COMMENT ON TABLE feedback IS '用户反馈表，存储词典反馈和应用功能反馈。';

COMMENT ON COLUMN feedback.feedback_scope IS '反馈作用域：dictionary（词典）、app（应用功能）。';

COMMENT ON COLUMN feedback.target_id IS '反馈目标标识：dictionary 为 dict_entry_id 或 word，app 为功能区域标识。';

COMMENT ON COLUMN feedback.sentiment IS '情感倾向：positive（正面）、negative（负面）、neutral（中性）。dictionary 作用域仅允许 negative。';

COMMENT ON COLUMN feedback.feedback_type IS '结构化反馈分类，含义随 feedback_scope 变化。';

COMMENT ON COLUMN feedback.context_json IS '反馈时的上下文快照 JSON，用于 RAG 训练数据提取。';

COMMENT ON COLUMN feedback.context_summary IS '供列表与后台快速浏览的上下文摘要。';

COMMENT ON COLUMN feedback.client_platform IS '反馈来源端标识，如 web 或 wechat_miniprogram。';

COMMENT ON COLUMN feedback.client_surface IS '反馈来源场景，如 reader、dictionary、settings、result_page、profile。';

COMMENT ON COLUMN feedback.entry_point IS '具体触发入口，如 selection_toolbar、inline_feedback_row、settings_form。';

COMMENT ON COLUMN feedback.status IS '处理状态：pending（待处理）、adopted（已采纳，触发奖励）、resolved（已解决）、dismissed（已关闭）。';

COMMENT ON COLUMN feedback.reward_points IS '因反馈被采纳而发放的奖励积分数，0 表示未发放。';

COMMENT ON COLUMN feedback.rag_harvested IS '是否已被用于 RAG 训练数据提取。';

CREATE TABLE layer_analysis_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    base_id uuid NOT NULL,
    layer_type text NOT NULL,
    policy_version text NOT NULL,
    generation integer NOT NULL,
    budget_total jsonb NOT NULL,
    budget_used jsonb DEFAULT '{"grammar_note": {}, "sentence_analysis": {}}'::jsonb NOT NULL,
    published_anchor_counts_by_type jsonb DEFAULT '{"grammar_note": {}, "sentence_analysis": {}}'::jsonb NOT NULL,
    published_dedup_keys_by_type jsonb DEFAULT '{"grammar_note": [], "sentence_analysis": []}'::jsonb NOT NULL,
    published_pattern_keys_by_type jsonb DEFAULT '{"grammar_note": [], "sentence_analysis": []}'::jsonb NOT NULL,
    density_by_record jsonb DEFAULT '{"grammar_note": 0, "sentence_analysis": 0}'::jsonb NOT NULL,
    covered_window_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    no_op_windows jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT layer_analysis_plans_generation_check CHECK ((generation >= 1)),
    CONSTRAINT layer_analysis_plans_status_check CHECK ((status = ANY (ARRAY['planning'::text, 'active'::text, 'completed'::text, 'completed_with_failures'::text, 'superseded'::text])))
);

CREATE TABLE llm_ask_config (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_created timestamp with time zone DEFAULT now() NOT NULL,
    date_updated timestamp with time zone,
    user_created uuid,
    user_updated uuid,
    default_option text,
    billing_defaults jsonb,
    runtime_defaults jsonb
);

COMMENT ON TABLE llm_ask_config IS 'Singleton for Ask Claread top-level configuration. Only one row should exist.';

COMMENT ON COLUMN llm_ask_config.default_option IS 'Slug of the default ask option. If null, the first enabled option is used.';

COMMENT ON COLUMN llm_ask_config.billing_defaults IS 'Billing defaults for Ask Claread (reserved_points, tokens_per_point, billing_policy_version).';

COMMENT ON COLUMN llm_ask_config.runtime_defaults IS 'Runtime defaults for Ask Claread (max_input_tokens, max_output_tokens, prompt_buffer_tokens).';

CREATE TABLE llm_ask_options (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_created timestamp with time zone DEFAULT now() NOT NULL,
    date_updated timestamp with time zone,
    user_created uuid,
    user_updated uuid,
    slug text NOT NULL,
    label text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    selection jsonb,
    price_multiplier numeric DEFAULT 1.0 NOT NULL,
    runtime_budget jsonb,
    enabled boolean DEFAULT true NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    CONSTRAINT llm_ask_options_slug_check CHECK ((slug ~ '^[a-z0-9_-]+$'::text))
);

COMMENT ON TABLE llm_ask_options IS 'Ask Claread model option definitions. Each option represents a user-selectable model tier in the Ask panel.';

COMMENT ON COLUMN llm_ask_options.slug IS 'Unique key name used as JSON key when exporting. Persisted to reader_ask_threads.selected_model_key.';

COMMENT ON COLUMN llm_ask_options.selection IS 'ModelSelection object. Can reference a preset slug or define routes directly. Null means use backend defaults.';

COMMENT ON COLUMN llm_ask_options.price_multiplier IS 'Multiplier applied to billing_defaults for this option.';

COMMENT ON COLUMN llm_ask_options.runtime_budget IS 'Optional per-option runtime budget overrides (max_input_tokens, max_output_tokens, prompt_buffer_tokens).';

CREATE TABLE llm_models (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_created timestamp with time zone DEFAULT now() NOT NULL,
    date_updated timestamp with time zone,
    user_created uuid,
    user_updated uuid,
    slug text NOT NULL,
    provider uuid NOT NULL,
    model_name text NOT NULL,
    model_settings jsonb,
    provider_options jsonb,
    openai_profile jsonb,
    note text DEFAULT ''::text NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    CONSTRAINT llm_models_slug_check CHECK ((slug ~ '^[a-z0-9_-]+$'::text)),
    CONSTRAINT llm_models_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'deprecated'::text])))
);

COMMENT ON TABLE llm_models IS 'LLM model definitions. Each model references a provider and declares the remote model name.';

COMMENT ON COLUMN llm_models.slug IS 'Unique key name used as JSON key when exporting. Must match [a-z0-9_-]+.';

COMMENT ON COLUMN llm_models.provider IS 'FK to llm_providers. Determines transport adapter and auth.';

COMMENT ON COLUMN llm_models.model_name IS 'Remote model name as recognized by the provider API.';

COMMENT ON COLUMN llm_models.model_settings IS 'Model-level default settings, override provider-level defaults.';

COMMENT ON COLUMN llm_models.provider_options IS 'Model-level extension options, override provider-level options.';

COMMENT ON COLUMN llm_models.openai_profile IS 'Model-level OpenAI compatibility override, takes precedence over provider-level.';

CREATE TABLE llm_presets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_created timestamp with time zone DEFAULT now() NOT NULL,
    date_updated timestamp with time zone,
    user_created uuid,
    user_updated uuid,
    slug text NOT NULL,
    base_preset uuid,
    default_profile uuid,
    routes jsonb DEFAULT '{}'::jsonb NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    CONSTRAINT llm_presets_slug_check CHECK ((slug ~ '^[a-z0-9_]+$'::text)),
    CONSTRAINT llm_presets_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'deprecated'::text])))
);

COMMENT ON TABLE llm_presets IS 'LLM preset definitions. A preset is a named set of route→profile mappings, optionally inheriting from a base preset.';

COMMENT ON COLUMN llm_presets.slug IS 'Unique key name used as JSON key when exporting.';

COMMENT ON COLUMN llm_presets.base_preset IS 'Optional FK to another llm_presets entry for inheritance.';

COMMENT ON COLUMN llm_presets.default_profile IS 'Default profile when a route is not explicitly specified in routes.';

COMMENT ON COLUMN llm_presets.routes IS 'Route→selection mapping. Format: {route_name: {profile: slug, fallback_profiles: [...], model_settings: {...}}}. Route names must match ModelRoute Literal in services/api/app/llm/routes.py.';

CREATE TABLE llm_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_created timestamp with time zone DEFAULT now() NOT NULL,
    date_updated timestamp with time zone,
    user_created uuid,
    user_updated uuid,
    slug text NOT NULL,
    model uuid NOT NULL,
    model_settings jsonb,
    note text DEFAULT ''::text NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    CONSTRAINT llm_profiles_slug_check CHECK ((slug ~ '^[a-z0-9_-]+$'::text)),
    CONSTRAINT llm_profiles_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'deprecated'::text])))
);

COMMENT ON TABLE llm_profiles IS 'LLM profile definitions. Each profile binds a model to a business scenario with optional settings overrides.';

COMMENT ON COLUMN llm_profiles.slug IS 'Unique key name used as JSON key when exporting. Must match [a-z0-9_-]+.';

COMMENT ON COLUMN llm_profiles.model IS 'FK to llm_models. Determines which model this profile uses.';

COMMENT ON COLUMN llm_profiles.model_settings IS 'Profile-level settings override, highest priority in the provider < model < profile chain.';

CREATE TABLE llm_providers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_created timestamp with time zone DEFAULT now() NOT NULL,
    date_updated timestamp with time zone,
    user_created uuid,
    user_updated uuid,
    slug text NOT NULL,
    adapter text NOT NULL,
    base_url text DEFAULT ''::text NOT NULL,
    api_key_env text DEFAULT ''::text NOT NULL,
    provider_options jsonb DEFAULT '{}'::jsonb NOT NULL,
    openai_profile jsonb,
    model_settings jsonb,
    note text DEFAULT ''::text NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    CONSTRAINT llm_providers_adapter_check CHECK ((adapter = ANY (ARRAY['openai_compatible'::text, 'dashscope_native'::text, 'dashscope_embedding'::text, 'dashscope_rerank'::text]))),
    CONSTRAINT llm_providers_slug_check CHECK ((slug ~ '^[a-z0-9_]+$'::text)),
    CONSTRAINT llm_providers_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'deprecated'::text])))
);

COMMENT ON TABLE llm_providers IS 'LLM provider definitions. Control-plane authoring only; not read at runtime by services/api.';

COMMENT ON COLUMN llm_providers.slug IS 'Unique key name used as JSON key when exporting. Must match [a-z0-9_]+.';

COMMENT ON COLUMN llm_providers.adapter IS 'Transport adapter type. Must match ModelAdapter Literal in services/api/app/llm/types.py.';

COMMENT ON COLUMN llm_providers.base_url IS 'Required for openai_compatible. Empty for dashscope_native/embedding/rerank.';

COMMENT ON COLUMN llm_providers.api_key_env IS 'Environment variable name containing the API key.';

COMMENT ON COLUMN llm_providers.provider_options IS 'Provider-level extension options (e.g. dimension, transport, profile hint).';

COMMENT ON COLUMN llm_providers.openai_profile IS 'OpenAI compatibility capability declaration (json_object, thinking, tool_choice, etc.).';

COMMENT ON COLUMN llm_providers.model_settings IS 'Provider-level default model settings (temperature, max_tokens, etc.).';

COMMENT ON COLUMN llm_providers.status IS 'Lifecycle: draft → active → deprecated. Only active records are exported.';

CREATE TABLE original_inputs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    user_id uuid NOT NULL,
    input_type text NOT NULL,
    source_text text,
    source_ref_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    content_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_original_inputs_has_source CHECK (((source_text IS NOT NULL) OR (source_ref_json <> '{}'::jsonb))),
    CONSTRAINT original_inputs_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT original_inputs_input_type_check CHECK ((input_type = ANY (ARRAY['plain_text'::text, 'markdown'::text, 'file_ref'::text, 'url'::text, 'image_ref'::text])))
);

CREATE TABLE parsed_decisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    base_id uuid NOT NULL,
    unit_id text NOT NULL,
    policy_code text NOT NULL,
    parsed_state text NOT NULL,
    rationale_code text,
    coverage_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_layer_id uuid,
    source_job_id uuid,
    decision_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT parsed_decisions_parsed_state_check CHECK ((parsed_state = ANY (ARRAY['not_started'::text, 'partial'::text, 'parsed'::text, 'skipped'::text, 'failed'::text])))
);

CREATE TABLE pipeline_runs (
    id text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    stage text DEFAULT 'init'::text NOT NULL,
    stage_detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    candidates_found integer DEFAULT 0 NOT NULL,
    candidates_extracted integer DEFAULT 0 NOT NULL,
    candidates_scored integer DEFAULT 0 NOT NULL,
    articles_generated integer DEFAULT 0 NOT NULL,
    errors jsonb DEFAULT '[]'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pipeline_runs_stage_check CHECK ((stage = ANY (ARRAY['init'::text, 'discovery'::text, 'extraction'::text, 'scoring'::text, 'selection'::text, 'workflow'::text, 'cover_download'::text, 'storing'::text, 'done'::text]))),
    CONSTRAINT pipeline_runs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'completed'::text, 'failed'::text])))
);

COMMENT ON TABLE pipeline_runs IS '每日精读 pipeline 执行记录，用于追踪异步任务进度。';

COMMENT ON COLUMN pipeline_runs.stage IS '当前执行阶段。';

COMMENT ON COLUMN pipeline_runs.stage_detail IS '阶段详情，如发现的来源、评分分布等。';

COMMENT ON COLUMN pipeline_runs.errors IS '错误列表，每项含 stage + message。';

CREATE TABLE reader_article_rag_index_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    stable_document_id uuid NOT NULL,
    base_id uuid NOT NULL,
    record_generation integer NOT NULL,
    stable_document_content_sha256 text NOT NULL,
    canonical_text_sha256 text NOT NULL,
    plan_content_sha256 text NOT NULL,
    chunk_count integer NOT NULL,
    status text NOT NULL,
    embedding_model text,
    vector_store_provider text,
    vector_collection text,
    job_id uuid,
    reader_run_id uuid,
    error_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT ck_reader_article_rag_index_runs_jsonb_object CHECK (((jsonb_typeof(error_json) = 'object'::text) AND (jsonb_typeof(metadata_json) = 'object'::text))),
    CONSTRAINT ck_reader_article_rag_index_runs_sha256_format CHECK (((stable_document_content_sha256 ~ '^[0-9a-f]{64}$'::text) AND (canonical_text_sha256 ~ '^[0-9a-f]{64}$'::text) AND (plan_content_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT reader_article_rag_index_runs_chunk_count_check CHECK ((chunk_count >= 0)),
    CONSTRAINT reader_article_rag_index_runs_record_generation_check CHECK ((record_generation >= 1)),
    CONSTRAINT reader_article_rag_index_runs_status_check CHECK ((status = ANY (ARRAY['planned'::text, 'queued'::text, 'indexing'::text, 'indexed'::text, 'failed'::text, 'superseded'::text])))
);

COMMENT ON TABLE reader_article_rag_index_runs IS 'Persistent state for Article RAG index builds. One row per stable_document_id index attempt. The Article RAG index is a single path. Stores only truth-layer hashes and counts; never chunk text, Plate JSON, Markdown syntax, DOM selections, or Slate paths.';

COMMENT ON COLUMN reader_article_rag_index_runs.plan_content_sha256 IS 'SHA-256 of the deterministic plan content (chunk ids, content hashes, citation refs). Computed by compute_plan_content_sha256 in article_rag_index_plan.py.';

COMMENT ON COLUMN reader_article_rag_index_runs.embedding_model IS 'Nullable placeholder. Populated only when a later milestone calls an embedding provider. The current baseline leaves this NULL.';

COMMENT ON COLUMN reader_article_rag_index_runs.vector_store_provider IS 'Nullable placeholder. Populated only when a later milestone writes to Zilliz / Milvus. The current baseline leaves this NULL.';

CREATE TABLE reader_ask_client_submissions (
    thread_id uuid NOT NULL,
    client_submission_id uuid NOT NULL,
    user_id uuid NOT NULL,
    user_message_id uuid,
    assistant_message_id uuid,
    status text DEFAULT 'claimed'::text NOT NULL,
    claim_generation bigint DEFAULT 1 NOT NULL,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reader_ask_client_submissions_status_check CHECK ((status = ANY (ARRAY['claimed'::text, 'streaming'::text, 'completed'::text, 'failed'::text, 'cancelled'::text])))
);

COMMENT ON TABLE reader_ask_client_submissions IS 'Ask retry contract: atomic client submission claim. PK (thread_id, client_submission_id) prevents duplicate turns. status: claimed → streaming → completed|failed|cancelled. claim_generation CAS prevents stale-owner bind after reclaim.';

COMMENT ON COLUMN reader_ask_client_submissions.claim_generation IS 'Claim-generation CAS token. bind/terminal UPDATE must match generation.';

CREATE TABLE reader_ask_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    thread_id uuid NOT NULL,
    role text NOT NULL,
    status text DEFAULT 'completed'::text NOT NULL,
    content_md text DEFAULT ''::text NOT NULL,
    context_anchors_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    citations_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    action_proposals_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    tool_trace_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    usage_event_id uuid,
    current_turn_run_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reader_ask_messages_role_check CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text]))),
    CONSTRAINT reader_ask_messages_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'streaming'::text, 'completed'::text, 'failed'::text, 'interrupted'::text])))
);

COMMENT ON TABLE reader_ask_messages IS 'Reader Ask 会话消息表。保存 Markdown 输出、上下文锚点、引用来源、动作提议和工具轨迹。';

COMMENT ON COLUMN reader_ask_messages.context_anchors_json IS '本条消息显式挂载或解析出的上下文锚点列表。';

COMMENT ON COLUMN reader_ask_messages.citations_json IS '本条消息的引用来源列表，包括正文锚点、用户高亮、Reader 笔记和稳定补充资产。';

COMMENT ON COLUMN reader_ask_messages.action_proposals_json IS 'AI 提议的待确认动作列表。修改性动作需通过 HITL confirm endpoint 执行。';

COMMENT ON COLUMN reader_ask_messages.tool_trace_json IS 'Reader Ask 内部工具调用轨迹，用于前端调试与审计。';

COMMENT ON COLUMN reader_ask_messages.metadata_json IS 'Reader Ask 消息扩展元数据，包含 task_mode、resolved_context 和结构化卡片等。';

COMMENT ON COLUMN reader_ask_messages.current_turn_run_id IS '当前 assistant message 对应的最新用户可见 turn run。';

CREATE TABLE reader_ask_supplements (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    supplement_type text NOT NULL,
    target_key text,
    sentence_id text,
    paragraph_id text,
    title text NOT NULL,
    content_md text NOT NULL,
    anchor_payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    schema_version text NOT NULL,
    created_from_turn_run_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    reading_record_id uuid,
    base_id uuid,
    generation integer,
    unit_id text,
    anchor_segment_id text,
    start_offset integer,
    end_offset integer,
    text_hash text,
    hash_algorithm text,
    CONSTRAINT reader_ask_supplements_scope_check CHECK (((reading_record_id IS NOT NULL) AND (base_id IS NOT NULL) AND (generation IS NOT NULL) AND (generation >= 1) AND (unit_id IS NOT NULL) AND (anchor_segment_id IS NOT NULL) AND (start_offset IS NOT NULL) AND (start_offset >= 0) AND (end_offset IS NOT NULL) AND (end_offset > start_offset) AND (text_hash IS NOT NULL) AND (hash_algorithm IS NOT NULL)))
);

COMMENT ON TABLE reader_ask_supplements IS 'Reader Ask 生成并持久化到文章视图中的补充内容，如语法旁注。';

CREATE TABLE reader_ask_thread_memory (
    thread_id uuid NOT NULL,
    snapshot_json jsonb NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE reader_ask_thread_memory IS 'Ask thread memory snapshot——派生只读视图，可凭 canonical messages (reader_ask_messages + reader_ask_turn_runs final_status=ok) 完全重建。snapshot_json 形状见 ThreadMemorySnapshot 合同。version 自增用于 CAS 守卫（防并发轮竞争）。本表不替代 canonical messages 作为真相源；丢失不造成事实损失。';

CREATE TABLE reader_ask_threads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    title text,
    selected_model_key text,
    is_default boolean DEFAULT false NOT NULL,
    archived_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_message_at timestamp with time zone,
    reading_record_id uuid,
    CONSTRAINT reader_ask_threads_scope_check CHECK ((reading_record_id IS NOT NULL))
);

COMMENT ON TABLE reader_ask_threads IS 'Reader 内 Ask Claread 会话线程。按用户和文章绑定，支持默认线程与 New chat。';

COMMENT ON COLUMN reader_ask_threads.selected_model_key IS '线程当前生效的 Ask Claread 模型选项键。用户在线程中切换后，后续消息默认沿用该键。';

COMMENT ON COLUMN reader_ask_threads.is_default IS '是否为当前文章的默认线程。一个用户在同一篇文章下仅允许一个未归档默认线程。';

COMMENT ON COLUMN reader_ask_threads.last_message_at IS '最近一条消息时间，用于线程排序与续聊。';

CREATE TABLE reader_ask_turn_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    message_id uuid NOT NULL,
    thread_id uuid NOT NULL,
    user_id uuid NOT NULL,
    turn_id uuid NOT NULL,
    run_attempt integer DEFAULT 1 NOT NULL,
    supersedes_run_id uuid,
    status text NOT NULL,
    resolved_intent text,
    user_visible_output_json jsonb,
    usage_summary_json jsonb,
    usage_event_id uuid,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    failed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reading_record_id uuid,
    base_id uuid,
    generation integer,
    execution_version text,
    envelope_fingerprint text,
    envelope_snapshot_json jsonb,
    final_status text,
    terminal_reason text,
    resolved_evidence_json jsonb,
    reasoning_projection_json jsonb,
    CONSTRAINT reader_ask_turn_runs_scope_check CHECK (((reading_record_id IS NOT NULL) AND (base_id IS NOT NULL) AND (generation IS NOT NULL) AND (generation >= 1))),
    CONSTRAINT reader_ask_turn_runs_status_check CHECK ((status = ANY (ARRAY['streaming'::text, 'completed'::text, 'failed'::text, 'interrupted'::text, 'cancelled'::text, 'stale'::text])))
);

COMMENT ON TABLE reader_ask_turn_runs IS 'Reader Ask assistant 单轮运行记录。作为当前用户可见输出与 regenerate 历史的正式真相源。';

COMMENT ON COLUMN reader_ask_turn_runs.execution_version IS 'Agentic lane version (e.g. reader_record_ask_agentic_v1). NULL for legacy Ask turns.';

COMMENT ON COLUMN reader_ask_turn_runs.envelope_fingerprint IS 'SHA-256 fingerprint of the immutable Context Envelope for this agentic turn.';

COMMENT ON COLUMN reader_ask_turn_runs.envelope_snapshot_json IS 'Server-owned Context Envelope snapshot (JSON). Not client-writable.';

COMMENT ON COLUMN reader_ask_turn_runs.final_status IS 'Finalizer status: ok | context_stale | invalid_citations | failed | cancelled.';

COMMENT ON COLUMN reader_ask_turn_runs.terminal_reason IS 'Typed terminal failure/stale reason. No fabricated user answer.';

COMMENT ON COLUMN reader_ask_turn_runs.resolved_evidence_json IS 'Finalizer-resolved typed evidence array for agentic turns.';

COMMENT ON COLUMN reader_ask_turn_runs.reasoning_projection_json IS 'Safe reasoning projection committed atomically with the terminal snapshot (same UPDATE as the ok answer or typed terminal). Discriminator: projection_policy_version. Current write shape is provider_reasoning_v1: {projection_policy_version, text, char_count, truncated, visibility_status} — the deterministic redaction of the provider thinking stream, persisted on every normal terminal (ok or failed/cancelled), NULL when the turn produced no visible reasoning. Legacy learner_reasoning_v1 rows {projection_policy_version, schema, text, stage, basis, revision, sequence, generation_id, truncated} are read-compatible only (fail-closed cold validator) and no longer produced. Never carries raw provider reasoning, secrets, handles, or unredacted text.';

CREATE TABLE reader_event_sequences (
    reading_record_id uuid NOT NULL,
    next_sequence bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reader_event_sequences_next_sequence_check CHECK ((next_sequence >= 1))
);

CREATE TABLE reader_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    sequence bigint NOT NULL,
    event_type text NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_run_id uuid,
    source_job_id uuid,
    source_layer_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reader_events_event_type_check CHECK ((event_type = ANY (ARRAY['article_ready'::text, 'record_product_state_updated'::text, 'layer_published'::text, 'layer_failed'::text, 'parsed_decision_updated'::text, 'record_state_changed'::text, 'action_required'::text, 'run_completed'::text, 'record_superseded'::text, 'projection_ops'::text, 'projection_reset_required'::text]))),
    CONSTRAINT reader_events_sequence_check CHECK ((sequence >= 1))
);

CREATE TABLE reader_job_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    run_id uuid,
    job_id uuid NOT NULL,
    event_type text NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE reader_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    base_id uuid,
    run_id uuid NOT NULL,
    user_id uuid NOT NULL,
    job_type text NOT NULL,
    target_type text NOT NULL,
    target_key text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    claimed_at timestamp with time zone,
    pause_owner text,
    attempt_count integer DEFAULT 0 NOT NULL,
    transient_attempt_count integer DEFAULT 0 NOT NULL,
    repair_attempt_count integer DEFAULT 0 NOT NULL,
    replan_attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 1 NOT NULL,
    expected_generation integer NOT NULL,
    operation_fingerprint text NOT NULL,
    idempotency_key text NOT NULL,
    input_hash text,
    input_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_ref_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    rationale_code text,
    failure_class text,
    failure_code text,
    failure_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_reader_jobs_base_scope CHECK ((((job_type = 'build_base'::text) AND (target_type = 'record'::text) AND (base_id IS NULL)) OR ((job_type = 'input_artifact_extraction'::text) AND (target_type = 'record'::text) AND (base_id IS NULL)) OR ((job_type = 'extracted_artifact_materialization'::text) AND (target_type = 'record'::text) AND (base_id IS NULL)) OR ((NOT ((job_type = ANY (ARRAY['build_base'::text, 'input_artifact_extraction'::text, 'extracted_artifact_materialization'::text])) AND (target_type = 'record'::text))) AND (base_id IS NOT NULL)))),
    CONSTRAINT reader_jobs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT reader_jobs_expected_generation_check CHECK ((expected_generation >= 1)),
    CONSTRAINT reader_jobs_job_type_check CHECK ((job_type = ANY (ARRAY['build_base'::text, 'translate_unit'::text, 'build_vocabulary_layer'::text, 'build_grammar_bundle'::text, 'build_grammar_bundle_window'::text, 'input_artifact_extraction'::text, 'extracted_artifact_materialization'::text, 'article_rag_index_build'::text, 'generate_display_title_zh'::text, 'translate_article'::text, 'build_vocabulary_layer_article'::text, 'build_semantic_outline'::text]))),
    CONSTRAINT reader_jobs_max_attempts_check CHECK ((max_attempts >= 1)),
    CONSTRAINT reader_jobs_pause_owner_check CHECK ((pause_owner = ANY (ARRAY['user'::text, 'quota'::text, 'system'::text, 'policy'::text]))),
    CONSTRAINT reader_jobs_repair_attempt_count_check CHECK ((repair_attempt_count >= 0)),
    CONSTRAINT reader_jobs_replan_attempt_count_check CHECK ((replan_attempt_count >= 0)),
    CONSTRAINT reader_jobs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'claimed'::text, 'retry_later'::text, 'paused'::text, 'skipped'::text, 'succeeded'::text, 'failed_terminal'::text, 'cancelled'::text, 'superseded'::text]))),
    CONSTRAINT reader_jobs_target_type_check CHECK ((target_type = ANY (ARRAY['record'::text, 'unit'::text, 'anchor_segment'::text, 'unit_range'::text]))),
    CONSTRAINT reader_jobs_transient_attempt_count_check CHECK ((transient_attempt_count >= 0))
);

CREATE TABLE ai_model_execution_journal (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    invocation_key text NOT NULL,
    invocation_kind text NOT NULL,
    reader_job_id uuid,
    reader_run_id uuid,
    attempt_ordinal integer NOT NULL,
    execution_slot integer NOT NULL,
    capture_state text DEFAULT 'started'::text NOT NULL,
    usage_delivery_state text DEFAULT 'not_ready'::text NOT NULL,
    resume_payload_kind text,
    resume_payload_schema_version integer,
    usage_event_draft_schema_version integer,
    normalized_payload_json jsonb,
    usage_event_draft_json jsonb,
    capture_envelope_sha256 text,
    resume_payload_bytes integer,
    usage_event_draft_bytes integer,
    ai_usage_event_id uuid,
    delivery_attempt_count integer DEFAULT 0 NOT NULL,
    delivery_next_attempt_at timestamp with time zone,
    delivery_last_error_code text,
    delivery_last_error_message text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    captured_at timestamp with time zone,
    ambiguous_at timestamp with time zone,
    reconciled_at timestamp with time zone,
    dead_lettered_at timestamp with time zone,
    payload_purged_at timestamp with time zone,
    business_terminal_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ai_model_execution_journal_attempt_ordinal_check CHECK ((attempt_ordinal >= 1)),
    CONSTRAINT ai_model_execution_journal_capture_state_check CHECK ((capture_state = ANY (ARRAY['started'::text, 'captured'::text, 'ambiguous'::text]))),
    CONSTRAINT ai_model_execution_journal_delivery_attempt_count_check CHECK ((delivery_attempt_count >= 0)),
    CONSTRAINT ai_model_execution_journal_delivery_state_check CHECK ((usage_delivery_state = ANY (ARRAY['not_ready'::text, 'pending'::text, 'reconciled'::text, 'dead_letter'::text]))),
    CONSTRAINT ai_model_execution_journal_execution_slot_check CHECK ((execution_slot >= 1)),
    CONSTRAINT ai_model_execution_journal_payload_size_check CHECK (((resume_payload_bytes IS NULL) OR ((resume_payload_bytes >= 0) AND (resume_payload_bytes <= 1048576))) AND ((usage_event_draft_bytes IS NULL) OR ((usage_event_draft_bytes >= 0) AND (usage_event_draft_bytes <= 65536))) AND (((resume_payload_bytes IS NULL) OR (usage_event_draft_bytes IS NULL)) OR ((resume_payload_bytes + usage_event_draft_bytes) <= 1114112))),
    CONSTRAINT ai_model_execution_journal_capture_payload_check CHECK ((((capture_state = ANY (ARRAY['started'::text, 'ambiguous'::text])) AND (resume_payload_kind IS NULL) AND (resume_payload_schema_version IS NULL) AND (usage_event_draft_schema_version IS NULL) AND (normalized_payload_json IS NULL) AND (usage_event_draft_json IS NULL) AND (capture_envelope_sha256 IS NULL) AND (resume_payload_bytes IS NULL) AND (usage_event_draft_bytes IS NULL) AND (captured_at IS NULL) AND (payload_purged_at IS NULL)) OR ((capture_state = 'captured'::text) AND (resume_payload_kind IS NOT NULL) AND (resume_payload_schema_version >= 1) AND (usage_event_draft_schema_version >= 1) AND ((normalized_payload_json IS NOT NULL) OR ((payload_purged_at IS NOT NULL) AND (usage_delivery_state = 'reconciled'::text))) AND (usage_event_draft_json IS NOT NULL) AND (capture_envelope_sha256 ~ '^[0-9a-f]{64}$'::text) AND (resume_payload_bytes IS NOT NULL) AND (usage_event_draft_bytes IS NOT NULL) AND (captured_at IS NOT NULL)))),
    CONSTRAINT ai_model_execution_journal_state_matrix_check CHECK ((((capture_state = ANY (ARRAY['started'::text, 'ambiguous'::text])) AND (usage_delivery_state = 'not_ready'::text)) OR ((capture_state = 'captured'::text) AND (usage_delivery_state = ANY (ARRAY['pending'::text, 'reconciled'::text, 'dead_letter'::text]))))),
    CONSTRAINT ai_model_execution_journal_state_timestamps_check CHECK ((((capture_state = 'ambiguous'::text) AND (ambiguous_at IS NOT NULL)) OR ((capture_state <> 'ambiguous'::text) AND (ambiguous_at IS NULL))) AND (((usage_delivery_state = 'reconciled'::text) AND (reconciled_at IS NOT NULL)) OR ((usage_delivery_state <> 'reconciled'::text) AND (reconciled_at IS NULL))) AND (((usage_delivery_state = 'dead_letter'::text) AND (dead_lettered_at IS NOT NULL)) OR ((usage_delivery_state <> 'dead_letter'::text) AND (dead_lettered_at IS NULL)))),
    CONSTRAINT ai_model_execution_journal_usage_event_link_check CHECK ((((usage_delivery_state = 'reconciled'::text) AND (ai_usage_event_id IS NOT NULL)) OR ((usage_delivery_state <> 'reconciled'::text) AND (ai_usage_event_id IS NULL))))
);

CREATE TABLE reader_notes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    quote_mode text NOT NULL,
    target_key text NOT NULL,
    paragraph_id text,
    sentence_id text,
    selected_text text NOT NULL,
    start_offset integer,
    end_offset integer,
    text_hash text,
    note_text text NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reading_record_id uuid,
    base_id uuid,
    generation integer,
    unit_id text,
    anchor_segment_id text,
    unit_start_utf16 integer,
    unit_end_utf16 integer,
    CONSTRAINT reader_notes_quote_mode_check CHECK ((quote_mode = ANY (ARRAY['sentence'::text, 'text_range'::text, 'multi_text'::text])))
);

COMMENT ON TABLE reader_notes IS 'Reader 笔记表，保存用户对句子或文本选区的引用式笔记。';

COMMENT ON COLUMN reader_notes.quote_mode IS '引用模式：sentence、text_range、multi_text。';

COMMENT ON COLUMN reader_notes.target_key IS '引用 identity 的逻辑键，用于 exact-hit 命中已有笔记。';

COMMENT ON COLUMN reader_notes.note_text IS '用户笔记正文。';

COMMENT ON COLUMN reader_notes.payload_json IS '笔记引用附加信息 JSON，至少包含 quoted_text 与 segments。';

CREATE TABLE reader_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    user_id uuid NOT NULL,
    run_type text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    record_generation integer NOT NULL,
    envelope_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    policy_version text NOT NULL,
    trigger_kind text NOT NULL,
    failure_class text,
    failure_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reader_runs_record_generation_check CHECK ((record_generation >= 1)),
    CONSTRAINT reader_runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'waiting_user'::text, 'waiting_quota'::text, 'paused'::text, 'completed'::text, 'failed_retryable'::text, 'failed_terminal'::text, 'cancelled'::text, 'superseded'::text])))
);

CREATE TABLE reader_runtime_spans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trace_id uuid NOT NULL,
    parent_span_id uuid,
    span_kind text NOT NULL,
    reader_run_id uuid,
    reader_job_id uuid,
    reading_record_id uuid,
    worker_type text,
    model_route text,
    model_name text,
    model_provider text,
    capability_code text,
    ai_usage_event_id uuid,
    attempt_number integer,
    retry_class text,
    status text NOT NULL,
    failure_class text,
    failure_code text,
    claim_wait_ms integer,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    duration_ms integer,
    input_tokens integer,
    output_tokens integer,
    total_tokens integer,
    cache_read_tokens integer,
    cache_write_tokens integer,
    langsmith_run_id text,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reader_runtime_spans_retry_class_check CHECK ((retry_class = ANY (ARRAY['transient'::text, 'repair'::text, 'replan'::text]))),
    CONSTRAINT reader_runtime_spans_span_kind_check CHECK ((span_kind = ANY (ARRAY['pipeline_root'::text, 'worker_tick'::text, 'publish_fence'::text, 'claim'::text]))),
    CONSTRAINT reader_runtime_spans_status_check CHECK ((status = ANY (ARRAY['started'::text, 'succeeded'::text, 'failed'::text, 'superseded'::text, 'skipped'::text]))),
    CONSTRAINT reader_runtime_spans_worker_type_check CHECK ((worker_type = ANY (ARRAY['display_title'::text, 'translation'::text, 'vocabulary'::text, 'grammar_bundle'::text, 'grammar_bundle_window'::text, 'article_rag_index'::text, 'artifact_extraction'::text, 'artifact_materialization'::text, 'translation_batch'::text, 'vocabulary_batch'::text, 'semantic_outline'::text])))
);

CREATE TABLE reading_bases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    base_version integer NOT NULL,
    record_generation integer NOT NULL,
    text text NOT NULL,
    content_sha256 text NOT NULL,
    content_utf16_length integer NOT NULL,
    canonicalizer_version text NOT NULL,
    builder_version text NOT NULL,
    segmenter_version text NOT NULL,
    language text,
    title_snapshot text,
    navigation_json jsonb DEFAULT '{"units": []}'::jsonb NOT NULL,
    diagnostics_json jsonb DEFAULT '{"version": "stable_annotation_diagnostics_v1", "items": []}'::jsonb NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    frozen_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_reading_bases_content_sha256 CHECK ((content_sha256 = encode(digest(text, 'sha256'::text), 'hex'::text))),
    CONSTRAINT ck_reading_bases_content_utf16_length CHECK ((content_utf16_length = utf16_code_unit_length(text))),
    CONSTRAINT reading_bases_base_version_check CHECK ((base_version >= 1)),
    CONSTRAINT reading_bases_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT reading_bases_content_utf16_length_check CHECK ((content_utf16_length >= 1)),
    CONSTRAINT reading_bases_record_generation_check CHECK ((record_generation >= 1)),
    CONSTRAINT reading_bases_status_check CHECK ((status = ANY (ARRAY['active'::text, 'superseded'::text]))),
    CONSTRAINT reading_bases_text_check CHECK ((text <> ''::text))
);

CREATE TABLE reading_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    client_record_id text,
    source_type text NOT NULL,
    title text,
    language text,
    lifecycle_status text DEFAULT 'active'::text NOT NULL,
    product_state text DEFAULT 'processing'::text NOT NULL,
    readiness_state text DEFAULT 'submitted'::text NOT NULL,
    generation integer DEFAULT 1 NOT NULL,
    active_base_id uuid,
    superseded_by_record_id uuid,
    deleted_at timestamp with time zone,
    last_opened_at timestamp with time zone,
    recent_hidden_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    generated_title_zh text,
    title_generation_status text DEFAULT 'pending'::text NOT NULL,
    title_generation_error_code text,
    title_generation_error_message text,
    title_generation_attempt_count integer DEFAULT 0 NOT NULL,
    title_generation_updated_at timestamp with time zone,
    reading_goal text DEFAULT 'daily_reading'::text NOT NULL,
    reading_variant text DEFAULT 'intermediate_reading'::text NOT NULL,
    CONSTRAINT ck_reading_records_generated_title_zh_succeeded CHECK (((title_generation_status <> 'succeeded'::text) OR ((generated_title_zh IS NOT NULL) AND (btrim(generated_title_zh) <> ''::text)))),
    CONSTRAINT ck_reading_records_reading_goal CHECK ((reading_goal = ANY (ARRAY['daily_reading'::text, 'exam'::text]))),
    CONSTRAINT ck_reading_records_reading_variant CHECK ((reading_variant = ANY (ARRAY['beginner_reading'::text, 'intermediate_reading'::text, 'intensive_reading'::text, 'gaokao'::text, 'cet'::text, 'kaoyan'::text, 'tem'::text, 'ielts_toefl'::text]))),
    CONSTRAINT ck_reading_records_reading_variant_belongs_to_goal CHECK ((((reading_goal = 'daily_reading'::text) AND (reading_variant = ANY (ARRAY['beginner_reading'::text, 'intermediate_reading'::text, 'intensive_reading'::text]))) OR ((reading_goal = 'exam'::text) AND (reading_variant = ANY (ARRAY['gaokao'::text, 'cet'::text, 'kaoyan'::text, 'tem'::text, 'ielts_toefl'::text]))))),
    CONSTRAINT ck_reading_records_title_generation_status CHECK ((title_generation_status = ANY (ARRAY['pending'::text, 'succeeded'::text, 'failed_retryable'::text]))),
    CONSTRAINT reading_records_generation_check CHECK ((generation >= 1)),
    CONSTRAINT reading_records_lifecycle_status_check CHECK ((lifecycle_status = ANY (ARRAY['active'::text, 'cancelled'::text, 'superseded'::text, 'deleted'::text]))),
    CONSTRAINT reading_records_product_state_check CHECK ((product_state = ANY (ARRAY['processing'::text, 'needs_confirmation'::text, 'readable_enhancing'::text, 'action_required'::text, 'failed'::text, 'deleted'::text]))),
    CONSTRAINT reading_records_readiness_state_check CHECK ((readiness_state = ANY (ARRAY['submitted'::text, 'candidate_base_ready'::text, 'article_ready'::text, 'initial_enhancement_ready'::text, 'coverage_complete'::text]))),
    CONSTRAINT reading_records_source_type_check CHECK ((source_type = ANY (ARRAY['text'::text, 'markdown'::text, 'file'::text, 'url'::text, 'pdf'::text, 'ocr'::text, 'image'::text])))
);

COMMENT ON COLUMN reading_records.generated_title_zh IS 'LLM-generated Simplified Chinese display title for Reader masthead. Generated from bounded stable-base preview, never by the frontend.';

COMMENT ON COLUMN reading_records.title_generation_status IS 'State for generated_title_zh: pending, succeeded, or failed_retryable. Missing Chinese title is never represented as a successful state.';

COMMENT ON COLUMN reading_records.title_generation_error_message IS 'Sanitized retryable failure reason for the Chinese display-title worker.';

COMMENT ON COLUMN reading_records.reading_goal IS 'Reader strategy goal (daily_reading | exam). First-class fact; do not infer from source_metadata. academic is intentionally not wired into the new orchestration.';

COMMENT ON COLUMN reading_records.reading_variant IS 'Reader strategy variant scoped to reading_goal. First-class fact; do not infer from source_metadata.';

CREATE TABLE reading_units (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    base_id uuid NOT NULL,
    unit_id text NOT NULL,
    order_index integer NOT NULL,
    unit_type text NOT NULL,
    boundary_quality text DEFAULT 'normal'::text NOT NULL,
    base_start_utf16 integer NOT NULL,
    base_end_utf16 integer NOT NULL,
    text_hash text NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_reading_units_offsets CHECK (((base_start_utf16 >= 0) AND (base_end_utf16 > base_start_utf16))),
    CONSTRAINT reading_units_boundary_quality_check CHECK ((boundary_quality = ANY (ARRAY['normal'::text, 'low'::text]))),
    CONSTRAINT reading_units_order_index_check CHECK ((order_index >= 1)),
    CONSTRAINT reading_units_text_hash_check CHECK ((text_hash ~ '^[0-9a-f]{8}$'::text)),
    CONSTRAINT reading_units_unit_type_check CHECK ((unit_type = ANY (ARRAY['body'::text, 'heading'::text, 'list'::text, 'quote'::text, 'unknown'::text, 'fallback'::text])))
);

CREATE TABLE source_artifacts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid,
    original_input_id uuid,
    user_id uuid NOT NULL,
    artifact_kind text NOT NULL,
    storage_provider text NOT NULL,
    bucket text,
    object_key text NOT NULL,
    endpoint text,
    content_type text,
    byte_size bigint,
    content_sha256 text,
    source_filename text,
    status text DEFAULT 'available'::text NOT NULL,
    source_refs_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    quality_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT source_artifacts_artifact_kind_check CHECK ((artifact_kind = ANY (ARRAY['original_upload'::text, 'pdf_page_image'::text, 'ocr_result'::text, 'extracted_text'::text, 'webpage_snapshot'::text, 'derived_preview'::text]))),
    CONSTRAINT source_artifacts_byte_size_check CHECK (((byte_size IS NULL) OR (byte_size >= 0))),
    CONSTRAINT source_artifacts_content_sha256_check CHECK (((content_sha256 IS NULL) OR (content_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT source_artifacts_metadata_json_check CHECK ((jsonb_typeof(metadata_json) = 'object'::text)),
    CONSTRAINT source_artifacts_quality_json_check CHECK ((jsonb_typeof(quality_json) = 'object'::text)),
    CONSTRAINT source_artifacts_source_refs_json_check CHECK ((jsonb_typeof(source_refs_json) = 'object'::text)),
    CONSTRAINT source_artifacts_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'available'::text, 'failed'::text, 'deleted'::text]))),
    CONSTRAINT source_artifacts_storage_provider_check CHECK ((storage_provider = ANY (ARRAY['oss'::text, 'local'::text])))
);

-- stable_document_blocks.interpretation_policy_json DEFAULT '{}'::jsonb is a
-- storage placeholder only (interpretation-policy storage contract): the service code is
-- responsible for writing the Python-model-generated per-block-type policy
-- (see default_interpretation_policy_for) into the column, so the DB default
-- is never relied on at runtime. Do not "fix" the DB default to match the
-- Python default; that would silently couple storage defaults to projection
-- rules.
CREATE TABLE stable_document_blocks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    stable_document_id uuid NOT NULL,
    block_id text NOT NULL,
    parent_block_id text,
    order_index integer NOT NULL,
    block_type text NOT NULL,
    text_content text,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_refs_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    canonical_text_start_utf16 integer,
    canonical_text_end_utf16 integer,
    interpretation_policy_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    quality_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_stable_document_blocks_canonical_text_offsets CHECK ((((canonical_text_start_utf16 IS NULL) AND (canonical_text_end_utf16 IS NULL)) OR ((canonical_text_start_utf16 IS NOT NULL) AND (canonical_text_end_utf16 IS NOT NULL) AND (canonical_text_end_utf16 > canonical_text_start_utf16)))),
    CONSTRAINT ck_stable_document_blocks_text_for_textual_types CHECK (((block_type = ANY (ARRAY['list'::text, 'table'::text, 'table_row'::text, 'table_cell'::text, 'image'::text, 'code_block'::text, 'thematic_break'::text, 'unknown'::text])) OR ((text_content IS NOT NULL) AND (length(text_content) > 0)))),
    CONSTRAINT stable_document_blocks_block_type_check CHECK ((block_type = ANY (ARRAY['paragraph'::text, 'heading'::text, 'list'::text, 'list_item'::text, 'blockquote'::text, 'table'::text, 'table_row'::text, 'table_cell'::text, 'footnote'::text, 'image'::text, 'image_ocr'::text, 'caption'::text, 'code_block'::text, 'thematic_break'::text, 'unknown'::text]))),
    CONSTRAINT stable_document_blocks_canonical_text_end_utf16_check CHECK (((canonical_text_end_utf16 IS NULL) OR (canonical_text_end_utf16 >= 0))),
    CONSTRAINT stable_document_blocks_canonical_text_start_utf16_check CHECK (((canonical_text_start_utf16 IS NULL) OR (canonical_text_start_utf16 >= 0))),
    CONSTRAINT stable_document_blocks_check CHECK (((parent_block_id IS NULL) OR (parent_block_id <> block_id))),
    CONSTRAINT stable_document_blocks_interpretation_policy_json_check CHECK ((jsonb_typeof(interpretation_policy_json) = 'object'::text)),
    CONSTRAINT stable_document_blocks_order_index_check CHECK ((order_index >= 0)),
    CONSTRAINT stable_document_blocks_payload_json_check CHECK ((jsonb_typeof(payload_json) = 'object'::text)),
    CONSTRAINT stable_document_blocks_quality_json_check CHECK ((jsonb_typeof(quality_json) = 'object'::text)),
    CONSTRAINT stable_document_blocks_source_refs_json_check CHECK ((jsonb_typeof(source_refs_json) = 'object'::text))
);

CREATE TABLE stable_image_source_overrides (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    stable_document_id uuid NOT NULL,
    block_id text NOT NULL,
    inline_ordinal integer,
    override_url text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_stable_image_override_ordinal CHECK (((inline_ordinal IS NULL) OR (inline_ordinal >= 0)))
);

CREATE TABLE stable_reading_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reading_record_id uuid NOT NULL,
    record_generation integer NOT NULL,
    title text,
    document_version integer NOT NULL,
    source_profile_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    content_sha256 text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    frozen_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT stable_reading_documents_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT stable_reading_documents_document_version_check CHECK ((document_version >= 1)),
    CONSTRAINT stable_reading_documents_record_generation_check CHECK ((record_generation >= 1)),
    CONSTRAINT stable_reading_documents_source_profile_json_check CHECK ((jsonb_typeof(source_profile_json) = 'object'::text)),
    CONSTRAINT stable_reading_documents_status_check CHECK ((status = ANY (ARRAY['active'::text, 'superseded'::text])))
);

CREATE TABLE user_annotations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    anchor_type text DEFAULT 'text_range'::text NOT NULL,
    target_key text NOT NULL,
    paragraph_id text,
    sentence_id text,
    selected_text text NOT NULL,
    start_offset integer,
    end_offset integer,
    text_hash text,
    color text DEFAULT 'warm_yellow'::text NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reading_record_id uuid,
    base_id uuid,
    generation integer,
    unit_id text,
    anchor_segment_id text,
    unit_start_utf16 integer,
    unit_end_utf16 integer,
    CONSTRAINT user_annotations_anchor_type_check CHECK ((anchor_type = 'text_range'::text)),
    CONSTRAINT user_annotations_color_check CHECK ((color = ANY (ARRAY['warm_yellow'::text, 'soft_mint'::text, 'soft_rose'::text]))),
    CONSTRAINT user_annotations_text_anchor_payload_check CHECK (((anchor_type = 'text_range'::text) AND (reading_record_id IS NOT NULL) AND (base_id IS NOT NULL) AND (generation IS NOT NULL) AND (generation >= 1) AND (unit_id IS NOT NULL) AND (anchor_segment_id IS NOT NULL) AND (unit_start_utf16 IS NOT NULL) AND (unit_start_utf16 >= 0) AND (unit_end_utf16 IS NOT NULL) AND (unit_end_utf16 > unit_start_utf16) AND (text_hash IS NOT NULL) AND (paragraph_id IS NULL) AND (sentence_id IS NULL) AND (start_offset IS NULL) AND (end_offset IS NULL)))
);

COMMENT ON TABLE user_annotations IS '用户高亮表，保存用户高亮标注。';

COMMENT ON COLUMN user_annotations.id IS '批注主键，使用 UUID。';

COMMENT ON COLUMN user_annotations.user_id IS '所属用户 ID。';

COMMENT ON COLUMN user_annotations.anchor_type IS '锚点类型：sentence（句子）、text_range（文本范围）、multi_text（跨句范围）。';

COMMENT ON COLUMN user_annotations.target_key IS '批注目标的逻辑键，用于唯一定位批注对象。';

COMMENT ON COLUMN user_annotations.paragraph_id IS '段落 ID。';

COMMENT ON COLUMN user_annotations.sentence_id IS '句子 ID。';

COMMENT ON COLUMN user_annotations.selected_text IS '用户选中的文本内容。';

COMMENT ON COLUMN user_annotations.start_offset IS '选区起始偏移量。';

COMMENT ON COLUMN user_annotations.end_offset IS '选区结束偏移量。';

COMMENT ON COLUMN user_annotations.text_hash IS '选中文本的哈希值。';

COMMENT ON COLUMN user_annotations.color IS '用户高亮颜色，固定支持 warm_yellow、soft_mint、soft_rose。';

COMMENT ON COLUMN user_annotations.payload_json IS '批注附加元数据 JSON。';

COMMENT ON COLUMN user_annotations.created_at IS '记录创建时间。';

COMMENT ON COLUMN user_annotations.updated_at IS '记录最后更新时间。';

CREATE TABLE user_credit_accounts (
    user_id uuid NOT NULL,
    daily_free_points integer DEFAULT 1000 NOT NULL,
    daily_used_points integer DEFAULT 0 NOT NULL,
    bonus_points integer DEFAULT 0 NOT NULL,
    last_reset_on date DEFAULT CURRENT_DATE NOT NULL,
    policy_version text DEFAULT 'v1'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE user_credit_accounts IS '用户积分账户快照，每用户一行。';

COMMENT ON COLUMN user_credit_accounts.daily_free_points IS '每日免费额度（默认 1000 积分，1 积分 = 1000 加权 token）。';

COMMENT ON COLUMN user_credit_accounts.daily_used_points IS '今日已使用积分。';

COMMENT ON COLUMN user_credit_accounts.bonus_points IS '活动赠送/人工补偿/邀请码奖励等长期积分。';

COMMENT ON COLUMN user_credit_accounts.last_reset_on IS '最近一次每日积分重置日期。';

COMMENT ON COLUMN user_credit_accounts.policy_version IS '积分策略版本号。';

CREATE TABLE user_credit_ledger (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    subject_type text,
    subject_id text,
    reading_record_id uuid,
    reader_run_id uuid,
    reader_job_id uuid,
    entry_type text NOT NULL,
    points integer NOT NULL,
    bucket_type text DEFAULT 'daily_free'::text NOT NULL,
    balance_after integer NOT NULL,
    title_snapshot text,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_credit_ledger_bucket_type_check CHECK ((bucket_type = ANY (ARRAY['daily_free'::text, 'bonus'::text]))),
    CONSTRAINT user_credit_ledger_entry_type_check CHECK ((entry_type = ANY (ARRAY['daily_grant'::text, 'bonus_grant'::text, 'analysis_deduct'::text, 'ai_capability_deduct'::text, 'manual_adjust'::text, 'refund'::text, 'feedback_reward'::text])))
);

COMMENT ON TABLE user_credit_ledger IS '积分流水账本，append-only，所有积分变动均记录。';

COMMENT ON COLUMN user_credit_ledger.entry_type IS '流水类型：daily_grant, bonus_grant, analysis_deduct, ai_capability_deduct, manual_adjust, refund, feedback_reward。';

COMMENT ON COLUMN user_credit_ledger.points IS '变动积分数（正为增加，负为扣减）。';

COMMENT ON COLUMN user_credit_ledger.bucket_type IS '积分桶类型：daily_free 或 bonus。';

COMMENT ON COLUMN user_credit_ledger.balance_after IS '变动后余额。';

COMMENT ON COLUMN user_credit_ledger.metadata_json IS '扩展元数据 JSON，如 { input_tokens, output_tokens, multiplier_input, multiplier_output }。';

CREATE TABLE user_identities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    provider text NOT NULL,
    provider_user_id text NOT NULL,
    unionid text,
    app_id text,
    auth_payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

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

CREATE TABLE user_password_credentials (
    user_id uuid NOT NULL,
    password_hash text NOT NULL,
    password_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE user_password_credentials IS '用户密码凭证表，保存邮箱密码登录的密码哈希；一个用户最多一条凭证。';

COMMENT ON COLUMN user_password_credentials.user_id IS '用户主键，同时作为外键指向 users(id)，删除用户时级联删除。';

COMMENT ON COLUMN user_password_credentials.password_hash IS 'Argon2id 编码后的完整密码哈希字符串；本表只存哈希，绝不存明文。';

COMMENT ON COLUMN user_password_credentials.password_changed_at IS '最近一次创建或重置密码的 UTC 时间。';

COMMENT ON COLUMN user_password_credentials.created_at IS '记录创建时间。';

COMMENT ON COLUMN user_password_credentials.updated_at IS '记录最后更新时间。';

CREATE TABLE user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    session_token_hash text NOT NULL,
    refresh_token_hash text,
    client_platform text DEFAULT 'wechat_miniprogram'::text NOT NULL,
    device_id text,
    device_name text,
    app_version text,
    ip_address inet,
    user_agent text,
    status text DEFAULT 'active'::text NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    refresh_expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_sessions_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text, 'expired'::text])))
);

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

CREATE TABLE users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    display_name text,
    avatar_url text,
    locale text DEFAULT 'zh-CN'::text NOT NULL,
    timezone text DEFAULT 'Asia/Shanghai'::text NOT NULL,
    cumulative_article_count integer DEFAULT 0 NOT NULL,
    last_active_at timestamp with time zone,
    settings_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_status_check CHECK ((status = ANY (ARRAY['active'::text, 'disabled'::text, 'deleted'::text])))
);

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

CREATE TABLE vocabulary_book (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    lemma text NOT NULL,
    display_word text NOT NULL,
    phonetic text,
    part_of_speech text,
    short_meaning text NOT NULL,
    meanings_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    tags text[] DEFAULT ARRAY[]::text[] NOT NULL,
    exchange text[] DEFAULT ARRAY[]::text[] NOT NULL,
    source_provider text DEFAULT 'tecd3'::text NOT NULL,
    dict_entry_id bigint,
    source_sentence text,
    source_context text,
    mastery_status text DEFAULT 'new'::text NOT NULL,
    review_count integer DEFAULT 0 NOT NULL,
    last_reviewed_at timestamp with time zone,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vocabulary_book_mastery_status_check CHECK ((mastery_status = ANY (ARRAY['new'::text, 'learning'::text, 'review'::text, 'mastered'::text, 'archived'::text])))
);

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

DO $dict$
BEGIN
ALTER TABLE ONLY dict_entries ALTER COLUMN id SET DEFAULT nextval('dict_entries_id_seq'::regclass);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

DO $dict$
BEGIN
ALTER TABLE ONLY dict_lookup_targets ALTER COLUMN id SET DEFAULT nextval('dict_lookup_targets_id_seq'::regclass);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

DO $dict$
BEGIN
ALTER TABLE ONLY dict_redirects ALTER COLUMN id SET DEFAULT nextval('dict_redirects_id_seq'::regclass);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

ALTER TABLE ONLY ai_usage_events
    ADD CONSTRAINT ai_usage_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ai_model_execution_journal
    ADD CONSTRAINT ai_model_execution_journal_pkey PRIMARY KEY (id);

ALTER TABLE ONLY analysis_windows
    ADD CONSTRAINT analysis_windows_pkey PRIMARY KEY (id);

ALTER TABLE ONLY analysis_windows
    ADD CONSTRAINT analysis_windows_plan_id_window_index_key UNIQUE (plan_id, window_index);

ALTER TABLE ONLY anchor_segments
    ADD CONSTRAINT anchor_segments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY anonymous_quotas
    ADD CONSTRAINT anonymous_quotas_pkey PRIMARY KEY (anonymous_id);

ALTER TABLE ONLY candidate_reading_documents
    ADD CONSTRAINT candidate_reading_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY confirmed_source_documents
    ADD CONSTRAINT confirmed_source_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY confirmed_source_revisions
    ADD CONSTRAINT confirmed_source_revisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY daily_readers
    ADD CONSTRAINT daily_readers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY dict_ai_candidate_entries
    ADD CONSTRAINT dict_ai_candidate_entries_pkey PRIMARY KEY (id);

DO $dict$
BEGIN
ALTER TABLE ONLY dict_entries
    ADD CONSTRAINT dict_entries_pkey PRIMARY KEY (id);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

DO $dict$
BEGIN
ALTER TABLE ONLY dict_lookup_targets
    ADD CONSTRAINT dict_lookup_targets_pkey PRIMARY KEY (id);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

DO $dict$
BEGIN
ALTER TABLE ONLY dict_redirects
    ADD CONSTRAINT dict_redirects_pkey PRIMARY KEY (id);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

ALTER TABLE ONLY enhancement_layers
    ADD CONSTRAINT enhancement_layers_pkey PRIMARY KEY (id);

DO $dict$
BEGIN
ALTER TABLE ONLY eval_example_lab_entries
    ADD CONSTRAINT eval_example_lab_entries_example_id_key UNIQUE (example_id);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

DO $dict$
BEGIN
ALTER TABLE ONLY eval_example_lab_entries
    ADD CONSTRAINT eval_example_lab_entries_pkey PRIMARY KEY (id);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

ALTER TABLE ONLY favorite_records
    ADD CONSTRAINT favorite_records_pkey PRIMARY KEY (id);

ALTER TABLE ONLY feedback
    ADD CONSTRAINT feedback_pkey PRIMARY KEY (id);

ALTER TABLE ONLY layer_analysis_plans
    ADD CONSTRAINT layer_analysis_plans_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_ask_config
    ADD CONSTRAINT llm_ask_config_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_ask_options
    ADD CONSTRAINT llm_ask_options_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_ask_options
    ADD CONSTRAINT llm_ask_options_slug_key UNIQUE (slug);

ALTER TABLE ONLY llm_models
    ADD CONSTRAINT llm_models_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_models
    ADD CONSTRAINT llm_models_slug_key UNIQUE (slug);

ALTER TABLE ONLY llm_presets
    ADD CONSTRAINT llm_presets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_presets
    ADD CONSTRAINT llm_presets_slug_key UNIQUE (slug);

ALTER TABLE ONLY llm_profiles
    ADD CONSTRAINT llm_profiles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_profiles
    ADD CONSTRAINT llm_profiles_slug_key UNIQUE (slug);

ALTER TABLE ONLY llm_providers
    ADD CONSTRAINT llm_providers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_providers
    ADD CONSTRAINT llm_providers_slug_key UNIQUE (slug);

ALTER TABLE ONLY original_inputs
    ADD CONSTRAINT original_inputs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY parsed_decisions
    ADD CONSTRAINT parsed_decisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY pipeline_runs
    ADD CONSTRAINT pipeline_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_article_rag_index_runs
    ADD CONSTRAINT reader_article_rag_index_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_ask_client_submissions
    ADD CONSTRAINT reader_ask_client_submissions_pkey PRIMARY KEY (thread_id, client_submission_id);

ALTER TABLE ONLY reader_ask_messages
    ADD CONSTRAINT reader_ask_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_ask_supplements
    ADD CONSTRAINT reader_ask_supplements_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_ask_thread_memory
    ADD CONSTRAINT reader_ask_thread_memory_pkey PRIMARY KEY (thread_id);

ALTER TABLE ONLY reader_ask_threads
    ADD CONSTRAINT reader_ask_threads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_event_sequences
    ADD CONSTRAINT reader_event_sequences_pkey PRIMARY KEY (reading_record_id);

ALTER TABLE ONLY reader_events
    ADD CONSTRAINT reader_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_job_events
    ADD CONSTRAINT reader_job_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_jobs
    ADD CONSTRAINT reader_jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_notes
    ADD CONSTRAINT reader_notes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_runs
    ADD CONSTRAINT reader_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reader_runtime_spans
    ADD CONSTRAINT reader_runtime_spans_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reading_bases
    ADD CONSTRAINT reading_bases_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reading_records
    ADD CONSTRAINT reading_records_pkey PRIMARY KEY (id);

ALTER TABLE ONLY reading_units
    ADD CONSTRAINT reading_units_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_artifacts
    ADD CONSTRAINT source_artifacts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY stable_document_blocks
    ADD CONSTRAINT stable_document_blocks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY stable_image_source_overrides
    ADD CONSTRAINT stable_image_source_overrides_pkey PRIMARY KEY (id);

ALTER TABLE ONLY stable_reading_documents
    ADD CONSTRAINT stable_reading_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY anchor_segments
    ADD CONSTRAINT uq_anchor_segments_base_anchor UNIQUE (base_id, anchor_segment_id);

ALTER TABLE ONLY anchor_segments
    ADD CONSTRAINT uq_anchor_segments_base_sentence UNIQUE (base_id, sentence_id);

ALTER TABLE ONLY anchor_segments
    ADD CONSTRAINT uq_anchor_segments_unit_order UNIQUE (base_id, unit_id, unit_order_index);

ALTER TABLE ONLY candidate_reading_documents
    ADD CONSTRAINT uq_candidate_reading_documents_id_record UNIQUE (id, reading_record_id);

ALTER TABLE ONLY confirmed_source_documents
    ADD CONSTRAINT uq_confirmed_source_documents_id_record UNIQUE (id, reading_record_id);

ALTER TABLE ONLY confirmed_source_documents
    ADD CONSTRAINT uq_confirmed_source_documents_record_generation UNIQUE (reading_record_id, record_generation);

ALTER TABLE ONLY confirmed_source_revisions
    ADD CONSTRAINT uq_confirmed_source_revisions_document_revision UNIQUE (confirmed_source_document_id, revision);

DO $dict$
BEGIN
ALTER TABLE ONLY dict_lookup_targets
    ADD CONSTRAINT uq_dict_lookup_targets UNIQUE (source, normalized_form, entry_id, match_kind);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

DO $dict$
BEGIN
ALTER TABLE ONLY dict_redirects
    ADD CONSTRAINT uq_dict_redirects UNIQUE (source, redirect_key, target_entry_key, redirect_kind);
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

ALTER TABLE ONLY enhancement_layers
    ADD CONSTRAINT uq_enhancement_layers_source_job_fingerprint UNIQUE (source_job_id, operation_fingerprint);

ALTER TABLE ONLY favorite_records
    ADD CONSTRAINT uq_favorite_records_target UNIQUE (user_id, target_type, target_key);

ALTER TABLE ONLY parsed_decisions
    ADD CONSTRAINT uq_parsed_decisions_record_base_unit_policy UNIQUE (reading_record_id, base_id, unit_id, policy_code);

ALTER TABLE ONLY reader_events
    ADD CONSTRAINT uq_reader_events_record_sequence UNIQUE (reading_record_id, sequence);

ALTER TABLE ONLY reader_jobs
    ADD CONSTRAINT uq_reader_jobs_run_idempotency UNIQUE (run_id, idempotency_key);

ALTER TABLE ONLY reader_runs
    ADD CONSTRAINT uq_reader_runs_id_record UNIQUE (id, reading_record_id);

ALTER TABLE ONLY reading_bases
    ADD CONSTRAINT uq_reading_bases_id_record UNIQUE (id, reading_record_id);

ALTER TABLE ONLY reading_bases
    ADD CONSTRAINT uq_reading_bases_id_record_generation UNIQUE (id, reading_record_id, record_generation);

ALTER TABLE ONLY reading_bases
    ADD CONSTRAINT uq_reading_bases_record_base_version UNIQUE (reading_record_id, base_version);

ALTER TABLE ONLY reading_bases
    ADD CONSTRAINT uq_reading_bases_record_generation UNIQUE (reading_record_id, record_generation);

ALTER TABLE ONLY reading_units
    ADD CONSTRAINT uq_reading_units_base_order UNIQUE (base_id, order_index);

ALTER TABLE ONLY reading_units
    ADD CONSTRAINT uq_reading_units_base_unit_id UNIQUE (base_id, unit_id);

ALTER TABLE ONLY stable_document_blocks
    ADD CONSTRAINT uq_stable_document_blocks_doc_block UNIQUE (stable_document_id, block_id);

ALTER TABLE ONLY stable_document_blocks
    ADD CONSTRAINT uq_stable_document_blocks_doc_order UNIQUE (stable_document_id, order_index);

ALTER TABLE ONLY stable_reading_documents
    ADD CONSTRAINT uq_stable_reading_documents_id_record UNIQUE (id, reading_record_id);

ALTER TABLE ONLY stable_reading_documents
    ADD CONSTRAINT uq_stable_reading_documents_record_generation UNIQUE (reading_record_id, record_generation);

ALTER TABLE ONLY stable_reading_documents
    ADD CONSTRAINT uq_stable_reading_documents_record_version UNIQUE (reading_record_id, document_version);

ALTER TABLE ONLY user_annotations
    ADD CONSTRAINT uq_user_annotations_target UNIQUE (user_id, target_key);

ALTER TABLE ONLY user_identities
    ADD CONSTRAINT uq_user_identities_provider_user UNIQUE (provider, provider_user_id);

ALTER TABLE ONLY user_annotations
    ADD CONSTRAINT user_annotations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY user_credit_accounts
    ADD CONSTRAINT user_credit_accounts_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY user_credit_ledger
    ADD CONSTRAINT user_credit_ledger_pkey PRIMARY KEY (id);

ALTER TABLE ONLY user_identities
    ADD CONSTRAINT user_identities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY user_password_credentials
    ADD CONSTRAINT user_password_credentials_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY user_sessions
    ADD CONSTRAINT user_sessions_refresh_token_hash_key UNIQUE (refresh_token_hash);

ALTER TABLE ONLY user_sessions
    ADD CONSTRAINT user_sessions_session_token_hash_key UNIQUE (session_token_hash);

ALTER TABLE ONLY users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY vocabulary_book
    ADD CONSTRAINT vocabulary_book_pkey PRIMARY KEY (id);

CREATE INDEX idx_ai_usage_events_capability_created ON ai_usage_events USING btree (capability_code, created_at DESC);

CREATE INDEX idx_ai_usage_events_daily_reader ON ai_usage_events USING btree (daily_reader_article_id, created_at DESC) WHERE (daily_reader_article_id IS NOT NULL);

CREATE INDEX idx_ai_usage_events_enhancement_layer ON ai_usage_events USING btree (enhancement_layer_id, created_at DESC) WHERE (enhancement_layer_id IS NOT NULL);

CREATE INDEX idx_ai_usage_events_operation_fingerprint ON ai_usage_events USING btree (operation_fingerprint, created_at DESC) WHERE (operation_fingerprint IS NOT NULL);

CREATE INDEX idx_ai_usage_events_reader_job ON ai_usage_events USING btree (reader_job_id, created_at DESC) WHERE (reader_job_id IS NOT NULL);

CREATE INDEX idx_ai_usage_events_reader_run ON ai_usage_events USING btree (reader_run_id, created_at DESC) WHERE (reader_run_id IS NOT NULL);

CREATE INDEX idx_ai_usage_events_reading_record ON ai_usage_events USING btree (reading_record_id, created_at DESC) WHERE (reading_record_id IS NOT NULL);

CREATE INDEX idx_ai_usage_events_scope_created ON ai_usage_events USING btree (usage_scope, created_at DESC);

CREATE INDEX idx_ai_usage_events_user_created ON ai_usage_events USING btree (user_id, created_at DESC) WHERE (user_id IS NOT NULL);

CREATE INDEX idx_analysis_windows_job ON analysis_windows USING btree (job_id) WHERE (job_id IS NOT NULL);

CREATE INDEX idx_analysis_windows_plan_status ON analysis_windows USING btree (plan_id, status);

CREATE INDEX idx_anchor_segments_record_base_order ON anchor_segments USING btree (reading_record_id, base_id, order_index);

CREATE INDEX idx_candidate_reading_documents_record_generation ON candidate_reading_documents USING btree (reading_record_id, record_generation);

CREATE INDEX idx_candidate_reading_documents_user_updated ON candidate_reading_documents USING btree (user_id, updated_at DESC) WHERE (status <> 'superseded'::text);

CREATE INDEX idx_confirmed_source_documents_user_updated ON confirmed_source_documents USING btree (user_id, updated_at DESC);

CREATE INDEX idx_confirmed_source_revisions_record_revision ON confirmed_source_revisions USING btree (reading_record_id, record_generation, revision);

CREATE INDEX idx_credit_ledger_reader_job ON user_credit_ledger USING btree (reader_job_id, created_at DESC) WHERE (reader_job_id IS NOT NULL);

CREATE INDEX idx_credit_ledger_reader_run ON user_credit_ledger USING btree (reader_run_id, created_at DESC) WHERE (reader_run_id IS NOT NULL);

CREATE INDEX idx_credit_ledger_reading_record ON user_credit_ledger USING btree (reading_record_id, created_at DESC) WHERE (reading_record_id IS NOT NULL);

CREATE INDEX idx_credit_ledger_subject ON user_credit_ledger USING btree (subject_type, subject_id, created_at DESC) WHERE ((subject_type IS NOT NULL) AND (subject_id IS NOT NULL));

CREATE INDEX idx_credit_ledger_user_created ON user_credit_ledger USING btree (user_id, created_at DESC);

CREATE INDEX idx_daily_readers_original_text_hash ON daily_readers USING btree (original_text_hash) WHERE (original_text_hash IS NOT NULL);

CREATE INDEX idx_daily_readers_published ON daily_readers USING btree (publish_date DESC) WHERE (status = 'published'::text);

CREATE INDEX idx_daily_readers_status_date ON daily_readers USING btree (status, publish_date DESC);

CREATE INDEX idx_dict_ai_candidates_query ON dict_ai_candidate_entries USING btree (query);

CREATE INDEX idx_dict_ai_candidates_reading_record ON dict_ai_candidate_entries USING btree (reading_record_id, created_at DESC) WHERE (reading_record_id IS NOT NULL);

CREATE INDEX idx_dict_ai_candidates_review_status_created ON dict_ai_candidate_entries USING btree (review_status, created_at DESC);

CREATE INDEX idx_dict_ai_candidates_usage_event ON dict_ai_candidate_entries USING btree (usage_event_id) WHERE (usage_event_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_dict_entries_base_headword_lower ON dict_entries USING btree (lower(base_headword));

CREATE INDEX IF NOT EXISTS idx_dict_entries_display_headword_lower ON dict_entries USING btree (lower(display_headword));

CREATE UNIQUE INDEX IF NOT EXISTS idx_dict_entries_source_entry_key ON dict_entries USING btree (source, source_entry_key);

CREATE INDEX IF NOT EXISTS idx_dict_lookup_targets_entry_id ON dict_lookup_targets USING btree (entry_id);

CREATE INDEX IF NOT EXISTS idx_dict_lookup_targets_form_rank ON dict_lookup_targets USING btree (source, normalized_form, rank);

CREATE INDEX IF NOT EXISTS idx_dict_redirects_key ON dict_redirects USING btree (source, redirect_key);

CREATE INDEX IF NOT EXISTS idx_dict_redirects_target ON dict_redirects USING btree (source, target_entry_key);

CREATE INDEX IF NOT EXISTS idx_eval_example_lab_entries_type_created ON eval_example_lab_entries USING btree (example_type, date_created DESC);

CREATE INDEX IF NOT EXISTS idx_eval_example_lab_entries_variant ON eval_example_lab_entries USING btree (reading_variant, example_type);

CREATE INDEX idx_favorite_records_user_created_at ON favorite_records USING btree (user_id, created_at DESC) WHERE (deleted_at IS NULL);

CREATE INDEX idx_favorite_records_user_target_updated ON favorite_records USING btree (user_id, target_type, updated_at DESC) WHERE (deleted_at IS NULL);

CREATE INDEX idx_feedback_client_platform_created ON feedback USING btree (client_platform, created_at DESC);

CREATE INDEX idx_feedback_client_surface_created ON feedback USING btree (client_surface, created_at DESC) WHERE (client_surface IS NOT NULL);

CREATE INDEX idx_feedback_context ON feedback USING gin (context_json);

CREATE INDEX idx_feedback_rag_harvested ON feedback USING btree (rag_harvested) WHERE ((rag_harvested = false) AND (feedback_scope = 'dictionary'::text));

CREATE INDEX idx_feedback_scope_type ON feedback USING btree (feedback_scope, feedback_type);

CREATE INDEX idx_feedback_sentiment ON feedback USING btree (sentiment, feedback_scope);

CREATE INDEX idx_feedback_status ON feedback USING btree (status) WHERE (status = 'pending'::text);

CREATE INDEX idx_feedback_user_created ON feedback USING btree (user_id, created_at DESC);

CREATE UNIQUE INDEX idx_llm_ask_config_singleton ON llm_ask_config USING btree ((1));

CREATE INDEX idx_llm_ask_options_enabled ON llm_ask_options USING btree (enabled, sort);

CREATE INDEX idx_llm_models_provider ON llm_models USING btree (provider, status, sort);

CREATE INDEX idx_llm_models_slug ON llm_models USING btree (slug);

CREATE INDEX idx_llm_presets_slug ON llm_presets USING btree (slug);

CREATE INDEX idx_llm_profiles_model ON llm_profiles USING btree (model, status, sort);

CREATE INDEX idx_llm_profiles_slug ON llm_profiles USING btree (slug);

CREATE INDEX idx_llm_providers_status ON llm_providers USING btree (status, sort);

CREATE INDEX idx_original_inputs_record_created ON original_inputs USING btree (reading_record_id, created_at DESC);

CREATE INDEX idx_parsed_decisions_record_state ON parsed_decisions USING btree (reading_record_id, parsed_state);

CREATE INDEX idx_parsed_decisions_source_layer ON parsed_decisions USING btree (source_layer_id) WHERE (source_layer_id IS NOT NULL);

CREATE INDEX idx_pipeline_runs_created ON pipeline_runs USING btree (created_at DESC);

CREATE INDEX idx_pipeline_runs_status ON pipeline_runs USING btree (status);

CREATE INDEX idx_reader_article_rag_index_runs_record ON reader_article_rag_index_runs USING btree (reading_record_id, status) WHERE (status = ANY (ARRAY['planned'::text, 'queued'::text, 'indexing'::text, 'indexed'::text]));

CREATE INDEX idx_reader_ask_client_submissions_assistant ON reader_ask_client_submissions USING btree (assistant_message_id) WHERE (assistant_message_id IS NOT NULL);

CREATE INDEX idx_reader_ask_client_submissions_orphan_claim ON reader_ask_client_submissions USING btree (status, lease_expires_at) WHERE ((status = 'claimed'::text) AND (user_message_id IS NULL) AND (assistant_message_id IS NULL));

CREATE INDEX idx_reader_ask_messages_current_turn_run ON reader_ask_messages USING btree (current_turn_run_id) WHERE (current_turn_run_id IS NOT NULL);

CREATE INDEX idx_reader_ask_messages_thread_created ON reader_ask_messages USING btree (thread_id, created_at);

CREATE INDEX idx_reader_ask_messages_usage_event ON reader_ask_messages USING btree (usage_event_id) WHERE (usage_event_id IS NOT NULL);

CREATE INDEX idx_reader_ask_supplements_target_key ON reader_ask_supplements USING btree (user_id, target_key) WHERE (deleted_at IS NULL);

CREATE INDEX idx_reader_ask_supplements_user_reading_record ON reader_ask_supplements USING btree (user_id, reading_record_id, created_at) WHERE ((deleted_at IS NULL) AND (reading_record_id IS NOT NULL));

CREATE INDEX idx_reader_ask_thread_memory_updated_on ON reader_ask_thread_memory USING btree (updated_at);

CREATE INDEX idx_reader_ask_threads_user_reading_record_last_message ON reader_ask_threads USING btree (user_id, reading_record_id, last_message_at DESC NULLS LAST) WHERE ((archived_at IS NULL) AND (reading_record_id IS NOT NULL));

CREATE INDEX idx_reader_ask_threads_user_reading_record_updated ON reader_ask_threads USING btree (user_id, reading_record_id, updated_at DESC) WHERE ((archived_at IS NULL) AND (reading_record_id IS NOT NULL));

CREATE INDEX idx_reader_ask_turn_runs_envelope_fingerprint ON reader_ask_turn_runs USING btree (envelope_fingerprint) WHERE (envelope_fingerprint IS NOT NULL);

CREATE INDEX idx_reader_ask_turn_runs_execution_version ON reader_ask_turn_runs USING btree (execution_version) WHERE (execution_version IS NOT NULL);

CREATE INDEX idx_reader_ask_turn_runs_message_created ON reader_ask_turn_runs USING btree (message_id, created_at DESC);

CREATE INDEX idx_reader_ask_turn_runs_reading_record_started ON reader_ask_turn_runs USING btree (reading_record_id, started_at DESC) WHERE (reading_record_id IS NOT NULL);

CREATE INDEX idx_reader_ask_turn_runs_thread_started ON reader_ask_turn_runs USING btree (thread_id, started_at DESC);

CREATE INDEX idx_reader_ask_turn_runs_usage_event ON reader_ask_turn_runs USING btree (usage_event_id) WHERE (usage_event_id IS NOT NULL);

CREATE INDEX idx_reader_events_record_created ON reader_events USING btree (reading_record_id, created_at DESC);

CREATE INDEX idx_reader_events_record_sequence ON reader_events USING btree (reading_record_id, sequence);

CREATE INDEX idx_reader_events_source_job ON reader_events USING btree (source_job_id) WHERE (source_job_id IS NOT NULL);

CREATE INDEX idx_reader_events_gc_intent_scan ON reader_events USING btree (created_at, id) WHERE ((event_type = 'record_state_changed'::text) AND ((payload_json ->> 'event_schema'::text) = 'reading_record_deleted_v1'::text) AND ((payload_json ->> 'article_rag_vector_gc_requested'::text) = 'true'::text));

CREATE INDEX idx_reader_events_gc_outcome_intent ON reader_events USING btree (((payload_json ->> 'intent_event_id'::text)), created_at) WHERE ((event_type = 'record_state_changed'::text) AND ((payload_json ->> 'event_schema'::text) = ANY (ARRAY['article_rag_vector_gc_completed_v1'::text, 'article_rag_vector_gc_retry_scheduled_v1'::text, 'article_rag_vector_gc_failed_terminal_v1'::text])));

CREATE INDEX idx_reader_job_events_job_created ON reader_job_events USING btree (job_id, created_at DESC);

CREATE INDEX idx_reader_job_events_record_created ON reader_job_events USING btree (reading_record_id, created_at DESC);

CREATE INDEX idx_reader_jobs_claim_queue ON reader_jobs USING btree (priority DESC, available_at, created_at, id) WHERE (status = ANY (ARRAY['queued'::text, 'retry_later'::text]));

CREATE INDEX idx_reader_jobs_claimed_lease ON reader_jobs USING btree (lease_expires_at) WHERE (status = 'claimed'::text);

CREATE INDEX idx_reader_notes_reading_record ON reader_notes USING btree (user_id, reading_record_id, base_id, generation) WHERE ((reading_record_id IS NOT NULL) AND (deleted_at IS NULL));

CREATE INDEX idx_reader_runs_record_created ON reader_runs USING btree (reading_record_id, created_at DESC);

CREATE INDEX idx_reader_runs_status_created ON reader_runs USING btree (status, created_at);

CREATE INDEX idx_reading_records_title_generation_status ON reading_records USING btree (title_generation_status, updated_at DESC) WHERE ((deleted_at IS NULL) AND (title_generation_status = ANY (ARRAY['pending'::text, 'failed_retryable'::text])));

CREATE INDEX idx_reading_records_user_goal_updated_at ON reading_records USING btree (user_id, reading_goal, updated_at DESC) WHERE (deleted_at IS NULL);

CREATE INDEX idx_reading_records_user_last_opened_at ON reading_records USING btree (user_id, last_opened_at DESC NULLS LAST, created_at DESC, id DESC) WHERE (deleted_at IS NULL);

CREATE INDEX idx_reading_records_user_recent_visible ON reading_records USING btree (user_id, last_opened_at DESC, created_at DESC, id DESC) WHERE (deleted_at IS NULL AND recent_hidden_at IS NULL AND last_opened_at IS NOT NULL);

CREATE INDEX idx_reading_records_user_product_state_updated_at ON reading_records USING btree (user_id, product_state, updated_at DESC);

CREATE INDEX idx_reading_records_user_updated_at ON reading_records USING btree (user_id, updated_at DESC);

CREATE INDEX idx_reading_units_record_base_order ON reading_units USING btree (reading_record_id, base_id, order_index);

CREATE INDEX idx_source_artifacts_record_created ON source_artifacts USING btree (reading_record_id, created_at DESC) WHERE (reading_record_id IS NOT NULL);

CREATE INDEX idx_source_artifacts_user_created ON source_artifacts USING btree (user_id, created_at DESC);

CREATE INDEX idx_stable_document_blocks_doc_order ON stable_document_blocks USING btree (stable_document_id, order_index);

CREATE INDEX idx_stable_document_blocks_parent ON stable_document_blocks USING btree (stable_document_id, parent_block_id) WHERE (parent_block_id IS NOT NULL);

CREATE INDEX idx_stable_document_blocks_type ON stable_document_blocks USING btree (stable_document_id, block_type);

CREATE INDEX idx_stable_reading_documents_record_status ON stable_reading_documents USING btree (reading_record_id, status);

CREATE INDEX idx_user_annotations_reading_record ON user_annotations USING btree (user_id, reading_record_id, base_id, generation) WHERE ((reading_record_id IS NOT NULL) AND (deleted_at IS NULL));

CREATE INDEX idx_user_annotations_user_anchor_updated ON user_annotations USING btree (user_id, anchor_type, updated_at DESC) WHERE (deleted_at IS NULL);

CREATE INDEX idx_user_identities_unionid ON user_identities USING btree (unionid) WHERE (unionid IS NOT NULL);

CREATE INDEX idx_user_identities_user_id ON user_identities USING btree (user_id);

CREATE INDEX idx_user_sessions_expires_at ON user_sessions USING btree (expires_at);

CREATE INDEX idx_user_sessions_user_id_status ON user_sessions USING btree (user_id, status);

CREATE INDEX idx_vocabulary_book_dict_entry_id ON vocabulary_book USING btree (dict_entry_id) WHERE (dict_entry_id IS NOT NULL);

CREATE INDEX idx_vocabulary_book_user_created_at ON vocabulary_book USING btree (user_id, created_at DESC);

CREATE INDEX idx_vocabulary_book_user_mastery_status ON vocabulary_book USING btree (user_id, mastery_status);

CREATE INDEX ix_reader_runtime_spans_job_id ON reader_runtime_spans USING btree (reader_job_id);

CREATE INDEX ix_reader_runtime_spans_record_id ON reader_runtime_spans USING btree (reading_record_id);

CREATE INDEX ix_reader_runtime_spans_run_id ON reader_runtime_spans USING btree (reader_run_id);

CREATE INDEX ix_reader_runtime_spans_started_at ON reader_runtime_spans USING btree (started_at DESC);

CREATE INDEX ix_reader_runtime_spans_status ON reader_runtime_spans USING btree (status);

CREATE INDEX ix_reader_runtime_spans_trace_id ON reader_runtime_spans USING btree (trace_id);

CREATE INDEX ix_reader_runtime_spans_worker_type ON reader_runtime_spans USING btree (worker_type);

CREATE UNIQUE INDEX uq_ai_usage_events_invocation_key ON ai_usage_events USING btree (invocation_key) WHERE (invocation_key IS NOT NULL);

CREATE UNIQUE INDEX uq_ai_model_execution_journal_invocation_key ON ai_model_execution_journal USING btree (invocation_key);

CREATE INDEX idx_ai_model_execution_journal_pending_delivery ON ai_model_execution_journal USING btree (delivery_next_attempt_at, created_at, id) WHERE ((capture_state = 'captured'::text) AND (usage_delivery_state = 'pending'::text));

CREATE INDEX idx_ai_model_execution_journal_reader_attempt ON ai_model_execution_journal USING btree (reader_job_id, attempt_ordinal, execution_slot) WHERE (reader_job_id IS NOT NULL);

CREATE UNIQUE INDEX uq_enhancement_layers_active_published ON enhancement_layers USING btree (reading_record_id, base_id, layer_type, target_scope, target_key) WHERE (status = 'published'::text);

CREATE UNIQUE INDEX uq_layer_analysis_plans_active ON layer_analysis_plans USING btree (reading_record_id, base_id, layer_type) WHERE (status = ANY (ARRAY['planning'::text, 'active'::text]));

CREATE UNIQUE INDEX uq_reader_article_rag_index_runs_active ON reader_article_rag_index_runs USING btree (stable_document_id) WHERE (status = ANY (ARRAY['planned'::text, 'queued'::text, 'indexing'::text, 'indexed'::text]));

CREATE UNIQUE INDEX uq_reader_ask_default_thread_reading_record ON reader_ask_threads USING btree (user_id, reading_record_id) WHERE ((is_default = true) AND (archived_at IS NULL) AND (reading_record_id IS NOT NULL));

CREATE UNIQUE INDEX uq_reader_jobs_active_fingerprint ON reader_jobs USING btree (reading_record_id, COALESCE(base_id, '00000000-0000-0000-0000-000000000000'::uuid), job_type, target_type, target_key, expected_generation, operation_fingerprint) WHERE (status = ANY (ARRAY['queued'::text, 'claimed'::text, 'retry_later'::text, 'paused'::text]));

CREATE UNIQUE INDEX uq_reader_notes_reading_record_anchor ON reader_notes USING btree (user_id, reading_record_id, base_id, anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash) WHERE ((reading_record_id IS NOT NULL) AND (deleted_at IS NULL));

CREATE UNIQUE INDEX uq_reading_bases_active_record ON reading_bases USING btree (reading_record_id) WHERE (status = 'active'::text);

CREATE UNIQUE INDEX uq_reading_records_user_client_active ON reading_records USING btree (user_id, client_record_id) WHERE ((client_record_id IS NOT NULL) AND (deleted_at IS NULL));

CREATE UNIQUE INDEX uq_source_artifacts_active_object ON source_artifacts USING btree (storage_provider, COALESCE(bucket, ''::text), object_key) WHERE (deleted_at IS NULL);

CREATE UNIQUE INDEX uq_stable_reading_documents_active_per_record ON stable_reading_documents USING btree (reading_record_id) WHERE (status = 'active'::text);

CREATE UNIQUE INDEX uq_stable_image_override_inline ON stable_image_source_overrides USING btree (stable_document_id, block_id, inline_ordinal) WHERE (inline_ordinal IS NOT NULL);

CREATE UNIQUE INDEX uq_stable_image_override_standalone ON stable_image_source_overrides USING btree (stable_document_id, block_id) WHERE (inline_ordinal IS NULL);

CREATE UNIQUE INDEX uq_user_annotations_reading_record_anchor ON user_annotations USING btree (user_id, reading_record_id, base_id, anchor_segment_id, unit_start_utf16, unit_end_utf16, text_hash) WHERE ((reading_record_id IS NOT NULL) AND (deleted_at IS NULL));

CREATE UNIQUE INDEX uq_vocabulary_book_user_lemma_lower ON vocabulary_book USING btree (user_id, lower(lemma));

CREATE TRIGGER trg_anonymous_quotas_set_updated_at BEFORE UPDATE ON anonymous_quotas FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_daily_readers_set_updated_at BEFORE UPDATE ON daily_readers FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_dict_ai_candidate_entries_set_updated_at BEFORE UPDATE ON dict_ai_candidate_entries FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_dict_entries_set_updated_at ON dict_entries;
CREATE TRIGGER trg_dict_entries_set_updated_at BEFORE UPDATE ON dict_entries FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_enhancement_layers_set_updated_at BEFORE UPDATE ON enhancement_layers FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_favorite_records_set_updated_at BEFORE UPDATE ON favorite_records FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_feedback_set_updated_at BEFORE UPDATE ON feedback FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reader_ask_messages_set_updated_at BEFORE UPDATE ON reader_ask_messages FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reader_ask_threads_set_updated_at BEFORE UPDATE ON reader_ask_threads FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reader_ask_turn_runs_set_updated_at BEFORE UPDATE ON reader_ask_turn_runs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reader_jobs_set_updated_at BEFORE UPDATE ON reader_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reader_notes_set_updated_at BEFORE UPDATE ON reader_notes FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reader_runs_set_updated_at BEFORE UPDATE ON reader_runs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_reading_records_initialize_event_sequence AFTER INSERT ON reading_records FOR EACH ROW EXECUTE FUNCTION initialize_reader_event_sequence();

CREATE TRIGGER trg_reading_records_set_updated_at BEFORE UPDATE ON reading_records FOR EACH ROW EXECUTE FUNCTION reading_records_touch_updated_at();

CREATE TRIGGER trg_user_annotations_set_updated_at BEFORE UPDATE ON user_annotations FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_credit_accounts_set_updated_at BEFORE UPDATE ON user_credit_accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_identities_set_updated_at BEFORE UPDATE ON user_identities FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_password_credentials_set_updated_at BEFORE UPDATE ON user_password_credentials FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_user_sessions_set_updated_at BEFORE UPDATE ON user_sessions FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_users_set_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_vocabulary_book_set_updated_at BEFORE UPDATE ON vocabulary_book FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE ONLY ai_usage_events
    ADD CONSTRAINT ai_usage_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE ONLY ai_model_execution_journal
    ADD CONSTRAINT ai_model_execution_journal_ai_usage_event_id_fkey FOREIGN KEY (ai_usage_event_id) REFERENCES ai_usage_events(id);

ALTER TABLE ONLY ai_model_execution_journal
    ADD CONSTRAINT ai_model_execution_journal_reader_job_id_fkey FOREIGN KEY (reader_job_id) REFERENCES reader_jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY ai_model_execution_journal
    ADD CONSTRAINT ai_model_execution_journal_reader_run_id_fkey FOREIGN KEY (reader_run_id) REFERENCES reader_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY analysis_windows
    ADD CONSTRAINT analysis_windows_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES layer_analysis_plans(id) ON DELETE CASCADE;

ALTER TABLE ONLY candidate_reading_documents
    ADD CONSTRAINT candidate_reading_documents_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY candidate_reading_documents
    ADD CONSTRAINT candidate_reading_documents_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY confirmed_source_documents
    ADD CONSTRAINT confirmed_source_documents_original_input_id_fkey FOREIGN KEY (original_input_id) REFERENCES original_inputs(id) ON DELETE SET NULL;

ALTER TABLE ONLY confirmed_source_documents
    ADD CONSTRAINT confirmed_source_documents_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY confirmed_source_documents
    ADD CONSTRAINT confirmed_source_documents_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY confirmed_source_revisions
    ADD CONSTRAINT confirmed_source_revisions_confirmed_source_document_id_fkey FOREIGN KEY (confirmed_source_document_id) REFERENCES confirmed_source_documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY confirmed_source_revisions
    ADD CONSTRAINT confirmed_source_revisions_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY confirmed_source_revisions
    ADD CONSTRAINT confirmed_source_revisions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY dict_ai_candidate_entries
    ADD CONSTRAINT dict_ai_candidate_entries_base_id_fkey FOREIGN KEY (base_id) REFERENCES reading_bases(id) ON DELETE SET NULL;

ALTER TABLE ONLY dict_ai_candidate_entries
    ADD CONSTRAINT dict_ai_candidate_entries_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE SET NULL;

ALTER TABLE ONLY dict_ai_candidate_entries
    ADD CONSTRAINT dict_ai_candidate_entries_usage_event_id_fkey FOREIGN KEY (usage_event_id) REFERENCES ai_usage_events(id) ON DELETE SET NULL;

DO $dict$
BEGIN
ALTER TABLE ONLY dict_lookup_targets
    ADD CONSTRAINT dict_lookup_targets_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES dict_entries(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object OR duplicate_table OR invalid_table_definition OR unique_violation THEN NULL;
END
$dict$;

ALTER TABLE ONLY enhancement_layers
    ADD CONSTRAINT enhancement_layers_source_job_id_fkey FOREIGN KEY (source_job_id) REFERENCES reader_jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY enhancement_layers
    ADD CONSTRAINT enhancement_layers_source_run_id_fkey FOREIGN KEY (source_run_id) REFERENCES reader_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY favorite_records
    ADD CONSTRAINT favorite_records_deleted_by_fkey FOREIGN KEY (deleted_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE ONLY favorite_records
    ADD CONSTRAINT favorite_records_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY feedback
    ADD CONSTRAINT feedback_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE ONLY feedback
    ADD CONSTRAINT feedback_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY ai_usage_events
    ADD CONSTRAINT fk_ai_usage_events_daily_reader FOREIGN KEY (daily_reader_article_id) REFERENCES daily_readers(id) ON DELETE SET NULL;

ALTER TABLE ONLY ai_usage_events
    ADD CONSTRAINT fk_ai_usage_events_enhancement_layer FOREIGN KEY (enhancement_layer_id) REFERENCES enhancement_layers(id) ON DELETE SET NULL;

ALTER TABLE ONLY ai_usage_events
    ADD CONSTRAINT fk_ai_usage_events_reader_job FOREIGN KEY (reader_job_id) REFERENCES reader_jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY ai_usage_events
    ADD CONSTRAINT fk_ai_usage_events_reader_run FOREIGN KEY (reader_run_id) REFERENCES reader_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY ai_usage_events
    ADD CONSTRAINT fk_ai_usage_events_reading_record FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE SET NULL;

ALTER TABLE ONLY anchor_segments
    ADD CONSTRAINT fk_anchor_segments_base_record FOREIGN KEY (base_id, reading_record_id) REFERENCES reading_bases(id, reading_record_id) ON DELETE CASCADE;

ALTER TABLE ONLY anchor_segments
    ADD CONSTRAINT fk_anchor_segments_unit FOREIGN KEY (base_id, unit_id) REFERENCES reading_units(base_id, unit_id) ON DELETE CASCADE;

ALTER TABLE ONLY enhancement_layers
    ADD CONSTRAINT fk_enhancement_layers_base_record FOREIGN KEY (base_id, reading_record_id, generation) REFERENCES reading_bases(id, reading_record_id, record_generation) ON DELETE CASCADE;

ALTER TABLE ONLY parsed_decisions
    ADD CONSTRAINT fk_parsed_decisions_base_record FOREIGN KEY (base_id, reading_record_id) REFERENCES reading_bases(id, reading_record_id) ON DELETE CASCADE;

ALTER TABLE ONLY parsed_decisions
    ADD CONSTRAINT fk_parsed_decisions_unit FOREIGN KEY (base_id, unit_id) REFERENCES reading_units(base_id, unit_id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_messages
    ADD CONSTRAINT fk_reader_ask_messages_current_turn_run FOREIGN KEY (current_turn_run_id) REFERENCES reader_ask_turn_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_jobs
    ADD CONSTRAINT fk_reader_jobs_base_record FOREIGN KEY (base_id, reading_record_id, expected_generation) REFERENCES reading_bases(id, reading_record_id, record_generation) ON DELETE CASCADE;

ALTER TABLE ONLY reader_jobs
    ADD CONSTRAINT fk_reader_jobs_run_record FOREIGN KEY (run_id, reading_record_id) REFERENCES reader_runs(id, reading_record_id) ON DELETE CASCADE;

ALTER TABLE ONLY reading_records
    ADD CONSTRAINT fk_reading_records_active_base FOREIGN KEY (active_base_id, id, generation) REFERENCES reading_bases(id, reading_record_id, record_generation) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY reading_units
    ADD CONSTRAINT fk_reading_units_base_record FOREIGN KEY (base_id, reading_record_id) REFERENCES reading_bases(id, reading_record_id) ON DELETE CASCADE;

ALTER TABLE ONLY stable_document_blocks
    ADD CONSTRAINT fk_stable_document_blocks_parent FOREIGN KEY (stable_document_id, parent_block_id) REFERENCES stable_document_blocks(stable_document_id, block_id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY user_credit_ledger
    ADD CONSTRAINT fk_user_credit_ledger_reader_job FOREIGN KEY (reader_job_id) REFERENCES reader_jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY user_credit_ledger
    ADD CONSTRAINT fk_user_credit_ledger_reader_run FOREIGN KEY (reader_run_id) REFERENCES reader_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY user_credit_ledger
    ADD CONSTRAINT fk_user_credit_ledger_reading_record FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE SET NULL;

ALTER TABLE ONLY vocabulary_book
    ADD CONSTRAINT fk_vocabulary_book_dict_entry FOREIGN KEY (dict_entry_id) REFERENCES dict_entries(id) ON DELETE SET NULL;

ALTER TABLE ONLY layer_analysis_plans
    ADD CONSTRAINT layer_analysis_plans_base_id_fkey FOREIGN KEY (base_id) REFERENCES reading_bases(id) ON DELETE CASCADE;

ALTER TABLE ONLY layer_analysis_plans
    ADD CONSTRAINT layer_analysis_plans_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY llm_models
    ADD CONSTRAINT llm_models_provider_fkey FOREIGN KEY (provider) REFERENCES llm_providers(id) ON DELETE RESTRICT;

ALTER TABLE ONLY llm_presets
    ADD CONSTRAINT llm_presets_base_preset_fkey FOREIGN KEY (base_preset) REFERENCES llm_presets(id) ON DELETE SET NULL;

ALTER TABLE ONLY llm_presets
    ADD CONSTRAINT llm_presets_default_profile_fkey FOREIGN KEY (default_profile) REFERENCES llm_profiles(id) ON DELETE SET NULL;

ALTER TABLE ONLY llm_profiles
    ADD CONSTRAINT llm_profiles_model_fkey FOREIGN KEY (model) REFERENCES llm_models(id) ON DELETE RESTRICT;

ALTER TABLE ONLY original_inputs
    ADD CONSTRAINT original_inputs_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY original_inputs
    ADD CONSTRAINT original_inputs_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY parsed_decisions
    ADD CONSTRAINT parsed_decisions_source_job_id_fkey FOREIGN KEY (source_job_id) REFERENCES reader_jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY parsed_decisions
    ADD CONSTRAINT parsed_decisions_source_layer_id_fkey FOREIGN KEY (source_layer_id) REFERENCES enhancement_layers(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_article_rag_index_runs
    ADD CONSTRAINT reader_article_rag_index_runs_base_id_fkey FOREIGN KEY (base_id) REFERENCES reading_bases(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_article_rag_index_runs
    ADD CONSTRAINT reader_article_rag_index_runs_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_article_rag_index_runs
    ADD CONSTRAINT reader_article_rag_index_runs_stable_document_id_fkey FOREIGN KEY (stable_document_id) REFERENCES stable_reading_documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_client_submissions
    ADD CONSTRAINT reader_ask_client_submissions_assistant_message_id_fkey FOREIGN KEY (assistant_message_id) REFERENCES reader_ask_messages(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_ask_client_submissions
    ADD CONSTRAINT reader_ask_client_submissions_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES reader_ask_threads(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_client_submissions
    ADD CONSTRAINT reader_ask_client_submissions_user_message_id_fkey FOREIGN KEY (user_message_id) REFERENCES reader_ask_messages(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_ask_messages
    ADD CONSTRAINT reader_ask_messages_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES reader_ask_threads(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_messages
    ADD CONSTRAINT reader_ask_messages_usage_event_id_fkey FOREIGN KEY (usage_event_id) REFERENCES ai_usage_events(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_ask_supplements
    ADD CONSTRAINT reader_ask_supplements_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_supplements
    ADD CONSTRAINT reader_ask_supplements_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_thread_memory
    ADD CONSTRAINT reader_ask_thread_memory_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES reader_ask_threads(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_threads
    ADD CONSTRAINT reader_ask_threads_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_threads
    ADD CONSTRAINT reader_ask_threads_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_message_id_fkey FOREIGN KEY (message_id) REFERENCES reader_ask_messages(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_supersedes_run_id_fkey FOREIGN KEY (supersedes_run_id) REFERENCES reader_ask_turn_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES reader_ask_threads(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_turn_id_fkey FOREIGN KEY (turn_id) REFERENCES reader_ask_messages(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_usage_event_id_fkey FOREIGN KEY (usage_event_id) REFERENCES ai_usage_events(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_ask_turn_runs
    ADD CONSTRAINT reader_ask_turn_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_event_sequences
    ADD CONSTRAINT reader_event_sequences_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_events
    ADD CONSTRAINT reader_events_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_events
    ADD CONSTRAINT reader_events_source_job_id_fkey FOREIGN KEY (source_job_id) REFERENCES reader_jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_events
    ADD CONSTRAINT reader_events_source_layer_id_fkey FOREIGN KEY (source_layer_id) REFERENCES enhancement_layers(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_events
    ADD CONSTRAINT reader_events_source_run_id_fkey FOREIGN KEY (source_run_id) REFERENCES reader_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_job_events
    ADD CONSTRAINT reader_job_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES reader_jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_job_events
    ADD CONSTRAINT reader_job_events_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_job_events
    ADD CONSTRAINT reader_job_events_run_id_fkey FOREIGN KEY (run_id) REFERENCES reader_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_jobs
    ADD CONSTRAINT reader_jobs_base_id_fkey FOREIGN KEY (base_id) REFERENCES reading_bases(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_jobs
    ADD CONSTRAINT reader_jobs_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_jobs
    ADD CONSTRAINT reader_jobs_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_notes
    ADD CONSTRAINT reader_notes_deleted_by_fkey FOREIGN KEY (deleted_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE ONLY reader_notes
    ADD CONSTRAINT reader_notes_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_runs
    ADD CONSTRAINT reader_runs_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_runs
    ADD CONSTRAINT reader_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY reader_runtime_spans
    ADD CONSTRAINT reader_runtime_spans_parent_span_id_fkey FOREIGN KEY (parent_span_id) REFERENCES reader_runtime_spans(id) ON DELETE SET NULL;

ALTER TABLE ONLY reading_bases
    ADD CONSTRAINT reading_bases_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY reading_records
    ADD CONSTRAINT reading_records_superseded_by_record_id_fkey FOREIGN KEY (superseded_by_record_id) REFERENCES reading_records(id) ON DELETE SET NULL;

ALTER TABLE ONLY reading_records
    ADD CONSTRAINT reading_records_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY source_artifacts
    ADD CONSTRAINT source_artifacts_original_input_id_fkey FOREIGN KEY (original_input_id) REFERENCES original_inputs(id) ON DELETE SET NULL;

ALTER TABLE ONLY source_artifacts
    ADD CONSTRAINT source_artifacts_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE SET NULL;

ALTER TABLE ONLY source_artifacts
    ADD CONSTRAINT source_artifacts_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY stable_document_blocks
    ADD CONSTRAINT stable_document_blocks_stable_document_id_fkey FOREIGN KEY (stable_document_id) REFERENCES stable_reading_documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY stable_image_source_overrides
    ADD CONSTRAINT fk_stable_image_source_overrides_block FOREIGN KEY (stable_document_id, block_id) REFERENCES stable_document_blocks(stable_document_id, block_id) ON DELETE CASCADE;

ALTER TABLE ONLY stable_reading_documents
    ADD CONSTRAINT stable_reading_documents_reading_record_id_fkey FOREIGN KEY (reading_record_id) REFERENCES reading_records(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_annotations
    ADD CONSTRAINT user_annotations_deleted_by_fkey FOREIGN KEY (deleted_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE ONLY user_annotations
    ADD CONSTRAINT user_annotations_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_credit_accounts
    ADD CONSTRAINT user_credit_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_credit_ledger
    ADD CONSTRAINT user_credit_ledger_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_identities
    ADD CONSTRAINT user_identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_password_credentials
    ADD CONSTRAINT user_password_credentials_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_sessions
    ADD CONSTRAINT user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY vocabulary_book
    ADD CONSTRAINT vocabulary_book_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
