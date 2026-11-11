import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent

# Секреты приходят из переменных окружения (systemd EnvironmentFile=/etc/tgbot.env, chmod 600)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN (см. /etc/tgbot.env)")

# Необязательно: ключ Anthropic для «язвительного друга» (ответы на упоминание бота)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "claude-haiku-5-5")

# Giphy (developers.giphy.com): если задан, ИИ может прикладывать к ответам гифки
GIPHY_API_KEY = os.environ.get("GIPHY_API_KEY", "")
GIPHY_BASE_URL = os.environ.get("GIPHY_BASE_URL", "https://api.giphy.com/v1")

# Google Gemini (ключ из Google AI Studio). Если задан, используется вместо Claude
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

# Абсолютный путь к файлу базы данных SQLite
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "bot_database.sqlite3"))

# Часовой пояс чата: от него зависят «дневные» фичи и недельный дайджест
TZ = ZoneInfo(os.environ.get("BOT_TZ", "Europe/Moscow"))

# Константы кулдаунов (в секундах)
DICK_COOLDOWN = 10800  # 3 часа (10 800 секунд)
ROB_COOLDOWN = 21600   # 6 часов (21 600 секунд)

# Мини-апп (HTTPS-адрес за nginx) и имя бота для ссылок t.me
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://example.com")
AI_CHAT = os.environ.get("AI_CHAT", "0") == "1"
BOT_USERNAME = os.environ.get("BOT_USERNAME", "kxela_bot")

# Короткое имя мини-аппа из BotFather (/newapp): если задано, кнопки в чате открывают апп прямо поверх чата
APP_SHORTNAME = os.environ.get("APP_SHORTNAME", "")
# Основной мини-апп бота задан в BotFather (/mainapp): ссылка t.me/<бот>?startapp=… открывает его прямо из группы
MAIN_APP = os.environ.get("MAIN_APP", "1") == "1"
