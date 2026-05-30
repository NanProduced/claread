from __future__ import annotations

from claread_eval.graders.base import BaseGrader
from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.grader import GraderResult, GraderSeverity, GraderVerdict
from claread_eval.schemas.run import EvalCaseArtifact


class WarningDropSummaryGrader(BaseGrader):
    @property
    def name(self) -> str:
        return "warning_drop_summary"

    def grade(self, case: EvalCase, artifact: EvalCaseArtifact) -> GraderResult:
        if artifact.error:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.SKIP,
                severity=GraderSeverity.INFO,
                metric="warning_drop_summary",
                value=None,
                expected=None,
                evidence="Skipped due to adapter error",
            )

        warning_count = len(artifact.warnings)
        if artifact.drop_log_summary and "total_drop_count" in artifact.drop_log_summary:
            drop_count = int(artifact.drop_log_summary.get("total_drop_count") or 0)
        else:
            drop_count = len(artifact.drop_log)

        allowed_codes = set(case.expected.allowed_warning_codes)
        tolerated_codes = set(case.expected.tolerated_warning_codes)
        disallowed_warnings = [
            w
            for w in artifact.warnings
            if w.code not in allowed_codes and w.code not in tolerated_codes
        ]

        max_warning = case.expected.max_warning_count
        max_drop_ratio = case.expected.max_drop_ratio
        has_warning_constraints = bool(
            {
                "allowed_warning_codes",
                "max_warning_count",
                "max_drop_ratio",
            }
            & case.expected.model_fields_set
        )

        issues: list[str] = []

        if disallowed_warnings:
            codes = ", ".join(w.code for w in disallowed_warnings)
            issues.append(f"Disallowed warning codes: {codes}")

        if max_warning is not None and warning_count > max_warning:
            issues.append(f"Warning count {warning_count} exceeds max {max_warning}")

        if max_drop_ratio is not None and drop_count > 0:
            output = artifact.output
            article = output.get("article", {})
            sentences = article.get("sentences", [])
            total = len(sentences) if sentences else max(drop_count, 1)
            drop_ratio = drop_count / total
            if drop_ratio > max_drop_ratio:
                issues.append(
                    f"Drop ratio {drop_ratio:.1%} exceeds max {max_drop_ratio:.1%}"
                )

        if issues and has_warning_constraints:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.SOFT,
                metric="warning_drop_summary",
                value={"warnings": warning_count, "drops": drop_count},
                expected={
                    "allowed_codes": list(allowed_codes),
                    "tolerated_codes": list(tolerated_codes),
                    "max_warning_count": max_warning,
                    "max_drop_ratio": max_drop_ratio,
                },
                evidence="; ".join(issues),
            )

        return GraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict=GraderVerdict.PASS,
            severity=GraderSeverity.SOFT if has_warning_constraints else GraderSeverity.INFO,
            metric="warning_drop_summary",
            value={"warnings": warning_count, "drops": drop_count},
            expected={
                "allowed_codes": list(allowed_codes),
                "tolerated_codes": list(tolerated_codes),
                "max_warning_count": max_warning,
                "max_drop_ratio": max_drop_ratio,
            },
            evidence=(
                f"Warnings: {warning_count}, Drops: {drop_count}"
                + (" — within bounds" if has_warning_constraints else " — observed only")
            ),
        )
