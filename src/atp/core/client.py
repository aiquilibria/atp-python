"""
ATP Client

HTTP client for communicating with the AIquilibria backend.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from atp.core.config import ATPConfig
from atp.core.exceptions import (
    ATPAuthError,
    ATPCommitError,
    ATPNetworkError,
    ATPQueryError,
    ATPRegistrationError,
    ATPVerificationError,
)
from atp.core.hashing import compute_proof_hash, hash_with_prefix
from atp.core.models import (
    DEFAULT_CLASSIFICATION,
    AssessmentDimension,
    AssessmentRecord,
    AssessmentsResponse,
    Commit,
    CommitResponse,
    CommitStatus,
    IntegrityAssessment,
    ProofSketch,
    StoredProof,
    System,
    SystemRegistration,
)

logger = logging.getLogger(__name__)


class ATPClient:
    """
    Unified ATP client for all ATP protocol operations.

    Provides a single interface for:

    **ATP Exchange Operations (Agent-to-Exchange):**
    - System registration
    - Proof commit creation
    - Commit queries and verification
    - System information queries

    **Agent-to-Agent Operations:**
    - Challenge requests (JSON-RPC to other ATP agents)
    - Automatic proof verification against exchange

    The client handles both types of operations because challenge verification
    requires exchange access, making a unified client more practical than
    separate clients for each concern.

    Example:
        ```python
        async with ATPClient(config) as client:
            # Register with exchange
            reg = await client.register_system("my-agent")

            # Commit proof to exchange
            await client.create_commit(system_id, task_id, proof)

            # Challenge another agent (agent-to-agent + exchange verification)
            proof, verified, msg = await client.challenge(
                "http://other-agent:8100",
                "task-123"
            )
        ```
    """

    def __init__(self, config: ATPConfig):
        """
        Initialize ATP client.

        Args:
            config: ATP configuration
        """
        self.config = config
        self._http_client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = (
            None  # Track which loop owns the client
        )
        self._registered_systems: dict[str, str] = {}  # name -> system_id mapping
        # Authoritative system_uri as returned by the backend during registration.
        # The backend constructs system_uri from EXCHANGE_BASE_URL (which may differ
        # from the client's exchange_url e.g. when behind a reverse proxy).
        self._system_uris: dict[str, str] = {}  # system_id -> system_uri
        self._system_types: dict[str, str] = {}  # system_id -> type ("agent"/"construct"/…)

    async def __aenter__(self):
        """Async context manager entry."""
        self._http_client = httpx.AsyncClient(
            base_url=self.config.exchange_url,
            timeout=self.config.commit_timeout,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._http_client:
            await self._http_client.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client, creating if needed and handling event loop changes."""
        # Get current event loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # Check if we need to recreate the client for a different event loop
        if self._http_client and self._client_loop is not current_loop:
            logger.debug("Different event loop detected, recreating HTTP client...")
            # Don't try to close in different loop - just discard
            self._http_client = None
            self._client_loop = None

        if not self._http_client:
            # Ensure exchange_url is a string (should always be set in config)
            base_url = self.config.exchange_url or "http://localhost:8080"
            self._http_client = httpx.AsyncClient(
                base_url=base_url,
                timeout=self.config.commit_timeout,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            )
            self._client_loop = current_loop
        return self._http_client

    async def register_system(
        self,
        name: str,
        system_type: str = "agent",
        capabilities: list[dict] | None = None,
        extensions: dict | None = None,
        url: str | None = None,
        proof_ttl_seconds: int | None = None,
    ) -> SystemRegistration:
        """
        Register a system (agent or construct) with the ATP Exchange.

        This is idempotent - registering the same system multiple times will
        return the same system_id.

        Args:
            name: System name
            system_type: "agent", "construct", or "toolbox"
            capabilities: Optional list of capability declarations (ATP v0.2.0).
                When provided, these are stored on the system record and surfaced in
                the dashboard and marketplace. Derived from the adapter's
                ``classification`` parameter at registration time.
            extensions: Optional protocol extensions
            url: Publicly accessible URL of the system (e.g. JSON-RPC challenge
                endpoint). The Exchange performs a HEAD check and stores the result
                in ``url_verified``. Required for marketplace publishing.
            proof_ttl_seconds: How long this system retains full proofs for
                challenge-response (seconds). When ``None`` the Exchange applies
                its own default (90 days). Surfaced in the dashboard so operators
                can see the declared retention window.

        Returns:
            SystemRegistration with system_uri, url_verified, and metadata

        Raises:
            ATPRegistrationError: If registration fails
            ATPNetworkError: If network communication fails
            ATPAuthError: If API key authentication fails

        Example:
            ```python
            from atp.core.models import Capability, Ontology

            registration = await client.register_system(
                name="Medical Analysis Agent",
                system_type="agent",
                capabilities=[
                    Capability(
                        description="Patient diagnosis from symptoms and lab results",
                        ontology=Ontology(
                            ontology_uri="https://agenttrustprotocol.org/ontology/v0.2.0",
                            occupation="29-1141.00",
                            work_activities=["assisting-caring-for-others"],
                            capabilities=["document-question-answering"],
                        ),
                    ).model_dump(mode="json")
                ],
                url="https://agent.example.com/challenge",
                proof_ttl_seconds=7_776_000,  # 90 days
            )
            print(f"Registered at: {registration.system_uri}")
            print(f"URL verified:  {registration.url_verified}")
            ```
        """
        # Check if already registered in this session
        if name in self._registered_systems:
            logger.info(
                f"System '{name}' already registered with ID: {self._registered_systems[name]}"
            )
            # Return cached registration (approximation since we don't cache full details)
            system_id = self._registered_systems[name]
            return SystemRegistration(
                system_uri=f"{self.config.exchange_url}/systems/{system_id}",
                system_id=system_id,
                registered_at=datetime.now(),  # Approximation
                status="active",
                capabilities_registered=len(capabilities) if capabilities else 0,
                url=url,
            )

        client = self._get_client()

        payload: dict[str, Any] = {
            "name": name,
            "type": system_type,
        }

        # Add capabilities if provided (ATP v0.2.0)
        if capabilities:
            payload["capabilities"] = capabilities

        # Add extensions if provided
        if extensions:
            payload["extensions"] = extensions

        # Add url and proof_ttl_seconds when supplied
        if url is not None:
            payload["url"] = url
        if proof_ttl_seconds is not None:
            payload["proof_ttl_seconds"] = proof_ttl_seconds

        try:
            logger.info(f"Registering system '{name}' as {system_type}...")
            response = await client.post("/api/v1/register", json=payload)

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            # Accept both 200 OK and 201 Created
            if response.status_code not in (200, 201):
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPRegistrationError(
                    f"Registration failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            registration = SystemRegistration(
                system_uri=data["system_uri"],
                system_id=data["system_id"],
                registered_at=datetime.fromisoformat(data["registered_at"]),
                status=data["status"],
                capabilities_registered=data["capabilities_registered"],
                url=data.get("url"),
                url_verified=data.get("url_verified", False),
                proof_ttl_seconds=data.get("proof_ttl_seconds"),
            )

            # Cache the system_id, authoritative system_uri, and system type
            self._registered_systems[name] = registration.system_id
            self._system_uris[registration.system_id] = registration.system_uri
            self._system_types[registration.system_id] = system_type
            logger.info(f"System '{name}' registered: {registration.system_uri}")

            return registration

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error during registration: {e}")
        except (KeyError, ValueError) as e:
            raise ATPRegistrationError(f"Invalid response from backend: {e}")

    async def create_commit(
        self,
        system_id: str,
        task_id: str,
        proof_sketch: ProofSketch,
    ) -> CommitResponse:
        """
        Commit a proof sketch to the ATP Exchange (ATP v0.2.0).

        The Exchange stores only the proof sketch — it never receives the full
        invocation/outcome content.  Agents must store full proofs locally for
        challenge-response.

        The client automatically:
        1. Overwrites ``proof_sketch.atp_metadata.system_uri`` with the
           authoritative value ``{exchange_url}/systems/{system_id}`` so that
           the Exchange can validate the proof_hash.
        2. Computes the ephemeral ``proof_hash`` and sends it alongside the
           proof sketch so the Exchange can verify it on arrival.

        Args:
            system_id: System identifier (must match a registered system)
            task_id: Task identifier (UUID string)
            proof_sketch: Privacy-preserving proof sketch (no content)

        Returns:
            CommitResponse with commit details

        Raises:
            ATPCommitError: If commit creation fails
            ATPNetworkError: If network communication fails
            ATPAuthError: If API key authentication fails
        """
        client = self._get_client()

        # Use the authoritative system_uri returned by the backend at registration time.
        # The Exchange constructs system_uri from its EXCHANGE_BASE_URL (which may differ
        # from the client's exchange_url when behind a proxy). Using the cached value
        # ensures the proof_hash is computed with the exact same URI as the backend.
        # Fall back to constructing from exchange_url when no cached value exists.
        system_uri = self._system_uris.get(
            system_id, f"{self.config.exchange_url}/systems/{system_id}"
        )
        proof_sketch.atp_metadata.system_uri = system_uri

        # Substitute DEFAULT_CLASSIFICATION (question-answering) when caller hasn't
        # provided one. This keeps pre-classification agents Exchange-compatible after
        # v0.2.0 enforcement is enabled, while still recording their activity.
        if proof_sketch.atp_metadata.classification is None:
            proof_sketch.atp_metadata.classification = DEFAULT_CLASSIFICATION
            logger.debug(
                "No classification provided — substituting DEFAULT_CLASSIFICATION "
                "(question-answering). Set atp_metadata.classification explicitly "
                "for accurate task-type tracking."
            )

        # Compute ephemeral proof_hash — sent alongside the sketch so the Exchange
        # can validate it without storing it.
        proof_hash = compute_proof_hash(
            system_uri,
            task_id,
            proof_sketch.cryptography.invocation_hash,
            proof_sketch.cryptography.outcome_hash,
            proof_sketch.cryptography.dependencies_hash,
            proof_sketch.timestamp,
        )

        payload = {
            "system_id": system_id,
            "task_id": task_id,
            "proof": proof_sketch.model_dump(mode="json"),
            "proof_hash": proof_hash,
        }

        try:
            logger.info(f"Creating commit for task {task_id} by system {system_id}...")
            response = await client.post("/api/v1/commit", json=payload)

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            # Accept both 200 OK and 201 Created
            if response.status_code not in (200, 201):
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPCommitError(
                    f"Commit creation failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            commit = CommitResponse(
                commit_id=data["commit_id"],
                system_id=data["system_id"],
                task_id=data["task_id"],
                status=data["status"],
                signature=data.get("signature"),
                cataloged_at=datetime.fromisoformat(data["cataloged_at"])
                if data.get("cataloged_at")
                else None,
                created_at=datetime.fromisoformat(data["created_at"]),
                message=data["message"],
            )

            logger.info(f"Commit created: {commit.commit_id} (status: {commit.status})")
            return commit

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error during commit: {e}")
        except (KeyError, ValueError) as e:
            raise ATPCommitError(f"Invalid response from backend: {e}")

    async def get_commit(self, commit_id: str | UUID) -> Commit:
        """
        Get a commit by ID.

        Useful for verifying responses from other ATP-compliant agents.

        Args:
            commit_id: Commit identifier (UUID string or UUID object)

        Returns:
            Commit object with current status and full proof

        Raises:
            ATPQueryError: If commit not found or query fails
            ATPNetworkError: If network communication fails
            ATPAuthError: If API key authentication fails
        """
        client = self._get_client()
        commit_id_str = str(commit_id)

        try:
            logger.debug(f"Querying commit {commit_id_str}...")
            response = await client.get(f"/api/v1/commits/{commit_id_str}")

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            if response.status_code == 404:
                raise ATPQueryError(f"Commit {commit_id_str} not found")

            if response.status_code != 200:
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPQueryError(
                    f"Query failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            return Commit.from_api_response(data)

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error during commit query: {e}")
        except (KeyError, ValueError) as e:
            raise ATPQueryError(f"Invalid response from backend: {e}")

    async def get_system_commits(
        self,
        system_id: str,
        status: CommitStatus | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Commit]:
        """
        Get commits for a specific system.

        Args:
            system_id: System identifier
            status: Optional filter by commit status
            limit: Maximum number of results (default: 10)
            offset: Number of results to skip (default: 0)

        Returns:
            List of Commit objects

        Raises:
            ATPQueryError: If query fails
            ATPNetworkError: If network communication fails
            ATPAuthError: If API key authentication fails
        """
        client = self._get_client()

        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if status:
            params["status"] = status.value

        try:
            logger.debug(f"Querying commits for system {system_id}...")
            response = await client.get(f"/api/v1/systems/{system_id}/commits", params=params)

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            if response.status_code != 200:
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPQueryError(
                    f"Query failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            return [Commit.from_api_response(item) for item in data]

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error during commit query: {e}")
        except (KeyError, ValueError) as e:
            raise ATPQueryError(f"Invalid response from backend: {e}")

    async def get_commit_by_system_and_task(
        self,
        system_id: str,
        task_id: str | UUID,
    ) -> Commit:
        """
        Get a commit by system_id and task_id.

        This is useful for verifying responses from other ATP agents when you know
        both the system_id (from agent card) and task_id (from A2A response).

        Args:
            system_id: System identifier from ATP metadata
            task_id: Task identifier from A2A response

        Returns:
            Commit object

        Raises:
            ATPQueryError: If commit not found or query fails
            ATPNetworkError: If network communication fails
            ATPAuthError: If API key authentication fails
        """
        client = self._get_client()
        task_id_str = str(task_id)

        try:
            logger.debug(f"Querying commit for system {system_id}, task {task_id_str}...")
            response = await client.get(f"/api/v1/systems/{system_id}/tasks/{task_id_str}/commit")

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            if response.status_code == 404:
                raise ATPQueryError(
                    f"No commit found for system_id={system_id}, task_id={task_id_str}"
                )

            if response.status_code != 200:
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPQueryError(
                    f"Query failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            return Commit.from_api_response(data)

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error during commit query: {e}")
        except (KeyError, ValueError) as e:
            raise ATPQueryError(f"Invalid response from backend: {e}")

    async def get_system(self, system_id: str) -> System:
        """
        Get system details by ID.

        Args:
            system_id: System identifier

        Returns:
            System object

        Raises:
            ATPQueryError: If system not found or query fails
            ATPNetworkError: If network communication fails
            ATPAuthError: If API key authentication fails
        """
        client = self._get_client()

        try:
            logger.debug(f"Querying system {system_id}...")
            response = await client.get(f"/api/v1/systems/{system_id}")

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            if response.status_code == 404:
                raise ATPQueryError(f"System {system_id} not found")

            if response.status_code != 200:
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPQueryError(
                    f"Query failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            return System.from_api_response(data)

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error during system query: {e}")
        except (KeyError, ValueError) as e:
            raise ATPQueryError(f"Invalid response from backend: {e}")

    async def verify_response(
        self,
        commit_id: str | UUID,
        response_data: str,
    ) -> bool:
        """
        Verify that a response matches its committed hash.

        Fetches the commit from the Exchange and compares the outcome hash of the
        provided ``response_data`` against what was committed. The commit is always
        already confirmed (Ed25519 exchange signature is synchronous).

        Args:
            commit_id: Commit identifier to verify
            response_data: Response text to verify

        Returns:
            True if response hash matches commit, False otherwise

        Raises:
            ATPVerificationError: If verification fails
            ATPQueryError: If commit not found
            ATPNetworkError: If network communication fails
        """
        try:
            # Get commit — status is always "confirmed" immediately
            commit = await self.get_commit(commit_id)

            # Compute hash of provided response using ATP v0.2.0 prefixed format
            response_hash = hash_with_prefix(response_data)

            # Compare with outcome_hash in the proof sketch
            committed_hash = commit.proof_sketch.cryptography.outcome_hash

            if response_hash != committed_hash:
                logger.warning(
                    f"Response hash mismatch for commit {commit_id}: "
                    f"expected {committed_hash}, got {response_hash}"
                )
                return False

            logger.info(f"Response verified for commit {commit_id}")
            return True

        except (ATPQueryError, ATPNetworkError) as e:
            raise ATPVerificationError(f"Failed to verify response: {e}")

    async def challenge(
        self,
        agent_url: str,
        task_id: str,
        verify: bool = True,
        assessor_task_id: str | None = None,
    ) -> tuple[StoredProof, bool, str]:
        """
        Challenge an ATP-compliant agent to provide proof for a task and verify it.

        Sends a JSON-RPC challenge (including this agent's identity for accountability),
        receives the ``StoredProof``, then transparently verifies it against the ATP
        Exchange in a single call.  Callers just inspect the returned ``(verified, message)``
        — they never need to touch raw hashes or proof fields.

        After verification, the client automatically reports an integrity assessment
        (``verified`` or ``compromised``) to the ATP Exchange. The assessment is
        fire-and-forget: a failure to report does NOT cause ``challenge()`` to raise.

        Args:
            agent_url: Base URL of the agent (e.g., "http://localhost:8012")
            task_id: Task identifier to challenge for
            verify: If True (default), automatically verifies against ATP Exchange
            assessor_task_id: Optional: the caller's own ATP task_id for this
                verification work (links the assessment to a challengeable proof).
                Pass ``verification_task.atp_task_id`` when calling from within
                an ``atp_task`` context.

        Returns:
            ``(stored_proof, verified, message)`` — always a 3-tuple.
            ``verified`` is ``True`` only when hashes match the Exchange record.

        Raises:
            ATPQueryError: If the challenge request fails or the proof is unparseable
            ATPNetworkError: If network communication fails
            RuntimeError: If no system has been registered (challenger identity required)

        Example::

            async with atp_task(client, store, query=q) as vt:
                proof, verified, msg = await client.challenge(
                    agent_url="http://other-agent:8100",
                    task_id="abc-123",
                    assessor_task_id=vt.atp_task_id,
                )
            if verified:
                logger.info("✅ %s", msg)
            else:
                logger.warning("❌ %s", msg)
        """
        # Get challenger's system_id from registered systems
        if not self._registered_systems:
            raise RuntimeError(
                "Cannot include challenger identity - no system registered. "
                "Call register_system() first."
            )

        # Get system name from first registered system
        challenger_name = list(self._registered_systems.keys())[0]
        challenger_system_id = self._registered_systems[challenger_name]

        # Get full system details for complete identity (ATP v0.2.0 field names)
        try:
            system = await self.get_system(challenger_system_id)
            challenger_identity: dict[str, Any] = {
                "atp_exchange_url": self.config.exchange_url,
                "system_id": challenger_system_id,
                "system_type": system.type,
                "system_name": system.name,
                "extensions": {},
            }
        except Exception as e:
            # Fallback to minimal identity if we can't fetch system details
            logger.warning(f"Could not fetch full system details: {e}")
            challenger_identity = {
                "atp_exchange_url": self.config.exchange_url,
                "system_id": challenger_system_id,
                "system_type": "agent",
                "system_name": challenger_name,
                "extensions": {},
            }

        logger.debug(
            f"Including challenger identity: {challenger_system_id} ({challenger_identity.get('system_name', 'unknown')})"
        )

        # Build ATP v0.2.0 flat JSON-RPC challenge payload
        request_payload = {
            "jsonrpc": "2.0",
            "method": "atp.challenge",
            "params": {
                "task_id": task_id,
                "challenger": challenger_identity,
            },
            "id": 1,
        }

        try:
            logger.info(f"Challenging agent at {agent_url} for task {task_id}...")

            # Create a temporary client for the agent URL
            async with httpx.AsyncClient(timeout=self.config.commit_timeout) as agent_client:
                response = await agent_client.post(agent_url, json=request_payload)

                if response.status_code != 200:
                    error_detail = response.text if response.text else "Unknown error"
                    raise ATPQueryError(
                        f"Challenge failed with status {response.status_code}: {error_detail}"
                    )

                data = response.json()

                # Check for JSON-RPC error
                if "error" in data and data.get("error"):
                    error = data["error"]
                    error_message = error.get("message", "Unknown error")
                    error_code = error.get("code", -1)
                    error_data = error.get("data")
                    error_reason = (
                        error_data.get("reason", "unknown")
                        if isinstance(error_data, dict)
                        else "unknown"
                    )

                    raise ATPQueryError(
                        f"Challenge failed (code {error_code}): {error_message} (reason: {error_reason})"
                    )

                # Extract result
                if "result" not in data:
                    raise ATPQueryError("Invalid JSON-RPC response: missing 'result' field")

                # Parse StoredProof from result
                try:
                    result_data = data["result"]
                    stored_proof = StoredProof.model_validate(result_data)
                except Exception as e:
                    logger.error(f"❌ Failed to parse StoredProof: {e}", exc_info=True)
                    logger.error(f"❌ Result data: {data.get('result')}")
                    raise ATPQueryError(f"Invalid proof structure from agent: {e}") from e

                logger.info(f"✓ Challenge successful for task {task_id}")
                task = stored_proof.proof_data.task
                logger.debug(f"  Invocation hash: {task.invocation_hash}")
                logger.debug(f"  Outcome hash:    {task.outcome_hash}")
                logger.debug(f"  Trust level:     {task.trust_level.value}")

                # If verification disabled, return proof only
                if not verify:
                    logger.warning(
                        "Verification disabled - proof not validated against ATP Exchange"
                    )
                    return stored_proof, False, "Verification skipped"

                # Verify proof against ATP Exchange (default behavior)
                try:
                    result_proof, verified, message = await self._verify(stored_proof)
                except Exception as e:
                    logger.error(f"Verification failed: {e}")
                    raise

                # Auto-report integrity assessment — fire-and-forget.
                # A failure to report does NOT cause challenge() to raise.
                try:
                    proof_task = stored_proof.proof_data.task
                    assessed_system_id = proof_task.system_uri.rstrip("/").rsplit("/", 1)[-1]
                    await self.report_assessment(
                        assessed_system_id=assessed_system_id,
                        assessed_task_id=proof_task.task_id,
                        dimension=AssessmentDimension.INTEGRITY,
                        assessment=(
                            IntegrityAssessment.VERIFIED
                            if verified
                            else IntegrityAssessment.COMPROMISED
                        ),
                        assessed_at=datetime.now(UTC),
                        assessor_task_id=assessor_task_id,
                    )
                    logger.info(
                        f"Integrity assessment reported: "
                        f"{'verified' if verified else 'compromised'} "
                        f"for {assessed_system_id}/{proof_task.task_id}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to report integrity assessment (non-fatal): {e}")

                return result_proof, verified, message

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error during challenge: {e}")
        except ATPQueryError:
            # Re-raise our custom exceptions
            raise
        except (KeyError, ValueError) as e:
            raise ATPQueryError(f"Invalid response from agent: {e}")

    async def report_assessment(
        self,
        assessed_system_id: str,
        assessed_task_id: str,
        dimension: AssessmentDimension,
        assessment: str,
        assessed_at: datetime,
        assessor_task_id: str | None = None,
    ) -> AssessmentRecord | None:
        """
        Report a trust assessment verdict to the ATP Exchange.

        Phase 1: ``challenge()`` calls this automatically for integrity assessments.
        Quality and compliance assessments can be submitted directly by authorised
        third-party evaluators.

        Args:
            assessed_system_id: System ID of the agent whose task is being assessed
            assessed_task_id: Task ID being assessed
            dimension: Assessment dimension (integrity / quality / compliance)
            assessment: Verdict string consistent with the dimension
                - integrity:  'verified' | 'compromised'
                - quality:    'passed'   | 'failed'
                - compliance: 'compliant'| 'non-compliant'
            assessed_at: When the assessment was performed
            assessor_task_id: Optional: the assessor's own ATP task_id (links the
                assessment to a challengeable proof of the assessment act itself)

        Returns:
            AssessmentRecord on success, None if the Exchange returned 409 (duplicate)

        Raises:
            ATPQueryError: If submission fails with a non-409 error
            ATPNetworkError: If network communication fails
            RuntimeError: If no system has been registered
        """
        if not self._registered_systems:
            raise RuntimeError(
                "Cannot report assessment — no system registered. Call register_system() first."
            )

        assessor_name = list(self._registered_systems.keys())[0]
        assessor_system_id = self._registered_systems[assessor_name]

        client = self._get_client()

        # Use getattr so both plain str and str-Enum values serialize correctly.
        payload: dict[str, Any] = {
            "assessor_system_id": assessor_system_id,
            "assessed_system_id": assessed_system_id,
            "assessed_task_id": assessed_task_id,
            "dimension": getattr(dimension, "value", dimension),
            "assessment": getattr(assessment, "value", assessment),
            "assessed_at": assessed_at.isoformat(),
        }
        if assessor_task_id is not None:
            payload["assessor_task_id"] = assessor_task_id

        try:
            response = await client.post("/api/v1/assess", json=payload)

            # 409 = duplicate assessment for this dimension; treat as success
            if response.status_code == 409:
                logger.debug(
                    f"Assessment already exists for {assessed_system_id}/{assessed_task_id} "
                    f"dimension={dimension} — skipping."
                )
                return None

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            if response.status_code not in (200, 201):
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPQueryError(
                    f"Assessment submission failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            dim_value = getattr(dimension, "value", dimension)
            return AssessmentRecord(
                assessment_id=data["assessment_id"],
                assessor_system_id=assessor_system_id,
                assessor_task_id=assessor_task_id,
                assessed_system_id=assessed_system_id,
                assessed_task_id=assessed_task_id,
                dimension=AssessmentDimension(dim_value),
                assessment=getattr(assessment, "value", assessment),
                assessed_at=assessed_at,
                created_at=datetime.fromisoformat(data["recorded_at"]),
            )

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error reporting assessment: {e}")

    async def get_assessments(
        self,
        system_id: str,
        task_id: str,
    ) -> AssessmentsResponse:
        """
        Get all trust assessment records for a specific system+task pair.

        Args:
            system_id: System identifier
            task_id: Task identifier (UUID string)

        Returns:
            AssessmentsResponse with all assessment records (may be empty list)

        Raises:
            ATPQueryError: If query fails
            ATPNetworkError: If network communication fails
            ATPAuthError: If API key authentication fails
        """
        client = self._get_client()

        try:
            response = await client.get(f"/api/v1/systems/{system_id}/tasks/{task_id}/assessments")

            if response.status_code == 401:
                raise ATPAuthError("Invalid API key")

            if response.status_code != 200:
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPQueryError(
                    f"Assessment query failed with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            return AssessmentsResponse.from_api_response(data)

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error querying assessments: {e}")
        except (KeyError, ValueError) as e:
            raise ATPQueryError(f"Invalid response from backend: {e}")

    async def _verify(
        self,
        stored_proof: StoredProof,
    ) -> tuple[StoredProof, bool, str]:
        """
        Internal: Verify a challenged proof against ATP Exchange (ATP v0.2.0).

        Step 1 — Local integrity: recompute invocation_hash and outcome_hash from
        the full content in the stored proof and confirm they match.

        Step 2 — Exchange consistency: fetch the proof sketch committed to the
        Exchange and confirm the hashes match what's stored there.

        Step 3 — Catalog status: confirm the commit is not in a failed state.

        Returns:
            tuple: (stored_proof, verified, message)
        """
        try:
            task = stored_proof.proof_data.task
            proof_task_id = task.task_id
            proof_inv_hash = task.invocation_hash
            proof_out_hash = task.outcome_hash
            # Extract system_id from the trailing path segment of system_uri
            # e.g. "https://exchange.example.com/systems/sys_abc123" → "sys_abc123"
            system_id = task.system_uri.rstrip("/").rsplit("/", 1)[-1]
        except Exception as e:
            logger.error(f"Error extracting proof data: {e}")
            return stored_proof, False, f"Invalid proof structure: {e}"

        # Step 1: Verify hash integrity — content must match the declared hashes
        computed_inv_hash = hash_with_prefix(task.invocation)
        computed_out_hash = hash_with_prefix(task.outcome)

        if computed_inv_hash != proof_inv_hash:
            return stored_proof, False, "Proof invocation hash mismatch (proof is invalid)"

        if computed_out_hash != proof_out_hash:
            return stored_proof, False, "Proof outcome hash mismatch (proof is invalid)"

        # Step 2: Verify against ATP Exchange — hashes must match the committed sketch
        try:
            commit = await self.get_commit_by_system_and_task(system_id, proof_task_id)
            exchange_inv_hash = commit.proof_sketch.cryptography.invocation_hash
            exchange_out_hash = commit.proof_sketch.cryptography.outcome_hash

            if exchange_inv_hash != proof_inv_hash:
                return stored_proof, False, "Invocation hash doesn't match ATP Exchange"

            if exchange_out_hash != proof_out_hash:
                return stored_proof, False, "Outcome hash doesn't match ATP Exchange"

        except Exception as e:
            logger.error(f"Exchange verification exception: {e}", exc_info=True)
            return stored_proof, False, f"Failed to verify with ATP Exchange: {e}"

        # Step 3: Check commit status — should always be "confirmed"
        status = commit.status.value
        if status == "confirmed":
            return stored_proof, True, "✅ Verified and confirmed"
        else:
            return stored_proof, False, f"Commit in unexpected state: {status}"

    async def get_exchange_public_key(self) -> str:
        """
        Get the exchange's Ed25519 public key for signature verification.

        Returns:
            Hex-encoded Ed25519 public key

        Raises:
            ATPQueryError: If query fails
            ATPNetworkError: If network communication fails
        """
        client = self._get_client()

        try:
            logger.debug("Fetching exchange public key...")
            response = await client.get("/api/v1/exchange/public-key")

            if response.status_code != 200:
                error_detail = (
                    response.json().get("error", response.text)
                    if response.text
                    else "Unknown error"
                )
                raise ATPQueryError(
                    f"Failed to fetch public key with status {response.status_code}: {error_detail}"
                )

            data = response.json()
            return str(data["public_key"])

        except httpx.RequestError as e:
            raise ATPNetworkError(f"Network error fetching public key: {e}")
        except (KeyError, ValueError) as e:
            raise ATPQueryError(f"Invalid response from backend: {e}")

    def verify_signature(
        self,
        commit: Commit | CommitResponse,
        exchange_public_key: str | None = None,
    ) -> bool:
        """
        Verify a commit signature against the exchange's public key.

        This allows clients to cryptographically verify that a commit was signed
        by the ATP exchange, providing proof of commitment without trusting
        the API response alone.

        Args:
            commit: Commit or CommitResponse object with signature
            exchange_public_key: Hex-encoded Ed25519 public key (fetched automatically if None)

        Returns:
            True if signature is valid, False otherwise

        Raises:
            ATPVerificationError: If signature verification fails
            ValueError: If commit has no signature or cataloged_at

        Example:
            ```python
            commit = await client.create_commit(system_id, task_id, proof)

            # Verify signature
            if client.verify_signature(commit):
                print("✅ Commit authentically signed by exchange")
            else:
                print("❌ Invalid signature!")
            ```
        """
        try:
            from nacl.exceptions import BadSignatureError
            from nacl.signing import VerifyKey
        except ImportError:
            raise ImportError(
                "PyNaCl is required for signature verification. Install it with: pip install pynacl"
            )

        try:
            import base58
        except ImportError:
            raise ImportError(
                "base58 is required for signature verification. Install it with: pip install base58"
            )

        # Extract commit data
        if not commit.signature:
            raise ValueError("Commit has no signature (may be pending)")

        if not commit.cataloged_at:
            raise ValueError("Commit has no cataloged_at time (may be pending)")

        # get_commit() must be used (CommitResponse has no proof_sketch)
        if not isinstance(commit, Commit):
            raise ValueError(
                "Cannot verify signature from CommitResponse (proof_sketch not available). "
                "Use get_commit() to fetch full Commit object first."
            )

        commit_id = str(commit.id)
        task_id = str(commit.task_id)
        system_id = commit.system_id

        # Recompute the ephemeral proof_hash from the stored proof sketch.
        # dependencies_hash is None for legacy commits → falls back to old 4-field formula,
        # ensuring verify_signature works correctly for both old and new commits.
        proof_hash = compute_proof_hash(
            commit.proof_sketch.atp_metadata.system_uri,
            commit.proof_sketch.atp_metadata.task_id,
            commit.proof_sketch.cryptography.invocation_hash,
            commit.proof_sketch.cryptography.outcome_hash,
            commit.proof_sketch.cryptography.dependencies_hash,
            commit.proof_sketch.timestamp,
        )

        # Reconstruct signature message — must match signCommit() in Go backend
        # Format: commitID|taskID|systemID|proofHash|catalogedAt (RFC3339Nano)
        cataloged_at_iso = commit.cataloged_at.isoformat()
        message = f"{commit_id}|{task_id}|{system_id}|{proof_hash}|{cataloged_at_iso}"

        try:
            # Decode base58 signature
            sig_bytes = base58.b58decode(commit.signature)

            # Decode hex public key and create VerifyKey
            pubkey_bytes = bytes.fromhex(exchange_public_key) if exchange_public_key else None
            if not pubkey_bytes:
                raise ValueError("Exchange public key required for signature verification")

            verify_key = VerifyKey(pubkey_bytes)

            # Verify signature
            verify_key.verify(message.encode(), sig_bytes)
            logger.info(f"✅ Signature verified for commit {commit_id}")
            return True

        except (BadSignatureError, ValueError) as e:
            logger.warning(f"❌ Signature verification failed for commit {commit_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Signature verification error: {e}", exc_info=True)
            raise ATPVerificationError(f"Signature verification failed: {e}")

    async def close(self):
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
