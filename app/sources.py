from __future__ import annotations

import re
from urllib.parse import urlparse

from app.parser import (
    FilterUrlError,
    ParsedLotUrl,
    extract_bidcars_url,
    filter_title as bidcars_filter_title,
    parse_lot_url as parse_bidcars_lot_url,
    validate_filter_url as validate_bidcars_filter_url,
)

SOURCE_BIDCARS = "bidcars"
SOURCE_COPART = "copart"
SOURCE_IAAI = "iaai"

SOURCE_LABELS = {
    SOURCE_BIDCARS: "bid.cars",
    SOURCE_COPART: "Copart",
    SOURCE_IAAI: "IAAI",
}

COPART_HOSTS = {"copart.com", "www.copart.com"}
IAAI_HOSTS = {"iaai.com", "www.iaai.com"}
BIDCARS_HOSTS = {"bid.cars", "www.bid.cars"}

_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:bid\.cars|copart\.com|iaai\.com)[^\s<>]*",
    re.I,
)


def detect_source(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if host in BIDCARS_HOSTS:
        return SOURCE_BIDCARS
    if host in COPART_HOSTS:
        return SOURCE_COPART
    if host in IAAI_HOSTS:
        return SOURCE_IAAI
    return None


def extract_auction_url(text: str) -> str | None:
    match = _URL_RE.search(text or "")
    if match:
        return match.group(0).rstrip(").,]>\"'")
    return extract_bidcars_url(text)


def source_label(source: str | None) -> str:
    return SOURCE_LABELS.get(source or "", source or "аукцион")


def parse_lot_url(url: str) -> ParsedLotUrl | None:
    from app import copart, iaai

    raw = (url or "").strip()
    extracted = extract_auction_url(raw) or raw
    source = detect_source(extracted)
    if source == SOURCE_COPART:
        return copart.parse_lot_url(extracted)
    if source == SOURCE_IAAI:
        return iaai.parse_lot_url(extracted)
    if source == SOURCE_BIDCARS:
        parsed = parse_bidcars_lot_url(extracted)
        if parsed is not None:
            parsed.source = SOURCE_BIDCARS
        return parsed
    return None


def validate_filter_url(url: str) -> str:
    from app import copart, iaai

    raw = (url or "").strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    source = detect_source(raw)
    if source == SOURCE_COPART:
        return copart.validate_filter_url(raw)
    if source == SOURCE_IAAI:
        return iaai.validate_filter_url(raw)
    if source == SOURCE_BIDCARS:
        return validate_bidcars_filter_url(raw)
    raise FilterUrlError(
        "Ссылка должна вести на bid.cars, Copart или IAAI "
        "(поиск с фильтрами или страница лота)."
    )


def filter_title(url: str, fallback: str | None = None) -> str:
    from app import copart, iaai

    source = detect_source(url)
    if source == SOURCE_COPART:
        return copart.filter_title(url, fallback=fallback)
    if source == SOURCE_IAAI:
        return iaai.filter_title(url, fallback=fallback)
    title = bidcars_filter_title(url, fallback=fallback)
    return title


def label_from_url(url: str) -> str:
    return filter_title(url)


def button_label(filter_id: int, url: str, fallback: str | None = None) -> str:
    """Текст кнопки: #id + заголовок. Лимит Telegram — 64 символа."""
    title = filter_title(url, fallback=fallback)
    text = f"#{filter_id} · {title}"
    if len(text) > 64:
        text = text[:63] + "…"
    return text


def external_id(source: str, native_id: str) -> str:
    native = (native_id or "").strip()
    if source == SOURCE_COPART:
        return f"c-{native}"
    if source == SOURCE_IAAI:
        return f"i-{native}"
    return native


def split_external_id(lot_external_id: str) -> tuple[str, str]:
    value = (lot_external_id or "").strip()
    if value.startswith("c-"):
        return SOURCE_COPART, value[2:]
    if value.startswith("i-"):
        return SOURCE_IAAI, value[2:]
    return SOURCE_BIDCARS, value
