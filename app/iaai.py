from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlparse, urlunparse

from app.parser import FilterUrlError, LotData, ParsedLotUrl, STATUS_RU, parse_lot_datetime

SOURCE = "iaai"
ALLOWED_HOSTS = {"iaai.com", "www.iaai.com"}
SEARCH_PATH_MARKERS = (
    "/search",
    "/vehiclelisting",
    "/vehiclelisting/",
)

_LOT_URL_RE = re.compile(
    r"^https?://(?:www\.)?iaai\.com"
    r"(?:/[a-z]{2}(?:-[a-z]{2})?)?"
    r"/VehicleDetail/(?P<item>\d+)(?:~(?P<tenant>[A-Za-z]{2}))?",
    re.I,
)

_HEADING_RE = re.compile(
    r'table-cell--heading[\s\S]{0,800}?href="/VehicleDetail/(?P<item>\d+)~(?P<tenant>[A-Za-z]{2})"'
    r"[^>]*>(?P<title>[\s\S]*?)</a>",
    re.I,
)
_WATCH_RE = re.compile(
    r"AddDelWatch\(\s*this\s*,\s*'(?P<item>\d+)~(?P<tenant>[A-Za-z]{2})'\s*,\s*'[^']*'\s*,\s*'(?P<when>[^']*)'",
    re.I,
)
_IMG_RE = re.compile(
    r'data-src="(https://vis\.iaai\.com/resizer\?imageKeys=(?P<item>\d+)~SID~I1[^"]*)"',
    re.I,
)
_STOCK_RE = re.compile(
    r'href="/VehicleDetail/(?P<item>\d+)~(?P<tenant>[A-Za-z]{2})"[^>]*>[\s\S]{0,400}?Stock\s*#:\s*</[^>]+>\s*(?P<stock>\d+)',
    re.I,
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = unescape(str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def _host_ok(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ALLOWED_HOSTS


def parse_lot_url(url: str) -> ParsedLotUrl | None:
    raw = (url or "").strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    # отрезаем длинные tracking-параметры, оставляем путь
    parsed = urlparse(raw)
    candidate = urlunparse(("https", "www.iaai.com", parsed.path, "", "", ""))
    match = _LOT_URL_RE.match(candidate) or _LOT_URL_RE.match(raw)
    if not match:
        # иногда ссылка приходит внутри длинного текста
        found = re.search(
            r"https?://(?:www\.)?iaai\.com(?:/[a-z]{2}(?:-[a-z]{2})?)?/VehicleDetail/\d+(?:~[A-Za-z]{2})?",
            raw,
            re.I,
        )
        if not found:
            return None
        return parse_lot_url(found.group(0))
    item = match.group("item")
    tenant = (match.group("tenant") or "US").upper()
    canonical = lot_page_url(item, tenant=tenant)
    return ParsedLotUrl(
        lot_id=f"i-{item}",
        url=canonical,
        lang="en",
        slug=None,
        source=SOURCE,
    )


def lot_page_url(item_id: str, *, tenant: str = "US") -> str:
    native = str(item_id).removeprefix("i-")
    return f"https://www.iaai.com/VehicleDetail/{native}~{tenant.upper()}"


def validate_filter_url(url: str) -> str:
    raw = url.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise FilterUrlError("Ссылка должна начинаться с http:// или https://")
    if not _host_ok(raw):
        raise FilterUrlError("Ссылка должна вести на домен iaai.com")
    path = parsed.path.lower()
    if "/vehicledetail/" in path:
        raise FilterUrlError(
            "Это ссылка на конкретный лот IAAI. Нужна ссылка на поиск "
            "(Search / Vehiclelisting) или вставь лот отдельно — подпишу на него."
        )
    if not any(marker in path for marker in SEARCH_PATH_MARKERS):
        raise FilterUrlError(
            "Не похоже на поиск IAAI. Открой выдачу на iaai.com "
            "и пришли URL вида https://www.iaai.com/Search?Keyword=... "
            "или https://www.iaai.com/Vehiclelisting/Cars/BMW"
        )
    # сохраняем query (Keyword / url=token), чистим tracking-мусор по возможности
    query_items = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith(("_gl", "_ga", "_fplc", "gclid", "fbclid"))
    ]
    from urllib.parse import urlencode

    cleaned = parsed._replace(
        scheme="https",
        netloc="www.iaai.com",
        query=urlencode(query_items, doseq=True),
        fragment="",
    )
    return urlunparse(cleaned)


def filter_title(url: str, fallback: str | None = None) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    keyword = _clean(params.get("Keyword") or params.get("keyword") or params.get("query"))
    if keyword:
        return f"IAAI · {keyword}"
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0].lower() == "vehiclelisting":
        label = " / ".join(parts[1:3]) if len(parts) > 1 else "Vehiclelisting"
        return f"IAAI · {label}"
    if params.get("url"):
        return "IAAI · сохранённый поиск"
    return fallback or "Фильтр IAAI"


def _parse_money(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return f"${int(value) if float(value).is_integer() else value}"
    text = _clean(value)
    if not text or text in {"$0", "$0.00", "0", "-"}:
        return None
    if text.startswith("$"):
        return text.split()[0]
    digits = re.sub(r"[^\d.]", "", text)
    if not digits:
        return None
    try:
        amount = float(digits)
    except ValueError:
        return text
    if amount <= 0:
        return None
    return f"${int(amount) if amount.is_integer() else amount}"


def _parse_iaai_datetime(value: Any) -> tuple[str | None, datetime | None]:
    text = _clean(value)
    if not text:
        return None, None
    # 11/16/2026 5:30:00 PM +00:00
    variants = [
        text,
        text.replace("\u202f", " ").replace("\u00a0", " "),
        re.sub(r"\s+", " ", text.replace("&#x202F;", " ").replace("&#x2B;", "+")),
    ]
    formats = (
        "%m/%d/%Y %I:%M:%S %p %z",
        "%m/%d/%Y %I:%M %p %z",
        "%m/%d/%Y %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for candidate in variants:
        normalized = candidate
        # +00:00 → +0000 for %z in older Python if needed; fromisoformat handles ISO
        try:
            if "T" in normalized or normalized.count("-") >= 2:
                iso = normalized.replace(" ", "T") if "T" not in normalized else normalized
                # ensure offset parseable
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return text, dt.astimezone(timezone.utc)
        except ValueError:
            pass
        for fmt in formats:
            try:
                stamp = normalized
                if re.search(r"[+-]\d{2}:\d{2}$", stamp):
                    stamp = stamp[:-3] + stamp[-2:]
                dt = datetime.strptime(stamp, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return text, dt.astimezone(timezone.utc)
            except ValueError:
                continue
    parsed = parse_lot_datetime(text)
    return text, parsed


def _table_map(block: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(block, dict):
        return out
    values = block.get("$values")
    if not isinstance(values, list):
        return out
    for row in values:
        if not isinstance(row, dict):
            continue
        key = _clean(row.get("key"))
        value = _clean(row.get("value"))
        if key and value:
            out[key] = value
    return out


def _extract_embedded_payload(html: str) -> dict[str, Any] | None:
    for match in re.finditer(
        r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
        html or "",
        re.I | re.S,
    ):
        body = (match.group(1) or "").strip()
        if not body or "inventoryView" not in body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "inventoryView" in data:
            return data
    return None


def _photo_urls(item_id: str, *, count: int = 8) -> list[str]:
    native = str(item_id).removeprefix("i-")
    urls = [
        f"https://vis.iaai.com/resizer?imageKeys={native}~SID~I{i}&width=800&height=600"
        for i in range(1, count + 1)
    ]
    return urls


def _map_start_code(value: str | None) -> str | None:
    if not value:
        return None
    mapped = STATUS_RU.get(value.strip().lower())
    if mapped:
        return mapped
    # IAAI: Run & Drive / Starts / Stationary
    lowered = value.strip().lower()
    if "run" in lowered and "drive" in lowered:
        return STATUS_RU.get("run / drive", value)
    if lowered.startswith("start"):
        return STATUS_RU.get("starts", value)
    if "station" in lowered:
        return STATUS_RU.get("stationary", value)
    return value


def lot_from_detail_html(html: str, *, page_url: str, lot_id: str | None = None) -> LotData | None:
    if not html:
        return None
    if "DetailsNotFoundView" in html or "Vehicle not found" in html:
        return None
    payload = _extract_embedded_payload(html)
    if payload is None:
        return None
    view = payload.get("inventoryView") or {}
    attrs = view.get("attributes") if isinstance(view, dict) else {}
    if not isinstance(attrs, dict):
        attrs = {}
    auction_info = payload.get("auctionInformation") or {}
    prebid = auction_info.get("prebidInformation") if isinstance(auction_info, dict) else {}
    bidding = auction_info.get("biddingInformation") if isinstance(auction_info, dict) else {}
    sale_table = _table_map(view.get("saleInformation") if isinstance(view, dict) else None)
    vehicle_info = _table_map(view.get("vehicleInformation") if isinstance(view, dict) else None)

    item = _clean(attrs.get("SalvageId") or attrs.get("Id") or view.get("itemId"))
    if item and "~" in item:
        item = item.split("~", 1)[0]
    if not item and lot_id:
        item = str(lot_id).removeprefix("i-")
    if not item:
        return None
    tenant = _clean(attrs.get("Tenant")) or "US"
    year = _clean(attrs.get("Year"))
    make = _clean(attrs.get("Make"))
    model = _clean(attrs.get("Model"))
    series = _clean(attrs.get("Series"))
    title = " ".join(p for p in (year, make, model, series) if p) or f"IAAI {item}"

    bid = (
        _parse_money(view.get("highBidAmount"))
        or _parse_money(view.get("decimalHighBidAmount"))
        or _parse_money((prebid or {}).get("highBidAmount"))
        or _parse_money((prebid or {}).get("decimalHighBidAmount"))
    )
    buy_now = _parse_money((bidding or {}).get("buyNowAmount")) or _parse_money(
        (bidding or {}).get("buyNowPrice")
    )

    auction_raw, auction_at = _parse_iaai_datetime(
        sale_table.get("AuctionDateTime")
        or (prebid or {}).get("liveDate")
        or (prebid or {}).get("adjustedCloseDate")
        or attrs.get("AuctionDateTime")
    )

    damage_parts = []
    for key in ("PrimaryDamageDesc", "SecondaryDamageDesc", "LossTypeDesc"):
        value = _clean(attrs.get(key)) or vehicle_info.get(key.replace("Desc", ""))
        if value and value not in damage_parts:
            damage_parts.append(value)
    if not damage_parts:
        for key in ("PrimaryDamage", "Loss"):
            value = vehicle_info.get(key)
            if value and value not in damage_parts:
                damage_parts.append(value)

    odo = attrs.get("ODOValue")
    miles = None
    try:
        if odo is not None and str(odo).strip() != "":
            miles = int(float(odo))
    except (TypeError, ValueError):
        miles = None
    km = int(miles * 1.609344) if miles is not None else None

    start = _clean(attrs.get("StartsDesc")) or vehicle_info.get("StartCode")
    status = _map_start_code(start)
    if attrs.get("RunAndDrive") is True and not status:
        status = STATUS_RU.get("run / drive", "На ходу")

    inventory_status = _clean(attrs.get("InventoryStatus"))
    search_status = "active"
    if inventory_status and inventory_status.upper() in {"SOLD", "SLD", "ARC", "CLOSED"}:
        search_status = "sold"
    if (prebid or {}).get("buyNowSold") is True:
        search_status = "sold"

    photos = _photo_urls(item)
    location = _clean(attrs.get("Name")) or vehicle_info.get("SellingBranch") or _clean(
        attrs.get("City")
    )
    if _clean(attrs.get("State")):
        location = f"{location} ({attrs.get('State').strip()})" if location else attrs.get("State").strip()

    return LotData(
        lot_external_id=f"i-{item}",
        title=title,
        url=page_url or lot_page_url(item, tenant=tenant),
        vin=_clean(attrs.get("VIN") or attrs.get("VINMask")),
        current_bid=bid,
        buy_now=buy_now,
        damage=", ".join(damage_parts) if damage_parts else None,
        status=status,
        location=location,
        photo_url=photos[0] if photos else None,
        photo_urls=photos,
        odometer_miles=miles,
        odometer_km=km,
        search_status=search_status,
        auction_at=auction_at,
        auction_raw=auction_raw,
        raw={"attributes": attrs, "sale": sale_table, "vehicle": vehicle_info},
        source=SOURCE,
    )


def parse_listing_html(html: str, *, source_url: str) -> list[LotData]:
    if not html:
        return []
    titles: dict[str, str] = {}
    for match in _HEADING_RE.finditer(html):
        item = match.group("item")
        title = _clean(re.sub(r"<[^>]+>", "", match.group("title") or ""))
        if item and title:
            titles[item] = title

    auctions: dict[str, str] = {}
    for match in _WATCH_RE.finditer(html):
        item = match.group("item")
        when = _clean(match.group("when"))
        if item and when:
            auctions[item] = when

    images: dict[str, str] = {}
    for match in _IMG_RE.finditer(html):
        item = match.group("item")
        if item:
            images[item] = unescape(match.group(1))

    stocks: dict[str, str] = {}
    for match in _STOCK_RE.finditer(html):
        stocks[match.group("item")] = match.group("stock")

    item_ids = list(dict.fromkeys([*titles.keys(), *auctions.keys(), *images.keys()]))
    lots: list[LotData] = []
    for item in item_ids:
        title = titles.get(item) or f"IAAI {item}"
        auction_raw, auction_at = _parse_iaai_datetime(auctions.get(item))
        photo = images.get(item) or _photo_urls(item, count=1)[0]
        lots.append(
            LotData(
                lot_external_id=f"i-{item}",
                title=title,
                url=lot_page_url(item),
                photo_url=photo,
                photo_urls=[photo],
                auction_at=auction_at,
                auction_raw=auction_raw,
                search_status="active",
                raw={"stock": stocks.get(item), "source_url": source_url},
                source=SOURCE,
            )
        )
    return lots
