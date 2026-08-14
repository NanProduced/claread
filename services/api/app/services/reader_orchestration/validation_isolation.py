"""Real-LLM validation isolation helpers (SOP).

Before a gated real-LLM diagnostic run, the harness must not share the
database queue with background enhancement workers. Concurrent claimers
mix evidence (lease_owner, correlation metadata) and invalidate acceptance.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

# Process command lines that indicate a local enhancement worker loop.
_WORKER_CMD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"run_reader_enhancement_worker", re.IGNORECASE),
    re.compile(r"ReaderEnhancementWorkerLoop", re.IGNORECASE),
    re.compile(r"reader-enhancement-worker", re.IGNORECASE),
)

# Default lease-owner prefix used by the production/local worker loop script.
DEFAULT_WORKER_LEASE_PREFIX = "reader-enhancement-worker"


@dataclass(frozen=True, slots=True)
class ExternalWorkerProcess:
    pid: int
    name: str
    cmdline: str


class ProcessInspectionUnavailable(RuntimeError):
    """Raised when worker-process isolation cannot be verified reliably."""


def _load_psutil() -> Any:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ProcessInspectionUnavailable(
            "psutil is required for gated real-LLM worker isolation; "
            "abort validation because process inspection is unavailable"
        ) from exc
    return psutil


def _cmdline_text(parts: Iterable[Any] | None) -> str:
    if not parts:
        return ""
    return " ".join(str(p) for p in parts)


def list_enhancement_worker_processes(
    *,
    exclude_pid: int | None = None,
) -> list[ExternalWorkerProcess]:
    """Return running processes that look like reader enhancement workers.

    Fail closed when process inspection is unavailable. An empty list means
    enumeration completed and no matching worker was found.
    """
    psutil = _load_psutil()

    self_pid = exclude_pid if exclude_pid is not None else os.getpid()
    found: list[ExternalWorkerProcess] = []
    try:
        processes = psutil.process_iter(attrs=["pid", "name", "cmdline"])
    except Exception as exc:
        raise ProcessInspectionUnavailable(
            "failed to enumerate processes; abort gated real-LLM validation"
        ) from exc
    for proc in processes:
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            if pid in {0, self_pid}:
                continue
            cmdline = _cmdline_text(info.get("cmdline"))
            name = str(info.get("name") or "")
            haystack = f"{name} {cmdline}"
            if any(p.search(haystack) for p in _WORKER_CMD_PATTERNS):
                found.append(
                    ExternalWorkerProcess(pid=pid, name=name, cmdline=cmdline[:500])
                )
        except (psutil.Error, TypeError, ValueError):  # type: ignore[name-defined]
            continue
    return found


def assert_no_external_enhancement_workers(
    *,
    exclude_pid: int | None = None,
) -> None:
    """Fail closed when a competing enhancement worker is running.

    Real-LLM validation SOP: if any external worker is found, abort before
    creating a record so evidence cannot mix lease owners / code versions.
    """
    workers = list_enhancement_worker_processes(exclude_pid=exclude_pid)
    if not workers:
        return
    details = "; ".join(f"pid={w.pid} name={w.name!r}" for w in workers[:5])
    raise RuntimeError(
        "External reader enhancement worker(s) detected; abort real-LLM "
        f"validation to avoid mixed evidence. {details}. "
        "Stop background workers, confirm this process loads the workspace "
        "tree, then re-run."
    )


def assert_lease_owners_belong_to_harness(
    lease_owners: Iterable[str | None],
    *,
    harness_lease_owner: str,
    forbidden_prefixes: tuple[str, ...] = (DEFAULT_WORKER_LEASE_PREFIX,),
) -> None:
    """After a run, reject evidence if any tick used a foreign lease_owner."""
    foreign: list[str] = []
    for owner in lease_owners:
        if owner is None:
            continue
        if owner == harness_lease_owner:
            continue
        if any(owner.startswith(prefix) for prefix in forbidden_prefixes):
            foreign.append(owner)
        elif owner != harness_lease_owner:
            # Any non-harness owner is foreign for exclusive validation.
            foreign.append(owner)
    if foreign:
        unique = sorted(set(foreign))
        raise RuntimeError(
            "Real-LLM validation evidence contaminated by foreign lease_owner(s): "
            f"{unique}. Harness expected {harness_lease_owner!r}. "
            "Abort acceptance; do not treat mixed results as O2-V1 pass."
        )


def workspace_code_fingerprint() -> dict[str, str]:
    """Fingerprint loaded O2 sources and their Git workspace state."""
    import app.llm.agent_runner as agent_runner
    import app.services.ai_usage.execution_diagnostics as ued

    source_paths = {
        "agent_runner": Path(agent_runner.__file__).resolve(),
        "execution_diagnostics": Path(ued.__file__).resolve(),
    }
    repo_root = next(
        (parent for parent in source_paths["agent_runner"].parents if (parent / ".git").exists()),
        None,
    )
    if repo_root is None:
        raise RuntimeError("cannot locate Git workspace root for validation fingerprint")

    def _git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    target_paths = [str(path.relative_to(repo_root)) for path in source_paths.values()]
    return {
        "python_executable": sys.executable,
        "workspace_root": str(repo_root),
        "git_head": _git("rev-parse", "HEAD"),
        "dirty_target_slice": str(bool(_git("status", "--porcelain", "--", *target_paths))).lower(),
        "agent_runner_file": str(source_paths["agent_runner"]),
        "agent_runner_sha256": sha256(source_paths["agent_runner"].read_bytes()).hexdigest(),
        "execution_diagnostics_file": str(source_paths["execution_diagnostics"]),
        "execution_diagnostics_sha256": sha256(
            source_paths["execution_diagnostics"].read_bytes()
        ).hexdigest(),
        "has_run_reader_scoped_agent": str(
            hasattr(agent_runner, "run_reader_scoped_agent")
        ).lower(),
    }


__all__ = [
    "DEFAULT_WORKER_LEASE_PREFIX",
    "ExternalWorkerProcess",
    "ProcessInspectionUnavailable",
    "assert_lease_owners_belong_to_harness",
    "assert_no_external_enhancement_workers",
    "list_enhancement_worker_processes",
    "workspace_code_fingerprint",
]
