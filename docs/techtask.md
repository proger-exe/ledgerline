# CORE Finance

## 1. Общая концепция

**CORE Finance** — внутренняя финансовая система CORE для управления денежными средствами, бюджетами проектов, расходами, криптовалютными переводами и финансовой отчётностью.

Система должна состоять из двух интерфейсных частей:

1. **Telegram Bot**

   * авторизация;
   * уведомления;
   * быстрые действия;
   * подтверждение критических операций;
   * переход в Mini App.

2. **Telegram Mini App**

   * основной интерфейс;
   * просмотр финансов;
   * создание платежей;
   * управление бюджетами;
   * история операций;
   * контрагенты;
   * approvals;
   * аналитика и отчётность.

Бот не должен превращаться в ебаный CLI из `/balance`, `/send`, `/history` на 40 команд.

Основная работа происходит через визуальный интерфейс Mini App.

---

# 2. Основные задачи

Система должна позволять:

* хранить информацию о финансовых активах CORE;
* управлять криптовалютными кошельками;
* выполнять переводы;
* вести бухгалтерский ledger;
* разделять деньги между проектами;
* создавать бюджеты;
* контролировать расходы;
* использовать роли и лимиты;
* согласовывать крупные или подозрительные платежи;
* хранить контрагентов и их реквизиты;
* автоматически связывать blockchain-транзакции с бухгалтерскими операциями;
* формировать отчёты;
* полностью логировать действия пользователей.

---

# 3. Архитектура

Общая схема:

```text
Telegram
│
├── Telegram Bot
│      │
│      ├── notifications
│      ├── approvals
│      └── Mini App launch
│
└── Telegram Mini App
       │
       ▼
CORE Finance API
       │
       ├── Auth
       ├── RBAC
       ├── Ledger
       ├── Budgets
       ├── Payments
       ├── Counterparties
       ├── Analytics
       ├── Policy Engine
       └── Audit
              │
              ▼
         Payment Engine
              │
              ▼
        Isolated Signer
              │
              ▼
          Blockchain
```

---

# 4. Принцип хранения средств

Система должна поддерживать несколько типов кошельков.

## 4.1 Treasury

Основное хранилище капитала CORE.

Назначение:

* хранение резервов;
* крупные переводы;
* пополнение операционных кошельков.

Для Treasury желательно использовать:

* multisig;
* hardware/cold wallet;
* ручное подтверждение операций.

Treasury нельзя использовать для повседневных платежей.

---

## 4.2 Operational Wallet

Hot wallet для текущей деятельности.

Используется бухгалтером для:

* оплаты серверов;
* рекламы;
* подрядчиков;
* аккаунтов;
* подписок;
* небольших внутренних расходов.

На operational wallet должна храниться только ограниченная сумма.

Пример:

```text
Treasury:       12 500 USDT
Operations:        750 USDT
```

---

# 5. Внутренние балансы

Blockchain wallet и баланс проекта не должны быть одной сущностью.

Один реальный кошелёк может содержать деньги нескольких проектов.

Пример:

```text
Operations Wallet

Actual balance:
4 700 USDT

Internal balances:

MineJust          1 250 USDT
AdShare             900 USDT
Patewen           1 400 USDT
Infrastructure      650 USDT
CORE Reserve        500 USDT
```

Все движения должны одновременно отражаться:

1. в blockchain;
2. в бухгалтерском ledger.

---

# 6. Роли

Минимально:

## Owner

Полный доступ.

Может:

* создавать кошельки;
* менять лимиты;
* управлять ролями;
* подтверждать любые операции;
* видеть всю финансовую информацию;
* выполнять treasury-переводы.

---

## CFO / Finance Admin

Доступ к финансовому управлению.

Может:

* создавать бюджеты;
* управлять проектами;
* добавлять контрагентов;
* просматривать аналитику;
* подтверждать платежи;
* формировать отчёты.

---

## Accountant

Основной рабочий пользователь.

Может:

* создавать платежи;
* выполнять разрешённые платежи;
* просматривать транзакции;
* управлять назначением платежей;
* добавлять документы;
* использовать существующих контрагентов.

Не может:

* менять собственные лимиты;
* получать приватные ключи;
* выводить Treasury;
* менять security policy.

---

## Project Manager

Видит только свои проекты.

Может:

* смотреть бюджет;
* смотреть расходы;
* создавать payment request;
* прикладывать счета.

Не имеет доступа непосредственно к кошельку.

---

## Viewer / Auditor

Только чтение.

---

# 7. Policy Engine

Все платежи должны проходить через policy engine.

Пример правил:

```yaml
accountant:
  transaction_limit: 250 USDT
  daily_limit: 750 USDT

  allowed_assets:
    - USDT

  allowed_wallets:
    - OPERATIONS

  whitelist:
    auto_approve_under: 250

  new_address:
    approval_required: true

  treasury:
    access: false
```

---

# 8. Approval Matrix

Пример:

```text
0–100 USDT
известный контрагент
→ автоматически

100–250 USDT
известный контрагент
→ бухгалтер

любой новый адрес
→ CFO approval

250–1000 USDT
→ CFO approval

1000+ USDT
→ Owner + CFO

Treasury transaction
→ минимум 2 подтверждения
```

Policy должна быть конфигурируемой без изменения кода.

---

# 9. Основные сущности

## User

```text
id
telegram_id
username
display_name
role
status
created_at
```

---

## Wallet

```text
id
name
type

TREASURY
OPERATIONS
PROJECT
OTHER

network
address
asset

balance
status
created_at
```

---

## Project

```text
id
name
code
status
manager_id
created_at
```

Примеры:

```text
MineJust
AdShare
Patewen
MineFed
CORE
Infrastructure
```

---

## Budget

```text
id
project_id

period
currency

allocated
spent
reserved
remaining

created_at
```

---

## Counterparty

```text
id
name
type

company
person
service

default_category

notes
status
created_at
```

---

## Counterparty Address

```text
id
counterparty_id

network
asset
address

label

verified
verified_by
created_at
```

---

## Payment

```text
id

wallet_id
project_id
counterparty_id

amount
asset
network

destination_address

category
description

created_by

status

tx_hash

created_at
approved_at
broadcasted_at
confirmed_at
```

---

# 10. Статусы платежей

```text
DRAFT

PENDING_APPROVAL

APPROVED

SIGNING

BROADCASTED

CONFIRMING

CONFIRMED

FAILED

REJECTED

CANCELLED
```

Каждое изменение статуса хранится отдельно.

---

# 11. Ledger

Blockchain transaction не является бухгалтерской записью.

Должен существовать внутренний double-entry ledger.

Пример:

```text
Payment #F-2938

Debit:
MineJust / Infrastructure

Credit:
Operations Wallet

Amount:
120 USDT
```

Ledger должен быть immutable.

Нельзя удалить бухгалтерскую операцию.

Ошибка исправляется новой корректирующей записью.

---

# 12. Категории расходов

Начальные категории:

```text
Infrastructure

Development

Media

Advertising

Accounts

Subscriptions

Payroll

Contractors

Legal

Taxes

Operations

Projects

Other
```

Должна быть возможность создавать собственные категории.

---

# 13. Интерфейс

## Общие требования

Интерфейс:

* минималистичный;
* современный;
* быстрый;
* mobile-first;
* без ощущения дешёвой админки;
* без Bootstrap-таблиц на весь экран;
* без миллиарда рамок;
* без разноцветных карточек ради карточек.

Основной стиль CORE:

* светлая база;
* нейтральный фон;
* белые поверхности;
* coral accent;
* чёрный/графитовый основной текст;
* мягкие серые secondary элементы.

---

# 14. Navigation

Нижняя мобильная навигация:

```text
Overview

Payments

Projects

Analytics

More
```

На desktop:

```text
CORE Finance

Overview
Payments
Projects
Wallets
Counterparties
Budgets
Analytics
Reports
Audit

────────────

Profile
Settings
```

---

# 15. Главный экран

Главный экран не должен выглядеть как Excel после инсульта.

Верх:

```text
CORE Finance

Total Assets
$14,842.10

+ $340.20 this month
```

Ниже горизонтальные карточки:

```text
Treasury
12,400 USDT

Operations
1,642 USDT

Available budget
800 USDT
```

---

## Quick actions

```text
[ New payment ]

Request money

Transfer between projects

Add transaction
```

Главная CTA:

**New payment**

---

# 16. Recent Activity

Вместо таблицы:

```text
Meredian Hosting
−120 USDT
Infrastructure
Today 14:32
Confirmed
```

```text
AdShare deposit
+400 USDT
AdShare
Yesterday 18:44
```

```text
Hetzner
−84.20 USDT
Infrastructure
Yesterday
```

Использовать нормальную типографическую иерархию.

Сумма должна визуально считываться быстрее названия категории.

---

# 17. Payment Flow

Нажатие:

**New payment**

Открывает fullscreen / large modal.

---

## Step 1 — Recipient

Поле:

```text
Who are you paying?
```

Autocomplete по контрагентам.

Например:

```text
Meredian Hosting
Hetzner
Puff
AdShare Accounts
```

Также:

```text
+ New recipient
```

---

# 18. Создание нового контрагента

Минимальная форма:

```text
Name

Wallet address

Network

Asset

Category
```

После добавления нового адреса:

```text
New destination

This address has never been used before.

Approval will be required.
```

---

# 19. Payment Amount

Экран:

```text
Pay Meredian Hosting

120
USDT

≈ $120.00
```

Под цифрой:

```text
Operations wallet
Balance: 842.14 USDT
```

---

# 20. Payment Metadata

```text
Project
MineJust

Category
Infrastructure

Description
September server payment

Invoice
[ Attach file ]
```

---

# 21. Confirmation Screen

Перед отправкой показывать summary.

```text
120 USDT

to

Meredian Hosting
TXxxxx...29aF

────────────

From
Operations

Project
MineJust

Category
Infrastructure

Network
TRON

Network fee
1.2 USDT

Total
121.2 USDT
```

CTA:

```text
Confirm payment
```

Не делать маленькую ебаную inline-кнопку среди двадцати других элементов.

Главное действие должно занимать всю доступную ширину.

---

# 22. Approval Flow

Если payment требует approval:

```text
Payment submitted

120 USDT

Meredian Hosting

Waiting for CFO approval.
```

Approver получает Telegram notification:

```text
Payment approval required

120 USDT
Meredian Hosting

Project:
MineJust

Category:
Infrastructure

Created by:
Puff
```

Кнопки:

```text
Review payment
```

Не давать Approve непосредственно из уведомления для крупных платежей.

Сначала открыть детали.

---

# 23. Approval Screen

```text
120 USDT

Meredian Hosting

Risk
Low

Known counterparty
12 previous payments

Last payment
120 USDT
30 Aug
```

Ниже:

```text
Created by
Puff

Project
MineJust

Description
September server payment
```

Действия:

```text
Reject

Approve
```

Reject требует comment.

---

# 24. Payment Detail

Каждый перевод получает отдельную страницу.

Пример:

```text
Payment #CF-2048

120 USDT

Confirmed
```

Timeline:

```text
19:42
Created by Puff

19:43
Approved by Shelby

19:43
Transaction signed

19:44
Broadcasted

19:45
Confirmed
```

---

# 25. Blockchain Information

Отдельный блок:

```text
Blockchain

Network
TRON

Tx hash
91fa...829e

Block
82491821

Confirmations
24
```

Кнопка:

```text
View explorer
```

---

# 26. Projects Screen

Карточка проекта:

```text
MineJust

Budget
1,200 USDT

Spent
720 USDT

Remaining
480 USDT
```

Progress indicator:

```text
████████████░░░░░
60%
```

---

# 27. Project Detail

```text
MineJust

Available
480 USDT
```

Разделы:

```text
Overview

Transactions

Budget

Categories

Members
```

---

# 28. Budget Analytics

Показывать:

```text
September

Budget
1,200

Spent
720

Remaining
480
```

Ниже:

```text
Infrastructure    320
Media             180
Development       140
Other              80
```

Графики должны использоваться только там, где они реально что-то показывают.

Не нужно превращать финансовый экран в новогоднюю ёлку из donut chart.

---

# 29. Wallet Screen

Пример:

```text
Operations

1,642.20 USDT
```

Информация:

```text
Network
TRON

Address
TXv...812

Type
Hot wallet
```

Actions:

```text
Send

Receive

Transfer
```

---

# 30. Receive

Показывать:

```text
Operations

USDT / TRON

TXxxxxxxxxxxxxxxxx
```

QR.

Кнопки:

```text
Copy address

Share
```

Обязательно крупное предупреждение:

```text
Send only USDT using TRON network.
```

---

# 31. Counterparties

Поиск сверху.

Карточки:

```text
Meredian Hosting

Infrastructure

12 transactions

1,420 USDT total
```

---

# 32. Counterparty Detail

```text
Meredian Hosting

Verified
```

Wallets:

```text
USDT / TRON
TXxxx...

USDT / TON
UQxxx...
```

Statistics:

```text
Total paid
1,420 USDT

Transactions
12

Last payment
30 Aug 2026
```

---

# 33. Analytics

Основные показатели:

```text
Income

Expenses

Net cash flow

Treasury

Operational balance
```

Периоды:

```text
7D
30D
3M
6M
1Y
Custom
```

---

# 34. Cash Flow

График:

```text
income
expenses
net
```

Не смешивать пять метрик в одну хуйню.

---

# 35. Expenses Breakdown

```text
Infrastructure
$1,420

Media
$840

Advertising
$620

Development
$570
```

---

# 36. Reports

Поддерживать:

```text
Monthly Finance Report

Project Report

Expenses Report

Cash Flow Report

Counterparty Report
```

Экспорт:

```text
CSV
XLSX
PDF
```

---

# 37. Notifications

Telegram bot уведомляет о:

* новом approval;
* подтверждённом платеже;
* failed transaction;
* превышении бюджета;
* низком балансе Operations;
* крупном входящем переводе;
* подозрительной операции.

---

# 38. Bot Home

Сообщение бота:

```text
CORE Finance

Total
$14,842

Operations
$1,642

Pending approvals
2
```

Кнопки:

```text
Open Finance

New Payment

Approvals
```

---

# 39. Security

Private keys никогда не должны передаваться:

* Telegram Bot;
* frontend;
* Finance API;
* PostgreSQL.

Ключ должен находиться только в signer service.

---

# 40. Signer

Signer запускается отдельно.

Пример:

```text
finance-api
   │
   ▼
private network
   │
signer
```

Signer:

* не имеет public port;
* принимает запросы только от Finance API;
* самостоятельно проверяет payment ID;
* проверяет лимиты;
* проверяет wallet;
* проверяет destination;
* подписывает transaction;
* возвращает signed transaction / tx hash.

---

# 41. Secrets

Использовать:

* Vault / Docker secrets / аналог;
* encrypted storage;
* key rotation.

Никаких:

```text
PRIVATE_KEY=...
```

в `.env` рядом с ботом.

---

# 42. Audit Log

Логировать:

```text
user

action

object

old value

new value

IP

Telegram user

timestamp
```

Пример:

```text
18 Sep 19:43

Shelby

APPROVED_PAYMENT

Payment #CF-2048

120 USDT
```

Audit log нельзя редактировать через основной интерфейс.

---

# 43. Авторизация

Telegram Mini App auth через signed initData.

Backend обязан проверять подпись Telegram.

После этого пользователь связывается с внутренним User.

---

# 44. Дополнительная защита

Для критических операций:

* Telegram confirmation;
* optional TOTP;
* session timeout;
* device history;
* rate limiting.

Для Treasury желательно обязательное второе подтверждение.

---

# 45. UX требования

## Скорость

Основные действия должны выполняться максимум за:

```text
Payment: 3–4 экрана

Approval: 2 экрана

Balance check: 1 экран
```

---

## Navigation

Назад всегда должно работать предсказуемо.

Никаких вложенностей:

```text
Settings
→ Finance
→ Wallet
→ Wallet settings
→ Security
→ Security settings
→ ...
```

чтобы потом человек не искал выход как из ебаного лабиринта Минотавра.

---

# 46. Loading

Использовать skeleton loading.

Не использовать пустой экран + spinner посередине для каждого запроса.

---

# 47. Empty States

Например:

```text
No payments yet

Your completed and pending payments
will appear here.

Create payment
```

---

# 48. Ошибки

Ошибка должна объяснять проблему.

Плохо:

```text
Something went wrong
```

Хорошо:

```text
Payment couldn't be sent.

The Operations wallet has
84 USDT available, but this
payment requires 121 USDT.
```

---

# 49. Visual Design System

## Border radius

```text
cards: 16–20px
buttons: 12–16px
inputs: 12–16px
```

---

## Shadows

Минимальные.

Большинство разделения интерфейса строить через:

* spacing;
* typography;
* background;
* subtle borders.

---

## Typography

Приоритет:

1. сумма;
2. статус;
3. объект операции;
4. metadata.

Например:

```text
−120 USDT

Meredian Hosting

Infrastructure · MineJust
Today 14:42
```

---

# 50. Цвета статусов

Использовать аккуратно.

```text
Confirmed
green

Pending
amber

Failed
red

Neutral
gray
```

Coral используется как **brand/action accent**, а не как цвет каждого второго блока.

---

# 51. Search

Глобальный поиск должен находить:

* payment ID;
* tx hash;
* counterparty;
* wallet address;
* project;
* description.

---

# 52. Backend

Рекомендуемый стек:

```text
Python 3.13+

Starlette / FastAPI

PostgreSQL

SQLAlchemy

Redis

Telegram Bot API

Telegram Mini Apps

Docker
```

Архитектуру backend строить модульно:

```text
app/

auth/

users/

wallets/

projects/

budgets/

ledger/

payments/

counterparties/

approvals/

policies/

blockchain/

analytics/

audit/

notifications/
```

---

# 53. Blockchain abstraction

Не привязывать бизнес-логику напрямую к TRON.

Интерфейс:

```python
class BlockchainProvider:

    async def get_balance(...):
        ...

    async def estimate_fee(...):
        ...

    async def build_transaction(...):
        ...

    async def broadcast(...):
        ...

    async def get_transaction(...):
        ...
```

Реализации:

```text
TronProvider

TonProvider

EthereumProvider
```

Можно начать только с одной сети.

---

# 54. MVP

Первая версия должна содержать:

### Wallets

* Operations wallet;
* balance;
* deposit address.

### Payments

* создание;
* approval;
* отправка;
* blockchain status.

### Projects

* project balances;
* categories;
* budgets.

### Counterparties

* создание;
* addresses;
* history.

### Accounting

* ledger;
* payment metadata.

### UX

* Telegram Mini App;
* Overview;
* Payments;
* Payment Detail;
* Projects;
* Counterparties;
* Approvals.

### Security

* isolated signer;
* RBAC;
* policy engine;
* audit log.

---

# 55. Post-MVP

В дальнейшем:

* fiat accounts;
* банковские карты;
* invoices;
* recurring payments;
* salary management;
* payroll;
* автоматическое распознавание счетов;
* OCR чеков;
* financial forecasts;
* recurring subscriptions detection;
* API для других систем CORE;
* автоматические monthly reports;
* Telegram expense requests;
* автоматические budget alerts.

---

# 56. Ключевой UX-принцип

CORE Finance не должен ощущаться как:

```text
криптокошелёк
+
админка
+
Telegram-бот
```

Это должен быть единый **финансовый продукт CORE**.

Пользователь думает:

```text
кому я плачу
сколько
за что
из бюджета какого проекта
```

а не:

```text
какой RPC
какой nonce
какая сеть
какой контракт
какая комиссия
```

Технические детали blockchain должны быть доступны, но находиться вторым уровнем интерфейса.

---

# 57. Definition of Done

MVP считается готовым, если бухгалтер может полностью выполнить следующий сценарий без доступа к серверу:

1. открыть Telegram;
2. перейти в CORE Finance;
3. увидеть текущий баланс;
4. выбрать существующего контрагента;
5. указать сумму;
6. выбрать проект;
7. указать категорию;
8. создать платёж;
9. получить approval при необходимости;
10. отправить транзакцию;
11. дождаться blockchain confirmation;
12. увидеть операцию в расходах проекта;
13. найти её через историю;
14. открыть tx hash;
15. получить её в месячном финансовом отчёте.

Owner при этом должен иметь возможность установить такие правила, чтобы бухгалтер **физически не мог** вывести больше разрешённой суммы или отправить деньги на неизвестный адрес без подтверждения.
