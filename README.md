# AI Provider Observer

Мини-сервис для локального контроля квот и балансов AI-провайдеров. Он ничего не проксирует и не отправляет пользовательские промпты: только опрашивает usage/balance endpoints и сохраняет нормализованные snapshots в SQLite.

Поддержано в MVP:

- **Z.AI Coding Plan** — 5h + weekly, used %, remaining, reset; поддержаны текущий credit endpoint и legacy monitor endpoint.
- **MiniMax Token/Coding Plan** — 5h + weekly из `token_plan/remains`.
- **DeepSeek API** — фактический PAYG balance (CNY/USD). У DeepSeek нет аналогичной публичной 5h/weekly подписочной квоты API.
- **OpenRouter** — usage текущего API key; если на key задан daily/weekly/monthly limit, показывается процент и reset. Опциональный Management key добавляет account-wide purchased/used credits.
- **OpenAI Codex (ChatGPT plan)** — 5h/weekly/credits через тот же локальный ChatGPT login, который использует Codex CLI (`~/.codex/auth.json`). Endpoint внутренний и может измениться.

Также сервис считает скорость изменения процента квоты/баланса по собственной истории и показывает latency самого quota/balance запроса.

### Возможности R1 (аналитика потребления)

- **Burn rate** — OLS-регрессии по окнам **10 м / 15 м / 1 ч / 3 ч** (плюс **24 ч / 3 д** для weekly); классификация ускорения (decelerating / stable / accelerating / anomaly) относительно `burn_1h`.
- **ETA и survival margin** — горизонты оценки исчерпания (по `burn_10m`, `burn_15m`, `burn_1h`, `burn_3h`) и дельта до ближайшего reset: `survival_margin < 0` означает, что квота закончится до автоматического сброса; `survival_margin_short` — то же по мелкому 10-минутному темпу (ловит burst-расход).
- **Weekly pace / projected** — для weekly-окон: `pace_ratio`, полосы (`comfortable → unsustainable`), три проекции конца окна (`whole_window` / `pace_24h` / `pace_3d`) и confidence (`LOW/MEDIUM/HIGH`) по длине истории.
- **Bottleneck и risk score** — факторный скор 0..100 на окно, боттлнек = argmax; уровни `HEALTHY → WATCH → WARNING → HIGH → CRITICAL`; пороги алертов (`ALERT_*`) настраиваются через env.
- **Рекомендации** — действия `NO_ACTION / WATCH / REDUCE_LOAD / SHIFT_TRAFFIC / INCREASE_BUDGET / UPGRADE_PLAN` с обязательными `reason_lines` (конкретные числа) и `capacity_overview` для cross-provider shift.
- **События с дедупом** — `quota_reset`, `high_burn`, `quota_warning/critical`, `predicted_exhaustion`, `balance_low`, `provider_error/recovered`, `tariff_insufficient`; insert-or-ignore по `dedup_key` + cooldown, повтор не каждую минуту.
- **Графики** — vanilla-JS SVG-чарт (`/static/charts.js`) поверх `/api/history` с диапазонами 6 ч / 24 ч / 7 д / 30 д и оверлеями (reset-маркеры, threshold-ы, projected exhaustion).
- **Demo-сценарии** — `DEMO_SCENARIO=normal|high_burn|weekly_exhaustion|critical` детерминированно сеет историю в `quota_snapshots`, чтобы аналитика и risk-уровни были видны без живых ключей.

## 1. Быстрый запуск

Требуется Python 3.11+.

### Windows PowerShell

```powershell
cd ai-provider-observer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\run.ps1
```

### Linux/macOS

```bash
cd ai-provider-observer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
./run.sh
```

Открыть: `http://127.0.0.1:8787`

Для проверки интерфейса без секретов выставьте в `.env`:

```env
DEMO_MODE=true
```

Это строго маркируется как DEMO MODE и не обращается к провайдерам.

## 2. Где добавить секреты

Создайте `.env` из `.env.example`. `.env` уже исключён через `.gitignore`.

```env
ZAI_API_KEY=ваш_zai_coding_plan_key
MINIMAX_API_KEY=ваш_minimax_sk_cp_key
DEEPSEEK_API_KEY=ваш_deepseek_api_key
OPENROUTER_API_KEY=ваш_openrouter_api_key
OPENROUTER_MANAGEMENT_KEY=необязательно_management_key
```

Не добавляйте кавычки, если в ключе их нет. После изменения `.env` перезапустите сервис.

### Z.AI

Используйте ключ Coding Plan, которым подключаете Claude Code/OpenCode к Z.AI. Адаптер сначала пробует новый `GET /api/monitor/usage`, затем legacy `GET /api/monitor/usage/quota/limit`, включая оба реально встречающихся варианта Authorization (Bearer/raw). Ключ не пишется в SQLite и не отдаётся браузеру.

### MiniMax

Используйте Token Plan / Coding Plan ключ (`sk-cp...`). Запрос идёт на `https://www.minimax.io/v1/token_plan/remains`. Для процентов источником истины считаются `current_interval_remaining_percent` и `current_weekly_remaining_percent`; сервис не пытается выводить их из неоднозначных count-полей.

### DeepSeek

Обычный DeepSeek API key. Проверяется официальный `GET https://api.deepseek.com/user/balance`. В карточке показываются валютные балансы и скорость их уменьшения по локальной истории.

### OpenRouter

`OPENROUTER_API_KEY` достаточно для `GET /api/v1/key`: видны total/daily/weekly/monthly usage этого ключа и его собственный limit, если вы его настроили.

`OPENROUTER_MANAGEMENT_KEY` опционален. Он нужен для `GET /api/v1/credits`, который возвращает total purchased и total used по аккаунту. Management key не используется для LLM inference.

### OpenAI Codex

Секрет вручную добавлять обычно **не нужно**.

1. Установите/запустите Codex CLI.
2. Выберите **Sign in with ChatGPT**.
3. Observer сначала использует `~/.codex/auth.json` (или `CODEX_HOME`) для быстрого read-only запроса usage.
4. Если файл недоступен/credential хранится иначе, Observer автоматически пробует официальный локальный `codex app-server` и метод `account/rateLimits/read`.

Если Codex хранится не в стандартном месте:

```env
CODEX_HOME=/custom/path/.codex
```

или укажите непосредственно файл:

```env
CODEX_AUTH_PATH=/custom/path/auth.json
```

На Windows типичный путь автоматически получается из профиля пользователя: `%USERPROFILE%\.codex\auth.json`.

**Важно:** Observer сам не реализует OAuth refresh. Если прямой запрос возвращает 401/403, он попробует `codex app-server`; если и это не помогает, запустите Codex и перелогиньтесь.

## 3. Docker

Для провайдеров с API keys:

```bash
cp .env.example .env
# заполнить .env
docker compose up --build
```

Для Codex на Linux/macOS раскомментируйте read-only mount `~/.codex:/root/.codex:ro` в `docker-compose.yml`. На Windows проще сначала запускать Python-версию вне Docker, чтобы она напрямую увидела `%USERPROFILE%\.codex\auth.json`.

## 4. API сервиса

| Метод | Эндпоинт | Описание |
|---|---|---|
| GET | `/` | Дашборд со сводкой, карточками провайдеров и графиками. |
| GET | `/api/status` | Последние нормализованные snapshots + тренды; для каждого провайдера добавлено поле `risk` (уровень, скор, bottleneck). |
| GET | `/api/analytics` | Полный кэш аналитики (burns, forecast, pacing, runway, risk, recommendation, capacity_overview). |
| GET | `/api/analytics/{provider}` | Блок одного провайдера; 404 для неизвестного имени. |
| GET | `/api/events` | Лента событий с дедупом и cooldown; параметры `?limit=&provider=`. |
| GET | `/api/recommendations` | Текстовые рекомендации + `capacity_overview` для shift-traffic. |
| GET | `/api/history/{provider}/{window_type}?hours=6\|24\|168\|720` | Сырые точки серии в окне (6ч / 24ч / 7д / 30д); даунсэмплинг до 800 точек. |
| POST | `/api/refresh` | Немедленный опрос всех провайдеров с возвратом обновлённых snapshots. |
| GET | `/healthz` | Health check (`{"ok": true}`). |

SQLite по умолчанию: `./data/observer.db`. Сохраняются только нормализованные usage/balance данные, **не секреты и не сырые upstream responses**.

## 5. Что означает «скорость»

В этой версии без дополнительных платных запросов показываются:

- `check N ms` — latency запроса к quota/balance endpoint;
- `+X п.п./ч` — изменение использованной квоты в процентных пунктах в час;
- `X currency/ч` — скорость уменьшения PAYG balance.

Активный TPS/TTFT benchmark моделей намеренно не включён по умолчанию: такой probe сам расходует квоту и может исказить измерение. Его разумно добавить отдельным opt-in модулем с редким интервалом и явно выбранной моделью.

## 6. Аналитика (R1)

Сервис сохраняет нормализованную историю квот/балансов в таблицу `quota_snapshots` и по ней считает скорость расхода (burn), ETA, survival margin, weekly pacing, денежный runway, risk score, bottleneck и рекомендации. Математика — в `app/analytics/`; collectors остаются чистыми источниками snapshot'ов и ничего не прогнозируют.

Ключевые гарантии:

- Любая отсутствующая метрика возвращается как `null` + `status="insufficient_data"`, а не как ноль (§29).
- Burn через reset не считается: сегментация по `reset_at` ловит настоящие сбросы и помечает подозрительные провалы без признака reset как «excluded».
- Конфиденциальность: в `raw_json` БД и в ответах `/api/*` ключи API/management удаляются автоматическим редактором (`app.normalize.redact`), см. §36.20 acceptance.

Полное описание архитектуры, формул и event-семантики — в [docs/R1_ANALYTICS_RU.md](docs/R1_ANALYTICS_RU.md).

## 7. Demo-сценарии

В `.env` можно включить demo-режим и выбрать один из четырёх сценариев; детерминированная история засеивается в `quota_snapshots` при первом `collect()`, поэтому аналитика, burn, risk и рекомендации видны без живых ключей:

```env
DEMO_MODE=true
DEMO_SCENARIO=normal           # normal | high_burn | weekly_exhaustion | critical
```

| Сценарий | Что демонстрирует |
|---|---|
| `normal` | Спокойное потребление: HEALTHY/WATCH на всех окнах, burn ровный, survival margin положительный. |
| `high_burn` | Резкий рост 5h-окна: HEALTHY→WARNING→HIGH/CRITICAL за минуты, `quota_reset`-событие в начале окна. |
| `weekly_exhaustion` | Недельное окно обгоняет график: `pace_ratio > 2.0`, `projected > 200%`, bottleneck = weekly. |
| `critical` | Margin < 0, projected > 120%, bottleneck = five_hour, ETA < 30 мин. |

## 8. Тесты

Полный прогон unit- и integration-тестов:

```bash
.venv/bin/python -m pytest tests -q
```

Эквивалент через Makefile (использует системный python, если venv отсутствует):

```bash
make unit
```

Парсерные тесты покрывают реальные формы ответов Z.AI credit windows, MiniMax remaining-percent, DeepSeek balance, OpenRouter key limit и Codex 5h/week. Аналитические unit-тесты проверяют burn, forecast, pacing, risk, events, recommendations и redaction-guard (`tests/test_redaction.py`) + временную семантику (`tests/test_time_handling.py`).

## 9. Безопасность

- не публикуйте `.env`;
- слушатель по умолчанию привязан к `127.0.0.1`;
- не открывайте порт 8787 в интернет без reverse proxy + authentication;
- `auth.json` Codex содержит OAuth credentials — монтируйте его только read-only;
- redirect following отключён для upstream usage calls, чтобы Authorization header не ушёл на другой host через redirect.

## 10. Переменные окружения

Полный список — в `.env.example`. Ниже — только переменные аналитического слоя R1 и смежные с ним (остальные — общие `POLL_INTERVAL_SECONDS`, `DATABASE_PATH`, ключи провайдеров и их `*_BASE_URL` без изменений).

| Переменная | Назначение |
|---|---|
| `ANALYTICS_ENABLED` | Мастер-выключатель аналитики (`true`/`false`). При `false` `/api/analytics*` возвращает `{"analytics_enabled": false}`. |
| `DEMO_SCENARIO` | Сценарий для `DEMO_MODE=true`: `normal` \| `high_burn` \| `weekly_exhaustion` \| `critical`. |
| `PLANS_CONFIG_PATH` | Путь к YAML со списком тарифов для рекомендаций `UPGRADE_PLAN` (по умолчанию `./config/plans.yaml`). |
| `QUOTA_RETENTION_DAYS` | Срок хранения `quota_snapshots`; `0` — хранить всё (MVP). |
| `HISTORY_LOOKBACK_HOURS` | Насколько глубоко engine читает `quota_snapshots` при пересчёте. |
| `ALERT_WARNING_USED` / `ALERT_HIGH_USED` / `ALERT_CRITICAL_USED` | Пороги `used_percent` для уровней WARNING / HIGH / CRITICAL (по умолчанию 70 / 85 / 95). |
| `ALERT_WARNING_PROJECTED_WEEK` / `ALERT_CRITICAL_PROJECTED_WEEK` | Пороги `projected_whole_window` (%), 90 / 120 по умолчанию. |
| `ALERT_CRITICAL_ETA_MINUTES` | ETA меньше этого числа минут поднимает CRITICAL (по умолчанию 30). |
| `BALANCE_LOW_DAYS` | Runway меньше этого числа дней → событие `balance_low` (по умолчанию 7). |

Тонкая настройка регрессий/сегментации (дефолты разумные, менять не обязательно): `BURN_MIN_POINTS`, `BURN_MIN_SPAN_MINUTES`, `RESET_DROP_MIN_PP`, `RESET_JITTER_PP`, `ACCEL_BASELINE_MIN`, `WEEK_MIN_ELAPSED_PCT`, `RECOMMENDED_HEADROOM_FACTOR`, `EVENT_COOLDOWN_MINUTES`.
