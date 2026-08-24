# AI Provider Observer

Мини-сервис для локального контроля квот и балансов AI-провайдеров. Он ничего не проксирует и не отправляет пользовательские промпты: только опрашивает usage/balance endpoints и сохраняет нормализованные snapshots в SQLite.

Поддержано в MVP:

- **Z.AI Coding Plan** — 5h + weekly, used %, remaining, reset; поддержаны текущий credit endpoint и legacy monitor endpoint.
- **MiniMax Token/Coding Plan** — 5h + weekly из `token_plan/remains`.
- **DeepSeek API** — фактический PAYG balance (CNY/USD). У DeepSeek нет аналогичной публичной 5h/weekly подписочной квоты API.
- **OpenRouter** — usage текущего API key; если на key задан daily/weekly/monthly limit, показывается процент и reset. Опциональный Management key добавляет account-wide purchased/used credits.
- **OpenAI Codex (ChatGPT plan)** — 5h/weekly/credits через тот же локальный ChatGPT login, который использует Codex CLI (`~/.codex/auth.json`). Endpoint внутренний и может измениться.

Также сервис считает скорость изменения процента квоты/баланса по собственной истории и показывает latency самого quota/balance запроса.

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

- `GET /` — dashboard.
- `GET /api/status` — последние нормализованные snapshots + тренды.
- `POST /api/refresh` — немедленный опрос всех провайдеров.
- `GET /healthz` — health check.

SQLite по умолчанию: `./data/observer.db`. Сохраняются только нормализованные usage/balance данные, **не секреты и не сырые upstream responses**.

## 5. Что означает «скорость»

В этой версии без дополнительных платных запросов показываются:

- `check N ms` — latency запроса к quota/balance endpoint;
- `+X п.п./ч` — изменение использованной квоты в процентных пунктах в час;
- `X currency/ч` — скорость уменьшения PAYG balance.

Активный TPS/TTFT benchmark моделей намеренно не включён по умолчанию: такой probe сам расходует квоту и может исказить измерение. Его разумно добавить отдельным opt-in модулем с редким интервалом и явно выбранной моделью.

## 6. Тесты

```bash
python -m pytest -q
```

Парсерные тесты покрывают реальные формы ответов Z.AI credit windows, MiniMax remaining-percent, DeepSeek balance, OpenRouter key limit и Codex 5h/week.

## 7. Безопасность

- не публикуйте `.env`;
- слушатель по умолчанию привязан к `127.0.0.1`;
- не открывайте порт 8787 в интернет без reverse proxy + authentication;
- `auth.json` Codex содержит OAuth credentials — монтируйте его только read-only;
- redirect following отключён для upstream usage calls, чтобы Authorization header не ушёл на другой host через redirect.
