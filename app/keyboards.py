from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.models import Filter
from app.parser import button_label

BTN_FILTERS = "📋 Мои фильтры"
BTN_ADD = "➕ Добавить"
BTN_STATUS = "📊 Статус"
BTN_INTERVAL = "⏱ Интервал"
BTN_SUB = "💎 Подписка"

MENU_BUTTON_TEXTS = {BTN_FILTERS, BTN_ADD, BTN_STATUS, BTN_INTERVAL, BTN_SUB}

INTERVAL_PRESETS = (5, 10, 15, 30, 60, 120)
LOTS_PAGE_SIZE = 10


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FILTERS), KeyboardButton(text=BTN_ADD)],
            [KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_INTERVAL)],
            [KeyboardButton(text=BTN_SUB)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def filters_keyboard(filters: list[Filter]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for filt in filters:
        rows.append(
            [
                InlineKeyboardButton(
                    text=button_label(filt.id, filt.url, fallback=filt.label),
                    callback_data=f"flt:{filt.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="nav:add"),
            InlineKeyboardButton(text="📊 Статус", callback_data="nav:status"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def empty_filters_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить фильтр", callback_data="nav:add")]
        ]
    )


def filter_card_keyboard(filt: Filter) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"flt:{filt.id}:resume")
        if filt.is_paused
        else InlineKeyboardButton(text="⏸ Пауза", callback_data=f"flt:{filt.id}:pause")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Текущие лоты", callback_data=f"flt:{filt.id}:lots")],
            [toggle],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"flt:{filt.id}:del")],
            [InlineKeyboardButton(text="🔗 Открыть на bid.cars", url=filt.url)],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="nav:filters")],
        ]
    )


def confirm_delete_keyboard(filter_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"flt:{filter_id}:delok"
                ),
                InlineKeyboardButton(text="Отмена", callback_data=f"flt:{filter_id}"),
            ]
        ]
    )


def interval_keyboard(current: int, min_minutes: int) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    for minutes in INTERVAL_PRESETS:
        if minutes < min_minutes:
            continue
        mark = " · сейчас" if minutes == current else ""
        buttons.append(
            InlineKeyboardButton(
                text=f"{minutes} мин{mark}",
                callback_data=f"int:{minutes}",
            )
        )
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 3):
        rows.append(buttons[i : i + 3])
    rows.append([InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="nav:filters")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="nav:cancel")]
        ]
    )


def back_to_filters_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="nav:filters")]
        ]
    )


def paywall_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 месяц · 5 USDT", callback_data="pay:month"),
                InlineKeyboardButton(text="1 год · 50 USDT", callback_data="pay:year"),
            ]
        ]
    )


def lots_page_keyboard(filter_id: int, page: int, total: int) -> InlineKeyboardMarkup:
    pages = max(1, (total + LOTS_PAGE_SIZE - 1) // LOTS_PAGE_SIZE) if total else 1
    nav: list[InlineKeyboardButton] = []
    if total and page > 0:
        nav.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"flt:{filter_id}:lots:{page - 1}")
        )
    if total and page + 1 < pages:
        nav.append(
            InlineKeyboardButton(text="➡️", callback_data=f"flt:{filter_id}:lots:{page + 1}")
        )
    rows: list[list[InlineKeyboardButton]] = []
    if nav:
        rows.append(nav)
    rows.append(
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"flt:{filter_id}:lots")]
    )
    rows.append(
        [InlineKeyboardButton(text="⬅️ К карточке", callback_data=f"flt:{filter_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_keyboard(plan: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"payok:{plan}")],
            [InlineKeyboardButton(text="⬅️ Тарифы", callback_data="nav:pay")],
        ]
    )
