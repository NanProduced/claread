function clampLimit(value) {
  const parsed = Number.parseInt(String(value ?? "5"), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return 5;
  return Math.min(parsed, 20);
}

function normalizeRows(result) {
  if (Array.isArray(result)) return result;
  if (Array.isArray(result?.rows)) return result.rows;
  if (Array.isArray(result?.[0])) return result[0];
  return [];
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

export default (router, { database }) => {
  router.get("/recent-failures", async (req, res, next) => {
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
      return;
    }

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
};
