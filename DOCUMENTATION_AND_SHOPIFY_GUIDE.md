# SortFlow Pro for Shopify — Архитектура, Анти-Конкурентная Стратегия и Пошаговый Гайд по Запуску

Все файлы приложения для Shopify находятся строго в отдельной папке [`sortflow_shopify/`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify) и **никак не пересекаются** с версией для Shopware 6 ([`sortflow_pro/`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_pro)) и со скрапером ниш ([`niche_finder/`](file:///c:/Users/mikro/Downloads/IMAGINE/niche_finder)).

---

## 1. Почему мы НЕ затеряемся в Shopify App Store (цифры из нашей базы `niches.sqlite`)

Мы проверили реальную структуру категории **`Store design -> Product display -> Collections`** (`store-design-product-display-collections`) в нашей базе данных:

1. **Маленькая узкая подкатегория (всего 173 приложения, а не тысячи):**
   - В отличие от переполненных категорий SEO или Upsell (где по 1 200+ приложений), в подкатегории `Collections` всего **173 приложения**.
   - На 1–2 странице выдачи стоят приложения с **7–29 отзывами**:
     - `Pushy — Out Of Stock Down` — **11 отзывов (5.0★)**
     - `Smart Collection Pro` — **11 отзывов (4.6★)**
     - `Dynasort Collection Sorter` — **15 отзывов (5.0★)**
     - `SortWise Collection Sort` — **29 отзывов (5.0★)**
     - `Echelon Collection Sort & Hide` — **7 отзывов (5.0★)**
2. **Фильтры-чекбоксы внутри категории (`sorting_actions`):**
   - На странице категории Shopify слева есть 10 чекбоксов фильтрации по функциям:
     - `Push down out-of-stock` (`cf.collections.sorting_actions.push_down`)
     - `Pin products` (`cf.collections.sorting_actions.pin_products`)
     - `Drag and drop` (`cf.collections.sorting_actions.drag_and_drop`)
     - `Manual sorting` (`cf.collections.sorting_actions.manual`)
     - `Automated sorting` (`cf.collections.sorting_actions.automated`)
   - Когда мерчант ставит галочки `Push down out-of-stock` + `Pin products` + `Drag and drop`, в выдаче остается **меньше 25 приложений**. Отметив все эти чекбоксы при заполнении анкеты в Shopify Partner Dashboard, мы сразу попадаем в целевой фильтр.
3. **Мы закрыли 4 главные технические боли из 1–2★ отзывов конкурентов:**
   - **Боль №1 (у *Jedi Collection Sort* и *Hide & Push Down Out of Stock*):** *«Включил приложение, а на витрине ничего не поменялось!»* — потому что в Shopify коллекция по умолчанию стоит в режиме `BEST_SELLING`, а конкуренты забывают переключить `sortOrder` в `MANUAL`.
     - **Наше решение:** Метод [`ensure_manual_sort_order()`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/shopify_service.py#L221-L250) автоматически проверяет и переключает коллекцию в `MANUAL` через GraphQL `collectionUpdate` перед сохранением позиций + показывает понятный желтый баннер в UI.
   - **Боль №2 (у *Ouiteo* и *Gift Moments*):** *«Когда товар снова появляется на складе, он навсегда остается в подвале коллекции!»*
     - **Наше решение:** **Restock Memory™** (`saved_in_stock_position` в таблице `product_positions`). Когда товар продается в `0`, мы сохраняем его старый ранг (например, `#2`). Как только приходит вебхук `inventory_levels/update` с остатком `> 0`, товар автоматически возвращается ровно на `#2`.
   - **Боль №3 (у *Hide & Push Down Out of Stock*):** *«Новые товары падают ниже распроданных (Out-of-Stock)!»*
     - **Наше решение:** Настройка `New Arrivals Placement` (`after_pinned` по умолчанию) ставит новинки сразу после закрепленных (`📌 Pinned`) товаров и строго **выше** распроданных.
   - **Боль №4 (у *StockIQ* и *merchbees*):** *«Приложение перемешало всю коллекцию, и нет кнопки Отменить (Undo)!»*
     - **Наше решение:** Перед первой сортировкой автоматически создается слепок в таблице `collection_snapshots`, а в шапке есть кнопка **«↺ 1-Click Undo (Restore Original Order)»**.

---

## 2. Как прямо сейчас запустить и пощупать приложение локально в браузере (БЕЗ Docker!)

Так как у вас на Windows установлен Python 3.13, мы встроили в [`app/server.py`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/server.py) полноценный **интерактивный Sandbox-режим (`demo-store.myshopify.com`)**.

1. Откройте PowerShell и выполните:
   ```powershell
   cd c:\Users\mikro\Downloads\IMAGINE\sortflow_shopify
   python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
   ```
2. Откройте в браузере: **`http://127.0.0.1:8000`**
3. Что можно протестировать прямо в браузере (и записать на 60-секундное видео для модерации):
   - Перетаскивать карточки товаров мышкой (**Drag & Drop**);
   - Закреплять товары кнопкой **«📌 Pin»**;
   - Вводить номер позиции в поле `#N` для мгновенного прыжка;
   - Нажать кнопку **«Simulate Sell-Out (0)»** на карточке товара `#2` — он мгновенно упадет в конец списка с пометкой `🧠 Restock Memory saved (Rank #2)`;
   - Нажать на нем **«Simulate Restock (+25)»** — он автоматически вернется обратно на `#2`!
   - Нажать **«✓ Save & Sync to Shopify»** — коллекция автоматически переключится из `BEST_SELLING` в `MANUAL`, и загорится кнопка **«↺ 1-Click Undo»**.

---

## 3. Пошаговая инструкция: Где и как зарегистрироваться в Shopify Partners (Словения, выплаты в EUR/USD)

В отличие от Shopware, **регистрация разработчиков в Shopify Partners открыта 24/7 без ожидания и ручных пауз**, и у Shopify действует **0% комиссии (Revenue Share = 0%) на ваш первый $1,000,000 выручки** (вы получаете 100% за вычетом 2.9% эквайринга).

### Шаг 1. Регистрация аккаунта партнера (Бесплатно, 3 минуты)
1. Перейдите на **[https://partners.shopify.com](https://partners.shopify.com)** и нажмите **Join now**.
2. Войдите или создайте Shopify ID на ваш рабочий email (`Sergey Gorbunov`).
3. На экране создания партнерской организации заполните:
   - **Business name:** `Sergey Gorbunov` (или ваше название студии, например `SortFlow Labs`)
   - **What is the main reason you're creating a Partner account?** → Выберите **Build apps for the Shopify App Store**.
   - **Country / Region:** `Slovenia` (укажите ваш адрес в Словении).
   - Нажмите **Next / Get Started**.

### Шаг 2. Настройка выплат и налогов в Словению (Payouts)
1. В левом меню Shopify Partner Dashboard откройте **Settings → Payouts**.
2. В блоке **Tax information**:
   - Если вы регистрируетесь как физлицо / самозанятый без VAT — выберите **Individual / Sole Proprietor**, страна резидентства **Slovenia** (между США/Канадой и Словенией действует соглашение об избежании двойного налогообложения, форма W-8BEN заполняется прямо внутри интерфейса за 1 минуту).
   - Кроме того, сразу подайте заявку на регистрацию в режиме **0% Revenue Share Plan** в разделе **Settings → Revenue share** (бесплатная галочка для всех разработчиков с доходом до $1M в год).
3. В блоке **Payout method**:
   - Нажмите **Manage payout账户 (Hyperwallet / PayPal / Bank account)** → укажите ваш **словенский IBAN (SEPA)** или PayPal. Выплаты приходят автоматически 2 раза в месяц.

### Шаг 3. Создание бесплатного тестового магазина (Development Store)
Чтобы проверить приложение на живом магазине Shopify:
1. В меню слева нажмите **Stores → Add store → Create development store**.
2. Выберите **Create a store to test and build**.
3. Назовите его, например, `sortflow-demo-store`, и выберите **Start with test data** (Shopify сам создаст тестовые коллекции и товары!).

### Шаг 4. Развертывание бэкенда на публичный HTTPS URL (Render.com / Railway.app)
Shopify требует, чтобы у приложения был HTTPS-адрес:
1. Загрузите папку `sortflow_shopify` в приватный GitHub-репозиторий.
2. Зайдите на **[Render.com](https://render.com)** или **[Railway.app](https://railway.app)** → **New Web Service** → выберите ваш репозиторий (в папке уже лежат готовые [`requirements.txt`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/requirements.txt) и [`Dockerfile`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/Dockerfile)).
3. Вы получите постоянный HTTPS-адрес вида `https://sortflow-pro.onrender.com`.

### Шаг 5. Создание приложения в Shopify Partner Dashboard
1. В меню слева откройте **Apps → Create app → Create app manually**.
2. **App name:** `SortFlow: Collection Merchandiser`
3. На вкладке **Configuration**:
   - **App URL:** `https://sortflow-pro.onrender.com/`
   - **Allowed redirection URL(s):** `https://sortflow-pro.onrender.com/auth/callback`
   - **Scopes:** `read_products,write_products`
   - **Compliance webhooks (GDPR):**
     - Customer data request endpoint: `https://sortflow-pro.onrender.com/webhooks/gdpr`
     - Customer data erasure endpoint: `https://sortflow-pro.onrender.com/webhooks/gdpr`
     - Shop data erasure endpoint: `https://sortflow-pro.onrender.com/webhooks/gdpr`
4. На вкладке **Overview** скопируйте **Client ID (`SHOPIFY_API_KEY`)** и **Client secret (`SHOPIFY_API_SECRET`)** и добавьте их в Environment Variables на Render/Railway.

---

## 4. Готовые тексты для карточки Shopify App Store (ASO под чекбоксы фильтрации)

- **App Name (до 30 символов):** `SortFlow Collection Sort & Pin`
- **Subtitle / Tagline:** `Drag & drop visual merchandiser. Push down out-of-stock with Restock Memory.`
- **Primary Category:** `Store design -> Product display -> Collections`
- **Обязательно отметьте чекбоксы Features в анкете:**
  - `[x] Push down out-of-stock`
  - `[x] Pin products`
  - `[x] Drag and drop`
  - `[x] Manual sorting`
  - `[x] Automated sorting`
  - `[x] Sort new products`
- **Pricing (Тарифная сетка для быстрой органики и первых отзывов):**
  - **Free Plan ($0/month):** Up to 3 collections, Visual Drag & Drop, Auto-switch to MANUAL sort, 1-Click Snapshot Undo. *(Бесплатный тариф приносит первые 15–30 установок и первые 5★ отзывы без трения).*
  - **Pro Unlimited ($9.99/month, 14-day free trial):** Unlimited collections, Real-time Out-of-Stock Demotion via Webhooks, **Restock Memory™** (auto-restores exact rank on restock), Smart New Arrivals placement.

---

## 5. Структура файлов проекта (`c:\Users\mikro\Downloads\IMAGINE\sortflow_shopify\`)

- [`shopify.app.toml`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/shopify.app.toml) — манифест приложения Shopify (`2025-10`, минимальные скоупы `read_products,write_products`, вебхуки склада и обязательные GDPR-вебхуки).
- [`app/database.py`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/database.py) — хранилище SQLite WAL (`shop_sessions`, `collection_rules`, `product_positions` с полем `saved_in_stock_position` для Restock Memory™, и `collection_snapshots` для 1-Click Undo).
- [`app/shopify_service.py`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/shopify_service.py) — детерминированный движок сортировки [`compute_final_order()`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/shopify_service.py#L24-L129) + GraphQL-клиент Shopify Admin API ([`ShopifyGraphQLClient`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/shopify_service.py#L132-L299)) + проверка HMAC-подписей вебхуков.
- [`app/server.py`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/server.py) — FastAPI-сервер с OAuth 2.0, обработчиками вебхуков, Billing API и встроенной песочницей (`demo-store.myshopify.com`).
- [`app/static/index.html`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/app/static/index.html) — интерфейс в стиле Shopify Polaris с Drag & Drop, закреплением товаров (`📌 Pin`), прыжком на позицию (`#N`), симулятором вебхуков склада и кнопкой `1-Click Undo`.
- [`test_sortflow.py`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/test_sortflow.py) — автотесты всех 7 ключевых сценариев (проходят на 100%).
- [`requirements.txt`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/requirements.txt) и [`Dockerfile`](file:///c:/Users/mikro/Downloads/IMAGINE/sortflow_shopify/Dockerfile) — конфигурация для деплоя в 1 клик.

