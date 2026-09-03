from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client import ParseError, parse_filter
from app.config import settings
from app.db import SessionLocal
from app.formatting import HELP_TEXT, START_TEXT, lot_caption
from app.models import Filter, SeenLot, User, as_utc, utcnow
from app.parser import FilterUrlError, LotData, label_from_url, validate_filter_url

logger = logging.getLogger(__name__)
router = Router()


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


def _require_id(command: CommandObject) -> int | None:
    if not command.args:
        return None
    try:
        return int(command.args.strip().split()[0])
    except (TypeError, ValueError):
        return None


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


async def add_filter_from_url(message: Message, url: str) -> None:
    try:
        canonical = validate_filter_url(url)
    except FilterUrlError as exc:
        await message.answer(str(exc))
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        count = await user_filter_count(session, user.id)
        if count >= settings.max_filters_per_user:
            await message.answer(
                f"Лимит фильтров: {settings.max_filters_per_user}. "
                "Удали один через /remove_filter, потом добавляй новый."
            )
            return

        duplicate = await session.execute(
            select(Filter).where(Filter.user_id == user.id, Filter.url == canonical)
        )
        if duplicate.scalar_one_or_none() is not None:
            await message.answer("Этот фильтр у тебя уже добавлен. Список: /list_filters")
            return

        wait_msg = await message.answer("Добавляю фильтр, сейчас сниму текущие лоты…")
        try:
            lots = await parse_filter(canonical)
        except ParseError as exc:
            logger.warning("Initial parse failed for %s: %s", canonical, exc)
            await wait_msg.edit_text(
                f"Не смог прочитать фильтр: {exc}\n"
                "Проверь ссылку и попробуй ещё раз чуть позже."
            )
            return
        except FilterUrlError as exc:
            await wait_msg.edit_text(str(exc))
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

        await wait_msg.edit_text(
            f"Фильтр #{filt.id} добавлен: <b>{filt.label}</b>\n"
            f"Сейчас по нему {len(lots)} лотов (на первых страницах). "
            "Их я запомнил и спамить не буду — пришлю только новые.\n\n"
            f"Проверка примерно раз в {filt.interval_minutes} мин. "
            "Список: /list_filters",
        )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with SessionLocal() as session:
        await get_or_create_user(session, message.chat.id)
        await session.commit()
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил.")


@router.message(Command("add_filter"))
async def cmd_add_filter(
    message: Message, command: CommandObject, state: FSMContext
) -> None:
    url = (command.args or "").strip()
    if url:
        await state.clear()
        await add_filter_from_url(message, url)
        return
    await state.set_state(AddFilterStates.waiting_url)
    await message.answer(
        "Пришли ссылку на страницу поиска bid.cars (из адресной строки после фильтра)."
    )


@router.message(AddFilterStates.waiting_url, F.text, ~F.text.startswith("/"))
async def got_filter_url(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await state.clear()
    await add_filter_from_url(message, text)


@router.message(Command("list_filters"))
async def cmd_list_filters(message: Message) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        result = await session.execute(
            select(Filter).where(Filter.user_id == user.id).order_by(Filter.id)
        )
        filters = result.scalars().all()
        await session.commit()

    if not filters:
        await message.answer("Фильтров пока нет. Добавь через /add_filter")
        return

    lines = ["Твои фильтры:"]
    for filt in filters:
        status = "⏸ пауза" if filt.is_paused else "✅ активен"
        lines.append(
            f"\n#{filt.id} {filt.label or 'фильтр'} — {status}\n"
            f"Проверка: {_fmt_dt(filt.last_checked_at)}\n"
            f"{filt.url}"
        )
    lines.append("\nУдалить: /remove_filter id   Пауза: /pause_filter id")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("remove_filter"))
async def cmd_remove_filter(message: Message, command: CommandObject) -> None:
    filter_id = _require_id(command)
    if filter_id is None:
        await message.answer("Укажи номер фильтра, например /remove_filter 3")
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        filt = await get_owned_filter(session, user.id, filter_id)
        if filt is None:
            await message.answer("Такого фильтра нет. Смотри /list_filters")
            return
        await session.delete(filt)
        await session.commit()
    await message.answer(f"Фильтр #{filter_id} удалён.")


@router.message(Command("pause_filter"))
async def cmd_pause_filter(message: Message, command: CommandObject) -> None:
    filter_id = _require_id(command)
    if filter_id is None:
        await message.answer("Укажи номер, например /pause_filter 3")
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        filt = await get_owned_filter(session, user.id, filter_id)
        if filt is None:
            await message.answer("Такого фильтра нет. Смотри /list_filters")
            return
        filt.is_paused = True
        await session.commit()
    await message.answer(f"Фильтр #{filter_id} на паузе. Вернуть: /resume_filter {filter_id}")


@router.message(Command("resume_filter"))
async def cmd_resume_filter(message: Message, command: CommandObject) -> None:
    filter_id = _require_id(command)
    if filter_id is None:
        await message.answer("Укажи номер, например /resume_filter 3")
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        filt = await get_owned_filter(session, user.id, filter_id)
        if filt is None:
            await message.answer("Такого фильтра нет. Смотри /list_filters")
            return
        filt.is_paused = False
        filt.consecutive_failures = 0
        filt.last_error = None
        await session.commit()
    await message.answer(f"Фильтр #{filter_id} снова активен.")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        result = await session.execute(
            select(Filter).where(Filter.user_id == user.id).order_by(Filter.id)
        )
        filters = result.scalars().all()
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
    await message.answer(
        f"Фильтров: {len(filters)} (активных {active})\n"
        f"Последняя проверка: {_fmt_dt(last_check)}\n"
        f"Всего запомненных лотов: {lots_count}\n"
        f"Интервал по умолчанию: {settings.effective_poll_interval} мин."
    )


@router.message(Command("set_interval"))
async def cmd_set_interval(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer(
            f"Укажи минуты, например /set_interval 15\n"
            f"Минимум: {settings.min_poll_interval_minutes} мин."
        )
        return
    try:
        minutes = int(command.args.strip().split()[0])
    except ValueError:
        await message.answer("Нужно целое число минут.")
        return
    if minutes < settings.min_poll_interval_minutes:
        await message.answer(
            f"Слишком часто. Минимум {settings.min_poll_interval_minutes} минут, "
            "чтобы не долбить сайт."
        )
        return
    if minutes > 24 * 60:
        await message.answer("Максимум 1440 минут (сутки).")
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(session, message.chat.id)
        result = await session.execute(select(Filter).where(Filter.user_id == user.id))
        filters = result.scalars().all()
        for filt in filters:
            filt.interval_minutes = minutes
        await session.commit()

    if not filters:
        await message.answer(
            f"Запомнил {minutes} мин. Когда добавишь фильтры, они будут проверяться с этим интервалом."
        )
        return
    await message.answer(f"Интервал проверки твоих фильтров: {minutes} мин.")


@router.message(StateFilter(None), F.text.regexp(r"https?://(?:www\.)?bid\.cars/", flags=0))
async def pasted_url(message: Message) -> None:
    await add_filter_from_url(message, message.text or "")
