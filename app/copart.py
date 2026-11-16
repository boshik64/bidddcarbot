from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.parser import FilterUrlError, LotData, ParsedLotUrl, STATUS_RU, _format_price

SOURCE = "copart"
ALLOWED_HOSTS = {"copart.com", "www.copart.com"}
SEARCH_PATH_MARKERS = ("/lotsearchresults", "/vehicle-search", "/vehiclesearchresults")

_LOT_URL_RE = re.compile(
    r"^https?://(?:www\.)?copart\.com"
    r"(?:/(?P<lang>[a-z]{2}(?:-[a-z]{2})?))?"
    r"/lot/(?P<lot>\d+)(?:/(?P<slug>[^/?#]*))?",
    re.I,
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _host_ok(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ALLOWED_HOSTS


def parse_lot_url(url: str) -> ParsedLotUrl | None:
    raw = (url or "").strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    match = _LOT_URL_RE.match(raw)
    if not match:
        return None
    lot_id = match.group("lot")
    lang = (match.group("lang") or "en").lower()
    slug = (match.group("slug") or "").strip() or None
    canonical = lot_page_url(lot_id, slug=slug, lang=lang)
    return ParsedLotUrl(
        lot_id=f"c-{lot_id}",
        url=canonical,
        lang=lang if len(lang) == 2 else "en",
        slug=slug,
        source=SOURCE,
    )


def lot_page_url(lot_number: str, *, slug: str | None = None, lang: str = "en") -> str:
    native = str(lot_number).removeprefix("c-")
    path = f"/{lang}/lot/{native}"
    if slug:
        path = f"{path}/{slug}"
    return f"https://www.copart.com{path}"


def validate_filter_url(url: str) -> str:
    raw = url.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise FilterUrlError("Ссылка должна начинаться с http:// или https://")
    if not _host_ok(raw):
        raise FilterUrlError("Ссылка должна вести на домен copart.com")
    path = parsed.path.lower()
    if "/lot/" in path and re.search(r"/lot/\d+", path):
        raise FilterUrlError(
            "Это ссылка на конкретный лот Copart. Нужна ссылка на поиск "
            "(lotSearchResults / vehicle-search) или вставь лот отдельно — "
            "подпишу на него."
        )
    if not any(marker in path for marker in SEARCH_PATH_MARKERS):
        raise FilterUrlError(
            "Не похоже на поиск Copart. Открой выдачу на copart.com "
            "и пришли URL вида https://www.copart.com/lotSearchResults/?query=..."
        )
    cleaned = parsed._replace(scheme="https", netloc="www.copart.com", fragment="")
    return urlunparse(cleaned)


def filter_title(url: str, fallback: str | None = None) -> str:
    params = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    query = _clean(params.get("query") or params.get("free") or params.get("q"))
    if query and query.lower() not in {"true", "false", "*"}:
        return f"Copart · {query}"
    return fallback or "Фильтр Copart"


def search_body_from_filter_url(url: str, *, page: int = 0, size: int = 50) -> dict[str, Any]:
    params = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    query = _clean(params.get("query") or params.get("q")) or "*"
    free_form = query != "*"
    start = max(page, 0) * size
    return {
        "query": [query],
        "filter": {},
        "sort": ["auction_date_type desc", "auction_date_utc asc"],
        "page": max(page, 0),
        "size": size,
        "start": start,
        "watchListOnly": False,
        "freeFormSearch": free_form,
        "hideImages": False,
        "defaultSort": False,
        "specificRowAllRequired": False,
    }


def lot_details_api_url(lot_number: str) -> str:
    native = str(lot_number).removeprefix("c-")
    return f"https://www.copart.com/public/data/lotdetails/solr/{native}"


def search_api_url() -> str:
    return "https://www.copart.com/public/lots/search-results"


def _money(value: Any) -> str | None:
    if value is None or value == "" or value == 0 or value == 0.0:
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return f"${int(value) if float(value).is_integer() else value}"
    text = str(value).strip()
    if not text or text in {"0", "0.0", "$0", "$0.00"}:
        return None
    if text.startswith("$"):
        return text
    return _format_price(text) or f"${text}"


def _auction_from_ms(value: Any) -> tuple[str | None, datetime | None]:
    try:
        ms = int(float(value))
    except (TypeError, ValueError):
        return None, None
    if ms <= 0:
        return None, None
    # Copart иногда отдаёт секунды.
    if ms < 10_000_000_000:
        ms *= 1000
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC"), dt


def _odometer(item: dict[str, Any]) -> tuple[int | None, int | None]:
    raw = item.get("orr")
    try:
        miles = int(float(raw))
    except (TypeError, ValueError):
        return None, None
    if miles < 0:
        return None, None
    return miles, int(miles * 1.609344)


def _damage(item: dict[str, Any]) -> str | None:
    parts = []
    for key in ("dd", "sdd", "lcd"):
        value = _clean(item.get(key))
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _status(item: dict[str, Any]) -> str | None:
    code = _clean(item.get("lcd") or item.get("driveStatus"))
    if not code:
        return None
    mapped = STATUS_RU.get(code.strip().lower())
    return mapped or code


def _photos(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    tims = _clean(item.get("tims"))
    if tims:
        urls.append(tims)
        if "_thb." in tims:
            urls.append(tims.replace("_thb.", "."))
    return list(dict.fromkeys(urls))


def _title(item: dict[str, Any]) -> str:
    name = _clean(item.get("ld")) or _clean(item.get("lotDesc"))
    if name:
        return name
    bits = [_clean(item.get("lcy")), _clean(item.get("mkn")), _clean(item.get("lm"))]
    return " ".join(b for b in bits if b) or "Copart lot"


def _is_sold(item: dict[str, Any]) -> bool:
    if item.get("lotSold") is True:
        return True
    dyn = item.get("dynamicLotDetails") or {}
    if isinstance(dyn, dict) and dyn.get("lotSold") is True:
        return True
    sale = str((dyn or {}).get("saleStatus") or "").upper()
    return sale in {"SOLD", "SOLD_PENDING"}


def lot_from_details(payload: dict[str, Any], *, page_url: str | None = None) -> LotData | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    item = None
    if isinstance(data, dict):
        item = data.get("lotDetails")
    if not isinstance(item, dict):
        return None
    lot_number = _clean(item.get("ln") or item.get("lotNumberStr"))
    if not lot_number:
        return None
    dyn = item.get("dynamicLotDetails") if isinstance(item.get("dynamicLotDetails"), dict) else {}
    bid = _money(dyn.get("currentBid")) or _money(item.get("hb"))
    buy_now = _money(dyn.get("buyTodayBid")) or _money(item.get("bnp"))
    auction_raw, auction_at = _auction_from_ms(item.get("ad"))
    photos = _photos(item)
    miles, km = _odometer(item)
    sold = _is_sold(item)
    slug_bits = [
        "salvage",
        _clean(item.get("lcy")),
        _clean(item.get("mkn")),
        _clean(item.get("lm")),
    ]
    slug = "-".join(re.sub(r"[^A-Za-z0-9]+", "-", (b or "").lower()).strip("-") for b in slug_bits if b)
    url = page_url or lot_page_url(lot_number, slug=slug or None)
    return LotData(
        lot_external_id=f"c-{lot_number}",
        title=_title(item),
        url=url,
        vin=_clean(item.get("fv")),
        current_bid=bid,
        buy_now=buy_now,
        damage=_damage(item),
        status=_status(item),
        location=_clean(item.get("yn") or item.get("syn")),
        photo_url=photos[0] if photos else None,
        photo_urls=photos,
        odometer_miles=miles,
        odometer_km=km,
        search_status="sold" if sold else "active",
        auction_at=auction_at,
        auction_raw=auction_raw,
        raw=item,
        source=SOURCE,
    )


def lot_from_search_item(item: dict[str, Any]) -> LotData | None:
    if not isinstance(item, dict):
        return None
    lot_number = _clean(item.get("ln") or item.get("lotNumberStr"))
    if not lot_number:
        return None
    dyn = item.get("dynamicLotDetails") if isinstance(item.get("dynamicLotDetails"), dict) else {}
    bid = _money(dyn.get("currentBid")) or _money(item.get("hb"))
    auction_raw, auction_at = _auction_from_ms(item.get("ad") or item.get("auction_date_utc"))
    photos = _photos(item)
    miles, km = _odometer(item)
    sold = _is_sold(item)
    return LotData(
        lot_external_id=f"c-{lot_number}",
        title=_title(item),
        url=lot_page_url(lot_number),
        vin=_clean(item.get("fv")),
        current_bid=bid,
        buy_now=_money(dyn.get("buyTodayBid")),
        damage=_damage(item),
        status=_status(item),
        location=_clean(item.get("yn") or item.get("syn")),
        photo_url=photos[0] if photos else None,
        photo_urls=photos,
        odometer_miles=miles,
        odometer_km=km,
        search_status="sold" if sold else "active",
        auction_at=auction_at,
        auction_raw=auction_raw,
        raw=item,
        source=SOURCE,
    )


def parse_search_payload(payload: dict[str, Any]) -> tuple[list[LotData], dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else {}
    results = (data or {}).get("results") if isinstance(data, dict) else {}
    content = (results or {}).get("content") if isinstance(results, dict) else []
    lots: list[LotData] = []
    seen: set[str] = set()
    for item in content or []:
        lot = lot_from_search_item(item)
        if lot is None or lot.lot_external_id in seen:
            continue
        seen.add(lot.lot_external_id)
        lots.append(lot)
    total = (results or {}).get("totalElements") if isinstance(results, dict) else len(lots)
    meta = {
        "total": total,
        "last_page": None,
        "next_page_url": bool(lots)
        and isinstance(total, int)
        and total > len(lots),
    }
    try:
        size = int(((data or {}).get("query") or {}).get("size") or 50)
        page = int(((data or {}).get("query") or {}).get("page") or 0)
        total_i = int(total or 0)
        last_page = max((total_i + size - 1) // size - 1, 0) if size else 0
        meta["last_page"] = last_page
        meta["next_page_url"] = page < last_page
        meta["page"] = page
    except (TypeError, ValueError):
        pass
    return lots, meta
