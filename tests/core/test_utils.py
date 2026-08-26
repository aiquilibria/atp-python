"""
Tests for ATP utility functions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atp.core.client import ATPClient
from atp.core.config import ATPConfig
from atp.core.storage import InMemoryProofStore
from atp.core.utils import (
    ATPResponseWrapper,
    atp_response,
    atp_task,
)


@pytest.fixture
def atp_config():
    """Create ATP config for testing."""
    return ATPConfig(
        api_key="test-key",
        exchange_url="http://test-exchange",
        proof_ttl_seconds=3600,
    )


@pytest.fixture
def proof_store(atp_config):
    """Create in-memory proof store."""
    return InMemoryProofStore(config=atp_config)


@pytest.fixture
def atp_client(atp_config):
    """Create ATP client with mocked methods."""
    client = ATPClient(config=atp_config)
    # Register a system
    client._registered_systems = {"test-agent": "test-system-id"}

    # Mock get_system to avoid network calls
    async def mock_get_system(system_id: str):
        mock_system = MagicMock()
        mock_system.system_id = system_id
        mock_system.name = "Test Agent"
        mock_system.type = "agent"
        mock_system.extensions = {}
        return mock_system

    client.get_system = mock_get_system
    return client


class TestATPTaskContext:
    """Tests for ATPTaskContext."""

    async def test_context_basic_usage(self, atp_client, proof_store):
        """Test basic context manager usage."""
        with patch.object(atp_client, "create_commit", new=AsyncMock()):
            async with atp_task(atp_client, proof_store, "test query") as task:
                assert task.query == "test query"
                assert task.atp_task_id is not None
                assert task.response is None

                task.set_response("test response")
                assert task.response == "test response"

    async def test_context_auto_commit(self, atp_client, proof_store):
        """Test automatic proof commit on context exit."""
        with patch.object(atp_client, "create_commit", new=AsyncMock()) as mock_commit:
            async with atp_task(atp_client, proof_store, "test query") as task:
                task.set_response("test response")

            # Check proof was stored
            stored = await proof_store.get(task.atp_task_id)
            assert stored is not None
            assert stored.proof_data.task.invocation["input"]["query"] == "test query"
            assert stored.proof_data.task.outcome["response"]["text"] == "test response"

            # Check commit was attempted
            assert mock_commit.called

    async def test_context_no_commit_without_response(self, atp_client, proof_store):
        """Test no commit if response not set."""
        with patch.object(atp_client, "create_commit", new=AsyncMock()) as mock_commit:
            async with atp_task(atp_client, proof_store, "test query") as _task:
                pass  # Don't set response

            # No commit should happen
            assert not mock_commit.called

    async def test_context_no_commit_on_error(self, atp_client, proof_store):
        """Test no commit if exception occurs."""
        with patch.object(atp_client, "create_commit", new=AsyncMock()) as mock_commit:
            try:
                async with atp_task(atp_client, proof_store, "test query") as task:
                    task.set_response("test response")
                    raise ValueError("Test error")
            except ValueError:
                pass

            # No commit should happen on error
            assert not mock_commit.called


class TestATPResponse:
    """Tests for atp_response decorator."""

    async def test_decorator_basic(self, atp_client, proof_store):
        """Test basic decorator usage."""

        @atp_response(atp_client, proof_store)
        async def test_func(query: str) -> str:
            return f"Response to: {query}"

        with patch.object(atp_client, "create_commit", new=AsyncMock()):
            result = await test_func(query="test question")

            assert isinstance(result, dict)
            assert "atp_task_id" in result
            assert "result" in result
            assert "atp_committed" in result
            assert result["result"] == "Response to: test question"

    async def test_decorator_without_wrapper(self, atp_client, proof_store):
        """Test decorator with response_wrapper=False."""

        @atp_response(atp_client, proof_store, response_wrapper=False)
        async def test_func(query: str) -> str:
            return f"Response to: {query}"

        with patch.object(atp_client, "create_commit", new=AsyncMock()):
            result = await test_func(query="test question")

            # Should return raw result without wrapper
            assert result == "Response to: test question"


class TestATPResponseWrapper:
    """Tests for ATPResponseWrapper class."""

    def test_wrapper_creation(self):
        """Test creating response wrapper."""
        wrapper = ATPResponseWrapper(
            atp_task_id="test-id", result="test result", atp_committed=True, extra_field="extra"
        )

        assert wrapper.atp_task_id == "test-id"
        assert wrapper.result == "test result"
        assert wrapper.atp_committed is True
        assert wrapper.extra == {"extra_field": "extra"}

    def test_wrapper_to_dict(self):
        """Test converting wrapper to dict."""
        wrapper = ATPResponseWrapper(
            atp_task_id="test-id", result="test result", atp_committed=True, custom="value"
        )

        result = wrapper.to_dict()

        assert result["atp_task_id"] == "test-id"
        assert result["result"] == "test result"
        assert result["atp_committed"] is True
        assert result["custom"] == "value"

    def test_wrapper_repr(self):
        """Test wrapper string representation."""
        wrapper = ATPResponseWrapper(atp_task_id="test-id", result="test result")

        repr_str = repr(wrapper)

        assert "ATPResponseWrapper" in repr_str
        assert "test-id" in repr_str
