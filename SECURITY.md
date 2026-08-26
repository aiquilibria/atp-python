# Security Policy

## Reporting a Vulnerability

Please report suspected vulnerabilities privately via **GitHub Security Advisories**
("Report a vulnerability" on this repository). Do not open public issues for security reports.

We will acknowledge reports within 5 business days. Coordinated disclosure is appreciated;
we will credit reporters in release notes unless anonymity is requested.

## Scope

This SDK implements the participant side of the
[ATP specification](https://agenttrustprotocol.org/spec/v0.2). In scope, in particular:

- Proof construction and canonical-JSON hashing (integrity of commitments)
- Challenge-response handling and challenger validation
- Local proof storage and TTL enforcement
- Verification of Exchange countersignatures

Protocol-level weaknesses belong to
[aiquilibria/agenttrustprotocol](https://github.com/aiquilibria/agenttrustprotocol/security).

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅ |
