# Reader Baseline Harness

Observation-only baseline harness for the Reader orchestration chain.
It does not modify business logic, does not write to the `public`
schema, and does not call a real LLM by default.

## Method

- **Fixed golden samples.** The corpus under
  `verification/golden_samples` (manifest + article text) is
  read-only fixture data with declared char / word bands; the loader
  refuses any sample that drifts outside its band.
- **Isolated PostgreSQL schema.** Every run happens inside a
  throwaway schema created by `schema_setup.isolated_schema`. The
  schema is dropped on exit unless `--keep-schema` is passed. Unsafe
  schema names (`public` and friends) are rejected before any
  database work starts.
- **Explicit executor gate.** `--executor-mode fake` requires the
  opt-in `--allow-fake-executors` flag and uses the deterministic
  dev-only executors; `--executor-mode real` uses the configured
  model profile and is never the default.
- **Structured metrics and reports.** Each sample emits
  `verification/reader_baseline/runs/<UTC-timestamp>/<sample_id>.{json,md}`
  plus a `summary.json` for the whole run. Metrics cover completion
  status and reasons, pipeline ticks / jobs, published layer counts,
  outstanding jobs, and `ai_usage_events` aggregates.
- **No real LLM by default.** The default invocation runs the fake
  executors; a real-provider run requires the explicit mode switch
  plus a configured model profile.

## Entry point

```powershell
# From the repo root.
python services/api/scripts/run_reader_baseline.py --samples list
python services/api/scripts/run_reader_baseline.py `
    --samples all --executor-mode fake --allow-fake-executors
```

Exit codes: `0` when every sample completes, `2` when at least one
sample is incomplete or raises.

## Layout

- `golden_samples.py` — corpus loaders and band validation.
- `new_chain.py` — metrics extraction from the published chain state.
- `report.py` — JSON / Markdown observation report.
- `schema_setup.py` — isolated schema lifecycle and name whitelist.
- `cli_helpers.py` — helpers shared by the CLI and the focused tests.
- `parse_eval/` — parse-stage evaluation subsystem with its own
  frozen-artifact contract.
