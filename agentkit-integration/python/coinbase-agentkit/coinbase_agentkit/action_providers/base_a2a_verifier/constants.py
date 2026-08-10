"""Constants for the Base A2A Verifier action provider."""

# Production verifier (Railway). Override with BASE_A2A_VERIFIER_URL if needed.
DEFAULT_VERIFIER_BASE_URL = "https://a2a-verifier-production.up.railway.app"

SCHEMA_PATH = "/schema"
VERIFY_PATH = "/verify"
HEALTH_PATH = "/health"

# Base mainnet native USDC
USDC_BASE_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6

# Fallback payment destination if the 402 response omits X-402-PayTo / pay_to
DEFAULT_PAY_TO = "0x1D1173c1465c9a01F6AfA38B36cc1125CC55C71a"
DEFAULT_PRICE_USDC = 0.05

# Minimal ERC-20 transfer ABI fragment
ERC20_TRANSFER_ABI = [
    {
        "type": "function",
        "name": "transfer",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "recipient", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"type": "bool"}],
    },
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
]

SUPPORTED_NETWORK_IDS = {"base-mainnet"}
