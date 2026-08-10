# Install Base A2A Verifier provider into GEO00/agentkit

## Paths to create in the fork

```text
GEO00/agentkit/
└── python/coinbase-agentkit/coinbase_agentkit/action_providers/base_a2a_verifier/
    ├── __init__.py
    ├── constants.py
    ├── schemas.py
    ├── base_a2a_verifier_action_provider.py
    └── README.md
```

Copy from this directory:

```bash
cd /path/to/GEO00/agentkit
cp -R /path/to/base-a2a-verifier/agentkit-integration/python/coinbase-agentkit/coinbase_agentkit/action_providers/base_a2a_verifier \
  python/coinbase-agentkit/coinbase_agentkit/action_providers/
```

## Patch 1 — `action_providers/__init__.py`

Add import (near other providers):

```python
from .base_a2a_verifier.base_a2a_verifier_action_provider import (
    BaseA2AVerifierActionProvider,
    base_a2a_verifier_action_provider,
)
```

Add to `__all__`:

```python
"BaseA2AVerifierActionProvider",
"base_a2a_verifier_action_provider",
```

## Patch 2 — `coinbase_agentkit/__init__.py`

Add the same symbols to the top-level package exports (mirror how `x402_action_provider` is exported).

## Service details (correct values)

| Field | Value |
|---|---|
| Verifier base URL | `https://a2a-verifier-production.up.railway.app` |
| Verify endpoint | `https://a2a-verifier-production.up.railway.app/verify?token={token}` |
| Schema | `https://a2a-verifier-production.up.railway.app/schema` |
| Price | `0.05` USDC on Base |
| Pay-to | `0x1D1173c1465c9a01F6AfA38B36cc1125CC55C71a` |
| Payment header | `X-PAYMENT-PROOF: <tx_hash>` |
| Network | `base-mainnet` |

## Security note

If you pasted an Alchemy URL containing an API key into chat, **rotate that Alchemy key**. Never put RPC API keys in the action provider. Configure wallet RPC via AgentKit wallet-provider env vars instead.
