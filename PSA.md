# PSA security review — bitbucket-mcp-server-datacenter

This document records the PSA (Privacy & Security Assessment) review of the
Bitbucket Data Center MCP server. There is **no MCP-specific PSA**; this review
maps the generic, applicable PSA requirement documents to the server's design.

- **Scope:** Python MCP server acting as a REST **consumer** of a Bitbucket
  Data Center instance over HTTPS, using an HTTP access token, run locally via
  stdio (VS Code MCP).
- **Date:** 2026-06-04
- **Source:** Telekom PSA requirement documents (`telecontext` MCP server).

## Method

1. Listed all PSA subject areas and requirement documents.
2. Selected the requirement documents applicable to a token-authenticated REST
   client/server over TLS (no MCP-specific document exists).
3. Reviewed the individual requirements of each selected document against the
   implementation and recorded status + evidence.

## Applicable requirement documents

| PSA document                                    | ID     | Relevance                                                  |
| ----------------------------------------------- | ------ | ---------------------------------------------------------- |
| Web Services                                    | 3.02   | Highest — REST consumer/provider, token auth, TLS, input validation |
| Cryptographic Algorithms and Security Protocols | 3.50   | TLS parameters for the Bitbucket connection                |
| Technical Baseline Security for IT/NT Systems   | 3.01   | Generic baseline (trusted software, hardening, logging)    |
| IAM                                             | 3.69   | Token usage, least privilege, technical accounts           |

## Findings

Legend: ✅ met · ⚠️ conditional / configuration-dependent · 🔶 open / out of scope for the tool

### Web Services (3.02)

| Req | Topic | Status | Evidence |
| --- | ----- | ------ | -------- |
| Req 2  | Software from trusted sources, integrity-checked | ⚠️ | Dependencies pinned via `uv`/lockfile; verify lockfile hashes in CI |
| Req 10 | Auth based on strong cryptography | ✅ | Bearer token over TLS |
| Req 12/13 | Validate requests/responses against a spec | ✅/⚠️ | FastMCP argument schemas validate inputs; response validation partial |
| Req 15–18 | TLS 1.2/1.3, PFS ciphers, certificate validation | ✅ | httpx/OpenSSL defaults; **TLS verification now enforced in code** |
| Req 21 | No confidential data in the URL | ✅ | Token sent in `Authorization` header, never in URL |
| Req 22 | Validate content types | ✅ | `put_file` uses multipart/form-data; JSON elsewhere |
| Req 33–37 | Time-stamped logging, forwarding to log server / SIEM | 🔶 | Only stderr logging; central/SIEM logging is a deployment concern |

### Cryptographic Algorithms and Security Protocols (3.50)

| Req | Topic | Status | Evidence |
| --- | ----- | ------ | -------- |
| Req 40 | TLS 1.2 or 1.3 | ✅ | Provided by httpx/OpenSSL |
| Req 41/42 | PFS cipher suites, DH groups | ✅ | Modern OpenSSL defaults |
| Req 43 | Certificates from a CA, correctly validated | ✅ | **Verification enforced**; internal CAs via `BITBUCKET_CA_BUNDLE` |

### Technical Baseline Security (3.01)

| Req | Topic | Status | Evidence |
| --- | ----- | ------ | -------- |
| Req 9  | Outputs must not disclose internal structures/secrets | ✅ | `_extract_error` returns only `errors[].message`; token kept out of errors |
| Req 14/15 | Protect data needing protection at rest / in transit | ✅ | Token via VS Code `password` input + env; transport over TLS only |
| Req 23 | Least privilege | ✅ | `ENABLE_TOOLS` gating; destructive tools permanently blocked |
| Req 33–37 | Security logging, forwarding, retention | 🔶 | Only stderr logging (see Web Services 33–37) |

### IAM (3.69)

| Req | Topic | Status | Evidence |
| --- | ----- | ------ | -------- |
| Req 17 | Least privilege | ✅ | Default `ENABLE_TOOLS=read` |
| Req 22 | Use tokens only per the defined standard | ✅ | Bitbucket HTTP access token in Bearer header |
| Req 35 | Technical-account secrets ≥ 30 chars | ✅ | Bitbucket HTTP access token satisfies this |
| Req 42/43 | Time-limited assignment / automatic rotation | 🔶 | No rotation in the tool; see Token lifecycle in `README.md` |

## Result

- **Well covered:** TLS with enforced certificate validation, token in header
  (never in URL), least-privilege tool gating, permanently blocked destructive
  tools, secret handling via the VS Code input.
- **Resolved in this review:** TLS verification is now **enforced in code** and
  can no longer be disabled (Web Services 3.02 Req 18 / Cryptographic
  Algorithms 3.50 Req 43). Internal CAs are supported via `BITBUCKET_CA_BUNDLE`.
- **Documented:** Token lifecycle (provisioning, storage, transport, rotation,
  revocation) added to `README.md`.

### Open points for a production deployment

1. **Central/SIEM logging** (3.02 Req 33–37, 3.01 Req 33–37): the tool only logs
   to stderr; near-real-time forwarding to a log server / SIEM is a deployment
   responsibility.
2. **Dependency integrity in CI** (3.02 Req 2): enforce lockfile hash
   verification in the build pipeline.
3. **Token rotation** (IAM 3.69 Req 42/43): rotation/expiry is operational; see
   the Token lifecycle section in `README.md`.
