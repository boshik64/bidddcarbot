from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.access import has_access
from app.client import ParseError, fetch_lot
from app.config import settings
from app.db import SessionLocal
from app.formatting import (
    watch_missing_text,
    watch_reminder_text,
    watch_sale_text,
    watch_update_text,
)
from app.keyboards import lot_watch_keyboard
from app.lot_send import recalled_lot, remember_lot, send_lot_album
from app.models import Filter, SeenLot, WatchedLot, as_utc, utcnow
from app.parser import LotData, LotFetch, extract_lang, is_lot_finished, lot_from_item, lot_from_preview

logger = logging.getLogger(__name__)

WATCH_MISS_LIMIT = 3

_watch_lock = asyncio.Lock()


class WatchLimitError(Exception):
    pass


def _norm(value: str | None) -> str:
    return (value or "").strip()


def reminder_due(
    auction_at,
    *,
    now=None,
    reminded_24h: bool,
    reminded_2h: bool,
) -> str | None:
    if auction_at is None:
        return None
    when = as_utc(auction_at)
    if when is None:
        return None
    now = now or utcnow()
    remaining = when - now
    if remaining <= timedelta(0):
        return None
    if remaining <= timedelta(hours=2):
        if not reminded_2h:
            return "2h"
        return None
    if remaining <= timedelta(hours=24) and not reminded_24h:
        return "24h"
    return None


def watch_changes(row: WatchedLot, lot: LotData) -> list[str]:
    changes: list[str] = []
    if _norm(lot.current_bid) and _norm(row.last_bid) != _norm(lot.current_bid):
        if row.last_bid:
            changes.append(
                f"💰 Ставка: {escape(row.last_bid)} → {escape(lot.current_bid)}"
            )
        else:
            changes.append(f"💰 Ставка: {escape(lot.current_bid)}")
    if _norm(lot.status) and _norm(row.last_status) != _norm(lot.status):
        if row.last_status:
            changes.append(
                f"🏁 Статус: {escape(row.last_status)} → {escape(lot.status)}"
            )
        else:
            changes.append(f"🏁 Статус: {escape(lot.status)}")
    if _norm(lot.auction_raw) and _norm(row.auction_raw) != _norm(lot.auction_raw):
        changes.append(f"🕒 Аукцион: {escape(lot.auction_raw)}")
    return changes


def apply_lot_to_watch(row: WatchedLot, lot: LotData) -> None:
    row.title = lot.title or row.title
    row.lot_url = lot.url or row.lot_url
    if lot.vin:
        row.vin = lot.vin
    row.last_bid = lot.current_bid
    row.last_search_status = lot.search_status
    row.last_status = lot.status
    if lot.auction_at is not None:
        previous = as_utc(row.auction_at)
        if (
            previous is not None
            and abs((previous - lot.auction_at).total_seconds()) > 1800
        ):
            row.reminded_24h = False
            row.reminded_2h = False
        row.auction_at = lot.auction_at
    if lot.auction_raw:
        row.auction_raw = lot.auction_raw
    if lot.raw:
        row.raw_data = lot.raw
    row.last_checked_at = utcnow()
    row.consecutive_misses = 0


def lot_from_watch(row: WatchedLot) -> LotData:
    if isinstance(row.raw_data, dict) and row.raw_data:
        parsed = lot_from_item(row.raw_data, lang=extract_lang(row.lot_url or ""))
        if parsed is not None:
            remember_lot(parsed)
            return parsed
    lot = LotData(
        lot_external_id=row.lot_external_id,
        title=row.title,
        url=row.lot_url,
        vin=row.vin,
        current_bid=row.last_bid,
        status=row.last_status,
        search_status=row.last_search_status,
        auction_at=as_utc(row.auction_at),
        auction_raw=row.auction_raw,
    )
    remember_lot(lot)
    return lot


async def is_watched(session: AsyncSession, user_id: int, lot_id: str) -> bool:
    result = await session.execute(
        select(WatchedLot.id).where(
            WatchedLot.user_id == user_id,
            WatchedLot.lot_external_id == lot_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def watched_ids_for(session: AsyncSession, user_id: int) -> set[str]:
    result = await session.execute(
        select(WatchedLot.lot_external_id).where(WatchedLot.user_id == user_id)
    )
    return set(result.scalars().all())


async def watched_count(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(WatchedLot)
        .where(WatchedLot.user_id == user_id)
    )
    return int(result.scalar_one())


async def get_watched(
    session: AsyncSession, user_id: int, lot_id: str
) -> WatchedLot | None:
    result = await session.execute(
        select(WatchedLot).where(
            WatchedLot.user_id == user_id,
            WatchedLot.lot_external_id == lot_id,
        )
    )
    return result.scalar_one_or_none()


async def list_watched(session: AsyncSession, user_id: int) -> list[WatchedLot]:
    result = await session.execute(
        select(WatchedLot)
        .where(WatchedLot.user_id == user_id)
        .order_by(WatchedLot.created_at.desc())
    )
    return list(result.scalars().all())


async def add_watch(session: AsyncSession, user_id: int, lot: LotData) -> bool:
    existing = await get_watched(session, user_id, lot.lot_external_id)
    if existing is not None:
        apply_lot_to_watch(existing, lot)
        existing.last_checked_at = utcnow()
        return False
    if await watched_count(session, user_id) >= settings.max_watched_per_user:
        raise WatchLimitError(
            f"Лимит отслеживания: {settings.max_watched_per_user} лотов."
        )
    row = WatchedLot(
        user_id=user_id,
        lot_external_id=lot.lot_external_id,
        lot_url=lot.url,
        title=lot.title,
        vin=lot.vin,
        last_bid=lot.current_bid,
        last_search_status=lot.search_status,
        last_status=lot.status,
        auction_at=lot.auction_at,
        auction_raw=lot.auction_raw,
        raw_data=lot.raw or None,
        last_checked_at=utcnow(),
    )
    session.add(row)
    remember_lot(lot)
    return True


async def remove_watch(session: AsyncSession, user_id: int, lot_id: str) -> bool:
    row = await get_watched(session, user_id, lot_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def resolve_lot(
    session: AsyncSession,
    user_id: int,
    lot_id: str,
    cached_previews: list[dict[str, Any]] | None = None,
) -> LotData | None:
    remembered = recalled_lot(lot_id)
    if remembered is not None:
        return remembered
    if cached_previews:
        for item in cached_previews:
            if str(item.get("id") or "") == lot_id:
                lot = lot_from_preview(item)
                remember_lot(lot)
                return lot
    watched = await get_watched(session, user_id, lot_id)
    if watched is not None:
        return lot_from_watch(watched)
    result = await session.execute(
        select(SeenLot.raw_data)
        .join(Filter, SeenLot.filter_id == Filter.id)
        .where(Filter.user_id == user_id, SeenLot.lot_external_id == lot_id)
        .limit(1)
    )
    raw = result.scalar_one_or_none()
    if isinstance(raw, dict) and raw:
        lot = lot_from_item(raw)
        if lot is not None:
            remember_lot(lot)
            return lot
    return None


def _watch_due(row: WatchedLot) -> bool:
    last = as_utc(row.last_checked_at)
    if last is None:
        return True
    return utcnow() - last >= timedelta(minutes=settings.effective_poll_interval)


async def watch_markup_for(user_id: int, lot_id: str):
    async with SessionLocal() as session:
        watched = await is_watched(session, user_id, lot_id)
    return lot_watch_keyboard(lot_id, watched)


async def _send_notice(
    bot: Bot,
    chat_id: int,
    text: str,
    lot: LotData | None = None,
    *,
    watched: bool = False,
) -> None:
    markup = (
        lot_watch_keyboard(lot.lot_external_id, watched)
        if lot is not None and watched
        else None
    )
    try:
        if lot is not None and lot.photo_url:
            await bot.send_photo(
                chat_id,
                photo=lot.photo_url,
                caption=text[:1024],
                reply_markup=markup,
            )
            return
        await bot.send_message(
            chat_id,
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramAPIError as exc:
        logger.warning("Watch notice failed for chat %s: %s", chat_id, exc)


async def _mark_errors(watch_ids: list[int]) -> None:
    async with SessionLocal() as session:
        for watch_id in watch_ids:
            row = await session.get(WatchedLot, watch_id)
            if row is None:
                continue
            row.last_checked_at = utcnow()
        await session.commit()


async def _process_watch_result(
    bot: Bot, watch_id: int, fetched: LotFetch
) -> None:
    async with SessionLocal() as session:
        row = await session.get(
            WatchedLot, watch_id, options=[selectinload(WatchedLot.user)]
        )
        if row is None or row.user is None:
            return
        chat_id = row.user.telegram_chat_id
        if fetched.lot is None:
            row.consecutive_misses += 1
            row.last_checked_at = utcnow()
            if row.consecutive_misses < WATCH_MISS_LIMIT:
                await session.commit()
                return
            title, url = row.title, row.lot_url
            chat_id = row.user.telegram_chat_id
            await _send_notice(bot, chat_id, watch_missing_text(title, url))
            await session.delete(row)
            await session.commit()
            return

        lot = fetched.lot
        remember_lot(lot)
        chat_id = row.user.telegram_chat_id
        if is_lot_finished(lot, source=fetched.source):
            price = lot.final_bid or lot.current_bid
            text = watch_sale_text(
                lot.title or row.title,
                lot.url or row.lot_url,
                price=price,
                search_status=lot.search_status,
            )
            await _send_notice(bot, chat_id, text, lot)
            await session.delete(row)
            await session.commit()
            return

        changes = watch_changes(row, lot)
        kind = reminder_due(
            lot.auction_at or row.auction_at,
            reminded_24h=row.reminded_24h,
            reminded_2h=row.reminded_2h,
        )
        if kind == "2h":
            row.reminded_2h = True
            row.reminded_24h = True
        elif kind == "24h":
            row.reminded_24h = True
        apply_lot_to_watch(row, lot)
        await session.commit()

    if changes:
        await _send_notice(
            bot,
            chat_id,
            watch_update_text(lot.title, lot.url, changes),
            lot,
            watched=True,
        )
    if kind:
        await _send_notice(
            bot,
            chat_id,
            watch_reminder_text(
                lot.title,
                lot.url,
                kind,
                bid=lot.current_bid,
                auction_raw=lot.auction_raw,
            ),
            lot,
            watched=True,
        )


async def poll_watched_lots(bot: Bot) -> None:
    if _watch_lock.locked():
        logger.info("Previous watch poll still running, skip")
        return
    async with _watch_lock:
        async with SessionLocal() as session:
            result = await session.execute(
                select(WatchedLot)
                .options(selectinload(WatchedLot.user))
                .order_by(WatchedLot.id)
            )
            rows = list(result.scalars().all())

        due = [
            row
            for row in rows
            if row.user is not None and has_access(row.user) and _watch_due(row)
        ]
        skipped = sum(1 for row in rows if row.user is None or not has_access(row.user))
        logger.info(
            "Watch cycle: %s tracked, %s due, %s skipped (no subscription)",
            len(rows),
            len(due),
            skipped,
        )
        groups: dict[str, list[WatchedLot]] = defaultdict(list)
        for row in due:
            groups[row.lot_external_id].append(row)

        for lot_id, group in groups.items():
            vin = next((row.vin for row in group if row.vin), None)
            lot_url = next((row.lot_url for row in group if row.lot_url), None)
            try:
                fetched = await fetch_lot(lot_id, vin=vin, lot_url=lot_url)
            except ParseError as exc:
                logger.warning("Watch fetch failed for %s: %s", lot_id, exc)
                await _mark_errors([row.id for row in group])
                continue
            for row in group:
                try:
                    await _process_watch_result(bot, row.id, fetched)
                except Exception:
                    logger.exception(
                        "Unexpected error while processing watch %s", row.id
                    )


async def send_watched_lot_photos(bot: Bot, chat_id: int, user_id: int, lot_id: str) -> None:
    async with SessionLocal() as session:
        row = await get_watched(session, user_id, lot_id)
        if row is None:
            return
        lot = lot_from_watch(row)
        markup = lot_watch_keyboard(lot_id, True)
    await send_lot_album(bot, chat_id, lot, reply_markup=markup)
