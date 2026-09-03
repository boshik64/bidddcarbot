from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.client import client
from app.config import settings
from app.db import init_db
from app.handlers import router
from app.logging_setup import setup_logging
from app.scheduler import poll_filters

logger = logging.getLogger(__name__)


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Приветствие и инструкция"),
            BotCommand(command="add_filter", description="Добавить фильтр bid.cars"),
            BotCommand(command="list_filters", description="Список фильтров"),
            BotCommand(command="remove_filter", description="Удалить фильтр по id"),
            BotCommand(command="pause_filter", description="Пауза фильтра"),
            BotCommand(command="resume_filter", description="Возобновить фильтр"),
            BotCommand(command="status", description="Статистика"),
            BotCommand(command="set_interval", description="Интервал проверки, минуты"),
            BotCommand(command="help", description="Список команд"),
        ]
    )


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Скопируй .env.example в .env и вставь токен от @BotFather.")
    setup_logging()
    await init_db()

    bot = Bot(
        token=settings.bot_token,
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
    scheduler.start()
    logger.info(
        "Scheduler started, default interval %s min, request delay %ss",
        settings.effective_poll_interval,
        settings.request_delay_seconds,
    )

    await _set_commands(bot)

    # Первая проверка не сразу при старте — даём боту подняться.
    async def _kickoff() -> None:
        await asyncio.sleep(15)
        await poll_filters(bot)

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
