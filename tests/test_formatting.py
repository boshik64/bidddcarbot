from app.formatting import filter_card_text, lot_caption
from app.parser import LotData


def test_lot_caption_contains_core_fields() -> None:
    lot = LotData(
        lot_external_id="0-1",
        title="2018 BMW 530xi",
        url="https://bid.cars/en/lot/0-1/2018-BMW",
        current_bid="$625",
        damage="Сторона, Задняя часть",
        status="На ходу",
        location="Houston (TX)",
        vin="WBA123",
    )
    text = lot_caption(lot)
    assert "2018 BMW 530xi" in text
    assert "$625" in text
    assert "Сторона, Задняя часть" in text
    assert "На ходу" in text
    assert "Houston (TX)" in text
    assert "Открыть лот" in text


def test_filter_card_text() -> None:
    text = filter_card_text(
        12,
        "https://bid.cars/ru/search/results?make=BMW&model=5+Series&year-from=2018&year-to=2020",
        is_paused=True,
        last_checked="03.09.2026 08:51",
        interval_minutes=10,
        lots_count=87,
    )
    assert "#12" in text
    assert "BMW" in text
    assert "на паузе" in text
    assert "87" in text


def test_filter_card_text() -> None:
    text = filter_card_text(
        12,
        "https://bid.cars/ru/search/results?make=BMW&model=5+Series&year-from=2018&year-to=2020",
        is_paused=True,
        last_checked="03.09.2026 08:51",
        interval_minutes=10,
        lots_count=87,
    )
    assert "#12" in text
    assert "BMW" in text
    assert "на паузе" in text
    assert "87" in text
