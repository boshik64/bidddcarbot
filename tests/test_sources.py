from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import copart, iaai
from app.parser import FilterUrlError
from app.sources import (
    SOURCE_COPART,
    SOURCE_IAAI,
    detect_source,
    extract_auction_url,
    parse_lot_url,
    validate_filter_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_detect_and_extract() -> None:
    assert detect_source("https://www.copart.com/ru/lot/75877886/x") == SOURCE_COPART
    assert detect_source("https://www.iaai.com/VehicleDetail/46522612~US") == SOURCE_IAAI
    text = "смотри https://www.copart.com/lot/12345/salvage-bmw и ещё"
    assert "copart.com/lot/12345" in (extract_auction_url(text) or "")


def test_parse_copart_lot_url() -> None:
    parsed = parse_lot_url(
        "https://www.copart.com/ru/lot/75877886/salvage-2020-bmw-530-xi-tx-houston-east"
    )
    assert parsed is not None
    assert parsed.source == SOURCE_COPART
    assert parsed.lot_id == "c-75877886"
    assert parsed.url.startswith("https://www.copart.com/ru/lot/75877886")


def test_parse_iaai_lot_url_strips_tracking() -> None:
    raw = (
        "https://www.iaai.com/VehicleDetail/46522612~US?"
        "_gl=1*abc*_ga*123"
    )
    parsed = parse_lot_url(raw)
    assert parsed is not None
    assert parsed.source == SOURCE_IAAI
    assert parsed.lot_id == "i-46522612"
    assert parsed.url == "https://www.iaai.com/VehicleDetail/46522612~US"


def test_validate_copart_and_iaai_filters() -> None:
    assert validate_filter_url(
        "https://www.copart.com/lotSearchResults/?free=true&query=bmw%20530"
    ).startswith("https://www.copart.com/lotSearchResults")
    assert validate_filter_url(
        "https://www.iaai.com/Vehiclelisting/Cars/BMW"
    ).startswith("https://www.iaai.com/Vehiclelisting/Cars/BMW")
    assert validate_filter_url(
        "https://www.iaai.com/Search?Keyword=toyota"
    ).startswith("https://www.iaai.com/Search")


def test_reject_wrong_copart_path() -> None:
    with pytest.raises(FilterUrlError):
        validate_filter_url("https://www.copart.com/login")


def test_copart_lot_from_details_fixture() -> None:
    payload = json.loads((FIXTURES / "copart_lot.json").read_text(encoding="utf-8"))
    lot = copart.lot_from_details(payload)
    assert lot is not None
    assert lot.lot_external_id == "c-75877886"
    assert "BMW" in lot.title.upper()
    assert lot.current_bid
    assert lot.auction_at is not None
    assert lot.source == SOURCE_COPART


def test_copart_search_fixture() -> None:
    path = FIXTURES / "copart_search.json"
    if not path.exists():
        pytest.skip("no copart search fixture")
    payload = json.loads(path.read_text(encoding="utf-8"))
    lots, meta = copart.parse_search_payload(payload)
    assert lots
    assert lots[0].lot_external_id.startswith("c-")
    assert meta.get("total") is not None


def test_iaai_lot_from_html_fixture() -> None:
    html = (FIXTURES / "iaai_lot.html").read_text(encoding="utf-8")
    lot = iaai.lot_from_detail_html(
        html,
        page_url="https://www.iaai.com/VehicleDetail/44995806~US",
    )
    assert lot is not None
    assert lot.lot_external_id == "i-44995806"
    assert lot.title.upper().startswith("2025 BMW")
    assert lot.odometer_miles == 3976
    assert lot.auction_at is not None
    assert lot.source == SOURCE_IAAI


def test_iaai_listing_fixture() -> None:
    html = (FIXTURES / "iaai_listing_snip.html").read_text(encoding="utf-8")
    lots = iaai.parse_listing_html(
        html, source_url="https://www.iaai.com/Vehiclelisting/Cars/BMW"
    )
    assert len(lots) >= 3
    assert all(lot.lot_external_id.startswith("i-") for lot in lots)
    assert any("BMW" in lot.title.upper() for lot in lots)
