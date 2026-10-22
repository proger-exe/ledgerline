# Ledgerline

<!-- portfolio-card:start -->
[![Ledgerline — Проектные бюджеты, согласования и учёт.](docs/portfolio/card.png)](docs/portfolio/screenshot.png)

Интерфейс с вымышленными данными, без подключения к Telegram и платёжной сети. [Полный размер](docs/portfolio/screenshot.png). [Десктоп](docs/portfolio/screenshot-extra.png).
<!-- portfolio-card:end -->

Telegram-бот + Mini App для казначейства компании: кошельки, платежи с approvals, бюджеты проектов, double-entry ledger, аудит. MVP по `docs/techtask.md` §54, сеть TRON / USDT.

```
Telegram ── bot (уведомления, вход в Mini App)
        └── Mini App (web/, без сборки) ──► API (FastAPI, app/) ──► PostgreSQL
                                              │  watcher: депозиты, подтверждения, алерты
                                              ▼
                                   signer (отдельный контейнер, без портов) ──► TRON
```

| Файл | Что внутри |
|---|---|
| `app/api.py` | все эндпоинты Mini App |
| `app/payments.py` | жизненный цикл платежа: submit → approve → sign → broadcast → confirm |
| `app/policy.py` | policy engine (лимиты, approval matrix), общий для API и signer |
| `app/ledger.py` | единственная точка записи в ledger |
| `app/signer.py` | изолированный signer, единственное место, где есть приватный ключ |
| `app/watcher.py` | фоновый цикл: балансы, депозиты, подтверждения, зависшие платежи |
| `app/blockchain.py` | `BlockchainProvider` + `TronProvider` |
| `app/bot.py`, `app/notify.py` | бот и уведомления |
| `web/` | Mini App: `index.html`, `app.js`, `app.css` |

## Запуск

1. Бот: создать через @BotFather, взять токен.
2. `.env`:
   ```sh
   cp .env.example .env   # BOT_TOKEN, WEBAPP_URL, OWNER_TELEGRAM_ID, пароли БД
   ```
   `OWNER_TELEGRAM_ID`: свой Telegram ID (бот покажет его на /start, если доступа ещё нет).
3. Секреты (ключ **никогда** не кладётся в `.env`):
   ```sh
   mkdir -p secrets
   # ключ hot-кошелька Operations: существующий hex или новый
   uv run python -c "from tronpy.keys import PrivateKey as K; k=K.random(); print(k.hex()); print(k.public_key.to_base58check_address(), file=__import__('sys').stderr)" > secrets/signer_key
   openssl rand -hex 32 > secrets/signer_token
   sudo chown 10001 secrets/* && sudo chmod 400 secrets/*
   ```
4. `docker-compose up -d --build`. API слушает `127.0.0.1:8000`; снаружи нужен HTTPS (Caddy/nginx/tunnel), его адрес и есть `WEBAPP_URL`.
5. Открыть бота → **Open Finance**. Владелец дальше всё делает в приложении:
   - Settings → **Add wallet**: Operations (адрес ключа signer'а) и Treasury (только наблюдение, это cold wallet);
   - Settings → **Users & roles**: добавить CFO, бухгалтера, PM по Telegram ID;
   - Projects → создать проекты, задать бюджеты, распределить деньги из Unallocated через **Transfer**;
   - Settings → **Payment policy**: лимиты и approval matrix.

Сеть: по умолчанию testnet `nile`; для боевого контура `TRON_NETWORK=mainnet` в `.env`.

## Деньги и ledger

- Баланс кошелька (on-chain) и баланс проекта — разные вещи. Проект — это измерение строк ledger'а на счёте кошелька: `wallet:<id>` × `project_id`.
- Депозит → `DEPOSIT` в Unallocated. CFO распределяет его по проектам через Transfer (только ledger, в сеть ничего не уходит).
- Платёж попадает в ledger только на `CONFIRMED`: Dr `expense` [проект, категория] / Cr `wallet` [проект]. До этого сумма считается «in flight» и зарезервирована.
- Ledger, audit log, история статусов и версии policy защищены от изменений триггерами PostgreSQL. Ошибка исправляется reversal-записью (Payment → «Change project or category»).
- Исходящий перевод с нашего кошелька, которому не соответствует ни один платёж, → `UNRECONCILED` + алерт владельцу «Suspicious operation».

## Безопасность

- Авторизация Mini App: подпись Telegram `initData` проверяется на каждом запросе, сессия живёт 12 ч (`SESSION_MAX_AGE`), пользователи только из белого списка.
- Signer: ключ читается из docker secret, публичного порта нет, запросы принимаются только с токеном API. Перед подписью signer сам читает платёж из БД (роль только на чтение) и заново прогоняет policy и approvals. Сверх этого действуют его собственные лимиты `SIGNER_MAX_TX` / `SIGNER_MAX_DAILY`: через приложение их не поменять, даже скомпрометировав API.
- Signer идемпотентен: один платёж = одна подписанная транзакция, повторный запрос возвращает её же.
- Treasury signer не подписывает никогда.

Policy по умолчанию (§7–8), редактируется владельцем в приложении:

| Условие (первое совпавшее) | Кто подтверждает |
|---|---|
| Treasury | 2 из Owner/CFO |
| > 1000 USDT | Owner + CFO |
| новый адрес | CFO |
| > 250 USDT | CFO |
| сверх лимита инициатора (бухгалтер: 250 за платёж / 750 за 24 ч) | CFO |
| больше доступного баланса проекта | CFO |
| остальное | автоматически |

Owner закрывает любой слот. Создатель платежа не может подтвердить свой же платёж. Кнопки Approve в уведомлении нет: сначала открывается экран с деталями.

## Разработка

```sh
uv sync
uv run pytest
DATABASE_URL=postgresql+asyncpg://core:core@localhost:5432/core BOT_TOKEN=... uv run uvicorn app.main:app --reload
```

После правок `web/` поднимайте `?v=` в `web/index.html`: Telegram агрессивно кэширует.

## Сознательно не сделано (MVP)

- Экспорт только CSV (открывается в Excel); XLSX/PDF — когда попросят.
- Платежи из Treasury в приложении заблокированы: это cold wallet, переводы подписываются офлайн, а пополнение Operations watcher сам проводит как `INTERNAL`.
- TOTP, история устройств, rate limiting — пост-MVP (§44). Redis не нужен, пока нет rate limiting.
- Схема создаётся через `create_all`; alembic нужно подключить до первой миграции в проде.
- Комиссия сети (TRX) видна в платеже, но в ledger не проводится.
- Период Custom в аналитике не сделан, только пресеты 7D–1Y.
- Бот, watcher и API работают одним процессом: одна реплика.
