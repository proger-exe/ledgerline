"""Blockchain access. Business logic talks to `provider(network)` only, never to TRON directly."""
from decimal import Decimal
from typing import Any, Protocol

import httpx
from tronpy import AsyncTron
from tronpy.exceptions import AddressNotFound, TransactionNotFound
from tronpy.keys import PrivateKey, is_base58check_address
from tronpy.providers.async_http import AsyncHTTPProvider

from app import config


class BlockchainProvider(Protocol):
    native: str  # gas coin

    def valid_address(self, address: str) -> bool: ...
    def explorer_url(self, tx_hash: str) -> str: ...
    async def get_balance(self, address: str, asset: str) -> Decimal: ...
    async def estimate_fee(self, destination: str, asset: str) -> Decimal: ...
    async def build_transaction(self, source: str, destination: str, asset: str, amount: Decimal) -> Any: ...
    def sign(self, tx: Any, private_key: str) -> dict: ...
    async def broadcast(self, signed: dict) -> str: ...
    async def get_transaction(self, tx_hash: str) -> dict | None: ...
    async def get_transfers(self, address: str, asset: str, since_ms: int) -> list[dict]: ...


class TronProvider:
    native = "TRX"
    NODES = {"mainnet": "https://api.trongrid.io", "nile": "https://nile.trongrid.io"}
    EXPLORERS = {"mainnet": "https://tronscan.org/#/transaction/", "nile": "https://nile.tronscan.org/#/transaction/"}
    TOKENS = {  # asset -> (contract, decimals)
        "mainnet": {"USDT": ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6)},
        "nile": {"USDT": ("TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf", 6)},
    }
    FEE_LIMIT_SUN = 100_000_000  # max 100 TRX burnt per transfer

    def __init__(self, network: str, api_key: str = ""):
        self.node = self.NODES[network]
        self.explorer = self.EXPLORERS[network]
        self.tokens = self.TOKENS[network]
        self.headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self.client = AsyncTron(AsyncHTTPProvider(self.node, api_key=api_key or None))
        self.http = httpx.AsyncClient(base_url=self.node, headers=self.headers, timeout=15)
        self._contracts = {}

    def valid_address(self, address: str) -> bool:
        try:
            return is_base58check_address(address)
        except ValueError:  # bad checksum/alphabet raises instead of returning False
            return False

    def explorer_url(self, tx_hash: str) -> str:
        return self.explorer + tx_hash

    async def _contract(self, asset: str):
        if asset not in self._contracts:
            self._contracts[asset] = await self.client.get_contract(self.tokens[asset][0])
        return self._contracts[asset]

    async def get_balance(self, address: str, asset: str) -> Decimal:
        if asset == self.native:
            try:
                return await self.client.get_account_balance(address)
            except AddressNotFound:
                return Decimal(0)
        raw = await (await self._contract(asset)).functions.balanceOf(address)
        return Decimal(raw) / 10 ** self.tokens[asset][1]

    async def estimate_fee(self, destination: str, asset: str) -> Decimal:
        """Worst case TRX burnt when the wallet has no staked energy."""
        # ponytail: fixed energy per TRC20 transfer; use wallet/estimateenergy + staked resources if fees matter.
        params = {p["key"]: p.get("value", 0) for p in await self.client.get_chain_parameters()}
        energy = 65_000 if await self.get_balance(destination, asset) > 0 else 131_000  # empty holder costs 2x
        sun = energy * params.get("getEnergyFee", 100) + 350 * params.get("getTransactionFee", 1000)
        return Decimal(sun) / 1_000_000

    async def build_transaction(self, source: str, destination: str, asset: str, amount: Decimal):
        contract, decimals = await self._contract(asset), self.tokens[asset][1]
        units = int((amount * 10**decimals).to_integral_exact())
        builder = await contract.functions.transfer(destination, units)
        return await builder.with_owner(source).fee_limit(self.FEE_LIMIT_SUN).build()

    def sign(self, tx, private_key: str) -> dict:
        return tx.sign(PrivateKey(bytes.fromhex(private_key))).to_json()

    async def broadcast(self, signed: dict) -> str:
        """Returns tx hash. Re-broadcasting the same tx is fine. Raises BroadcastRejected if the node refuses."""
        r = await self.http.post("/wallet/broadcasttransaction", json=signed)
        r.raise_for_status()
        payload = r.json()
        if payload.get("result") is True or payload.get("code") == "DUP_TRANSACTION_ERROR":
            return signed["txID"]
        msg = payload.get("message", "")
        try:
            msg = bytes.fromhex(msg).decode()
        except ValueError:
            pass
        raise BroadcastRejected(f"{payload.get('code', 'ERROR')}: {msg}")

    async def get_transaction(self, tx_hash: str) -> dict | None:
        try:
            info = await self.client.get_transaction_info(tx_hash)
        except TransactionNotFound:
            return None
        latest = await self.client.get_latest_block_number()
        return {
            "block": info["blockNumber"],
            "confirmations": max(latest - info["blockNumber"] + 1, 0),
            "success": info.get("receipt", {}).get("result", "SUCCESS") == "SUCCESS" and info.get("result") != "FAILED",
            "fee": Decimal(info.get("fee", 0)) / 1_000_000,
        }

    async def get_transfers(self, address: str, asset: str, since_ms: int) -> list[dict]:
        """Confirmed `asset` transfers in/out of `address` since `since_ms` (inclusive), oldest first."""
        # ponytail: one page of 200 per poll; the cursor catches up on the next tick.
        contract, decimals = self.tokens[asset]
        r = await self.http.get(f"/v1/accounts/{address}/transactions/trc20", params={
            "only_confirmed": "true", "contract_address": contract, "min_timestamp": since_ms,
            "order_by": "block_timestamp,asc", "limit": 200})
        r.raise_for_status()
        return [{"tx_hash": t["transaction_id"], "from": t["from"], "to": t["to"], "asset": asset,
                 "amount": Decimal(t["value"]) / 10**decimals, "ts": t["block_timestamp"]}
                for t in r.json().get("data", []) if t.get("type") == "Transfer"]


class BroadcastRejected(Exception):
    pass


_providers: dict[str, BlockchainProvider] = {}


def provider(network: str) -> BlockchainProvider:
    if network != "TRON":
        raise ValueError(f"Network {network} is not supported yet")
    if network not in _providers:
        _providers[network] = TronProvider(config.TRON_NETWORK, config.TRONGRID_API_KEY)
    return _providers[network]
