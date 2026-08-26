"""
Agent Trust Protocol (ATP) Python SDK

Framework-agnostic core with optional framework adapters.

Optional adapters are imported lazily so that installing only the base
package (without `a2a`, `langchain`, `mcp`, etc. extras) does not raise
``ImportError`` at import time.
"""

# ---------------------------------------------------------------------------
# Core — always available
# ---------------------------------------------------------------------------
from atp.adapters import FrameworkAdapter, MinimalAdapter
from atp.core import (
    DEFAULT_CLASSIFICATION,
    ATPAuthError,
    ATPClient,
    ATPCommitError,
    ATPConfig,
    ATPError,
    ATPMetadata,
    ATPNetworkError,
    ATPQueryError,
    ATPRegistrationError,
    ATPResponseWrapper,
    ATPTaskContext,
    ATPTrustLevel,
    ATPVerificationError,
    Capability,
    Commit,
    CommitResponse,
    CommitStatus,
    Cryptography,
    Dependency,
    InMemoryProofStore,
    Ontology,
    ProofData,
    ProofDataTask,
    ProofSketch,
    ProofStore,
    SQLiteProofStore,
    StoredProof,
    System,
    SystemRegistration,
    atp_response,
    atp_task,
    compute_data_hash,
    compute_proof_hash,
    hash_with_prefix,
)

__version__ = "0.2.0"

# ---------------------------------------------------------------------------
# Optional adapters — imported only when the relevant extra is installed.
# Accessing e.g. ``atp.atp_agent`` without the a2a extra raises a clear
# AttributeError rather than an ImportError at package import time.
# ---------------------------------------------------------------------------
try:
    from atp.adapters.a2a import ATPRequestHandler, atp_agent  # noqa: F401

    _has_a2a = True
except ImportError:
    _has_a2a = False

__all__ = [
    # Core Client
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
    # Models — classification
    "Capability",
    "DEFAULT_CLASSIFICATION",
    "Ontology",
    # Models
    "ATPTrustLevel",
    "ATPMetadata",
    "Commit",
    "CommitResponse",
    "CommitStatus",
    "Cryptography",
    "Dependency",
    "ProofData",
    "ProofDataTask",
    "ProofSketch",
    "StoredProof",
    "System",
    "SystemRegistration",
    # Storage
    "ProofStore",
    "InMemoryProofStore",
    "SQLiteProofStore",
    # Hashing
    "compute_data_hash",
    "hash_with_prefix",
    "compute_proof_hash",
    # Utilities
    "atp_task",
    "atp_response",
    "ATPTaskContext",
    "ATPResponseWrapper",
    # Adapter Base
    "FrameworkAdapter",
    "MinimalAdapter",
]

# A2A adapter symbols are only added to __all__ when the extra is installed.
if _has_a2a:
    __all__ += ["atp_agent", "ATPRequestHandler"]
