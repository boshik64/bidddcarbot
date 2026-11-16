from __future__ import annotations

import logging
import re
from collections import OrderedDict
from html import escape
from typing import Any, Optional
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import TelegramMethod
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from curl_cffi.requests import AsyncSession

from app.config import settings
from app.formatting import lot_caption
from app.parser import TG_ALBUM_MAX, TG_SLIDESHOW_MAX, LotData

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_LOT_CACHE_MAX = 400
_lot_cache: OrderedDict[str, LotData] = OrderedDict()

# Telegram сам ходит за URL; Copart/IAAI CDN часто отдают 403/пусто → WEBPAGE_CURL_FAILED.
_UPLOAD_HOST_MARKERS = (
    "copart.com",
    "cs.copart.com",
    "iaai.com",
    "vis.iaai.com",
)
_URL_FETCH_FAIL_HINTS = (
    "failed to get http url content",
    "webpage_curl_failed",
    "wrong type of the web page content",
    "rich_message_photo_no_media_found",
)


class SendRichMessage(TelegramMethod[Message]):
    """Bot API 10.2+ sendRichMessage — native article slideshow."""

    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int
    rich_message: dict[str, Any]
    reply_markup: Optional[InlineKeyboardMarkup] = None


def remember_lot(lot: LotData) -> None:
    if not lot.lot_external_id:
        return
    prev = _lot_cache.get(lot.lot_external_id)
    if prev is not None:
        if not lot.photo_urls and prev.photo_urls:
            lot.photo_urls = list(prev.photo_urls)
            lot.photo_url = lot.photo_url or prev.photo_url
        elif not lot.photo_url and prev.photo_url:
            lot.photo_url = prev.photo_url
        if not lot.damage and prev.damage:
            lot.damage = prev.damage
        if not lot.location and prev.location:
            lot.location = prev.location
        if lot.odometer_miles is None and prev.odometer_miles is not None:
            lot.odometer_miles = prev.odometer_miles
            lot.odometer_km = prev.odometer_km
        if not lot.status and prev.status:
            lot.status = prev.status
        if not lot.vin and prev.vin:
            lot.vin = prev.vin
    _lot_cache[lot.lot_external_id] = lot
    _lot_cache.move_to_end(lot.lot_external_id)
    while len(_lot_cache) > _LOT_CACHE_MAX:
        _lot_cache.popitem(last=False)


def recalled_lot(lot_id: str) -> LotData | None:
    lot = _lot_cache.get(lot_id)
    if lot is not None:
        _lot_cache.move_to_end(lot_id)
    return lot


def album_photo_urls(lot: LotData, limit: int = TG_SLIDESHOW_MAX) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for url in list(lot.photo_urls or []) + ([lot.photo_url] if lot.photo_url else []):
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def lot_article_html(caption: str, img_srcs: list[str]) -> str:
    slides = "".join(f'<img src="{src}">' for src in img_srcs)
    body = "".join(f"<p>{line}</p>" for line in caption.split("\n") if line)
    if len(img_srcs) >= 2:
        return f"<tg-slideshow>{slides}</tg-slideshow>{body}"
    if img_srcs:
        return f"{slides}{body}"
    return body


def slideshow_rich_message(caption: str, urls: list[str]) -> dict[str, Any]:
    media_ids = [f"p{i}" for i in range(len(urls))]
    img_srcs = [f"tg://photo?id={mid}" for mid in media_ids]
    return {
        "html": lot_article_html(caption, img_srcs),
        "media": [
            {"id": mid, "media": {"type": "photo", "media": url}}
            for mid, url in zip(media_ids, urls)
        ],
    }


def slideshow_direct_html_message(caption: str, urls: list[str]) -> dict[str, Any]:
    img_srcs = [escape(url, quote=True) for url in urls]
    return {"html": lot_article_html(caption, img_srcs)}


def slideshow_blocks_message(caption: str, urls: list[str]) -> dict[str, Any]:
    plain = _TAG_RE.sub("", caption).replace("&nbsp;", " ").strip()
    slideshow: dict[str, Any] = {
        "type": "slideshow",
        "blocks": [
            {"type": "photo", "photo": {"type": "photo", "media": url}} for url in urls
        ],
    }
    if plain:
        slideshow["caption"] = {"text": plain}
    return {"blocks": [slideshow]}


async def _send_rich(
    bot: Bot,
    chat_id: int,
    payload: dict[str, Any],
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    await bot(
        SendRichMessage(
            chat_id=chat_id, rich_message=payload, reply_markup=reply_markup
        )
    )


async def _send_keyboard_followup(
    bot: Bot, chat_id: int, reply_markup: InlineKeyboardMarkup | None
) -> None:
    if reply_markup is None:
        return
    try:
        await bot.send_message(chat_id, "❤️ Лот", reply_markup=reply_markup)
    except TelegramAPIError as exc:
        logger.warning("Watch keyboard follow-up failed: %s", exc)


def _host_needs_upload(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == marker or host.endswith("." + marker) for marker in _UPLOAD_HOST_MARKERS)


def _lot_needs_upload(lot: LotData, urls: list[str]) -> bool:
    source = (getattr(lot, "source", None) or "").lower()
    if source in {"copart", "iaai"}:
        return True
    return any(_host_needs_upload(url) for url in urls)


def _is_url_fetch_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _URL_FETCH_FAIL_HINTS)


async def _download_photo_bytes(url: str) -> bytes | None:
    try:
        async with AsyncSession(impersonate="chrome") as session:
            response = await session.get(
                url,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": f"https://{urlparse(url).hostname}/",
                },
                timeout=settings.request_timeout_seconds,
                allow_redirects=True,
            )
    except Exception as exc:
        logger.warning("Photo download failed %s: %s", url, exc)
        return None
    if response.status_code != 200:
        logger.warning("Photo download HTTP %s for %s", response.status_code, url)
        return None
    data = response.content or b""
    if len(data) < 100:
        return None
    return data


async def _download_photos(urls: list[str], *, limit: int = TG_ALBUM_MAX) -> list[bytes]:
    files: list[bytes] = []
    for url in urls[:limit]:
        data = await _download_photo_bytes(url)
        if data:
            files.append(data)
    return files


def _as_input_file(data: bytes, index: int = 0) -> BufferedInputFile:
    return BufferedInputFile(data, filename=f"lot_{index}.jpg")


async def _send_single_photo(
    bot: Bot,
    chat_id: int,
    photo: str | BufferedInputFile,
    caption: str,
    lot_id: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    try:
        await bot.send_photo(
            chat_id, photo=photo, caption=caption[:1024], reply_markup=reply_markup
        )
    except TelegramAPIError as exc:
        logger.warning("Single photo failed for lot %s: %s", lot_id, exc)
        await bot.send_message(
            chat_id,
            caption,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )


async def _send_album_fallback(
    bot: Bot,
    chat_id: int,
    photos: list[str] | list[BufferedInputFile],
    caption: str,
    lot_id: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    if not photos:
        await bot.send_message(
            chat_id,
            caption,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return
    limited = photos[:TG_ALBUM_MAX]
    media = [InputMediaPhoto(media=item) for item in limited]
    media[0] = InputMediaPhoto(media=limited[0], caption=caption[:1024])
    try:
        await bot.send_media_group(chat_id, media=media)
        await _send_keyboard_followup(bot, chat_id, reply_markup)
    except TelegramAPIError as exc:
        logger.warning("Album fallback failed for lot %s: %s", lot_id, exc)
        await _send_single_photo(bot, chat_id, limited[0], caption, lot_id, reply_markup)


async def _send_uploaded_photos(
    bot: Bot,
    chat_id: int,
    urls: list[str],
    caption: str,
    lot_id: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> bool:
    files = await _download_photos(urls)
    if not files:
        return False
    inputs = [_as_input_file(data, i) for i, data in enumerate(files)]
    if len(inputs) == 1:
        await _send_single_photo(
            bot, chat_id, inputs[0], caption, lot_id, reply_markup
        )
    else:
        await _send_album_fallback(bot, chat_id, inputs, caption, lot_id, reply_markup)
    return True


async def send_lot_album(
    bot: Bot,
    chat_id: int,
    lot: LotData,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    remember_lot(lot)
    caption = lot_caption(lot)
    urls = album_photo_urls(lot)
    if not urls:
        await bot.send_message(
            chat_id,
            caption,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return

    if _lot_needs_upload(lot, urls):
        ok = await _send_uploaded_photos(
            bot, chat_id, urls, caption, lot.lot_external_id, reply_markup
        )
        if ok:
            return
        await bot.send_message(
            chat_id,
            caption,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return

    if len(urls) == 1:
        try:
            await bot.send_photo(
                chat_id,
                photo=urls[0],
                caption=caption[:1024],
                reply_markup=reply_markup,
            )
            return
        except TelegramAPIError as exc:
            logger.warning("Single photo failed for lot %s: %s", lot.lot_external_id, exc)
            if _is_url_fetch_error(exc):
                if await _send_uploaded_photos(
                    bot, chat_id, urls, caption, lot.lot_external_id, reply_markup
                ):
                    return
            await bot.send_message(
                chat_id,
                caption,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            return

    attempts = (
        ("tg-slideshow+media", slideshow_rich_message(caption, urls)),
        ("tg-slideshow+urls", slideshow_direct_html_message(caption, urls)),
        ("blocks", slideshow_blocks_message(caption, urls)),
    )
    url_fetch_failed = False
    for name, payload in attempts:
        try:
            await _send_rich(bot, chat_id, payload, reply_markup)
            return
        except TelegramAPIError as exc:
            logger.warning(
                "Article %s failed for lot %s: %s",
                name,
                lot.lot_external_id,
                exc,
            )
            url_fetch_failed = url_fetch_failed or _is_url_fetch_error(exc)
            if reply_markup is not None:
                try:
                    await _send_rich(bot, chat_id, payload, None)
                    await _send_keyboard_followup(bot, chat_id, reply_markup)
                    return
                except TelegramAPIError as nested:
                    url_fetch_failed = url_fetch_failed or _is_url_fetch_error(nested)

    if url_fetch_failed:
        if await _send_uploaded_photos(
            bot, chat_id, urls, caption, lot.lot_external_id, reply_markup
        ):
            return

    await _send_album_fallback(
        bot, chat_id, urls, caption, lot.lot_external_id, reply_markup
    )
