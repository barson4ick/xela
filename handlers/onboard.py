"""Онбординг района: закреплённая памятка для граждан, полиции, мэра и Крёстного."""
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from keyboards import app_link

router = Router()

TEXT = """🧭 <b>КАК ИГРАТЬ В «РАЙОНЕ»</b>
Разделы: Работа · Дом · ОПГ · Полиция · Город · Новости. Сверху блок «🧭 Сейчас» подсказывает, что делать.

👤 <b>Житель</b>
• Работа и чёрные дела дают деньги и уличный авторитет (10 — можно в ОПГ или полицию).
• «Город → Жители районов»: выбери район и раз в 4 часа поддерживай банду или полицию.
• «Дом»: имущество, квартира, займы (берёшь сам, срок выбираешь сам), адвокат.

🕴 <b>ОПГ</b>
• Районы, налёты, склад и карта; у каждого района своя особенность.
• Босс договаривается о займах, заказах и союзах; проигрыш в суде ударит по всей банде.

🚓 <b>Полиция</b>
• Смены, патрули, допросы; капитана назначает мэр, а если мэра-игрока нет, посты занимает ИИ.
• Оперативная сводка показывает, на что уже хватает дела против банды.

🎩 <b>Мэр и 👑 Крёстный</b>
• Мэр — из полиции (выборы по воскресеньям), без игрока правит ИИ-мэр Архип.
• Крёстный берёт дань, стравливает банды, воюет с мэром или Архипом; кабинеты — в «Город → Власть района».

⚖️ <b>Суд</b>: адвокат, признание вины, конверт судье, показания свидетелей — у каждого свои последствия.

📱 Открыть мини-апп: /app"""


def _kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Открыть Район", url=app_link("rp", chat_id))]])


async def _send_onboard(message: Message, bot: Bot) -> None:
    if message.chat.type == "private":
        await message.answer(TEXT, reply_markup=_kb(message.chat.id))
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        await message.reply("Онбординг закрепляют админы. Вот памятка:", reply_markup=_kb(message.chat.id))
        return
    sent = await message.answer(TEXT, reply_markup=_kb(message.chat.id))
    try:
        await bot.unpin_all_chat_messages(message.chat.id)
    except Exception:
        pass
    try:
        await bot.pin_chat_message(message.chat.id, sent.message_id, disable_notification=True)
    except Exception:
        pass
    # сама команда не должна висеть в чате
    try:
        await message.delete()
    except Exception:
        pass


@router.message(Command("onboard", "start_here"))
async def cmd_onboard(message: Message, bot: Bot) -> None:
    await _send_onboard(message, bot)
