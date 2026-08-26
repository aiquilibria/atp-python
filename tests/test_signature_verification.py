"""Tests for signature verification functionality — ATP v0.2.0."""
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from atp.core.hashing import compute_proof_hash
from atp.core.models import (
    ATPMetadata,
    Commit,
    CommitStatus,
    Cryptography,
    ProofSketch,
)


def _make_proof_sketch(
    system_id: str = "sys_test",
    task_id: str = "task_test",
    timestamp: datetime | None = None,
) -> ProofSketch:
    """Build a v0.2.0 ProofSketch for tests."""
    return ProofSketch(
        atp_metadata=ATPMetadata(
            spec_version="0.2.0",
            spec_uri="https://agenttrustprotocol.org/spec/v0.2",
            system_uri=f"http://localhost:8080/systems/{system_id}",
            system_type="agent",
            task_id=task_id,
        ),
        dependencies=[],
        cryptography=Cryptography(
            algorithm="SHA-256",
            invocation_hash="sha256:query_hash_placeholder",
            outcome_hash="sha256:hash123_placeholder",
        ),
        timestamp=timestamp or datetime.now(UTC),
    )


@pytest.fixture
def mock_commit():
    """Create a mock commit for testing."""
    proof_sketch = _make_proof_sketch()
    return Commit(
        id=uuid4(),
        system_id="sys_test",
        task_id=uuid4(),
        proof_sketch=proof_sketch,
        status=CommitStatus.CONFIRMED,
        signature="test_signature",
        cataloged_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


def test_commit_has_signature_field(mock_commit):
    """Test that Commit model has signature field."""
    assert hasattr(mock_commit, "signature")
    assert mock_commit.signature == "test_signature"


def test_commit_has_cataloged_at_field(mock_commit):
    """Test that Commit model has cataloged_at field (not blocktime)."""
    assert hasattr(mock_commit, "cataloged_at")
    assert not hasattr(mock_commit, "blocktime")
    assert mock_commit.cataloged_at is not None


def test_commit_without_signature():
    """Test that signature and cataloged_at are optional (default None)."""
    commit = Commit(
        id=uuid4(),
        system_id="sys_test",
        task_id=uuid4(),
        proof_sketch=_make_proof_sketch(),
        status=CommitStatus.CONFIRMED,
        created_at=datetime.now(UTC),
    )

    assert commit.signature is None
    assert commit.cataloged_at is None


def test_proof_hash_computation():
    """Test proof sketch hashing for signature verification."""
    proof_sketch = _make_proof_sketch(
        timestamp=datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC),
    )

    # compute_proof_hash returns a raw 64-char hex digest (no prefix) —
    # the Exchange signs this raw value directly.
    proof_hash = compute_proof_hash(proof_sketch)

    assert len(proof_hash) == 64
    assert all(c in "0123456789abcdef" for c in proof_hash)


def test_signature_message_format():
    """Test signature message reconstruction."""
    commit_id = uuid4()
    task_id = uuid4()
    system_id = "sys_test"
    proof_hash = "sha256:2a5f8b9c3d4e6f7a8b9c0d1e2f3a4b5c" + "0" * 32
    cataloged_at = datetime(2026, 2, 1, 13, 15, 0, 123456, tzinfo=UTC)

    # Format: commitID|taskID|systemID|proofHash|catalogedAt
    message = f"{commit_id}|{task_id}|{system_id}|{proof_hash}|{cataloged_at.isoformat()}"

    assert str(commit_id) in message
    assert str(task_id) in message
    assert system_id in message
    assert proof_hash in message
    assert cataloged_at.isoformat() in message
    assert message.count("|") == 4  # Four delimiters


def test_verify_signature_requires_full_commit():
    """Test that signature verification requires full Commit object."""
    from atp.core.models import CommitResponse

    # CommitResponse is a lightweight receipt — no proof_sketch
    response = CommitResponse(
        commit_id="test",
        system_id="sys_test",
        task_id="task_test",
        status="confirmed",
        signature="sig123",
        cataloged_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        message="Test",
    )

    assert response.signature is not None
    assert not hasattr(response, "proof_sketch")


def test_commit_status_enum():
    """Test CommitStatus enum values."""
    assert CommitStatus.CONFIRMED == "confirmed"
    assert CommitStatus.FAILED == "failed"
    assert not hasattr(CommitStatus, "PENDING"), "PENDING was removed — commits are always confirmed immediately"


def test_commit_from_api_response():
    """Test Commit.from_api_response() parses v0.2.0 proof_sketch correctly."""
    api_response = {
        "id": str(uuid4()),
        "system_id": "sys_test",
        "task_id": str(uuid4()),
        "proof_sketch": json.dumps(
            {
                "atp_metadata": {
                    "spec_version": "0.2.0",
                    "spec_uri": "https://agenttrustprotocol.org/spec/v0.2",
                    "system_uri": "http://localhost:8080/systems/sys_test",
                    "system_type": "agent",
                    "task_id": "task_test",
                },
                "dependencies": [],
                "cryptography": {
                    "algorithm": "SHA-256",
                    "invocation_hash": "sha256:abc123",
                    "outcome_hash": "sha256:def456",
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ),
        "status": "confirmed",
        "signature": "test_sig",
        "cataloged_at": datetime.now(UTC).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
    }

    commit = Commit.from_api_response(api_response)

    assert commit.signature == "test_sig"
    assert commit.cataloged_at is not None
    assert commit.status == CommitStatus.CONFIRMED
    assert isinstance(commit.id, UUID)
    assert isinstance(commit.task_id, UUID)
    assert commit.proof_sketch.cryptography.invocation_hash == "sha256:abc123"
    assert commit.proof_sketch.atp_metadata.system_type == "agent"


def test_commit_response_structure():
    """Test CommitResponse has correct field structure."""
    from atp.core.models import CommitResponse

    response = CommitResponse(
        commit_id="test",
        system_id="sys_test",
        task_id="task_test",
        status="confirmed",
        signature="sig123",
        cataloged_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        message="Test",
    )

    assert response.signature == "sig123"
    assert response.cataloged_at is not None
    assert response.status == "confirmed"


def test_cataloged_at_not_blocktime():
    """Test that cataloged_at is used (not blocktime)."""
    commit = Commit(
        id=uuid4(),
        system_id="sys_test",
        task_id=uuid4(),
        proof_sketch=_make_proof_sketch(),
        status=CommitStatus.CONFIRMED,
        cataloged_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )

    assert hasattr(commit, "cataloged_at")
    assert not hasattr(commit, "blocktime")


def test_proof_serialization_deterministic():
    """Test that proof sketch serialization is deterministic for hashing."""
    proof_sketch = _make_proof_sketch(
        timestamp=datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC),
    )

    json1 = json.dumps(
        proof_sketch.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    )
    json2 = json.dumps(
        proof_sketch.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    )

    assert json1 == json2

    hash1 = hashlib.sha256(json1.encode()).hexdigest()
    hash2 = hashlib.sha256(json2.encode()).hexdigest()
    assert hash1 == hash2
