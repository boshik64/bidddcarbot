from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

from app import copart, iaai
from app.config import settings
from app.parser import (
    FilterUrlError,
    LotData,
    LotFetch,
    extract_lang,
    filter_url_to_api_url,
    is_lot_finished,
    looks_like_lot_html,
    lot_lookup_url,
    lot_page_url,
    parse_lot_html,
    parse_search_json,
    validate_filter_url as validate_bidcars_filter_url,
)
from app.sources import (
    SOURCE_BIDCARS,
    SOURCE_COPART,
    SOURCE_IAAI,
    detect_source,
    split_external_id,
    validate_filter_url,
)

logger = logging.getLogger(__name__)


class ParseError(Exception):
    pass


class RateLimiter:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_for = self._last + self.delay - now
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last = time.monotonic()


rate_limiter = RateLimiter(settings.request_delay_seconds)

_NETWORK_HINTS = (
    "dns",
    "resolve",
    "timed out",
    "timeout",
    "connection",
    "connect",
    "reset",
    "unreachable",
    "temporary failure",
    "could not resolve",
)


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _is_transient_network_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "dns" in name or "timeout" in name or "connection" in name:
        return True
    return any(hint in text for hint in _NETWORK_HINTS)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return f"{parsed.scheme or 'https'}://{host}"


class AuctionClient:
    def __init__(self) -> None:
        self._session: AsyncSession | None = None
        self._warmed_origins: set[str] = set()

    async def _session_get(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate="chrome")
            self._warmed_origins.clear()
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._warmed_origins.clear()

    async def _with_network_retries(self, label: str, factory):
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                return await factory()
            except ParseError as exc:
                last_exc = exc
                if not _is_transient_network_error(exc) or attempt >= 3:
                    raise
                wait_for = min(2 ** attempt, 8)
                logger.warning(
                    "%s failed (attempt %s/3), retry in %ss: %s",
                    label,
                    attempt,
                    wait_for,
                    exc,
                )
                await self.close()
                await asyncio.sleep(wait_for)
            except Exception as exc:
                last_exc = exc
                if not _is_transient_network_error(exc) or attempt >= 3:
                    raise
                wait_for = min(2 ** attempt, 8)
                logger.warning(
                    "%s failed (attempt %s/3), retry in %ss: %s",
                    label,
                    attempt,
                    wait_for,
                    exc,
                )
                await self.close()
                await asyncio.sleep(wait_for)
        assert last_exc is not None
        raise last_exc

    async def _warmup_once(self, referer: str) -> str:
        origin = _origin(referer)
        logger.info("Warming session via %s", referer)
        await rate_limiter.wait()
        session = await self._session_get()
        try:
            response = await session.get(
                referer,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                },
                timeout=settings.request_timeout_seconds,
                allow_redirects=True,
            )
        except Exception as exc:
            await self.close()
            raise ParseError(f"Сеть при прогреве: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise ParseError(f"Прогрев сессии: HTTP {response.status_code}")
        self._warmed_origins.add(origin)
        return response.text or ""

    async def _warmup(self, referer: str) -> str:
        return await self._with_network_retries(
            "warmup", lambda: self._warmup_once(referer)
        )

    async def _ensure_warm(self, referer: str) -> None:
        origin = _origin(referer)
        if origin not in self._warmed_origins:
            await self._warmup(referer)

    async def _raw_get_once(self, url: str, referer: str, *, accept: str) -> Any:
        await rate_limiter.wait()
        session = await self._session_get()
        try:
            return await session.get(
                url,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": accept,
                    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer,
                },
                timeout=settings.request_timeout_seconds,
                allow_redirects=True,
            )
        except Exception as exc:
            await self.close()
            raise ParseError(f"Сеть: {type(exc).__name__}: {exc}") from exc

    async def _raw_post_json_once(self, url: str, referer: str, body: dict[str, Any]) -> Any:
        await rate_limiter.wait()
        session = await self._session_get()
        try:
            return await session.post(
                url,
                json=body,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/json",
                    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": referer,
                },
                timeout=settings.request_timeout_seconds,
                allow_redirects=True,
            )
        except Exception as exc:
            await self.close()
            raise ParseError(f"Сеть: {type(exc).__name__}: {exc}") from exc

    async def _get_json(self, url: str, referer: str) -> dict[str, Any]:
        await self._ensure_warm(referer)

        async def once():
            response = await self._raw_get_once(
                url,
                referer,
                accept="application/json, text/javascript, */*; q=0.01",
            )
            text = response.text or ""
            if not _looks_like_json(text):
                logger.info("API returned non-JSON, re-warming session")
                await self.close()
                await self._warmup(referer)
                response = await self._raw_get_once(
                    url,
                    referer,
                    accept="application/json, text/javascript, */*; q=0.01",
                )
                text = response.text or ""
            if response.status_code != 200:
                raise ParseError(f"HTTP {response.status_code} от {urlparse(url).hostname}")
            if not _looks_like_json(text):
                raise ParseError("Сайт вернул HTML вместо JSON (возможна защита Cloudflare)")
            try:
                payload = response.json()
            except Exception as exc:
                raise ParseError(f"Ответ не JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ParseError("Неожиданный формат JSON")
            return payload

        return await self._with_network_retries("json GET", once)

    async def _post_json(self, url: str, referer: str, body: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_warm(referer)

        async def once():
            response = await self._raw_post_json_once(url, referer, body)
            text = response.text or ""
            if not _looks_like_json(text):
                logger.info("API POST returned non-JSON, re-warming session")
                await self.close()
                await self._warmup(referer)
                response = await self._raw_post_json_once(url, referer, body)
                text = response.text or ""
            if response.status_code != 200:
                raise ParseError(f"HTTP {response.status_code} от {urlparse(url).hostname}")
            if not _looks_like_json(text):
                raise ParseError("Сайт вернул HTML вместо JSON (возможна защита Cloudflare)")
            try:
                payload = response.json()
            except Exception as exc:
                raise ParseError(f"Ответ не JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ParseError("Неожиданный формат JSON")
            return payload

        return await self._with_network_retries("json POST", once)

    async def _raw_get_html_once(self, url: str) -> Any:
        await rate_limiter.wait()
        session = await self._session_get()
        try:
            return await session.get(
                url,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                },
                timeout=max(settings.request_timeout_seconds, 60),
                allow_redirects=True,
            )
        except Exception as exc:
            await self.close()
            raise ParseError(f"Сеть: {type(exc).__name__}: {exc}") from exc

    async def _get_html(self, url: str, *, lot_check: bool = False) -> str:
        await self._ensure_warm(url)

        async def once():
            response = await self._raw_get_html_once(url)
            text = response.text or ""
            if response.status_code == 404:
                return ""
            if response.status_code != 200:
                raise ParseError(f"HTTP {response.status_code} от {urlparse(url).hostname}")
            if lot_check and looks_like_lot_html(text):
                return text
            if lot_check:
                logger.info("Lot page returned unexpected HTML, re-warming session")
                await self.close()
                html = await self._warmup(url)
                if looks_like_lot_html(html):
                    return html
                response = await self._raw_get_html_once(url)
                text = response.text or ""
                if response.status_code == 404:
                    return ""
                if response.status_code != 200:
                    raise ParseError(
                        f"HTTP {response.status_code} от {urlparse(url).hostname}"
                    )
            return text

        return await self._with_network_retries("html GET", once)

    async def _search_first_page_bidcars(self, url: str) -> list[LotData]:
        filter_url = validate_bidcars_filter_url(url)
        api_url = filter_url_to_api_url(filter_url, page=1)
        payload = await self._get_json(api_url, referer=filter_url)
        lots, _meta = parse_search_json(payload, source_url=filter_url)
        return lots

    async def fetch_lot(
        self,
        lot_id: str,
        *,
        vin: str | None = None,
        lang: str | None = None,
        lot_url: str | None = None,
        include_archived: bool = True,
    ) -> LotFetch:
        source, native = split_external_id(lot_id)
        if lot_url:
            detected = detect_source(lot_url)
            if detected:
                source = detected
        if source == SOURCE_COPART:
            return await self._fetch_copart_lot(native, lot_url=lot_url)
        if source == SOURCE_IAAI:
            return await self._fetch_iaai_lot(native, lot_url=lot_url)
        return await self._fetch_bidcars_lot(
            native,
            vin=vin,
            lang=lang,
            lot_url=lot_url,
            include_archived=include_archived,
        )

    async def _fetch_bidcars_lot(
        self,
        lot_id: str,
        *,
        vin: str | None = None,
        lang: str | None = None,
        lot_url: str | None = None,
        include_archived: bool = True,
    ) -> LotFetch:
        lang = lang or extract_lang(lot_url or "https://bid.cars/en/")
        page_url = lot_url or (
            lot_page_url(lot_id, vin, lang=lang)
            if vin
            else f"https://bid.cars/{lang}/lot/{lot_id}"
        )
        html = await self._get_html(page_url, lot_check=True)
        lot = parse_lot_html(html, page_url, lot_id=lot_id)
        if lot is None:
            return LotFetch(lot=None, source="missing")
        lot.source = SOURCE_BIDCARS
        status = "archived" if is_lot_finished(lot) else "active"
        return LotFetch(lot=lot, source=status)

    async def _fetch_copart_lot(
        self, lot_number: str, *, lot_url: str | None = None
    ) -> LotFetch:
        native = lot_number.removeprefix("c-")
        page_url = lot_url or copart.lot_page_url(native)
        referer = page_url
        await self._ensure_warm("https://www.copart.com/")
        api_url = copart.lot_details_api_url(native)
        payload = await self._get_json(api_url, referer=referer)
        lot = copart.lot_from_details(payload, page_url=page_url)
        if lot is None:
            return LotFetch(lot=None, source="missing")
        status = "archived" if is_lot_finished(lot) else "active"
        return LotFetch(lot=lot, source=status)

    async def _fetch_iaai_lot(
        self, item_id: str, *, lot_url: str | None = None
    ) -> LotFetch:
        native = item_id.removeprefix("i-")
        page_url = lot_url or iaai.lot_page_url(native)
        html = await self._get_html(page_url, lot_check=False)
        lot = iaai.lot_from_detail_html(html, page_url=page_url, lot_id=native)
        if lot is None:
            return LotFetch(lot=None, source="missing")
        status = "archived" if is_lot_finished(lot) else "active"
        return LotFetch(lot=lot, source=status)

    async def find_active_lot_by_vin(
        self, vin: str, *, lang: str = "en"
    ) -> LotData | None:
        vin = (vin or "").strip().upper()
        if not vin or "*" in vin:
            return None
        url = lot_lookup_url(lang, vin=vin, archived=False)
        try:
            lots = await self._search_first_page_bidcars(url)
        except ParseError as exc:
            logger.info("VIN lookup via bid.cars failed: %s", exc)
            return None
        for lot in lots:
            if (lot.vin or "").upper() != vin:
                continue
            if is_lot_finished(lot):
                continue
            lot.source = SOURCE_BIDCARS
            return lot
        return None

    async def parse_filter_with_total(self, url: str) -> tuple[list[LotData], int]:
        filter_url = validate_filter_url(url)
        source = detect_source(filter_url)
        if source == SOURCE_COPART:
            return await self._parse_copart_filter(filter_url)
        if source == SOURCE_IAAI:
            return await self._parse_iaai_filter(filter_url)
        return await self._parse_bidcars_filter(filter_url)

    async def _parse_bidcars_filter(self, filter_url: str) -> tuple[list[LotData], int]:
        collected: list[LotData] = []
        seen_ids: set[str] = set()
        last_page = settings.parser_max_pages
        total: int | None = None

        for page in range(1, settings.parser_max_pages + 1):
            if page > last_page:
                break
            api_url = filter_url_to_api_url(filter_url, page=page)
            logger.info("Fetching bid.cars page %s: %s", page, api_url)
            payload = await self._get_json(api_url, referer=filter_url)
            lots, meta = parse_search_json(payload, source_url=filter_url)
            for lot in lots:
                lot.source = SOURCE_BIDCARS
            if total is None:
                try:
                    total = int(meta.get("total"))
                except (TypeError, ValueError):
                    total = None
            if not lots:
                break
            for lot in lots:
                if lot.lot_external_id not in seen_ids:
                    seen_ids.add(lot.lot_external_id)
                    collected.append(lot)
            try:
                last_page = min(
                    int(meta.get("last_page") or last_page), settings.parser_max_pages
                )
            except (TypeError, ValueError):
                pass
            if not meta.get("next_page_url") and page >= int(meta.get("last_page") or page):
                break

        return collected, total if total is not None else len(collected)

    async def _parse_copart_filter(self, filter_url: str) -> tuple[list[LotData], int]:
        collected: list[LotData] = []
        seen_ids: set[str] = set()
        total: int | None = None
        await self._ensure_warm("https://www.copart.com/")
        for page in range(0, settings.parser_max_pages):
            body = copart.search_body_from_filter_url(filter_url, page=page, size=50)
            logger.info("Fetching Copart page %s: %s", page, filter_url)
            payload = await self._post_json(
                copart.search_api_url(), filter_url, body
            )
            lots, meta = copart.parse_search_payload(payload)
            if total is None:
                try:
                    total = int(meta.get("total"))
                except (TypeError, ValueError):
                    total = None
            if not lots:
                break
            for lot in lots:
                if lot.lot_external_id not in seen_ids:
                    seen_ids.add(lot.lot_external_id)
                    collected.append(lot)
            if not meta.get("next_page_url"):
                break
        return collected, total if total is not None else len(collected)

    async def _parse_iaai_filter(self, filter_url: str) -> tuple[list[LotData], int]:
        logger.info("Fetching IAAI listing: %s", filter_url)
        html = await self._get_html(filter_url, lot_check=False)
        lots = iaai.parse_listing_html(html, source_url=filter_url)
        return lots, len(lots)

    async def parse_filter(self, url: str) -> list[LotData]:
        lots, _total = await self.parse_filter_with_total(url)
        return lots


# backward-compatible alias
BidCarsClient = AuctionClient
client = AuctionClient()


async def parse_filter(url: str) -> list[LotData]:
    """Публичный вход парсера: URL фильтра → список лотов."""
    return await client.parse_filter(url)


async def parse_filter_with_total(url: str) -> tuple[list[LotData], int]:
    return await client.parse_filter_with_total(url)


async def fetch_lot(
    lot_id: str,
    *,
    vin: str | None = None,
    lang: str | None = None,
    lot_url: str | None = None,
    include_archived: bool = True,
) -> LotFetch:
    return await client.fetch_lot(
        lot_id,
        vin=vin,
        lang=lang,
        lot_url=lot_url,
        include_archived=include_archived,
    )


async def find_active_lot_by_vin(vin: str, *, lang: str = "en") -> LotData | None:
    return await client.find_active_lot_by_vin(vin, lang=lang)


__all__ = [
    "ParseError",
    "FilterUrlError",
    "LotData",
    "LotFetch",
    "AuctionClient",
    "BidCarsClient",
    "parse_filter",
    "parse_filter_with_total",
    "fetch_lot",
    "find_active_lot_by_vin",
    "client",
    "rate_limiter",
]
