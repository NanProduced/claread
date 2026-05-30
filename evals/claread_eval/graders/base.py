from __future__ import annotations

from abc import ABC, abstractmethod

from claread_eval.schemas.dataset import EvalCase
from claread_eval.schemas.grader import GraderResult
from claread_eval.schemas.run import EvalCaseArtifact


class BaseGrader(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def grade(self, case: EvalCase, artifact: EvalCaseArtifact) -> GraderResult:
        ...
