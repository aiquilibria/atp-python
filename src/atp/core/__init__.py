"""
ATP Core Module — ATP v0.1.0

Framework-agnostic implementation of the Agent Trust Protocol.
"""

from atp.core.client import ATPClient
from atp.core.config import ATPConfig
from atp.core.exceptions import (
    ATPAuthError,
    ATPCommitError,
    ATPError,
    ATPNetworkError,
    ATPQueryError,
    ATPRegistrationError,
    ATPVerificationError,
)
from atp.core.hashing import (
    compute_data_hash,
    compute_dependencies_hash,
    compute_proof_hash,
    hash_with_prefix,
)
from atp.core.models import (
    DEFAULT_CLASSIFICATION,
    NEUTRAL_BY_DIMENSION,
    AssessmentDimension,
    AssessmentRecord,
    AssessmentsResponse,
    ATPIdentity,
    ATPMetadata,
    ATPSystemIdentity,
    ATPTrustLevel,
    Capability,
    Commit,
    CommitResponse,
    CommitStatus,
    ComplianceAssessment,
    Cryptography,
    Dependency,
    DependencyEvaluation,
    IntegrityAssessment,
    Ontology,
    ProofData,
    ProofDataTask,
    ProofSketch,
    QualityAssessment,
    StoredProof,
    System,
    SystemRegistration,
)
from atp.core.storage import InMemoryProofStore, ProofStore, SQLiteProofStore
from atp.core.utils import ATPResponseWrapper, ATPTaskContext, atp_response, atp_task

__all__ = [
    # Client
    "ATPClient",
    # Config
    "ATPConfig",
    # Exceptions
    "ATPError",
    "ATPAuthError",
    "ATPCommitError",
    "ATPNetworkError",
    "ATPQueryError",
    "ATPRegistrationError",
    "ATPVerificationError",
    # Hashing
    "compute_data_hash",
    "compute_dependencies_hash",
    "compute_proof_hash",
    "hash_with_prefix",
    # Models — proof sketch (committed to Exchange)
    "ATPMetadata",
    "Capability",
    "Cryptography",
    "DEFAULT_CLASSIFICATION",
    "Dependency",
    "DependencyEvaluation",
    "Ontology",
    "ProofSketch",
    # Models — full proof (stored locally for challenge-response)
    "ATPIdentity",
    "ATPSystemIdentity",
    "ATPTrustLevel",
    "ProofData",
    "ProofDataTask",
    "StoredProof",
    # Models — assessments
    "AssessmentDimension",
    "AssessmentRecord",
    "AssessmentsResponse",
    "ComplianceAssessment",
    "IntegrityAssessment",
    "NEUTRAL_BY_DIMENSION",
    "QualityAssessment",
    # Models — exchange API
    "Commit",
    "CommitResponse",
    "CommitStatus",
    "System",
    "SystemRegistration",
    # Storage
    "ProofStore",
    "InMemoryProofStore",
    "SQLiteProofStore",
    # Utilities
    "atp_task",
    "atp_response",
    "ATPTaskContext",
    "ATPResponseWrapper",
]
