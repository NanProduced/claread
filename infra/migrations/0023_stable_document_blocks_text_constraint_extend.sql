-- 0023_stable_document_blocks_text_constraint_extend.sql
--
-- 同步 stable_document_blocks 的两个 CHECK constraint 到 M1 schema：
--   1. block_type 允许列表（stable_document_blocks_block_type_check）
--   2. text_content 豁免列表（ck_stable_document_blocks_text_for_textual_types）
--
-- 背景：
--   migration 0004 定义了这两个 constraint。M1 Markdown Structured Source
--   Contract 把 ``list`` 和 ``thematic_break`` 加入了
--   StableDocumentBlockType 枚举和 _STRUCTURAL_BLOCK_TYPES
--   （app/schemas/reader_documents.py），因为 markdown-it-py 的
--   ordered_list_open / bullet_list_open token 不带 text_content，
--   thematic_break（``---``）也没有文本内容。
--
--   但当时漏了同步更新 DB 的两个 constraint：
--   - block_type check 的允许列表少了 ``list`` 和 ``thematic_break``，
--     导致 ``list`` 类型 block 直接被拒绝（stable_document_blocks_block_type_check
--     violation）。
--   - text_content check 的豁免列表少了 ``list`` 和 ``thematic_break``，
--     导致 text_content=NULL 的结构性 block 违反
--     ck_stable_document_blocks_text_for_textual_types。
--
--   断点2（pasted_text/txt_file 检测到 Markdown 结构升级走 markdown path）
--   让这条路径对粘贴文本可达后，两个 gap 同时暴露。
--
--   本 migration 把两个 constraint 对齐 schema：
--   - block_type 允许列表加入 ``list`` 和 ``thematic_break``
--   - text_content 豁免列表加入 ``list`` 和 ``thematic_break``
--     （与 _STRUCTURAL_BLOCK_TYPES 一致）
--
-- Pre-apply safety check（应返回 0 行，因为 constraint 漏掉的是
-- text_content 允许为 NULL 的结构性 block，不应有违规行）：
--
--   SELECT block_type, count(*)
--   FROM stable_document_blocks
--   WHERE block_type IN ('list', 'thematic_break')
--     AND text_content IS NULL
--   GROUP BY 1;
--
-- Status: AUTHORED, NOT EXECUTED（需用户在本地执行 migration 并做 DB 备份）。

-- (1) block_type 允许列表：加入 list 和 thematic_break
ALTER TABLE stable_document_blocks
  DROP CONSTRAINT IF EXISTS stable_document_blocks_block_type_check;

ALTER TABLE stable_document_blocks
  ADD CONSTRAINT stable_document_blocks_block_type_check
    CHECK (block_type IN (
      'paragraph',
      'heading',
      'list',
      'list_item',
      'blockquote',
      'table',
      'table_row',
      'table_cell',
      'footnote',
      'image',
      'image_ocr',
      'caption',
      'code_block',
      'thematic_break',
      'unknown'
    ));

-- (2) text_content 豁免列表：加入 list 和 thematic_break
ALTER TABLE stable_document_blocks
  DROP CONSTRAINT IF EXISTS ck_stable_document_blocks_text_for_textual_types;

ALTER TABLE stable_document_blocks
  ADD CONSTRAINT ck_stable_document_blocks_text_for_textual_types
    CHECK (
      block_type IN ('list', 'table', 'table_row', 'table_cell', 'image', 'code_block', 'thematic_break', 'unknown')
      OR (text_content IS NOT NULL AND length(text_content) > 0)
    );
