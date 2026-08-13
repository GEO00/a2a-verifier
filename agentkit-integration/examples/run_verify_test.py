"""Paid verify smoke test (sync AgentKit API — there is no execute_action)."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing sibling helpers when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_with_agentkit import build_agent, build_wallet, invoke, _load_env  # noqa: E402

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def main() -> int:
    _load_env()
    wallet = build_wallet()
    agent_kit = build_agent(wallet)

    print([a.name for a in agent_kit.get_actions()])

    # Equivalent of: await agent_kit.execute_action("verify_base_token", {...})
    # coinbase-agentkit exposes sync Action.invoke(args) only.
    result = invoke(
        agent_kit,
        "verify_base_token",
        {
            "token_address": USDC_BASE,
            "auto_pay": True,
        },
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
