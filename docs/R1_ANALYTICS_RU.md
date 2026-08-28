# R1 Consumption Intelligence — архитектура аналитики

Документ описывает аналитический слой R1: как из истории snapshot'ов считаются burn rate,
ETA, survival margin, weekly pacing, runway, risk score и рекомендации.

Жёсткое правило системы: **collectors не прогнозируют**. Они только собирают объективные
snapshot'ы. Вся математика — в `app/analytics/` по истории таблицы `quota_snapshots`.
Observer никогда не генерирует трафик ради измерения расхода.

---

## 1. Поток данных

```text
providers → Collector.collect()
    ├─► snapshots          (сырой журнал, без изменений, legacy)
    ├─► quota_snapshots    (нормализованные ряды: строка на окно за poll, raw_json)
    └─► AnalyticsEngine.refresh_all()
            series → burn_rate → forecast/pacing/runway
                   → risk → recommendation → events
        └─► кэш в памяти; REST читает кэш
```

- Перезапуск безопасен: аналитика пересчитывается из `quota_snapshots`.
- Сбой записи истории не ломает сбор (try/except вокруг persist).

## 2. Нормализация окон

| Имя от collector | window_type | Единицы |
|---|---|---|
| `5h` | `five_hour` | credits/tokens или % |
| `week`, `weekly` | `weekly` | credits/USD/% |
| `daily` | `daily` | USD/% |
| `monthly` | `monthly` | USD/% |
| балансы провайдеров | `balance` | валюта |
| Codex credits | `credits` | USD |
| прочее | `unknown` | — |

Абсолютные значения приоритетны (Z.AI credits, OpenRouter USD); если есть только проценты —
`percentage_points_per_hour`. Неизвестное значение никогда не заменяется нулём:
`null` + `status="insufficient_data"`.

## 3. Reset и сегментация

Точка классифицируется как граница окна (`quota_reset`), когда одновременно:

- `drop = prev.used_percent − cur.used_percent > RESET_JITTER_PP` (2 п.п.);
- и есть признак reset: `reset_at` изменился **или** наступило время старого `reset_at`.

Снижение used без признаков reset — подозрительная точка: исключается из регрессий,
сегмент продолжается. Burn через границу reset не считается никогда.
Для `balance`/`credits` окон reset-логика отключена (падения — нормальный расход).

## 4. Burn rate (регрессия, не две точки)

Для каждого окна считаются скорости OLS-регрессией `y=used`, `x=elapsed_hours`
по текущему сегменту:

| Ключ | Окно | Применимость |
|---|---|---|
| `10m` | все | всегда — мелкая дискретизация: последние ~10 минут расхода |
| `15m` (`burn_short`) | все | всегда |
| `1h` (`burn_medium`) | все | всегда |
| `3h` (`burn_long`) | все | всегда |
| `24h`, `3d` | weekly | дополнительно |
| `window` | все | от первого наблюдения сегмента |

Guards: ≥3 валидных точки, span ≥5 минут; иначе `insufficient_data`.

**Acceleration** = `burn_15m / burn_1h`; считается только при baseline
`|burn_1h| ≥ ACCEL_BASELINE_MIN`. Полосы: `<0.7` decelerating · `0.7–1.3` stable ·
`1.3–2.0` accelerating · `>2.0` anomaly.

## 5. ETA и survival margin

```
eta_current      ← burn_15m        eta_stable   ← burn_1h
eta_conservative ← burn_3h         eta_X = remaining / burn_X
eta_short        ← burn_10m        (реакция на burst в пределах ~10 минут)
survival_margin  = eta_current − time_to_reset      (< 0 ⟹ закончится ДО reset)
survival_margin_short = eta_short − time_to_reset  (< 0 ⟹ текущий burst не дотягивает до reset)
```

- `recovery_mode`: `hard_reset` — reset гарантирован API; `estimated_reset` — вычислен
  (OpenRouter); `unknown` — rolling/неизвестен (margin тогда не показывается).
- Confidence по длине истории сегмента: `<15м` LOW · `15м–2ч` MEDIUM · `>2ч` HIGH.
  `confidence_short` считается по span самой регрессии `burn_10m` (а не всей истории),
  поэтому короткое 10-минутное окно не заимствует доверие длинной истории.

## 6. Weekly pacing и прогноз конца недели

```
week_start  = reset_at − 7d           elapsed = (now − week_start)/7d
pace_ratio  = used% / elapsed%        (elapsed < WEEK_MIN_ELAPSED_PCT → insufficient)
projected_whole_window = used% / (elapsed/100)          ← headline
projected_pace_24h     = used% + burn_24h·hours_left
projected_pace_3d      = used% + burn_3d·hours_left
```

Полосы pace: `<0.8` comfortable · `0.8–1.1` normal · `1.1–1.4` elevated ·
`1.4–2.0` critical · `>2.0` unsustainable. Границы недели — от провайдерского
`reset_at`, не от локального понедельника.

## 7. Денежный runway (balance/credits)

Регрессия по балансу: `usd_per_day` (окно 24h, fallback 7d), `runway_days =
balance / usd_per_day`, `projected_monthly_spend = usd_per_day × 30.44`.
Пополнение (spend ≤ 0) → runway неизвестен, а не бесконечность.
Account balance и key-budget OpenRouter — разные окна и не смешиваются.

## 8. Risk score и bottleneck

Факторы окна: `f_remaining` · `f_margin` · `f_accel` · `f_pace` · `f_projected`;
скор окна = max факторов (+бонус при двух факторах ≥55). Провайдер:
`score = max(окон) + error_penalty` (cap 100); уровень: 0–29 HEALTHY · 30–49 WATCH ·
50–69 WARNING · 70–84 HIGH · 85–100 CRITICAL.

Bottleneck — argmax по окнам (при равенстве приоритет five_hour > weekly > monthly >
daily > balance); доминирующие ошибки API → `errors`.

Пороги алертов §24 — в конфиге (`ALERT_*`): WARNING `used≥70 OR projected≥90`,
HIGH `used≥85 OR margin<0`, CRITICAL `used≥95 OR ETA≤30мин OR projected≥120`.

## 9. Рекомендации и тарифы

```
required_capacity_ratio    = max(1, projected_headline/100)
recommended_capacity_ratio = required × RECOMMENDED_HEADROOM_FACTOR (1.25)
```

Действия: `NO_ACTION | WATCH | REDUCE_LOAD | SHIFT_TRAFFIC | INCREASE_BUDGET | UPGRADE_PLAN`.
Каждая рекомендация содержит reason с конкретными числами. Названия планов — только из
`config/plans.yaml` (`source=configured`) или от API провайдера; пустых/выдуманных цифр нет.
Cross-provider SHIFT_TRAFFIC — текстовая рекомендация, автороутинга нет.

## 10. События

Типы: `quota_reset, high_burn, quota_warning, quota_critical, predicted_exhaustion,
balance_low, provider_error, provider_recovered, tariff_insufficient`.

События создаются **при переходах** состояний (например WARNING→CRITICAL) с dedup_key
и cooldown `EVENT_COOLDOWN_MINUTES` (30 мин по умолчанию). Одно и то же событие
не генерируется каждую минуту.

## 11. Конфигурация

Смотри `.env.example`: пороги `ALERT_*`, константы регрессий `BURN_*`, `RESET_*`,
cooldown событий, `PLANS_CONFIG_PATH`, `QUOTA_RETENTION_DAYS` (0 = хранить всё),
`DEMO_SCENARIO` (`normal|high_burn|weekly_exhaustion|critical`).
Polling — не быстрее 30 секунд; snapshot сохраняется даже без изменений квоты.
