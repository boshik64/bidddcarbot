from __future__ import annotations

import json
from pathlib import Path

from app.parser import (
    FilterUrlError,
    button_label,
    filter_url_to_api_url,
    label_from_url,
    lot_from_item,
    parse_search_json,
    validate_filter_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "search_page.json"


def test_validate_en_and_ru_search_urls() -> None:
    en = validate_filter_url(
        "https://bid.cars/en/search/results?search-type=filters&make=Toyota&model=Camry"
        "&year-from=2018&year-to=2026&status=All&type=Automobile&auction-type=All"
    )
    ru = validate_filter_url(
        "https://www.bid.cars/ru/search/results?search-type=filters&make=BMW&model=All"
        "&year-from=2015&year-to=2020"
    )
    assert en.startswith("https://bid.cars/en/search/results")
    assert "www." not in ru
    assert "/ru/search/results" in ru


def test_validate_archived_url() -> None:
    url = validate_filter_url(
        "https://bid.cars/en/search/archived/results?search-type=filters&make=Audi&model=A6"
    )
    assert "archived" in url


def test_reject_lot_url() -> None:
    try:
        validate_filter_url("https://bid.cars/en/lot/0-45732519/2021-Toyota-Camry-VIN")
        assert False, "should have raised"
    except FilterUrlError as exc:
        assert "лот" in str(exc).lower() or "конкретный" in str(exc)


def test_reject_other_domain() -> None:
    try:
        validate_filter_url("https://copart.com/search?year=2018")
        assert False, "should have raised"
    except FilterUrlError:
        pass


def test_api_url_keeps_filters_and_page() -> None:
    src = (
        "https://bid.cars/ru/search/results?search-type=filters&status=All"
        "&type=Automobile&make=Toyota&model=Camry&year-from=2018&year-to=2026"
        "&auction-type=All"
    )
    api = filter_url_to_api_url(src, page=2)
    assert api.startswith("https://bid.cars/app/search/request?")
    assert "make=Toyota" in api
    assert "model=Camry" in api
    assert "page=2" in api


def test_label_from_url() -> None:
    label = label_from_url(
        "https://bid.cars/en/search/results?make=Toyota&model=Camry&year-from=2018&year-to=2026"
    )
    assert "Toyota" in label
    assert "Camry" in label
    assert "2018" in label


def test_button_label_id_make_model_year() -> None:
    url = (
        "https://bid.cars/en/search/results?make=BMW&model=5+Series"
        "&year-from=2018&year-to=2020"
    )
    text = button_label(12, url)
    assert text.startswith("#12")
    assert "BMW" in text
    assert "5 Series" in text
    assert "2018" in text
    assert "2020" in text
    assert len(text) <= 64


def test_button_label_skips_all_model() -> None:
    url = "https://bid.cars/ru/search/results?make=Toyota&model=All&year-from=2015&year-to=2020"
    text = button_label(3, url)
    assert text == "#3 · Toyota · 2015–2020"


def test_button_label_truncated() -> None:
    url = (
        "https://bid.cars/en/search/results?make="
        + "VeryLongMakeName" * 5
        + "&model="
        + "VeryLongModelName" * 5
        + "&year-from=2010&year-to=2026"
    )
    text = button_label(99, url)
    assert len(text) <= 64
    assert text.startswith("#99")


def test_parse_fixture_lots() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source = "https://bid.cars/ru/search/results?make=Toyota&model=Camry"
    lots, meta = parse_search_json(payload, source)
    assert len(lots) == 2
    assert meta["last_page"] == 24
    assert meta["total"] == 1177

    first = lots[0]
    assert first.lot_external_id == "0-45732519"
    assert first.vin == "4T1G11AK1MU486913"
    assert first.title == "2021 Toyota Camry, SE"
    assert first.current_bid == "$400"
    assert "Collision" in (first.damage or "")
    assert "Right side" in (first.damage or "")
    assert first.status == "На ходу"
    assert first.location == "Hartford (CT)"
    assert first.photo_url.endswith("-1.jpg")
    assert first.photo_urls[0].startswith("https://pluto.bid.car/")
    assert len(first.photo_urls) == 2
    assert first.odometer_miles == 32431
    assert first.odometer_km == 52193
    assert first.url.startswith("https://bid.cars/ru/lot/0-45732519/")
    assert first.auction_raw == "вт 21 апр., 13:00 GMT+2"
    assert first.auction_at is not None
    assert first.auction_at.month == 4
    assert first.auction_at.day == 21
    assert first.time_left == "12 д 4 ч"

    second = lots[1]
    assert second.status == "Не на ходу"
    assert second.lot_external_id == "0-45495498"


def test_nested_status_success_envelope() -> None:
    inner = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = {"status": "success", "data": inner}
    lots, meta = parse_search_json(
        payload, "https://bid.cars/en/search/results?make=Toyota"
    )
    assert len(lots) == 2
    assert meta["next_page_url"].endswith("page=2")


def test_simple_paginator_stops_without_last_page() -> None:
    payload = {
        "current_page": 1,
        "data": [
            {
                "lot": "0-1",
                "name": "2020 Test Car",
                "tag": "2020-Test-Car",
                "vin": "TESTVIN123",
                "prebid_price": "$100",
                "start_code": "Run / Drive",
                "primary_damage": "Front end",
                "location": "Dallas (TX)",
                "img": {"img_1": "https://images.bid.cars/x.jpg"},
            }
        ],
        "next_page_url": None,
        "per_page": 50,
    }
    lots, meta = parse_search_json(
        payload, "https://bid.cars/en/search/results?make=Test"
    )
    assert len(lots) == 1
    assert meta.get("next_page_url") is None
    assert lots[0].url.startswith("https://bid.cars/en/lot/0-1/")


def test_skip_item_without_lot_id() -> None:
    lot = lot_from_item({"name": "broken"})
    assert lot is None


def test_lot_lookup_urls() -> None:
    from app.parser import lot_lookup_url

    active = lot_lookup_url("en", vin="4T1G11AK1MU486913", archived=False)
    assert "/en/search/results" in active
    assert "search-type=vin" in active
    assert "4T1G11AK1MU486913" in active
    archived = lot_lookup_url("ru", query="0-45732519", archived=True)
    assert "/ru/search/archived/results" in archived
    assert "search-type=text" in archived
    assert "0-45732519" in archived


def test_parse_auction_time_bidcars_format() -> None:
    from datetime import datetime, timezone

    from app.parser import parse_auction_time

    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    parsed = parse_auction_time("Tue 21 Apr, 13:00 GMT+2", now=now)
    assert parsed is not None
    assert parsed.year == 2027
    assert parsed.month == 4
    assert parsed.day == 21
    assert parsed.hour == 13
    assert parsed.utcoffset().total_seconds() == 2 * 3600


def test_ignores_zero_buy_now_close_time() -> None:
    lot = lot_from_item(
        {
            "lot": "1-52519866",
            "name": "2020 BMW 5 Series",
            "buy_now_close_time": 0,
            "prebid_close_time_lang": {
                "en": "Fri 11 Sep, 20:00 GMT+2",
                "ru": "пт 11 сент., 20:00 GMT+2",
            },
            "time_left": 159053,
            "time_left_formatted": "1 d 20 h 10 min",
            "prebid_price": "$550",
            "search_status": "active",
        },
        lang="ru",
    )
    assert lot is not None
    assert lot.auction_raw == "пт 11 сент., 20:00 GMT+2"
    assert lot.auction_raw != "0"
    assert lot.time_left == "1 д 20 ч 10 мин"
    assert lot.auction_at is not None
    assert lot.auction_at.month == 9
    assert lot.auction_at.day == 11
    assert lot.auction_at.hour == 20


def test_time_left_from_seconds_when_formatted_missing() -> None:
    from app.parser import format_time_left

    assert format_time_left(159053) == "1 д 20 ч 10 мин"
    lot = lot_from_item(
        {
            "lot": "0-1",
            "name": "Test",
            "buy_now_close_time": 0,
            "time_left": 90,
        }
    )
    assert lot is not None
    assert lot.auction_raw is None
    assert lot.time_left == "1 мин 30 сек"


def test_finished_search_status() -> None:
    from app.parser import is_finished_status

    assert is_finished_status("sold")
    assert is_finished_status("not sold")
    assert is_finished_status("archived")
    assert not is_finished_status("active")
    assert not is_finished_status("live")


LOT_HTML = Path(__file__).parent / "fixtures" / "lot_page.html"


def test_parse_lot_html_reads_bid_and_auction() -> None:
    from app.parser import parse_lot_html

    url = "https://bid.cars/en/lot/1-52519866/2020-BMW-5-Series-WBAJR3C09LWW79076"
    lot = parse_lot_html(LOT_HTML.read_text(encoding="utf-8"), url, lot_id="1-52519866")
    assert lot is not None
    assert lot.lot_external_id == "1-52519866"
    assert lot.current_bid == "$550"
    assert lot.final_bid is None
    assert lot.vin == "WBAJR3C09LWW79076"
    assert lot.title.startswith("2020 BMW 5 Series")
    assert lot.time_left == "1 д 19 ч 55 мин 12 сек"
    assert lot.auction_raw == "2026-09-11 20:00:00"
    assert lot.auction_at is not None
    assert lot.auction_at.year == 2026
    assert lot.auction_at.month == 9
    assert lot.auction_at.day == 11
    assert lot.auction_at.hour == 20
    assert lot.auction_at.utcoffset().total_seconds() == 2 * 3600
    assert lot.search_status == "active"


def test_parse_lot_html_sold_uses_final_bid() -> None:
    from app.parser import is_lot_finished, parse_lot_html

    html = """
    <html><head><title>2020 BMW 5 Series | BidCars</title></head>
    <body>
    <script>
      var currentBid = 0;
      var finalBid = 2100;
      var lotNumber = '1-52519866';
      var liveAuctionStartDateTime = '2026-09-11 20:00:00';
    </script>
    </body></html>
    """
    lot = parse_lot_html(html, "https://bid.cars/en/lot/1-52519866/bmw", "1-52519866")
    assert lot is not None
    assert lot.final_bid == "$2100"
    assert lot.current_bid == "$2100"
    assert lot.search_status == "sold"
    assert is_lot_finished(lot)
    assert lot.time_left is None


def test_parse_lot_html_rejects_search_page() -> None:
    from app.parser import parse_lot_html

    html = "<html><body>Active lots search results</body></html>"
    assert parse_lot_html(html, "https://bid.cars/en/search/results", "1-52519866") is None
