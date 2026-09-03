from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from curl_cffi.requests import AsyncSession

from app.config import settings
from app.parser import (
    FilterUrlError,
    LotData,
    filter_url_to_api_url,
    parse_search_json,
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


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


class BidCarsClient:
    def __init__(self) -> None:
        self._session: AsyncSession | None = None
        self._warmed = False

    async def _session_get(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate="chrome")
            self._warmed = False
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._warmed = False

    async def _warmup(self, referer: str) -> None:
        """Первый HTML-заход ставит Cloudflare/session cookies. API без них отдаёт challenge."""
        logger.info("Warming bid.cars session via %s", referer)
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
        self._warmed = True

    async def _raw_get(self, url: str, referer: str) -> Any:
        await rate_limiter.wait()
        session = await self._session_get()
        try:
            return await session.get(
                url,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
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
        if not self._warmed:
            await self._warmup(referer)
        response = await self._raw_get(url, referer)
        text = response.text or ""
        if not _looks_like_json(text):
            logger.info("API returned non-JSON, re-warming session")
            await self.close()
            await self._warmup(referer)
            response = await self._raw_get(url, referer)
            text = response.text or ""
        if response.status_code != 200:
            raise ParseError(f"HTTP {response.status_code} от bid.cars")
        if not _looks_like_json(text):
            raise ParseError("Сайт вернул HTML вместо JSON (возможна защита Cloudflare)")
        try:
            payload = response.json()
        except Exception as exc:
            raise ParseError(f"Ответ не JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ParseError("Неожиданный формат JSON")
        return payload

    async def parse_filter(self, url: str) -> list[LotData]:
        filter_url = validate_filter_url(url)
        collected: list[LotData] = []
        seen_ids: set[str] = set()
        last_page = settings.parser_max_pages

        for page in range(1, settings.parser_max_pages + 1):
            if page > last_page:
                break
            api_url = filter_url_to_api_url(filter_url, page=page)
            logger.info("Fetching bid.cars page %s: %s", page, api_url)
            payload = await self._get_json(api_url, referer=filter_url)
            lots, meta = parse_search_json(payload, source_url=filter_url)
            if not lots:
                break
            for lot in lots:
                if lot.lot_external_id not in seen_ids:
                    seen_ids.add(lot.lot_external_id)
                    collected.append(lot)
            try:
                last_page = min(int(meta.get("last_page") or last_page), settings.parser_max_pages)
            except (TypeError, ValueError):
                pass
            if not meta.get("next_page_url") and page >= int(meta.get("last_page") or page):
                break

        return collected


client = BidCarsClient()


async def parse_filter(url: str) -> list[LotData]:
    """Публичный вход парсера: URL фильтра → список лотов."""
    return await client.parse_filter(url)


__all__ = [
    "ParseError",
    "FilterUrlError",
    "LotData",
    "parse_filter",
    "client",
    "rate_limiter",
]
