from __future__ import annotations

from html import escape

from app.parser import LotData


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
    "Команды:\n"
    "/start — приветствие\n"
    "/add_filter &lt;ссылка&gt; — добавить фильтр bid.cars\n"
    "/list_filters — список твоих фильтров\n"
    "/remove_filter &lt;id&gt; — удалить фильтр\n"
    "/pause_filter &lt;id&gt; — поставить на паузу\n"
    "/resume_filter &lt;id&gt; — возобновить\n"
    "/status — статистика\n"
    "/set_interval &lt;минуты&gt; — частота проверки\n"
    "/help — эта справка\n\n"
    "Как получить ссылку: открой bid.cars, настрой фильтр "
    "(марка/модель/год/повреждение) и скопируй URL из адресной строки."
)

START_TEXT = (
    "Привет! Я слежу за новыми лотами на bid.cars.\n\n"
    "1. Открой нужный поиск на сайте и скопируй ссылку.\n"
    "2. Пришли её командой /add_filter или просто вставь URL.\n"
    "3. Я запомню текущие лоты и буду присылать только новые.\n\n"
    + HELP_TEXT
)
