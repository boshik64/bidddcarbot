# Telegram-бот: новые лоты bid.cars по твоим фильтрам

Бот принимает ссылку на поиск bid.cars, запоминает текущие лоты и присылает в Telegram только **новые**. Несколько фильтров на человека, несколько пользователей сразу.

## Что умеет

Управление кнопками внизу чата и карточками фильтров:

- **Мои фильтры** — список кнопок `#id · марка · модель · годы`. Нажатие открывает карточку с паузой, удалением и ссылкой на bid.cars
- **Добавить** — бот просит ссылку на поиск (её можно просто вставить в чат)
- **Отслеживаемые** — сердечко на карточке лота или в текущих лотах: пришлю изменение ставки, напомню за 24 и 2 часа до аукциона и отпишу после продажи
- **Статус** — сколько фильтров и лотов
- **Интервал** — выбрать частоту проверки кнопкой

Команды `/start`, `/list_filters`, `/add_filter`, `/watch`, `/status`, `/set_interval`, `/help` по-прежнему работают как ярлыки к тем же экранам.

При добавлении фильтра бот сразу парсит выдачу и **не спамит** уже висящими лотами. Дальше приходят только новые карточки с фото, ставкой, повреждением и ссылкой.

## Как получить ссылку фильтра

1. Открой [bid.cars](https://bid.cars), выставь марку/модель/год/повреждение и т.д.
2. Скопируй URL из адресной строки. Он выглядит так:

```
https://bid.cars/ru/search/results?search-type=filters&status=All&type=Automobile&make=Toyota&model=Camry&year-from=2018&year-to=2026&auction-type=All
```

Ссылка на конкретный лот (`/lot/...`) не подойдёт — нужна именно страница результатов поиска.

## Быстрый старт без Docker

Нужны Python 3.11+ и токен от [@BotFather](https://t.me/BotFather).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# впиши BOT_TOKEN в .env
python -m app
```

Тесты (без сети, на сохранённой JSON-фикстуре):

```bash
pytest -q
```

Разведка живого ответа bid.cars:

```bash
python explore.py "https://bid.cars/en/search/results?search-type=filters&status=All&type=Automobile&make=Toyota&model=Camry&year-from=2018&year-to=2026&auction-type=All"
```

## Деплой в Docker рядом с другими ботами

Этот стек **изолирован** от уже крутящихся контейнеров:

- compose-проект называется `bidcarbot` — `docker compose up` в этой папке не остановит чужие compose-проекты;
- контейнер `bidcarbot`, volume `bidcarbot_data` — имена ни с чем не пересекаются;
- **порты на хост не пробрасываются** (боту они не нужны, Telegram ходит исходящим long poll);
- своя сеть compose, host-network не используется.

На сервере:

```bash
git clone <этот-репозиторий> /opt/bidcarbot
cd /opt/bidcarbot
cp .env.example .env
nano .env   # BOT_TOKEN=...

docker compose up -d --build
docker compose logs -f
```

Проверка, что соседние боты на месте:

```bash
docker ps
```

Должны быть и старые контейнеры, и новый `bidcarbot`. Если вдруг поднял не из этой папки — смотри `docker compose ls`.

Данные (SQLite + логи) живут в volume `bidcarbot_data` и переживают пересборку образа. Бэкап:

```bash
docker compose cp bidcarbot:/app/data/bidcarbot.db ./bidcarbot.db.bak
```

Обновление:

```bash
cd /opt/bidcarbot
git pull
docker compose up -d --build
```

Остановка только этого бота (остальные не трогает):

```bash
cd /opt/bidcarbot
docker compose down        # volume с БД останется
```

## Переменные окружения

См. `.env.example`. Важные:

- `BOT_TOKEN` — токен BotFather
- `POLL_INTERVAL_MINUTES` — дефолтный интервал (по умолчанию 10)
- `MIN_POLL_INTERVAL_MINUTES` — нижняя граница, даже если пользователь просит чаще (5)
- `MAX_FILTERS_PER_USER` — лимит фильтров (10)
- `REQUEST_DELAY_SECONDS` — пауза между запросами к bid.cars (3)
- `PARSER_MAX_PAGES` — сколько страниц выдачи смотреть за раз (50 лотов на страницу)
- `PARSE_FAILURE_THRESHOLD` — после стольких ошибок подряд фильтр уходит на паузу

## Как устроен парсер

Сайт за Cloudflare: обычный curl часто получает 403. Бот ходит в внутренний JSON API
`https://bid.cars/app/search/request` с TLS impersonation Chrome (`curl_cffi`).
Перед API делается короткий заход на страницу фильтра, чтобы получить session-cookie.
HTML через BeautifulSoup не нужен — карточки лотов приходят в JSON.
Успешный JSON иногда приходит с заголовком `Content-Type: text/html`, клиент смотрит на тело ответа.

Запросы идут **последовательно**, с паузой 3 секунды. Если парсинг фильтра падает, цикл остальных фильтров не рвётся; после 5 ошибок подряд пользователь получает уведомление, фильтр ставится на паузу.

`robots.txt` запрещает индексировать `/app` и выдачу `/search/results?*`. Бот рассчитан на личный мониторинг своих фильтров, не на коммерческую перепродажу данных.

## Структура

```
app/
  main.py          запуск бота + планировщик
  handlers.py      команды Telegram
  scheduler.py     опрос фильтров (APScheduler)
  client.py        HTTP к bid.cars
  parser.py        URL + JSON → LotData
  models.py        users / filters / seen_lots
  db.py            SQLite + SQLAlchemy async
config: .env  →  app/config.py
```
# bidddcarbot
# bidddcarbot
