from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputMediaPhoto

from app.formatting import lot_caption
from app.parser import TG_ALBUM_MAX, LotData

logger = logging.getLogger(__name__)


def album_photo_urls(lot: LotData) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for url in list(lot.photo_urls or []) + ([lot.photo_url] if lot.photo_url else []):
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
        if len(urls) >= TG_ALBUM_MAX:
            break
    return urls


async def send_lot_album(bot: Bot, chat_id: int, lot: LotData) -> None:
    caption = lot_caption(lot)
    urls = album_photo_urls(lot)
    if not urls:
        await bot.send_message(chat_id, caption, disable_web_page_preview=True)
        return
    if len(urls) == 1:
        try:
            await bot.send_photo(chat_id, photo=urls[0], caption=caption[:1024])
            return
        except TelegramAPIError as exc:
            logger.warning("Single photo failed for lot %s: %s", lot.lot_external_id, exc)
            await bot.send_message(chat_id, caption, disable_web_page_preview=True)
            return

    media = [
        InputMediaPhoto(
            media=url,
            caption=caption[:1024] if index == 0 else None,
            parse_mode=ParseMode.HTML if index == 0 else None,
        )
        for index, url in enumerate(urls)
    ]
    try:
        await bot.send_media_group(chat_id, media=media)
    except TelegramAPIError as exc:
        logger.warning("Album failed for lot %s: %s", lot.lot_external_id, exc)
        try:
            await bot.send_photo(chat_id, photo=urls[0], caption=caption[:1024])
        except TelegramAPIError:
            await bot.send_message(chat_id, caption, disable_web_page_preview=True)
