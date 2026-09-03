from datetime import timedelta
from decimal import Decimal

from app.access import format_usdt, grant_lifetime, grant_plan, has_access, plan_amount
from app.models import User, utcnow
from app.tron import PaymentError, amounts_equal, extract_tx_hash, matches_invoice, parse_trongrid_trc20


def test_has_access_lifetime_and_expiry() -> None:
    user = User(telegram_chat_id=1)
    assert has_access(user) is False
    grant_lifetime(user)
    assert has_access(user) is True

    user2 = User(telegram_chat_id=2)
    grant_plan(user2, "month", now=utcnow() - timedelta(days=31))
    assert has_access(user2) is False
    grant_plan(user2, "month", now=utcnow())
    assert has_access(user2) is True


def test_plan_amount_defaults() -> None:
    assert plan_amount("month") == Decimal("5.000000")
    assert plan_amount("year") == Decimal("50.000000")
    assert format_usdt(plan_amount("month")) == "5"
    assert format_usdt(plan_amount("year")) == "50"


def test_parse_trongrid_and_match_plan_amount() -> None:
    item = {
        "transaction_id": "ab" * 32,
        "to": "TKXbcn4tpzudTc66CP65prmE6rcgmTybAs",
        "from": "TFromWallet11111111111111111111111",
        "value": "5000000",
        "token_info": {
            "address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            "decimals": 6,
        },
    }
    transfer = parse_trongrid_trc20(item)
    assert transfer is not None
    assert transfer.from_address.startswith("TFrom")
    assert amounts_equal(transfer.amount, Decimal("5"))
    assert matches_invoice(
        transfer,
        "TKXbcn4tpzudTc66CP65prmE6rcgmTybAs",
        Decimal("5"),
    )
    assert not matches_invoice(
        transfer,
        "TKXbcn4tpzudTc66CP65prmE6rcgmTybAs",
        Decimal("50"),
    )


def test_extract_tx_hash() -> None:
    tx = "a" * 64
    assert extract_tx_hash(f"https://tronscan.org/#/transaction/{tx}") == tx
    try:
        extract_tx_hash("not-a-hash")
        raise AssertionError("expected PaymentError")
    except PaymentError:
        pass
