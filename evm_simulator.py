import asyncio
import logging
import os
import time
from typing import Any

import httpx

# --- Fallback Hierarchy for ABI Decoding ---
ETH_ABI_AVAILABLE = False
WEB3_ABI_AVAILABLE = False

try:
    import eth_abi
    ETH_ABI_AVAILABLE = True
except ImportError:
    try:
        from web3 import Web3 as _W3
        # web3's stubs don't declare `eth.abi`; branch is unreachable in
        # practice since web3 hard-depends on eth_abi.
        web3_abi = _W3.eth.abi  # type: ignore[attr-defined]
        WEB3_ABI_AVAILABLE = True
    except ImportError:
        pass

try:
    from web3 import AsyncWeb3
    from web3.providers import AsyncHTTPProvider
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    # Optional-dependency sentinels; all uses are guarded by WEB3_AVAILABLE.
    AsyncWeb3 = None  # type: ignore[misc, assignment]
    AsyncHTTPProvider = None  # type: ignore[misc, assignment]

logger = logging.getLogger("evm_simulator")

# Base L2 Contracts & Simulation Hex Data
WETH_BASE = "0x4200000000000000000000000000000000000006"
AERODROME_ROUTER = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"
UNISWAP_V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_V3_QUOTER = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
SIMULATION_WALLET = "0x1111111111111111111111111111111111111111"

# On-Chain DEX Factory Addresses
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
AERODROME_FACTORY = "0x420DD381b31aEf6683db6b902084cB0FFECe40Da"

# EIP-1967 Proxy Storage Slot & Null Address Constants
EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
# Legacy ZeppelinOS slot: keccak("org.zeppelinos.proxy.implementation") — used by e.g. USDC (FiatTokenProxy)
ZOS_IMPLEMENTATION_SLOT = "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEAD_ADDRESS = "0x000000000000000000000000000000000000dead"

# --- Optimized Keccak-256 Cascade ---
try:
    from eth_utils import keccak as _native_keccak
    def _keccak256(data: bytes) -> bytes:
        return _native_keccak(data)
except ImportError:
    try:
        from Crypto.Hash import keccak as _crypto_keccak
        def _keccak256(data: bytes) -> bytes:
            return _crypto_keccak.new(digest_bits=256, data=data).digest()
    except ImportError:
        try:
            from web3 import Web3 as _W3
            def _keccak256(data: bytes) -> bytes:
                return _W3.keccak(data)
        except ImportError:
            def _keccak256(data: bytes) -> bytes:
                """Pure Python Keccak-256 (Ethereum variant)."""
                state = [0] * 25
                r = 1088 // 8
                padlen = r - (len(data) % r)
                padded = data + (b'\x81' if padlen == 1 else b'\x01' + b'\x00' * (padlen - 2) + b'\x80')

                RC = [
                    0x0000000000000001, 0x0000000000008082, 0x800000000000808a, 0x8000000080008000,
                    0x000000000000808b, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
                    0x000000000000008a, 0x0000000000000088, 0x0000000080008089, 0x000000008000000a,
                    0x000000008000808b, 0x800000000000008b, 0x8000000000008089, 0x8000000000008003,
                    0x8000000000008082, 0x8000000000000080, 0x000000000000800a, 0x800000008000000a,
                    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008
                ]
                rot = lambda x, n: ((x << n) & 0xffffffffffffffff) | (x >> (64 - n))

                for i in range(0, len(padded), r):
                    block = padded[i:i+r]
                    for j in range(r // 8):
                        state[j] ^= int.from_bytes(block[j*8:(j+1)*8], 'little')
                    
                    for round_idx in range(24):
                        C = [state[x] ^ state[x+5] ^ state[x+10] ^ state[x+15] ^ state[x+20] for x in range(5)]
                        D = [C[(x+4)%5] ^ rot(C[(x+1)%5], 1) for x in range(5)]
                        for idx in range(25):
                            state[idx] ^= D[idx % 5]
                        
                        last = state[1]
                        x, y = 1, 0
                        offsets = [
                            [0, 36, 3, 41, 18],
                            [1, 44, 10, 45, 2],
                            [62, 6, 43, 15, 61],
                            [28, 55, 25, 21, 56],
                            [27, 20, 39, 8, 14]
                        ]
                        for _ in range(24):
                            nx, ny = y, (2*x + 3*y) % 5
                            offset = offsets[x][y]
                            next_val = state[nx + 5*ny]
                            state[nx + 5*ny] = rot(last, offset)
                            last = next_val
                            x, y = nx, ny
                            
                        for y_idx in range(5):
                            row = [state[x_idx + 5*y_idx] for x_idx in range(5)]
                            for x_idx in range(5):
                                state[x_idx + 5*y_idx] = row[x_idx] ^ ((~row[(x_idx+1)%5]) & row[(x_idx+2)%5])
                        
                        state[0] ^= RC[round_idx]

                return b''.join(state[i].to_bytes(8, 'little') for i in range(4))


def _compute_storage_slot(owner: str, slot_index: int) -> str:
    """Computes storage slot for ERC-20 mapping(address => uint256)."""
    owner_bytes = bytes.fromhex(owner[2:].zfill(64))
    slot_bytes = slot_index.to_bytes(32, 'big')
    return "0x" + _keccak256(owner_bytes + slot_bytes).hex()


def _compute_allowance_slot(owner: str, spender: str, slot_index: int) -> str:
    """Computes storage slot for ERC-20 mapping(address => mapping(address => uint256))."""
    owner_bytes = bytes.fromhex(owner[2:].zfill(64))
    slot_bytes = slot_index.to_bytes(32, 'big')
    inner = _keccak256(owner_bytes + slot_bytes)
    spender_bytes = bytes.fromhex(spender[2:].zfill(64))
    return "0x" + _keccak256(spender_bytes + inner).hex()


def decode_abi_payload(types: list[str], hex_str: str) -> list[Any]:
    """
    Decodes ABI calldata/return payload using fallback hierarchy:
    1. eth_abi.decode()
    2. web3.eth.abi.decode()
    3. Pure Python ABI Decoder for uint256, uint256[], and address
    """
    clean_hex = hex_str.strip()
    clean_hex = clean_hex.removeprefix("0x")

    if not clean_hex:
        raise ValueError("Cannot decode empty hex payload")

    data_bytes = bytes.fromhex(clean_hex)

    # 1. Try eth_abi
    if ETH_ABI_AVAILABLE:
        try:
            return list(eth_abi.decode(types, data_bytes))
        except Exception as e:
            logger.debug(f"eth_abi decode failed, trying web3 fallback: {e}")

    # 2. Try web3.eth.abi
    if WEB3_ABI_AVAILABLE:
        try:
            return list(web3_abi.decode(types, data_bytes))
        except Exception as e:
            logger.debug(f"web3_abi decode failed, trying pure python fallback: {e}")

    # 3. Pure Python Decoder for uint256, uint256[] & address
    # Any: decoded ABI values are heterogeneous (int, list[int], hex str)
    results: list[Any] = []
    pos = 0

    for t in types:
        if t == "uint256":
            if len(clean_hex) < (pos + 64):
                raise ValueError("Hex string too short for uint256")
            val = int(clean_hex[pos : pos + 64], 16)
            results.append(val)
            pos += 64
        elif t == "uint256[]":
            if len(clean_hex) < (pos + 64):
                raise ValueError("Hex string too short for uint256[] offset")
            offset_bytes = int(clean_hex[pos : pos + 64], 16)
            offset_hex_idx = offset_bytes * 2

            if len(clean_hex) < (offset_hex_idx + 64):
                raise ValueError("Hex string too short for uint256[] length")

            array_len = int(clean_hex[offset_hex_idx : offset_hex_idx + 64], 16)
            elements = []
            curr_element_pos = offset_hex_idx + 64

            for _ in range(array_len):
                if len(clean_hex) < (curr_element_pos + 64):
                    break
                elements.append(int(clean_hex[curr_element_pos : curr_element_pos + 64], 16))
                curr_element_pos += 64

            results.append(elements)
            pos += 64
        elif t == "address":
            if len(clean_hex) < (pos + 64):
                raise ValueError("Hex string too short for address")
            addr_hex = "0x" + clean_hex[pos + 24 : pos + 64].lower()
            results.append(addr_hex)
            pos += 64
        else:
            raise NotImplementedError(f"Type {t} not supported in pure Python fallback ABI decoder")

    return results


class EVMTokenSimulator:
    def __init__(self, rpc_urls: Any | None = None):
        if isinstance(rpc_urls, str):
            self.rpc_urls = [u.strip() for u in rpc_urls.split(",") if u.strip()]
        elif isinstance(rpc_urls, list):
            self.rpc_urls = rpc_urls
        else:
            rpc_env = os.getenv("BASE_RPC_URLS", os.getenv("BASE_RPC_URL", "https://mainnet.base.org"))
            self.rpc_urls = [u.strip() for u in rpc_env.split(",") if u.strip()]

        if not self.rpc_urls:
            self.rpc_urls = ["https://mainnet.base.org"]

        self._rpc_rr_index = 0
        self.rpc_errors_total = 0

        # Shared httpx AsyncClient for connection pooling
        self._http_client: httpx.AsyncClient | None = None

        # In-memory LRU / TTL Caching (Capped at 1,000 entries max)
        self._sim_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._contract_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            self._http_client = httpx.AsyncClient(
                headers={"User-Agent": "BaseEVMAgent/1.1"},
                limits=limits,
                timeout=3.0
            )
        return self._http_client

    def _prune_cache_if_needed(self, cache_dict: dict[str, Any], max_size: int = 1000) -> None:
        """Enforces a strict max size cap of 1,000 entries on in-memory caches by evicting oldest timestamp."""
        if len(cache_dict) >= max_size:
            oldest_key = min(cache_dict, key=lambda k: cache_dict[k][0])
            del cache_dict[oldest_key]

    def get_cache_stats(self) -> tuple[int, int]:
        return self.cache_hits, self.cache_misses

    def _get_next_rpc_url(self) -> str:
        url = self.rpc_urls[self._rpc_rr_index % len(self.rpc_urls)]
        self._rpc_rr_index += 1
        return url

    async def _rpc_call(self, method: str, params: list, timeout: float = 2.5) -> Any:
        """Executes JSON-RPC payload against Base L2 RPC with failover retries."""
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
                        err = data["error"]
                        msg = str(err.get("message", "")).lower()
                        # Execution reverts are a definitive on-chain answer.
                        # Anything else (rate limit, invalid params, node
                        # hiccup) is a transport failure: try the next RPC.
                        if err.get("code") == 3 or "revert" in msg:
                            return None
                        self.rpc_errors_total += 1
                        logger.warning(f"RPC transient error ({method}) on {rpc_url}: {err}")
                        continue
                    return data.get("result")
                else:
                    self.rpc_errors_total += 1
                    logger.warning(f"RPC HTTP {resp.status_code} from {rpc_url}")
            except Exception as e:
                self.rpc_errors_total += 1
                logger.debug(f"RPC HTTP exception during {method} to {rpc_url}: {e}")

        return None

    async def _encode_eth_call(
        self,
        to_address: str,
        data: str,
        value: str = "0x0",
        from_address: str = SIMULATION_WALLET,
        state_override: dict[str, Any] | None = None,
        timeout: float = 2.5
    ) -> str | None:
        tx_obj = {
            "from": from_address,
            "to": to_address,
            "data": data,
            "value": value
        }
        params = [tx_obj, "latest"]
        if state_override:
            params.append(state_override)
        return await self._rpc_call("eth_call", params, timeout=timeout)

    async def _detect_dex_routing(self, token_address: str) -> tuple[bool, str, int, bool]:
        """
        Parallelized On-Chain DEX Detection via asyncio.gather:
        1-3: Uniswap V3 Factory (0x33128a8fC17869897dcE68Ed026d694621f6FDfD) getPool(token, WETH, fee) for fees [500, 3000, 10000]
        4: Aerodrome Volatile Pool getPool(token, WETH, false)
        5: Aerodrome Stable Pool getPool(token, WETH, true)
        6: Aerodrome V2 Fallback getPair(token, WETH)
        Returns: (is_v3, router_to_use, v3_fee, aero_stable)
        """
        token_clean = token_address.lower()
        token_padded = token_clean[2:].zfill(64)
        weth_padded = WETH_BASE[2:].zfill(64)

        v3_fees = [500, 3000, 10000]
        tasks = []

        # 1-3: Uniswap V3 fees
        for fee in v3_fees:
            fee_hex = hex(fee)[2:].zfill(64)
            calldata = "0x1698ee82" + token_padded + weth_padded + fee_hex
            tasks.append(self._encode_eth_call(UNISWAP_V3_FACTORY, calldata, timeout=1.5))

        # 4: Aerodrome Volatile Pool (stable=false, 0x342938a1 ... 0x0)
        aero_vol_calldata = "0x342938a1" + token_padded + weth_padded + "0"*64
        tasks.append(self._encode_eth_call(AERODROME_FACTORY, aero_vol_calldata, timeout=1.5))

        # 5: Aerodrome Stable Pool (stable=true, 0x342938a1 ... 0x1)
        aero_stb_calldata = "0x342938a1" + token_padded + weth_padded + "0"*63 + "1"
        tasks.append(self._encode_eth_call(AERODROME_FACTORY, aero_stb_calldata, timeout=1.5))

        # 6: Aerodrome V2 Fallback getPair (0xe6a43905)
        v2_calldata = "0xe6a43905" + token_padded + weth_padded
        tasks.append(self._encode_eth_call(AERODROME_FACTORY, v2_calldata, timeout=1.5))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check V3 fee pool results (indices 0, 1, 2)
        for idx, fee in enumerate(v3_fees):
            res = results[idx]
            if res and isinstance(res, str) and len(res) >= 66:
                pool_addr = "0x" + res[-40:].lower()
                if pool_addr != ZERO_ADDRESS:
                    return True, UNISWAP_V3_ROUTER, fee, False

        # Check Aerodrome Volatile (idx 3) and Stable (idx 4)
        for idx, stable in ((3, False), (4, True)):
            res = results[idx]
            if res and isinstance(res, str) and len(res) >= 66:
                pool_addr = "0x" + res[-40:].lower()
                if pool_addr != ZERO_ADDRESS:
                    return False, AERODROME_ROUTER, 0, stable

        # Check V2 fallback getPair (idx 5)
        res_v2 = results[5]
        if res_v2 and isinstance(res_v2, str) and len(res_v2) >= 66:
            pool_addr = "0x" + res_v2[-40:].lower()
            if pool_addr != ZERO_ADDRESS:
                return False, AERODROME_ROUTER, 0, False

        return False, AERODROME_ROUTER, 0, False

    async def _smart_storage_override(self, token_address: str, router_address: str) -> tuple[dict[str, Any], bool]:
        """
        Smart storage slot probing on likely slots [0, 1, 2, 3, 5, 6, 9] concurrently.
        Verifies balance of SIMULATION_WALLET via eth_call balanceOf(SIMULATION_WALLET).
        Overrides allowance at candidate indices [found_slot, found_slot + 1, found_slot + 2].
        Returns (state_override_dict, layout_discovered_bool).
        """
        wallet_padded = SIMULATION_WALLET[2:].zfill(64)
        bal_calldata = "0x70a08231" + wallet_padded
        # Top bit intentionally clear (2^255 - 1): tokens like USDC v2.2 pack a
        # blacklist flag into the balance slot's MSB; setting it would make the
        # override wallet appear blacklisted and revert every sell.
        max_uint256 = "0x7" + "f" * 63
        probed_slots = [0, 1, 2, 3, 5, 6, 9]

        # Concurrently probe candidate slots. The wallet may hold a real
        # pre-existing balance (0x1111...1111 is a common burn address), so a
        # slot only counts as discovered if balanceOf reflects the huge
        # override value itself, not merely any non-zero balance.
        override_threshold = 1 << 200

        async def _probe_slot(slot: int) -> tuple[int, bool]:
            bal_slot = _compute_storage_slot(SIMULATION_WALLET, slot)
            override = {
                token_address.lower(): {
                    "stateDiff": {bal_slot: max_uint256}
                }
            }
            res = await self._encode_eth_call(token_address, bal_calldata, state_override=override, timeout=1.5)
            if res and isinstance(res, str) and len(res) >= 66:
                try:
                    val = int(res[2:66], 16)
                    if val >= override_threshold:
                        return slot, True
                except Exception:
                    pass
            return slot, False

        probe_tasks = [_probe_slot(s) for s in probed_slots]
        probe_results = await asyncio.gather(*probe_tasks, return_exceptions=True)

        found_slot = None
        for r in probe_results:
            if isinstance(r, tuple) and r[1]:
                found_slot = r[0]
                break

        state_diff = {}
        if found_slot is not None:
            # Discovered exact storage mapping slot
            bal_slot = _compute_storage_slot(SIMULATION_WALLET, found_slot)
            state_diff[bal_slot] = max_uint256

            # Fix 1: Override allowance at found_slot, found_slot + 1, and found_slot + 2
            for allow_idx in [found_slot, found_slot + 1, found_slot + 2]:
                allow_slot = _compute_allowance_slot(SIMULATION_WALLET, router_address, allow_idx)
                state_diff[allow_slot] = max_uint256

            layout_discovered = True
        else:
            # Fall back to brute forcing slots 0-64
            for s in range(65):
                bal_slot = _compute_storage_slot(SIMULATION_WALLET, s)
                allow_slot = _compute_allowance_slot(SIMULATION_WALLET, router_address, s)
                state_diff[bal_slot] = max_uint256
                state_diff[allow_slot] = max_uint256
            layout_discovered = False

        state_override = {
            token_address.lower(): {
                "stateDiff": state_diff
            },
            SIMULATION_WALLET.lower(): {
                "balance": "0xDE0B6B3A7640000"  # 1 ETH
            }
        }
        return state_override, layout_discovered

    @staticmethod
    def _abi_word(value: "int | str") -> str:
        """Encode an int or 0x-hex string as one 64-char ABI word (prevents odd-length hex)."""
        if isinstance(value, int):
            word = hex(value)[2:]
        else:
            word = value.removeprefix("0x")
        if len(word) > 64:
            raise ValueError(f"ABI word overflow: {word}")
        return word.zfill(64)

    def _aero_route_tail(self, from_token: str, to_token: str, stable: bool) -> str:
        """ABI tail for Aerodrome Route[] of length 1: (from, to, stable, factory)."""
        w = self._abi_word
        return (
            w(1)                    # routes array length
            + w(from_token)
            + w(to_token)
            + w(1 if stable else 0)
            + w(AERODROME_FACTORY)
        )

    def _build_v2_buy_data(self, token_address: str, stable: bool = False, wallet_address: str = SIMULATION_WALLET) -> str:
        """Aerodrome Router swapExactETHForTokens(uint256,Route[],address,uint256) (0x903638a4)."""
        w = self._abi_word
        return (
            "0x903638a4"
            + w(0)                  # amountOutMin = 0
            + w(0x80)               # offset to routes array
            + w(wallet_address)     # to
            + "f" * 64              # deadline = max uint
            + self._aero_route_tail(WETH_BASE, token_address, stable)
        )

    def _build_v2_sell_data(self, token_address: str, sell_amount: int, stable: bool = False, wallet_address: str = SIMULATION_WALLET) -> str:
        """Aerodrome Router swapExactTokensForETH(uint256,uint256,Route[],address,uint256) (0xc6b7f1b6)."""
        w = self._abi_word
        return (
            "0xc6b7f1b6"
            + w(sell_amount)        # amountIn
            + w(0)                  # amountOutMin = 0
            + w(0xa0)               # offset to routes array
            + w(wallet_address)     # to
            + "f" * 64              # deadline = max uint
            + self._aero_route_tail(token_address, WETH_BASE, stable)
        )

    def _build_v2_buy_quote_data(self, token_address: str, stable: bool = False) -> str:
        """Aerodrome Router getAmountsOut(uint256,Route[]) (0x5509a1ac) for WETH -> Token."""
        w = self._abi_word
        return (
            "0x5509a1ac"
            + w(10**15)             # amountIn = 0.001 ETH
            + w(0x40)               # offset to routes array
            + self._aero_route_tail(WETH_BASE, token_address, stable)
        )

    def _build_v2_sell_quote_data(self, token_address: str, sell_amount: int, stable: bool = False) -> str:
        """Aerodrome Router getAmountsOut(uint256,Route[]) (0x5509a1ac) for Token -> WETH."""
        w = self._abi_word
        return (
            "0x5509a1ac"
            + w(sell_amount)        # amountIn
            + w(0x40)               # offset to routes array
            + self._aero_route_tail(token_address, WETH_BASE, stable)
        )

    def _encode_v3_path(self, path_hex: str) -> str:
        """ABI tail (len + right-padded bytes) for a V3 packed path."""
        path_bytes_len = len(path_hex) // 2
        padded = path_hex.ljust(64 * ((len(path_hex) + 63) // 64), "0")
        return self._abi_word(path_bytes_len) + padded

    def _build_v3_buy_data(self, token_address: str, fee: int = 3000, wallet_address: str = SIMULATION_WALLET) -> str:
        """SwapRouter02 exactInput((bytes,address,uint256,uint256)) (0xb858183f) for Buy (no deadline field)."""
        w = self._abi_word
        path_hex = WETH_BASE[2:] + hex(fee)[2:].zfill(6) + token_address[2:]
        return (
            "0xb858183f"
            + w(0x20)               # offset to params struct
            + w(0x80)               # offset to path within struct
            + w(wallet_address)     # recipient
            + w(10**15)             # amountIn = 0.001 ETH
            + w(0)                  # amountOutMinimum = 0
            + self._encode_v3_path(path_hex)
        )

    def _build_v3_sell_data(self, token_address: str, sell_amount: int, fee: int = 3000, wallet_address: str = SIMULATION_WALLET) -> str:
        """SwapRouter02 exactInput((bytes,address,uint256,uint256)) (0xb858183f) for Sell (no deadline field)."""
        w = self._abi_word
        path_hex = token_address[2:] + hex(fee)[2:].zfill(6) + WETH_BASE[2:]
        return (
            "0xb858183f"
            + w(0x20)
            + w(0x80)
            + w(wallet_address)
            + w(sell_amount)
            + w(0)
            + self._encode_v3_path(path_hex)
        )

    def _build_v3_quoter_data(self, token_address: str, fee: int = 3000) -> str:
        """QuoterV2 quoteExactInput(bytes,uint256) (0xcdca1753) for spot pricing (WETH -> Token)."""
        w = self._abi_word
        path_hex = WETH_BASE[2:] + hex(fee)[2:].zfill(6) + token_address[2:]
        return (
            "0xcdca1753"
            + w(0x40)               # offset to path bytes
            + w(10**15)             # amountIn = 0.001 ETH
            + self._encode_v3_path(path_hex)
        )

    def _build_v3_sell_quoter_data(self, token_address: str, sell_amount: int, fee: int = 3000) -> str:
        """QuoterV2 quoteExactInput(bytes,uint256) (0xcdca1753) for Token -> WETH sell pricing."""
        w = self._abi_word
        path_hex = token_address[2:] + hex(fee)[2:].zfill(6) + WETH_BASE[2:]
        return (
            "0xcdca1753"
            + w(0x40)
            + w(sell_amount)
            + self._encode_v3_path(path_hex)
        )

    async def _inspect_contract(self, token_clean: str) -> dict[str, Any]:
        """
        Performs bytecode verification, EIP-1967 proxy resolution,
        and multi-selector ownership checking asynchronously with sub-timeout (2.0s).
        Caches result for 300s (capped at 1,000 max entries).
        """
        now = time.time()
        if token_clean in self._contract_cache:
            ts, cached_info = self._contract_cache[token_clean]
            if now - ts < 300.0:
                return cached_info

        has_bytecode = False
        is_proxy = False
        implementation_address = None
        owner_address = None
        contract_renounced = True

        try:
            tasks = [
                self._rpc_call("eth_getCode", [token_clean, "latest"], timeout=2.0),
                self._rpc_call("eth_getStorageAt", [token_clean, EIP1967_IMPLEMENTATION_SLOT, "latest"], timeout=2.0),
                self._encode_eth_call(token_clean, "0x8da5cb5b", timeout=2.0),
                self._encode_eth_call(token_clean, "0x893d20e8", timeout=2.0),
                self._encode_eth_call(token_clean, "0xf851a101", timeout=2.0),
                self._rpc_call("eth_getStorageAt", [token_clean, ZOS_IMPLEMENTATION_SLOT, "latest"], timeout=2.0),
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            code_res = results[0] if not isinstance(results[0], Exception) else None
            storage_res = results[1] if not isinstance(results[1], Exception) else None
            owner_res = results[2] if not isinstance(results[2], Exception) else None
            get_owner_res = results[3] if not isinstance(results[3], Exception) else None
            admin_res = results[4] if not isinstance(results[4], Exception) else None
            zos_res = results[5] if not isinstance(results[5], Exception) else None

            # 1. Bytecode Existence
            has_bytecode = bool(code_res and isinstance(code_res, str) and len(code_res) > 2)

            # 2. Proxy Resolution: EIP-1967 slot first, legacy ZeppelinOS slot as fallback
            for slot_res in (storage_res, zos_res):
                if slot_res and isinstance(slot_res, str) and len(slot_res) >= 66:
                    raw_addr = "0x" + slot_res[-40:].lower()
                    if raw_addr != ZERO_ADDRESS:
                        imp_code = await self._rpc_call("eth_getCode", [raw_addr, "latest"], timeout=2.0)
                        if imp_code and isinstance(imp_code, str) and len(imp_code) > 2:
                            is_proxy = True
                            implementation_address = raw_addr
                            break

            # 3. Multi-Selector Ownership Checking
            resolved_owner = None
            for res in [owner_res, get_owner_res, admin_res]:
                if res and isinstance(res, str) and len(res) >= 66:
                    addr = "0x" + res[-40:].lower()
                    resolved_owner = addr
                    break

            if resolved_owner is not None:
                owner_address = resolved_owner
                contract_renounced = (resolved_owner == ZERO_ADDRESS or resolved_owner == DEAD_ADDRESS)
            else:
                owner_address = None
                contract_renounced = True

        except Exception as e:
            logger.warning(f"Contract inspection exception for {token_clean}: {e}")
            has_bytecode = False
            is_proxy = False
            implementation_address = None
            owner_address = None
            contract_renounced = True

        info = {
            "has_bytecode": has_bytecode,
            "is_proxy": is_proxy,
            "implementation_address": implementation_address,
            "owner_address": owner_address,
            "contract_renounced": contract_renounced
        }
        self._prune_cache_if_needed(self._contract_cache, max_size=1000)
        self._contract_cache[token_clean] = (now, info)
        return info

    async def analyze_token(self, token_address: str) -> dict[str, Any]:
        """
        Executes EVM state verification and transaction simulation against Base L2 RPC.
        Caches full analysis for 60 seconds (LRU/TTL, capped at 1,000 max entries).
        """
        token_clean = token_address.strip().lower()
        now = time.time()

        # Check In-Memory Cache (TTL: 60s)
        if token_clean in self._sim_cache:
            ts, cached_res = self._sim_cache[token_clean]
            if now - ts < 60.0:
                self.cache_hits += 1
                return cached_res

        self.cache_misses += 1
        client = self._get_http_client()

        # 1. Contract Analysis with 2.0s sub-timeout
        contract_analysis = await self._inspect_contract(token_clean)
        has_bytecode = contract_analysis["has_bytecode"]

        # 2. DEX Pool & Liquidity Lookup (DexScreener API with 1.5s sub-timeout)
        liquidity_usd = 0.0
        volume_24h = 0.0
        pair_address = None
        dex_id = "aerodrome"

        try:
            dex_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_clean}"
            dex_resp = await client.get(dex_url, timeout=1.5)
            if dex_resp.status_code == 200:
                dex_data = dex_resp.json()
                # Only consider Base-chain pairs; pick the deepest pool.
                pairs = [p for p in (dex_data.get("pairs") or []) if p.get("chainId") == "base"]
                if pairs:
                    top_pair = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
                    liquidity_usd = float((top_pair.get("liquidity") or {}).get("usd", 0.0))
                    volume_24h = float((top_pair.get("volume") or {}).get("h24", 0.0))
                    pair_address = top_pair.get("pairAddress")
                    dex_id = top_pair.get("dexId", "aerodrome")
        except Exception as e:
            logger.warning(f"DexScreener API lookup failed/timed out: {e}")

        # 3. Real EVM Transaction Simulation Logic
        simulated_buy_success = False
        simulated_sell_success = False
        effective_buy_tax = 0.0
        effective_sell_tax = 0.0
        unknown_storage_layout = False

        if token_clean == WETH_BASE.lower():
            # WETH is the quote asset itself: it cannot be routed against a
            # WETH pair. Wrapping/unwrapping is permissionless and untaxed.
            simulated_buy_success = True
            simulated_sell_success = True
        elif has_bytecode:
            # Parallelized On-Chain DEX Factory routing detection
            is_v3, router_to_use, v3_fee, aero_stable = await self._detect_dex_routing(token_clean)

            # Build Router Calldata according to Router type (V2 vs V3)
            if is_v3:
                buy_data = self._build_v3_buy_data(token_clean, fee=v3_fee)
                quote_res = await self._encode_eth_call(UNISWAP_V3_QUOTER, self._build_v3_quoter_data(token_clean, fee=v3_fee), timeout=1.5)
            else:
                buy_data = self._build_v2_buy_data(token_clean, stable=aero_stable)
                quote_res = await self._encode_eth_call(
                    router_to_use, self._build_v2_buy_quote_data(token_clean, stable=aero_stable), timeout=1.5
                )

            # Decode Quote using ABI decoding hierarchy
            expected_buy_tokens = 0
            if quote_res and isinstance(quote_res, str) and len(quote_res) >= 66:
                try:
                    if is_v3:
                        decoded_quote = decode_abi_payload(["uint256"], quote_res)
                        expected_buy_tokens = decoded_quote[0] if decoded_quote else 0
                    else:
                        decoded_quote = decode_abi_payload(["uint256[]"], quote_res)
                        expected_buy_tokens = decoded_quote[0][-1] if (decoded_quote and decoded_quote[0]) else 0
                except Exception as e:
                    logger.debug(f"Quote ABI decoding exception: {e}")
                    expected_buy_tokens = 0

            # Execute eth_call simulation for Buy (fund the simulation wallet:
            # some RPCs enforce the sender balance for value-bearing eth_call)
            buy_override = {SIMULATION_WALLET.lower(): {"balance": "0xDE0B6B3A7640000"}}  # 1 ETH
            buy_res = await self._encode_eth_call(
                router_to_use, buy_data, value="0x2386f26fc10000", state_override=buy_override, timeout=2.5
            )

            actual_buy_tokens = 0
            if buy_res and isinstance(buy_res, str) and len(buy_res) >= 66:
                simulated_buy_success = True
                try:
                    if is_v3:
                        decoded_buy = decode_abi_payload(["uint256"], buy_res)
                        actual_buy_tokens = decoded_buy[0] if decoded_buy else expected_buy_tokens
                    else:
                        decoded_buy = decode_abi_payload(["uint256[]"], buy_res)
                        actual_buy_tokens = decoded_buy[0][-1] if (decoded_buy and decoded_buy[0]) else expected_buy_tokens
                except Exception as e:
                    logger.debug(f"Buy result ABI decoding exception: {e}")
                    actual_buy_tokens = expected_buy_tokens

                if expected_buy_tokens > 0 and actual_buy_tokens > 0:
                    if expected_buy_tokens > actual_buy_tokens:
                        diff = expected_buy_tokens - actual_buy_tokens
                        effective_buy_tax = (diff / expected_buy_tokens) * 100.0
                    else:
                        effective_buy_tax = 0.0
                else:
                    effective_buy_tax = 0.0
            else:
                simulated_buy_success = False
                effective_buy_tax = 99.0

            # Execute Sell Simulation if Buy Succeeded
            if simulated_buy_success and actual_buy_tokens > 0:
                sell_amount = actual_buy_tokens  # Post-tax reality

                # Smart storage slot probing on slots [0,1,2,3,5,6,9]
                state_override, layout_discovered = await self._smart_storage_override(token_clean, router_to_use)
                logger.info(f"Storage layout for {token_clean}: discovered={layout_discovered}")

                # PRE-SELL BALANCE GATE (Audit Fix 1.2): Check balance with state override before attempting sell
                wallet_padded = SIMULATION_WALLET[2:].zfill(64)
                bal_gate_res = await self._encode_eth_call(token_clean, "0x70a08231" + wallet_padded, state_override=state_override, timeout=1.5)
                
                bal_gate_val = 0
                if bal_gate_res and isinstance(bal_gate_res, str) and len(bal_gate_res) >= 66:
                    try:
                        bal_gate_val = int(bal_gate_res[2:66], 16)
                    except Exception:
                        bal_gate_val = 0

                if bal_gate_val == 0:
                    logger.warning(f"Pre-sell balance gate failed for {token_clean}. Storage layout unknown.")
                    unknown_storage_layout = True
                    simulated_sell_success = False
                    effective_sell_tax = 99.0
                else:
                    # Dynamically Quote Expected Sell ETH return for sell_amount with fallback to buy input (10**15 wei = 0.001 ETH)
                    expected_sell_eth = 10**15  # Fallback: buy ETH input amount
                    try:
                        if is_v3:
                            sell_quote_res = await self._encode_eth_call(
                                UNISWAP_V3_QUOTER,
                                self._build_v3_sell_quoter_data(token_clean, sell_amount, fee=v3_fee),
                                timeout=0.8
                            )
                            if sell_quote_res and isinstance(sell_quote_res, str) and len(sell_quote_res) >= 66:
                                decoded_sell_quote = decode_abi_payload(["uint256"], sell_quote_res)
                                if decoded_sell_quote and decoded_sell_quote[0] > 0:
                                    expected_sell_eth = decoded_sell_quote[0]
                        else:
                            sell_quote_res = await self._encode_eth_call(
                                router_to_use,
                                self._build_v2_sell_quote_data(token_clean, sell_amount, stable=aero_stable),
                                timeout=0.8
                            )
                            if sell_quote_res and isinstance(sell_quote_res, str) and len(sell_quote_res) >= 66:
                                decoded_sell_quote = decode_abi_payload(["uint256[]"], sell_quote_res)
                                if decoded_sell_quote and decoded_sell_quote[0] and decoded_sell_quote[0][-1] > 0:
                                    expected_sell_eth = decoded_sell_quote[0][-1]
                    except Exception as e:
                        logger.debug(f"Sell quote exception/timeout, using buy amount fallback: {e}")

                    # Build sell calldata
                    if is_v3:
                        sell_data = self._build_v3_sell_data(token_clean, sell_amount, fee=v3_fee)
                    else:
                        sell_data = self._build_v2_sell_data(token_clean, sell_amount, stable=aero_stable)

                    # Run Sell Simulation
                    sell_res = await self._encode_eth_call(
                        router_to_use,
                        sell_data,
                        from_address=SIMULATION_WALLET,
                        state_override=state_override,
                        timeout=2.5
                    )

                    if sell_res and isinstance(sell_res, str) and len(sell_res) >= 66:
                        simulated_sell_success = True
                        actual_sell_eth = 0
                        try:
                            if is_v3:
                                decoded_sell = decode_abi_payload(["uint256"], sell_res)
                                actual_sell_eth = decoded_sell[0] if decoded_sell else expected_sell_eth
                            else:
                                decoded_sell = decode_abi_payload(["uint256[]"], sell_res)
                                actual_sell_eth = decoded_sell[0][-1] if (decoded_sell and decoded_sell[0]) else expected_sell_eth
                        except Exception as e:
                            logger.debug(f"Sell result ABI decoding exception: {e}")
                            actual_sell_eth = expected_sell_eth

                        if expected_sell_eth > actual_sell_eth > 0:
                            diff = expected_sell_eth - actual_sell_eth
                            effective_sell_tax = (diff / expected_sell_eth) * 100.0
                        else:
                            effective_sell_tax = 0.0
                    else:
                        simulated_sell_success = False
                        effective_sell_tax = 99.0
            else:
                simulated_sell_success = False
                effective_sell_tax = 99.0
        else:
            simulated_buy_success = False
            simulated_sell_success = False
            effective_buy_tax = 99.0
            effective_sell_tax = 99.0

        # Honeypot vs High Tax Classification Rules
        is_honeypot = not simulated_sell_success  # True only if sell simulation reverts/fails
        is_high_tax = effective_sell_tax > 10.0

        # Empirical Safety Score & Score Breakdown
        score_breakdown = {
            "base_score": 100,
            "buy_tax_deduction": 0.0,
            "sell_tax_deduction": 0.0,
            "high_liquidity_bonus": 0,
            "low_liquidity_penalty": 0,
            "renounced_bonus": 0,
            "non_proxy_bonus": 0,
            "final_score": 0
        }

        if is_honeypot:
            score = 0
            score_breakdown["final_score"] = 0
        else:
            score_val = 100.0
            buy_ded = min(30.0, effective_buy_tax * 2.0)
            sell_ded = min(40.0, effective_sell_tax * 3.0)
            score_val -= (buy_ded + sell_ded)

            score_breakdown["buy_tax_deduction"] = round(buy_ded, 2)
            score_breakdown["sell_tax_deduction"] = round(sell_ded, 2)

            if liquidity_usd > 100000:
                score_val += 5.0
                score_breakdown["high_liquidity_bonus"] = 5
            elif liquidity_usd < 1000:
                # A sellable token in a near-empty pool is still a rug/exit risk.
                score_val -= 50.0
                score_breakdown["low_liquidity_penalty"] = -50
            elif liquidity_usd < 10000:
                score_val -= 30.0
                score_breakdown["low_liquidity_penalty"] = -30
            if contract_analysis.get("contract_renounced"):
                score_val += 10.0
                score_breakdown["renounced_bonus"] = 10
            if has_bytecode and not contract_analysis.get("is_proxy"):
                score_val += 5.0
                score_breakdown["non_proxy_bonus"] = 5

            score = max(0, min(100, int(score_val)))
            score_breakdown["final_score"] = score

        # Recommendation Tiering
        if is_honeypot:
            recommendation = "HONEYPOT"
        elif is_high_tax:
            recommendation = "HIGH_TAX"
        elif score >= 80:
            recommendation = "SAFE_TO_TRADE"
        else:
            recommendation = "HIGH_RISK"

        result_payload = {
            "schema_version": "1.1",
            "token": token_clean,
            "chain": "base-mainnet",
            "simulation_results": {
                "simulated_buy_success": simulated_buy_success,
                "simulated_sell_success": simulated_sell_success,
                "effective_buy_tax_pct": round(effective_buy_tax, 2),
                "effective_sell_tax_pct": round(effective_sell_tax, 2),
                "is_honeypot": is_honeypot,
                "is_high_tax": is_high_tax,
                "safety_score": score,
                "unknown_storage_layout": unknown_storage_layout,
                "score_breakdown": score_breakdown
            },
            "contract_analysis": contract_analysis,
            "liquidity_metrics": {
                "liquidity_usd": liquidity_usd,
                "volume_24h": volume_24h,
                "primary_dex": dex_id or "aerodrome",
                "pair_address": pair_address
            },
            "recommendation": recommendation
        }

        # Cache result (enforce 1,000 max size cap)
        self._prune_cache_if_needed(self._sim_cache, max_size=1000)
        self._sim_cache[token_clean] = (now, result_payload)
        return result_payload

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
