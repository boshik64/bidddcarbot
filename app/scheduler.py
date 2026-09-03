from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.client import ParseError, parse_filter
from app.config import settings
from app.db import SessionLocal
from app.formatting import lot_caption
from app.models import Filter, SeenLot, as_utc, utcnow
from app.parser import LotData

logger = logging.getLogger(__name__)

_poll_lock = asyncio.Lock()


def _interval_for(filt: Filter) -> int:
    minutes = filt.interval_minutes or settings.effective_poll_interval
    return max(minutes, settings.min_poll_interval_minutes)


def _is_due(filt: Filter) -> bool:
    last = as_utc(filt.last_checked_at)
    if last is None:
        return True
    delta = utcnow() - last
    return delta >= timedelta(minutes=_interval_for(filt))


async def _send_lot(bot: Bot, chat_id: int, lot: LotData) -> None:
    caption = lot_caption(lot)
    try:
        if lot.photo_url and len(caption) <= 1024:
            await bot.send_photo(
                chat_id,
                photo=lot.photo_url,
                caption=caption,
            )
            return
        if lot.photo_url:
            await bot.send_photo(chat_id, photo=lot.photo_url)
        await bot.send_message(chat_id, caption, disable_web_page_preview=True)
    except TelegramAPIError as exc:
        logger.warning("Failed to send lot %s to %s: %s", lot.lot_external_id, chat_id, exc)
        try:
            await bot.send_message(chat_id, caption, disable_web_page_preview=True)
        except TelegramAPIError:
            logger.exception("Fallback text send also failed for lot %s", lot.lot_external_id)


async def _process_filter(bot: Bot, filt: Filter) -> None:
    chat_id = filt.user.telegram_chat_id
    try:
        lots = await parse_filter(filt.url)
    except Exception as exc:
        logger.exception("Parse failed for filter %s: %s", filt.id, exc)
        async with SessionLocal() as session:
            db_filt = await session.get(Filter, filt.id)
            if db_filt is None:
                return
            db_filt.consecutive_failures += 1
            db_filt.last_error = str(exc)[:1000]
            db_filt.last_checked_at = utcnow()
            should_pause = db_filt.consecutive_failures >= settings.parse_failure_threshold
            if should_pause:
                db_filt.is_paused = True
            await session.commit()
        if should_pause:
            try:
                await bot.send_message(
                    chat_id,
                    f"Не могу распарсить фильтр #{filt.id} ({filt.label}). "
                    "Возможно, ссылка устарела или сайт изменился. "
                    f"Поставил на паузу. Вернуть: /resume_filter {filt.id}",
                )
            except TelegramAPIError:
                logger.warning("Could not notify user %s about paused filter", chat_id)
        return

    async with SessionLocal() as session:
        db_filt = await session.get(Filter, filt.id)
        if db_filt is None:
            return
        existing = await session.execute(
            select(SeenLot.lot_external_id).where(SeenLot.filter_id == filt.id)
        )
        known = set(existing.scalars().all())
        new_lots = [lot for lot in lots if lot.lot_external_id not in known]
        now = utcnow()
        for lot in new_lots:
            session.add(
                SeenLot(
                    filter_id=filt.id,
                    lot_external_id=lot.lot_external_id,
                    first_seen_at=now,
                    raw_data=lot.raw,
                )
            )
        db_filt.last_checked_at = now
        db_filt.consecutive_failures = 0
        db_filt.last_error = None
        await session.commit()

    if not new_lots:
        logger.info("Filter %s: no new lots (%s total on pages)", filt.id, len(lots))
        return

    to_send = new_lots[: settings.max_notify_per_filter]
    skipped = len(new_lots) - len(to_send)
    logger.info("Filter %s: %s new lots, sending %s", filt.id, len(new_lots), len(to_send))

    header = f"Новые лоты по фильтру #{filt.id} ({filt.label}):"
    try:
        await bot.send_message(chat_id, header)
    except TelegramAPIError:
        logger.warning("Could not send header to %s", chat_id)

    for lot in to_send:
        await _send_lot(bot, chat_id, lot)
        await asyncio.sleep(0.4)

    if skipped:
        try:
            await bot.send_message(
                chat_id,
                f"Ещё {skipped} новых лотов не отправил, чтобы не заспамить. "
                "Они уже в списке «виденных».",
            )
        except TelegramAPIError:
            pass


async def poll_filters(bot: Bot) -> None:
    if _poll_lock.locked():
        logger.info("Previous poll still running, skip")
        return
    async with _poll_lock:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Filter)
                .where(Filter.is_paused.is_(False))
                .options(selectinload(Filter.user))
                .order_by(Filter.id)
            )
            filters = list(result.scalars().all())

        due = [filt for filt in filters if _is_due(filt)]
        logger.info("Poll cycle: %s active, %s due", len(filters), len(due))
        for filt in due:
            try:
                await _process_filter(bot, filt)
            except Exception:
                logger.exception("Unexpected error while processing filter %s", filt.id)
