from claread_eval.judge_bridge.store import (
    AsyncpgJudgeRunRequestStore,
    InMemoryJudgeRunRequestStore,
    JudgeRunRequest,
)


def __getattr__(name: str):
    if name == "JudgeWorker":
        from claread_eval.judge_bridge.worker import JudgeWorker

        return JudgeWorker
    if name == "main":
        from claread_eval.judge_bridge.worker import main

        return main
    raise AttributeError(name)

__all__ = [
    "AsyncpgJudgeRunRequestStore",
    "InMemoryJudgeRunRequestStore",
    "JudgeRunRequest",
    "JudgeWorker",
    "main",
]
