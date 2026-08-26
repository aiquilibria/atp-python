"""
Tests for ATP hashing utilities
"""

import hashlib
import json
from datetime import UTC, datetime

from atp.core.hashing import compute_data_hash, compute_dependencies_hash, compute_proof_hash
from atp.core.models import Dependency, DependencyEvaluation


class TestComputeResponseHash:
    """Test response hash computation."""

    def test_hash_simple_string(self):
        """Test hashing a simple string."""
        result = compute_data_hash("Hello, world!")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex digest is 64 characters

    def test_hash_empty_string(self):
        """Test hashing an empty string."""
        result = compute_data_hash("")
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_unicode_string(self):
        """Test hashing a Unicode string."""
        result = compute_data_hash("Hello 世界 🌍")
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_multiline_string(self):
        """Test hashing a multi-line string."""
        text = """Line 1
Line 2
Line 3"""
        result = compute_data_hash(text)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_deterministic(self):
        """Test that hashing is deterministic."""
        text = "Test message"
        hash1 = compute_data_hash(text)
        hash2 = compute_data_hash(text)
        assert hash1 == hash2

    def test_hash_different_inputs(self):
        """Test that different inputs produce different hashes."""
        hash1 = compute_data_hash("input1")
        hash2 = compute_data_hash("input2")
        assert hash1 != hash2

    def test_hash_dict_response(self):
        """Test hashing a dictionary response."""
        response = {"status": "success", "result": 42}
        result = compute_data_hash(response)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_list_response(self):
        """Test hashing a list response."""
        response = [1, 2, 3, "a", "b", "c"]
        result = compute_data_hash(response)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_other_types(self):
        """Test hashing other types (int, bool, etc)."""
        # Test integer
        result_int = compute_data_hash(42)
        assert isinstance(result_int, str)
        assert len(result_int) == 64

        # Test boolean
        result_bool = compute_data_hash(True)
        assert isinstance(result_bool, str)
        assert len(result_bool) == 64

        # Test None
        result_none = compute_data_hash(None)
        assert isinstance(result_none, str)
        assert len(result_none) == 64


class TestComputeTaskHash:
    """Test task hash computation."""

    def test_hash_simple_dict(self):
        """Test hashing a simple dictionary."""
        task_data = {"task_id": "123", "input": "test"}
        result = compute_data_hash(task_data)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_empty_dict(self):
        """Test hashing an empty dictionary."""
        result = compute_data_hash({})
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_nested_dict(self):
        """Test hashing a nested dictionary."""
        task_data = {
            "task_id": "123",
            "params": {"a": 1, "b": 2},
            "metadata": {"timestamp": "2026-01-19"},
        }
        result = compute_data_hash(task_data)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_with_list(self):
        """Test hashing a dictionary containing lists."""
        task_data = {"items": [1, 2, 3], "tags": ["a", "b", "c"]}
        result = compute_data_hash(task_data)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hash_deterministic(self):
        """Test that hashing is deterministic."""
        task_data = {"task_id": "123", "input": "test"}
        hash1 = compute_data_hash(task_data)
        hash2 = compute_data_hash(task_data)
        assert hash1 == hash2

    def test_hash_different_dicts(self):
        """Test that different dictionaries produce different hashes."""
        hash1 = compute_data_hash({"key": "value1"})
        hash2 = compute_data_hash({"key": "value2"})
        assert hash1 != hash2

    def test_hash_key_order_independent(self):
        """Test that key order doesn't affect hash (using sort_keys)."""
        dict1 = {"a": 1, "b": 2, "c": 3}
        dict2 = {"c": 3, "a": 1, "b": 2}
        hash1 = compute_data_hash(dict1)
        hash2 = compute_data_hash(dict2)
        # Hashes should be the same since JSON serialization sorts keys
        assert hash1 == hash2


class TestComputeDependenciesHash:
    """Tests for compute_dependencies_hash."""

    def test_none_produces_real_hash(self):
        """None input returns a sha256:-prefixed hash, not None."""
        result = compute_dependencies_hash(None)
        assert result.startswith("sha256:")
        assert len(result) == len("sha256:") + 64

    def test_empty_list_produces_real_hash(self):
        """Empty list returns a sha256:-prefixed hash, not None."""
        result = compute_dependencies_hash([])
        assert result.startswith("sha256:")
        assert len(result) == len("sha256:") + 64

    def test_none_and_empty_list_are_equal(self):
        """None and [] produce identical hashes — both mean 'no dependencies'."""
        assert compute_dependencies_hash(None) == compute_dependencies_hash([])

    def test_empty_deps_matches_sha256_of_empty_json_array(self):
        """Empty deps hash == sha256:SHA256('[]') — matches Go's ComputeDependenciesHash."""
        expected_hex = hashlib.sha256(b"[]").hexdigest()
        assert compute_dependencies_hash([]) == f"sha256:{expected_hex}"

    def test_deterministic(self):
        """Same input always produces the same hash."""
        dep = Dependency(
            system_uri="https://exchange.example.com/systems/sys_abc",
            task_id="task-123",
        )
        h1 = compute_dependencies_hash([dep])
        h2 = compute_dependencies_hash([dep])
        assert h1 == h2

    def test_different_deps_produce_different_hashes(self):
        """Different dependency lists produce different hashes."""
        dep_a = Dependency(system_uri="https://exchange.example.com/systems/sys_a", task_id="t1")
        dep_b = Dependency(system_uri="https://exchange.example.com/systems/sys_b", task_id="t2")
        assert compute_dependencies_hash([dep_a]) != compute_dependencies_hash([dep_b])

    def test_single_dep_without_evaluations_omits_evaluations_field(self):
        """
        A dependency with no evaluations must produce the same hash as
        Go's json.Marshal, which omits the evaluations field (omitempty).

        Expected JSON: [{"system_uri":"...","task_id":"..."}]
        """
        dep = Dependency(
            system_uri="https://exchange.example.com/systems/sys_abc",
            task_id="550e8400-e29b-41d4-a716-446655440000",
        )
        expected_json = json.dumps(
            [{"system_uri": "https://exchange.example.com/systems/sys_abc",
              "task_id": "550e8400-e29b-41d4-a716-446655440000"}],
            separators=(",", ":"),
        )
        expected = "sha256:" + hashlib.sha256(expected_json.encode()).hexdigest()
        assert compute_dependencies_hash([dep]) == expected

    def test_dep_with_evaluation_included(self):
        """Dependencies with evaluations include the evaluations field."""
        ev = DependencyEvaluation(
            evaluation_type="integrity",
            evaluation_result="verified",
            evaluated_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )
        dep = Dependency(
            system_uri="https://exchange.example.com/systems/sys_abc",
            task_id="task-123",
            evaluations=[ev],
        )
        result = compute_dependencies_hash([dep])
        # Must be different from a dep without evaluations
        dep_no_eval = Dependency(
            system_uri="https://exchange.example.com/systems/sys_abc",
            task_id="task-123",
        )
        assert result != compute_dependencies_hash([dep_no_eval])

    def test_dep_with_evaluation_policy_omitted_when_none(self):
        """evaluation_policy is omitted from the hash when None (matches Go omitempty)."""
        ev_with_policy = DependencyEvaluation(
            evaluation_type="integrity",
            evaluation_policy="strict",
            evaluation_result="verified",
            evaluated_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )
        ev_without_policy = DependencyEvaluation(
            evaluation_type="integrity",
            evaluation_result="verified",
            evaluated_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )
        dep_uri = "https://exchange.example.com/systems/sys_abc"
        dep_with = Dependency(system_uri=dep_uri, task_id="t1", evaluations=[ev_with_policy])
        dep_without = Dependency(system_uri=dep_uri, task_id="t1", evaluations=[ev_without_policy])
        # Different policy → different hash
        assert compute_dependencies_hash([dep_with]) != compute_dependencies_hash([dep_without])

    def test_accepts_dict_form(self):
        """compute_dependencies_hash also accepts plain dicts (useful for testing)."""
        dep_dict = {"system_uri": "https://exchange.example.com/systems/sys_x", "task_id": "t1"}
        dep_obj = Dependency(
            system_uri="https://exchange.example.com/systems/sys_x", task_id="t1"
        )
        assert compute_dependencies_hash([dep_dict]) == compute_dependencies_hash([dep_obj])

    def test_multiple_deps_order_matters(self):
        """Dependency order affects the hash (list is ordered, not a set)."""
        dep_a = Dependency(system_uri="https://exchange.example.com/systems/sys_a", task_id="t1")
        dep_b = Dependency(system_uri="https://exchange.example.com/systems/sys_b", task_id="t2")
        assert compute_dependencies_hash([dep_a, dep_b]) != compute_dependencies_hash(
            [dep_b, dep_a]
        )

    def test_dep_with_evaluation_canonical_key_order(self):
        """
        Dependency with evaluations must produce a hash from alphabetically-sorted
        keys at every nesting level (ATP §5.3).

        Canonical JSON for a dep with one integrity evaluation must be:
          [{"evaluations":[{"evaluated_at":"...","evaluation_result":"...","evaluation_type":"..."}],
            "system_uri":"...","task_id":"..."}]

        Keys are sorted alphabetically:
          dep-level:  evaluations < system_uri < task_id  (e < s < t)
          eval-level: evaluated_at < evaluation_result < evaluation_type
                      ("d" < "r" < "t" at position 11 after "evaluation_")
        """
        ev = DependencyEvaluation(
            evaluation_type="integrity",
            evaluation_result="verified",
            evaluated_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )
        dep = Dependency(
            system_uri="https://exchange.example.com/systems/sys_abc",
            task_id="550e8400-e29b-41d4-a716-446655440000",
            evaluations=[ev],
        )
        expected_json = json.dumps(
            [
                {
                    "evaluations": [
                        {
                            "evaluated_at": "2026-03-01T12:00:00Z",
                            "evaluation_result": "verified",
                            "evaluation_type": "integrity",
                        }
                    ],
                    "system_uri": "https://exchange.example.com/systems/sys_abc",
                    "task_id": "550e8400-e29b-41d4-a716-446655440000",
                }
            ],
            separators=(",", ":"),
        )
        expected = "sha256:" + hashlib.sha256(expected_json.encode()).hexdigest()
        assert compute_dependencies_hash([dep]) == expected

    def test_dep_with_evaluation_policy_canonical_key_order(self):
        """
        When evaluation_policy is present, sorted key order at eval level is:
          evaluated_at < evaluation_policy < evaluation_result < evaluation_type
          ("d" < "p" < "r" < "t" at position 11 after "evaluation_")
        """
        ev = DependencyEvaluation(
            evaluation_type="quality",
            evaluation_policy="internal-qa-v1",
            evaluation_result="passed",
            evaluated_at=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )
        dep = Dependency(
            system_uri="https://exchange.example.com/systems/sys_abc",
            task_id="550e8400-e29b-41d4-a716-446655440000",
            evaluations=[ev],
        )
        expected_json = json.dumps(
            [
                {
                    "evaluations": [
                        {
                            "evaluated_at": "2026-03-01T12:00:00Z",
                            "evaluation_policy": "internal-qa-v1",
                            "evaluation_result": "passed",
                            "evaluation_type": "quality",
                        }
                    ],
                    "system_uri": "https://exchange.example.com/systems/sys_abc",
                    "task_id": "550e8400-e29b-41d4-a716-446655440000",
                }
            ],
            separators=(",", ":"),
        )
        expected = "sha256:" + hashlib.sha256(expected_json.encode()).hexdigest()
        assert compute_dependencies_hash([dep]) == expected


class TestComputeProofHash:
    """Tests for the updated compute_proof_hash (with dependencies_hash param)."""

    def test_hash(self):
        """New formula includes dependencies_hash between outcome_hash and timestamp."""
        ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        deps_hash = compute_dependencies_hash([])
        result = compute_proof_hash(
            "https://exchange.example.com/systems/sys_abc",
            "task-123",
            "sha256:aabbcc",
            "sha256:ddeeff",
            deps_hash,
            ts,
        )
        # Must be a 64-char hex string
        assert isinstance(result, str)
        assert len(result) == 64


    def test_proof_sketch_form_uses_cryptography_dependencies_hash(self):
        """ProofSketch convenience form reads dependencies_hash from cryptography."""
        from atp.core.models import (
            ATPMetadata,
            Capability,
            Cryptography,
            Ontology,
            ProofSketch,
        )

        ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        deps_hash = compute_dependencies_hash([])
        sketch = ProofSketch(
            atp_metadata=ATPMetadata(
                spec_version="0.2.0",
                system_uri="https://exchange.example.com/systems/sys_abc",
                system_type="agent",
                task_id="task-123",
                classification=Capability(
                    ontology=Ontology(ontology_uri="https://agenttrustprotocol.org/ontology/v0.2.0")
                ),
            ),
            cryptography=Cryptography(
                invocation_hash="sha256:aabbcc",
                outcome_hash="sha256:ddeeff",
                dependencies_hash=deps_hash,
            ),
            timestamp=ts,
        )
        # ProofSketch convenience form
        h_sketch = compute_proof_hash(sketch)
        # Explicit args form
        h_explicit = compute_proof_hash(
            "https://exchange.example.com/systems/sys_abc",
            "task-123",
            "sha256:aabbcc",
            "sha256:ddeeff",
            deps_hash,
            ts,
        )
        assert h_sketch == h_explicit

