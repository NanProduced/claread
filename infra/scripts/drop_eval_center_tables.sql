-- ============================================================
-- drop_eval_center_tables.sql — Data owner DROP manifest
--
-- CUTOVER-CONTROL-EVAL 收口：本文件是旧 Eval Center / Workflow Lab / Node Lab
-- 控制面 **12 张遗留表** 的精确 DROP manifest，供 Data owner 在后续独立的数据
-- 清理阶段执行（与旧 analysis_* 业务表清理一同进行）。
--
-- ⛔ 明确禁止：本 manifest **不得** 包含 `eval_example_lab_entries`。
--    该表是 KEEP/REHOME 的 Example Lab grammar curation substrate，由
--    hooks-bundle 的 validation/normalization hook 保护，不属于旧控制面删除范围。
--    check-logical-registration.mjs 会静态断言本文件不含 eval_example_lab_entries。
--
-- ⚠️ Cutover 阶段 **不执行** 本文件（无 SQL/DDL 运行）；仅作为 Data owner 的
--    准确清单存在。12 张表 = DB 中 13 张 eval_* 表减去受保护的
--    eval_example_lab_entries。
-- ============================================================

DROP TABLE IF EXISTS eval_node_lab_review_notes CASCADE;
DROP TABLE IF EXISTS eval_node_lab_judge_requests CASCADE;
DROP TABLE IF EXISTS eval_node_lab_judge_configs CASCADE;
DROP TABLE IF EXISTS eval_node_lab_trials CASCADE;
DROP TABLE IF EXISTS eval_node_lab_sessions CASCADE;
DROP TABLE IF EXISTS eval_node_lab_candidate_drafts CASCADE;
DROP TABLE IF EXISTS eval_workflow_compare_judge_requests CASCADE;
DROP TABLE IF EXISTS eval_workflow_compares CASCADE;
DROP TABLE IF EXISTS eval_review_notes CASCADE;
DROP TABLE IF EXISTS eval_judge_run_requests CASCADE;
DROP TABLE IF EXISTS eval_workflow_run_requests CASCADE;
DROP TABLE IF EXISTS eval_prompt_variant_drafts CASCADE;
