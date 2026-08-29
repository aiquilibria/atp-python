"""
Unit tests for ATP Client — ATP v0.2.0
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from atp.core.client import ATPClient
from atp.core.config import ATPConfig
from atp.core.exceptions import (
    ATPAuthError,
    ATPCommitError,
    ATPNetworkError,
    ATPRegistrationError,
)
from atp.core.hashing import hash_with_prefix
from atp.core.models import (
    DEFAULT_CLASSIFICATION,
    ATPMetadata,
    Capability,
    Cryptography,
    Ontology,
    ProofSketch,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def atp_config():
    """Create test ATP configuration."""
    return ATPConfig(
        api_key="test-api-key",
        exchange_url="http://test.example.com",
        enable_logging=False,
    )


@pytest.fixture
def atp_client(atp_config):
    """Create test ATP client."""
    return ATPClient(atp_config)


@pytest.fixture
def proof_sketch():
    """Build a minimal valid v0.2.0 ProofSketch for testing."""
    ts = datetime(2026, 2, 7, 10, 30, 0, tzinfo=UTC)
    invocation = {"method": "query", "trigger": {"type": "user_message"}, "input": {"q": "2+2?"}}
    outcome = {"response": {"text": "4"}, "status": "success", "actions": [], "error": None}
    return ProofSketch(
        atp_metadata=ATPMetadata(
            spec_version="0.2.0",
            spec_uri="https://agenttrustprotocol.org/spec/v0.2",
            system_uri="http://test.example.com/systems/test-system-id",
            system_type="agent",
            task_id="test-task-id",
        ),
        cryptography=Cryptography(
            algorithm="SHA-256",
            invocation_hash=hash_with_prefix(invocation),
            outcome_hash=hash_with_prefix(outcome),
        ),
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestATPClientInit:
    def test_client_initialization(self, atp_config):
        """Test client is initialized with correct config."""
        client = ATPClient(atp_config)
        assert client.config == atp_config
        assert client._http_client is None
        assert client._registered_systems == {}


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestATPClientContextManager:
    @pytest.mark.asyncio
    async def test_context_manager_creates_http_client(self, atp_client):
        async with atp_client as client:
            assert client._http_client is not None
            assert isinstance(client._http_client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_context_manager_closes_http_client(self, atp_client):
        async with atp_client:
            assert atp_client._http_client is not None
        assert atp_client._http_client is not None  # assigned but closed


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestATPClientRegistration:
    @pytest.mark.asyncio
    async def test_successful_registration(self, atp_client):
        """Test successful system registration."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "system_uri": "http://test.example.com/systems/test-system-id",
            "system_id": "test-system-id",
            "registered_at": "2026-01-19T12:00:00Z",
            "status": "active",
            "capabilities_registered": 0,
        }

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            registration = await atp_client.register_system(
                name="TestAgent", system_type="agent"
            )

            assert registration.system_uri == "http://test.example.com/systems/test-system-id"
            assert registration.system_id == "test-system-id"
            assert registration.status == "active"
            assert registration.capabilities_registered == 0
            assert atp_client._registered_systems["TestAgent"] == "test-system-id"

    @pytest.mark.asyncio
    async def test_registration_caching(self, atp_client):
        """Test that registration is cached and not repeated."""
        atp_client._registered_systems["CachedAgent"] = "cached-id"

        registration = await atp_client.register_system(name="CachedAgent")

        assert registration.system_id == "cached-id"
        assert registration.system_uri == "http://test.example.com/systems/cached-id"
        assert registration.status == "active"

    @pytest.mark.asyncio
    async def test_registration_with_capabilities(self, atp_client):
        """Test system registration with capabilities (ATP v0.2.0)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "system_uri": "http://test.example.com/systems/test-system-id",
            "system_id": "test-system-id",
            "registered_at": "2026-01-19T12:00:00Z",
            "status": "active",
            "capabilities_registered": 2,
        }

        capabilities = [
            {
                "description": "Patient diagnosis",
                "ontology": {
                    "ontology_uri": "https://agenttrustprotocol.org/ontology/v0.2.0",
                    "occupation": "29-1141.00",
                    "work_activities": ["4.A.4.a.5"],
                    "capabilities": ["document-question-answering"],
                },
            },
            {
                "description": "Image analysis",
                "ontology": {
                    "ontology_uri": "https://agenttrustprotocol.org/ontology/v0.2.0",
                    "occupation": "29-2034.00",
                    "work_activities": ["4.A.2.a.1"],
                    "capabilities": ["image-classification"],
                },
            },
        ]

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            registration = await atp_client.register_system(
                name="TestAgent", system_type="agent", capabilities=capabilities
            )

            assert registration.capabilities_registered == 2

            call_args = mock_http_client.post.call_args
            payload = call_args[1]["json"]
            assert "capabilities" in payload
            assert len(payload["capabilities"]) == 2

    @pytest.mark.asyncio
    async def test_registration_auth_error(self, atp_client):
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            with pytest.raises(ATPAuthError, match="Invalid API key"):
                await atp_client.register_system(name="TestAgent")

    @pytest.mark.asyncio
    async def test_registration_server_error(self, atp_client):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.json.return_value = {"error": "Server error"}

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            with pytest.raises(ATPRegistrationError, match="status 500"):
                await atp_client.register_system(name="TestAgent")

    @pytest.mark.asyncio
    async def test_registration_network_error(self, atp_client):
        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(
                side_effect=httpx.RequestError("Network error")
            )
            mock_get_client.return_value = mock_http_client

            with pytest.raises(ATPNetworkError, match="Network error"):
                await atp_client.register_system(name="TestAgent")


# ---------------------------------------------------------------------------
# Commit (v0.2.0)
# ---------------------------------------------------------------------------


class TestATPClientCommit:
    @pytest.mark.asyncio
    async def test_successful_commit(self, atp_client, proof_sketch):
        """Test successful commit with v0.2.0 ProofSketch."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "commit_id": "commit-123",
            "system_id": "test-system-id",
            "task_id": "test-task-id",
            "status": "confirmed",
            "signature": "5g7...",
            "cataloged_at": "2026-02-07T10:30:00Z",
            "created_at": "2026-02-07T10:30:00Z",
            "message": "Exchange commit confirmed immediately",
        }

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            commit = await atp_client.create_commit(
                system_id="test-system-id",
                task_id="test-task-id",
                proof_sketch=proof_sketch,
            )

            assert commit.commit_id == "commit-123"
            assert commit.status == "confirmed"
            assert commit.system_id == "test-system-id"
            assert commit.task_id == "test-task-id"

    @pytest.mark.asyncio
    async def test_commit_sets_authoritative_system_uri(self, atp_client, proof_sketch):
        """Client must overwrite system_uri with the authoritative Exchange-derived value."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "commit_id": "c1",
            "system_id": "test-system-id",
            "task_id": "test-task-id",
            "status": "confirmed",
            "signature": None,
            "cataloged_at": None,
            "created_at": "2026-02-07T10:30:00Z",
            "message": "ok",
        }

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            await atp_client.create_commit(
                system_id="test-system-id",
                task_id="test-task-id",
                proof_sketch=proof_sketch,
            )

            # Verify the authoritative system_uri was set on the sketch
            expected_uri = "http://test.example.com/systems/test-system-id"
            assert proof_sketch.atp_metadata.system_uri == expected_uri

            # Verify proof_hash was sent in the payload
            call_args = mock_http_client.post.call_args
            payload = call_args[1]["json"]
            assert "proof_hash" in payload
            assert len(payload["proof_hash"]) == 64  # raw hex, no prefix

    @pytest.mark.asyncio
    async def test_commit_payload_structure(self, atp_client, proof_sketch):
        """Verify the v0.2.0 commit payload shape sent to the Exchange."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "commit_id": "c2",
            "system_id": "sys",
            "task_id": "task",
            "status": "pending",
            "signature": None,
            "cataloged_at": None,
            "created_at": "2026-02-07T10:30:00Z",
            "message": "queued",
        }

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            await atp_client.create_commit(
                system_id="sys", task_id="task", proof_sketch=proof_sketch
            )

            payload = mock_http_client.post.call_args[1]["json"]
            assert payload["system_id"] == "sys"
            assert payload["task_id"] == "task"
            assert "proof" in payload        # the serialized ProofSketch
            assert "proof_hash" in payload   # the ephemeral proof hash
            assert "atp_metadata" in payload["proof"]
            assert "cryptography" in payload["proof"]
            assert "timestamp" in payload["proof"]

    @pytest.mark.asyncio
    async def test_commit_auth_error(self, atp_client, proof_sketch):
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            with pytest.raises(ATPAuthError, match="Invalid API key"):
                await atp_client.create_commit(
                    system_id="test-system-id",
                    task_id="test-task-id",
                    proof_sketch=proof_sketch,
                )

    @pytest.mark.asyncio
    async def test_commit_server_error(self, atp_client, proof_sketch):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server error"
        mock_response.json.return_value = {"error": "Server error"}

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http_client

            with pytest.raises(ATPCommitError, match="status 500"):
                await atp_client.create_commit(
                    system_id="test-system-id",
                    task_id="test-task-id",
                    proof_sketch=proof_sketch,
                )

    @pytest.mark.asyncio
    async def test_commit_network_error(self, atp_client, proof_sketch):
        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(
                side_effect=httpx.RequestError("Network error")
            )
            mock_get_client.return_value = mock_http_client

            with pytest.raises(ATPNetworkError, match="Network error"):
                await atp_client.create_commit(
                    system_id="test-system-id",
                    task_id="test-task-id",
                    proof_sketch=proof_sketch,
                )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestATPClientClose:
    @pytest.mark.asyncio
    async def test_close_method(self, atp_client):
        atp_client._http_client = AsyncMock()
        await atp_client.close()
        assert atp_client._http_client is None


# ---------------------------------------------------------------------------
# Classification (ATP v0.2.0)
# ---------------------------------------------------------------------------


class TestATPClientClassification:
    """Tests for ATP v0.2.0 task classification behavior in create_commit()."""

    def _ok_response(self):
        """Minimal 200 commit response."""
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {
            "commit_id": "c-cls",
            "system_id": "sys-id",
            "task_id": "task-id",
            "status": "confirmed",
            "signature": "sig",
            "cataloged_at": "2026-02-07T10:30:00Z",
            "created_at": "2026-02-07T10:30:00Z",
            "message": "ok",
        }
        return mock

    def _make_sketch(self, classification=None):
        ts = datetime(2026, 2, 7, 10, 30, 0, tzinfo=UTC)
        inv = {"method": "query", "trigger": {"type": "user_message"}, "input": {"q": "hi"}}
        out = {"response": {"text": "hello"}, "status": "success", "actions": [], "error": None}
        return ProofSketch(
            atp_metadata=ATPMetadata(
                spec_version="0.2.0",
                system_uri="http://test.example.com/systems/sys-id",
                system_type="agent",
                task_id="task-id",
                classification=classification,
            ),
            cryptography=Cryptography(
                invocation_hash=hash_with_prefix(inv),
                outcome_hash=hash_with_prefix(out),
            ),
            timestamp=ts,
        )

    @pytest.mark.asyncio
    async def test_default_classification_substituted_when_none(self, atp_client):
        """create_commit() must substitute DEFAULT_CLASSIFICATION when classification is None."""
        sketch = self._make_sketch(classification=None)
        assert sketch.atp_metadata.classification is None

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=self._ok_response())
            mock_get_client.return_value = mock_http

            await atp_client.create_commit(
                system_id="sys-id", task_id="task-id", proof_sketch=sketch
            )

        # After create_commit(), classification should be DEFAULT_CLASSIFICATION
        assert sketch.atp_metadata.classification is not None
        assert sketch.atp_metadata.classification == DEFAULT_CLASSIFICATION
        assert sketch.atp_metadata.classification.ontology.capabilities == ["question-answering"]

    @pytest.mark.asyncio
    async def test_explicit_classification_preserved(self, atp_client):
        """create_commit() must NOT overwrite an explicit classification."""
        custom = Capability(
            description="Medical diagnosis",
            ontology=Ontology(
                ontology_uri="https://agenttrustprotocol.org/ontology/v0.2.0",
                occupation="29-1141.00",
                capabilities=["document-question-answering"],
                work_activities=["4.A.4.a.5"],
            ),
        )
        sketch = self._make_sketch(classification=custom)

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=self._ok_response())
            mock_get_client.return_value = mock_http

            await atp_client.create_commit(
                system_id="sys-id", task_id="task-id", proof_sketch=sketch
            )

        assert sketch.atp_metadata.classification is not None
        assert sketch.atp_metadata.classification == custom
        assert sketch.atp_metadata.classification.ontology.occupation == "29-1141.00"
        assert "document-question-answering" in sketch.atp_metadata.classification.ontology.capabilities

    @pytest.mark.asyncio
    async def test_classification_included_in_proof_payload(self, atp_client):
        """Classification must be serialized into the proof field sent to the Exchange."""
        custom = Capability(
            description="Code review",
            ontology=Ontology(
                ontology_uri="https://agenttrustprotocol.org/ontology/v0.2.0",
                capabilities=["code-review"],
            ),
        )
        sketch = self._make_sketch(classification=custom)

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=self._ok_response())
            mock_get_client.return_value = mock_http

            await atp_client.create_commit(
                system_id="sys-id", task_id="task-id", proof_sketch=sketch
            )

            payload = mock_http.post.call_args[1]["json"]
            classification_data = payload["proof"]["atp_metadata"]["classification"]
            assert classification_data is not None
            assert classification_data["description"] == "Code review"
            assert "code-review" in classification_data["ontology"]["capabilities"]

    @pytest.mark.asyncio
    async def test_default_classification_in_payload_when_none_provided(self, atp_client):
        """When no classification given, DEFAULT_CLASSIFICATION appears in the payload."""
        sketch = self._make_sketch(classification=None)

        with patch.object(atp_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=self._ok_response())
            mock_get_client.return_value = mock_http

            await atp_client.create_commit(
                system_id="sys-id", task_id="task-id", proof_sketch=sketch
            )

            payload = mock_http.post.call_args[1]["json"]
            classification_data = payload["proof"]["atp_metadata"]["classification"]
            assert classification_data is not None
            assert "question-answering" in classification_data["ontology"]["capabilities"]
