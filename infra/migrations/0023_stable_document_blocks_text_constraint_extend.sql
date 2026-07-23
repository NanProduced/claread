-- 0023_stable_document_blocks_text_constraint_extend.sql
--
-- 扩展 ck_stable_document_blocks_text_for_textual_types 豁免列表，
-- 加入 ``list`` 和 ``thematic_break``。
--
-- 背景：
--   migration 0004 定义了 constraint，豁免列表为
--   ('table', 'table_row', 'table_cell', 'image', 'code_block', 'unknown')。
--   M1 Markdown Structured Source Contract 把 ``list`` 和 ``thematic_break``
--   加入了 StableDocumentBlockType 枚举和 _STRUCTURAL_BLOCK_TYPES
--   （app/schemas/reader_documents.py），因为 markdown-it-py 的
--   ordered_list_open / bullet_list_open token 不带 text_content，
--   thematic_break（``---``）也没有文本内容。
--
--   但当时漏了同步更新 DB constraint，导致 pasted_text/txt_file 走 markdown
--   解析路径后，list wrapper block 的 text_content=NULL 违反 constraint，
--   stable-ready 落库失败。
--
--   本 migration 把豁免列表对齐 schema 的 _STRUCTURAL_BLOCK_TYPES：
--     ('list', 'table', 'table_row', 'table_cell', 'image',
--      'code_block', 'thematic_break', 'unknown')
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

ALTER TABLE stable_document_blocks
  DROP CONSTRAINT IF EXISTS ck_stable_document_blocks_text_for_textual_types;

ALTER TABLE stable_document_blocks
  ADD CONSTRAINT ck_stable_document_blocks_text_for_textual_types
    CHECK (
      block_type IN ('list', 'table', 'table_row', 'table_cell', 'image', 'code_block', 'thematic_break', 'unknown')
      OR (text_content IS NOT NULL AND length(text_content) > 0)
    );
