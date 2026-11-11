"""РП-действия между игроками: обнять, угостить, потанцевать. Каждое растит отношения (знакомые → друзья → лучшие друзья)."""
import random

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import database as db
import relations
from utils import display_name, esc

router = Router()
ANON = {1087968824, 136817688}

# команда: (глагол, иконка, очки)
ACTIONS = {
    "hug": ("обнял(а)", "🤗", 2), "five": ("дал(а) пять", "🙏", 1), "shake": ("пожал(а) руку", "🤝", 1),
    "treat": ("угостил(а)", "🍻", 2), "praise": ("похвалил(а)", "👏", 1), "pity": ("пожалел(а)", "🥺", 1),
    "dance": ("потанцевал(а) с", "💃", 2), "serenade": ("спел(а) серенаду для", "🎸", 2), "tease": ("по-дружески подколол(а)", "😜", 1),
    "salute": ("отдал(а) честь", "🫡", 1), "tea": ("налил(а) чаю", "🍵", 1), "toast": ("чокнулся(ась) с", "🥂", 1),
    "wave": ("помахал(а)", "👋", 1), "cheer": ("подбодрил(а)", "💪", 1), "listen": ("выслушал(а)", "👂", 1),
    "cook": ("приготовил(а) шаурму для", "🌯", 2), "carry": ("подхватил(а) на руки", "🦸", 2), "pat": ("погладил(а) по голове", "🫳", 1),
    "joke": ("рассказал(а) анекдот", "🤡", 1), "gift2": ("подарил(а) воздушный шарик", "🎈", 1),
}
ENDINGS = ["Мило!", "Чат растроган.", "Классика дружбы.", "Так держать!", "Все аплодируют.", "Записано в летопись чата."]


async def _target(message: Message, command: CommandObject):
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot and reply.from_user.id not in ANON:
        name = display_name(reply.from_user)
        await db.ensure_user(reply.from_user.id, message.chat.id, name)
        return reply.from_user.id, name
    token = next((a for a in (command.args or "").split() if a.startswith("@") and len(a) > 1), None)
    if token:
        found = await db.find_user_by_name(message.chat.id, token)
        if found:
            return found
    return None, None


@router.message(Command(*ACTIONS))
async def cmd_action(message: Message, command: CommandObject) -> None:
    if message.chat.type == "private" or not message.from_user or message.from_user.is_bot:
        await message.reply("РП-действия работают в групповом чате.")
        return
    verb, icon, pts = ACTIONS[command.command.lower()]
    tid, tname = await _target(message, command)
    if not tid:
        await message.reply(f"Ответь на сообщение человека или укажи @имя: <code>/{command.command} @username</code>")
        return
    me = message.from_user
    if tid == me.id:
        await message.reply("С собой так нельзя, найди друга 🙂")
        return
    await db.ensure_user(me.id, message.chat.id, display_name(me))
    res = await relations.add_points(message.chat.id, me.id, tid, pts)
    info = res["info"]
    text = f"{icon} <b>{esc(display_name(me))}</b> {verb} <b>{esc(tname)}</b>. {random.choice(ENDINGS)}"
    if res["gained"]:
        tail = f"\n{info['icon']} Отношения: <b>{info['stage']}</b> (+{res['gained']})"
        if info["next"]:
            tail += f", до «{info['next_name']}» ещё {info['next']}"
        text += tail
        if res["up"]:
            text += f"\n🎉 Теперь вы: <b>{info['stage']}</b>!"
    else:
        text += f"\n{info['icon']} Отношения: <b>{info['stage']}</b> (очки растут не чаще раза в 10 минут)"
    await message.reply(text)


@router.message(Command("rel", "relations"))
async def cmd_rel(message: Message, command: CommandObject) -> None:
    if message.chat.type == "private" or not message.from_user:
        return
    tid, tname = await _target(message, command)
    me = message.from_user
    if tid:
        info = await relations.get(message.chat.id, me.id, tid)
        extra = f", до «{info['next_name']}» ещё {info['next']}" if info["next"] else ", выше уже некуда"
        await message.reply(f"{info['icon']} Вы с <b>{esc(tname)}</b>: <b>{info['stage']}</b> ({info['pts']} очк.{extra})")
        return
    rows = await relations.top(message.chat.id, me.id)
    if not rows:
        await message.reply("Пока ни с кем нет отношений. Попробуй /hug, /treat или /dance в ответ на чьё-то сообщение.")
        return
    await message.reply("💞 <b>Твои отношения</b>\n" + "\n".join(f"{r['icon']} {esc(r['name'])} — {r['stage']} ({r['pts']} очк.)" for r in rows))
