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

export default (router, { database }) => {
  router.get("/reader-orch/trace/:trace_id", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const result = await database.raw(
        `
          SELECT
            id, trace_id, parent_span_id, span_kind, reader_run_id, reader_job_id,
            reading_record_id, worker_type, model_route, model_name, model_provider,
            capability_code, ai_usage_event_id, attempt_number, retry_class,
            status, failure_class, failure_code, claim_wait_ms,
            started_at, ended_at, duration_ms,
            input_tokens, output_tokens, total_tokens,
            cache_read_tokens, cache_write_tokens, langsmith_run_id,
            metadata_json
          FROM reader_runtime_spans
          WHERE trace_id = ?
          ORDER BY started_at ASC
        `,
        [req.params.trace_id],
      );

      res.json({
        data: normalizeRows(result),
      });
    } catch (error) {
      next(error);
    }
  });

  router.get("/reader-orch/run/:run_id", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    try {
      const result = await database.raw(
        `
          SELECT
            s.id, s.trace_id, s.span_kind, s.worker_type, s.status,
            s.duration_ms, s.claim_wait_ms, s.attempt_number, s.retry_class,
            s.model_route, s.model_name, s.model_provider, s.capability_code,
            s.input_tokens, s.output_tokens, s.total_tokens,
            s.cache_read_tokens, s.cache_write_tokens,
            s.langsmith_run_id, s.started_at, s.ended_at,
            s.failure_class, s.failure_code,
            e.billed_points, e.billing_policy_version
          FROM reader_runtime_spans s
          LEFT JOIN ai_usage_events e ON e.id = s.ai_usage_event_id
          WHERE s.reader_run_id = ?
          ORDER BY s.started_at ASC
        `,
        [req.params.run_id],
      );

      res.json({
        data: normalizeRows(result),
      });
    } catch (error) {
      next(error);
    }
  });

  router.get("/reader-orch/record/:record_id/summary", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    const days = clampDays(req.query?.days);

    try {
      const result = await database.raw(
        `
          SELECT
            worker_type,
            COUNT(*) AS span_count,
            COUNT(*) FILTER (WHERE status = 'succeeded') AS succeeded,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COUNT(*) FILTER (WHERE status = 'superseded') AS superseded,
            AVG(duration_ms) AS avg_duration_ms,
            MAX(duration_ms) AS max_duration_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_duration_ms,
            SUM(input_tokens) AS total_input_tokens,
            SUM(output_tokens) AS total_output_tokens,
            SUM(total_tokens) AS total_tokens,
            SUM(cache_read_tokens) AS total_cache_read_tokens,
            AVG(claim_wait_ms) AS avg_claim_wait_ms,
            MAX(claim_wait_ms) AS max_claim_wait_ms
          FROM reader_runtime_spans
          WHERE reading_record_id = ?
            AND started_at >= NOW() - (? || ' days')::INTERVAL
          GROUP BY worker_type
          ORDER BY worker_type
        `,
        [req.params.record_id, String(days)],
      );

      res.json({
        data: normalizeRows(result),
      });
    } catch (error) {
      next(error);
    }
  });

  router.get("/reader-orch/dashboard", async (req, res, next) => {
    if (!buildAuthGuard(req, res)) return;

    const days = clampDays(req.query?.days);

    try {
      const result = await database.raw(
        `
          SELECT
            worker_type,
            COUNT(DISTINCT trace_id) AS trace_count,
            COUNT(DISTINCT reader_run_id) AS run_count,
            COUNT(*) AS span_count,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
            AVG(duration_ms) AS avg_duration_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_duration_ms,
            SUM(total_tokens) AS total_tokens,
            SUM(input_tokens + output_tokens) AS total_io_tokens,
            AVG(claim_wait_ms) AS avg_claim_wait_ms,
            MAX(claim_wait_ms) AS max_claim_wait_ms
          FROM reader_runtime_spans
          WHERE started_at >= NOW() - (? || ' days')::INTERVAL
          GROUP BY worker_type
          ORDER BY worker_type
        `,
        [String(days)],
      );

      res.json({
        data: normalizeRows(result),
      });
    } catch (error) {
      next(error);
    }
  });
};
