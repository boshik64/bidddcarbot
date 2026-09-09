from __future__ import annotations

import logging
import secrets
from datetime import datetime
from html import escape
from typing import Union
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    PLAN_MONTH,
    PLAN_YEAR,
    access_label,
    format_usdt,
    grant_lifetime,
    grant_plan,
    has_access,
    plan_amount,
    plan_title,
)
from app.client import ParseError, fetch_lot, parse_filter_with_total
from app.lot_send import remember_lot, send_lot_album
from app.config import settings
from app.db import SessionLocal
from app.formatting import (
    HELP_TEXT,
    START_TEXT,
    filter_card_text,
    lots_page_text,
    watch_list_text,
)
from app.keyboards import (
    BTN_ADD,
    BTN_FILTERS,
    BTN_INTERVAL,
    BTN_STATUS,
    BTN_SUB,
    MENU_BUTTON_TEXTS,
    WATCH_PAGE_SIZE,
    add_filter_keyboard,
    back_to_filters_keyboard,
    confirm_delete_keyboard,
    empty_filters_keyboard,
    filter_card_keyboard,
    filters_keyboard,
    interval_keyboard,
    is_watch_button,
    LOTS_PAGE_SIZE,
    invoice_keyboard,
    lot_watch_keyboard,
    lots_page_keyboard,
    main_reply_keyboard,
    paywall_keyboard,
    watched_list_keyboard,
)
from app.models import Filter, Payment, SeenLot, User, as_utc, utcnow
from app.parser import (
    FilterUrlError,
    LotData,
    filter_title,
    label_from_url,
    lot_from_preview,
    lot_to_preview,
    validate_filter_url,
)
from app.watch import (
    WatchLimitError,
    add_watch,
    is_watched,
    list_watched,
    remaining_from_watch,
    refresh_user_watched,
    remove_watch,
    resolve_lot,
    send_watched_lot_photos,
    watched_count,
    watched_ids_for,
)
from app.tron import (
    PaymentError,
    UsdtTransfer,
    extract_tx_hash,
    find_transfer_by_hash,
    find_unused_exact_payments,
    matches_invoice,
)

logger = logging.getLogger(__name__)
router = Router()

Event = Union[Message, CallbackQuery]


class AddFilterStates(StatesGroup):
    waiting_url = State()


class PayStates(StatesGroup):
    waiting_tx = State()


class GodStates(StatesGroup):
    waiting_password = State()


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except Exception:
        return ZoneInfo("UTC")


def _fmt_dt(value: datetime | None) -> str:
    aware = as_utc(value)
    if aware is None:
        return "ещё не было"
    local = aware.astimezone(_tz())
    return local.strftime("%d.%m.%Y %H:%M")


def _chat_id(event: Event) -> int:
    if isinstance(event, CallbackQuery):
        if event.message is None:
            return event.from_user.id
        return event.message.chat.id
    return event.chat.id


async def _send(
    event: Event,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    edit: bool = True,
) -> None:
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except TelegramBadRequest:
            pass
        message = event.message
        if message is None:
            return
        if edit:
            try:
                await message.edit_text(
                    text, reply_markup=markup, disable_web_page_preview=True
                )
                return
            except TelegramBadRequest:
                pass
        await message.answer(
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        return
    await event.answer(
        text,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


async def get_or_create_user(session: AsyncSession, chat_id: int) -> User:
    result = await session.execute(
        select(User).where(User.telegram_chat_id == chat_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_chat_id=chat_id)
        session.add(user)
        await session.flush()
    return user


async def user_filter_count(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(Filter).where(Filter.user_id == user_id)
    )
    return int(result.scalar_one())


async def get_owned_filter(
    session: AsyncSession, user_id: int, filter_id: int
) -> Filter | None:
    result = await session.execute(
        select(Filter).where(Filter.id == filter_id, Filter.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def snapshot_lots(session: AsyncSession, filter_id: int, lots: list[LotData]) -> None:
    existing = await session.execute(
        select(SeenLot.lot_external_id).where(SeenLot.filter_id == filter_id)
    )
    known = set(existing.scalars().all())
    now = utcnow()
    for lot in lots:
        if lot.lot_external_id in known:
            continue
        session.add(
            SeenLot(
                filter_id=filter_id,
                lot_external_id=lot.lot_external_id,
                first_seen_at=now,
                raw_data=lot.raw,
            )
        )
        known.add(lot.lot_external_id)


async def _lots_count(session: AsyncSession, filter_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(SeenLot).where(SeenLot.filter_id == filter_id)
    )
    return int(result.scalar_one())


def _interval_of(filt: Filter) -> int:
    return max(
        filt.interval_minutes or settings.effective_poll_interval,
        settings.min_poll_interval_minutes,
    )


def _invoice_text(plan: str) -> str:
    amount_s = format_usdt(plan_amount(plan))
    wallet = settings.usdt_trc20_wallet
    return (
        f"Тариф: <b>{plan_title(plan)}</b> — {amount_s} USDT\n"
        f"Сеть: <b>TRON (TRC20)</b>\n"
        f"Токен: <b>USDT</b>\n\n"
        f"Кошелёк (нажми, чтобы скопировать):\n<code>{wallet}</code>\n\n"
        f"Отправь <b>ровно {amount_s} USDT TRC20</b> на этот адрес.\n"
        "Потом нажми «Я оплатил» или сразу пришли TxID из кошелька."
    )


async def show_paywall(event: Event) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        label = access_label(user, _fmt_dt)
        await session.commit()
    await _send(
        event,
        "Для фильтров и уведомлений нужна подписка.\n"
        f"Сейчас: <b>{label}</b>\n\n"
        f"• {plan_title(PLAN_MONTH)} — <b>{format_usdt(plan_amount(PLAN_MONTH))} USDT</b> TRC20\n"
        f"• {plan_title(PLAN_YEAR)} — <b>{format_usdt(plan_amount(PLAN_YEAR))} USDT</b> TRC20\n\n"
        "Оплата на TRON, только USDT TRC20. Выбери тариф:",
        paywall_keyboard(),
    )


async def require_access(event: Event) -> bool:
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        ok = has_access(user)
        await session.commit()
    if ok:
        return True
    await show_paywall(event)
    return False


async def show_invoice(event: Event, state: FSMContext, plan: str) -> None:
    if plan not in (PLAN_MONTH, PLAN_YEAR):
        return
    await state.set_state(PayStates.waiting_tx)
    await state.update_data(plan=plan)
    await _send(event, _invoice_text(plan), invoice_keyboard(plan))


async def _used_hashes(session: AsyncSession) -> set[str]:
    result = await session.execute(select(Payment.tx_hash))
    return {h.lower() for h in result.scalars().all() if h}


async def _activate_payment(
    session: AsyncSession, user: User, plan: str, transfer: UsdtTransfer
) -> str:
    existing = await session.execute(
        select(Payment).where(Payment.tx_hash == transfer.tx_hash)
    )
    if existing.scalar_one_or_none() is not None:
        raise PaymentError("Эта транзакция уже была засчитана.")
    until = grant_plan(user, plan)
    session.add(
        Payment(
            user_id=user.id,
            plan=plan,
            amount_usdt=format_usdt(transfer.amount),
            tx_hash=transfer.tx_hash,
            from_address=transfer.from_address,
            status="confirmed",
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise PaymentError("Эта транзакция уже была засчитана.") from exc
    logger.info(
        "Payment confirmed telegram=%s plan=%s amount=%s tx=%s from=%s",
        user.telegram_chat_id,
        plan,
        format_usdt(transfer.amount),
        transfer.tx_hash,
        transfer.from_address,
    )
    return _fmt_dt(until)


async def confirm_plan_payment(
    event: Event, state: FSMContext, plan: str, tx_hash: str | None
) -> None:
    wallet = settings.usdt_trc20_wallet
    amount = plan_amount(plan)
    wait_msg = None
    if isinstance(event, Message):
        wait_msg = await event.answer("Проверяю транзакцию в сети TRON…")
    try:
        async with SessionLocal() as session:
            used = await _used_hashes(session)
        if tx_hash:
            transfer = await find_transfer_by_hash(tx_hash, wallet)
            if not matches_invoice(transfer, wallet, amount):
                raise PaymentError(
                    f"Перевод найден, но это не {format_usdt(amount)} USDT "
                    f"на кошелёк подписки."
                )
            chosen = transfer
        else:
            candidates = await find_unused_exact_payments(wallet, amount, used)
            if not candidates:
                raise PaymentError(
                    "Пока не вижу подходящий перевод. "
                    "Пришли TxID — 64 символа из кошелька."
                )
            if len(candidates) > 1:
                raise PaymentError(
                    "Нашёл несколько переводов на эту сумму. Пришли точный TxID."
                )
            chosen = candidates[0]
        async with SessionLocal() as session:
            user = await get_or_create_user(session, _chat_id(event))
            until = await _activate_payment(session, user, plan, chosen)
    except PaymentError as exc:
        text = str(exc)
        if wait_msg is not None:
            await wait_msg.edit_text(text, reply_markup=invoice_keyboard(plan))
        else:
            await _send(event, text, invoice_keyboard(plan))
        return
    except Exception:
        logger.exception("Payment check failed")
        text = "Не удалось проверить оплату. Попробуй ещё раз через минуту или пришли TxID."
        if wait_msg is not None:
            await wait_msg.edit_text(text, reply_markup=invoice_keyboard(plan))
        else:
            await _send(event, text, invoice_keyboard(plan))
        return

    await state.clear()
    from_wallet = escape(chosen.from_address) if chosen.from_address else "—"
    text = (
        f"Оплата подтверждена. Подписка <b>{plan_title(plan)}</b> активна до {until}.\n"
        f"С кошелька: <code>{from_wallet}</code>\n"
        f"TxID: <code>{escape(chosen.tx_hash)}</code>\n\n"
        "Можно добавлять фильтры."
    )
    if wait_msg is not None:
        await wait_msg.edit_text(text, reply_markup=back_to_filters_keyboard())
    else:
        await _send(event, text, back_to_filters_keyboard())


async def show_filter_list(event: Event) -> None:
    if not await require_access(event):
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        result = await session.execute(
            select(Filter).where(Filter.user_id == user.id).order_by(Filter.id)
        )
        filters = list(result.scalars().all())
        await session.commit()

    if not filters:
        await _send(
            event,
            "Фильтров пока нет. Нажми «Добавить» и пришли ссылку на поиск bid.cars.",
            empty_filters_keyboard(),
        )
        return
    await _send(event, "Твои фильтры — нажми карточку, чтобы открыть:", filters_keyboard(filters))


async def show_filter_card(event: Event, filter_id: int, *, edit: bool = True) -> None:
    if not await require_access(event):
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        filt = await get_owned_filter(session, user.id, filter_id)
        if filt is None:
            if isinstance(event, CallbackQuery):
                await event.answer("Фильтр не найден", show_alert=True)
            else:
                await event.answer("Фильтр не найден")
            return
        lots_count = await _lots_count(session, filt.id)
        text = filter_card_text(
            filt.id,
            filt.url,
            is_paused=filt.is_paused,
            last_checked=_fmt_dt(filt.last_checked_at),
            interval_minutes=_interval_of(filt),
            lots_count=lots_count,
            active_lots=filt.last_active_count,
            last_error=filt.last_error,
            label=filt.label,
        )
        markup = filter_card_keyboard(filt)
        await session.commit()
    await _send(event, text, markup, edit=edit)


async def show_filter_lots(
    event: Event,
    state: FSMContext,
    filter_id: int,
    page: int = 0,
    *,
    refresh: bool = False,
) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        filt = await get_owned_filter(session, user.id, filter_id)
        if filt is None:
            if isinstance(event, CallbackQuery):
                await event.answer("Фильтр не найден", show_alert=True)
            else:
                await event.answer("Фильтр не найден")
            return
        url = filt.url
        title = filter_title(filt.url, fallback=filt.label)
        await session.commit()

    data = await state.get_data()
    cached = (
        data.get("browse_lots")
        if data.get("browse_filter_id") == filter_id and "browse_lots" in data
        else None
    )
    if refresh or cached is None:
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("Загружаю лоты…")
            except TelegramBadRequest:
                pass
            message = event.message
            if message is not None:
                try:
                    await message.edit_text("Загружаю текущие лоты с bid.cars…")
                except TelegramBadRequest:
                    pass
        try:
            fetched, active_total = await parse_filter_with_total(url)
        except ParseError as exc:
            logger.warning("Browse lots failed for filter %s: %s", filter_id, exc)
            await _send(
                event,
                f"Не смог загрузить лоты: {escape(str(exc))}",
                lots_page_keyboard(filter_id, 0, 0),
            )
            return
        cached = [lot_to_preview(lot) for lot in fetched]
        for lot in fetched:
            remember_lot(lot)
        await state.update_data(browse_filter_id=filter_id, browse_lots=cached)
        async with SessionLocal() as session:
            user = await get_or_create_user(session, _chat_id(event))
            filt = await get_owned_filter(session, user.id, filter_id)
            if filt is not None:
                filt.last_active_count = active_total
                await session.commit()

    lots = [lot_from_preview(item) for item in cached]
    for lot in lots:
        remember_lot(lot)
    total = len(lots)
    pages = max(1, (total + LOTS_PAGE_SIZE - 1) // LOTS_PAGE_SIZE) if total else 1
    page = max(0, min(page, pages - 1))
    await state.update_data(browse_page=page)
    start = page * LOTS_PAGE_SIZE
    page_lots = lots[start : start + LOTS_PAGE_SIZE]
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        watched = await watched_ids_for(session, user.id)
        await session.commit()
    await _send(
        event,
        lots_page_text(
            filter_id,
            title,
            lots,
            page=page,
            page_size=LOTS_PAGE_SIZE,
        ),
        lots_page_keyboard(
            filter_id,
            page,
            total,
            page_lot_ids=[lot.lot_external_id for lot in page_lots],
            watched_ids=watched,
        ),
    )


async def send_filter_lot_photos(event: Event, state: FSMContext, filter_id: int, index: int) -> None:
    data = await state.get_data()
    cached = data.get("browse_lots") if data.get("browse_filter_id") == filter_id else None
    if not cached:
        await show_filter_lots(event, state, filter_id, page=index // LOTS_PAGE_SIZE, refresh=True)
        data = await state.get_data()
        cached = data.get("browse_lots") if data.get("browse_filter_id") == filter_id else None
        if not cached:
            return
    if index < 0 or index >= len(cached):
        if isinstance(event, CallbackQuery):
            await event.answer("Лот не найден", show_alert=True)
        return
    lot = lot_from_preview(cached[index])
    remember_lot(lot)
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        watched = await is_watched(session, user.id, lot.lot_external_id)
        await session.commit()
    markup = lot_watch_keyboard(lot.lot_external_id, watched)
    if isinstance(event, CallbackQuery):
        try:
            await event.answer("Отправляю фото…")
        except TelegramBadRequest:
            pass
        chat_id = _chat_id(event)
        await send_lot_album(event.bot, chat_id, lot, reply_markup=markup)
        return
    await send_lot_album(event.bot, event.chat.id, lot, reply_markup=markup)


async def show_status(event: Event) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        sub = access_label(user, _fmt_dt)
        allowed = has_access(user)
        result = await session.execute(
            select(Filter).where(Filter.user_id == user.id).order_by(Filter.id)
        )
        filters = list(result.scalars().all())
        ids = [f.id for f in filters]
        lots_count = 0
        watch_count = await watched_count(session, user.id)
        if ids:
            lots_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(SeenLot)
                        .where(SeenLot.filter_id.in_(ids))
                    )
                ).scalar_one()
            )
        await session.commit()

    if not allowed:
        await _send(
            event,
            f"Подписка: <b>{sub}</b>\n\nОплати тариф, чтобы добавлять фильтры и получать лоты.",
            paywall_keyboard(),
        )
        return

    active = sum(1 for f in filters if not f.is_paused)
    last_check = max((f.last_checked_at for f in filters if f.last_checked_at), default=None)
    text = (
        f"Подписка: <b>{sub}</b>\n"
        f"Фильтров: {len(filters)} (активных {active})\n"
        f"Последняя проверка: {_fmt_dt(last_check)}\n"
        f"Всего запомненных лотов: {lots_count}\n"
        f"Отслеживаю лотов: {watch_count}\n"
        f"Интервал: {settings.effective_poll_interval} мин "
        f"(минимум {settings.min_poll_interval_minutes})."
    )
    await _send(event, text, back_to_filters_keyboard())


async def show_watch_list(
    event: Event,
    state: FSMContext,
    page: int = 0,
    *,
    refresh: bool = False,
) -> None:
    if not await require_access(event):
        return
    wait_msg = None
    if isinstance(event, Message):
        wait_msg = await event.answer(
            "Обновляю отслеживаемые лоты…" if refresh else "Отслеживаемые лоты",
            reply_markup=main_reply_keyboard(),
        )
    elif refresh:
        try:
            await event.answer("Обновляю…")
        except TelegramBadRequest:
            pass
        if event.message is not None:
            try:
                await event.message.edit_text("Обновляю отслеживаемые лоты…")
            except TelegramBadRequest:
                pass
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        user_id = user.id
        rows: list = []
        removed: list[str] = []
        if not refresh:
            rows = await list_watched(session, user_id)
        await session.commit()
    if refresh:
        rows, removed = await refresh_user_watched(user_id)
    total = len(rows)
    pages = max(1, (total + WATCH_PAGE_SIZE - 1) // WATCH_PAGE_SIZE) if total else 1
    page = max(0, min(page, pages - 1))
    await state.update_data(watch_page=page)
    start = page * WATCH_PAGE_SIZE
    chunk = rows[start : start + WATCH_PAGE_SIZE]
    items = [
        (
            row.title,
            row.lot_url,
            row.last_bid,
            row.auction_raw,
            remaining_from_watch(row),
        )
        for row in rows
    ]
    markup_items = [(row.lot_external_id, row.title) for row in chunk]
    text = watch_list_text(items, page=page, page_size=WATCH_PAGE_SIZE, total=total)
    if removed:
        names = ", ".join(escape(name) for name in removed[:5])
        extra = f"Снял с отслеживания (уже не активны): {names}"
        if len(removed) > 5:
            extra += f" и ещё {len(removed) - 5}"
        text = extra + ".\n\n" + text
    markup = watched_list_keyboard(markup_items, page=page, total=total)
    if wait_msg is not None:
        try:
            await wait_msg.edit_text(
                text, reply_markup=markup, disable_web_page_preview=True
            )
            return
        except TelegramBadRequest:
            pass
    await _send(event, text, markup)


async def show_interval(event: Event) -> None:
    if not await require_access(event):
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        result = await session.execute(
            select(Filter.interval_minutes).where(Filter.user_id == user.id)
        )
        values = [row[0] for row in result.all()]
        await session.commit()
    current = next((v for v in values if v), None) or settings.effective_poll_interval
    current = max(current, settings.min_poll_interval_minutes)
    await _send(
        event,
        f"Как часто проверять фильтры?\nСейчас: <b>{current} мин</b>.\n"
        f"Чаще {settings.min_poll_interval_minutes} мин нельзя.",
        interval_keyboard(current, settings.min_poll_interval_minutes),
    )


async def prompt_add_filter(event: Event, state: FSMContext) -> None:
    if not await require_access(event):
        return
    await state.set_state(AddFilterStates.waiting_url)
    await _send(
        event,
        "Пришли ссылку на страницу поиска bid.cars "
        "(из адресной строки после фильтра).",
        add_filter_keyboard(),
        edit=isinstance(event, CallbackQuery),
    )


async def add_filter_from_url(message: Message, url: str) -> None:
    if not await require_access(message):
        return
    try:
        canonical = validate_filter_url(url)
    except FilterUrlError as exc:
        await message.answer(str(exc), reply_markup=add_filter_keyboard())
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        count = await user_filter_count(session, user.id)
        if count >= settings.max_filters_per_user:
            await message.answer(
                f"Лимит фильтров: {settings.max_filters_per_user}. "
                "Удали один из карточки, потом добавляй новый.",
                reply_markup=back_to_filters_keyboard(),
            )
            return

        duplicate = await session.execute(
            select(Filter).where(Filter.user_id == user.id, Filter.url == canonical)
        )
        existing = duplicate.scalar_one_or_none()
        if existing is not None:
            await message.answer(
                "Этот фильтр уже добавлен.",
                reply_markup=filter_card_keyboard(existing),
            )
            return

        wait_msg = await message.answer("Добавляю фильтр, сейчас сниму текущие лоты…")
        try:
            lots, active_total = await parse_filter_with_total(canonical)
        except ParseError as exc:
            logger.warning("Initial parse failed for %s: %s", canonical, exc)
            await wait_msg.edit_text(
                f"Не смог прочитать фильтр: {exc}\n"
                "Проверь ссылку и попробуй ещё раз чуть позже.",
                reply_markup=add_filter_keyboard(),
            )
            return
        except FilterUrlError as exc:
            await wait_msg.edit_text(str(exc), reply_markup=add_filter_keyboard())
            return

        filt = Filter(
            user_id=user.id,
            url=canonical,
            label=label_from_url(canonical),
            interval_minutes=settings.effective_poll_interval,
        )
        session.add(filt)
        await session.flush()
        await snapshot_lots(session, filt.id, lots)
        filt.last_checked_at = utcnow()
        filt.consecutive_failures = 0
        filt.last_active_count = active_total
        await session.commit()
        await session.refresh(filt)

        text = (
            f"Фильтр <b>#{filt.id}</b> добавлен: {filter_title(canonical)}\n"
            f"Сейчас по нему {active_total} лотов. "
            "Их я запомнил и спамить не буду — пришлю только новые."
        )
        card = filter_card_text(
            filt.id,
            filt.url,
            is_paused=False,
            last_checked=_fmt_dt(filt.last_checked_at),
            interval_minutes=_interval_of(filt),
            lots_count=len(lots),
            active_lots=active_total,
            label=filt.label,
        )
        await wait_msg.edit_text(
            f"{text}\n\n{card}",
            reply_markup=filter_card_keyboard(filt),
            disable_web_page_preview=True,
        )


# --- Reply-клавиатура и /start -------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        sub = access_label(user, _fmt_dt)
        await session.commit()
    await message.answer(
        START_TEXT + f"\n\nТвоя подписка: <b>{sub}</b>",
        reply_markup=main_reply_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP_TEXT, reply_markup=main_reply_keyboard())


@router.message(Command("cancel"))
@router.message(F.text == "Отмена")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=main_reply_keyboard())


@router.message(Command("list_filters"))
@router.message(F.text == BTN_FILTERS)
async def cmd_list_filters(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_filter_list(message)


@router.message(Command("add_filter"))
async def cmd_add_filter(
    message: Message, command: CommandObject, state: FSMContext
) -> None:
    url = (command.args or "").strip()
    if url:
        await state.clear()
        await add_filter_from_url(message, url)
        return
    await prompt_add_filter(message, state)


@router.message(F.text == BTN_ADD)
async def kb_add(message: Message, state: FSMContext) -> None:
    await prompt_add_filter(message, state)


@router.message(Command("status"))
@router.message(F.text == BTN_STATUS)
async def cmd_status(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_status(message)


@router.message(Command("watch"))
@router.message(F.text.func(is_watch_button))
async def cmd_watch(message: Message, state: FSMContext) -> None:
    await show_watch_list(message, state)


@router.message(Command("set_interval"))
async def cmd_set_interval(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    raw = (command.args or "").strip()
    if raw:
        try:
            minutes = int(raw.split()[0])
        except ValueError:
            await show_interval(message)
            return
        await _apply_interval(message, minutes)
        return
    await show_interval(message)


@router.message(F.text == BTN_INTERVAL)
async def kb_interval(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_interval(message)


@router.message(Command("subscribe"))
@router.message(F.text == BTN_SUB)
async def cmd_subscribe(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_paywall(message)


@router.message(Command("set_god_mode"))
async def cmd_set_god_mode(
    message: Message, command: CommandObject, state: FSMContext
) -> None:
    if not settings.god_mode_password:
        await message.answer("Команда не настроена.")
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Нужен telegram_id.")
        return
    try:
        target_id = int(raw.split()[0])
    except ValueError:
        await message.answer("telegram_id должен быть числом.")
        return
    await state.set_state(GodStates.waiting_password)
    await state.update_data(god_target=target_id)
    await message.answer("Введи пароль.")


@router.message(GodStates.waiting_password, F.text)
async def god_password(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_id = data.get("god_target")
    given = (message.text or "").strip().encode("utf-8")
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    expected = settings.god_mode_password.encode("utf-8")
    await state.clear()
    if target_id is None or len(given) != len(expected) or not secrets.compare_digest(given, expected):
        await message.answer("Неверный пароль.")
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, int(target_id))
        grant_lifetime(user)
        await session.commit()
    await message.answer(f"Бессрочный доступ выдан: <code>{target_id}</code>")


@router.message(PayStates.waiting_tx, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTON_TEXTS), ~F.text.func(is_watch_button))
async def got_tx_hash(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    plan = data.get("plan")
    if plan not in (PLAN_MONTH, PLAN_YEAR):
        await state.clear()
        await show_paywall(message)
        return
    try:
        tx_hash = extract_tx_hash(message.text or "")
    except PaymentError as exc:
        await message.answer(str(exc), reply_markup=invoice_keyboard(plan))
        return
    await confirm_plan_payment(message, state, plan, tx_hash)


@router.message(Command("remove_filter"))
@router.message(Command("pause_filter"))
@router.message(Command("resume_filter"))
async def cmd_legacy_manage(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Выбери фильтр кнопкой — дальше пауза и удаление внутри карточки.")
    await show_filter_list(message)


@router.message(AddFilterStates.waiting_url, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTON_TEXTS), ~F.text.func(is_watch_button))
async def got_filter_url(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await state.clear()
    await add_filter_from_url(message, text)


@router.message(StateFilter(None), F.text.regexp(r"https?://(?:www\.)?bid\.cars/", flags=0))
async def pasted_url(message: Message) -> None:
    await add_filter_from_url(message, message.text or "")


# --- Callbacks -----------------------------------------------------------------

@router.callback_query(F.data == "nav:filters")
async def cb_filters(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_filter_list(callback)


@router.callback_query(F.data == "nav:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await prompt_add_filter(callback, state)


@router.callback_query(F.data == "nav:watch")
async def cb_watch(callback: CallbackQuery, state: FSMContext) -> None:
    await show_watch_list(callback, state)


@router.callback_query(F.data == "nav:status")
async def cb_status(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_status(callback)


@router.callback_query(F.data == "nav:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_filter_list(callback)


@router.callback_query(F.data == "nav:pay")
async def cb_pay(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_paywall(callback)


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay_plan(callback: CallbackQuery, state: FSMContext) -> None:
    plan = (callback.data or "").split(":")[1]
    if plan not in (PLAN_MONTH, PLAN_YEAR):
        await callback.answer()
        return
    await show_invoice(callback, state, plan)


@router.callback_query(F.data.startswith("payok:"))
async def cb_pay_ok(callback: CallbackQuery, state: FSMContext) -> None:
    plan = (callback.data or "").split(":")[1]
    if plan not in (PLAN_MONTH, PLAN_YEAR):
        await callback.answer()
        return
    await state.set_state(PayStates.waiting_tx)
    await state.update_data(plan=plan)
    await confirm_plan_payment(callback, state, plan, None)


@router.callback_query(F.data.startswith("int:"))
async def cb_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        minutes = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный интервал", show_alert=True)
        return
    await _apply_interval(callback, minutes)


@router.callback_query(F.data.startswith("flt:"))
async def cb_filter_actions(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_access(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 2:
        await callback.answer()
        return
    try:
        filter_id = int(parts[1])
    except ValueError:
        await callback.answer("Некорректный фильтр", show_alert=True)
        return
    action = parts[2] if len(parts) > 2 else "open"

    if action == "open":
        await show_filter_card(callback, filter_id)
        return
    if action == "lots":
        page = 0
        refresh = len(parts) <= 3
        if len(parts) > 3:
            try:
                page = int(parts[3])
            except ValueError:
                page = 0
        await show_filter_lots(callback, state, filter_id, page, refresh=refresh)
        return
    if action == "ph":
        try:
            index = int(parts[3])
        except (IndexError, ValueError):
            await callback.answer("Лот не найден", show_alert=True)
            return
        await send_filter_lot_photos(callback, state, filter_id, index)
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(callback))
        filt = await get_owned_filter(session, user.id, filter_id)
        if filt is None:
            await callback.answer("Фильтр не найден", show_alert=True)
            await show_filter_list(callback)
            return

        if action == "pause":
            filt.is_paused = True
            await session.commit()
            await show_filter_card(callback, filter_id)
            return
        if action == "resume":
            filt.is_paused = False
            filt.consecutive_failures = 0
            filt.last_error = None
            await session.commit()
            await show_filter_card(callback, filter_id)
            return
        if action == "del":
            title = filter_title(filt.url, fallback=filt.label)
            await session.commit()
            await _send(
                callback,
                f"Удалить фильтр <b>#{filter_id}</b> — {title}?\nЭто нельзя отменить.",
                confirm_delete_keyboard(filter_id),
            )
            return
        if action == "delok":
            await session.delete(filt)
            await session.commit()
            await callback.answer("Фильтр удалён")
            await show_filter_list(callback)
            return

    await callback.answer()


async def _refresh_after_watch(callback: CallbackQuery, state: FSMContext, lot_id: str, watched: bool) -> None:
    markup = getattr(callback.message, "reply_markup", None) if callback.message is not None else None
    data = [btn.callback_data or "" for row in (markup.inline_keyboard if markup else []) for btn in row]
    if any(item.startswith("flt:") and (":lots" in item or ":ph:" in item) for item in data):
        browse = await state.get_data()
        filter_id = browse.get("browse_filter_id")
        page = int(browse.get("browse_page") or 0)
        if filter_id:
            await show_filter_lots(callback, state, int(filter_id), page)
            return
    if any(item.startswith("wch:ph:") or item.startswith("wch:p:") for item in data):
        page = int((await state.get_data()).get("watch_page") or 0)
        await show_watch_list(callback, state, page, refresh=False)
        return
    if callback.message is not None and hasattr(callback.message, "edit_reply_markup"):
        try:
            await callback.message.edit_reply_markup(
                reply_markup=lot_watch_keyboard(lot_id, watched)
            )
        except TelegramBadRequest:
            pass


@router.callback_query(F.data.startswith("wch:"))
async def cb_watch_actions(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_access(callback):
        return
    parts = (callback.data or "").split(":", 2)
    action = parts[1] if len(parts) > 1 else "l"
    if action in {"l", "p"}:
        page = 0
        if action == "p" and len(parts) > 2:
            try:
                page = int(parts[2])
            except ValueError:
                page = 0
        await show_watch_list(callback, state, page, refresh=(action == "l"))
        return
    lot_id = parts[2] if len(parts) > 2 else ""
    if not lot_id:
        await callback.answer()
        return
    if action == "ph":
        async with SessionLocal() as session:
            user = await get_or_create_user(session, _chat_id(callback))
            user_id = user.id
            await session.commit()
        try:
            await callback.answer("Отправляю фото…")
        except TelegramBadRequest:
            pass
        await send_watched_lot_photos(callback.bot, _chat_id(callback), user_id, lot_id)
        return
    if action != "t":
        await callback.answer()
        return

    browse = await state.get_data()
    cached = browse.get("browse_lots") if browse.get("browse_filter_id") else None
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(callback))
        currently = await is_watched(session, user.id, lot_id)
        if currently:
            await remove_watch(session, user.id, lot_id)
            await session.commit()
            await callback.answer("Снял отслеживание")
            await _refresh_after_watch(callback, state, lot_id, False)
            return
        lot = await resolve_lot(session, user.id, lot_id, cached)
        if lot is None:
            await session.commit()
        else:
            try:
                await add_watch(session, user.id, lot)
            except WatchLimitError as exc:
                await session.commit()
                await callback.answer(str(exc), show_alert=True)
                return
            await session.commit()
            await callback.answer("Отслеживаю ❤️")
            await _refresh_after_watch(callback, state, lot_id, True)
            return

    try:
        await callback.answer("Ищу лот…")
    except TelegramBadRequest:
        pass
    try:
        fetched = await fetch_lot(lot_id)
    except ParseError:
        fetched = None
    lot = fetched.lot if fetched is not None else None
    if lot is None:
        await callback.bot.send_message(
            _chat_id(callback),
            "Не нашёл этот лот на bid.cars, чтобы отслеживать.",
        )
        return
    remember_lot(lot)
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(callback))
        try:
            await add_watch(session, user.id, lot)
        except WatchLimitError as exc:
            await session.commit()
            await callback.bot.send_message(_chat_id(callback), str(exc))
            return
        await session.commit()
    await _refresh_after_watch(callback, state, lot_id, True)


async def _apply_interval(event: Event, minutes: int) -> None:
    if not await require_access(event):
        return
    if minutes < settings.min_poll_interval_minutes:
        if isinstance(event, CallbackQuery):
            await event.answer(
                f"Минимум {settings.min_poll_interval_minutes} мин",
                show_alert=True,
            )
            return
        await event.answer(
            f"Слишком часто. Минимум {settings.min_poll_interval_minutes} минут."
        )
        return
    if minutes > 24 * 60:
        if isinstance(event, CallbackQuery):
            await event.answer("Максимум сутки", show_alert=True)
            return
        await event.answer("Максимум 1440 минут (сутки).")
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        result = await session.execute(select(Filter).where(Filter.user_id == user.id))
        filters = list(result.scalars().all())
        for filt in filters:
            filt.interval_minutes = minutes
        await session.commit()

    extra = "" if filters else " Когда появятся фильтры, будут проверяться с этим интервалом."
    await _send(
        event,
        f"Интервал проверки: <b>{minutes} мин</b>.{extra}",
        back_to_filters_keyboard(),
    )
