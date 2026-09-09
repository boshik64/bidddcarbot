from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import TelegramMethod
from aiogram.types import InputMediaPhoto, Message

from app.formatting import lot_caption
from app.parser import TG_ALBUM_MAX, TG_SLIDESHOW_MAX, LotData

logger = logging.getLogger(__name__)


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


def lot_article_html(caption: str, media_ids: list[str]) -> str:
    slides = "".join(f'<img src="tg://photo?id={mid}">' for mid in media_ids)
    body = "".join(f"<p>{line}</p>" for line in caption.split("\n") if line)
    if len(media_ids) >= 2:
        return f"<slideshow>{slides}</slideshow>{body}"
    if media_ids:
        return f"{slides}{body}"
    return body


def slideshow_rich_message(caption: str, urls: list[str]) -> dict[str, Any]:
    media_ids = [f"p{i}" for i in range(len(urls))]
    return {
        "html": lot_article_html(caption, media_ids),
        "media": [
            {"id": mid, "media": {"type": "photo", "media": url}}
            for mid, url in zip(media_ids, urls)
        ],
    }


def slideshow_blocks_message(urls: list[str]) -> dict[str, Any]:
    return {
        "blocks": [
            {
                "type": "slideshow",
                "blocks": [
                    {"type": "photo", "photo": {"type": "photo", "media": url}} for url in urls
                ],
            }
        ]
    }


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

    try:
        await bot(SendRichMessage(chat_id=chat_id, rich_message=slideshow_rich_message(caption, urls)))
        return
    except TelegramAPIError as exc:
        logger.warning("Article slideshow failed for lot %s: %s", lot.lot_external_id, exc)

    try:
        await bot(SendRichMessage(chat_id=chat_id, rich_message=slideshow_blocks_message(urls)))
        await bot.send_message(chat_id, caption, disable_web_page_preview=True)
        return
    except TelegramAPIError as exc:
        logger.warning("Block slideshow failed for lot %s: %s", lot.lot_external_id, exc)

    await _send_album_fallback(bot, chat_id, urls, caption, lot.lot_external_id)
