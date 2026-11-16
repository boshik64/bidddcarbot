from __future__ import annotations

import asyncio
import logging
import socket

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.client import client
from app.config import settings
from app.db import init_db
from app.handlers import router
from app.logging_setup import setup_logging
from app.scheduler import poll_filters
from app.watch import poll_watched_lots

logger = logging.getLogger(__name__)


class IPv4AiohttpSession(AiohttpSession):
    """На Timeweb DNS/Happy Eyeballs часто сначала лезут в мёртвый IPv6."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._connector_init["family"] = socket.AF_INET


async def _set_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Меню и кнопки"),
        BotCommand(command="list_filters", description="Мои фильтры"),
        BotCommand(command="add_filter", description="Добавить фильтр"),
        BotCommand(command="watch", description="Отслеживаемые лоты"),
        BotCommand(command="status", description="Статистика"),
        BotCommand(command="help", description="Справка"),
    ]
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            await bot.set_my_commands(commands)
            return
        except TelegramNetworkError as exc:
            last_error = exc
            wait_for = min(2 ** attempt, 15)
            logger.warning(
                "set_my_commands failed (attempt %s/5), retry in %ss: %s",
                attempt,
                wait_for,
                exc,
            )
            await asyncio.sleep(wait_for)
    logger.error("Could not set bot commands, continue polling: %s", last_error)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Скопируй .env.example в .env и вставь токен от @BotFather.")
    setup_logging()
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        session=IPv4AiohttpSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_filters,
        "interval",
        minutes=1,
        args=[bot],
        id="poll_filters",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        poll_watched_lots,
        "interval",
        minutes=1,
        args=[bot],
        id="poll_watched_lots",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    logger.info(
        "Scheduler started, default interval %s min, request delay %ss",
        settings.poll_interval_minutes,
        settings.request_delay_seconds,
    )

    await _set_commands(bot)

    # Первая проверка не сразу при старте — даём боту подняться.
    async def _kickoff() -> None:
        await asyncio.sleep(15)
        await poll_filters(bot)
        await poll_watched_lots(bot)

    kickoff = asyncio.create_task(_kickoff())

    try:
        logger.info("Bot polling started")
        await dp.start_polling(bot)
    finally:
        kickoff.cancel()
        scheduler.shutdown(wait=False)
        await client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
