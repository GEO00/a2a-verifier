"""Run Base A2A verifier actions via AgentKit + EthAccountWalletProvider.

Prereqs (env):
  AGENT_PRIVATE_KEY  — Base mainnet key with USDC (>= 0.05) and ETH for gas
  BASE_RPC_URL       — optional; else first entry of BASE_RPC_URLS

Usage:
  python agentkit-integration/examples/verify_with_agentkit.py
  python agentkit-integration/examples/verify_with_agentkit.py --token 0x...
  python agentkit-integration/examples/verify_with_agentkit.py --token 0x... --no-pay
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account

from coinbase_agentkit import (
    AgentKit,
    AgentKitConfig,
    EthAccountWalletProvider,
    EthAccountWalletProviderConfig,
    base_a2a_verifier_action_provider,
)

# Base mainnet
BASE_CHAIN_ID = "8453"
DEFAULT_TOKEN = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # native USDC


def _load_env() -> None:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)
    load_dotenv(override=False)


def _rpc_url() -> str:
    single = os.getenv("BASE_RPC_URL") or os.getenv("RPC_URL")
    if single:
        return single.strip()
    urls = os.getenv("BASE_RPC_URLS", "")
    first = next((u.strip() for u in urls.split(",") if u.strip()), "")
    if not first:
        raise SystemExit("Set BASE_RPC_URL or BASE_RPC_URLS in the environment / .env")
    return first


def _private_key() -> str:
    key = (os.getenv("AGENT_PRIVATE_KEY") or "").strip().strip('"').strip("'")
    if key.startswith("0x"):
        return key
    if key:
        return "0x" + key
    raise SystemExit("Set AGENT_PRIVATE_KEY in the environment / .env")


def build_wallet() -> EthAccountWalletProvider:
    account = Account.from_key(_private_key())
    return EthAccountWalletProvider(
        EthAccountWalletProviderConfig(
            account=account,
            chain_id=BASE_CHAIN_ID,
            rpc_url=_rpc_url(),
        )
    )


def build_agent(wallet_provider: EthAccountWalletProvider) -> AgentKit:
    # Same shape as: AgentKit(AgentKitConfig(wallet_provider=wallet, action_providers=[...]))
    return AgentKit(
        AgentKitConfig(
            wallet_provider=wallet_provider,
            action_providers=[
                base_a2a_verifier_action_provider(),
            ],
        )
    )


def _actions_by_name(agent: AgentKit) -> dict[str, object]:
    """Index by full name and short suffix (after last `_` segment group).

    AgentKit names actions like ``BaseA2AVerifierActionProvider_verify_base_token``.
    """
    by_name: dict[str, object] = {}
    for action in agent.get_actions():
        by_name[action.name] = action
        short = action.name.split("_", 1)[-1] if "_" in action.name else action.name
        # Prefer exact short names used in provider @create_action
        for candidate in (
            action.name.rsplit("ActionProvider_", 1)[-1],
            short,
            action.name,
        ):
            by_name.setdefault(candidate, action)
    return by_name


def invoke(agent: AgentKit, name: str, args: dict | None = None) -> str:
    actions = _actions_by_name(agent)
    if name not in actions:
        available = ", ".join(sorted({a.name for a in agent.get_actions()}))
        raise SystemExit(f"Action {name!r} not available. Have: {available}")
    return actions[name].invoke(args or {})


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="Base A2A verifier via AgentKit")
    parser.add_argument(
        "--token",
        default=None,
        help=f"Token to verify (default: USDC {DEFAULT_TOKEN} when verifying)",
    )
    parser.add_argument(
        "--no-pay",
        action="store_true",
        help="Only fetch the 402 challenge (no USDC transfer)",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Only run free health + schema checks",
    )
    args = parser.parse_args(argv)

    wallet = build_wallet()
    agent = build_agent(wallet)
    print(f"wallet={wallet.get_address()}")
    print(f"network={wallet.get_network().network_id}")
    print(f"actions={sorted(a.name for a in agent.get_actions())}")

    print("\n=== get_base_a2a_health ===")
    print(invoke(agent, "get_base_a2a_health"))

    print("\n=== get_base_a2a_schema ===")
    print(invoke(agent, "get_base_a2a_schema"))

    if args.skip_verify:
        return 0

    token = args.token or DEFAULT_TOKEN
    print(f"\n=== verify_base_token token={token} auto_pay={not args.no_pay} ===")
    print(
        invoke(
            agent,
            "verify_base_token",
            {
                "token_address": token,
                "auto_pay": not args.no_pay,
            },
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
