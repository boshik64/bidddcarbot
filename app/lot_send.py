from __future__ import annotations

import logging
import re
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import TelegramMethod
from aiogram.types import InputMediaPhoto, Message

from app.formatting import lot_caption
from app.parser import TG_ALBUM_MAX, TG_SLIDESHOW_MAX, LotData

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


class SendRichMessage(TelegramMethod[Message]):
    """Bot API 10.2+ sendRichMessage — native article slideshow."""

    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int
    rich_message: dict[str, Any]


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


async def _send_rich(bot: Bot, chat_id: int, payload: dict[str, Any]) -> None:
    await bot(SendRichMessage(chat_id=chat_id, rich_message=payload))


async def _send_single_photo(bot: Bot, chat_id: int, url: str, caption: str, lot_id: str) -> None:
    try:
        await bot.send_photo(chat_id, photo=url, caption=caption[:1024])
    except TelegramAPIError as exc:
        logger.warning("Single photo failed for lot %s: %s", lot_id, exc)
        await bot.send_message(chat_id, caption, disable_web_page_preview=True)


async def _send_album_fallback(bot: Bot, chat_id: int, urls: list[str], caption: str, lot_id: str) -> None:
    media = [InputMediaPhoto(media=url) for url in urls[:TG_ALBUM_MAX]]
    media[0] = InputMediaPhoto(media=urls[0], caption=caption[:1024])
    try:
        await bot.send_media_group(chat_id, media=media)
    except TelegramAPIError as exc:
        logger.warning("Album fallback failed for lot %s: %s", lot_id, exc)
        await _send_single_photo(bot, chat_id, urls[0], caption, lot_id)


async def send_lot_album(bot: Bot, chat_id: int, lot: LotData) -> None:
    caption = lot_caption(lot)
    urls = album_photo_urls(lot)
    if not urls:
        await bot.send_message(chat_id, caption, disable_web_page_preview=True)
        return
    if len(urls) == 1:
        await _send_single_photo(bot, chat_id, urls[0], caption, lot.lot_external_id)
        return

    attempts = (
        ("tg-slideshow+media", slideshow_rich_message(caption, urls)),
        ("tg-slideshow+urls", slideshow_direct_html_message(caption, urls)),
        ("blocks", slideshow_blocks_message(caption, urls)),
    )
    for name, payload in attempts:
        try:
            await _send_rich(bot, chat_id, payload)
            return
        except TelegramAPIError as exc:
            logger.warning(
                "Article %s failed for lot %s: %s",
                name,
                lot.lot_external_id,
                exc,
            )

    await _send_album_fallback(bot, chat_id, urls, caption, lot.lot_external_id)
