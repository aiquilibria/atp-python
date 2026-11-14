"""Negative-path verification tests: the challenge-verify flow must reject
records tampered after commitment and proofs that diverge from their
committed sketch.

These exercise the record-integrity boundary: a record modified after the
fact fails hash verification (locally, before any Exchange round-trip); a
disclosed proof inconsistent with the committed sketch fails Exchange
consistency; and an intact record verifies — including one that faithfully
records a wrong answer, whose correctness is Quality's concern, not
Integrity's.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atp.core import ATPClient, ATPConfig
from atp.core.hashing import hash_with_prefix
from atp.core.models import ATPTrustLevel, ProofData, ProofDataTask, StoredProof


def _make_stored_proof(response_text: str = "4") -> StoredProof:
    inv = {
        "method": "query",
        "trigger": {"type": "user_message"},
        "input": {"query": "What is 2+2?"},
    }
    out = {
        "response": {"text": response_text},
        "status": "success",
        "actions": [],
        "error": None,
    }
    task = ProofDataTask(
        system_uri="http://localhost:8080/systems/sys_test",
        system_type="agent",
        task_id="task-verify-negative",
        invocation=inv,
        invocation_hash=hash_with_prefix(inv),
        outcome=out,
        outcome_hash=hash_with_prefix(out),
        timestamp=datetime.now(UTC),
        trust_level=ATPTrustLevel.UNVERIFIED,
    )
    now = datetime.now(UTC)
    return StoredProof(
        proof_data=ProofData(task=task, dependencies=[]),
        created_at=now,
        expires_at=now + timedelta(seconds=60),
    )


def _client() -> ATPClient:
    return ATPClient(ATPConfig(api_key="test-key", exchange_url="http://localhost:8080"))


def _mock_commit(inv_hash: str, out_hash: str, status: str = "confirmed"):
    return SimpleNamespace(
        proof_sketch=SimpleNamespace(
            cryptography=SimpleNamespace(
                invocation_hash=inv_hash, outcome_hash=out_hash
            )
        ),
        status=SimpleNamespace(value=status),
    )


@pytest.mark.asyncio
async def test_tampered_outcome_fails_before_any_network_call():
    proof = _make_stored_proof()
    proof.proof_data.task.outcome["response"]["text"] = "5"  # tamper after commit
    client = _client()
    client.get_commit_by_system_and_task = AsyncMock(
        side_effect=AssertionError("local mismatch must short-circuit")
    )
    _, verified, message = await client._verify(proof)
    assert not verified
    assert "outcome hash mismatch" in message.lower()


@pytest.mark.asyncio
async def test_tampered_invocation_fails_before_any_network_call():
    proof = _make_stored_proof()
    proof.proof_data.task.invocation["input"]["query"] = "What is 3+3?"
    client = _client()
    client.get_commit_by_system_and_task = AsyncMock(
        side_effect=AssertionError("local mismatch must short-circuit")
    )
    _, verified, message = await client._verify(proof)
    assert not verified
    assert "invocation hash mismatch" in message.lower()


@pytest.mark.asyncio
async def test_disclosed_proof_must_match_committed_sketch():
    proof = _make_stored_proof()
    task = proof.proof_data.task
    client = _client()
    # Internally consistent proof, but the Exchange holds a different sketch:
    # the disclosed record is not the committed one.
    client.get_commit_by_system_and_task = AsyncMock(
        return_value=_mock_commit(task.invocation_hash, "sha256:" + "0" * 64)
    )
    _, verified, message = await client._verify(proof)
    assert not verified
    assert "doesn't match atp exchange" in message.lower()


@pytest.mark.asyncio
async def test_intact_record_verifies_even_when_the_answer_is_wrong():
    # Faithfully recorded wrong answer: Integrity verifies the account,
    # not the arithmetic.
    proof = _make_stored_proof(response_text="5")
    task = proof.proof_data.task
    client = _client()
    client.get_commit_by_system_and_task = AsyncMock(
        return_value=_mock_commit(task.invocation_hash, task.outcome_hash)
    )
    _, verified, message = await client._verify(proof)
    assert verified
