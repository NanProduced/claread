from __future__ import annotations

from claread_eval.graders.base import BaseGrader
from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.grader import GraderResult, GraderSeverity, GraderVerdict
from claread_eval.schemas.run import EvalCaseArtifact


class TranslationCoverageGrader(BaseGrader):
    @property
    def name(self) -> str:
        return "translation_coverage"

    def grade(self, case: EvalCase, artifact: EvalCaseArtifact) -> GraderResult:
        if artifact.error:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.SKIP,
                severity=GraderSeverity.INFO,
                metric="translation_coverage",
                value=None,
                expected=None,
                evidence="Skipped due to adapter error",
            )

        output = artifact.output
        article = output.get("article", {})
        sentences = article.get("sentences", [])
        total_sentences = len(sentences) if sentences else len(artifact.translations)

        if total_sentences == 0:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.SKIP,
                severity=GraderSeverity.INFO,
                metric="translation_coverage",
                value=None,
                expected=None,
                evidence="No sentences found to measure coverage",
            )

        translated_count = len(artifact.translations)
        coverage = translated_count / total_sentences

        threshold = case.expected.min_translation_coverage
        if "min_translation_coverage" not in case.expected.model_fields_set:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.PASS,
                severity=GraderSeverity.INFO,
                metric="translation_coverage",
                value=round(coverage, 4),
                expected=None,
                evidence=(
                    f"{translated_count}/{total_sentences} sentences translated "
                    f"({coverage:.1%}); no threshold configured"
                ),
            )
        passed = coverage >= threshold

        return GraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict=GraderVerdict.PASS if passed else GraderVerdict.FAIL,
            severity=GraderSeverity.HARD,
            metric="translation_coverage",
            value=round(coverage, 4),
            expected=f">={threshold}",
            evidence=(
                f"{translated_count}/{total_sentences} sentences translated "
                f"({coverage:.1%}), threshold={threshold:.1%}"
            ),
        )
