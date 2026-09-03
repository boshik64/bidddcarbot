from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models import Base


def _ensure_sqlite_dir(url: str) -> None:
    if "sqlite" not in url:
        return
    # sqlite+aiosqlite:///./data/foo.db  or  sqlite+aiosqlite:////app/data/foo.db
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    raw_path = url[len(prefix) :]
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"timeout": 30},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.connect() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.commit()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
