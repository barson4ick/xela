from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import kv_get, kv_set

router = Router()


@router.message(Command("remind"))
async def cmd_remind(message: Message) -> None:
    if message.chat.type != "private":
        await message.reply("Напоминания приходят в личку. Напиши мне /remind там.")
        return
    key = f"remind_off:{message.from_user.id}"
    if await kv_get(key):
        await kv_set(key, "")
        await message.answer("🔔 Напоминания о серии бонуса включены.")
    else:
        await kv_set(key, "1")
        await message.answer("🔕 Напоминания выключены. Вернуть: /remind.")
