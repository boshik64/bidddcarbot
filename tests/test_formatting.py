from app.formatting import filter_card_text, lot_caption, lots_page_text
from app.keyboards import LOTS_PAGE_SIZE, lots_page_keyboard
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


def _sample_lots(n: int) -> list[LotData]:
    return [
        LotData(
            lot_external_id=str(i),
            title=f"Car {i}",
            url=f"https://bid.cars/en/lot/{i}/car-{i}",
            current_bid=f"${i}00",
            location="Houston (TX)",
            status="На ходу",
        )
        for i in range(1, n + 1)
    ]


def test_lots_page_text_paginates() -> None:
    lots = _sample_lots(24)
    text = lots_page_text(3, "Toyota Camry", lots, page=1, page_size=LOTS_PAGE_SIZE)
    assert "Страница 2 из 3" in text
    assert "всего 24" in text
    assert "Car 11" in text
    assert "Car 20" in text
    assert "Car 10" not in text
    assert "Car 21" not in text
    assert "https://bid.cars/en/lot/11/car-11" in text


def test_lots_page_text_empty() -> None:
    text = lots_page_text(8, "BMW", [], page=0, page_size=LOTS_PAGE_SIZE)
    assert "нет лотов" in text
    assert "Страница 1 из 1" in text


def test_lots_page_keyboard_navigation() -> None:
    first = lots_page_keyboard(7, 0, 25)
    first_data = [btn.callback_data for row in first.inline_keyboard for btn in row]
    assert "flt:7:lots:1" in first_data
    assert "flt:7:lots:-1" not in first_data
    assert "flt:7:lots" in first_data
    assert "flt:7" in first_data

    last = lots_page_keyboard(7, 2, 25)
    last_data = [btn.callback_data for row in last.inline_keyboard for btn in row]
    assert "flt:7:lots:1" in last_data
    assert "flt:7:lots:3" not in last_data
