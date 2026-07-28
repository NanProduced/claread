-- 0025_confirmed_source_documents.sql
-- L2 单一 Confirmed Source 生命周期实体。
-- 依据 docs/tmp/TMP-reader-confirmed-source-schema-api-design-2026-07-28.md §1
-- 与 docs/tmp/TMP-reader-markdown-adaptation-analysis-2026-07-28.md §5.3。
-- 不变量：每个 (reading_record_id, record_generation) 至多一行，
-- 全库任意时刻每个 generation 只有本表 markdown_text 一份完整正文。
-- 不动 0001/0004 任何已有 migration（遵循 0004 文件头与 0023 增量修正先例）。

CREATE TABLE confirmed_source_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reading_record_id UUID NOT NULL
    REFERENCES reading_records(id) ON DELETE CASCADE,
  user_id UUID NOT NULL
    REFERENCES users(id) ON DELETE CASCADE,
  record_generation INTEGER NOT NULL CHECK (record_generation >= 1),
  -- 输入 lineage 链接；original_inputs 行只随 record 级联删除，
  -- 防御性 SET NULL，不作为生命周期依赖。
  original_input_id UUID
    REFERENCES original_inputs(id) ON DELETE SET NULL,
  markdown_text TEXT NOT NULL CHECK (markdown_text <> ''),
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'frozen')),
  -- 审计元数据：正文来源（不保存旧版正文，只保存分类标签）。
  edit_source TEXT NOT NULL DEFAULT 'initial' CHECK (edit_source IN (
    'initial', 'extraction', 'wysiwyg', 'source_mode', 'content_check'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  frozen_at TIMESTAMPTZ,
  CONSTRAINT uq_confirmed_source_documents_record_generation
    UNIQUE (reading_record_id, record_generation),
  CONSTRAINT uq_confirmed_source_documents_id_record
    UNIQUE (id, reading_record_id),
  -- 与 reading_bases 相同的 DB 级 hash 自校验先例
  -- （0001_initial_schema.sql:795-796）。
  CONSTRAINT ck_confirmed_source_documents_content_sha256
    CHECK (content_sha256 = encode(digest(markdown_text, 'sha256'), 'hex')),
  CONSTRAINT ck_confirmed_source_documents_frozen_at
    CHECK (
      (status = 'frozen' AND frozen_at IS NOT NULL)
      OR (status = 'draft' AND frozen_at IS NULL)
    )
);

CREATE INDEX idx_confirmed_source_documents_user_updated
  ON confirmed_source_documents (user_id, updated_at DESC);
