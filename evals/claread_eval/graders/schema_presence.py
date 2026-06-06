from __future__ import annotations

from claread_eval.graders.base import BaseGrader
from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.grader import GraderResult, GraderSeverity, GraderVerdict
from claread_eval.schemas.run import EvalCaseArtifact


class SchemaPresenceGrader(BaseGrader):
    @property
    def name(self) -> str:
        return "schema_presence"

    def grade(self, case: EvalCase, artifact: EvalCaseArtifact) -> GraderResult:
        if artifact.error:
            message = artifact.error.get("message") or str(artifact.error)
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.HARD,
                metric="output_present",
                value=False,
                expected=True,
                evidence=f"Adapter returned error: {message}",
            )

        output = artifact.output
        if not output:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.HARD,
                metric="output_present",
                value=False,
                expected=True,
                evidence="Output dict is empty",
            )

        missing: list[str] = []
        required_keys = ["schema_version", "request", "article", "user_facing_state"]
        for key in required_keys:
            if key not in output:
                missing.append(key)

        if missing:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.HARD,
                metric="required_fields_present",
                value=False,
                expected=True,
                evidence=f"Missing required fields: {', '.join(missing)}",
            )

        collection_keys = ["translations", "inline_marks", "sentence_entries", "warnings"]
        malformed = [
            key
            for key in collection_keys
            if key in output and not isinstance(output[key], list)
        ]
        if malformed:
            return GraderResult(
                grader_name=self.name,
                case_id=case.id,
                verdict=GraderVerdict.FAIL,
                severity=GraderSeverity.HARD,
                metric="collection_fields_are_lists",
                value=False,
                expected=True,
                evidence=f"Malformed collection fields: {', '.join(malformed)}",
            )

        return GraderResult(
            grader_name=self.name,
            case_id=case.id,
            verdict=GraderVerdict.PASS,
            severity=GraderSeverity.HARD,
            metric="schema_presence",
            value=True,
            expected=True,
            evidence="All required fields present",
        )
