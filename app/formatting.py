from __future__ import annotations

from html import escape

from app.parser import LotData, filter_title


def lot_caption(lot: LotData) -> str:
    lines = [f"🚗 <b>{escape(lot.title)}</b>"]
    if lot.current_bid:
        lines.append(f"💰 Текущая ставка: {escape(lot.current_bid)}")
    if lot.damage:
        lines.append(f"🔧 Повреждение: {escape(lot.damage)}")
    if lot.status:
        lines.append(f"🏁 Статус: {escape(lot.status)}")
    if lot.location:
        lines.append(f"📍 Локация: {escape(lot.location)}")
    if lot.vin:
        lines.append(f"🔢 VIN: <code>{escape(lot.vin)}</code>")
    lines.append(f'🔗 <a href="{escape(lot.url, quote=True)}">Открыть лот</a>')
    return "\n".join(lines)


HELP_TEXT = (
    "Я слежу за новыми лотами на bid.cars по твоим фильтрам.\n\n"
    "Управление — кнопками внизу экрана и карточками фильтров:\n"
    "📋 <b>Мои фильтры</b> — список, внутри пауза и удаление\n"
    "➕ <b>Добавить</b> — прислать ссылку на поиск\n"
    "📊 <b>Статус</b> — сколько фильтров и лотов\n"
    "⏱ <b>Интервал</b> — как часто проверять\n\n"
    "Ссылку на фильтр можно просто вставить в чат, без команды.\n"
    "Как получить: bid.cars → настрой поиск → скопируй URL из адресной строки."
)

START_TEXT = (
    "Привет! Я слежу за новыми лотами на bid.cars.\n\n"
    "1. Открой нужный поиск на сайте и скопируй ссылку.\n"
    "2. Нажми <b>Добавить</b> или просто вставь URL.\n"
    "3. Я запомню текущие лоты и буду присылать только новые.\n\n"
    + HELP_TEXT
)


def filter_card_text(
    filter_id: int,
    url: str,
    *,
    is_paused: bool,
    last_checked: str,
    interval_minutes: int,
    lots_count: int,
    last_error: str | None = None,
    label: str | None = None,
) -> str:
    title = filter_title(url, fallback=label)
    status = "⏸ на паузе" if is_paused else "✅ активен"
    lines = [
        f"Фильтр <b>#{filter_id}</b>",
        f"🚗 {escape(title)}",
        f"📌 {status}",
        f"🕒 Последняя проверка: {escape(last_checked)}",
        f"⏱ Интервал: {interval_minutes} мин",
        f"📦 Лотов в памяти: {lots_count}",
        f'🔗 <a href="{escape(url, quote=True)}">Страница фильтра</a>',
    ]
    if last_error:
        lines.append(f"⚠️ Последняя ошибка: {escape(last_error)}")
    return "\n".join(lines)
