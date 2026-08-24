# План разработки R1 — Consumption Intelligence

Статус: план на ревью. Реализация начнётся после подтверждения.
Базовый срез: 78/78 тестов проходят (`​.venv/bin/python -m pytest tests -q`), collectors/dashboard не трогаем по контракту.

---

## 0. Текущее состояние (что уже есть в коде)

| Компонент | Файл | Что даёт для R1 |
|---|---|---|
| Модели | `app/models.py` | `QuotaWindow(name, used_percent, remaining_percent, reset_at, used, limit, remaining, unit, unlimited)`, `ProviderSnapshot(provider, label, status, checked_at, latency_ms, plan, windows[], balances[], details{}, error)` |
| Collectors | `app/providers/{zai,minimax,deepseek,openrouter,codex}.py` | Уже возвращают used/limit (Z.AI credits, OpenRouter USD), только проценты (MiniMax, Codex), баланс (DeepSeek). Reset уже нормализован к ISO UTC (`epoch_to_iso` понимает seconds vs ms) |
| Store | `app/store.py` | Одна таблица `snapshots(provider, checked_at, payload JSON)`, retention ~25k строк/провайдер, методы `latest()/recent()` |
| Наивный тренд | `app/collector.py::_compute_trends` | Двухточечная скорость %/ч; уже не сравнивает разные `reset_at`. Будет заменён слоем аналитики (в `/api/status` сохраняем обратную совместимость) |
| UI | `app/static/*` (vanilla JS, без сборки) | Карточки провайдеров, сводка. Будет расширен |
| Demo | `app/demo.py` | Одноразовые snapshots без истории — для burn-аналитики нужно сеять историю |
| Конфиг | `app/config.py`, `.env.example` | `POLL_INTERVAL_SECONDS` (clamp ≥15 — поднять до 30 по §35), ключи провайдеров |

Ключевые факты, на которых строится план:

- Z.AI и OpenRouter дают абсолютные значения → burn считаем в credits/h и USD/h.
- MiniMax и Codex дают только проценты → `percentage_points_per_hour`.
- DeepSeek — только денежный баланс → runway в днях (§16).
- OpenRouter имеет **два независимых ограничения**: лимит ключа (daily/weekly/monthly) и account balance — нельзя смешивать (§17).
- Codex отдаёт только фактически присутствующие окна (primary/secondary) — не выдумываем отсутствующие (§18).
- `reset_at`: у Z.AI/MiniMax/Codex — настоящий от API; у OpenRouter — вычисленная граница (помечаем `estimated`); если reset неизвестен — режим rolling/unknown (§9).

---

## 1. Принципы и осознанные отклонения от буквы ТЗ

1. **Collectors не меняются** (кроме одного аддитивного поля, см. ниже). Правило «collector не содержит бизнес-логики прогнозирования» соблюдается: вся математика в новом пакете `app/analytics/`.
2. **Расположение кода**: в ТЗ показаны папки `collectors/` и `analytics/`. Существующие коллекторы живут в `app/providers/` и переименовывать их = ломать импорты/тесты (нарушение «не ломать существующие collectors»). Поэтому analytics создаётся как **`app/analytics/`** — структура и разделение ответственности те же.
3. **Аддитивные изменения моделей** (обратно совместимы, дефолты):
   - `QuotaWindow.window_type: str | None` — канонический тип из ряда `five_hour|daily|weekly|monthly|balance|credits|unknown`;
   - `QuotaWindow.reset_estimated: bool = False` — reset вычислен, а не гарантирован API;
   - `ProviderSnapshot.account: str = "default"` — идентификатор аккаунта/ключа (для OpenRouter = label ключа).
   Единственное место, где трогаем collector-код: заполнение этих полей в парсерах (чисто аннотация, логика не меняется).
4. **Никаких выдуманных данных** (§29): любое отсутствующее значение → `null` + `"status": "insufficient_data"` / `"unknown"`. Ноль не подставляется никогда. Покрывается отдельным тестом.
5. **Observer не генерирует трафик** (§34): никаких inference-вызовов; synthetic probes — только заглушка-флаг, выключенная по умолчанию, без реализации.
6. **UTC внутри, TZ браузера снаружи** (§30); хранение ISO 8601 UTC, фронтенд форматирует локально.
7. **Секреты**: в `raw_json` пишется только тело ответа провайдера (там ключей нет); добавляется автоматический тест-редактор, который сканирует БД/API/логи на утечку значений ключей из Settings (§36.20).

---

## 2. Целевая архитектура

```text
providers (без изменений логики)
    │  ProviderSnapshot
    ▼
Collector.collect() ──► Store.save()            (старая таблица snapshots — как была)
    │                     │
    │                     └──► Store.save_quota_snapshot()   НОВАЯ таблица quota_snapshots
    │                           (нормализация + raw_json, append-only)
    ▼
AnalyticsEngine (кэш, пересчёт после каждого collect, лениво при старте)
    ├── series.py        серия по (provider, account, window_type), сегментация по reset
    ├── burn_rate.py     регрессия 15m/1h/3h/window (+24h/3d для weekly), acceleration
    ├── forecast.py      ETA ×3, reset_in, survival_margin, rolling-semantics
    ├── pacing.py        weekly pace_ratio, expected_usage_by_now, projected ×3
    ├── runway.py        денежный баланс: USD/h, USD/day, USD/week, runway, monthly
    ├── risk.py          risk_score 0..100, level, bottleneck
    ├── recommendation.py plan_headroom, capacity ratios, действия + причины
    ├── events.py        генерация событий с dedup/cooldown (переходы состояний)
    ├── confidence.py    LOW/MEDIUM/HIGH по длине истории
    └── plans.py         опциональный config/plans.yaml (source=configured)
    ▼
API: /api/analytics, /api/analytics/{provider}, /api/events,
     /api/recommendations, /api/history/{provider}/{window_type}
     /api/status — прежний контракт + добавленное поле "risk" (не ломающее)
    ▼
Dashboard (vanilla JS): summary, карточки с burn/margin/pace, таблица bottlenecks, графики
```

Пересчёт аналитики: после каждого успешного `collect()` (и один раз при старте из истории БД). Результат держится в памяти процесса (`AnalyticsEngine.snapshot_cache`) с TTL до следующего цикла — эндпоинты не считают регрессии на каждый запрос. Рестарт безопасен: всё считается заново из `quota_snapshots`.

---

## 3. Схема данных

### 3.1 `quota_snapshots` (новая, append-only, retention по умолчанию не ограничен)

```sql
CREATE TABLE IF NOT EXISTS quota_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT 'default',
    window_type TEXT NOT NULL,            -- five_hour|daily|weekly|monthly|balance|credits|unknown
    window_label TEXT,                    -- исходное имя окна ("5h", "week", "weekly"...)
    collected_at TEXT NOT NULL,           -- ISO 8601 UTC

    used REAL,                            -- абсолютные единицы, если есть
    remaining REAL,
    limit_value REAL,
    used_percent REAL,                    -- 0..100 или NULL
    unit TEXT,                            -- credits|tokens|USD|requests|percent

    reset_at TEXT,                        -- ISO UTC или NULL
    reset_estimated INTEGER NOT NULL DEFAULT 0,

    raw_json TEXT NOT NULL                -- полное тело ответа провайдера за этот poll
);
CREATE INDEX IF NOT EXISTS idx_quota_series
    ON quota_snapshots(provider, account, window_type, collected_at);
```

Решения:

- **Одна строка на (poll, окно)** — разворачиваем `windows[]` и `balances[]` (баланс → `window_type=balance`/`credits`, `used=limit-total`, `remaining=total`). Это делает ряды однородными для регрессии.
- `raw_json` дублируется на каждую строку одного poll'а — сознательно (MVP, объём мал: 5 провайдеров × несколько окон × 1/мин); упрощает расследование изменений API без join'ов.
- Старая таблица `snapshots` остаётся как есть (сырой журнал + источник для старого `/api/status`). Retention на неё не меняем; на `quota_snapshots` добавляем опциональный `QUOTA_RETENTION_DAYS=0` (0 = не удалять, MVP).
- Ошибочные poll'ы (status=error) в `quota_snapshots` **не пишутся** (нет числовых метрик), но фиксируются событием `provider_error` и учитываются в risk.

### 3.2 `events` (новая)

```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT 'default',
    window_type TEXT,
    event_type TEXT NOT NULL,   -- quota_reset|high_burn|quota_warning|quota_critical|
                                -- predicted_exhaustion|balance_low|provider_error|
                                -- provider_recovered|tariff_insufficient
    severity TEXT NOT NULL,     -- info|warning|high|critical
    created_at TEXT NOT NULL,
    dedup_key TEXT NOT NULL,    -- provider:account:type:window:bucket
    payload_json TEXT,
    UNIQUE(dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(provider, created_at);
```

Dedup/cooldown (§23):

- `quota_warning/critical/predicted_exhaustion/balance_high_burn/tariff_insufficient` — генерируются только **при переходе** уровня (например WARNING→CRITICAL), повтор не раньше cooldown (по умолчанию 30 мин, конфигурируемо per-type).
- `quota_reset` — одно на сегмент (`dedup_key` включает новый `reset_at`).
- `provider_error` — cooldown 10 мин; `provider_recovered` — только при переходе error→ok.
- Реализация insert-or-ignore по `dedup_key` + проверка cooldown в `events.py`; тестируется отдельно.

---

## 4. Алгоритмика (формулы и пороги, выносимые в config)

### 4.1 Нормализация имён окон

| Collector имя | window_type |
|---|---|
| `5h` (Z.AI unit=3,number=5; MiniMax interval; Codex primary ≈17000–19000s) | `five_hour` |
| `week` / `weekly` | `weekly` |
| `daily` | `daily` |
| `monthly` | `monthly` |
| balances USD | `balance` (account-level) / `credits` (Codex credits, key-budget OpenRouter) |
| прочее (`window N`, `primary_window`) | `unknown` (окно сохраняем, но без прогнозов типа окна) |

### 4.2 Детекция reset (§3)

Точка `(prev → cur)` по окну классифицируется как `quota_reset`, если:

```
drop = prev.used_percent − cur.used_percent
drop > RESET_DROP_MIN_PP (default 5 п.п., гистерезис против джиттера)
И (cur.reset_at ≠ prev.reset_at ИЛИ now ≥ prev.reset_at)
```

Дополнительно: любое снижение used (drop > джиттера 2 п.п.) **без** признаков reset помечает точку как подозрительную → точка исключается из регрессий, но сегмент не рвётся (защита от глюка API). Событие `quota_reset`, новая аналитическая серия. Burn через границу reset не считается никогда.

### 4.3 Burn rate (§4–6)

Для каждого окна и каждого lookback L ∈ {15m, 1h, 3h} (+ {24h, 3d} для weekly):

- выборка: точки текущего reset-сегмента с `collected_at ≥ now − L`, `n ≥ MIN_POINTS (3)`, span ≥ MIN_SPAN_MINUTES (5);
- дедупликация по timestamp (последний при коллизии), отброс битых точек (used_percent вне 0..100, время назад);
- OLS-регрессия `y = used` (абсолютные единицы, если есть, иначе `used_percent`), `x = elapsed_hours`; slope = burn;
- выход: `{value, unit: "credits/hour"|... |"percentage_points_per_hour", points, span_minutes, status: "ok"|"insufficient_data"}`.

Производные:

- `burn_short=burn_15m`, `burn_medium=burn_1h`, `burn_long=burn_3h`;
- `burn_acceleration = burn_15m / burn_1h` — только если `|burn_1h| ≥ ACCEL_BASELINE_MIN (default 1.0 ед./ч)`; иначе `null`. Полосы: `<0.7` замедление, `0.7–1.3` стабильно, `1.3–2.0` ускорение, `>2.0` аномалия;
- `burn_window` — от первого наблюдения текущего сегмента.

### 4.4 ETA и survival_margin (§7–9)

```
eta_hours(L) = remaining / burn_L          (remaining в тех же единицах, что burn)
eta_current      ← burn_15m
eta_stable       ← burn_1h
eta_conservative ← burn_3h (или самый длинный доступный)
burn ≤ ε  →  ETA = null ("не расходуется")
reset_in_seconds = reset_at − now          (NULL, если reset неизвестен)
survival_margin_seconds = eta_seconds − reset_in_seconds   (< 0 ⟹ закончится ДО reset)
confidence: <15м истории → LOW; 15м–2ч → MEDIUM; >2ч → HIGH (§27)
```

Rolling-семантика (§9): если `reset_at is None` или `reset_estimated=true` — блок Recovery рендерится как `rolling/estimated`, точный reset не обещается; survival_margin в этом случае `null` (не число из выдуманного reset).

### 4.5 Weekly pacing и forecast (§10–11)

```
week_start = reset_at − 7d (если reset_at известен), иначе первый наблюдаемый пункт сегмента
elapsed_pct = clamp((now − week_start)/7d, 0..1)×100
expected_usage_by_now = elapsed_pct
pace_ratio = used_percent / elapsed_pct          (elapsed < MIN_WEEK_ELAPSED_PCT(3%) → null)
полосы: <0.8 комфортно; 0.8–1.1 нормально; 1.1–1.4 повышенный; 1.4–2.0 критично; >2.0 тариф недостаточен

projected_week_usage:
  whole_window : used / (elapsed/100)                       (guard elapsed)
  pace_24h     : used + burn_24h × оставшиеся часы недели
  pace_3d      : used + burn_3d  × оставшиеся часы недели
headline = whole_window, рядом все три (как в примере ТЗ 167%/143%/132%)
```

Границы недели берутся от провайдерского `reset_at`, не от локального понедельника (корректно для любых тайзон/DST — покрыть тестами §30).

### 4.6 Денежный runway (§16, §17)

Окно `balance`/`credits`:

```
usd_per_hour  ← регрессия по 1h; usd_per_day ← burn_24h; usd_per_week ← burn_7d
runway_days   = balance_total / usd_per_day        (spend ≤ 0 → null, "стабилен")
projected_monthly_spend = usd_per_day × 30.4
```

OpenRouter: key-limit окно (daily/weekly/monthly) анализируется как обычная квота; account balance — отдельное окно `balance`; в UI и API они не смешиваются. `balance_low` при runway < BALANCE_LOW_DAYS (default 7).

### 4.7 Risk score и bottleneck (§14–15)

На каждое окно — факторный скор 0..100 (веса в конфиге):

```
f_remaining   : used% ≥95→95+, ≥85→~80, ≥70→~55, иначе линейно вниз
f_margin      : margin<0 → 70+по мере глубины; 0≤margin<30мин → ~55; else ↓
f_accel       : полосы acceleration (>2.0 → 80+)
f_pace        : полосы pace_ratio
f_projected   : projected_week_usage (≥120% → 75+)
f_errors      : доля ошибочных poll за последние 15 мин / throttling-флаги из details
window_risk   = max-комбинация факторов (доминирует самый опасный)
provider_risk = max(окон, баланса) + штраф errors     (верхняя граница = самый опасный лимит)
bottleneck    = argmax: five_hour|weekly|monthly|balance|performance|errors|none
уровни: 0–29 HEALTHY, 30–49 WATCH, 50–69 WARNING, 70–84 HIGH, 85–100 CRITICAL
```

Все пороги alert'ов §24 — в `config.py`/env: `ALERT_WARNING_USED=70`, `ALERT_HIGH_USED=85`, `ALERT_CRITICAL_USED=95`, `ALERT_WARNING_PROJECTED_WEEK=90`, `ALERT_CRITICAL_PROJECTED_WEEK=120`, `ALERT_CRITICAL_ETA_MINUTES=30`.

### 4.8 Recommendation engine (§12–13, §25–26)

```
required_capacity_ratio   = max(1, projected_headline/100)
recommended_capacity_ratio = required × HEADROOM_FACTOR (1.25)
plan_headroom             = recommended_ratio
```

- Если есть `config/plans.yaml` (source=`configured`) или провайдер сам сообщает планы (source=`provider`) — подбор следующего достаточного плана («Current: Pro → Next: Max, sufficient»). **Без configured/provider данных названий тарифов не предлагаем** — только коэффициенты (§12).
- Действия: `NO_ACTION | WATCH | REDUCE_LOAD | SHIFT_TRAFFIC | INCREASE_BUDGET | UPGRADE_PLAN`; каждая рекомендация обязана содержать `reason` с числами (пример текста — из §25).
- Cross-provider (§26): `available_capacity` = список провайдеров уровней HEALTHY/WATCH; рекомендация SHIFT_TRAFFIC перечисляет цели. Только текст, никакого автороутинга.

Пример `config/plans.yaml.example` (пустые значения, без выдуманных цифр):

```yaml
providers:
  zai:
    current_plan: lite
    plans:
      lite: { weekly_capacity: null }
      pro:  { weekly_capacity: null }
```

---

## 5. Фазы работ

### M0 — Инфраструктура тестирования и базовая совместимость (0.5 дня)
- Починить `make unit`: целевой раннер — `.venv/bin/python -m pytest` (сейчас unittest-discover пропускает function-style тесты и падает на системном python без httpx). Оставить оба таргета согласованными.
- Characterization-тесты на текущий контракт `/api/status` (заморозить поля, чтобы R1 ничего не сломал).
- `Settings`: clamp poll ≥ 30s (§35), новые поля аналитики (см. §7 плана).

### M1 — Хранилище истории (§2, §23) (1 день)
- `app/store.py`: DDL `quota_snapshots` + `events`, индексы, методы `save_quota_snapshots()`, `load_series(provider, account, window_type, since)`, `insert_event()`, `recent_events()`.
- Новый `app/normalize.py`: `ProviderSnapshot → rows[quota_snapshots]` (мэппинг окон §4.1, account, raw_json, redact-хук).
- `Collector.collect()`: после `store.save(snap)` вызывать нормализацию и запись (в одном try/except — сбой аналитики не должен валить сбор).
- Тесты: миграция на свежей и существующей БД; рестарт без потери истории; индекс используется; retention off.

### M2 — Ядро аналитики (§3–§11, §27, §16) (2–3 дня)
- `app/analytics/series.py` — сегментация по reset (правила §4.2), чистка битых точек.
- `app/analytics/burn_rate.py` — регрессии, insufficient_data, acceleration.
- `app/analytics/forecast.py` — ETA×3, reset_in, survival_margin, rolling, confidence.
- `app/analytics/pacing.py` — pace_ratio, projected×3.
- `app/analytics/runway.py` — USD/h·day·week, runway_days, monthly projection.
- Все функции чистые: `(points, now, config) → dataclass`, time инжектируется — это основа и для unit-, и для integration-тестов с ускоренным временем.
- Юнит-тесты §31 один-в-один (см. §9 плана).

### M3 — Risk, bottleneck, events, recommendations (§12–15, §23–26) (1.5 дня)
- `risk.py`, `recommendation.py`, `events.py`, `plans.py` (+ `config/plans.yaml.example`).
- Переходы уровней и cooldown; генерация событий в конце каждого цикла AnalyticsEngine.
- Тесты: границы полос risk/pace/acceleration; dedup/cooldown; reason-строки рекомендаций; bottleneck=argmax (пример Z.AI 82/34→overall 82, bottleneck=five_hour).

### M4 — Engine + API (§28) (1 день)
- `app/engine.py`: `AnalyticsEngine(store, settings)` — `refresh_all()`, `get(provider)`, `summary()`; кэш; пересбор при старте из истории.
- Эндпоинты: `GET /api/analytics`, `GET /api/analytics/{provider}`, `GET /api/events?limit&provider`, `GET /api/recommendations`, `GET /api/history/{provider}/{window_type}?hours=6|24|168|720`.
- Контракт ответа — как в §28 ТЗ (+ `windows.*.burn_3h`, `pace`, `projected`, `recommendation`, `confidence`).
- `/api/status`: прежние поля нетронуты; добавлено `providers[].risk` (аддитивно).
- Интеграционный smoke: TestClient поверх temp-БД.

### M5 — Dashboard (§19–22) (1.5–2 дня)
- Карточка провайдера по макету §19: риск-бейдж, per-window burn/margin, weekly pace/projected, BOTTLENECK, capacity-совет.
- Summary-панель §20 (healthy/warning/critical, most constrained, first exhaustion, highest overspend, lowest runway).
- Таблица Bottlenecks §21.
- Графики §22: лёгкий vanilla-JS SVG-чарт (без сборки и внешних CDN), диапазоны 6h/24h/7d/30d, оверлеи: reset-маркеры, warning threshold, projected exhaustion, линия slope; для баланса — переключатель Balance/Daily spend. Данные — `/api/history`.
- Всё время — в TZ браузера; unknown → «—».

### M6 — Demo-сценарии и integration-тесты (§32–33, §35) (1.5 дня)
- `app/demo.py` → генератор сценариев `DEMO_SCENARIO=normal|high_burn|weekly_exhaustion|critical`:
  - сеет детерминированную историю в `quota_snapshots` при старте (несколько часов назад → сейчас), затем эволюционирует состояние каждым tick;
  - `high_burn` визуально проводит HEALTHY→WARNING→HIGH/CRITICAL за минуты (для приёмки §36.18);
  - сценарий `critical`: margin<0, projected>120%, bottleneck=five_hour.
- Fake-transport (`tests/fakes.py`): canned HTTP-ответы всех 5 провайдеров; ускоренное время — прямая запись исторических точек (не sleep'ы).
- Сценарная матрица §32: normal usage / rapid consumption / quota reset / provider API failure / recovery / balance exhaustion / weekly exhaustion → проверки через API (не только функции).
- Restart-тест: собрать историю, «перезапустить» приложение (новый Store/Engine на той же БД), история и аналитика живы (§36.19).

### M7 — Безопасность, время, документация, финал (1 день)
- Redaction-тест: значения всех ключей из Settings не встречаются ни в БД (snapshot+events+raw_json), ни в `/api/*` ответах, ни в логах.
- Time-тесты §30: переход дня, DST-смещение (Europe/Berlin весной), Unix s vs ms, провайдерские offset'ы (+10:00), week-границы.
- README (RU) + `docs/R1_ANALYTICS_RU.md` (архитектура, формулы, API, env-переменные, сценарии demo).
- Прогон полного acceptance-чеклиста §36 и демо critical-сценария со скриншотом.

Итого ориентировочно: **8–10 рабочих дней** агента; поставка инкрементальная, после каждой фазы — зелёные тесты и рабочий сервис.

---

## 6. Новые/изменённые файлы (полный список)

```text
изменённые:
  app/models.py            (+3 аддитивных поля)
  app/store.py             (+2 таблицы, +методы серии/событий)
  app/collector.py         (+нормализация после save; _compute_trends остаётся для /api/status)
  app/main.py              (+5 эндпоинтов, +engine в lifespan)
  app/config.py            (+~20 настроек аналитики/алертов)
  app/demo.py              (переписан: сценарии + посев истории)
  app/static/index.html|app.js|style.css  (расширение; +chart.js)
  .env.example, Makefile, README.md
новые:
  app/normalize.py
  app/engine.py
  app/analytics/{__init__,series,burn_rate,forecast,pacing,runway,risk,recommendation,events,confidence,plans}.py
  config/plans.yaml.example
  docs/R1_ANALYTICS_RU.md
  tests/unit/test_series.py test_burn_rate.py test_forecast.py test_pacing.py
        test_runway.py test_risk.py test_recommendation.py test_events.py
        test_normalize.py test_confidence.py
  tests/integration/test_api_analytics.py test_scenarios.py test_restart_persistence.py
  tests/test_redaction.py tests/test_time_handling.py tests/fakes.py
```

Зависимостей новых не требуется (PyYAML уже в venv; регрессия — собственные 20 строк OLS).

---

## 7. Новые переменные окружения

```env
ANALYTICS_ENABLED=true
POLL_INTERVAL_SECONDS=60          # clamp снизу поднимается до 30
QUOTA_RETENTION_DAYS=0            # 0 = хранить всё (MVP)
DEMO_SCENARIO=normal              # normal|high_burn|weekly_exhaustion|critical
PLANS_CONFIG_PATH=./config/plans.yaml
# пороги алертов (§24)
ALERT_WARNING_USED=70  ALERT_HIGH_USED=85  ALERT_CRITICAL_USED=95
ALERT_WARNING_PROJECTED_WEEK=90  ALERT_CRITICAL_PROJECTED_WEEK=120
ALERT_CRITICAL_ETA_MINUTES=30     BALANCE_LOW_DAYS=7
# аналитические константы (разумные дефолты, менять не обязательно)
BURN_MIN_POINTS=3  BURN_MIN_SPAN_MINUTES=5  RESET_DROP_MIN_PP=5
ACCEL_BASELINE_MIN=1.0  WEEK_MIN_ELAPSED_PCT=3  RECOMMENDED_HEADROOM_FACTOR=1.25
EVENT_COOLDOWN_MINUTES=30
```

---

## 8. Мэппинг acceptance criteria §36 → план

| # | Критерий | Где закрывается |
|---|---|---|
| 1 | Collectors работают | M0 characterization-тесты; парсеры не меняются (кроме аннотаций) |
| 2 | История не теряется | M1 append-only `quota_snapshots`, restart-тест M6 |
| 3 | Reset без отрицательного burn | §4.2 + юнит «90/95/3» (§31) |
| 4 | Burn 15m/1h/3h | M2 burn_rate |
| 5 | ETA | M2 forecast |
| 6 | ETA vs reset | M2 survival_margin |
| 7 | survival_margin | M2 + API-контракт §28 |
| 8 | Weekly pacing | M2 pacing |
| 9 | Projected weekly | M2 projected×3 |
| 10 | Bottleneck detection | M3 risk |
| 11 | Risk score | M3 |
| 12 | Recommendation | M3 |
| 13 | Confidence | M2 confidence + UI «ETA …, confidence: medium» |
| 14 | Balance runway | M2 runway |
| 15 | Таблица bottlenecks | M5 |
| 16 | Unknown ≠ zero | правило §29 + специальный тест |
| 17 | Tests PASS | M0–M7, pytest зелёный |
| 18 | DEMO high-burn переход | M6 сценарии |
| 19 | Переживает рестарт | M6 restart-тест |
| 20 | Секреты нигде | M7 redaction-тест |

---

## 9. Тест-план

Юнит (§31, дословные случаи): linear burn 20→25→30% за час ⇒ 10 п.п./ч; reset 90/95/3 ⇒ без отрицательного burn + событие quota_reset; remaining 20%, burn 10%/h ⇒ ETA ровно 2h; ETA 4h/reset 2h ⇒ safe (низкий risk); ETA 1h/reset 3h ⇒ HIGH/CRITICAL; pace 50%/25% ⇒ 2.0x и projected ≈200%; missing data ⇒ `insufficient_data`, без exception и без нулей. Плюс: acceleration-полосы и guard малого baseline; confidence-полосы; OLS против двухточечной оценки на шумных данных; битые точки отбрасываются; разные reset_at не смешиваются; weekly-границы от reset_at при UTC+10; epoch s vs ms; dedup/cooldown событий; redaction.

Интеграция (§32): fake-responses 5 провайдеров + temp SQLite + TestClient; ускоренное время записью истории; матрица сценариев normal/rapid/reset/failure/recovery/balance-exhaustion/weekly-exhaustion; проверяются `/api/status`, `/api/analytics*`, `/api/events`, `/api/recommendations`, `/api/history`; рестарт-тест.

---

## 10. Риски и открытые решения (принятые по умолчанию, можно переопределить)

1. **`make unit` сломан вне venv** (системный python без httpx + unittest не видит function-tests) — чиню в M0 на pytest; это изменение Makefile, не продукта.
2. **Объём raw_json** при 60-секундном polling — оценка: <100 МБ/месяц для 5 провайдеров; retention по умолчанию выключен, лимитатор предусмотрен.
3. **OpenRouter estimated reset** — вычисленная граница может отличаться от реальной политики OR; помечаем `reset_estimated`, в margin используем, в подписи честно «estimated».
4. **Week start** определяется от провайдерского `reset_at`; если окну неделя без reset (теоретически) — fallback на первую наблюдаемую точку сегмента, pace помечается низкой confidence.
5. **Названия тарифов** — только из `plans.yaml` (source=configured) или от API; никаких захардкоженных цифр (§13).
6. R1 не делает: notification channels, авто-routing, probes, seasonality, cost-per-task — фиксируется как R2.

---

## 11. Что будет в финальном отчёте (§37)

Архитектурное описание, список изменённых файлов, команды запуска/тестов, результаты unit+integration, описание/скрин DEMO critical, известные ограничения, бэклог R2 — плюс самостоятельно поднятый сервис с проверенными API-ответами и dashboard.
