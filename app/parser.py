from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ALLOWED_HOSTS = {"bid.cars", "www.bid.cars"}
SEARCH_PATH_MARKERS = ("/search/results", "/search/archived/results")
LANGS = ("en", "ru", "ua", "pl")

STATUS_RU = {
    "run / drive": "На ходу",
    "run and drive": "На ходу",
    "run/drive": "На ходу",
    "starts": "Заводится",
    "stationary": "Не на ходу",
    "no information": "Нет информации",
}


class FilterUrlError(ValueError):
    pass


KM_PER_MILE = 1.609344
TG_ALBUM_MAX = 10
TG_SLIDESHOW_MAX = 50


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

ACTIVE_SEARCH_STATUSES = {"active", "live", "upcoming", "prebid", "buy now", "buy_now"}
FINISHED_SEARCH_STATUSES = {
    "sold",
    "unsold",
    "not sold",
    "not_sold",
    "archived",
    "ended",
    "closed",
    "finished",
    "completed",
    "history",
}

_AUCTION_TIME_RE = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3})[a-z]*,?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?:\s*(?:GMT|UTC)\s*(?P<sign>[+-])(?P<offh>\d{1,2})(?::?(?P<offm>\d{2}))?)?",
    re.IGNORECASE,
)


@dataclass
class LotData:
    lot_external_id: str
    title: str
    url: str
    vin: str | None = None
    current_bid: str | None = None
    buy_now: str | None = None
    final_bid: str | None = None
    damage: str | None = None
    status: str | None = None
    location: str | None = None
    photo_url: str | None = None
    photo_urls: list[str] = field(default_factory=list)
    odometer_miles: int | None = None
    odometer_km: int | None = None
    search_status: str | None = None
    auction_at: datetime | None = None
    auction_raw: str | None = None
    time_left: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LotFetch:
    lot: LotData | None
    source: str  # active | archived | missing


def _normalize_host(host: str) -> str:
    return host.lower().removeprefix("www.")


def extract_lang(url: str) -> str:
    path = urlparse(url).path.lower()
    parts = [p for p in path.split("/") if p]
    if parts and parts[0] in LANGS:
        return parts[0]
    return "en"


def validate_filter_url(url: str) -> str:
    raw = url.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise FilterUrlError("Ссылка должна начинаться с http:// или https://")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise FilterUrlError("Ссылка должна вести на домен bid.cars")
    path = parsed.path.lower()
    if "/lot/" in path:
        raise FilterUrlError(
            "Это ссылка на конкретный лот. Нужна ссылка на страницу поиска "
            "с фильтрами (bid.cars → поиск → скопировать URL из адресной строки)."
        )
    if not any(marker in path for marker in SEARCH_PATH_MARKERS):
        raise FilterUrlError(
            "Не похоже на ссылку фильтра. Открой на bid.cars нужный поиск "
            "и пришли URL вида https://bid.cars/ru/search/results?..."
        )
    # Канонический https без www, без фрагмента
    cleaned = parsed._replace(
        scheme="https",
        netloc="bid.cars",
        fragment="",
    )
    return urlunparse(cleaned)


def _param_pretty(value: str | None) -> str | None:
    if not value:
        return None
    text = value.replace("+", " ").strip()
    if not text or text.lower() == "all":
        return None
    return text


def filter_identity(url: str) -> tuple[str | None, str | None, str | None]:
    """Марка, модель, годы из URL фильтра."""
    params = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    make = _param_pretty(params.get("make") or params.get("brand"))
    model = _param_pretty(params.get("model"))
    year_from = params.get("year-from") or params.get("year_from")
    year_to = params.get("year-to") or params.get("year_to")
    years = None
    if year_from and year_to and year_from == year_to:
        years = year_from
    elif year_from or year_to:
        years = f"{year_from or '?'}–{year_to or '?'}"
    return make, model, years


def filter_title(url: str, fallback: str | None = None) -> str:
    make, model, years = filter_identity(url)
    bits = [p for p in (make, model, years) if p]
    if "archived" in urlparse(url).path.lower():
        bits.append("архив")
    if bits:
        return " · ".join(bits)
    return fallback or "Фильтр bid.cars"


def button_label(filter_id: int, url: str, fallback: str | None = None) -> str:
    """Текст кнопки: #id, марка, модель, год. Лимит Telegram — 64 символа."""
    title = filter_title(url, fallback=fallback)
    text = f"#{filter_id} · {title}"
    if len(text) > 64:
        text = text[:63] + "…"
    return text


def label_from_url(url: str) -> str:
    return filter_title(url)


def filter_url_to_api_url(filter_url: str, page: int = 1) -> str:
    parsed = urlparse(filter_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["page"] = str(page)
    query = urlencode(params)
    return urlunparse(("https", "bid.cars", "/app/search/request", "", query, ""))


def lot_page_url(lot: str, tag: str | None, lang: str = "en") -> str:
    slug = tag or lot
    return f"https://bid.cars/{lang}/lot/{lot}/{slug}"


_LOT_PATH_ID_RE = re.compile(r"/lot/([^/]+)")
_JS_CURRENT_BID_RE = re.compile(r"var\s+currentBid\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*;")
_JS_FINAL_BID_RE = re.compile(r"var\s+finalBid\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*;")
_JS_LOT_NUMBER_RE = re.compile(r"var\s+lotNumber\s*=\s*'([^']+)'")
_JS_VIN_RE = re.compile(r"var\s+vin\s*=\s*'([A-HJ-NPR-Z0-9]{17})'", re.I)
_JS_AUCTION_DT_RE = re.compile(r"var\s+liveAuctionStartDateTime\s*=\s*'([^']*)'")
_TIME_LEFT_RE = re.compile(r"""id=["']time-left["'][^>]*>([^<]*)<""", re.I)
_PRICE_SPAN_RE = re.compile(
    r"""class=["'][^"']*current_bid[^"']*["'][^>]*>\s*(\$[\d\s,]+)""",
    re.I,
)
_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.I)
_VIN_TEXT_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")


def looks_like_lot_html(html: str) -> bool:
    if not html:
        return False
    return bool(
        _JS_LOT_NUMBER_RE.search(html)
        or _JS_CURRENT_BID_RE.search(html)
        or _TIME_LEFT_RE.search(html)
        or "currentBid" in html
    )


def parse_lot_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone(timedelta(hours=2)))
        except ValueError:
            continue
    return parse_auction_time(text)


def lot_lookup_url(
    lang: str,
    *,
    vin: str | None = None,
    query: str | None = None,
    archived: bool = False,
) -> str:
    path = f"/{lang}/search/archived/results" if archived else f"/{lang}/search/results"
    if vin:
        params = {"search-type": "vin", "query": vin, "vin": vin}
    else:
        params = {"search-type": "text", "query": query or ""}
    return urlunparse(("https", "bid.cars", path, "", urlencode(params), ""))


def _img_key_order(key: Any) -> tuple[int, str]:
    text = str(key)
    match = re.search(r"(\d+)$", text)
    return (int(match.group(1)) if match else 0, text)


def _collect_image_urls(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value] if value.startswith("http") else []
    found: list[str] = []
    if isinstance(value, dict):
        for key in sorted(value.keys(), key=_img_key_order):
            found.extend(_collect_image_urls(value[key]))
        return found
    if isinstance(value, list):
        for item in value:
            found.extend(_collect_image_urls(item))
    return found


def extract_image_urls(*sources: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for url in _collect_image_urls(source):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _first_image(value: Any) -> str | None:
    urls = _collect_image_urls(value)
    return urls[0] if urls else None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).replace(",", "").replace(" ", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parse_odometer(item: dict[str, Any]) -> tuple[int | None, int | None]:
    miles = _as_int(item.get("odometer_miles") if item.get("odometer_miles") not in (None, "") else item.get("odometer"))
    km = _as_int(item.get("odometer_km") or item.get("odometer_kilometers"))
    unit = str(item.get("odometer_unit") or "").lower()
    if miles is not None and miles < 0:
        miles = None
    if km is not None and km < 0:
        km = None
    if miles is not None and km is None:
        if "km" in unit:
            km = miles
            miles = round(km / KM_PER_MILE)
        else:
            km = round(miles * KM_PER_MILE)
    elif km is not None and miles is None:
        miles = round(km / KM_PER_MILE)
    return miles, km


def format_mileage(miles: int | None, km: int | None) -> str | None:
    if miles is None and km is None:
        return None
    if miles is None and km is not None:
        miles = round(km / KM_PER_MILE)
    if km is None and miles is not None:
        km = round(miles * KM_PER_MILE)
    return f"{miles:,} mi / {km:,} км".replace(",", " ")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"---", "null", "None"}:
        return None
    return text


def _format_price(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = f"{value:.0f}" if float(value).is_integer() else str(value)
        return f"${number}"
    return _clean_text(value)


def _clean_time_text(value: Any) -> str | None:
    if value in (None, "", 0, 0.0, "0", "0.0", "---"):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    return _clean_text(value)


def _pick_lang_text(value: Any, lang: str) -> str | None:
    if not isinstance(value, dict):
        return _clean_time_text(value)
    for key in (lang, "en", "ru"):
        text = _clean_time_text(value.get(key))
        if text:
            return text
    for item in value.values():
        text = _clean_time_text(item)
        if text:
            return text
    return None


def format_time_left(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs and not days:
        parts.append(f"{secs} сек")
    return " ".join(parts) or "меньше минуты"


def localize_time_left(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\bseconds?\b", "сек", text, flags=re.I)
    text = re.sub(r"\bsecs?\b", "сек", text, flags=re.I)
    text = re.sub(r"\bminutes?\b", "мин", text, flags=re.I)
    text = re.sub(r"\bmins?\b", "мин", text, flags=re.I)
    text = re.sub(r"\bhours?\b", "ч", text, flags=re.I)
    text = re.sub(r"\bhrs?\b", "ч", text, flags=re.I)
    text = re.sub(r"\bdays?\b", "д", text, flags=re.I)
    text = re.sub(r"(\d+)\s*d\b", r"\1 д", text, flags=re.I)
    text = re.sub(r"(\d+)\s*h\b", r"\1 ч", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def parse_time_left_seconds(item: dict[str, Any]) -> int | None:
    for key in ("time_left_seconds", "time_left"):
        value = item.get(key)
        if isinstance(value, bool) or value in (None, ""):
            continue
        if isinstance(value, (int, float)):
            seconds = int(value)
            if 0 < seconds < 1_000_000_000:
                return seconds
        if isinstance(value, str) and value.strip().isdigit():
            seconds = int(value.strip())
            if 0 < seconds < 1_000_000_000:
                return seconds
    return None


def parse_auction_time(value: Any, *, now: datetime | None = None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000
        if ts > 1e9:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return None
    text = str(value).strip()
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    iso = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        pass
    match = _AUCTION_TIME_RE.search(text)
    if not match:
        return None
    mon = _MONTHS.get(match.group("mon")[:3].lower())
    if not mon:
        return None
    try:
        day = int(match.group("day"))
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        offh = int(match.group("offh") or 0)
        offm = int(match.group("offm") or 0)
    except (TypeError, ValueError):
        return None
    offset = timedelta(hours=offh, minutes=offm)
    if (match.group("sign") or "+") == "-":
        offset = -offset
    try:
        parsed = datetime(now.year, mon, day, hour, minute, tzinfo=timezone(offset))
    except ValueError:
        return None
    if parsed < now - timedelta(days=14):
        try:
            parsed = parsed.replace(year=now.year + 1)
        except ValueError:
            pass
    return parsed


def _auction_fields(
    item: dict[str, Any], lang: str
) -> tuple[str | None, datetime | None]:
    lang_map = item.get("prebid_close_time_lang") or item.get("bid_close_time_lang")
    en_raw = _pick_lang_text(lang_map, "en") if isinstance(lang_map, dict) else None
    display = _pick_lang_text(lang_map, lang) if isinstance(lang_map, dict) else None
    if not display:
        for key in (
            "prebid_close_time",
            "bid_close_time",
            "buy_now_close_time",
            "auction_date",
            "sale_date",
            "sale_time",
        ):
            display = _clean_time_text(item.get(key))
            if display:
                break
    parsed = parse_auction_time(en_raw or display)
    return display, parsed


def _time_left_text(item: dict[str, Any]) -> tuple[str | None, int | None]:
    seconds = parse_time_left_seconds(item)
    formatted = _clean_text(item.get("time_left_formatted"))
    if not formatted:
        raw = item.get("time_left")
        if isinstance(raw, str):
            formatted = _clean_text(raw)
    if formatted:
        formatted = localize_time_left(formatted)
    elif seconds:
        formatted = format_time_left(seconds)
    return formatted, seconds


def normalize_search_status(value: str | None) -> str:
    return (value or "").strip().lower().replace("_", " ")


def is_finished_status(status: str | None) -> bool:
    text = normalize_search_status(status)
    if not text or text in ACTIVE_SEARCH_STATUSES:
        return False
    if text in FINISHED_SEARCH_STATUSES:
        return True
    if "not sold" in text:
        return True
    if "sold" in text:
        return True
    return False


def is_lot_finished(lot: LotData, *, source: str = "active") -> bool:
    if source == "archived":
        return True
    if is_finished_status(lot.search_status):
        return True
    if source == "missing":
        return False
    return False


def _map_status(start_code: str | None) -> str | None:
    if not start_code:
        return None
    mapped = STATUS_RU.get(start_code.strip().lower())
    return mapped or start_code


def _damage_line(item: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key in ("loss_type", "primary_damage"):
        value = _clean_text(item.get(key))
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def extract_items_and_meta(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return data, payload
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"], data
    return [], payload


def lot_from_item(item: dict[str, Any], lang: str = "en") -> LotData | None:
    lot_id = _clean_text(item.get("lot"))
    if not lot_id:
        return None
    title = _clean_text(item.get("name_long")) or _clean_text(item.get("name")) or lot_id
    tag = _clean_text(item.get("tag"))
    photos = extract_image_urls(item.get("img_large")) or extract_image_urls(item.get("img"))
    miles, km = parse_odometer(item)
    prebid = _format_price(item.get("prebid_price"))
    buy_now = _format_price(item.get("buy_now_price"))
    auction_raw, auction_at = _auction_fields(item, lang=lang)
    time_left, seconds = _time_left_text(item)
    if auction_at is None and seconds:
        auction_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return LotData(
        lot_external_id=lot_id,
        title=title,
        url=lot_page_url(lot_id, tag, lang=lang),
        vin=_clean_text(item.get("vin")),
        current_bid=prebid or buy_now,
        buy_now=buy_now,
        final_bid=_format_price(item.get("final_bid"))
        or _format_price(item.get("finalBid"))
        or _format_price(item.get("final_bid_raw")),
        damage=_damage_line(item),
        status=_map_status(_clean_text(item.get("start_code"))),
        location=_clean_text(item.get("location")),
        photo_url=photos[0] if photos else None,
        photo_urls=photos,
        odometer_miles=miles,
        odometer_km=km,
        search_status=_clean_text(item.get("search_status")),
        auction_at=auction_at,
        auction_raw=auction_raw,
        time_left=time_left,
        raw=item,
    )


def lot_to_preview(lot: LotData) -> dict[str, Any]:
    return {
        "id": lot.lot_external_id,
        "title": lot.title,
        "url": lot.url,
        "current_bid": lot.current_bid,
        "buy_now": lot.buy_now,
        "final_bid": lot.final_bid,
        "status": lot.status,
        "location": lot.location,
        "vin": lot.vin,
        "damage": lot.damage,
        "odometer_miles": lot.odometer_miles,
        "odometer_km": lot.odometer_km,
        "photo_url": lot.photo_url,
        "photo_urls": list(lot.photo_urls or []),
        "search_status": lot.search_status,
        "auction_at": lot.auction_at.isoformat() if lot.auction_at else None,
        "auction_raw": lot.auction_raw,
        "time_left": lot.time_left,
        "raw": lot.raw or {},
    }


def lot_from_preview(data: dict[str, Any]) -> LotData:
    photos = [str(url) for url in (data.get("photo_urls") or []) if url]
    auction_at = None
    raw_at = data.get("auction_at")
    if raw_at:
        try:
            auction_at = datetime.fromisoformat(str(raw_at))
        except ValueError:
            auction_at = parse_auction_time(raw_at)
    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    return LotData(
        lot_external_id=str(data.get("id") or ""),
        title=str(data.get("title") or "Лот"),
        url=str(data.get("url") or ""),
        current_bid=data.get("current_bid"),
        buy_now=data.get("buy_now"),
        final_bid=data.get("final_bid"),
        status=data.get("status"),
        location=data.get("location"),
        vin=data.get("vin"),
        damage=data.get("damage"),
        odometer_miles=data.get("odometer_miles"),
        odometer_km=data.get("odometer_km"),
        photo_url=data.get("photo_url") or (photos[0] if photos else None),
        photo_urls=photos,
        search_status=data.get("search_status"),
        auction_at=auction_at,
        auction_raw=data.get("auction_raw"),
        time_left=data.get("time_left"),
        raw=raw,
    )


def parse_search_json(payload: dict[str, Any], source_url: str) -> tuple[list[LotData], dict[str, Any]]:
    lang = extract_lang(source_url)
    items, meta = extract_items_and_meta(payload)
    lots: list[LotData] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        lot = lot_from_item(item, lang=lang)
        if lot is None or lot.lot_external_id in seen:
            continue
        seen.add(lot.lot_external_id)
        lots.append(lot)
    return lots, meta


def parse_lot_html(
    html: str,
    lot_url: str,
    lot_id: str | None = None,
) -> LotData | None:
    if not html or not looks_like_lot_html(html):
        return None
    found_id = None
    match = _JS_LOT_NUMBER_RE.search(html)
    if match:
        found_id = match.group(1).strip()
    if not found_id:
        match = _LOT_PATH_ID_RE.search(lot_url or "")
        if match:
            found_id = match.group(1)
    found_id = found_id or lot_id
    if not found_id:
        return None
    if lot_id and found_id != lot_id:
        return None

    current_bid = None
    match = _JS_CURRENT_BID_RE.search(html)
    if match:
        amount = float(match.group(1))
        if amount > 0:
            current_bid = _format_price(amount)
    if not current_bid:
        match = _PRICE_SPAN_RE.search(html)
        if match:
            current_bid = match.group(1).replace(" ", "")

    final_bid = None
    match = _JS_FINAL_BID_RE.search(html)
    if match:
        amount = float(match.group(1))
        if amount > 0:
            final_bid = _format_price(amount)

    time_left = None
    match = _TIME_LEFT_RE.search(html)
    if match:
        raw_left = match.group(1).strip()
        lowered = raw_left.lower()
        if raw_left and lowered not in {"0", "---", "-", "0 sec", "0 сек"}:
            if any(token in lowered for token in ("ended", "заверш", "sold", "продан")):
                time_left = None
            else:
                time_left = localize_time_left(raw_left)

    auction_raw = None
    auction_at = None
    match = _JS_AUCTION_DT_RE.search(html)
    if match and match.group(1).strip():
        auction_raw = match.group(1).strip()
        auction_at = parse_lot_datetime(auction_raw)

    title = found_id
    match = _TITLE_RE.search(html)
    if match:
        raw_title = " ".join(match.group(1).split())
        raw_title = raw_title.split("|")[0].strip()
        if raw_title:
            title = raw_title

    vin = None
    match = _JS_VIN_RE.search(html)
    if match:
        vin = match.group(1).upper()
    if not vin:
        match = _VIN_TEXT_RE.search(lot_url or "")
        if match:
            vin = match.group(1).upper()
    if not vin and title:
        match = _VIN_TEXT_RE.search(title)
        if match:
            vin = match.group(1).upper()

    search_status = "sold" if final_bid and not time_left else "active"

    return LotData(
        lot_external_id=found_id,
        title=title,
        url=lot_url,
        vin=vin,
        current_bid=current_bid or final_bid,
        final_bid=final_bid,
        search_status=search_status,
        auction_at=auction_at,
        auction_raw=auction_raw,
        time_left=time_left,
    )
