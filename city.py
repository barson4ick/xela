"""Команды города: /opg (банды), /gazeta (газета), очередь сообщений из мини-аппа."""
import io
import logging
import random
import re
import time

import aiosqlite
from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import gangwar
import paper
from config import DB_PATH
from database import kv_get, kv_set

log = logging.getLogger(__name__)
router = Router()


async def flush_outbox(bot: Bot) -> None:
    """Больше не пишет в чат: переносит события в ленту мини-аппа (rp_news)."""
    import rp
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT id, chat_id, text FROM outbox ORDER BY id LIMIT 40")
        rows = await cur.fetchall()
        for oid, chat_id, text in rows:
            clean = re.sub(r"<[^>]+>", "", str(text or "")).strip()
            if clean:
                await rp._news(conn, int(chat_id), clean)
            await conn.execute("DELETE FROM outbox WHERE id = ?", (oid,))
            await conn.commit()


@router.message(Command("opg", "gangs", "banda"))
async def cmd_opg(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    await message.reply(await gangwar.overview_text(message.chat.id))


@router.message(Command("gazeta", "paper"))
async def cmd_paper(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    key = f"paper_cmd:{message.chat.id}"
    if time.time() - float(await kv_get(key) or 0) < 3600:
        await message.reply("Свежий выпуск уже выходил, типография отдыхает. Заходите позже.")
        return
    text = await paper.build(message.chat.id)
    if not text:
        await message.reply("Пока в районе тишина, печатать нечего. Совершите что-нибудь криминальное!")
        return
    await kv_set(key, str(time.time()))
    await message.answer(text)
