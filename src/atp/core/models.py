"""
ATP Data Models

Data structures for ATP proofs and commits — ATP v0.2.0.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CommitStatus(str, Enum):
    """Commit status values.

    All commits are immediately ``CONFIRMED`` via an Ed25519 exchange signature.
    Blockchain anchoring is asynchronous (~1-2 min) and is tracked via the
    ``blockchain_signature`` field on ``Commit``, not via a separate status.
    """

    CONFIRMED = "confirmed"
    FAILED = "failed"


class ATPTrustLevel(str, Enum):
    """
    Trust level used in challenge-response verification.

    Not part of v0.2.0 proof sketches (those use the Assessment system).
    Used internally when an agent verifies a challenged proof.
    """

    TRUSTED = "Trusted"  # Not yet verified
    VERIFIED = "Verified"  # Verified against ATP exchange
    UNTRUSTED = "Untrusted"  # Verification failed (tampering detected)
    UNVERIFIED = "Unverified"  # Placeholder before Phase 5 Assessment


# ---------------------------------------------------------------------------
# ATP v0.2.0 Proof Sketch Types (committed to Exchange)
# ---------------------------------------------------------------------------


class Ontology(BaseModel):
    """
    ATP task classification ontology (ATP Spec v0.2.0 Section 6).

    Attributes:
        ontology_version: Ontology schema version (e.g. "0.1.0")
        ontology_uri: URI identifying the ontology (required when classification present)
        occupation: O*NET SOC code (format: XX-XXXX.XX, e.g. "29-1141.00")
        work_activities: List of work activity slugs
        capabilities: List of capability IDs from onet.capabilities
    """

    ontology_version: str | None = None
    ontology_uri: str
    occupation: str | None = None
    work_activities: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class Capability(BaseModel):
    """
    ATP task capability declaration.

    Used both in system registration (capabilities list) and as the
    classification field inside ATPMetadata for a proof sketch.

    Attributes:
        description: Free-form description of the capability
        ontology: Structured ontology classification
    """

    description: str | None = None
    ontology: Ontology

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


# ---------------------------------------------------------------------------
# Default classification — used when callers do not supply one.
#
# ATP v0.2.0 Exchange enforcement requires classification on every commit.
# Rather than raising an error for existing agents that haven't been updated,
# the SDK silently substitutes this default so they remain Exchange-compatible.
# Agents should override this with their real classification for accurate tracking.
# ---------------------------------------------------------------------------
DEFAULT_CLASSIFICATION = Capability(
    description="General question-answering task (SDK default)",
    ontology=Ontology(
        ontology_uri="https://agenttrustprotocol.org/ontology/v0.2.0",
        capabilities=["question-answering"],
    ),
)


class ATPMetadata(BaseModel):
    """
    ATP protocol metadata for a proof sketch (ATP v0.2.0).

    Attributes:
        spec_version: ATP spec version (e.g. "0.2.0")
        spec_uri: URI of the spec document
        system_uri: Full URI of the system: {exchange_base_url}/systems/{system_id}
        system_type: "agent", "toolbox", or "construct"
        task_id: Unique task identifier
        classification: Task classification (REQUIRED by Exchange v0.2.0+).
            When ``None``, ``ATPClient.create_commit()`` substitutes
            ``DEFAULT_CLASSIFICATION`` (``question-answering``) automatically.
    """

    spec_version: str
    spec_uri: str | None = None
    system_uri: str
    system_type: str
    task_id: str
    # Classification is REQUIRED for all commits as of ATP v0.2.0 Exchange enforcement.
    # ATPClient.create_commit() substitutes DEFAULT_CLASSIFICATION when this is None.
    classification: Capability | None = None

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class DependencyEvaluation(BaseModel):
    """Assessment performed on a dependency's proof."""

    evaluation_type: str
    evaluation_policy: str | None = None
    evaluation_result: str
    evaluated_at: datetime

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class Dependency(BaseModel):
    """
    Declared dependency on another agent's task (flat, non-recursive).

    Dual purpose — same model used in both contexts:

    1. **ProofSketch.dependencies** (committed to Exchange):
       Records which upstream tasks this proof depended on.
       ``challenge_url`` is ``None`` here — it is not needed after the fact.

    2. **ATPRequestMeta.dependencies** (carried in requests):
       Tells the *receiving* agent which upstream proofs it should challenge
       before its domain logic runs.  ``challenge_url`` is the HTTP base URL
       of the upstream agent's challenge-response endpoint.

    Attributes:
        system_uri: Full URI of the dependency system.
            Optional in request context — recipient learns it from the proof.
        task_id: Task ID of the dependency
        challenge_url: HTTP base URL of the upstream agent for JSON-RPC
            challenge-response.  Set in request context; ``None`` in proofs.
        evaluations: Optional assessments of this dependency (proof context)
    """

    system_uri: str | None = None  # optional in request context; required in proof context
    task_id: str
    challenge_url: str | None = None  # request context only; None in ProofSketch.dependencies
    evaluations: list[DependencyEvaluation] = Field(default_factory=list)

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class Cryptography(BaseModel):
    """
    Cryptographic hashes for a proof sketch.

    Note: proof_hash is NOT stored here — it is computed ephemerally by the
    Exchange only when generating the exchange signature or blockchain anchor.

    Attributes:
        algorithm: Hash algorithm (always "SHA-256")
        invocation_hash: sha256: prefixed hash of the canonical invocation JSON
        outcome_hash: sha256: prefixed hash of the canonical outcome JSON
        dependencies_hash: sha256: prefixed hash of the canonical dependencies JSON.
            Computed via ``compute_dependencies_hash(proof_sketch.dependencies)``.
            None for legacy commits created before this field was introduced;
            those commits used the old 4-field proof_hash formula.
    """

    algorithm: str = "SHA-256"
    invocation_hash: str  # "sha256:" + 64 lowercase hex chars
    outcome_hash: str  # "sha256:" + 64 lowercase hex chars
    dependencies_hash: str | None = None  # "sha256:" + 64 lowercase hex chars

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class ProofSketch(BaseModel):
    """
    Privacy-preserving proof committed to the ATP Exchange (ATP v0.2.0).

    Contains all proof metadata and cryptographic hashes but excludes the actual
    invocation input and outcome response, which remain locally with the agent
    for challenge-response.

    Attributes:
        atp_metadata: Identity and classification metadata
        dependencies: Flat list of dependency task references (non-recursive)
        cryptography: Invocation and outcome hashes
        timestamp: When the task was completed (UTC, RFC3339Nano)
    """

    atp_metadata: ATPMetadata
    dependencies: list[Dependency] = Field(default_factory=list)
    cryptography: Cryptography
    timestamp: datetime

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


# ---------------------------------------------------------------------------
# Exchange API response models
# ---------------------------------------------------------------------------


class SystemRegistration(BaseModel):
    """
    System registration response (ATP v0.2.0).

    Attributes:
        system_uri: Full URI of the registered system
        system_id: Unique system identifier hash
        registered_at: Registration timestamp
        status: Registration status (always "active" for new registrations)
        capabilities_registered: Count of capabilities declared
        url: Publicly accessible URL of the agent/system (e.g. challenge endpoint).
            The Exchange performs a HEAD check and reflects the result in url_verified.
        url_verified: True when the Exchange could successfully HEAD the declared url.
        proof_ttl_seconds: How long the system stores full proofs for challenge-response.
            None means the backend default applies.
    """

    system_uri: str
    system_id: str
    registered_at: datetime
    status: str
    capabilities_registered: int
    url: str | None = None
    url_verified: bool = False
    proof_ttl_seconds: int | None = None


class CommitResponse(BaseModel):
    """
    Commit creation response.

    ``status`` is always ``"confirmed"`` — the Exchange issues an Ed25519
    signature synchronously. The ``extensions.catalog`` field that previously
    indicated "exchange" vs "blockchain" is no longer present; blockchain
    anchoring is now automatic and unconditional for every commit.

    Attributes:
        commit_id: Unique commit identifier
        system_id: System that created the commit
        task_id: Task identifier
        status: Always "confirmed" on success
        signature: Ed25519 exchange signature (always present on confirmed commits)
        cataloged_at: Exchange signature timestamp
        created_at: Commit creation timestamp
        message: Status message
        extensions: Reserved for future use (catalog field removed)
    """

    commit_id: str
    system_id: str
    task_id: str
    status: str
    signature: str | None = None
    cataloged_at: datetime | None = None
    created_at: datetime
    message: str
    extensions: dict[str, Any] = Field(default_factory=dict)  # catalog field removed


class Commit(BaseModel):
    """
    Full commit details returned by Exchange query endpoints (ATP v0.2.0+).

    Every commit is immediately confirmed with an Ed25519 exchange signature.
    Blockchain anchoring happens asynchronously (~1-2 minutes) and is reflected
    in the four flat ``blockchain_*`` fields once the batch worker has run.

    Attributes:
        id: Commit UUID
        system_id: System that created the commit
        organization_id: Organization UUID
        task_id: Task UUID
        proof_sketch: Embedded proof sketch object (not a string)
        status: "confirmed" or "failed" (never "pending" — exchange sig is synchronous)
        signature: Ed25519 exchange signature (always present on confirmed commits)
        cataloged_at: Exchange signature timestamp
        created_at: Commit creation timestamp
        blockchain_signature: Solana tx signature; ``None`` until batch anchored (~1-2 min)
        blockchain_confirmed_at: Block time of the Solana anchor; ``None`` until anchored
        blockchain_provider: "solana" or "mock"; ``None`` until anchored
        blockchain_catalog_url: Block explorer URL for the anchor tx; ``None`` until anchored
    """

    id: UUID
    system_id: str
    organization_id: UUID | None = None
    task_id: UUID
    proof_sketch: ProofSketch
    status: CommitStatus
    signature: str | None = None
    cataloged_at: datetime | None = None
    created_at: datetime

    # Flat blockchain anchor fields — null until the batch worker runs (~1-2 min after creation)
    blockchain_signature: str | None = None
    blockchain_confirmed_at: datetime | None = None
    blockchain_provider: str | None = None
    blockchain_catalog_url: str | None = None

    # Denormalized classification fields — extracted from proof_sketch at query time.
    # Null for legacy commits created before classification was required.
    # classification_capabilities defaults to ["question-answering"] in the backend
    # for legacy commits that have no classification in their proof_sketch.
    classification_occupation: str | None = None
    classification_capabilities: list[str] | None = None
    classification_work_activities: list[str] | None = None
    classification_description: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "Commit":
        """
        Create Commit from API response.

        In v0.2.0+ the Exchange returns proof_sketch as an embedded JSON object
        (not a string), so no json.loads() is needed.

        The ``catalog``, ``catalog_error``, ``retry_count``, and ``last_retry_at``
        fields are no longer present in API responses and are silently ignored if
        sent by an old server version.
        """
        # proof_sketch is already a dict (embedded JSON object in response)
        proof_sketch_data = data["proof_sketch"]
        if isinstance(proof_sketch_data, str):
            # Defensive: handle if server returns string for any reason
            import json

            proof_sketch_data = json.loads(proof_sketch_data)

        return cls(
            id=UUID(data["id"]) if isinstance(data["id"], str) else data["id"],
            system_id=data["system_id"],
            organization_id=(
                UUID(data["organization_id"]) if data.get("organization_id") else None
            ),
            task_id=UUID(data["task_id"]) if isinstance(data["task_id"], str) else data["task_id"],
            proof_sketch=ProofSketch.model_validate(proof_sketch_data),
            status=CommitStatus(data["status"]),
            signature=data.get("signature"),
            cataloged_at=(
                datetime.fromisoformat(data["cataloged_at"]) if data.get("cataloged_at") else None
            ),
            created_at=datetime.fromisoformat(data["created_at"]),
            # Flat blockchain anchor fields (null until the batch worker anchors the commit)
            blockchain_signature=data.get("blockchain_signature"),
            blockchain_confirmed_at=(
                datetime.fromisoformat(data["blockchain_confirmed_at"])
                if data.get("blockchain_confirmed_at")
                else None
            ),
            blockchain_provider=data.get("blockchain_provider"),
            blockchain_catalog_url=data.get("blockchain_catalog_url"),
            # Denormalized classification fields (present from Exchange v0.2.0+)
            classification_occupation=data.get("classification_occupation"),
            classification_capabilities=data.get("classification_capabilities"),
            classification_work_activities=data.get("classification_work_activities"),
            classification_description=data.get("classification_description"),
        )


class System(BaseModel):
    """
    System details from Exchange query.

    Attributes:
        system_id: System identifier hash
        type: System type ("agent", "construct", or "toolbox")
        name: System name
        capabilities: Declared capabilities (ATP v0.2.0)
        extensions: Protocol extensions (e.g., Sigstore credentials)
        created_at: Registration timestamp
    """

    system_id: str
    type: str
    name: str
    capabilities: list[Capability] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "System":
        """Create System from API response."""
        system_id = data.get("system_id") or data.get("id")
        if not system_id:
            raise ValueError("Response missing system_id/id field")

        return cls(
            system_id=system_id,
            type=data["type"],
            name=data["name"],
            capabilities=[Capability.model_validate(c) for c in data.get("capabilities", [])],
            extensions=data.get("extensions") or data.get("identity_ext") or {},
            created_at=datetime.fromisoformat(data["created_at"]),
        )


# ---------------------------------------------------------------------------
# Challenge-Response Models (agent-to-agent, full proof with content)
# ---------------------------------------------------------------------------


class ATPIdentity(BaseModel):
    """
    Legacy ATP system identity — kept for challenge-response challenger identification.

    In v0.2.0 the proof sketch uses system_uri (not ATPIdentity). This struct
    is still used in the challenge request to identify the challenging agent.
    """

    atp_exchange_url: str
    system_id: str
    system_type: str = "agent"
    system_name: str = ""
    extensions: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


# Alias for backwards compatibility in challenge code
ATPSystemIdentity = ATPIdentity


class ProofDataTask(BaseModel):
    """
    Full task data with content for challenge-response (agent stores locally).

    This is the content-bearing version of ATPMetadata + Cryptography.
    Agents store this locally and return it in response to atp.challenge.

    Attributes:
        system_uri: Full system URI
        system_type: System type
        task_id: Task identifier
        invocation: Full invocation content (method, trigger, input)
        invocation_hash: sha256: prefixed hash of canonical invocation JSON
        outcome: Full outcome content (response, status, actions)
        outcome_hash: sha256: prefixed hash of canonical outcome JSON
        timestamp: Task completion timestamp
        trust_level: Internal trust level after verification
    """

    system_uri: str
    system_type: str = "agent"
    task_id: str
    invocation: dict[str, Any]
    invocation_hash: str
    outcome: dict[str, Any]
    outcome_hash: str
    timestamp: datetime
    trust_level: ATPTrustLevel = ATPTrustLevel.UNVERIFIED

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class ProofData(BaseModel):
    """
    Full proof with content for challenge-response (ATP v0.2.0).

    Agents store this locally and return it when challenged.
    The Exchange never receives or stores this — it stores only ProofSketches.

    Attributes:
        task: Full task data with invocation and outcome content
        dependencies: Flat list of dependency references
    """

    task: ProofDataTask
    dependencies: list[Dependency] = Field(default_factory=list)

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class StoredProof(BaseModel):
    """
    Stored proof with TTL metadata for local proof management.

    Attributes:
        proof_data: Full proof with content (stored by agent for challenge-response)
        created_at: When the proof was stored
        expires_at: When the proof should be deleted (based on TTL)
    """

    proof_data: ProofData
    created_at: datetime
    expires_at: datetime

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


# ---------------------------------------------------------------------------
# Assessment Models (ATP Spec v0.1.0 Section 10)
# ---------------------------------------------------------------------------


class AssessmentDimension(str, Enum):
    """The three independent trust assessment axes."""

    INTEGRITY = "integrity"
    QUALITY = "quality"
    COMPLIANCE = "compliance"


class IntegrityAssessment(str, Enum):
    """Terminal verdicts for the integrity dimension."""

    VERIFIED = "verified"
    COMPROMISED = "compromised"


class QualityAssessment(str, Enum):
    """Terminal verdicts for the quality dimension (third-party evaluators only)."""

    PASSED = "passed"
    FAILED = "failed"


class ComplianceAssessment(str, Enum):
    """Terminal verdicts for the compliance dimension (third-party auditors only)."""

    COMPLIANT = "compliant"
    NON_COMPLIANT = "non-compliant"


# Neutral (absent) states — never stored, returned only for UI rendering.
NEUTRAL_BY_DIMENSION: dict[AssessmentDimension, str] = {
    AssessmentDimension.INTEGRITY: "unverified",
    AssessmentDimension.QUALITY: "unevaluated",
    AssessmentDimension.COMPLIANCE: "unassessed",
}


class AssessmentRecord(BaseModel):
    """
    A single trust assessment record returned from the Exchange.

    Attributes:
        assessment_id: Unique assessment UUID
        assessor_system_id: System that submitted this assessment
        assessor_task_id: Optional: the assessor's own ATP task_id (future: required)
        assessed_system_id: System whose task was assessed
        assessed_task_id: The assessed task UUID
        dimension: Which trust axis (integrity / quality / compliance)
        assessment: The verdict (e.g. 'verified', 'passed', 'compliant')
        assessed_at: When the assessment was performed
        created_at: When the record was stored in the Exchange
    """

    assessment_id: str
    assessor_system_id: str
    assessor_task_id: str | None = None
    assessed_system_id: str
    assessed_task_id: str
    dimension: AssessmentDimension
    assessment: str  # one of the per-dimension verdict strings
    assessed_at: datetime
    created_at: datetime

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "AssessmentRecord":
        """Create AssessmentRecord from raw API response dict."""
        return cls(
            assessment_id=data["assessment_id"],
            assessor_system_id=data["assessor_system_id"],
            assessor_task_id=data.get("assessor_task_id"),
            assessed_system_id=data["assessed_system_id"],
            assessed_task_id=str(data["assessed_task_id"]),
            dimension=AssessmentDimension(data["dimension"]),
            assessment=data["assessment"],
            assessed_at=datetime.fromisoformat(data["assessed_at"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


class AssessmentsResponse(BaseModel):
    """
    Response from GET /api/v1/systems/:id/tasks/:task_id/assessments.

    Attributes:
        system_id: Assessed system identifier
        task_id: Assessed task identifier
        assessments: All assessment records for this task (may be empty)
    """

    system_id: str
    task_id: str
    assessments: list[AssessmentRecord] = Field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "AssessmentsResponse":
        """Create AssessmentsResponse from raw API response dict."""
        return cls(
            system_id=data["system_id"],
            task_id=data["task_id"],
            assessments=[
                AssessmentRecord.from_api_response(a) for a in data.get("assessments", [])
            ],
        )


# ─── ATP Request Metadata (v0.2.0+) ────────────────────────────────────────


class ATPRequestMeta(BaseModel):
    """
    ATP metadata carried in every request from an ATP-compliant caller.

    Mirrors ``ATPMetadata`` in ProofSketch for protocol consistency:

      ProofSketch.atp_metadata → who committed this proof + task identity
      Request.atp_metadata     → who is making this request + which upstream
                                  proofs the recipient should challenge

    The ``dependencies`` field reuses the existing ``Dependency`` model —
    the same model that appears in ``ProofSketch.dependencies``.  In request
    context, ``Dependency.challenge_url`` is set so the recipient knows where
    to send the JSON-RPC challenge; ``system_uri`` is optional (the recipient
    learns it from the challenged proof).  In proof context, ``challenge_url``
    is ``None`` (it is not needed after the fact).

    Protocol usage::

        # Caller (Construct) builds request:
        {
          "report": "...",
          "atp_metadata": {
            "caller_task_id": "C-1",
            "dependencies": [
              { "task_id": "A-w-1", "challenge_url": "http://worker:8202" }
            ]
          }
        }

        # Recipient (@atp_agent pre-processing hook):
        #   1. Parses atp_metadata.dependencies from inbound request/message
        #   2. For each dep with challenge_url set: challenges + verifies proof
        #   3. Appends verified ProofData to self._atp_dependencies
        #   4. Calls execute()/ainvoke() — domain logic sees no ATP calls
    """

    caller_task_id: str | None = None  # caller's current ATP task id (audit trail)
    dependencies: list[Dependency] = []
