from __future__ import annotations

import logging
from datetime import datetime
from typing import Union
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client import ParseError, parse_filter
from app.config import settings
from app.db import SessionLocal
from app.formatting import HELP_TEXT, START_TEXT, filter_card_text
from app.keyboards import (
    BTN_ADD,
    BTN_FILTERS,
    BTN_INTERVAL,
    BTN_STATUS,
    MENU_BUTTON_TEXTS,
    add_filter_keyboard,
    back_to_filters_keyboard,
    confirm_delete_keyboard,
    empty_filters_keyboard,
    filter_card_keyboard,
    filters_keyboard,
    interval_keyboard,
    main_reply_keyboard,
)
from app.models import Filter, SeenLot, User, as_utc, utcnow
from app.parser import FilterUrlError, LotData, filter_title, label_from_url, validate_filter_url

logger = logging.getLogger(__name__)
router = Router()

Event = Union[Message, CallbackQuery]


class AddFilterStates(StatesGroup):
    waiting_url = State()


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
        await event.answer()
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


async def show_filter_list(event: Event) -> None:
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
            last_error=filt.last_error,
            label=filt.label,
        )
        markup = filter_card_keyboard(filt)
        await session.commit()
    await _send(event, text, markup, edit=edit)


async def show_status(event: Event) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(session, _chat_id(event))
        result = await session.execute(
            select(Filter).where(Filter.user_id == user.id).order_by(Filter.id)
        )
        filters = list(result.scalars().all())
        ids = [f.id for f in filters]
        lots_count = 0
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

    active = sum(1 for f in filters if not f.is_paused)
    last_check = max((f.last_checked_at for f in filters if f.last_checked_at), default=None)
    text = (
        f"Фильтров: {len(filters)} (активных {active})\n"
        f"Последняя проверка: {_fmt_dt(last_check)}\n"
        f"Всего запомненных лотов: {lots_count}\n"
        f"Интервал: {settings.effective_poll_interval} мин "
        f"(минимум {settings.min_poll_interval_minutes})."
    )
    await _send(event, text, back_to_filters_keyboard())


async def show_interval(event: Event) -> None:
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
    await state.set_state(AddFilterStates.waiting_url)
    await _send(
        event,
        "Пришли ссылку на страницу поиска bid.cars "
        "(из адресной строки после фильтра).",
        add_filter_keyboard(),
        edit=isinstance(event, CallbackQuery),
    )


async def add_filter_from_url(message: Message, url: str) -> None:
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
            lots = await parse_filter(canonical)
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
        await session.commit()
        await session.refresh(filt)

        text = (
            f"Фильтр <b>#{filt.id}</b> добавлен: {filter_title(canonical)}\n"
            f"Сейчас по нему {len(lots)} лотов (на первых страницах). "
            "Их я запомнил и спамить не буду — пришлю только новые."
        )
        card = filter_card_text(
            filt.id,
            filt.url,
            is_paused=False,
            last_checked=_fmt_dt(filt.last_checked_at),
            interval_minutes=_interval_of(filt),
            lots_count=len(lots),
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
        await get_or_create_user(session, message.chat.id)
        await session.commit()
    await message.answer(START_TEXT, reply_markup=main_reply_keyboard())


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


@router.message(Command("remove_filter"))
@router.message(Command("pause_filter"))
@router.message(Command("resume_filter"))
async def cmd_legacy_manage(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Выбери фильтр кнопкой — дальше пауза и удаление внутри карточки.")
    await show_filter_list(message)


@router.message(AddFilterStates.waiting_url, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTON_TEXTS))
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


@router.callback_query(F.data == "nav:status")
async def cb_status(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_status(callback)


@router.callback_query(F.data == "nav:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_filter_list(callback)


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


async def _apply_interval(event: Event, minutes: int) -> None:
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
