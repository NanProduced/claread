from claread_eval.runner_bridge.materialize import materialize_runner_config
from claread_eval.runner_bridge.store import (
    AsyncpgWorkflowRunRequestStore,
    InMemoryWorkflowRunRequestStore,
    WorkflowRunRequest,
)


def __getattr__(name: str):
    if name == "BridgeWorker":
        from claread_eval.runner_bridge.worker import BridgeWorker

        return BridgeWorker
    if name == "main":
        from claread_eval.runner_bridge.worker import main

        return main
    raise AttributeError(name)

__all__ = [
    "AsyncpgWorkflowRunRequestStore",
    "BridgeWorker",
    "InMemoryWorkflowRunRequestStore",
    "WorkflowRunRequest",
    "main",
    "materialize_runner_config",
]
