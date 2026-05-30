from __future__ import annotations

from claread_eval.graders.base import BaseGrader
from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.grader import GraderResult, GraderSeverity, GraderVerdict
from claread_eval.schemas.run import EvalCaseArtifact


class StatusErrorGrader(BaseGrader):
    @property
    def name(self) -> str:
        return "status_error"

    def grade(self, case: EvalCase, artifact: EvalCaseArtifact) -> GraderResult:
        if artifact.error:
            message = artifact.error.get("message") or str(artifact.error)
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.HARD,
                metric="adapter_error",
                value=message,
                expected=None,
                evidence=f"Adapter error: {message}",
            )

        if artifact.timeout:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.HARD,
                metric="timeout",
                value=True,
                expected=False,
                evidence="Case timed out",
            )

        state = artifact.user_facing_state
        if state == "degraded_heavy":
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.HARD,
                metric="user_facing_state",
                value=state,
                expected="normal",
                evidence="Heavy degraded state",
            )

        if state == "degraded_light":
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.SOFT,
                metric="user_facing_state",
                value=state,
                expected="normal",
                evidence="Light degraded state",
            )

        return GraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict=GraderVerdict.PASS,
            severity=GraderSeverity.HARD,
            metric="user_facing_state",
            value=state,
            expected="normal",
            evidence="Normal state",
        )
