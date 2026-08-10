"""AgentKit action provider for the Base A2A EVM token verifier (x402 tx-hash flow).

This service uses a custom settlement header:
  X-PAYMENT-PROOF: <base_usdc_transfer_tx_hash>

It is NOT the stock AgentKit x402 facilitator flow (PAYMENT-REQUIRED / x402 SDK).
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import requests
from web3 import Web3

from ...network import Network
from ...wallet_providers.evm_wallet_provider import EvmWalletProvider
from ..action_decorator import create_action
from ..action_provider import ActionProvider
from ..erc20.utils import get_token_details
from .constants import (
    DEFAULT_PAY_TO,
    DEFAULT_PRICE_USDC,
    DEFAULT_VERIFIER_BASE_URL,
    ERC20_TRANSFER_ABI,
    HEALTH_PATH,
    SCHEMA_PATH,
    SUPPORTED_NETWORK_IDS,
    USDC_BASE_MAINNET,
    USDC_DECIMALS,
    VERIFY_PATH,
)
from .schemas import EmptySchema, SettleWithProofSchema, VerifyBaseTokenSchema

_TOKEN_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


class BaseA2AVerifierActionProvider(ActionProvider[EvmWalletProvider]):
    """Call the production Base A2A verifier with USDC x402 settlement."""

    def __init__(self, base_url: str | None = None, request_timeout: float = 45.0) -> None:
        super().__init__("base-a2a-verifier", [])
        self._base_url = (
            base_url
            or os.getenv("BASE_A2A_VERIFIER_URL")
            or DEFAULT_VERIFIER_BASE_URL
        ).rstrip("/")
        self._timeout = request_timeout

    # ------------------------------------------------------------------
    # Public discovery / health
    # ------------------------------------------------------------------

    @create_action(
        name="get_base_a2a_schema",
        description=(
            "Fetch the public machine-readable schema for the Base A2A token verifier "
            f"({DEFAULT_VERIFIER_BASE_URL}/schema). Free, no payment required. "
            "Use this to discover capabilities before calling verify_base_token."
        ),
        schema=EmptySchema,
    )
    def get_base_a2a_schema(self, wallet_provider: EvmWalletProvider, args: dict[str, Any]) -> str:
        """GET /schema (public)."""
        try:
            url = urljoin(self._base_url + "/", SCHEMA_PATH.lstrip("/"))
            resp = requests.get(url, timeout=self._timeout)
            return json.dumps(
                {
                    "success": resp.ok,
                    "status": resp.status_code,
                    "url": url,
                    "data": self._parse_json(resp),
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"error": True, "message": f"Failed to fetch schema: {e}"}, indent=2)

    @create_action(
        name="get_base_a2a_health",
        description=(
            "Check liveness of the Base A2A verifier (/health). Free. "
            "Returns production_mode, pay_to wallet, and x402 price."
        ),
        schema=EmptySchema,
    )
    def get_base_a2a_health(self, wallet_provider: EvmWalletProvider, args: dict[str, Any]) -> str:
        """GET /health (public)."""
        try:
            url = urljoin(self._base_url + "/", HEALTH_PATH.lstrip("/"))
            resp = requests.get(url, timeout=self._timeout)
            return json.dumps(
                {
                    "success": resp.ok,
                    "status": resp.status_code,
                    "url": url,
                    "data": self._parse_json(resp),
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"error": True, "message": f"Failed to fetch health: {e}"}, indent=2)

    # ------------------------------------------------------------------
    # Paid verify flow
    # ------------------------------------------------------------------

    @create_action(
        name="verify_base_token",
        description=(
            "Verify a Base mainnet token with the paid A2A EVM simulator "
            f"({DEFAULT_VERIFIER_BASE_URL}/verify). "
            "Flow: (1) GET /verify?token=... (2) on HTTP 402, send 0.05 USDC on Base to "
            "the pay_to wallet (3) retry with header X-PAYMENT-PROOF: <tx_hash>. "
            "Returns buy/sell simulation, honeypot/tax flags, liquidity, and safety score. "
            "Requires a Base-mainnet EVM wallet with USDC + gas. "
            "Set auto_pay=false to only fetch the 402 challenge. "
            "Set payment_tx_hash to reuse an existing USDC payment tx."
        ),
        schema=VerifyBaseTokenSchema,
    )
    def verify_base_token(self, wallet_provider: EvmWalletProvider, args: dict[str, Any]) -> str:
        """Challenge → optional USDC pay → settle with X-PAYMENT-PROOF."""
        try:
            validated = VerifyBaseTokenSchema(**args)
            token = validated.token_address.strip()
            if not _TOKEN_RE.fullmatch(token):
                return self._err("Invalid token_address. Must be 42-character hex.")

            network = wallet_provider.get_network()
            if network.network_id not in SUPPORTED_NETWORK_IDS:
                return self._err(
                    f"Wallet network is {network.network_id}; only base-mainnet is supported."
                )

            # Path A: settle with an existing payment proof
            if validated.payment_tx_hash:
                return self._settle(token, validated.payment_tx_hash.strip())

            # Path B: unpaid challenge
            challenge = self._get_verify(token, payment_proof=None)
            if challenge["status"] == 200:
                return json.dumps(
                    {
                        "success": True,
                        "message": "Verifier returned 200 without a new payment "
                        "(unexpected for a fresh request; response included).",
                        "data": challenge["data"],
                    },
                    indent=2,
                )

            if challenge["status"] != 402:
                return json.dumps(
                    {
                        "error": True,
                        "message": f"Unexpected HTTP {challenge['status']} from verifier",
                        "data": challenge["data"],
                    },
                    indent=2,
                )

            pay_to, price = self._extract_payment_terms(challenge)
            if not validated.auto_pay:
                return json.dumps(
                    {
                        "status": "payment_required",
                        "message": "Verifier returned HTTP 402. Set auto_pay=true to pay, "
                        "or settle_base_a2a_verify with a payment_tx_hash.",
                        "pay_to": pay_to,
                        "price_usdc": price,
                        "network": "base-mainnet",
                        "usdc_contract": USDC_BASE_MAINNET,
                        "verify_url": challenge["url"],
                        "challenge": challenge["data"],
                        "headers": challenge.get("payment_headers", {}),
                    },
                    indent=2,
                )

            # Path C: auto-pay then settle
            tx_hash = self._pay_usdc(wallet_provider, pay_to, price)
            settled = self._settle(token, tx_hash)
            # Enrich with payment metadata
            try:
                body = json.loads(settled)
                body["payment"] = {
                    "tx_hash": tx_hash,
                    "pay_to": pay_to,
                    "amount_usdc": price,
                    "asset": USDC_BASE_MAINNET,
                    "header": "X-PAYMENT-PROOF",
                }
                return json.dumps(body, indent=2)
            except Exception:
                return settled

        except Exception as e:
            return self._err(f"verify_base_token failed: {e}")

    @create_action(
        name="settle_base_a2a_verify",
        description=(
            "Settle a Base A2A verify request using an existing USDC payment tx hash. "
            "Sends GET /verify?token=... with header X-PAYMENT-PROOF: <payment_tx_hash>. "
            "Use after you already transferred >= 0.05 USDC to the verifier pay_to wallet."
        ),
        schema=SettleWithProofSchema,
    )
    def settle_base_a2a_verify(
        self, wallet_provider: EvmWalletProvider, args: dict[str, Any]
    ) -> str:
        """Retry /verify with X-PAYMENT-PROOF only."""
        try:
            validated = SettleWithProofSchema(**args)
            token = validated.token_address.strip()
            tx = validated.payment_tx_hash.strip()
            if not _TOKEN_RE.fullmatch(token):
                return self._err("Invalid token_address.")
            if not _TX_RE.fullmatch(tx):
                return self._err("Invalid payment_tx_hash. Must be 66-character hex.")
            return self._settle(token, tx)
        except Exception as e:
            return self._err(f"settle_base_a2a_verify failed: {e}")

    def supports_network(self, network: Network) -> bool:
        """Only Base mainnet — verifier settles USDC on Base."""
        return (
            network.protocol_family == "evm"
            and (network.network_id or "") in SUPPORTED_NETWORK_IDS
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _verify_url(self, token: str) -> str:
        return f"{self._base_url}{VERIFY_PATH}?token={token}"

    def _get_verify(self, token: str, payment_proof: str | None) -> dict[str, Any]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if payment_proof:
            headers["X-PAYMENT-PROOF"] = payment_proof
        url = self._verify_url(token)
        resp = requests.get(url, headers=headers, timeout=self._timeout)
        payment_headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower().startswith("x-402") or k.lower() == "x-payment-proof"
        }
        return {
            "url": url,
            "status": resp.status_code,
            "data": self._parse_json(resp),
            "payment_headers": payment_headers,
        }

    def _settle(self, token: str, payment_tx_hash: str) -> str:
        if not _TX_RE.fullmatch(payment_tx_hash):
            return self._err("Invalid payment_tx_hash. Must be 66-character hex.")
        result = self._get_verify(token, payment_proof=payment_tx_hash)
        if result["status"] == 200:
            return json.dumps(
                {
                    "success": True,
                    "status": 200,
                    "token": token.lower(),
                    "payment_tx_hash": payment_tx_hash,
                    "data": result["data"],
                },
                indent=2,
            )
        return json.dumps(
            {
                "error": True,
                "status": result["status"],
                "message": "Settlement failed",
                "payment_tx_hash": payment_tx_hash,
                "data": result["data"],
            },
            indent=2,
        )

    def _extract_payment_terms(self, challenge: dict[str, Any]) -> tuple[str, float]:
        headers = {k.lower(): v for k, v in (challenge.get("payment_headers") or {}).items()}
        body = challenge.get("data") or {}
        pay_to = (
            headers.get("x-402-payto")
            or (body.get("pay_to") if isinstance(body, dict) else None)
            or DEFAULT_PAY_TO
        )
        price_raw = (
            headers.get("x-402-price")
            or (body.get("x402_price") if isinstance(body, dict) else None)
            or str(DEFAULT_PRICE_USDC)
        )
        # "0.05 USDC" -> 0.05
        price_str = str(price_raw).split()[0]
        try:
            price = float(price_str)
        except ValueError:
            price = DEFAULT_PRICE_USDC
        return Web3.to_checksum_address(pay_to), price

    def _pay_usdc(
        self, wallet_provider: EvmWalletProvider, pay_to: str, amount_usdc: float
    ) -> str:
        """Send USDC on Base; return tx hash."""
        w3 = Web3()
        usdc = w3.to_checksum_address(USDC_BASE_MAINNET)
        destination = w3.to_checksum_address(pay_to)
        amount_atomic = int(Decimal(str(amount_usdc)) * (10**USDC_DECIMALS))

        details = get_token_details(wallet_provider, usdc)
        if details is not None and details.balance < amount_atomic:
            raise RuntimeError(
                f"Insufficient USDC: need {amount_usdc}, wallet has {details.formatted_balance}"
            )

        contract = w3.eth.contract(address=usdc, abi=ERC20_TRANSFER_ABI)
        data = contract.encode_abi("transfer", [destination, amount_atomic])
        tx_hash = wallet_provider.send_transaction({"to": usdc, "data": data})
        wallet_provider.wait_for_transaction_receipt(tx_hash)
        if isinstance(tx_hash, bytes):
            return "0x" + tx_hash.hex()
        h = str(tx_hash)
        return h if h.startswith("0x") else "0x" + h

    @staticmethod
    def _parse_json(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return resp.text

    @staticmethod
    def _err(message: str) -> str:
        return json.dumps({"error": True, "message": message}, indent=2)


def base_a2a_verifier_action_provider(
    base_url: str | None = None,
) -> BaseA2AVerifierActionProvider:
    """Factory for the Base A2A verifier action provider."""
    return BaseA2AVerifierActionProvider(base_url=base_url)
