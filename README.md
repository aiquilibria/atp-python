# ATP Python SDK

> **Framework-agnostic SDK for the Agent Trust Protocol**
>
> Enable AI agents to create cryptographically verifiable proofs of their work.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![ATP Spec](https://img.shields.io/badge/ATP%20spec-v0.2-teal.svg)](https://agenttrustprotocol.org/spec/v0.2)
[![Tests](https://img.shields.io/badge/tests-107%20passing-brightgreen.svg)](./tests)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)

---

## 🎯 What is ATP?

The [Agent Trust Protocol (ATP)](https://agenttrustprotocol.org) is an open protocol that enables
AI agents to create **cryptographically verifiable proofs** of their task execution. It provides a
standardized way for agents to:

- ✅ **Prove they did the work** — cryptographic hashes bind task inputs and outputs
- 🔒 **Preserve privacy** — agents keep full data locally; the Exchange stores only hashes
- 🔗 **Enable verification** — any registered agent can challenge and verify any other's work
- 🌐 **Remain interoperable** — transport-agnostic protocol works with any agent framework
- 📜 **Build reputation** — committed, countersigned records establish trust over time

This SDK implements the **participant side of ATP v0.2** ([specification](https://agenttrustprotocol.org/spec/v0.2)):
proof construction over canonical-JSON SHA-256 commitments, local proof persistence with
TTL-based retention, system registration, Proof Sketch commitment, both sides of the JSON-RPC
`atp.challenge` flow, integrity verification against Exchange-committed sketches, and assessment
reporting across the three trust dimensions (Integrity, Quality, Compliance).

---

## 🚀 Quick Start

### Installation

```bash
pip install -e .
# or, with uv
uv pip install -e .
```

### Your first ATP agent

```python
import asyncio
from atp import ATPClient, ATPConfig, SQLiteProofStore, atp_task

config = ATPConfig(
    api_key="your-exchange-api-key",
    exchange_url="https://exchange.example.com",
)

async def main():
    async with ATPClient(config) as client:
        # 1. Register with the ATP Exchange (idempotent; safe on every startup)
        await client.register_system(
            name="my-first-agent",
            system_type="agent",
            url="https://my-agent.example.com",   # your challenge endpoint
        )

        # 2. Execute a task under an atp_task context —
        #    the proof is created, stored locally, and its sketch
        #    committed to the Exchange automatically on exit.
        store = SQLiteProofStore(config=config, db_path="proofs.db")
        async with atp_task(client, store, query="What is 2+2?") as task:
            task.set_response("4")

        print(f"Committed: {task.atp_task_id}")

asyncio.run(main())
```

### Challenging another agent

```python
proof, verified, message = await client.challenge(
    agent_url="https://other-agent.example.com",
    task_id="1f0e6c58-9f3a-4d2b-8c47-5a9e2d7b6c1a",
)
# verified is True only when every hash in the disclosed proof
# matches the Proof Sketch committed to the Exchange.
```

Serving the challenge side is one route: mount a JSON-RPC 2.0 `atp.challenge` handler at your
registered `url` that returns the stored proof for a validated challenger. See
[`examples`](https://github.com/aiquilibria/atp-examples) for a complete FastAPI implementation.

---

## 🏗️ What's in the box

| Module | Purpose |
|--------|---------|
| `atp.core.client` | `ATPClient` — registration, commits, challenges, verification, assessments |
| `atp.core.models` | `ProofSketch`, `ProofData`, `Dependency`, `AssessmentRecord`, … (spec §5, §9) |
| `atp.core.hashing` | Canonical-JSON SHA-256 hashing (spec §5.3) |
| `atp.core.storage` | `SQLiteProofStore` / `InMemoryProofStore` with TTL retention |
| `atp.core.utils` | `atp_task` context manager — proof lifecycle without boilerplate |
| `atp.adapters.base` | `FrameworkAdapter` — the extension point for framework integrations |

## 🔌 Framework adapters

ATP is deliberately framework-agnostic: an existing agent becomes an ATP participant through a
thin adapter, not a rewrite. The `FrameworkAdapter` base class in `atp.adapters.base` is the
supported extension point — implement proof capture around your framework's invocation path and
mount the challenge route.

Adapters for **MCP** (tool servers), **A2A** (agent cards + handlers), and **LangChain** are
implemented and battle-tested against this SDK, and will be released in a subsequent version.
Building your own on `FrameworkAdapter` is deliberately straightforward — the financial-research
reference pipeline wires five systems across three frameworks with a few dozen lines of glue.

---

## 🧪 Tests

```bash
uv run --extra dev pytest
```

## 📖 Protocol

- Specification: [agenttrustprotocol.org/spec/v0.2](https://agenttrustprotocol.org/spec/v0.2)
- Ontology: [agenttrustprotocol.org/ontology/v0.2.0](https://agenttrustprotocol.org/ontology/v0.2.0)
- Changelog: [agenttrustprotocol.org/changelog](https://agenttrustprotocol.org/changelog)

## 📄 License

[Apache 2.0](./LICENSE) — Copyright © 2026 AIquilibria
