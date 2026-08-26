"""
ATP Proof Storage

Manages local storage of proof data for challenge-response system.
Follows A2A's TaskStore pattern with abstract base class and implementations.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from atp.core.config import ATPConfig
from atp.core.exceptions import ATPProofExpiredError, ATPStorageError
from atp.core.models import StoredProof

logger = logging.getLogger(__name__)


class ProofStore(ABC):
    """
    Abstract Proof Store interface.

    Defines the methods for persisting and retrieving `StoredProof` objects.
    Follows A2A's TaskStore pattern for consistency.
    """

    @abstractmethod
    async def save(self, proof: StoredProof) -> None:
        """Save or update a stored proof."""

    @abstractmethod
    async def get(self, task_id: str) -> StoredProof | None:
        """Retrieve a stored proof by task_id."""

    @abstractmethod
    async def delete(self, task_id: str) -> None:
        """Delete a stored proof by task_id."""

    @abstractmethod
    async def list(self) -> list[str]:
        """List all stored proof task_ids."""


class InMemoryProofStore(ProofStore):
    """
    In-memory implementation of ProofStore.

    Stores proofs in a dict with automatic TTL-based garbage collection.
    Similar to A2A's InMemoryTaskStore but with ATP-specific TTL management.

    Core methods (ProofStore interface):
    - save(stored_proof, context=None) - Store proof with TTL
    - get(task_id, context=None) - Retrieve proof (checks expiration)
    - delete(task_id, context=None) - Remove proof
    - list() - List all stored proof task_ids
    - garbage_collect() - Background task for cleaning expired proofs
    - stop() - Stop garbage collection task
    """

    def __init__(self, config: ATPConfig):
        """
        Initialize in-memory proof store.

        Args:
            config: ATP configuration with TTL settings
        """
        self.proofs: dict[str, StoredProof] = {}
        self.config = config
        self.lock = asyncio.Lock()
        self._gc_task: asyncio.Task | None = None

        logger.info(
            f"InMemoryProofStore initialized (TTL: {config.proof_ttl_seconds}s, "
            f"GC interval: {config.proof_cleanup_interval}s)"
        )

    def _ensure_gc_task(self) -> None:
        """Ensure garbage collection task is running."""
        if self._gc_task is None or self._gc_task.done():
            try:
                self._gc_task = asyncio.create_task(self.garbage_collect())
                logger.debug("Started garbage collection task")
            except RuntimeError:
                logger.debug("Cannot start GC task - no event loop")

    async def save(self, proof: StoredProof) -> None:
        """
        Save or update a stored proof.

        Args:
            task: StoredProof to save
            context: Optional server call context (unused)

        Raises:
            ATPStorageError: If storage operation fails
        """
        try:
            # Ensure GC task is running
            self._ensure_gc_task()

            # Extract task_id from proof_data (ATP v0.2.0 nested structure)
            task_id = proof.proof_data.task.task_id

            async with self.lock:
                self.proofs[task_id] = proof

            logger.debug(f"Saved proof for task {task_id} (expires: {proof.expires_at})")

        except Exception as e:
            logger.error(f"Failed to save proof: {e}")
            raise ATPStorageError(f"Failed to save proof: {e}") from e

    async def get(self, task_id: str) -> StoredProof | None:
        """
        Retrieve a stored proof by task_id.

        Checks TTL and automatically deletes expired proofs.

        Args:
            task_id: Task identifier
            context: Optional server call context (unused)

        Returns:
            StoredProof if found and not expired, None otherwise

        Raises:
            ATPProofExpiredError: If proof is found but expired
            ATPStorageError: If storage operation fails
        """
        try:
            async with self.lock:
                stored_proof = self.proofs.get(task_id)

            if not stored_proof:
                return None

            # Check expiration
            now = datetime.now(UTC)
            if stored_proof.expires_at < now:
                logger.warning(f"Proof for task {task_id} has expired")
                # Delete expired proof
                await self.delete(task_id)
                raise ATPProofExpiredError(
                    f"Proof for task {task_id} expired at {stored_proof.expires_at}"
                )

            logger.debug(f"Retrieved proof for task {task_id}")
            return stored_proof

        except ATPProofExpiredError:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve proof for task {task_id}: {e}")
            raise ATPStorageError(f"Failed to retrieve proof: {e}") from e

    async def delete(self, task_id: str) -> None:
        """
        Delete a stored proof by task_id.

        Args:
            task_id: Task identifier
            context: Optional server call context (unused)

        Raises:
            ATPStorageError: If storage operation fails
        """
        try:
            async with self.lock:
                self.proofs.pop(task_id, None)

            logger.debug(f"Deleted proof for task {task_id}")

        except Exception as e:
            logger.error(f"Failed to delete proof for task {task_id}: {e}")
            raise ATPStorageError(f"Failed to delete proof: {e}") from e

    async def list(self) -> list[str]:
        """
        List all stored proof task_ids.

        Returns:
            List of task_ids
        """
        async with self.lock:
            return list(self.proofs.keys())

    async def garbage_collect(self) -> None:
        """
        Background task for periodic cleanup of expired proofs.

        Runs continuously in the background, sleeping between cleanup cycles.
        """
        logger.info("Proof garbage collection task started")

        while True:
            try:
                await asyncio.sleep(self.config.proof_cleanup_interval)

                # Get all task_ids
                task_ids = await self.list()
                deleted_count = 0

                # Check each proof for expiration
                for task_id in task_ids:
                    try:
                        # Attempt to get proof (will raise if expired)
                        await self.get(task_id)
                    except ATPProofExpiredError:
                        # Already deleted by get()
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Error checking proof {task_id} during GC: {e}")

                if deleted_count > 0:
                    logger.info(f"GC: Deleted {deleted_count} expired proof(s)")

            except asyncio.CancelledError:
                logger.info("Proof garbage collection task stopped")
                break
            except Exception as e:
                logger.error(f"Error in garbage collection loop: {e}")
                # Continue running despite errors

    async def stop(self) -> None:
        """Stop the background garbage collection task."""
        if self._gc_task and not self._gc_task.done():
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass
            logger.info("Proof garbage collection task stopped")


class SQLiteProofStore(ProofStore):
    """
    Persistent SQLite implementation of ProofStore.

    Survives process restarts — a second process (or the same process after
    restart) pointing at the same ``db_path`` will find all proofs that were
    saved before shutdown.  This is the key property that ``InMemoryProofStore``
    cannot provide.

    Storage layout
    --------------
    One table ``atp_proofs`` with three columns:

    .. code-block:: sql

        task_id    TEXT PRIMARY KEY
        proof_json TEXT NOT NULL        -- StoredProof.model_dump_json()
        expires_at TEXT NOT NULL        -- ISO-8601 UTC, used for TTL queries

    The entire ``StoredProof`` (including nested ``ProofData``, invocation /
    outcome dicts, and ``Dependency`` lists) serialises round-trip via Pydantic's
    ``model_dump_json()`` / ``model_validate_json()``, so no extra column
    mapping is required.

    Concurrency
    -----------
    ``aiosqlite`` runs the connection in a dedicated background thread and
    serialises all operations through it, so multiple coroutines calling
    ``save`` / ``get`` concurrently are safe without an extra asyncio lock.
    WAL mode is enabled on first open for better read/write concurrency.

    Args:
        config: ATP configuration (``proof_ttl_seconds``, ``proof_cleanup_interval``,
                ``proof_db_path`` used as default ``db_path``).
        db_path: Filesystem path to the SQLite database file.  Overrides
                 ``config.proof_db_path`` when supplied.  Parent directories
                 are created automatically.  ``~`` is expanded.
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS atp_proofs (
            task_id    TEXT PRIMARY KEY,
            proof_json TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """
    _CREATE_INDEX = """
        CREATE INDEX IF NOT EXISTS idx_atp_proofs_expires_at
        ON atp_proofs (expires_at)
    """

    def __init__(self, config: ATPConfig, db_path: str | None = None):
        self.config = config
        # Expand ~ and resolve to absolute path
        raw_path = db_path or config.proof_db_path
        self._db_path = Path(raw_path).expanduser().resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._gc_task: asyncio.Task | None = None

        # Schema initialisation is done exactly once via _ensure_schema().
        # Protecting it with an asyncio Lock prevents all 10 concurrent callers
        # from trying to run PRAGMA + CREATE TABLE at the same time (which
        # causes "database is locked" even in WAL mode).
        self._schema_lock: asyncio.Lock = asyncio.Lock()
        self._schema_ready: bool = False

        logger.info(
            f"SQLiteProofStore initialised (db: {self._db_path}, "
            f"TTL: {config.proof_ttl_seconds}s, "
            f"GC interval: {config.proof_cleanup_interval}s)"
        )
        # GC task is started lazily on the first save() call via _ensure_gc().
        # Eager startup in __init__ risks creating a task in a different event loop
        # than the one the tests (or the application) actually use.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_schema(self) -> None:
        """
        Create the table and index exactly once, serialised by an asyncio Lock.

        Running PRAGMA + CREATE TABLE concurrently from multiple coroutines
        triggers "database is locked" even in WAL mode because all of them try
        to acquire an exclusive write-lock at the same instant.  Serialising
        through this coroutine means only the first caller does real work; all
        subsequent callers return immediately once _schema_ready is True.
        """
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:  # double-checked after acquiring the lock
                return
            conn = await aiosqlite.connect(str(self._db_path), timeout=30)
            try:
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute(self._CREATE_TABLE)
                await conn.execute(self._CREATE_INDEX)
                await conn.commit()
            finally:
                await conn.close()
            self._schema_ready = True

    async def _open(self) -> aiosqlite.Connection:
        """Open a connection (schema initialisation is handled separately)."""
        await self._ensure_schema()
        conn = await aiosqlite.connect(str(self._db_path), timeout=30)
        conn.row_factory = aiosqlite.Row
        return conn

    def _ensure_gc(self) -> None:
        """Start the background GC task if it isn't running."""
        if self._gc_task is None or self._gc_task.done():
            try:
                self._gc_task = asyncio.create_task(self._gc_loop())
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # ProofStore interface
    # ------------------------------------------------------------------

    async def save(self, proof: StoredProof) -> None:
        """
        Persist a proof (INSERT OR REPLACE).

        Args:
            proof: The ``StoredProof`` to store.

        Raises:
            ATPStorageError: On any database error.
        """
        self._ensure_gc()
        task_id = proof.proof_data.task.task_id
        proof_json = proof.model_dump_json()
        expires_at = proof.expires_at.isoformat()
        conn = await self._open()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO atp_proofs (task_id, proof_json, expires_at) "
                "VALUES (?, ?, ?)",
                (task_id, proof_json, expires_at),
            )
            await conn.commit()
            logger.debug(f"SQLiteProofStore: saved proof for task {task_id}")
        except Exception as exc:
            logger.error(f"SQLiteProofStore: failed to save proof for task {task_id}: {exc}")
            raise ATPStorageError(f"Failed to save proof: {exc}") from exc
        finally:
            await conn.close()

    async def get(self, task_id: str) -> StoredProof | None:
        """
        Retrieve a proof by task_id.

        Expired proofs are deleted and ``ATPProofExpiredError`` is raised.

        Returns:
            The ``StoredProof`` if found and not expired; ``None`` if not found.

        Raises:
            ATPProofExpiredError: If the proof exists but has passed its TTL.
            ATPStorageError: On any database error.
        """
        conn = await self._open()
        try:
            async with conn.execute(
                "SELECT proof_json, expires_at FROM atp_proofs WHERE task_id = ?",
                (task_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                return None

            expires_at = datetime.fromisoformat(row["expires_at"])
            now = datetime.now(UTC)
            # Ensure both are timezone-aware for comparison
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)

            if expires_at < now:
                logger.warning(f"SQLiteProofStore: proof for task {task_id} has expired")
                await self.delete(task_id)
                raise ATPProofExpiredError(f"Proof for task {task_id} expired at {expires_at}")

            proof = StoredProof.model_validate_json(row["proof_json"])
            logger.debug(f"SQLiteProofStore: retrieved proof for task {task_id}")
            return proof

        except (ATPProofExpiredError, ATPStorageError):
            raise
        except Exception as exc:
            logger.error(f"SQLiteProofStore: failed to get proof for task {task_id}: {exc}")
            raise ATPStorageError(f"Failed to retrieve proof: {exc}") from exc
        finally:
            await conn.close()

    async def delete(self, task_id: str) -> None:
        """
        Delete a proof by task_id (no-op if not found).

        Raises:
            ATPStorageError: On any database error.
        """
        conn = await self._open()
        try:
            await conn.execute("DELETE FROM atp_proofs WHERE task_id = ?", (task_id,))
            await conn.commit()
            logger.debug(f"SQLiteProofStore: deleted proof for task {task_id}")
        except Exception as exc:
            logger.error(f"SQLiteProofStore: failed to delete proof for task {task_id}: {exc}")
            raise ATPStorageError(f"Failed to delete proof: {exc}") from exc
        finally:
            await conn.close()

    async def list(self) -> list[str]:
        """
        Return task_ids for all non-expired proofs.

        Expired rows are NOT deleted here (that is the GC loop's job); they are
        simply excluded from the result so callers never see stale ids.
        """
        now = datetime.now(UTC).isoformat()
        conn = await self._open()
        try:
            async with conn.execute(
                "SELECT task_id FROM atp_proofs WHERE expires_at > ?", (now,)
            ) as cursor:
                rows = await cursor.fetchall()
            return [row["task_id"] for row in rows]
        except Exception as exc:
            logger.error(f"SQLiteProofStore: failed to list proofs: {exc}")
            raise ATPStorageError(f"Failed to list proofs: {exc}") from exc
        finally:
            await conn.close()

    # ------------------------------------------------------------------
    # Garbage collection
    # ------------------------------------------------------------------

    async def _gc_loop(self) -> None:
        """Background loop: periodically DELETE expired rows from the DB."""
        logger.info("SQLiteProofStore: GC task started")
        while True:
            try:
                await asyncio.sleep(self.config.proof_cleanup_interval)
                now = datetime.now(UTC).isoformat()
                conn = await self._open()
                try:
                    cursor = await conn.execute(
                        "DELETE FROM atp_proofs WHERE expires_at < ?", (now,)
                    )
                    deleted = cursor.rowcount
                    await conn.commit()
                finally:
                    await conn.close()
                if deleted:
                    logger.info(f"SQLiteProofStore GC: deleted {deleted} expired proof(s)")
            except asyncio.CancelledError:
                logger.info("SQLiteProofStore: GC task stopped")
                break
            except Exception as exc:
                logger.error(f"SQLiteProofStore: GC error (continuing): {exc}")

    async def stop(self) -> None:
        """Cancel the background GC task."""
        if self._gc_task and not self._gc_task.done():
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass
            logger.info("SQLiteProofStore: GC task stopped")
