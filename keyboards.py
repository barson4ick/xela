from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import APP_SHORTNAME, BOT_USERNAME, MAIN_APP

QUICK = {
    "top": ("🏆 Топ", "act:top"),
    "daily": ("🎁 Бонус", "act:daily"),
    "dick": ("📏 Замер", "act:dick"),
}


def app_link(tab: str = "", chat_id: int = 0) -> str:
    """Ссылка, открывающая мини-апп поверх чата: вкладка и чат передаются в параметре startapp."""
    param = (tab or "rp") + (f"_c{abs(chat_id)}" if chat_id else "")
    if APP_SHORTNAME:
        return f"https://t.me/{BOT_USERNAME}/{APP_SHORTNAME}?startapp={param}"
    if MAIN_APP:
        # Основной мини-апп из BotFather: эта ссылка открывает его сразу, без захода в чат бота
        return f"https://t.me/{BOT_USERNAME}?startapp={param}"
    return f"https://t.me/{BOT_USERNAME}?start=app"


def app_link_button(text: str = "📱 Мини-апп", tab: str = "", chat_id: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=app_link(tab, chat_id))


def quick_kb(*names: str) -> InlineKeyboardMarkup:
    """Кнопки быстрых действий под ответами бота + вход в мини-апп."""
    row = [InlineKeyboardButton(text=QUICK[n][0], callback_data=QUICK[n][1]) for n in names]
    row.append(app_link_button())
    return InlineKeyboardMarkup(inline_keyboard=[row])
