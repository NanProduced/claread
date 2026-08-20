"""Daily Reader workflow harness — runs INSIDE the services/api venv.

Executes the production ``daily_reader`` LangGraph workflow for one eval
case and dumps the final state as a JSON artifact. Reuses the real
``build_daily_reader_graph`` call chain; performs NO database writes
(the workflow itself is DB-free; only pipeline.py stores rows), so the
production ``daily_readers`` table is never touched.

Invoke via ``run_daily_reader_eval.py`` (it picks the api venv python
and sets cwd=services/api). Manual usage:

    cd services/api
    .venv/Scripts/python.exe ../evals/scripts/daily_reader_workflow_harness.py <case> <out>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))  # services/api root when cwd is set


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: daily_reader_workflow_harness.py <case.json> <out.json>", file=sys.stderr)
        return 2
    case = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_path = Path(sys.argv[2])

    from app.services.daily_reader.workflow import WORKFLOW_NAME, build_daily_reader_graph

    src = case["input"]
    gold = case.get("gold", {})
    input_state = {
        "original_text": src["original_text"],
        "title": src["title"],
        "subtitle": src.get("subtitle", ""),
        "source": src.get("source", "synthetic"),
        "source_url": src.get("source_url", f"synthetic://daily-eval/{case['case_id']}"),
        "cover_image_url": None,
        "tags": src.get("tags", []),
        "difficulty": gold.get("expected_difficulty", "B2"),
        "read_time_minutes": src.get("read_time_minutes", 3),
        "pipeline_source": "evals",
        "pipeline_meta": {"eval_case_id": case["case_id"], "entrypoint": "daily_reader_eval"},
    }

    graph = build_daily_reader_graph()
    final_state = asyncio.run(
        graph.ainvoke(
            input_state,
            config={
                "run_name": WORKFLOW_NAME,
                "tags": ["daily_reader_eval"],
                "metadata": {
                    "workflow_name": WORKFLOW_NAME,
                    "source_type": "evals",
                    "eval_case_id": case["case_id"],
                },
            },
        )
    )

    artifact = {
        "case_id": case["case_id"],
        "title": src["title"],
        "difficulty": input_state["difficulty"],
        "original_text": src["original_text"],
        "body_json": final_state.get("body_json", {}),
        "highlights_json": final_state.get("highlights_json", []),
        "paragraph_notes_json": final_state.get("paragraph_notes_json", {}),
        "takeaways_json": final_state.get("takeaways_json", {}),
        "review_result": final_state.get("review_result"),
        "abort": bool(final_state.get("abort", False)),
        "usage_summary": final_state.get("usage_summary"),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"artifact written: {out_path} (abort={artifact['abort']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
