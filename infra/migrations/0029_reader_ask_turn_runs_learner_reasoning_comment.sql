-- 0029_reader_ask_turn_runs_learner_reasoning_comment.sql
--
-- ASK-LEARNER-REASONING-PROJECTOR-R1 (comment-only semantic update).
--
-- Does NOT re-create the column. Migration 0024 already added
-- ``reasoning_projection_json`` with ``ADD COLUMN IF NOT EXISTS``.
-- This file only refreshes the column COMMENT to document the
-- ``learner_reasoning_v1`` policy discriminator alongside the retired
-- ``reasoning_projection_v1`` shape.
--
-- Pre-apply check (human):
--   SELECT column_name
--   FROM information_schema.columns
--   WHERE table_name = 'reader_ask_turn_runs'
--     AND column_name = 'reasoning_projection_json';
--
-- Status: AUTHORED, NOT EXECUTED.

COMMENT ON COLUMN reader_ask_turn_runs.reasoning_projection_json IS
    'Safe learner-reasoning or legacy reasoning projection committed '
    'atomically with the ok answer (same UPDATE). Discriminator: '
    'projection_policy_version. '
    'learner_reasoning_v1 shape: '
    '{projection_policy_version, schema, text, stage, basis, revision, '
    'sequence, generation_id, truncated}. '
    'reasoning_projection_v1 (legacy) is retired at the public boundary. '
    'NULL when no summary was produced or the turn was not ok. Never '
    'carries raw provider reasoning, secrets, handles, or unredacted text.';
