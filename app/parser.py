from __future__ import annotations

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


def label_from_url(url: str) -> str:
    params = dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    make = params.get("make") or params.get("brand")
    model = params.get("model")
    year_from = params.get("year-from") or params.get("year_from")
    year_to = params.get("year-to") or params.get("year_to")
    bits: list[str] = []
    if make and make.lower() != "all":
        bits.append(make)
    if model and model.lower() != "all":
        bits.append(model)
    if year_from or year_to:
        bits.append(f"{year_from or '?'}–{year_to or '?'}")
    if "archived" in urlparse(url).path.lower():
        bits.append("архив")
    return " ".join(bits) if bits else "Фильтр bid.cars"


def filter_url_to_api_url(filter_url: str, page: int = 1) -> str:
    parsed = urlparse(filter_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["page"] = str(page)
    query = urlencode(params)
    return urlunparse(("https", "bid.cars", "/app/search/request", "", query, ""))


def lot_page_url(lot: str, tag: str | None, lang: str = "en") -> str:
    slug = tag or lot
    return f"https://bid.cars/{lang}/lot/{lot}/{slug}"


def _first_image(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in sorted(value.keys(), key=lambda k: (len(k), k)):
            item = value[key]
            if isinstance(item, str) and item.startswith("http"):
                return item
            if isinstance(item, dict):
                nested = _first_image(item)
                if nested:
                    return nested
    if isinstance(value, list) and value:
        return _first_image(value[0])
    return None


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
    photo = _first_image(item.get("img")) or _first_image(item.get("img_large"))
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
        photo_url=photo,
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
