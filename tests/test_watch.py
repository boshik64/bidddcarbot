from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.parser import LotData, is_lot_finished, lot_from_preview, lot_to_preview
from app.watch import remaining_from_auction, reminder_due, watch_changes


def test_reminder_due_windows() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    in_20h = now + timedelta(hours=20)
    in_90m = now + timedelta(minutes=90)
    in_2d = now + timedelta(days=2)

    assert reminder_due(in_20h, now=now, reminded_24h=False, reminded_2h=False) == "24h"
    assert reminder_due(in_20h, now=now, reminded_24h=True, reminded_2h=False) is None
    assert reminder_due(in_90m, now=now, reminded_24h=False, reminded_2h=False) == "2h"
    assert reminder_due(in_90m, now=now, reminded_24h=True, reminded_2h=True) is None
    assert reminder_due(in_2d, now=now, reminded_24h=False, reminded_2h=False) is None
    assert remaining_from_auction(now + timedelta(hours=2, minutes=5), now=now) == "2 ч 5 мин"
    assert remaining_from_auction(now - timedelta(minutes=1), now=now) == "идёт аукцион"


def test_apply_lot_keeps_reminder_flags() -> None:
    from app.watch import apply_lot_to_watch

    stored = datetime(2026, 9, 11, 20, 0)
    live = datetime(2026, 9, 11, 20, 0, tzinfo=timezone(timedelta(hours=2)))
    row = SimpleNamespace(
        title="BMW",
        lot_url="https://bid.cars/en/lot/1-1/bmw",
        vin=None,
        last_bid="$550",
        last_search_status="active",
        last_status=None,
        auction_at=stored,
        auction_raw="пт 11 сент., 20:00 GMT+2",
        raw_data=None,
        reminded_24h=True,
        reminded_2h=False,
        last_checked_at=None,
        consecutive_misses=0,
    )
    apply_lot_to_watch(
        row,
        LotData(
            lot_external_id="1-1",
            title="BMW",
            url="https://bid.cars/en/lot/1-1/bmw",
            current_bid="$575",
            auction_at=live,
            auction_raw="2026-09-11 20:00:00",
        ),
    )
    assert row.reminded_24h is True
    assert row.reminded_2h is False
    assert row.last_bid == "$575"


def test_watch_changes_price_and_auction() -> None:
    row = SimpleNamespace(
        last_bid="$400",
        last_status="На ходу",
        auction_raw="Tue 21 Apr, 13:00 GMT+2",
    )
    lot = LotData(
        lot_external_id="0-1",
        title="BMW",
        url="https://bid.cars/en/lot/0-1/bmw",
        current_bid="$625",
        status="На ходу",
        auction_raw="Wed 22 Apr, 13:00 GMT+2",
    )
    changes = watch_changes(row, lot)
    assert any(" $400 → $625" in item for item in changes)
    assert any("Аукцион" in item for item in changes)
    same = watch_changes(row, LotData(
        lot_external_id="0-1",
        title="BMW",
        url="https://bid.cars/en/lot/0-1/bmw",
        current_bid="$400",
        status="На ходу",
        auction_raw="Tue 21 Apr, 13:00 GMT+2",
    ))
    assert same == []


def test_lot_finished_from_archive() -> None:
    lot = LotData(
        lot_external_id="0-1",
        title="BMW",
        url="https://bid.cars/en/lot/0-1/bmw",
        search_status="active",
        final_bid="$2100",
    )
    assert is_lot_finished(lot, source="archived")
    assert not is_lot_finished(lot, source="active")
    lot.search_status = "sold"
    assert is_lot_finished(lot, source="active")


def test_preview_roundtrip_keeps_auction() -> None:
    original = LotData(
        lot_external_id="0-1",
        title="BMW",
        url="https://bid.cars/en/lot/0-1/bmw",
        current_bid="$400",
        auction_raw="Tue 21 Apr, 13:00 GMT+2",
        auction_at=datetime(2027, 4, 21, 13, 0, tzinfo=timezone(timedelta(hours=2))),
        search_status="active",
    )
    restored = lot_from_preview(lot_to_preview(original))
    assert restored.lot_external_id == "0-1"
    assert restored.current_bid == "$400"
    assert restored.auction_raw == original.auction_raw
    assert restored.auction_at is not None
    assert restored.auction_at.isoformat() == original.auction_at.isoformat()


def test_watch_changes_ignores_auction_text_when_time_same() -> None:
    when = datetime(2026, 9, 11, 20, 0, tzinfo=timezone(timedelta(hours=2)))
    row = SimpleNamespace(
        last_bid="$550",
        last_status=None,
        auction_raw="пт 11 сент., 20:00 GMT+2",
        auction_at=when,
    )
    lot = LotData(
        lot_external_id="1-52519866",
        title="BMW",
        url="https://bid.cars/en/lot/1-52519866/bmw",
        current_bid="$550",
        auction_raw="2026-09-11 20:00:00",
        auction_at=when,
    )
    assert watch_changes(row, lot) == []
