"""
ATP Utility Functions — ATP v0.2.0

Helpers for automatic ATP integration: task_id generation, response wrapping,
and proof lifecycle management.
"""

import logging
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, TypeVar

from atp.core.client import ATPClient
from atp.core.config import ATPConfig
from atp.core.hashing import compute_dependencies_hash, hash_with_prefix
from atp.core.models import (
    DEFAULT_CLASSIFICATION,
    AssessmentDimension,
    ATPMetadata,
    ATPTrustLevel,
    Capability,
    Cryptography,
    Dependency,
    DependencyEvaluation,
    IntegrityAssessment,
    ProofData,
    ProofDataTask,
    ProofSketch,
    StoredProof,
)
from atp.core.storage import ProofStore

logger = logging.getLogger(__name__)

# ContextVar that holds the currently-active ATPTaskContext (if any).
# Set by ATPTaskContext.__aenter__, reset in __aexit__.
# ATPClientSession reads this to auto-register MCP tool-call proofs as
# dependencies on the active task without any explicit plumbing in the agent.
_current_atp_task: ContextVar["ATPTaskContext | None"] = ContextVar(
    "_current_atp_task", default=None
)

T = TypeVar("T")


class ATPTaskContext:
    """
    Context for an ATP task execution.

    Automatically generates atp_task_id and handles proof lifecycle.

    Usage::

        async with atp_task(client, store, query="What is 2+2?") as task:
            answer = await my_agent_logic(task.query)
            task.set_response(answer)
            # Proof automatically created and committed

        logger.info(f"Task ID: {task.atp_task_id}")
    """

    def __init__(
        self,
        client: ATPClient,
        proof_store: ProofStore,
        query: str,
        config: ATPConfig | None = None,
        classification: Capability | None = None,
    ):
        self.client = client
        self.proof_store = proof_store
        self.query = query
        self.config = config or client.config
        self.atp_task_id = str(uuid.uuid4())
        self.response: str | None = None
        self._committed = False
        # ATP v0.2.0: task classification — defaults to question-answering when not set.
        self._classification: Capability = classification or DEFAULT_CLASSIFICATION
        # v0.2.0: dependencies are Dependency references, not nested sketches
        self._dependencies: list[Dependency] = []

    def set_response(self, response: str | Any) -> None:
        """Set the response for this task."""
        self.response = str(response) if not isinstance(response, str) else response

    def add_dependent_proof(
        self,
        proof: Dependency | StoredProof,
        evaluations: list[DependencyEvaluation] | None = None,
    ) -> None:
        """
        Add a dependency on another agent's task.

        In v0.2.0 dependencies are flat references (system_uri + task_id),
        not nested proof copies.

        Quality (and integrity) verdicts assessed by the verifying agent should
        be passed as ``evaluations``.  They are embedded in the proof sketch
        committed to the Exchange rather than sent via a separate assessment API
        call, keeping all assessment evidence in one auditable location.

        Args:
            proof:       Dependency reference or StoredProof (converted to Dependency).
            evaluations: Optional list of ``DependencyEvaluation`` objects recording
                         assessment verdicts for this dependency (e.g. integrity and
                         quality outcomes).

        .. TODO::
            This method currently accepts either a ``Dependency`` or a ``StoredProof``
            and silently converts the latter.  The mixed-type signature is clumsy and
            makes call-sites harder to reason about.  Refactor to accept only
            ``Dependency`` and provide a separate helper (e.g.
            ``Dependency.from_stored_proof(proof, evaluations=...)``) so conversions
            are explicit and the purpose of each call site is clear.
        """
        if isinstance(proof, StoredProof):
            dep = Dependency(
                system_uri=proof.proof_data.task.system_uri,
                task_id=proof.proof_data.task.task_id,
                evaluations=evaluations or [],
            )
            self._dependencies.append(dep)
        elif isinstance(proof, Dependency):
            if evaluations:
                # Merge caller-supplied evaluations into an existing Dependency.
                proof = proof.model_copy(update={"evaluations": proof.evaluations + evaluations})
            self._dependencies.append(proof)
        else:
            raise TypeError(f"Expected Dependency or StoredProof, got {type(proof)}")

    async def _commit(self) -> None:
        """Internal: Create and commit proof sketch."""
        if self._committed or not self.response:
            return

        now = datetime.now(UTC)

        system_id = None
        if hasattr(self.client, "_registered_systems") and self.client._registered_systems:
            system_id = list(self.client._registered_systems.values())[0]

        if not system_id:
            raise RuntimeError("No registered system found. Call client.register_system() first.")

        exchange_url = self.config.exchange_url or "http://localhost:8080"
        system_uri = f"{exchange_url}/systems/{system_id}"
        system_type = self.client._system_types.get(system_id, "agent")

        # Build canonical invocation / outcome dicts
        invocation: dict[str, Any] = {
            "method": "query",
            "trigger": {"type": "user_message"},
            "input": {"query": self.query},
        }
        outcome: dict[str, Any] = {
            "response": {"text": self.response},
            "status": "success",
            "actions": [],
            "error": None,
        }

        invocation_hash = hash_with_prefix(invocation)
        outcome_hash = hash_with_prefix(outcome)
        dependencies_hash = compute_dependencies_hash(self._dependencies)

        # --- ProofSketch (committed to Exchange — hashes only) ---
        proof_sketch = ProofSketch(
            atp_metadata=ATPMetadata(
                spec_version="0.2.0",
                spec_uri="https://agenttrustprotocol.org/spec/v0.2",
                system_uri=system_uri,
                system_type=system_type,
                task_id=self.atp_task_id,
                classification=self._classification,
            ),
            dependencies=self._dependencies,
            cryptography=Cryptography(
                algorithm="SHA-256",
                invocation_hash=invocation_hash,
                outcome_hash=outcome_hash,
                dependencies_hash=dependencies_hash,
            ),
            timestamp=now,
        )

        # --- ProofData (stored locally — full content for challenge-response) ---
        proof_data = ProofData(
            task=ProofDataTask(
                system_uri=system_uri,
                system_type=system_type,
                task_id=self.atp_task_id,
                invocation=invocation,
                invocation_hash=invocation_hash,
                outcome=outcome,
                outcome_hash=outcome_hash,
                timestamp=now,
                trust_level=ATPTrustLevel.UNVERIFIED,
            ),
            dependencies=self._dependencies,
        )

        stored_proof = StoredProof(
            proof_data=proof_data,
            created_at=now,
            expires_at=now + timedelta(seconds=self.config.proof_ttl_seconds),
        )

        await self.proof_store.save(stored_proof)

        await self.client.create_commit(
            system_id=system_id,
            task_id=self.atp_task_id,
            proof_sketch=proof_sketch,
        )

        self._committed = True

    async def __aenter__(self):
        self._task_ctx_token = _current_atp_task.set(self)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        _current_atp_task.reset(self._task_ctx_token)
        if exc_type is None and self.response:
            await self._commit()


@asynccontextmanager
async def atp_task(
    client: ATPClient,
    proof_store: ProofStore,
    query: str,
    config: ATPConfig | None = None,
    classification: Capability | None = None,
):
    """
    Async context manager for ATP tasks.

    Args:
        client: Registered ATPClient.
        proof_store: Local proof storage backend.
        query: The input query text for this task.
        config: Optional override config (defaults to client.config).
        classification: ATP task classification (ATP v0.2.0+).
            Defaults to ``DEFAULT_CLASSIFICATION`` (question-answering) when
            not provided. Supply a real ``Capability`` for accurate tracking::

                from atp.core.models import Capability, Ontology
                async with atp_task(
                    client, store, query=q,
                    classification=Capability(
                        description="Customer support triage",
                        ontology=Ontology(
                            ontology_uri="https://agenttrustprotocol.org/ontology/v0.2.0",
                            occupation="43-4051.00",
                            capabilities=["text-classification"],
                        ),
                    ),
                ) as task:
                    ...

    Example::

        async with atp_task(client, store, "What is 2+2?") as task:
            answer = await my_agent_logic(task.query)
            task.set_response(answer)

        logger.info(f"ATP Task ID: {task.atp_task_id}")
    """
    context = ATPTaskContext(client, proof_store, query, config, classification)
    async with context:
        yield context


def atp_response(
    client: ATPClient,
    proof_store: ProofStore,
    query_param: str = "query",
    response_wrapper: bool = True,
):
    """
    Decorator that automatically adds ATP proof lifecycle to functions.

    Example::

        @atp_response(client, store)
        async def my_agent(query: str) -> str:
            return "4"

        result = await my_agent(query="What is 2+2?")
        # {"atp_task_id": "...", "result": "4", "atp_committed": True}
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> dict[str, Any] | Any:
            query = kwargs.get(query_param)
            if not query and args:
                query = args[0] if args else ""
            query = str(query)

            async with atp_task(client, proof_store, query) as task:
                if args:
                    result = await func(task.query, *args[1:], **kwargs)
                else:
                    kwargs[query_param] = task.query
                    result = await func(**kwargs)

                task.set_response(result)

                if response_wrapper:
                    return {
                        "atp_task_id": task.atp_task_id,
                        "result": result,
                        "atp_committed": task._committed,
                    }
                return result

        return wrapper

    return decorator


async def challenge_and_verify(
    client: ATPClient,
    agent_url: str,
    task_id: str,
    expected_invocation: dict[str, Any] | None = None,
    expected_outcome: dict[str, Any] | None = None,
) -> tuple[dict, bool, str]:
    """
    Challenge an agent for proof and automatically verify against ATP Exchange.

    The *caller* (``client``) must be a registered ATP system — i.e.
    ``client.register_system()`` must have been called before invoking this
    function.  The caller's registered system acts as the assessor: it provides
    the challenger identity sent in the JSON-RPC challenge request and is the
    entity whose name appears on the integrity assessment committed to the
    Exchange on success or failure.

    In ATP there are only registered systems (agents / constructs / toolboxes).
    A "bare" HTTP client that has not registered cannot assess — it has no
    accountable identity on the Exchange.  The LangChain client example
    reflects this by registering the verifier as an ATP agent before calling
    this function.

    Steps:
    1. Include challenger identity in JSON-RPC challenge request
    2. Verify local hash integrity (content matches declared hashes)
    3. Verify hashes against ATP Exchange (critical!)
    4. Optionally verify invocation/outcome content
    5. Report integrity assessment to Exchange as the registered assessor

    Args:
        client: ATPClient — must have a registered system (used as assessor)
        agent_url: URL of agent to challenge
        task_id: Task ID to challenge for
        expected_invocation: Optional invocation dict to verify content
        expected_outcome: Optional outcome dict to verify content

    Returns:
        tuple: (stored_proof_dict, verified, message)
    """
    import httpx

    system_id = None
    if hasattr(client, "_registered_systems") and client._registered_systems:
        system_id = list(client._registered_systems.values())[0]

    if not system_id:
        return {}, False, "No registered system found. Call client.register_system() first."

    try:
        system = await client.get_system(system_id)
        challenger_identity: dict[str, Any] = {
            "atp_exchange_url": client.config.exchange_url or "http://localhost:80",
            "system_id": system_id,
            "system_type": system.type,
            "system_name": system.name,
            "extensions": {},
        }
    except Exception:
        challenger_identity = {
            "atp_exchange_url": client.config.exchange_url or "http://localhost:80",
            "system_id": system_id,
            "system_type": "agent",
            "system_name": "ATPClient",
            "extensions": {},
        }

    # ATP v0.2.0 flat challenge params
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
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(agent_url, json=request_payload, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            if data.get("error"):
                return {}, False, f"Challenge failed: {data['error']}"

            stored_proof = data["result"]
    except Exception as e:
        return {}, False, f"Challenge request failed: {e}"

    # Extract task data from v0.2.0 nested structure
    try:
        proof_task = stored_proof["proof_data"]["task"]
        proof_system_uri = proof_task["system_uri"]
        proof_task_id = proof_task["task_id"]
        proof_invocation = proof_task["invocation"]
        proof_invocation_hash = proof_task["invocation_hash"]
        proof_outcome = proof_task["outcome"]
        proof_outcome_hash = proof_task["outcome_hash"]
        # Extract system_id from system_uri trailing segment
        extracted_system_id = proof_system_uri.rstrip("/").rsplit("/", 1)[-1]
    except (KeyError, TypeError) as e:
        return stored_proof, False, f"Invalid proof structure: {e}"

    # Resolve assessor identity — the caller must be a registered ATP system.
    # challenge_and_verify() requires registration so there is always a valid
    # assessor_system_id (we already checked this above and returned early if not).
    assessor_system_id = list(client._registered_systems.values())[0]
    assessor_name = list(client._registered_systems.keys())[0]

    # Helper: fire-and-forget integrity assessment (non-fatal)
    async def _report(assessment: IntegrityAssessment) -> None:
        try:
            await client.report_assessment(
                assessed_system_id=extracted_system_id,
                assessed_task_id=proof_task_id,
                dimension=AssessmentDimension.INTEGRITY,
                assessment=assessment,
                assessed_at=datetime.now(UTC),
            )
            logger.info(
                f"Integrity assessment ({assessment.value}) submitted to Exchange — "
                f"assessor: {assessor_name} ({assessor_system_id}), "
                f"assessed: {extracted_system_id}/{proof_task_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to report integrity assessment (non-fatal): {e}")

    # Step 1: Verify hash integrity (content → hash)
    if hash_with_prefix(proof_invocation) != proof_invocation_hash:
        await _report(IntegrityAssessment.COMPROMISED)
        return stored_proof, False, "Proof invocation hash mismatch (proof is invalid)"

    if hash_with_prefix(proof_outcome) != proof_outcome_hash:
        await _report(IntegrityAssessment.COMPROMISED)
        return stored_proof, False, "Proof outcome hash mismatch (proof is invalid)"

    # Step 2: Verify against ATP Exchange (CRITICAL!)
    try:
        commit = await client.get_commit_by_system_and_task(
            system_id=extracted_system_id, task_id=proof_task_id
        )
        exchange_inv_hash = commit.proof_sketch.cryptography.invocation_hash
        exchange_out_hash = commit.proof_sketch.cryptography.outcome_hash

        if exchange_inv_hash != proof_invocation_hash:
            await _report(IntegrityAssessment.COMPROMISED)
            return stored_proof, False, "Invocation hash doesn't match ATP Exchange"

        if exchange_out_hash != proof_outcome_hash:
            await _report(IntegrityAssessment.COMPROMISED)
            return stored_proof, False, "Outcome hash doesn't match ATP Exchange"

    except Exception as e:
        # Network/exchange errors are ambiguous — no assessment reported
        return stored_proof, False, f"Failed to verify with ATP Exchange: {e}"

    # Step 3: Optionally verify content
    if expected_invocation is not None and proof_invocation != expected_invocation:
        await _report(IntegrityAssessment.COMPROMISED)
        return stored_proof, False, f"Invocation content mismatch: {proof_invocation!r}"

    if expected_outcome is not None and proof_outcome != expected_outcome:
        await _report(IntegrityAssessment.COMPROMISED)
        return stored_proof, False, f"Outcome content mismatch: {proof_outcome!r}"

    # Step 4: Check catalog status — commits are always confirmed immediately
    status = commit.status.value
    if status == "confirmed":
        await _report(IntegrityAssessment.VERIFIED)
        return stored_proof, True, "✅ Verified and confirmed"
    else:
        # Unexpected state is ambiguous — no assessment reported
        return stored_proof, False, f"Commit in unexpected state: {status}"


class ATPResponseWrapper:
    """
    Wrapper for responses that includes atp_task_id.

    Example::

        return ATPResponseWrapper(atp_task_id=task_id, result=answer, atp_committed=True)
    """

    def __init__(self, atp_task_id: str, result: Any, atp_committed: bool = True, **extra_fields):
        self.atp_task_id = atp_task_id
        self.result = result
        self.atp_committed = atp_committed
        self.extra = extra_fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "atp_task_id": self.atp_task_id,
            "result": self.result,
            "atp_committed": self.atp_committed,
            **self.extra,
        }

    def __repr__(self) -> str:
        return f"ATPResponseWrapper(atp_task_id={self.atp_task_id}, atp_committed={self.atp_committed})"


async def pre_challenge_dependencies(
    client: ATPClient,
    agent_dependencies: list,
    dependencies: list[Dependency | dict],
) -> None:
    """
    Challenge upstream ATP proofs declared in ``ATPRequestMeta.dependencies``.

    Called by the @atp_agent pre-processing hook whenever an inbound request
    or A2A message contains ``atp_metadata.dependencies`` with one or more
    entries whose ``challenge_url`` is set.

    For each such dependency:
      1. POST JSON-RPC challenge to ``dep.challenge_url``
      2. Verify local hash integrity (invocation_hash / outcome_hash)
      3. Verify hashes against the ATP Exchange
      4. Report IntegrityAssessment (verified / compromised)
      5. Append a verified ``ProofData`` object to ``agent_dependencies``
         (``self._atp_dependencies`` injected by @atp_agent) so that the
         agent's proof commit automatically references this as a dependency.

    Dependency entries without ``challenge_url`` are silently skipped — they
    are recorded as known dependencies but cannot be challenged at call time
    (e.g. already-verified deps referenced by system_uri + task_id only).

    Args:
        client:             ATPClient instance (``self._atp_client`` from @atp_agent).
        agent_dependencies: The agent's live dependency list (``self._atp_dependencies``).
        dependencies:       List of ``Dependency`` objects or raw dicts from
                            ``atp_metadata.dependencies`` in the inbound request.
    """
    for raw in dependencies:
        dep = Dependency(**raw) if isinstance(raw, dict) else raw
        if not dep.challenge_url:
            # No challenge URL — record as a known dependency without challenging.
            continue
        if not dep.task_id:
            logger.warning("atp_request_meta_dep_missing_task_id dep=%s", str(raw))
            continue

        try:
            proof_dict, verified, msg = await challenge_and_verify(
                client=client,
                agent_url=dep.challenge_url,
                task_id=dep.task_id,
            )
            if verified and proof_dict:
                task_data = proof_dict["proof_data"]["task"]
                ts_raw = task_data.get("timestamp")
                if isinstance(ts_raw, str):
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                    except ValueError:
                        ts = datetime.now(UTC)
                elif isinstance(ts_raw, datetime):
                    ts = ts_raw
                else:
                    ts = datetime.now(UTC)

                agent_dependencies.append(
                    ProofData(
                        task=ProofDataTask(
                            system_uri=task_data["system_uri"],
                            system_type=task_data.get("system_type", "agent"),
                            task_id=task_data["task_id"],
                            invocation=task_data["invocation"],
                            invocation_hash=task_data["invocation_hash"],
                            outcome=task_data["outcome"],
                            outcome_hash=task_data["outcome_hash"],
                            timestamp=ts,
                            trust_level=ATPTrustLevel.VERIFIED,
                        ),
                        dependencies=[],
                    )
                )
                logger.info(
                    "atp_request_dep_verified task_id=%s challenge_url=%s",
                    dep.task_id,
                    dep.challenge_url,
                )
            else:
                logger.warning(
                    "atp_request_dep_unverified task_id=%s msg=%s",
                    dep.task_id,
                    msg,
                )
        except Exception as e:
            logger.warning(
                "atp_request_dep_challenge_failed task_id=%s challenge_url=%s error=%s",
                dep.task_id,
                dep.challenge_url,
                str(e),
            )


__all__ = [
    "ATPTaskContext",
    "atp_task",
    "atp_response",
    "ATPResponseWrapper",
    "challenge_and_verify",
    "pre_challenge_dependencies",
]
