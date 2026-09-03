from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.models import User, as_utc, utcnow

PLAN_MONTH = "month"
PLAN_YEAR = "year"
USDT_QUANT = Decimal("0.000001")

PLAN_DAYS = {
    PLAN_MONTH: 30,
    PLAN_YEAR: 365,
}


def plan_amount(plan: str) -> Decimal:
    from app.config import settings

    raw = settings.sub_month_usdt if plan == PLAN_MONTH else settings.sub_year_usdt
    return Decimal(str(raw)).quantize(USDT_QUANT)


def format_usdt(amount: Decimal) -> str:
    return format(amount.quantize(USDT_QUANT).normalize(), "f")


def plan_title(plan: str) -> str:
    return "1 месяц" if plan == PLAN_MONTH else "1 год"


def has_access(user: User) -> bool:
    if user.is_lifetime:
        return True
    until = as_utc(user.subscribed_until)
    return until is not None and until > utcnow()


def access_label(user: User, tz_fmt) -> str:
    if user.is_lifetime:
        return "бессрочный доступ"
    until = as_utc(user.subscribed_until)
    if until is None or until <= utcnow():
        return "нет активной подписки"
    return f"до {tz_fmt(until)}"


def grant_plan(user: User, plan: str, now: datetime | None = None) -> datetime:
    now = now or utcnow()
    days = PLAN_DAYS[plan]
    current = as_utc(user.subscribed_until)
    start = current if current and current > now else now
    until = start + timedelta(days=days)
    user.subscribed_until = until
    return until


def grant_lifetime(user: User) -> None:
    user.is_lifetime = True
