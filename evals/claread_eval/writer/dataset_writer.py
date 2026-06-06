from __future__ import annotations

from pathlib import Path

import orjson

from claread_eval.schemas.dataset import EvalCase


class DatasetWriteError(Exception):
    pass


def save_case_to_dataset(
    dataset_dir: str | Path,
    case: EvalCase,
    *,
    convert_origin: bool = True,
    overwrite: bool = False,
    require_explicit_expected: bool = False,
) -> Path:
    dataset_dir = Path(dataset_dir)
    cases_dir = dataset_dir / "cases"
    if not (dataset_dir / "dataset.yaml").is_file():
        raise DatasetWriteError(f"dataset.yaml not found: {dataset_dir}")
    cases_dir.mkdir(parents=True, exist_ok=True)

    path = cases_dir / f"{case.id}.json"
    if path.exists() and not overwrite:
        raise DatasetWriteError(f"Case file already exists: {path}")

    case_to_write = (
        case.model_copy(update={"origin": "dataset"})
        if convert_origin and case.origin != "dataset"
        else case
    )
    if require_explicit_expected and case.origin != "dataset":
        warnings = expected_readiness_warnings(case_to_write)
        if warnings:
            raise DatasetWriteError(
                "Case expected fields are not ready for dataset promotion: "
                + "; ".join(warnings)
            )
    path.write_bytes(
        orjson.dumps(
            case_to_write.model_dump(mode="json", exclude_none=True),
            option=orjson.OPT_INDENT_2,
        )
    )
    return path


def expected_readiness_warnings(case: EvalCase) -> list[str]:
    warnings: list[str] = []
    if case.expected.min_translation_coverage <= 0.0:
        warnings.append("min_translation_coverage is 0.0")
    if case.expected.max_warning_count is None:
        warnings.append("max_warning_count is not set")
    if case.expected.max_drop_ratio is None:
        warnings.append("max_drop_ratio is not set")
    return warnings
