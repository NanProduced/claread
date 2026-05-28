CREATE OR REPLACE VIEW console_parse_run_usage_v AS
SELECT
  e.id,
  e.record_id,
  e.task_id,
  e.capability_code,
  e.status,
  e.workflow_name,
  e.workflow_version,
  e.schema_version,
  e.prompt_version,
  e.model_route,
  e.model_profile,
  e.model_provider,
  e.model_name,
  e.input_tokens,
  e.output_tokens,
  e.total_tokens,
  e.billed_points,
  e.error_code,
  e.error_message,
  e.metadata_json,
  e.created_at
FROM ai_usage_events e
JOIN analysis_records r ON r.id = e.record_id
WHERE r.deleted_at IS NULL
  AND e.record_id IS NOT NULL
  AND e.capability_code IN ('analysis_full', 'analysis_overview_hint');

CREATE OR REPLACE VIEW console_parse_run_events_v AS
SELECT
  t.analysis_record_id AS record_id,
  'analysis_task'::text AS lane,
  t.id AS task_id,
  t.status AS task_status,
  e.id AS event_id,
  e.event_type,
  e.created_at,
  e.event_payload_json
FROM analysis_tasks t
JOIN analysis_task_events e ON e.task_id = t.id
JOIN analysis_records r ON r.id = t.analysis_record_id
WHERE r.deleted_at IS NULL

UNION ALL

SELECT
  t.analysis_record_id AS record_id,
  'overview_task'::text AS lane,
  t.id AS task_id,
  t.status AS task_status,
  e.id AS event_id,
  e.event_type,
  e.created_at,
  e.event_payload_json
FROM analysis_overview_tasks t
JOIN analysis_overview_task_events e ON e.task_id = t.id
JOIN analysis_records r ON r.id = t.analysis_record_id
WHERE r.deleted_at IS NULL;

CREATE OR REPLACE VIEW console_parse_runs_v AS
WITH result_payloads AS (
  SELECT
    c.record_id,
    CASE
      WHEN jsonb_typeof(c.render_scene_json) = 'object' THEN c.render_scene_json
      WHEN jsonb_typeof(c.render_scene_json) = 'string'
        AND NULLIF(c.render_scene_json #>> '{}', '') IS NOT NULL
        THEN (c.render_scene_json #>> '{}')::jsonb
      ELSE '{}'::jsonb
    END AS normalized_render_scene_json,
    CASE
      WHEN jsonb_typeof(c.page_state_json) = 'object' THEN c.page_state_json
      WHEN jsonb_typeof(c.page_state_json) = 'string'
        AND NULLIF(c.page_state_json #>> '{}', '') IS NOT NULL
        THEN (c.page_state_json #>> '{}')::jsonb
      ELSE '{}'::jsonb
    END AS normalized_page_state_json,
    c.workflow_version AS result_workflow_version,
    c.schema_version AS result_schema_version
  FROM analysis_results c
),
warning_rows AS (
  SELECT
    p.record_id,
    warning
  FROM result_payloads p
  LEFT JOIN LATERAL jsonb_array_elements(
    CASE
      WHEN jsonb_typeof(p.normalized_render_scene_json -> 'warnings') = 'array'
        THEN p.normalized_render_scene_json -> 'warnings'
      ELSE '[]'::jsonb
    END
  ) warning ON TRUE
),
warning_agg AS (
  SELECT
    record_id,
    COUNT(*) FILTER (WHERE warning IS NOT NULL)::int AS warnings_count,
    COALESCE(
      ARRAY_AGG(DISTINCT COALESCE(warning ->> 'code', 'unknown_warning'))
        FILTER (WHERE warning IS NOT NULL),
      ARRAY[]::text[]
    ) AS warning_codes
  FROM warning_rows
  GROUP BY record_id
),
latest_analysis_task AS (
  SELECT DISTINCT ON (analysis_record_id)
    analysis_record_id,
    id AS main_task_id,
    status AS main_task_status,
    attempt_no AS main_task_attempt_no,
    failure_code AS main_task_failure_code,
    failure_message AS main_task_failure_message,
    usage_summary_json AS main_task_usage_summary_json,
    quota_cost_points AS main_task_quota_cost_points,
    queued_at AS main_task_queued_at,
    started_at AS main_task_started_at,
    finished_at AS main_task_finished_at,
    updated_at AS main_task_updated_at
  FROM analysis_tasks
  ORDER BY analysis_record_id, updated_at DESC, queued_at DESC
),
latest_overview_task AS (
  SELECT DISTINCT ON (analysis_record_id)
    analysis_record_id,
    id AS overview_task_id,
    status AS overview_task_status,
    attempt_no AS overview_task_attempt_no,
    failure_code AS overview_task_failure_code,
    failure_message AS overview_task_failure_message,
    usage_summary_json AS overview_task_usage_summary_json,
    queued_at AS overview_task_queued_at,
    started_at AS overview_task_started_at,
    finished_at AS overview_task_finished_at,
    updated_at AS overview_task_updated_at
  FROM analysis_overview_tasks
  ORDER BY analysis_record_id, updated_at DESC, queued_at DESC
),
usage_totals AS (
  SELECT
    record_id,
    COALESCE(SUM(total_tokens), 0)::int AS total_tokens,
    COALESCE(SUM(billed_points), 0)::int AS billed_points,
    COUNT(*)::int AS usage_event_count
  FROM console_parse_run_usage_v
  GROUP BY record_id
),
latest_usage AS (
  SELECT DISTINCT ON (record_id)
    record_id,
    capability_code AS latest_capability_code,
    prompt_version AS latest_prompt_version,
    workflow_version AS latest_workflow_version,
    schema_version AS latest_schema_version,
    model_route AS latest_model_route,
    created_at AS latest_usage_created_at
  FROM console_parse_run_usage_v
  ORDER BY record_id, created_at DESC
)
SELECT
  r.id AS record_id,
  r.title,
  r.source_type,
  CASE
    WHEN char_length(r.source_text) > 180 THEN substring(r.source_text FROM 1 FOR 177) || '...'
    ELSE r.source_text
  END AS source_text_excerpt,
  r.reading_goal,
  r.reading_variant,
  r.analysis_status,
  r.user_facing_state,
  r.created_at,
  r.updated_at,
  p.result_workflow_version,
  p.result_schema_version,
  COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') AS schema_version,
  CASE
    WHEN COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') IS NULL THEN 'unknown'
    WHEN COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') = '3.0.0' THEN 'learning'
    WHEN COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') = '3.0.0-academic' THEN 'academic'
    ELSE 'unsupported_schema'
  END AS schema_type,
  COALESCE(w.warnings_count, 0) AS warnings_count,
  COALESCE(w.warning_codes, ARRAY[]::text[]) AS warning_codes,
  COALESCE(w.warnings_count, 0) > 0 AS has_warnings,
  p.normalized_page_state_json #>> '{derived,overview_hint,status}' AS overview_hint_status,
  p.normalized_page_state_json #>> '{derived,overview_hint,overview}' AS overview_hint_text,
  t.main_task_id,
  t.main_task_status,
  t.main_task_attempt_no,
  t.main_task_failure_code,
  t.main_task_failure_message,
  t.main_task_usage_summary_json,
  t.main_task_quota_cost_points,
  t.main_task_queued_at,
  t.main_task_started_at,
  t.main_task_finished_at,
  t.main_task_updated_at,
  o.overview_task_id,
  o.overview_task_status,
  o.overview_task_attempt_no,
  o.overview_task_failure_code,
  o.overview_task_failure_message,
  o.overview_task_usage_summary_json,
  o.overview_task_queued_at,
  o.overview_task_started_at,
  o.overview_task_finished_at,
  o.overview_task_updated_at,
  COALESCE(u.total_tokens, 0) AS total_tokens,
  COALESCE(u.billed_points, 0) AS billed_points,
  COALESCE(u.usage_event_count, 0) AS usage_event_count,
  lu.latest_capability_code,
  lu.latest_prompt_version,
  lu.latest_workflow_version,
  lu.latest_schema_version,
  lu.latest_model_route,
  lu.latest_usage_created_at,
  CASE
    WHEN COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') NOT IN ('3.0.0', '3.0.0-academic')
      AND COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') IS NOT NULL
      THEN 'invalid'
    WHEN r.analysis_status = 'failed'
      OR t.main_task_status = 'failed'
      OR o.overview_task_status = 'failed'
      THEN 'failed'
    WHEN r.analysis_status = 'partial'
      OR COALESCE(r.user_facing_state, 'normal') <> 'normal'
      OR COALESCE(w.warnings_count, 0) > 0
      THEN 'attention'
    WHEN t.main_task_status = 'succeeded'
      THEN 'healthy'
    ELSE 'active'
  END AS parse_health,
  ARRAY_REMOVE(
    ARRAY[
      CASE WHEN r.analysis_status = 'partial' THEN 'partial_status' END,
      CASE WHEN COALESCE(r.user_facing_state, 'normal') <> 'normal' THEN 'degraded_output' END,
      CASE WHEN COALESCE(w.warnings_count, 0) > 0 THEN 'has_warnings' END,
      CASE WHEN t.main_task_status = 'failed' THEN 'main_task_failed' END,
      CASE WHEN o.overview_task_status = 'failed' THEN 'overview_task_failed' END,
      CASE
        WHEN COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') NOT IN ('3.0.0', '3.0.0-academic')
          AND COALESCE(p.result_schema_version, p.normalized_render_scene_json ->> 'schema_version') IS NOT NULL
          THEN 'unsupported_schema'
      END
    ]::text[],
    NULL
  ) AS risk_reasons
FROM analysis_records r
LEFT JOIN result_payloads p ON p.record_id = r.id
LEFT JOIN warning_agg w ON w.record_id = r.id
LEFT JOIN latest_analysis_task t ON t.analysis_record_id = r.id
LEFT JOIN latest_overview_task o ON o.analysis_record_id = r.id
LEFT JOIN usage_totals u ON u.record_id = r.id
LEFT JOIN latest_usage lu ON lu.record_id = r.id
WHERE r.deleted_at IS NULL;
