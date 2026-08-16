"""PostgreSQL session advisory-lock helper (Wave 9).

Minimal shared helper so the Article RAG index writer and the vector-GC
service can serialize vector mutations per ``stable_document_id`` (and
per GC intent) without a second job runtime or a distributed lock
service.

Contract
--------

* Keys are deterministic signed bigint derived from a namespace + UUID
  via stdlib hashing only (stable across processes/restarts).
* ``SessionAdvisoryLock`` is bound to ONE checked-out connection.
  ``pg_advisory_lock`` / ``pg_try_advisory_lock`` / ``pg_advisory_unlock``
  are session-scoped: PostgreSQL auto-releases them when the connection
  exits abnormally, so a crashed holder never deadlocks the world.
* Callers MUST ``unlock()`` in ``finally``.
* Session locks may be held during external vector I/O, but callers must
  NOT hold a database transaction on the same connection while the lock
  is held (the lock conn is reserved for the lock lifecycle).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

import asyncpg

# Lock namespaces — the namespace participates in the key derivation, so
# two different namespaces can never collide on the same int64 key.
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

    ``held`` tracks ownership so ``unlock()`` is idempotent and safe in
    ``finally`` blocks.
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
        """Release the lock if held. Idempotent; never raises."""
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
