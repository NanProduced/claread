function clampLimit(value) {
  const parsed = Number.parseInt(String(value ?? "5"), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return 5;
  return Math.min(parsed, 20);
}

function clampDays(value) {
  const parsed = Number.parseInt(String(value ?? "7"), 10);
  if (!Number.isFinite(parsed) || parsed < 1 || parsed > 30) return 7;
  return parsed;
}

function normalizeRows(result) {
  if (Array.isArray(result)) return result;
  if (Array.isArray(result?.rows)) return result.rows;
  if (Array.isArray(result?.[0])) return result[0];
  return [];
}

function firstRow(result) {
  return normalizeRows(result)[0] ?? {};
}

function toNumber(value) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function buildAuthGuard(req, res) {
  const accountability = req.accountability;
  if (!accountability?.user && accountability?.admin !== true) {
    res.status(403).json({
      errors: [
        {
          message: "Authentication required.",
          extensions: { code: "FORBIDDEN" },
        },
      ],
    });
    return false;
  }
  return true;
}

function buildRow(row) {
  const lane = row.lane === "overview" ? "overview" : "analysis";
  const taskCollection = lane === "overview" ? "analysis_overview_tasks" : "analysis_tasks";

  return {
    lane,
    task_id: row.task_id,
    record_id: row.record_id,
    record_title: row.record_title,
    client_record_id: row.client_record_id,
    failure_code: row.failure_code,
    failure_message: row.failure_message,
    queued_at: row.queued_at,
    detail_url: `/admin/content/${taskCollection}/${encodeURIComponent(row.task_id)}`,
    inspector_url: row.record_id
      ? `/admin/claread-render-scene-inspector?record=${encodeURIComponent(row.record_id)}`
      : null,
  };
}

function normalizeHealth(row) {
  const total = toNumber(row.total);
  const succeeded = toNumber(row.succeeded);
  return {
    total,
    succeeded,
    failed: toNumber(row.failed),
    success_rate: total > 0 ? succeeded / total : 0,
    active: toNumber(row.active),
    stale: toNumber(row.stale),
    window_days: toNumber(row.window_days),
  };
}

function normalizeQueueRows(rows) {
  const base = {
    analysis: {
      lane: "analysis",
      queued: 0,
      running: 0,
      finalizing: 0,
      stale_queued: 0,
      stale_active: 0,
      oldest_queued_wait_seconds: null,
      oldest_active_run_seconds: null,
    },
    overview: {
      lane: "overview",
      queued: 0,
      running: 0,
      finalizing: 0,
      stale_queued: 0,
      stale_active: 0,
      oldest_queued_wait_seconds: null,
      oldest_active_run_seconds: null,
    },
  };

  for (const row of rows) {
    const lane = row.lane === "overview" ? "overview" : "analysis";
    const status = String(row.status || "");
    if (status in base[lane]) base[lane][status] = toNumber(row.count);
    base[lane].stale_queued += toNumber(row.stale_queued);
    base[lane].stale_active += toNumber(row.stale_active);
    if (row.oldest_queued_wait_seconds != null) {
      base[lane].oldest_queued_wait_seconds = Math.max(
        base[lane].oldest_queued_wait_seconds ?? 0,
        toNumber(row.oldest_queued_wait_seconds),
      );
    }
    if (row.oldest_active_run_seconds != null) {
      base[lane].oldest_active_run_seconds = Math.max(
        base[lane].oldest_active_run_seconds ?? 0,
        toNumber(row.oldest_active_run_seconds),
      );
    }
  }

  return {
    lanes: Object.values(base).map((lane) => ({
      ...lane,
      active: lane.queued + lane.running + lane.finalizing,
      stale: lane.stale_queued + lane.stale_active,
    })),
  };
}

function normalizeFailureRows(rows) {
  return rows.map((row) => ({
    failure_code: row.failure_code || "UnknownFailure",
    count: toNumber(row.count),
    analysis_count: toNumber(row.analysis_count),
    overview_count: toNumber(row.overview_count),
    latest_queued_at: row.latest_queued_at,
  }));
}

function normalizeQualityRows(rows) {
  return rows.map((row) => ({
    state: row.state || "unknown",
    count: toNumber(row.count),
    ready_count: toNumber(row.ready_count),
    failed_count: toNumber(row.failed_count),
  }));
}

function normalizeDailyCostRows(rows) {
  return rows.map((row) => ({
    day: row.day,
    calls: toNumber(row.calls),
    total_tokens: toNumber(row.total_tokens),
    billed_points: toNumber(row.billed_points),
  }));
}

function normalizeTopRows(rows) {
  return rows.map((row) => ({
    key: row.key || "unknown",
    label: row.label || row.key || "unknown",
    detail: row.detail || "",
    calls: toNumber(row.calls),
    total_tokens: toNumber(row.total_tokens),
    billed_points: toNumber(row.billed_points),
  }));
}

function normalizeRagOverview(row) {
  const snapshots = toNumber(row.snapshots);
  const outputTypeCount = toNumber(row.output_type_count);
  const ragHits = toNumber(row.rag_hits);

  return {
    snapshots,
    output_type_count: outputTypeCount,
    rag_hits: ragHits,
    fallback_count: toNumber(row.fallback_count),
    low_confidence_count: toNumber(row.low_confidence_count),
    hit_rate: outputTypeCount > 0 ? ragHits / outputTypeCount : 0,
  };
}

function normalizeRagOutputRows(rows) {
  return rows.map((row) => {
    const outputTypeCount = toNumber(row.output_type_count);
    const ragHits = toNumber(row.rag_hits);
    return {
      output_type: row.output_type || "unknown",
      output_type_count: outputTypeCount,
      rag_hits: ragHits,
      fallback_count: toNumber(row.fallback_count),
      low_confidence_count: toNumber(row.low_confidence_count),
      hit_rate: outputTypeCount > 0 ? ragHits / outputTypeCount : 0,
      avg_ann_hit_count: toNumber(row.avg_ann_hit_count),
      avg_rerank_hit_count: toNumber(row.avg_rerank_hit_count),
      avg_selected_examples: toNumber(row.avg_selected_examples),
      avg_embedding_latency_ms: toNumber(row.avg_embedding_latency_ms),
      avg_rerank_latency_ms: toNumber(row.avg_rerank_latency_ms),
    };
  });
}

function normalizeReasonRows(rows, keyField) {
  return rows.map((row) => ({
    key: row[keyField] || "unknown",
    label: row[keyField] || "unknown",
    count: toNumber(row.count),
  }));
}

export default (router, { database }) => {
  router.get("/recent-failures", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const limit = clampLimit(req.query?.limit);
      const result = await database.raw(
        `
          SELECT *
          FROM (
            SELECT
              'analysis' AS lane,
              t.id::text AS task_id,
              t.analysis_record_id::text AS record_id,
              r.title AS record_title,
              r.client_record_id AS client_record_id,
              t.failure_code AS failure_code,
              t.failure_message AS failure_message,
              t.queued_at AS queued_at
            FROM analysis_tasks t
            LEFT JOIN analysis_records r ON r.id = t.analysis_record_id
            WHERE t.status = 'failed'

            UNION ALL

            SELECT
              'overview' AS lane,
              t.id::text AS task_id,
              t.analysis_record_id::text AS record_id,
              r.title AS record_title,
              r.client_record_id AS client_record_id,
              t.failure_code AS failure_code,
              t.failure_message AS failure_message,
              t.queued_at AS queued_at
            FROM analysis_overview_tasks t
            LEFT JOIN analysis_records r ON r.id = t.analysis_record_id
            WHERE t.status = 'failed'
          ) failures
          ORDER BY queued_at DESC NULLS LAST
          LIMIT ?
        `,
        [limit],
      );

      res.json({
        data: normalizeRows(result).map(buildRow),
      });
    } catch (error) {
      next(error);
    }
  });

  router.get("/summary", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    const days = clampDays(req.query?.days);
    const usageCapabilities = [
      "analysis_full",
      "analysis_overview_hint",
      "rag_embedding",
      "rag_rerank",
    ];
    const usageCapabilityPlaceholders = usageCapabilities.map(() => "?").join(", ");

    try {
      const [
        healthResult,
        failureResult,
        queueResult,
        qualityResult,
        dailyCostResult,
        topModelResult,
        topCapabilityResult,
        ragOverviewResult,
        ragByOutputTypeResult,
        ragFallbackReasonResult,
        ragDropReasonResult,
      ] = await Promise.all([
        database.raw(
          `
            WITH recent_tasks AS (
              SELECT status
              FROM analysis_tasks
              WHERE queued_at >= now() - (?::int * interval '1 day')

              UNION ALL

              SELECT status
              FROM analysis_overview_tasks
              WHERE queued_at >= now() - (?::int * interval '1 day')
            ),
            active_tasks AS (
              SELECT status, queued_at, updated_at
              FROM analysis_tasks
              WHERE status IN ('queued', 'running', 'finalizing')

              UNION ALL

              SELECT status, queued_at, updated_at
              FROM analysis_overview_tasks
              WHERE status IN ('queued', 'running', 'finalizing')
            )
            SELECT
              ?::int AS window_days,
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE recent_tasks.status = 'succeeded') AS succeeded,
              COUNT(*) FILTER (WHERE recent_tasks.status = 'failed') AS failed,
              (SELECT COUNT(*) FROM active_tasks) AS active,
              (
                SELECT COUNT(*)
                FROM active_tasks
                WHERE (
                  status = 'queued'
                  AND queued_at < now() - interval '5 minutes'
                )
                OR (
                  status IN ('running', 'finalizing')
                  AND updated_at < now() - interval '5 minutes'
                )
              ) AS stale
            FROM recent_tasks
          `,
          [days, days, days],
        ),
        database.raw(
          `
            SELECT
              COALESCE(NULLIF(failure_code, ''), 'UnknownFailure') AS failure_code,
              COUNT(*) AS count,
              COUNT(*) FILTER (WHERE lane = 'analysis') AS analysis_count,
              COUNT(*) FILTER (WHERE lane = 'overview') AS overview_count,
              MAX(queued_at) AS latest_queued_at
            FROM (
              SELECT 'analysis' AS lane, failure_code, queued_at
              FROM analysis_tasks
              WHERE status = 'failed'
                AND queued_at >= now() - (?::int * interval '1 day')

              UNION ALL

              SELECT 'overview' AS lane, failure_code, queued_at
              FROM analysis_overview_tasks
              WHERE status = 'failed'
                AND queued_at >= now() - (?::int * interval '1 day')
            ) failures
            GROUP BY COALESCE(NULLIF(failure_code, ''), 'UnknownFailure')
            ORDER BY count DESC, latest_queued_at DESC NULLS LAST
            LIMIT 5
          `,
          [days, days],
        ),
        database.raw(
          `
            SELECT
              lane,
              status,
              COUNT(*) AS count,
              COUNT(*) FILTER (
                WHERE status = 'queued'
                  AND queued_at < now() - interval '5 minutes'
              ) AS stale_queued,
              COUNT(*) FILTER (
                WHERE status IN ('running', 'finalizing')
                  AND updated_at < now() - interval '5 minutes'
              ) AS stale_active,
              MAX(EXTRACT(EPOCH FROM (now() - queued_at))) FILTER (
                WHERE status = 'queued'
              ) AS oldest_queued_wait_seconds,
              MAX(EXTRACT(EPOCH FROM (now() - COALESCE(started_at, queued_at)))) FILTER (
                WHERE status IN ('running', 'finalizing')
              ) AS oldest_active_run_seconds
            FROM (
              SELECT 'analysis' AS lane, status, queued_at, started_at, updated_at
              FROM analysis_tasks
              WHERE status IN ('queued', 'running', 'finalizing')

              UNION ALL

              SELECT 'overview' AS lane, status, queued_at, started_at, updated_at
              FROM analysis_overview_tasks
              WHERE status IN ('queued', 'running', 'finalizing')
            ) active_tasks
            GROUP BY lane, status
            ORDER BY lane, status
          `,
        ),
        database.raw(
          `
            SELECT
              COALESCE(
                NULLIF(user_facing_state, ''),
                CASE WHEN analysis_status = 'failed' THEN 'failed' ELSE 'unknown' END
              ) AS state,
              COUNT(*) AS count,
              COUNT(*) FILTER (WHERE analysis_status = 'ready') AS ready_count,
              COUNT(*) FILTER (WHERE analysis_status = 'failed') AS failed_count
            FROM analysis_records
            WHERE deleted_at IS NULL
              AND created_at >= now() - (?::int * interval '1 day')
            GROUP BY COALESCE(
              NULLIF(user_facing_state, ''),
              CASE WHEN analysis_status = 'failed' THEN 'failed' ELSE 'unknown' END
            )
            ORDER BY count DESC
          `,
          [days],
        ),
        database.raw(
          `
            WITH day_series AS (
              SELECT generate_series(
                (CURRENT_DATE - ((?::int - 1) * interval '1 day'))::date,
                CURRENT_DATE,
                interval '1 day'
              )::date AS day
            ),
            events AS (
              SELECT
                created_at::date AS day,
                COUNT(*) AS calls,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(billed_points), 0) AS billed_points
              FROM ai_usage_events
              WHERE capability_code IN (${usageCapabilityPlaceholders})
                AND created_at::date >= (CURRENT_DATE - ((?::int - 1) * interval '1 day'))::date
              GROUP BY created_at::date
            )
            SELECT
              day_series.day::text AS day,
              COALESCE(events.calls, 0) AS calls,
              COALESCE(events.total_tokens, 0) AS total_tokens,
              COALESCE(events.billed_points, 0) AS billed_points
            FROM day_series
            LEFT JOIN events ON events.day = day_series.day
            ORDER BY day_series.day
          `,
          [days, ...usageCapabilities, days],
        ),
        database.raw(
          `
            SELECT
              COALESCE(NULLIF(model_provider, ''), 'unknown') || ':' ||
                COALESCE(NULLIF(model_name, ''), 'unknown') AS key,
              COALESCE(NULLIF(model_name, ''), '未记录模型') AS label,
              COALESCE(NULLIF(model_provider, ''), 'provider 未记录') AS detail,
              COUNT(*) AS calls,
              COALESCE(SUM(total_tokens), 0) AS total_tokens,
              COALESCE(SUM(billed_points), 0) AS billed_points
            FROM ai_usage_events
            WHERE capability_code IN (${usageCapabilityPlaceholders})
              AND created_at >= now() - (?::int * interval '1 day')
            GROUP BY model_provider, model_name
            ORDER BY total_tokens DESC, calls DESC
            LIMIT 5
          `,
          [...usageCapabilities, days],
        ),
        database.raw(
          `
            SELECT
              capability_code AS key,
              capability_code AS label,
              '' AS detail,
              COUNT(*) AS calls,
              COALESCE(SUM(total_tokens), 0) AS total_tokens,
              COALESCE(SUM(billed_points), 0) AS billed_points
            FROM ai_usage_events
            WHERE capability_code IN (${usageCapabilityPlaceholders})
              AND created_at >= now() - (?::int * interval '1 day')
            GROUP BY capability_code
            ORDER BY total_tokens DESC, calls DESC
            LIMIT 5
          `,
          [...usageCapabilities, days],
        ),
        database.raw(
          `
            WITH snapshots AS (
              SELECT id
              FROM analysis_debug_snapshots
              WHERE rag_debug_json IS NOT NULL
                AND created_at >= now() - (?::int * interval '1 day')
            ),
            expanded AS (
              SELECT
                snapshot.id AS snapshot_id,
                entry.key AS output_type,
                entry.value AS item
              FROM snapshots snapshot
              JOIN analysis_debug_snapshots source ON source.id = snapshot.id
              CROSS JOIN LATERAL jsonb_each(
                COALESCE(source.rag_debug_json #> '{agents,grammar}', '{}'::jsonb)
              ) AS entry(key, value)
            )
            SELECT
              (SELECT COUNT(*) FROM snapshots) AS snapshots,
              COUNT(*) AS output_type_count,
              COUNT(*) FILTER (WHERE item ->> 'selection_mode' = 'rag') AS rag_hits,
              COUNT(*) FILTER (
                WHERE item ->> 'is_fallback' = 'true'
                   OR item ->> 'selection_mode' IN ('rag_fallback', 'baseline')
                   OR COALESCE(NULLIF(item ->> 'fallback_reason', ''), '') <> ''
              ) AS fallback_count,
              COUNT(*) FILTER (WHERE item ->> 'fallback_reason' = 'low_confidence') AS low_confidence_count
            FROM expanded
          `,
          [days],
        ),
        database.raw(
          `
            WITH expanded AS (
              SELECT
                entry.key AS output_type,
                entry.value AS item
              FROM analysis_debug_snapshots source
              CROSS JOIN LATERAL jsonb_each(
                COALESCE(source.rag_debug_json #> '{agents,grammar}', '{}'::jsonb)
              ) AS entry(key, value)
              WHERE source.rag_debug_json IS NOT NULL
                AND source.created_at >= now() - (?::int * interval '1 day')
            ),
            typed AS (
              SELECT
                output_type,
                item ->> 'selection_mode' AS selection_mode,
                item ->> 'fallback_reason' AS fallback_reason,
                item ->> 'is_fallback' AS is_fallback,
                CASE WHEN jsonb_typeof(item -> 'ann_hit_count') = 'number'
                  THEN (item ->> 'ann_hit_count')::numeric ELSE 0 END AS ann_hit_count,
                CASE WHEN jsonb_typeof(item -> 'rerank_hit_count') = 'number'
                  THEN (item ->> 'rerank_hit_count')::numeric ELSE 0 END AS rerank_hit_count,
                CASE WHEN jsonb_typeof(item -> 'example_count') = 'number'
                  THEN (item ->> 'example_count')::numeric ELSE 0 END AS selected_examples,
                CASE WHEN jsonb_typeof(item -> 'embedding_latency_ms') = 'number'
                  THEN (item ->> 'embedding_latency_ms')::numeric ELSE 0 END AS embedding_latency_ms,
                CASE WHEN jsonb_typeof(item -> 'rerank_latency_ms') = 'number'
                  THEN (item ->> 'rerank_latency_ms')::numeric ELSE 0 END AS rerank_latency_ms
              FROM expanded
            )
            SELECT
              output_type,
              COUNT(*) AS output_type_count,
              COUNT(*) FILTER (WHERE selection_mode = 'rag') AS rag_hits,
              COUNT(*) FILTER (
                WHERE is_fallback = 'true'
                   OR selection_mode IN ('rag_fallback', 'baseline')
                   OR COALESCE(NULLIF(fallback_reason, ''), '') <> ''
              ) AS fallback_count,
              COUNT(*) FILTER (WHERE fallback_reason = 'low_confidence') AS low_confidence_count,
              COALESCE(AVG(ann_hit_count), 0) AS avg_ann_hit_count,
              COALESCE(AVG(rerank_hit_count), 0) AS avg_rerank_hit_count,
              COALESCE(AVG(selected_examples), 0) AS avg_selected_examples,
              COALESCE(AVG(embedding_latency_ms), 0) AS avg_embedding_latency_ms,
              COALESCE(AVG(rerank_latency_ms), 0) AS avg_rerank_latency_ms
            FROM typed
            GROUP BY output_type
            ORDER BY output_type
          `,
          [days],
        ),
        database.raw(
          `
            WITH expanded AS (
              SELECT
                COALESCE(NULLIF(entry.value ->> 'fallback_reason', ''), 'UnknownFallback') AS fallback_reason,
                entry.value ->> 'selection_mode' AS selection_mode,
                entry.value ->> 'is_fallback' AS is_fallback
              FROM analysis_debug_snapshots source
              CROSS JOIN LATERAL jsonb_each(
                COALESCE(source.rag_debug_json #> '{agents,grammar}', '{}'::jsonb)
              ) AS entry(key, value)
              WHERE source.rag_debug_json IS NOT NULL
                AND source.created_at >= now() - (?::int * interval '1 day')
            )
            SELECT fallback_reason, COUNT(*) AS count
            FROM expanded
            WHERE is_fallback = 'true'
               OR selection_mode IN ('rag_fallback', 'baseline')
               OR fallback_reason <> 'UnknownFallback'
            GROUP BY fallback_reason
            ORDER BY count DESC, fallback_reason
            LIMIT 5
          `,
          [days],
        ),
        database.raw(
          `
            WITH expanded AS (
              SELECT entry.value AS item
              FROM analysis_debug_snapshots source
              CROSS JOIN LATERAL jsonb_each(
                COALESCE(source.rag_debug_json #> '{agents,grammar}', '{}'::jsonb)
              ) AS entry(key, value)
              WHERE source.rag_debug_json IS NOT NULL
                AND source.created_at >= now() - (?::int * interval '1 day')
            ),
            drops AS (
              SELECT
                COALESCE(NULLIF(drop_item ->> 'drop_stage', ''), 'unknown') AS drop_stage,
                COALESCE(NULLIF(drop_item ->> 'drop_reason', ''), 'unknown') AS drop_reason
              FROM expanded
              CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                  WHEN jsonb_typeof(item -> 'dropped_examples') = 'array'
                  THEN item -> 'dropped_examples'
                  ELSE '[]'::jsonb
                END
              ) AS drop_item
            )
            SELECT
              drop_stage || ':' || drop_reason AS drop_reason,
              COUNT(*) AS count
            FROM drops
            GROUP BY drop_stage, drop_reason
            ORDER BY count DESC, drop_stage, drop_reason
            LIMIT 5
          `,
          [days],
        ),
      ]);

      res.json({
        data: {
          window_days: days,
          generated_at: new Date().toISOString(),
          health: normalizeHealth(firstRow(healthResult)),
          failure_types: normalizeFailureRows(normalizeRows(failureResult)),
          queue: normalizeQueueRows(normalizeRows(queueResult)),
          quality: {
            rows: normalizeQualityRows(normalizeRows(qualityResult)),
          },
          model_cost_trend: {
            days: normalizeDailyCostRows(normalizeRows(dailyCostResult)),
            top_models: normalizeTopRows(normalizeRows(topModelResult)),
            top_capabilities: normalizeTopRows(normalizeRows(topCapabilityResult)),
          },
          rag_quality: {
            overview: normalizeRagOverview(firstRow(ragOverviewResult)),
            by_output_type: normalizeRagOutputRows(normalizeRows(ragByOutputTypeResult)),
            fallback_reasons: normalizeReasonRows(normalizeRows(ragFallbackReasonResult), "fallback_reason"),
            drop_reasons: normalizeReasonRows(normalizeRows(ragDropReasonResult), "drop_reason"),
          },
        },
      });
    } catch (error) {
      next(error);
    }
  });
};
