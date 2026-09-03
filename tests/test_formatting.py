from app.formatting import lot_caption
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
