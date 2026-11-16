# CONTEXT — bidcarbot (handoff)

Дата: 2026-11-16. Compose: IPv4-пины `api.telegram.org` + `bid.cars` в `extra_hosts`.

## Что это

Telegram-бот на **aiogram 3.22** / Python 3.12 (Docker), SQLite.
Источники лотов/фильтров: **bid.cars**, **Copart**, **IAAI** (Insurance Auto Auctions).
Репозиторий: `https://github.com/boshik64/bidddcarbot.git`
Прод: `/var/www/bidddcarbot` на Timeweb VPS, бот `@bidddcar_bot`.
Локально: `/Users/vladislavisenko/Projects/bidcarbot`, venv часто Python 3.9 — не поднимать aiogram выше 3.22 без апгрейда Python; в типах избегать `X | None` в pydantic-моделях на 3.9 (уже `Optional`).

Не коммитить и не деплоить без явной просьбы пользователя. UI и сообщения — на русском.

## Деплой на сервере

Старый **docker-compose v1** — ключ `name:` в корне compose **ломает** файл.
**`docker restart` недостаточно** после смены `extra_hosts` / DNS — нужен `docker rm` + `up`.

Всегда:

```bash
cd /var/www/bidddcarbot
git checkout -- docker-compose.yml   # если локально правили
git pull
docker rm -f bidcarbot               # иначе Conflict имени контейнера
docker-compose -p bidcarbot up -d --build
docker logs --tail 40 bidcarbot
docker exec bidcarbot getent hosts api.telegram.org
docker exec bidcarbot getent hosts bid.cars
docker exec bidcarbot cat /etc/hosts
```

Ожидание после деплоя:
- в `/etc/hosts` контейнера есть `149.154.166.110 api.telegram.org` и `104.18.27.43 bid.cars`;
- `getent` показывает **IPv4**, не только `2001:` / `2606:`;
- в логах `Bot polling started` **без** `ClientConnectorDNSError` / `Temporary failure in name resolution`.

Данные в volume `bidcarbot_data`. Порты наружу не пробрасываются.

На сервере часто остаются **локальные правки** `docker-compose.yml` → `git pull` abort. Лечится `git checkout -- docker-compose.yml`.

## Сеть / Timeweb (критично)

После смены IP сервера сеть/DNS плавали.

- Исходящий IP: **`64.188.60.81`** (на eth0 ещё висит `64.188.60.126/32` из `60-extra-ip.yaml` — уточнять в панели Timeweb).
- DNS хоста через `systemd-resolved` stub `127.0.0.53`; uplink часто **Timeweb** `85.193.93.193` / `85.193.93.194`.
- **`8.8.8.8:53` с VPS часто timeout** — не ставить только Google DNS.
- **IPv6 в контейнере опасен:** DNS/`getent hosts` часто показывают AAAA даже когда в `/etc/hosts` уже есть IPv4-пин → aiohttp сначала лезет в мёртвый IPv6 → `ClientConnectorDNSError` / `Request timeout`. Scheduler при этом жив. Пользователь в РФ ни при чём — рвётся путь **контейнер → api.telegram.org**.
- Симптомы: бот «молчит» 10+ мин; `TelegramNetworkError: Request timeout`; `Temporary failure in name resolution`; `getent hosts` → только `2001:`/`2606:`, но `cat /etc/hosts` уже с IPv4.
- С хоста `curl -I https://api.telegram.org` может быть **ок** (IPv4), а внутри контейнера — нет. Сверять `cat /etc/hosts` + логи, не только `getent`.
- С хоста `curl https://bid.cars` часто **403 Cloudflare challenge**; бот ходит через **`curl_cffi` impersonate=chrome** — нормальный ответ ~`200` и HTML.
- В compose: DNS Timeweb + `1.1.1.1`, **`extra_hosts`** (IPv4), **`sysctls` disable IPv6**:
  - `api.telegram.org` → `149.154.166.110` (обновить: `dig +short api.telegram.org A`)
  - `bid.cars` / `www.bid.cars` → `104.18.27.43` (обновить: `dig +short bid.cars A`)
  - `www.copart.com` → Imperva IPv4 (сейчас `45.60.13.242`; `dig +short www.copart.com A`)
  - `www.iaai.com` / `vis.iaai.com` → Fastly IPv4 (сейчас `151.101.194.129`)
  - `cs.copart.com` → Cloudflare IPv4 для фото
- В `app/main.py` — `IPv4AiohttpSession` (`family=AF_INET`) + ретраи `set_my_commands` при `TelegramNetworkError`.
- В `app/client.py` — до **3 ретраев** на DNS/timeout при прогреве и GET.

Временный костыль для git, если DNS мёртв:

```bash
echo '140.82.121.3 github.com' >> /etc/hosts   # IP меняется
resolvectl dns eth0 85.193.93.193 85.193.93.194
resolvectl flush-caches
```

На сервере крутятся и другие контейнеры (nginx, laravel/mirror_bot, mysql, mtproxy) — **не трогать** чужие compose.

## Функционал (актуальный)

### Фильтры
- Ссылка на поиск **bid.cars / Copart / IAAI** → снимок лотов → только **новые** карточки.
- Copart: `lotSearchResults?query=...` → POST `/public/lots/search-results`.
- IAAI: `Search?Keyword=...` или `Vehiclelisting/...` → HTML-выдача (id/title/дата).
- Интервал проверки **фиксирован 10 мин** (`POLL_INTERVAL_MINUTES`). Выбор интервала **убран** (кнопка/команда показывают заглушку).
- Планировщик jobs каждую 1 мин, due по `poll_interval_minutes`.

### Отслеживаемые (❤️)
- Подписка с карточки / текущих лотов / **вставкой URL лота** в чат (`sources.parse_lot_url` → `watch_from_lot_url`).
- External id: bid.cars как есть (`0-…`/`1-…`); Copart `c-{lot}`; IAAI `i-{itemId}`.
- Copart лот: GET `/public/data/lotdetails/solr/{lot}`.
- IAAI лот: HTML `VehicleDetail/{id}~US` → embedded `application/json` (`inventoryView`).
- Список открывается **сразу из SQLite** (без сети). Свежие данные — опрос раз в 10 мин или **🔄 Обновить**.
- После продажи лот **не снимается**; если снова выставят (тот же ID или новый по **VIN** на bid.cars) — «🔁 Лот снова на аукционе».
- Напоминания **24ч и 2ч — по одному разу** (`reminded_24h` / `reminded_2h`).
- В «Текущие лоты» для watched: `sync_watch_from_listing` + `apply_watch_snapshot`.

### Ключевые файлы
| Файл | Зачем |
|------|--------|
| `app/main.py` | polling, scheduler, soft set_my_commands |
| `app/handlers.py` | UI, `watch_from_lot_url`, browse/watch list |
| `app/watch.py` | poll, relist, reminders, refresh |
| `app/client.py` | `AuctionClient`: маршрутизация bid.cars/Copart/IAAI |
| `app/sources.py` | detect/validate/parse URL всех источников |
| `app/copart.py` | Copart API lot/search → `LotData` |
| `app/iaai.py` | IAAI HTML lot/listing → `LotData` |
| `app/parser.py` | bid.cars search JSON + `parse_lot_html` |
| `app/models.py` | User, Filter, SeenLot, WatchedLot |
| `app/formatting.py` | тексты, бейдж источника на карточке |
| `app/keyboards.py` | reply/inline; BTN_INTERVAL устарела |
| `docker-compose.yml` | v3.8, DNS Timeweb, extra_hosts (Telegram + bid.cars + Copart/IAAI) |
| `tests/` | parser/watch/formatting/sources; `pytest -q` |

### Callback / кнопки
- `wch:t:{lot_id}` toggle, `wch:ph:`, `wch:p:`, `wch:l` refresh
- `nav:watch`, фильтры `flt:`, оплата `pay:` / `payok:`
- Reply: Мои фильтры / Отслеживаемые / Добавить / Статус / Подписка

### Лимиты (config / .env)
- `POLL_INTERVAL_MINUTES=10`
- `MAX_WATCHED_PER_USER=30`, `MAX_FILTERS_PER_USER=10`
- `REQUEST_DELAY_SECONDS=3`, `PARSER_MAX_PAGES=2`

## Что было сломано / уже починено в коде
1. Медленный список отслеживаемых — больше не парсит активный каталог на каждый open.
2. Спам «через 24 часа» — флаги reminded; не сбрасывать при том же auction_at.
3. Интервал 5 мин не применялся / убран — всегда 10.
4. Подписка по ссылке на лот + релист + sync инфы между разделами — в `d0aec45`+.
5. Compose/DNS под Timeweb — `5372961`.
6. Бот молчит при живом scheduler: DNS предпочитает мёртвый IPv6 → IPv4-пины + `sysctls` disable IPv6 + `IPv4AiohttpSession`.

## Открытые риски / хвосты
- После `git pull` с новым `extra_hosts` обязателен **`docker rm -f` + `up -d`**, не `restart`. Локальные правки compose на сервере снова могут блокировать pull.
- `extra_hosts` IP Telegram / Cloudflare могут устареть — проверить `dig +short … A` и обновить compose.
- DNS на VPS нестабилен + IPv6-only ответы → сбои Telegram даже когда scheduler/bid.cars ок; это инфра.
- Cloudflare challenge на голый curl с ДЦ IP — ожидаемо; смотреть `curl_cffi`.
- Два IP на eth0 (`.81` и `.126`) — возможная путаница после смены IP.
- Poll filters в логах иногда `0 active` / `0 due` при живом боте — не путать с «бот мёртв»; смотреть `TelegramNetworkError` / DNS.

## Как продолжать в новом чате
1. Прочитать этот файл и `README.md`.
2. Не ломать изоляцию compose от соседних ботов.
3. Деплой только по просьбе; типичная команда — блок выше с `docker rm -f` + `docker-compose -p bidcarbot`.
4. Тесты: `.venv/bin/python -m pytest -q` (локально).
5. Язык ответов пользователю — русский, коротко.
