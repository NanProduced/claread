"""PostgreSQL session advisory-lock helper (Wave 9, hardened 9.1).

Minimal shared helper so the Article RAG index writer and the vector-GC
service can serialize vector mutations per ``stable_document_id`` (and
per GC intent) without a second job runtime or a distributed lock
service.

Contract
--------

* Keys are deterministic signed bigint derived from a namespace + UUID
  via stdlib hashing only (stable across processes/restarts).  The
  64-bit key space means two distinct inputs CAN theoretically collide;
  the only consequence is extra serialization between two lock users.
* ``SessionAdvisoryLock`` is bound to ONE checked-out connection.
  ``pg_advisory_lock`` / ``pg_try_advisory_lock`` / ``pg_advisory_unlock``
  are session-scoped: PostgreSQL auto-releases them when the connection
  exits abnormally, so a crashed holder never deadlocks the world.
* Callers MUST ``unlock()`` in ``finally``.
* Session-lock lifecycle vs. transactions:
  - SHORT validation / event transactions on the lock connection ARE
    allowed while the lock is held (the writer's fence re-check, the
    GC's quiescence check and outcome-event writes all do this).
  - No database transaction may span external vector I/O: the lock
    connection must be transaction-free while the deleter / writer
    performs vector calls, then the lock is released in ``finally``.
* The lock connection is a single checked-out pool connection; callers
  MUST NOT nested-acquire another connection from the same pool while
  holding it (pools may be sized as low as ``max_size=1``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

import asyncpg

# Lock namespaces — the namespace participates in the key derivation.
# A theoretical int64 key collision between namespaces is possible; the
# consequence is only extra serialization, never corruption.
LOCK_NAMESPACE_VECTOR_GC_INTENT = "claread.article_rag.vector_gc.intent"
LOCK_NAMESPACE_VECTOR_MUTATION = "claread.article_rag.vector_mutation"


def advisory_lock_key(namespace: str, identity: UUID) -> int:
    """Deterministic signed int64 advisory-lock key for namespace + UUID."""
    digest = hashlib.sha256(
        f"{namespace}:{identity}".encode()
    ).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


@dataclass(slots=True)
class SessionAdvisoryLock:
    """A session advisory lock bound to one connection.

    ``held`` tracks ownership so ``unlock()`` is idempotent under normal
    operation and safe in ``finally`` blocks.
    """

    conn: asyncpg.Connection
    key: int
    held: bool = False

    async def acquire(self) -> None:
        """Block until the lock is acquired (or the session dies)."""
        if self.held:
            return
        await self.conn.fetchval("SELECT pg_advisory_lock($1)", self.key)
        self.held = True

    async def try_acquire(self) -> bool:
        """Try once; return True iff the lock was acquired."""
        if self.held:
            return True
        acquired = await self.conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", self.key
        )
        self.held = bool(acquired)
        return self.held

    async def unlock(self) -> bool:
        """Release the lock if held. Idempotent when not held.

        Note: the underlying ``pg_advisory_unlock`` round-trip can raise
        on connection / database failure — callers must not assume this
        method never raises, and the lock connection is auto-released by
        PostgreSQL if the session dies before ``unlock()`` succeeds.
        """
        if not self.held:
            return True
        released = await self.conn.fetchval(
            "SELECT pg_advisory_unlock($1)", self.key
        )
        self.held = False
        return bool(released)


__all__ = [
    "LOCK_NAMESPACE_VECTOR_GC_INTENT",
    "LOCK_NAMESPACE_VECTOR_MUTATION",
    "SessionAdvisoryLock",
    "advisory_lock_key",
]
