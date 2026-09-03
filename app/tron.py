from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TX_HASH_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]{64})$")


class PaymentError(Exception):
    pass


@dataclass
class UsdtTransfer:
    tx_hash: str
    to_address: str
    from_address: str
    amount: Decimal
    contract: str
    confirmed: bool


def extract_tx_hash(raw: str) -> str:
    text = (raw or "").strip()
    match = re.search(r"(?:0x)?([0-9a-fA-F]{64})", text)
    if not match:
        raise PaymentError(
            "Это не похоже на TxID. Пришли 64-символьный хеш транзакции из кошелька."
        )
    return match.group(1).lower()


def normalize_tx_hash(raw: str) -> str:
    return extract_tx_hash(raw)


def amounts_equal(left: Decimal, right: Decimal) -> bool:
    q = Decimal("0.000001")
    return left.quantize(q) == right.quantize(q)


def parse_trongrid_trc20(item: dict) -> UsdtTransfer | None:
    token = item.get("token_info") or {}
    contract = token.get("address") or item.get("token_address") or ""
    raw_amount = item.get("value")
    if raw_amount is None:
        return None
    decimals = int(token.get("decimals") or 6)
    amount = Decimal(str(raw_amount)) / (Decimal(10) ** decimals)
    tx_hash = (item.get("transaction_id") or item.get("transactionId") or "").lower()
    if not tx_hash:
        return None
    return UsdtTransfer(
        tx_hash=tx_hash,
        to_address=item.get("to") or "",
        from_address=item.get("from") or "",
        amount=amount,
        contract=contract,
        confirmed=True,
    )


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if settings.trongrid_api_key:
        headers["TRON-PRO-API-KEY"] = settings.trongrid_api_key
    return headers


async def _fetch_recent_usdt(wallet: str, limit: int = 80) -> list[UsdtTransfer]:
    url = f"{settings.trongrid_base_url.rstrip('/')}/v1/accounts/{wallet}/transactions/trc20"
    params = {
        "limit": str(limit),
        "only_to": "true",
        "only_confirmed": "true",
        "contract_address": USDT_TRC20_CONTRACT,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, params=params, headers=_headers())
    if response.status_code != 200:
        raise PaymentError(f"TronGrid HTTP {response.status_code}")
    payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise PaymentError("Неожиданный ответ TronGrid")
    transfers: list[UsdtTransfer] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = parse_trongrid_trc20(item)
        if parsed is not None:
            transfers.append(parsed)
    return transfers


def matches_invoice(transfer: UsdtTransfer, wallet: str, amount: Decimal) -> bool:
    if transfer.to_address != wallet:
        return False
    if transfer.contract and transfer.contract != USDT_TRC20_CONTRACT:
        return False
    if not transfer.confirmed:
        return False
    return amounts_equal(transfer.amount, amount)


async def find_transfer_by_hash(tx_hash: str, wallet: str) -> UsdtTransfer:
    normalized = normalize_tx_hash(tx_hash)
    transfers = await _fetch_recent_usdt(wallet)
    for item in transfers:
        if item.tx_hash == normalized:
            return item
    raise PaymentError(
        "Транзакция не найдена среди входящих USDT на кошелёк. "
        "Проверь сеть (TRC20), адрес и что перевод уже подтверждён."
    )


async def find_unused_exact_payments(
    wallet: str,
    amount: Decimal,
    used_hashes: set[str],
) -> list[UsdtTransfer]:
    transfers = await _fetch_recent_usdt(wallet)
    found: list[UsdtTransfer] = []
    for item in transfers:
        if item.tx_hash in used_hashes:
            continue
        if matches_invoice(item, wallet, amount):
            found.append(item)
    return found
