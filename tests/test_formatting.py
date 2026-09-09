from app.formatting import (
    filter_card_text,
    lot_caption,
    lots_page_text,
    watch_list_text,
    watch_reminder_text,
    watch_sale_text,
    watch_update_text,
)
from app.keyboards import LOTS_PAGE_SIZE, lot_watch_keyboard, lots_page_keyboard
from app.lot_send import album_photo_urls, lot_article_html, slideshow_rich_message
from app.parser import LotData, TG_ALBUM_MAX, TG_SLIDESHOW_MAX, format_mileage


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
        odometer_miles=32431,
        odometer_km=52193,
    )
    text = lot_caption(lot)
    assert "2018 BMW 530xi" in text
    assert "$625" in text
    assert "32 431 mi / 52 193 км" in text
    assert "Сторона, Задняя часть" in text
    assert "На ходу" in text
    assert "Houston (TX)" in text
    assert "Открыть лот" in text
    lot.auction_raw = "Tue 21 Apr, 13:00 GMT+2"
    assert "Аукцион" in lot_caption(lot)
    assert "Tue 21 Apr" in lot_caption(lot)


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
    assert "Активных лотов" in text


def _sample_lots(n: int) -> list[LotData]:
    return [
        LotData(
            lot_external_id=str(i),
            title=f"Car {i}",
            url=f"https://bid.cars/en/lot/{i}/car-{i}",
            current_bid=f"${i}00",
            location="Houston (TX)",
            status="На ходу",
            odometer_miles=1000 * i,
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
    assert "mi /" in text


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
    assert "flt:7:ph:0" in first_data
    assert "flt:7:ph:20" in last_data
    assert "wch:t:" not in " ".join(first_data)

    hearts = lots_page_keyboard(
        7, 0, 25, page_lot_ids=["0-1", "0-2"], watched_ids={"0-1"}
    )
    heart_data = [btn.callback_data for row in hearts.inline_keyboard for btn in row]
    heart_labels = [btn.text for row in hearts.inline_keyboard for btn in row]
    assert "wch:t:0-1" in heart_data
    assert "wch:t:0-2" in heart_data
    assert any(text.startswith("❤️") for text in heart_labels)
    assert any(text.startswith("🤍") for text in heart_labels)


def test_format_mileage_converts_miles_to_km() -> None:
    assert format_mileage(32431, None) == "32 431 mi / 52 193 км"
    assert format_mileage(None, None) is None


def test_album_never_exceeds_telegram_limit() -> None:
    urls = [f"https://pluto.bid.car/x-{i}.jpg" for i in range(1, 21)]
    lot = LotData(
        lot_external_id="1",
        title="Car",
        url="https://bid.cars/en/lot/1/car",
        photo_urls=urls,
    )
    album = album_photo_urls(lot, limit=TG_ALBUM_MAX)
    assert len(album) == TG_ALBUM_MAX
    assert album[0].endswith("-1.jpg")
    slideshow = album_photo_urls(lot)
    assert len(slideshow) == 20

    crowded = LotData(
        lot_external_id="2",
        title="Car",
        url="https://bid.cars/en/lot/2/car",
        photo_urls=[f"https://pluto.bid.car/y-{i}.jpg" for i in range(TG_SLIDESHOW_MAX + 5)],
    )
    assert len(album_photo_urls(crowded)) == TG_SLIDESHOW_MAX


def test_lot_article_html_wraps_photos_in_slideshow() -> None:
    html = lot_article_html("<b>BMW</b>\n<a href=\"https://bid.cars/x\">лот</a>", ["p0", "p1", "p2"])
    assert html.startswith("<tg-slideshow>")
    assert '<img src="p0">' in html
    assert '<img src="p2">' in html
    assert "</tg-slideshow>" in html
    assert "<slideshow>" not in html
    assert "<p><b>BMW</b></p>" in html
    assert "https://bid.cars/x" in html


def test_slideshow_rich_message_payload() -> None:
    payload = slideshow_rich_message("hello", ["https://a.jpg", "https://b.jpg"])
    assert payload["html"].startswith("<tg-slideshow>")
    assert '<img src="tg://photo?id=p0">' in payload["html"]
    assert payload["media"][0]["id"] == "p0"
    assert payload["media"][0]["media"]["type"] == "photo"
    assert payload["media"][1]["media"]["media"] == "https://b.jpg"


def test_watch_messages() -> None:
    update = watch_update_text(
        "BMW",
        "https://bid.cars/en/lot/0-1/bmw",
        ["💰 Ставка: $400 → $625"],
    )
    assert "Обновление" in update
    assert "$625" in update
    reminder = watch_reminder_text(
        "BMW",
        "https://bid.cars/en/lot/0-1/bmw",
        "24h",
        bid="$625",
        auction_raw="Tue 21 Apr, 13:00 GMT+2",
    )
    assert "24 часа" in reminder
    sale = watch_sale_text(
        "BMW",
        "https://bid.cars/en/lot/0-1/bmw",
        price="$2 100",
        search_status="sold",
    )
    assert "продан" in sale.lower()
    assert "$2 100" in sale
    listing = watch_list_text(
        [("BMW", "https://bid.cars/en/lot/0-1/bmw", "$400", "Tue 21 Apr")],
        page=0,
        page_size=8,
        total=1,
    )
    assert "Отслеживаемые" in listing
    assert "$400" in listing


def test_lot_watch_keyboard_toggles_label() -> None:
    on = lot_watch_keyboard("0-1", True)
    off = lot_watch_keyboard("0-1", False)
    assert on.inline_keyboard[0][0].callback_data == "wch:t:0-1"
    assert "Отслеживаю" in on.inline_keyboard[0][0].text
    assert "Отслеживать" in off.inline_keyboard[0][0].text
