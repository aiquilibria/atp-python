"""
Tests for SQLiteProofStore — ATP v0.2.0

Mirrors test_storage.py but targets the persistent SQLite backend.
Adds a persistence-across-instances test that only the SQLite store can satisfy.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from atp.core.config import ATPConfig
from atp.core.exceptions import ATPProofExpiredError
from atp.core.models import (
    ATPTrustLevel,
    Dependency,
    ProofData,
    ProofDataTask,
    StoredProof,
)
from atp.core.storage import SQLiteProofStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proof_data_task(
    task_id: str = "task-123",
    query: str = "What is 2+2?",
    response: str = "4",
    system_uri: str = "http://localhost:8080/systems/test-system-123",
    system_type: str = "agent",
    trust_level: ATPTrustLevel = ATPTrustLevel.VERIFIED,
    timestamp: datetime | None = None,
) -> ProofDataTask:
    """Build a v0.2.0 ProofDataTask from simple query/response strings."""
    from atp.core.hashing import hash_with_prefix

    inv = {"method": "query", "trigger": {"type": "user_message"}, "input": {"query": query}}
    out = {"response": {"text": response}, "status": "success", "actions": [], "error": None}
    return ProofDataTask(
        system_uri=system_uri,
        system_type=system_type,
        task_id=task_id,
        invocation=inv,
        invocation_hash=hash_with_prefix(inv),
        outcome=out,
        outcome_hash=hash_with_prefix(out),
        timestamp=timestamp or datetime.now(UTC),
        trust_level=trust_level,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return ATPConfig(api_key="test-key", exchange_url="http://localhost:8080", proof_ttl_seconds=60)


@pytest.fixture
def db_path(tmp_path):
    """Temporary SQLite database path — cleaned up automatically by pytest."""
    return str(tmp_path / "test_proofs.db")


@pytest_asyncio.fixture
async def proof_store(config, db_path):
    """SQLiteProofStore backed by a temp file; stopped after each test."""
    store = SQLiteProofStore(config, db_path=db_path)
    yield store
    await store.stop()


@pytest.fixture
def sample_proof_data():
    return ProofData(task=_make_proof_data_task(), dependencies=[])


@pytest.fixture
def sample_stored_proof(sample_proof_data):
    now = datetime.now(UTC)
    return StoredProof(
        proof_data=sample_proof_data, created_at=now, expires_at=now + timedelta(seconds=60)
    )


# ---------------------------------------------------------------------------
# Core interface tests  (mirrors test_storage.py exactly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_proof(proof_store, sample_stored_proof):
    task_id = sample_stored_proof.proof_data.task.task_id
    await proof_store.save(sample_stored_proof)
    retrieved = await proof_store.get(task_id)

    assert retrieved is not None
    assert retrieved.proof_data.task.task_id == task_id
    assert retrieved.proof_data.task.invocation["input"]["query"] == "What is 2+2?"
    assert retrieved.proof_data.task.outcome["response"]["text"] == "4"


@pytest.mark.asyncio
async def test_get_nonexistent_proof(proof_store):
    result = await proof_store.get("nonexistent-task-id")
    assert result is None


@pytest.mark.asyncio
async def test_delete_proof(proof_store, sample_stored_proof):
    task_id = sample_stored_proof.proof_data.task.task_id
    await proof_store.save(sample_stored_proof)
    assert await proof_store.get(task_id) is not None
    await proof_store.delete(task_id)
    assert await proof_store.get(task_id) is None


@pytest.mark.asyncio
async def test_list_proofs(proof_store):
    now = datetime.now(UTC)
    for i in range(3):
        proof_data = ProofData(task=_make_proof_data_task(task_id=f"task-{i}"), dependencies=[])
        stored_proof = StoredProof(
            proof_data=proof_data, created_at=now, expires_at=now + timedelta(seconds=60)
        )
        await proof_store.save(stored_proof)

    all_task_ids = await proof_store.list()
    assert len(all_task_ids) == 3
    assert set(all_task_ids) == {"task-0", "task-1", "task-2"}


@pytest.mark.asyncio
async def test_expired_proof_not_returned(proof_store, sample_proof_data):
    now = datetime.now(UTC)
    expired_proof = StoredProof(
        proof_data=sample_proof_data,
        created_at=now - timedelta(seconds=120),
        expires_at=now - timedelta(seconds=60),
    )
    task_id = sample_proof_data.task.task_id
    await proof_store.save(expired_proof)
    with pytest.raises(ATPProofExpiredError):
        await proof_store.get(task_id)


@pytest.mark.asyncio
async def test_garbage_collection(proof_store):
    now = datetime.now(UTC)

    valid_proof = StoredProof(
        proof_data=ProofData(task=_make_proof_data_task(task_id="valid"), dependencies=[]),
        created_at=now,
        expires_at=now + timedelta(seconds=60),
    )
    expired_proof = StoredProof(
        proof_data=ProofData(task=_make_proof_data_task(task_id="expired"), dependencies=[]),
        created_at=now - timedelta(seconds=120),
        expires_at=now - timedelta(seconds=60),
    )

    await proof_store.save(valid_proof)
    await proof_store.save(expired_proof)

    assert await proof_store.get("valid") is not None
    with pytest.raises(ATPProofExpiredError):
        await proof_store.get("expired")


@pytest.mark.asyncio
async def test_proof_with_dependencies(proof_store, sample_proof_data):
    """Test storing and retrieving proof with flat Dependency references (v0.2.0)."""
    dep = Dependency(
        system_uri="http://localhost:8080/systems/dep-system",
        task_id="dep-task",
    )
    main_proof_data = ProofData(task=sample_proof_data.task, dependencies=[dep])
    now = datetime.now(UTC)
    stored_proof = StoredProof(
        proof_data=main_proof_data, created_at=now, expires_at=now + timedelta(seconds=60)
    )

    await proof_store.save(stored_proof)
    retrieved = await proof_store.get(sample_proof_data.task.task_id)

    assert retrieved is not None
    assert len(retrieved.proof_data.dependencies) == 1
    assert retrieved.proof_data.dependencies[0].task_id == "dep-task"
    assert (
        retrieved.proof_data.dependencies[0].system_uri
        == "http://localhost:8080/systems/dep-system"
    )


@pytest.mark.asyncio
async def test_concurrent_access(proof_store):
    import asyncio

    async def save_proof(task_id: str):
        now = datetime.now(UTC)
        stored_proof = StoredProof(
            proof_data=ProofData(task=_make_proof_data_task(task_id=task_id), dependencies=[]),
            created_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        await proof_store.save(stored_proof)

    tasks = [save_proof(f"task-{i}") for i in range(10)]
    await asyncio.gather(*tasks)
    all_proofs = await proof_store.list()
    assert len(all_proofs) == 10


@pytest.mark.asyncio
async def test_ttl_configuration(tmp_path):
    config = ATPConfig(
        api_key="test-key", exchange_url="http://localhost:8080", proof_ttl_seconds=3600
    )
    store = SQLiteProofStore(config, db_path=str(tmp_path / "ttl_test.db"))
    try:
        proof_data = ProofData(task=_make_proof_data_task(), dependencies=[])
        now = datetime.now(UTC)
        stored_proof = StoredProof(
            proof_data=proof_data, created_at=now, expires_at=now + timedelta(seconds=3600)
        )

        await store.save(stored_proof)
        retrieved = await store.get(proof_data.task.task_id)
        assert retrieved is not None
        time_to_expire = (retrieved.expires_at - now).total_seconds()
        assert 3595 < time_to_expire < 3605
    finally:
        await store.stop()


@pytest.mark.asyncio
async def test_proof_overwrite(proof_store, sample_proof_data):
    task_id = sample_proof_data.task.task_id
    now = datetime.now(UTC)

    proof1 = StoredProof(
        proof_data=ProofData(
            task=_make_proof_data_task(task_id=task_id, response="first"), dependencies=[]
        ),
        created_at=now,
        expires_at=now + timedelta(seconds=60),
    )
    await proof_store.save(proof1)

    proof2 = StoredProof(
        proof_data=ProofData(
            task=_make_proof_data_task(task_id=task_id, response="second"), dependencies=[]
        ),
        created_at=now,
        expires_at=now + timedelta(seconds=60),
    )
    await proof_store.save(proof2)

    retrieved = await proof_store.get(task_id)
    assert retrieved is not None
    assert retrieved.proof_data.task.outcome["response"]["text"] == "second"


# ---------------------------------------------------------------------------
# SQLite-specific tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistence_across_instances(config, tmp_path):
    """
    Core property of the SQLite store: data written by one instance is readable
    by a second instance pointing at the same database file.
    InMemoryProofStore cannot satisfy this — it loses all data on restart.
    """
    db_path = str(tmp_path / "persist.db")
    task_id = "persist-task-001"
    now = datetime.now(UTC)

    # Instance 1 — write a proof and close
    store1 = SQLiteProofStore(config, db_path=db_path)
    proof = StoredProof(
        proof_data=ProofData(task=_make_proof_data_task(task_id=task_id, response="hello"), dependencies=[]),
        created_at=now,
        expires_at=now + timedelta(seconds=3600),
    )
    await store1.save(proof)
    await store1.stop()

    # Instance 2 — open the same file, proof must still be there
    store2 = SQLiteProofStore(config, db_path=db_path)
    retrieved = await store2.get(task_id)
    await store2.stop()

    assert retrieved is not None
    assert retrieved.proof_data.task.task_id == task_id
    assert retrieved.proof_data.task.outcome["response"]["text"] == "hello"


@pytest.mark.asyncio
async def test_list_excludes_expired_rows(proof_store):
    """list() should only return task_ids whose expires_at is in the future."""
    now = datetime.now(UTC)

    valid = StoredProof(
        proof_data=ProofData(task=_make_proof_data_task(task_id="live"), dependencies=[]),
        created_at=now,
        expires_at=now + timedelta(seconds=300),
    )
    stale = StoredProof(
        proof_data=ProofData(task=_make_proof_data_task(task_id="stale"), dependencies=[]),
        created_at=now - timedelta(seconds=200),
        expires_at=now - timedelta(seconds=60),
    )
    await proof_store.save(valid)
    await proof_store.save(stale)

    ids = await proof_store.list()
    assert "live" in ids
    assert "stale" not in ids


@pytest.mark.asyncio
async def test_db_directory_created_automatically(config, tmp_path):
    """SQLiteProofStore should create missing parent directories."""
    nested_path = str(tmp_path / "deep" / "nested" / "proofs.db")
    store = SQLiteProofStore(config, db_path=nested_path)
    try:
        now = datetime.now(UTC)
        proof = StoredProof(
            proof_data=ProofData(task=_make_proof_data_task(task_id="dir-test"), dependencies=[]),
            created_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        await store.save(proof)
        retrieved = await store.get("dir-test")
        assert retrieved is not None
    finally:
        await store.stop()
