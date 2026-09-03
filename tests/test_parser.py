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
    assert first.url.startswith("https://bid.cars/ru/lot/0-45732519/")

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
