import time
from typing import Dict

import aiosqlite
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from config import DB_PATH
from utils import esc

router = Router()

COOLDOWN = 600  # секунд между созывами в одном чате
CHUNK = 5
_last: Dict[int, float] = {}


async def chat_members(chat_id: int, skip: int) -> list[tuple[int, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, username FROM users WHERE chat_id = ? AND user_id != ?",
            (chat_id, skip),
        )
        return await cur.fetchall()


async def call_all(message: Message) -> None:
    if message.chat.type == "private":
        await message.reply("Созывать некого, мы тут вдвоём 🙂 Это работает в группе.")
        return
    left = COOLDOWN - (time.time() - _last.get(message.chat.id, 0))
    if left > 0:
        await message.reply(f"Только что звали. Подожди ещё {int(left // 60) + 1} мин.")
        return
    me = await message.bot.me()
    members = [m for m in await chat_members(message.chat.id, message.from_user.id) if m[0] != me.id]
    if not members:
        await message.reply("Я пока никого не знаю в этом чате. Пусть люди хоть раз напишут.")
        return
    _last[message.chat.id] = time.time()
    # текст после команды/тега — повод для созыва
    text = (message.text or message.caption or "").split(maxsplit=1)
    reason = text[1].strip() if len(text) > 1 else ""
    head = f"📢 {esc(message.from_user.full_name)} зовёт всех" + (f": {esc(reason[:200])}" if reason else "!")
    await message.reply(head)
    for i in range(0, len(members), CHUNK):
        part = members[i:i + CHUNK]
        line = " ".join(f'<a href="tg://user?id={uid}">{esc(name.lstrip("@") or "друг")}</a>' for uid, name in part)
        await message.answer(line)


@router.message(Command("all", "alldick", "everyone", "tagall"))
async def cmd_all(message: Message) -> None:
    await call_all(message)


@router.message(F.text.regexp(r"(?i)(^|\s)@(alldick|all|everyone)\b"))
async def tag_all(message: Message) -> None:
    await call_all(message)
