from __future__ import annotations

from html import escape

from app.parser import LotData, filter_title, format_mileage


def lot_caption(lot: LotData) -> str:
    lines = [f"🚗 <b>{escape(lot.title)}</b>"]
    if lot.current_bid:
        lines.append(f"💰 Текущая ставка: {escape(lot.current_bid)}")
    mileage = format_mileage(lot.odometer_miles, lot.odometer_km)
    if mileage:
        lines.append(f"📏 Пробег: {escape(mileage)}")
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
    "Доступ по подписке USDT TRC20: 5 USDT / месяц или 50 USDT / год.\n\n"
    "Управление — кнопками внизу экрана и карточками фильтров:\n"
    "📋 <b>Мои фильтры</b> — список, внутри текущие лоты, пауза и удаление\n"
    "➕ <b>Добавить</b> — прислать ссылку на поиск\n"
    "📊 <b>Статус</b> — сколько фильтров и лотов\n"
    "⏱ <b>Интервал</b> — как часто проверять\n"
    "💎 <b>Подписка</b> — оплата USDT TRC20\n\n"
    "Ссылку на фильтр можно просто вставить в чат, без команды.\n"
    "Как получить: bid.cars → настрой поиск → скопируй URL из адресной строки."
)

START_TEXT = (
    "Привет! Я слежу за новыми лотами на bid.cars.\n\n"
    "Чтобы пользоваться ботом, нужна подписка:\n"
    "• 1 месяц — <b>5 USDT TRC20</b>\n"
    "• 1 год — <b>50 USDT TRC20</b>\n\n"
    "Нажми <b>Подписка</b>, оплати и пришли TxID. "
    "После этого добавляй фильтры кнопками.\n\n"
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
    active_lots: int | None = None,
    last_error: str | None = None,
    label: str | None = None,
) -> str:
    title = filter_title(url, fallback=label)
    status = "⏸ на паузе" if is_paused else "✅ активен"
    active = "ещё нет" if active_lots is None else str(active_lots)
    lines = [
        f"Фильтр <b>#{filter_id}</b>",
        f"🚗 {escape(title)}",
        f"📌 {status}",
        f"🕒 Последняя проверка: {escape(last_checked)}",
        f"⏱ Интервал: {interval_minutes} мин",
        f"📦 Лотов в памяти: {lots_count}",
        f"🚗 Активных лотов: {active}",
        f'🔗 <a href="{escape(url, quote=True)}">Страница фильтра</a>',
    ]
    if last_error:
        lines.append(f"⚠️ Последняя ошибка: {escape(last_error)}")
    return "\n".join(lines)


def _clip(text: str, limit: int = 80) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def lots_page_text(
    filter_id: int,
    title: str,
    lots: list[LotData],
    *,
    page: int,
    page_size: int = 10,
) -> str:
    total = len(lots)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = max(0, min(page, pages - 1))
    header = (
        f"Текущие лоты фильтра <b>#{filter_id}</b> — {escape(title)}\n"
        f"Страница {page + 1} из {pages} · всего {total}\n"
        "Кнопки 📷 — пролистать фото авто в Telegram."
    )
    if not total:
        return header + "\n\nСейчас по фильтру нет лотов."

    start = page * page_size
    chunk = lots[start : start + page_size]
    blocks: list[str] = []
    for offset, lot in enumerate(chunk):
        idx = start + offset + 1
        name = escape(_clip(lot.title or "Лот"))
        url = escape(lot.url or "", quote=True)
        line = f'{idx}. <a href="{url}">{name}</a>'
        bits: list[str] = []
        if lot.current_bid:
            bits.append(f"💰 {escape(lot.current_bid)}")
        mileage = format_mileage(lot.odometer_miles, lot.odometer_km)
        if mileage:
            bits.append(f"📏 {escape(mileage)}")
        if lot.location:
            bits.append(f"📍 {escape(_clip(lot.location, 40))}")
        if lot.status:
            bits.append(f"🏁 {escape(lot.status)}")
        if bits:
            line += "\n" + " · ".join(bits)
        blocks.append(line)
    return header + "\n\n" + "\n\n".join(blocks)
