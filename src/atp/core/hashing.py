"""
ATP Hashing Utilities

SHA-256 helpers for computing content hashes (invocation, outcome, dependencies)
and the ephemeral proof hash used by the Exchange for signing and blockchain anchoring.

Public API
----------
- ``hash_with_prefix(data)``          → ``"sha256:<hex>"``  — use for invocation / outcome hashes
- ``compute_data_hash(data)``         → ``"<hex>"``         — plain hex, kept for internal use
- ``compute_dependencies_hash(deps)`` → ``"sha256:<hex>"``  — canonical hash of dependencies list
- ``compute_proof_hash(...)``         → ``"<hex>"``         — ephemeral proof hash
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from atp.core.models import Dependency, DependencyEvaluation, ProofSketch


def compute_sha256(data: str) -> str:
    """
    Compute SHA-256 of a UTF-8 string.

    Returns:
        64-char lowercase hex digest (no prefix).
    """
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def compute_data_hash(data: Any) -> str:
    """
    Compute SHA-256 of arbitrary data using canonical JSON serialisation.

    Uses sorted keys and compact separators so that key order never affects
    the resulting hash.

    Returns:
        64-char lowercase hex digest (no prefix).

    Examples::

        hash1 = compute_data_hash({"a": 1, "b": 2})
        hash2 = compute_data_hash({"b": 2, "a": 1})
        assert hash1 == hash2
    """
    if isinstance(data, str):
        return compute_sha256(data)
    elif isinstance(data, (dict, list)):
        normalized = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return compute_sha256(normalized)
    else:
        return compute_sha256(str(data))


def hash_with_prefix(data: Any) -> str:
    """
    Compute SHA-256 of data and return with the ``sha256:`` algorithm prefix.

    ATP requires all hashes exchanged between agents and the Exchange to include
    an explicit algorithm prefix so that the hash format is self-describing and
    forward-compatible with future algorithms.

    Use this function for **all** content hashes in a proof sketch:
    invocation hashes, outcome hashes, dependency hashes, etc.::

        proof_sketch = ProofSketch(
            ...
            cryptography=Cryptography(
                invocation_hash=hash_with_prefix(invocation_dict),
                outcome_hash=hash_with_prefix(outcome_dict),
                dependencies_hash=compute_dependencies_hash(dependencies),
            ),
        )

    Args:
        data: String, dict, list, or anything serialisable to canonical JSON.

    Returns:
        ``"sha256:" + 64-char lowercase hex digest``
    """
    return "sha256:" + compute_data_hash(data)


# ---------------------------------------------------------------------------
# Dependencies hash
#
# ATP §5.3 requires canonical JSON serialisation: object keys sorted
# alphabetically at all nesting levels, no insignificant whitespace, UTF-8
# encoding.  This ensures hash values are byte-for-byte reproducible across
# all agents and SDK implementations regardless of language or library.
#
# Omitempty rules (per spec):
#   - "evaluations" key omitted when the list is empty
#   - "evaluation_policy" key omitted when None/empty
# ---------------------------------------------------------------------------


def _format_datetime_rfc3339nano(dt: datetime) -> str:
    """
    Format a datetime per ATP §5.3 canonical timestamp requirements.

    ATP §5.3 requires timestamps to be serialised as ISO 8601 / RFC 3339 UTC
    with:
    - ``Z`` suffix (not ``+00:00``)
    - Fractional seconds with trailing zeros stripped

    This function produces up to microsecond precision (6 significant digits
    maximum), which is sufficient for all practical ATP timestamp values.
    """
    # Normalise to UTC without tzinfo offset so strftime produces bare values
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)

    us = dt.microsecond
    if us == 0:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    frac = f"{us:06d}".rstrip("0")
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{frac}Z")


def _dep_eval_to_canonical(ev: "DependencyEvaluation | dict[str, Any]") -> dict[str, Any]:
    """
    Serialise a DependencyEvaluation to canonical form per ATP §5.3.

    ``evaluation_policy`` is omitted when empty/None (ATP omitempty rule).
    """
    if isinstance(ev, dict):
        d: dict[str, Any] = {"evaluation_type": ev["evaluation_type"]}
        if ev.get("evaluation_policy"):
            d["evaluation_policy"] = ev["evaluation_policy"]
        d["evaluation_result"] = ev["evaluation_result"]
        d["evaluated_at"] = ev["evaluated_at"]
    else:
        d = {"evaluation_type": ev.evaluation_type}
        if ev.evaluation_policy:
            d["evaluation_policy"] = ev.evaluation_policy
        d["evaluation_result"] = ev.evaluation_result
        d["evaluated_at"] = _format_datetime_rfc3339nano(ev.evaluated_at)
    return d


def _dep_to_canonical(dep: "Dependency | dict[str, Any]") -> dict[str, Any]:
    """
    Serialise a Dependency to canonical form per ATP §5.3.

    ``evaluations`` is omitted when the list is empty (ATP omitempty rule).
    """
    if isinstance(dep, dict):
        d: dict[str, Any] = {
            "system_uri": dep["system_uri"],
            "task_id": dep["task_id"],
        }
        evals: list = dep.get("evaluations") or []
    else:
        d = {"system_uri": dep.system_uri, "task_id": dep.task_id}
        evals = dep.evaluations or []

    if evals:
        d["evaluations"] = [_dep_eval_to_canonical(ev) for ev in evals]
    return d


def compute_dependencies_hash(
    dependencies: "list[Dependency] | list[dict[str, Any]] | None" = None,
) -> str:
    """
    Compute the canonical SHA-256 hash of the dependencies list (ATP §5.3).

    Keys are sorted alphabetically at every nesting level and empty collections
    are omitted, producing a canonical form that is byte-for-byte reproducible
    across all ATP-compliant implementations.

    An empty or ``None`` dependencies list always hashes the literal ``"[]"``
    string:

    * ``compute_dependencies_hash(None)`` == ``compute_dependencies_hash([])``
      == ``sha256:`` + SHA-256(``"[]"``)

    This means every new commit carries a non-``None`` ``dependencies_hash``
    regardless of whether it has actual dependencies.  A ``None``
    ``Cryptography.dependencies_hash`` only appears on *legacy* commits that were
    created before this field was introduced.

    Args:
        dependencies: List of ``Dependency`` objects or dicts, or ``None``.

    Returns:
        ``"sha256:" + 64-char lowercase hex digest``
    """
    if not dependencies:
        dependencies = []
    canonical = json.dumps(
        [_dep_to_canonical(d) for d in dependencies],
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Proof hash — ephemeral; never stored; always recomputed on demand.
# ---------------------------------------------------------------------------


@overload
def compute_proof_hash(sketch: "ProofSketch") -> str: ...


@overload
def compute_proof_hash(
    system_uri: str,
    task_id: str,
    invocation_hash: str,
    outcome_hash: str,
    dependencies_hash: "str | None",
    timestamp: datetime,
) -> str: ...


def compute_proof_hash(  # type: ignore[misc]
    system_uri_or_sketch: "str | ProofSketch",
    task_id: str | None = None,
    invocation_hash: str | None = None,
    outcome_hash: str | None = None,
    dependencies_hash: str | None = None,
    timestamp: datetime | None = None,
) -> str:
    """
    Compute the ephemeral proof hash.

    Accepts either a ``ProofSketch`` object (convenience form) or individual
    string/datetime arguments::

        # From a ProofSketch (dependencies_hash taken from cryptography field)
        h = compute_proof_hash(proof_sketch)

        # From individual parts
        h = compute_proof_hash(system_uri, task_id, inv_hash, out_hash, deps_hash, ts)

    **Formula (new commits with dependencies_hash set):**::

        SHA256(system_uri | task_id | inv_hash | out_hash | deps_hash | timestamp)

    **Legacy formula (old commits where dependencies_hash is None or empty):**::

        SHA256(system_uri | task_id | inv_hash | out_hash | timestamp)

    The legacy formula is used automatically when ``dependencies_hash`` is
    ``None`` or empty — this ensures ``verify_signature()`` continues to work
    for commits created before ``dependencies_hash`` was introduced.
    """
    if hasattr(system_uri_or_sketch, "atp_metadata"):
        sketch = system_uri_or_sketch
        return _compute_proof_hash_parts(
            sketch.atp_metadata.system_uri,  # type: ignore[union-attr]
            sketch.atp_metadata.task_id,  # type: ignore[union-attr]
            sketch.cryptography.invocation_hash,  # type: ignore[union-attr]
            sketch.cryptography.outcome_hash,  # type: ignore[union-attr]
            sketch.cryptography.dependencies_hash,  # type: ignore[union-attr]
            sketch.timestamp,  # type: ignore[union-attr]
        )
    if task_id is None or invocation_hash is None or outcome_hash is None or timestamp is None:
        raise ValueError(
            "compute_proof_hash requires task_id, invocation_hash, outcome_hash, "
            "and timestamp when not called with a ProofSketch"
        )
    return _compute_proof_hash_parts(
        str(system_uri_or_sketch),
        task_id,
        invocation_hash,
        outcome_hash,
        dependencies_hash,
        timestamp,
    )


def _compute_proof_hash_parts(
    system_uri: str,
    task_id: str,
    invocation_hash: str,
    outcome_hash: str,
    dependencies_hash: str | None,
    timestamp: datetime,
) -> str:
    """
    Core proof hash computation (ATP §5.3).

    When ``dependencies_hash`` is non-empty, uses the 5-field formula::

        SHA256(system_uri | task_id | inv_hash | out_hash | deps_hash | timestamp)

    When ``dependencies_hash`` is ``None`` or ``""`` (legacy commits), falls back
    to the original 4-field formula::

        SHA256(system_uri | task_id | inv_hash | out_hash | timestamp)

    This ensures ``verify_signature()`` works for both old and new commits.

    Args:
        system_uri: Full system URI.
        task_id: UUID string of the task.
        invocation_hash: ``"sha256:..."`` prefixed hash of the invocation.
        outcome_hash: ``"sha256:..."`` prefixed hash of the outcome.
        dependencies_hash: ``"sha256:..."`` prefixed hash of the dependencies list,
            or ``None`` / ``""`` for legacy commits.
        timestamp: Task completion datetime (timezone-aware, UTC preferred).

    Returns:
        64-char lowercase hex digest (**no** ``sha256:`` prefix).
    """
    # Format timestamp per ATP §5.3: ISO 8601 / RFC 3339 UTC, Z suffix,
    # fractional seconds with trailing zeros stripped.
    us = timestamp.microsecond
    if us == 0:
        ts_str = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        frac = f"{us:06d}".rstrip("0")
        ts_str = timestamp.strftime(f"%Y-%m-%dT%H:%M:%S.{frac}Z")

    if dependencies_hash:
        # New formula — binds identity + AI-BOM + both content hashes + timestamp
        message = (
            f"{system_uri}|{task_id}|{invocation_hash}|{outcome_hash}|{dependencies_hash}|{ts_str}"
        )
    else:
        # Legacy formula — used for commits created before dependencies_hash was introduced
        message = f"{system_uri}|{task_id}|{invocation_hash}|{outcome_hash}|{ts_str}"

    return hashlib.sha256(message.encode("utf-8")).hexdigest()
