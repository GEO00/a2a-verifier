# Base A2A Verifier Action Provider

Python AgentKit action provider for **https://a2a-verifier-production.up.railway.app**.

## Settlement model (important)

This verifier uses a **custom** header, not the stock AgentKit `x402` facilitator SDK:

```http
GET /verify?token=0x...
X-PAYMENT-PROOF: 0x<base_usdc_transfer_tx_hash>
```

On unpaid requests the API returns `HTTP 402` with `X-402-PayTo` / `X-402-Price: 0.05 USDC`.
The provider transfers Base USDC to `pay_to`, then retries with `X-PAYMENT-PROOF`.

Do **not** point this provider at an Alchemy RPC URL. Alchemy is for your wallet RPC only.

## Actions

| Action | Payment | Purpose |
|---|---|---|
| `get_base_a2a_schema` | free | `GET /schema` |
| `get_base_a2a_health` | free | `GET /health` |
| `verify_base_token` | 0.05 USDC (auto) | Full challenge → pay → settle |
| `settle_base_a2a_verify` | existing tx hash | Settle with `X-PAYMENT-PROOF` only |

## Install into GEO00/agentkit

Copy this folder to:

```text
python/coinbase-agentkit/coinbase_agentkit/action_providers/base_a2a_verifier/
```

Then wire exports (see `../../../../INSTALL.md`).

## Usage

```python
from coinbase_agentkit import AgentKit, AgentKitConfig
from coinbase_agentkit.action_providers.base_a2a_verifier import (
    base_a2a_verifier_action_provider,
)

agent_kit = AgentKit(
    AgentKitConfig(
        wallet_provider=wallet_provider,  # Base mainnet EVM wallet with USDC + ETH gas
        action_providers=[base_a2a_verifier_action_provider()],
    )
)
```

Optional override:

```bash
export BASE_A2A_VERIFIER_URL=https://a2a-verifier-production.up.railway.app
```
