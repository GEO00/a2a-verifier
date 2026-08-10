import asyncio
import logging
import os
import sqlite3
import time
from decimal import Decimal
from typing import Any

import httpx

try:
    import aiosqlite
    AIOSQLITE_AVAILABLE = True
except ImportError:
    AIOSQLITE_AVAILABLE = False
    aiosqlite = None  # type: ignore[assignment]

try:
    from web3 import AsyncWeb3
    from web3.providers import AsyncHTTPProvider
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    # Optional-dependency sentinels; all uses are guarded by WEB3_AVAILABLE.
    AsyncWeb3 = None  # type: ignore[misc, assignment]
    AsyncHTTPProvider = None  # type: ignore[misc, assignment]

logger = logging.getLogger("x402_verifier")

class X402PaymentVerifier:
    def __init__(
        self,
        rpc_url: str | None = None,
        pay_to_wallet: str = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
        required_usdc: float = 0.05,
        allow_test_proofs: bool | None = None,
        production_mode: bool | None = None,
        ttl_seconds: float = 86400.0,  # 24-hour replay protection window
        db_path: str = "used_proofs.db"
    ):
        # Env var fallbacks with security defaults
        if allow_test_proofs is None:
            allow_test_proofs = os.getenv("ALLOW_TEST_PAYMENT_PROOFS", "false").lower() == "true"
        
        if production_mode is None:
            production_mode = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
            
        rpc_env = rpc_url or os.getenv("BASE_RPC_URLS") or os.getenv("BASE_RPC_URL") or "https://mainnet.base.org"
        self.rpc_urls: list[str] = [u.strip() for u in rpc_env.split(",") if u.strip()]
        if not self.rpc_urls:
            self.rpc_urls = ["https://mainnet.base.org"]

        self.pay_to_wallet = pay_to_wallet.lower()
        self.required_usdc = required_usdc
        self.allow_test_proofs = allow_test_proofs
        self.production_mode = production_mode
        self.ttl_seconds = ttl_seconds
        self.db_path = db_path
        self._rpc_rr_index = 0
        
        # Base L2 USDC contract address (6 decimals)
        self.usdc_contract = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower()
        # Safe integer calculation using Decimal to prevent floating-point inaccuracy (0.05 * 1_000_000)
        self.required_units = int(Decimal(str(required_usdc)) * Decimal(1000000))

        # Shared httpx AsyncClient for connection pooling
        self._http_client: httpx.AsyncClient | None = None

        if self.allow_test_proofs:
            if self.production_mode:
                logger.warning("CRITICAL: PRODUCTION_MODE=true overrides ALLOW_TEST_PAYMENT_PROOFS. Test payment proofs are HARD-REJECTED.")
            else:
                logger.warning("WARNING: Test payment proofs are ENABLED. Do NOT run with this setting in production!")

        # Initialize SQLite DB schema
        self._db_initialized = False

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            self._http_client = httpx.AsyncClient(limits=limits, timeout=2.5)
        return self._http_client

    async def _init_db(self) -> None:
        if self._db_initialized:
            return
        if AIOSQLITE_AVAILABLE:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS used_proofs (
                        tx_hash TEXT PRIMARY KEY,
                        token TEXT NOT NULL,
                        amount_usdc REAL NOT NULL,
                        used_at REAL NOT NULL
                    );
                """)
                await db.execute("CREATE INDEX IF NOT EXISTS idx_used_at ON used_proofs(used_at);")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_token ON used_proofs(token);")
                await db.commit()
        else:
            def _sync_init():
                conn = sqlite3.connect(self.db_path)
                with conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS used_proofs (
                            tx_hash TEXT PRIMARY KEY,
                            token TEXT NOT NULL,
                            amount_usdc REAL NOT NULL,
                            used_at REAL NOT NULL
                        );
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_used_at ON used_proofs(used_at);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_token ON used_proofs(token);")
                conn.close()
            await asyncio.to_thread(_sync_init)
        self._db_initialized = True

    async def _prune_expired(self, now: float) -> None:
        await self._init_db()
        cutoff = now - self.ttl_seconds
        if AIOSQLITE_AVAILABLE:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM used_proofs WHERE used_at < ?", (cutoff,))
                await db.commit()
        else:
            def _sync_prune():
                conn = sqlite3.connect(self.db_path)
                with conn:
                    conn.execute("DELETE FROM used_proofs WHERE used_at < ?", (cutoff,))
                conn.close()
            await asyncio.to_thread(_sync_prune)

    async def _claim_proof_tx(self, tx_hash: str, token_address: str, amount_usdc: float, now: float) -> bool:
        """
        Atomically attempts to claim proof in SQLite database using INSERT OR IGNORE.
        Returns True if claim succeeded (rowcount > 0), False if already claimed by a concurrent request.
        """
        await self._init_db()
        tx_clean = tx_hash.strip().lower()
        token_clean = token_address.strip().lower()

        if AIOSQLITE_AVAILABLE:
            try:
                async with aiosqlite.connect(self.db_path, timeout=5.0) as db:
                    await db.execute("PRAGMA busy_timeout=5000")
                    cursor = await db.execute(
                        "INSERT OR IGNORE INTO used_proofs (tx_hash, token, amount_usdc, used_at) VALUES (?, ?, ?, ?)",
                        (tx_clean, token_clean, amount_usdc, now)
                    )
                    await db.commit()
                    return cursor.rowcount > 0
            except sqlite3.OperationalError as e:
                # Contended writers under --workers 1 should be rare; log loudly if they appear.
                logger.error(f"SQLite lock contention claiming proof {tx_clean[:18]}...: {e}")
                raise
        else:
            def _sync_claim():
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                try:
                    conn.execute("PRAGMA busy_timeout=5000")
                    with conn:
                        cursor = conn.execute(
                            "INSERT OR IGNORE INTO used_proofs (tx_hash, token, amount_usdc, used_at) VALUES (?, ?, ?, ?)",
                            (tx_clean, token_clean, amount_usdc, now)
                        )
                        return cursor.rowcount > 0
                except sqlite3.OperationalError as e:
                    logger.error(f"SQLite lock contention claiming proof {tx_clean[:18]}...: {e}")
                    raise
                finally:
                    conn.close()
            return await asyncio.to_thread(_sync_claim)

    def _get_next_rpc_url(self) -> str:
        url = self.rpc_urls[self._rpc_rr_index % len(self.rpc_urls)]
        self._rpc_rr_index += 1
        return url

    async def _rpc_call(self, method: str, params: list, timeout: float = 2.0) -> Any:
        """Executes JSON-RPC payload against Base L2 RPC with multi-RPC failover retries."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1
        }
        client = self._get_http_client()
        attempts = min(len(self.rpc_urls), 3)

        for attempt in range(attempts):
            rpc_url = self._get_next_rpc_url()
            try:
                resp = await client.post(rpc_url, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        logger.debug(f"RPC Error ({method}) on {rpc_url}: {data['error']}")
                        return None
                    return data.get("result")
                else:
                    logger.warning(f"RPC HTTP {resp.status_code} from {rpc_url}, retrying...")
            except Exception as e:
                logger.warning(f"RPC HTTP error during {method} call to {rpc_url}: {e}")
        return None

    async def verify_payment_proof(
        self,
        proof_header: str,
        token_address: str = "0x0000000000000000000000000000000000000000"
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Verifies the X-PAYMENT-PROOF header asynchronously with atomic SQLite Replay Protection.
        Rejects double-spending of transaction hashes globally.
        """
        if not proof_header:
            return False, "Missing payment proof header", {}

        proof_clean = proof_header.strip()
        now = time.time()
        token_clean = token_address.strip().lower()

        # Hard rejection of test proofs in production mode regardless of allow_test_proofs flag
        is_test_prefix = proof_clean.startswith(("test_proof_", "tx_demo_"))
        if self.production_mode and is_test_prefix:
            logger.warning(f"Production mode active: rejected test proof prefix '{proof_clean}'")
            return False, "Test payment proofs strictly disabled in production mode", {}

        # Prune expired records prior to claim attempt
        await self._prune_expired(now)

        # --- 1. TEST / SIMULATION PROOF VERIFICATION ---
        if self.allow_test_proofs and not self.production_mode and is_test_prefix:
            claimed = await self._claim_proof_tx(proof_clean, token_clean, self.required_usdc, now)
            if not claimed:
                logger.warning(f"Replay attack detected for transaction hash: {proof_clean}")
                return False, "Transaction hash already used (double-spend rejected)", {}

            return True, "Valid test payment proof verified", {
                "tx_hash": proof_clean,
                "paid_amount_usdc": self.required_usdc,
                "verified_on_chain": False,
                "mode": "test_simulation"
            }

        # --- 2. ON-CHAIN WEB3 TRANSACTION HASH VERIFICATION ---
        if proof_clean.startswith("0x") and len(proof_clean) == 66:
            try:
                receipt = await self._rpc_call("eth_getTransactionReceipt", [proof_clean], timeout=2.0)

                if not receipt:
                    return False, "Transaction unconfirmed, not found, or RPC lookup unavailable", {}

                # Status 1 indicates EVM execution success (hex '0x1' or integer 1)
                tx_status = receipt.get("status")
                if tx_status not in (1, "0x1", "1"):
                    return False, "Transaction execution failed or reverted on-chain", {}

                # Scan ERC-20 Transfer logs in receipt
                transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                logs = receipt.get("logs", [])

                for log in logs:
                    log_address = log.get("address", "").lower() if isinstance(log, dict) else log.address.lower()
                    if log_address == self.usdc_contract:
                        topics = log.get("topics", []) if isinstance(log, dict) else log.topics
                        raw_topics = [t.hex() if hasattr(t, "hex") else str(t) for t in topics]
                        
                        if len(raw_topics) >= 3 and raw_topics[0].lower() == transfer_topic.lower():
                            # Recipient address is in topic 2 (indexed bytes32)
                            to_address = "0x" + raw_topics[2][-40:].lower()
                            
                            data_hex = log.get("data", "0x") if isinstance(log, dict) else log.data
                            if hasattr(data_hex, "hex"):
                                data_hex = data_hex.hex()
                            
                            value_units = int(data_hex, 16) if data_hex and data_hex != "0x" else 0

                            if to_address == self.pay_to_wallet and value_units >= self.required_units:
                                # Atomically claim transaction proof in SQLite DB to prevent double-spending
                                claimed = await self._claim_proof_tx(proof_clean, token_clean, value_units / 1_000_000.0, now)
                                if not claimed:
                                    logger.warning(f"Replay attack detected for transaction hash: {proof_clean}")
                                    return False, "Transaction hash already used (double-spend rejected)", {}

                                block_num = receipt.get("blockNumber") if isinstance(receipt, dict) else receipt.blockNumber
                                return True, "On-chain USDC settlement verified", {
                                    "tx_hash": proof_clean,
                                    "paid_amount_usdc": value_units / 1_000_000.0,
                                    "verified_on_chain": True,
                                    "block_number": block_num
                                }

                return False, f"Transaction did not contain required USDC transfer to {self.pay_to_wallet}", {}

            except Exception as e:
                logger.error(f"Error checking on-chain tx receipt for {proof_clean}: {e}")
                return False, f"Payment verification error: {e!s}", {}

        return False, "Invalid payment proof format. Must be a valid 66-character Base L2 TX hash or signed proof.", {}

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
