"""Minimal example: wire the Base A2A verifier into AgentKit.

Prereqs:
  - GEO00/agentkit checked out with the base_a2a_verifier provider installed
  - Base mainnet wallet with USDC (>= 0.05) and ETH for gas
  - Wallet provider env configured (CDP or eth_account + RPC)
"""

from coinbase_agentkit import AgentKit, AgentKitConfig
from coinbase_agentkit.action_providers.base_a2a_verifier import (
    base_a2a_verifier_action_provider,
)

# Pseudocode — replace with your real wallet provider construction from AgentKit docs.
# from coinbase_agentkit import eth_account_wallet_provider, EthAccountWalletProviderConfig
# wallet = eth_account_wallet_provider(EthAccountWalletProviderConfig(...))

def build_agent(wallet_provider):
    return AgentKit(
        AgentKitConfig(
            wallet_provider=wallet_provider,
            action_providers=[
                base_a2a_verifier_action_provider(
                    # Optional override; defaults to production Railway URL
                    # base_url="https://a2a-verifier-production.up.railway.app",
                )
            ],
        )
    )


if __name__ == "__main__":
    print(
        "Import base_a2a_verifier_action_provider into your agent, then invoke "
        "verify_base_token with token_address=0x..."
    )
