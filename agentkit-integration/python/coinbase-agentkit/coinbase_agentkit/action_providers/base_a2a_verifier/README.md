# Base A2A Verifier Action Provider

Python AgentKit action provider for **https://a2a-verifier-production.up.railway.app**.

## Settlement model (important)

Production now speaks **x402 v2** (CDP Facilitator):

```http
GET /verify?token=0x...
→ HTTP 402 + PAYMENT-REQUIRED: <base64 {x402Version:2, accepts, extensions.bazaar}>

GET /verify?token=0x...
PAYMENT-SIGNATURE: <base64 payment payload>
→ HTTP 200 simulation JSON
```

This AgentKit provider still implements the **legacy** `X-PAYMENT-PROOF: <tx_hash>`
flow and will not auto-settle against the current production API. Prefer an
x402-compatible client (or AgentKit's stock x402 provider) for paid calls, and
use `get_base_a2a_schema` / `get_base_a2a_health` here for free discovery.

## Actions

| Action | Payment | Purpose |
|---|---|---|
| `get_base_a2a_schema` | free | `GET /schema` |
| `get_base_a2a_health` | free | `GET /health` |
| `verify_base_token` | legacy | Needs rewrite for PAYMENT-SIGNATURE |
| `settle_base_a2a_verify` | legacy | Needs rewrite for PAYMENT-SIGNATURE |

## Install into GEO00/agentkit

Copy this folder to:

```text
python/coinbase-agentkit/coinbase_agentkit/action_providers/base_a2a_verifier/
```

Then wire exports (see `../../../../INSTALL.md`).

## Usage

```python
from coinbase_agentkit import AgentKit, AgentKitConfig
from coinbase_agentkit import base_a2a_verifier_action_provider

agent_kit = AgentKit(AgentKitConfig(
    wallet_provider=wallet,  # EthAccountWalletProvider on Base mainnet
    action_providers=[
        base_a2a_verifier_action_provider(),
    ],
))
```

Optional override:

```bash
export BASE_A2A_VERIFIER_URL=https://a2a-verifier-production.up.railway.app
```
