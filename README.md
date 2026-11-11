# xela

Шуточный Telegram-бот для дружеской группы и мини-приложение «Район»: ролевая игра про район с работой, бандами, полицией, мэром, Крёстным отцом, судом и событиями.

## Состав

- `main.py` — бот (aiogram 3) и фоновые задачи (`jobs.py`).
- `webapp.py` — сервер мини-приложения (aiohttp), статика в `webapp/static` (`app.js`, `style.css`, `index.html`).
- `rp.py`, `gang*.py`, `police*.py`, `power.py`, `control.py`, `justice.py`, `residents.py`, `debts.py`, `doom.py` и другие — игровые механики.
- `handlers/` — команды бота.
- `tools/` — служебные скрипты (`purge_player.py`, `doom_start.py`).

## Запуск

Нужен Python 3.12.

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
BOT_TOKEN=... WEBAPP_URL=https://ваш-домен venv/bin/python main.py     # бот
BOT_TOKEN=... WEBAPP_URL=https://ваш-домен venv/bin/python webapp.py   # мини-приложение
```

Оба процесса работают с одной базой SQLite (`DB_PATH`, по умолчанию `bot_database.sqlite3` рядом с кодом); таблицы создаются при старте.

Переменные окружения: `BOT_TOKEN` (обязательна), `WEBAPP_URL`, `BOT_USERNAME`, `DB_PATH`, `BOT_TZ`, `OWNER_ID` (кому слать ежедневный отчёт экономики), `GEMINI_API_KEY`, `GIPHY_API_KEY`, `ANTHROPIC_API_KEY`, `AI_CHAT`, `APP_SHORTNAME`, `MAIN_APP`.

## Чего здесь нет

База данных, резервные копии, секреты и внутренняя документация с деталями сервера в репозиторий не входят. Идентификаторы чата и игроков из кода убраны, поэтому служебные скрипты принимают их через аргументы и окружение.
