from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

from claread_eval.schemas.rubric import RubricCaseInput, RubricSpec
from claread_eval.schemas.run import EvalCaseArtifact
from claread_eval.writer.sanitizer import sanitized_payload


class JudgePacketWriteError(Exception):
    pass


def build_rubric_case_input(
    *,
    rubric: RubricSpec,
    artifact: EvalCaseArtifact,
    source_text_char_limit: int = 1800,
    output_item_limit: int = 12,
) -> RubricCaseInput:
    input_snapshot = artifact.input_snapshot or {}
    source_text = str(input_snapshot.get("text") or "")
    return RubricCaseInput(
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        case_id=artifact.case_id,
        run_id=artifact.run_id,
        prompt_identity=artifact.prompt_identity.model_dump(mode="json"),
        model_identity=artifact.model_identity.model_dump(mode="json"),
        reading_goal=input_snapshot.get("reading_goal"),
        reading_variant=input_snapshot.get("reading_variant"),
        source_text_excerpt=_truncate(source_text, source_text_char_limit),
        output_excerpt=_output_excerpt(artifact, item_limit=output_item_limit),
        criteria=rubric.criteria,
    )


def build_run_rubric_inputs(
    *,
    rubric: RubricSpec,
    run_dir: str | Path,
    source_text_char_limit: int = 1800,
    output_item_limit: int = 12,
) -> list[RubricCaseInput]:
    cases_dir = Path(run_dir) / "cases"
    if not cases_dir.is_dir():
        raise JudgePacketWriteError(f"Run cases directory not found: {cases_dir}")
    packets: list[RubricCaseInput] = []
    for case_path in sorted(cases_dir.glob("*.json")):
        artifact = EvalCaseArtifact.model_validate(orjson.loads(case_path.read_bytes()))
        packets.append(
            build_rubric_case_input(
                rubric=rubric,
                artifact=artifact,
                source_text_char_limit=source_text_char_limit,
                output_item_limit=output_item_limit,
            )
        )
    return packets


def write_run_rubric_inputs(
    *,
    rubric: RubricSpec,
    run_dir: str | Path,
) -> tuple[Path, Path]:
    run_path = Path(run_dir)
    packets = build_run_rubric_inputs(rubric=rubric, run_dir=run_path)
    output_dir = run_path / "judge-inputs" / rubric.id
    if output_dir.exists():
        raise JudgePacketWriteError(f"Judge input directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)

    for packet in packets:
        payload = sanitized_payload(packet.model_dump(mode="json"))
        (output_dir / f"{packet.case_id}.json").write_bytes(
            orjson.dumps(payload, option=orjson.OPT_INDENT_2)
        )

    index_path = output_dir / "index.md"
    index_path.write_text(_render_index_md(rubric, packets), encoding="utf-8")
    return output_dir, index_path


def _output_excerpt(artifact: EvalCaseArtifact, *, item_limit: int) -> dict[str, Any]:
    return {
        "user_facing_state": artifact.user_facing_state,
        "translations": artifact.translations[:item_limit],
        "sentence_entries": artifact.sentence_entries[:item_limit],
        "inline_marks": artifact.inline_marks[:item_limit],
        "warnings": [warning.model_dump(mode="json") for warning in artifact.warnings[:item_limit]],
        "drop_log": [entry.model_dump(mode="json") for entry in artifact.drop_log[:item_limit]],
    }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _render_index_md(rubric: RubricSpec, packets: list[RubricCaseInput]) -> str:
    lines = [
        f"# Judge Inputs: {rubric.id}",
        "",
        f"- Rubric version: `{rubric.version}`",
        f"- Target: `{rubric.target}`",
        f"- Cases: {len(packets)}",
        "",
        "| Case ID | Run ID | Reading Goal | Variant | Prompt Variant |",
        "|---------|--------|--------------|---------|----------------|",
    ]
    for packet in packets:
        prompt_variant = packet.prompt_identity.get("prompt_variant_id") or ""
        lines.append(
            f"| `{packet.case_id}` | `{packet.run_id}` | "
            f"{packet.reading_goal or ''} | {packet.reading_variant or ''} | "
            f"{prompt_variant} |"
        )
    lines.append("")
    return "\n".join(lines)
