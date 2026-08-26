"""
ATP Framework Adapter Base Interface — ATP v0.2.0

Defines the contract for framework-specific ATP adapters.
This enables consistent integration patterns across different agent frameworks.
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

# Avoid circular imports
if TYPE_CHECKING:
    from atp.core import ATPConfig

from atp.core import (
    ATPClient,
    ATPMetadata,
    ATPTrustLevel,
    Capability,
    Cryptography,
    InMemoryProofStore,
    ProofData,
    ProofDataTask,
    ProofSketch,
    StoredProof,
    SystemRegistration,
    hash_with_prefix,
)
from atp.core.exceptions import ATPProofNotFoundError
from atp.core.hashing import compute_dependencies_hash
from atp.core.models import DEFAULT_CLASSIFICATION


class FrameworkAdapter(ABC):
    """
    Abstract base class for ATP framework adapters.

    Framework adapters wrap the core ATP client to provide framework-specific
    integration. Each adapter translates between the framework's conventions
    and ATP's core protocol.

    Design Philosophy:
    - Core ATP logic stays in atp.core (framework-agnostic)
    - Adapters handle framework-specific glue code
    - Developers can create custom adapters for any framework

    Example Frameworks:
    - A2A (Agent-to-Agent protocol)
    - LangChain (LLM application framework)
    - AutoGPT (autonomous agent framework)
    - Custom frameworks

    Example Implementation (ATP v0.2.0)::

        from atp.adapters.base import FrameworkAdapter
        from atp.core import ATPClient, ATPConfig, InMemoryProofStore

        class MyFrameworkAdapter(FrameworkAdapter):
            def __init__(self, config: ATPConfig):
                self.config = config
                self.proof_store = InMemoryProofStore(config)
                self.client = ATPClient(config)
                self.system_id = None

            async def register(self, agent_config):
                registration = await self.client.register_system(
                    name=agent_config.name,
                    system_type="agent"
                )
                self.system_id = registration.system_id
                return registration

            async def on_task_complete(self, task_id, query, response):
                # Build proof sketch (hashes only — never send content to Exchange)
                invocation = {"method": "query", "input": {"query": query}}
                outcome = {"response": {"text": response}, "status": "success"}
                proof_sketch = ProofSketch(
                    atp_metadata=ATPMetadata(...),
                    cryptography=Cryptography(
                        invocation_hash=hash_with_prefix(invocation),
                        outcome_hash=hash_with_prefix(outcome),
                    ),
                    timestamp=datetime.now(UTC),
                )

                # Store full proof locally for challenge-response
                proof_data = ProofData(
                    task=ProofDataTask(
                        system_uri=...,
                        task_id=task_id,
                        invocation=invocation,
                        invocation_hash=proof_sketch.cryptography.invocation_hash,
                        outcome=outcome,
                        outcome_hash=proof_sketch.cryptography.outcome_hash,
                        timestamp=proof_sketch.timestamp,
                    )
                )
                await self.proof_store.save(StoredProof(..., proof_data=proof_data))

                # Commit proof sketch to exchange
                await self.client.create_commit(
                    system_id=self.system_id,
                    task_id=task_id,
                    proof_sketch=proof_sketch,
                )

            async def on_challenge(self, task_id):
                stored_proof = await self.proof_store.get(task_id)
                if not stored_proof:
                    raise ProofNotFoundError(f"No proof for task {task_id}")
                return stored_proof
    """

    @abstractmethod
    async def register(self, **kwargs) -> Any:
        """
        Register the agent/system with ATP Exchange.

        Framework adapters should call atp.core.ATPClient.register_system()
        and handle framework-specific registration logic.
        """
        pass

    @abstractmethod
    async def on_task_complete(self, task_id: str, query: str, response: str, **kwargs) -> Any:
        """
        Handle task completion with ATP proof creation and commitment.

        Framework adapters should:
        1. Build the invocation dict from query/task input
        2. Build the outcome dict from response/task output
        3. Create a ProofSketch using hash_with_prefix() for both hashes
        4. Store the full ProofData locally (with content, for challenge-response)
        5. Commit the ProofSketch (hashes only) to the Exchange
        """
        pass

    @abstractmethod
    async def on_challenge(self, task_id: str, **kwargs) -> Any:
        """
        Handle ATP challenge request for proof.

        Framework adapters should retrieve the full proof from local storage
        and return it in a format appropriate for the framework.
        """
        pass


class MinimalAdapter(FrameworkAdapter):
    """
    Minimal reference implementation of FrameworkAdapter — ATP v0.2.0.

    This shows the simplest possible adapter implementation and can be used
    as a starting point for custom adapters.

    Usage::

        from atp.adapters.base import MinimalAdapter
        from atp.core import ATPConfig

        config = ATPConfig(api_key="...", exchange_url="...")
        adapter = MinimalAdapter(config)

        # Register
        await adapter.register(name="my-agent")

        # On task completion
        await adapter.on_task_complete(
            task_id="abc-123",
            query="What is 2+2?",
            response="4"
        )

        # On challenge
        proof = await adapter.on_challenge(task_id="abc-123")
    """

    def __init__(self, config: "ATPConfig"):
        """
        Initialize minimal adapter.

        Args:
            config: ATP configuration
        """
        self.config = config
        self.proof_store = InMemoryProofStore(config)
        self.client = ATPClient(config)
        self.system_id: str | None = None
        self._system_uri: str | None = None

    async def register(
        self,
        name: str = "minimal-agent",
        url: str | None = None,
        proof_ttl_seconds: int | None = None,
        capabilities: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Register agent with ATP Exchange.

        Args:
            name: Agent name.
            url: Publicly accessible URL of the agent (challenge endpoint). The
                Exchange HEAD-checks this and stores the verification result. Required
                for marketplace publishing.
            proof_ttl_seconds: How long the agent retains full proofs for
                challenge-response (seconds). Defaults to the Exchange default (90 days).
            capabilities: Explicit capability declarations to register. When omitted,
                ``DEFAULT_CLASSIFICATION`` is registered so the system appears in
                search results with at least a generic classification.
            **kwargs: Additional keyword args (``system_type`` accepted).
        """
        cap_list = capabilities or [DEFAULT_CLASSIFICATION.model_dump(mode="json")]
        registration: SystemRegistration = await self.client.register_system(
            name=name,
            system_type=kwargs.get("system_type", "agent"),
            capabilities=cap_list,
            url=url,
            proof_ttl_seconds=proof_ttl_seconds,
        )
        self.system_id = registration.system_id
        self._system_uri = registration.system_uri

        return {
            "system_uri": registration.system_uri,
            "system_id": registration.system_id,
            "status": registration.status,
            "capabilities_registered": registration.capabilities_registered,
            "url": registration.url,
            "url_verified": registration.url_verified,
            "proof_ttl_seconds": registration.proof_ttl_seconds,
        }

    async def on_task_complete(
        self,
        task_id: str,
        query: str,
        response: str,
        artifacts: dict[str, Any] | None = None,
        classification: Capability | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Create and commit ATP v0.2.0 proof sketch for a completed task.

        The query is wrapped in an invocation dict and the response in an outcome
        dict — both canonical structures defined by ATP v0.2.0. Only the
        cryptographic hashes are sent to the Exchange; the full content stays local.

        Args:
            task_id: Unique task identifier.
            query: The user / caller query text.
            response: The agent response text.
            artifacts: Optional extra artifacts to include in the outcome.
            classification: ATP classification for this task. When ``None``,
                ``ATPClient.create_commit()`` automatically substitutes
                ``DEFAULT_CLASSIFICATION`` (question-answering).
                Provide a real ``Capability`` for accurate task-type tracking::

                    from atp.core.models import Capability, Ontology
                    await adapter.on_task_complete(
                        task_id=task_id,
                        query=q,
                        response=r,
                        classification=Capability(
                            description="Patient diagnosis",
                            ontology=Ontology(
                                ontology_uri="https://agenttrustprotocol.org/ontology/v0.2.0",
                                occupation="29-1141.00",
                                capabilities=["document-question-answering"],
                            ),
                        ),
                    )
        """
        if not self.system_id:
            raise RuntimeError("Adapter not registered. Call register() first.")

        now = datetime.now(UTC)
        exchange_url = self.config.exchange_url or "http://localhost:8080"
        system_uri = self._system_uri or f"{exchange_url}/systems/{self.system_id}"

        # --- Build canonical invocation and outcome dicts ---
        invocation: dict[str, Any] = {
            "method": "query",
            "trigger": {"type": "user_message"},
            "input": {"query": query},
        }
        outcome: dict[str, Any] = {
            "response": {"text": response},
            "status": "success",
            "actions": [],
            "error": None,
        }
        if artifacts is not None:
            outcome["artifacts"] = artifacts

        # --- Compute hashes (sha256: prefix required by v0.2.0) ---
        invocation_hash = hash_with_prefix(invocation)
        outcome_hash = hash_with_prefix(outcome)
        dependencies_hash = compute_dependencies_hash([])

        resolved_classification = classification or DEFAULT_CLASSIFICATION
        system_type = self.client._system_types.get(self.system_id, "agent")

        # --- Build proof sketch (committed to Exchange — no content) ---
        proof_sketch = ProofSketch(
            atp_metadata=ATPMetadata(
                spec_version="0.2.0",
                spec_uri="https://agenttrustprotocol.org/spec/v0.2",
                system_uri=system_uri,
                system_type=system_type,
                task_id=task_id,
                classification=resolved_classification,
            ),
            cryptography=Cryptography(
                algorithm="SHA-256",
                invocation_hash=invocation_hash,
                outcome_hash=outcome_hash,
                dependencies_hash=dependencies_hash,
            ),
            timestamp=now,
        )

        # --- Build full proof data (stored locally for challenge-response) ---
        proof_data = ProofData(
            task=ProofDataTask(
                system_uri=system_uri,
                system_type=system_type,
                task_id=task_id,
                invocation=invocation,
                invocation_hash=invocation_hash,
                outcome=outcome,
                outcome_hash=outcome_hash,
                timestamp=now,
                trust_level=ATPTrustLevel.UNVERIFIED,
            ),
        )

        stored_proof = StoredProof(
            proof_data=proof_data,
            created_at=now,
            expires_at=now + timedelta(seconds=self.config.proof_ttl_seconds),
        )

        # Store locally (full proof with content)
        await self.proof_store.save(stored_proof)

        # Commit proof sketch to exchange (hashes only)
        commit = await self.client.create_commit(
            system_id=self.system_id,
            task_id=task_id,
            proof_sketch=proof_sketch,
        )

        return {
            "task_id": task_id,
            "commit_id": commit.commit_id,
            "status": commit.status,
        }

    async def on_challenge(self, task_id: str, **kwargs) -> dict[str, Any]:
        """Retrieve full proof from local storage for challenge-response."""
        stored_proof = await self.proof_store.get(task_id)

        if not stored_proof:
            raise ATPProofNotFoundError(f"No proof found for task {task_id}")

        return stored_proof.model_dump(mode="json")


__all__ = [
    "FrameworkAdapter",
    "MinimalAdapter",
]
