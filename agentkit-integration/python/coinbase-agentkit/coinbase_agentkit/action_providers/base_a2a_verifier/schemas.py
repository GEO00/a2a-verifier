"""Pydantic schemas for Base A2A Verifier actions."""

from pydantic import BaseModel, Field


class EmptySchema(BaseModel):
    """No parameters."""

    class Config:
        """Pydantic config."""

        title = "No parameters required"


class VerifyBaseTokenSchema(BaseModel):
    """Verify a Base token via the paid A2A verifier."""

    token_address: str = Field(
        ...,
        description=(
            "Base mainnet ERC-20 token contract address to analyze "
            "(42-char hex, e.g. 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 for USDC)."
        ),
    )
    auto_pay: bool = Field(
        default=True,
        description=(
            "If true (default), on HTTP 402 automatically send 0.05 USDC on Base to the "
            "verifier payment wallet, then retry with X-PAYMENT-PROOF set to the tx hash. "
            "If false, return the 402 challenge details without paying."
        ),
    )
    payment_tx_hash: str | None = Field(
        default=None,
        description=(
            "Optional existing Base USDC payment transaction hash. When set, skips a new "
            "transfer and settles using X-PAYMENT-PROOF: <payment_tx_hash>."
        ),
    )

    class Config:
        """Pydantic config."""

        title = "Parameters for verifying a Base token with the A2A verifier"


class SettleWithProofSchema(BaseModel):
    """Settle a previously challenged verify request with a payment tx hash."""

    token_address: str = Field(
        ...,
        description="Token address originally requested (42-char hex).",
    )
    payment_tx_hash: str = Field(
        ...,
        description=(
            "Base transaction hash of a USDC transfer (>= 0.05) to the verifier pay_to wallet. "
            "Sent as the X-PAYMENT-PROOF header."
        ),
    )

    class Config:
        """Pydantic config."""

        title = "Parameters for settling a verify request with X-PAYMENT-PROOF"
