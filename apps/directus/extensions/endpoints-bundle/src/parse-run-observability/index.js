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
        },
      });
    } catch (error) {
      next(error);
    }
  });
};
