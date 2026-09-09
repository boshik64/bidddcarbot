from __future__ import annotations

import re
from dataclasses import dataclass, field
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


@dataclass
class LotData:
    lot_external_id: str
    title: str
    url: str
    vin: str | None = None
    current_bid: str | None = None
    damage: str | None = None
    status: str | None = None
    location: str | None = None
    photo_url: str | None = None
    photo_urls: list[str] = field(default_factory=list)
    odometer_miles: int | None = None
    odometer_km: int | None = None
    search_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


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
    return LotData(
        lot_external_id=lot_id,
        title=title,
        url=lot_page_url(lot_id, tag, lang=lang),
        vin=_clean_text(item.get("vin")),
        current_bid=_clean_text(item.get("prebid_price"))
        or _clean_text(item.get("buy_now_price")),
        damage=_damage_line(item),
        status=_map_status(_clean_text(item.get("start_code"))),
        location=_clean_text(item.get("location")),
        photo_url=photos[0] if photos else None,
        photo_urls=photos,
        odometer_miles=miles,
        odometer_km=km,
        search_status=_clean_text(item.get("search_status")),
        raw=item,
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
