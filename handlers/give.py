import re

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database import ensure_user, find_user_by_name, give_size
from utils import display_name, esc

router = Router()

USAGE = (
    "🎁 <b>Передать свои см другому</b>\n"
    "• Ответь на сообщение человека: <code>/give 30</code>\n"
    "• Или по имени: <code>/give @username 30</code>\n"
    "Сумма любая, но не больше, чем у тебя есть. Передавать можно раз в 2 часа."
)


@router.message(Command("give", "gift"))
async def cmd_give(message: Message, command: CommandObject) -> None:
    if message.chat.type == "private":
        await message.reply("Передавать см можно только в групповом чате.")
        return
    sender = message.from_user
    args = (command.args or "").split()
    amount, target_id, target_name = None, None, None

    for a in args:
        if re.fullmatch(r"\d+", a):
            amount = int(a)
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        target_id, target_name = reply.from_user.id, display_name(reply.from_user)
        await ensure_user(target_id, message.chat.id, target_name)
    else:
        name = next((a for a in args if a.startswith("@")), None)
        if name:
            found = await find_user_by_name(message.chat.id, name)
            if found:
                target_id, target_name = found

    if amount is None or target_id is None:
        await message.reply(USAGE)
        return

    await ensure_user(sender.id, message.chat.id, display_name(sender))
    status, size, wait = await give_size(message.chat.id, sender.id, target_id, amount)
    if status == "ok":
        await message.reply(
            f"🎁 <b>{esc(display_name(sender))}</b> подарил <b>{esc(target_name)}</b> {amount} см!\n"
            f"У тебя осталось {size} см."
        )
    elif status == "cooldown":
        await message.reply(f"⏳ Передавать можно раз в 2 часа. Подожди ещё {(wait + 59) // 60} мин.")
    elif status == "funds":
        await message.reply(f"У тебя только {size} см, больше передать не получится.")
    elif status == "self":
        await message.reply("Себе передавать незачем 🙂")
    elif status == "amount":
        await message.reply("Сумма должна быть от 1 см.")
    else:
        await message.reply("Не нашёл такого игрока в этом чате. Пусть он хоть раз напишет здесь.")
