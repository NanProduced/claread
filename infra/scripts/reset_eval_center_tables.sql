BEGIN;

TRUNCATE TABLE
  eval_prompt_variant_drafts,
  eval_workflow_run_requests,
  eval_workflow_compares,
  eval_workflow_compare_judge_requests,
  eval_judge_run_requests,
  eval_review_notes,
  eval_node_lab_candidate_drafts,
  eval_node_lab_sessions,
  eval_node_lab_trials,
  eval_node_lab_judge_configs,
  eval_node_lab_judge_requests,
  eval_node_lab_review_notes,
  eval_example_lab_entries
RESTART IDENTITY CASCADE;

COMMIT;
