# Distribution & Directory Submission Package

## Shared Submission Metadata

| Field | Value |
|---|---|
| API endpoint | `https://a2a-verifier-production.up.railway.app/verify?token={token_address}` |
| Schema URL | `https://a2a-verifier-production.up.railway.app/schema` |
| OpenAPI spec | `https://a2a-verifier-production.up.railway.app/openapi.json` |
| Health check | `https://a2a-verifier-production.up.railway.app/health` |
| Pricing | $0.05 USDC per request (x402, Base mainnet) |
| Payment wallet | `0x1D1173c1465c9a01F6AfA38B36cc1125CC55C71a` |
| Description | Base L2 EVM simulation verifier with x402 payment gating |
| Protocol | x402 v2 (HTTP 402 + `PAYMENT-REQUIRED` / `PAYMENT-SIGNATURE`, CDP Facilitator) |
| Rate limits | 10 unpaid + 30 paid requests/min per IP |

## 1. Coinbase AgentKit Action Provider Directory

**Submission copy:**
Base L2 EVM simulation verifier with x402 payment gating. Agents pay $0.05 USDC
on Base per query and receive full buy/sell transaction simulation, honeypot and
transfer-tax detection, EIP-1967 proxy resolution, and contract ownership analysis
for any Base token — machine-readable schema at `/schema`, no API key required.

## 2. x402 Protocol Ecosystem Directory

**Submission copy:**
Production x402-gated verification service on Base mainnet: HTTP 402 challenge with
free schema preview, on-chain USDC settlement verification via transaction hash, and
SQLite-backed replay protection (global tx-hash binding). $0.05 USDC per verified
request. Discovery endpoint: `/schema`.

## 3. Virtuals Protocol Ecosystem

**Submission copy:**
Agent-to-agent token safety oracle for Base L2. Autonomous agents submit a token
address and a USDC payment proof, and receive EVM-simulated buy/sell results,
effective tax percentages, honeypot verdicts, and a composite safety score —
priced at $0.05 USDC per call via the x402 protocol.

## Announcements

### Coinbase Developer Platform Discord

> Just shipped: an x402-gated EVM token verifier on Base mainnet. Send any token
> address, get back full buy/sell simulation, honeypot/tax analysis, proxy
> resolution, and a safety score — $0.05 USDC per query, paid on-chain with
> replay-protected settlement. Free machine-readable schema for agent discovery:
> `https://a2a-verifier-production.up.railway.app/schema` — feedback welcome!

### Web3 AI Agent Forums

> **A2A token safety verification as a paid x402 service (Base L2)**
>
> If your agent trades or evaluates Base tokens, it can now buy verification
> on demand: HTTP 402 challenge -> pay 0.05 USDC on Base -> submit the tx hash as
> `X-PAYMENT-PROOF` -> receive EVM-simulated buy/sell results, honeypot and
> transfer-tax detection, proxy/ownership analysis, and a 0-100 safety score.
> No accounts, no API keys, fully machine-discoverable via `/schema` and OpenAPI.
> Endpoint: `https://a2a-verifier-production.up.railway.app/verify?token=0x...`
