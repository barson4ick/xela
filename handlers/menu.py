from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

router = Router()

CATEGORIES = {
    "cm": (
        "📏 Сантиметры",
        "📏 <b>Сантиметры</b>\n\n"
        "/dick — замер (раз в 3 часа)\n"
        "/top — рейтинг чата\n"
        "/rob — ограбить (reply на сообщение)\n"
        "/daily — ежедневный бонус, серия растёт\n"
        "/casino 3 — слоты на сантиметры\n"
        "/lottery — ежедневная лотерея (розыгрыш в 21:00)",
    ),
    "games": (
        "⚔️ Игры",
        "⚔️ <b>Игры</b>\n\n"
        "/duel — дуэль (reply на сообщение)\n"
        "/trivia — викторина\n"
        "/mafia — мафия-лайт (от 4 игроков), /mafiastop — остановить\n"
        "/sud @имя обвинение — суд чата\n"
        "/bank — ограбление банка всем чатом (само стартует вечером)\n"
        "/opg — банды, войны и районы города\n"
        "/mission — тайные миссии дня (выходят в 10:00, итоги в 22:00)\n"
        "Мафия и покер — в мини-аппе, вкладка «Арена»",
    ),
    "fun": (
        "🎭 Развлечения",
        "🎭 <b>Развлечения</b>\n\n"
        "/hero — герой дня\n"
        "/future — мем-предсказание\n"
        "/gazeta — газета «Вечерний Район»\n"
        "/brak (reply) — предложить брак, /semya — семья, /vznos и /snyat — общий счёт, /rebenok, /razvod",
    ),
    "chat": (
        "💬 Чат",
        "💬 <b>Чат</b>\n\n"
        "/quote — случайная цитата; reply сохраняет цитату\n"
        "/question — вопрос дня (выходит в 11:00, ответ — +1 см)\n"
        "Итоги недели публикую сам — по воскресеньям в 20:00\n"
        "/app — мини-апп: рейтинг, казино, арена, профиль\n"
        "/onboard — онбординг: как играть за все стороны\n"
        "/status — твой градус и статус\n"
        "/give 30 — подарить см (ответом на сообщение или /give @ник 30), раз в 2 часа\n"
        "/all или @alldick — позвать всех (раз в 10 мин)\n"
        "«+» или «-» ответом на сообщение — карма\n"
        "Упомяни меня или ответь на моё сообщение — отвечу",
    ),
}


def _main_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=title, callback_data=f"menu:{key}")
        for key, (title, _) in CATEGORIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[:2], buttons[2:]])


MAIN_TEXT = "🤖 <b>Меню</b>\nВыбери раздел:"


@router.message(Command("menu", "help"))
async def cmd_menu(message: Message) -> None:
    await message.reply(MAIN_TEXT, reply_markup=_main_kb())


@router.callback_query(F.data.startswith("menu:"))
async def handle_menu(callback: CallbackQuery) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    key = callback.data.split(":", 1)[1]
    try:
        if key == "back":
            await callback.message.edit_text(MAIN_TEXT, reply_markup=_main_kb())
        elif key in CATEGORIES:
            back = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="menu:back")]]
            )
            await callback.message.edit_text(CATEGORIES[key][1], reply_markup=back)
    except TelegramAPIError:
        pass
    await callback.answer()
