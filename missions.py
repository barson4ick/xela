"""Тайные миссии дня: задание видит только взявший (всплывающее окно), вечером раскрываем, кто что выполнил."""
import json
import logging
import random
import re
import time

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message

import database as db
import rp
from config import DB_PATH
from utils import day_str, esc, now_local

log = logging.getLogger(__name__)
router = Router()

POST_HOUR = 10
REVEAL_HOUR = 22
REWARD_RUB = 500
REWARD_CM = 1
WORDS = ["картошка", "шаурма", "тапок", "пельмень", "банан", "самовар", "огурец", "валенок"]
AVOID = ["ну", "короче", "типа", "блин"]
LAUGH = re.compile(r"(ха){2,}|хех|ахах|😂|🤣|лол|lol", re.I)

# вид → (шаблон текста, сколько нужно)
KINDS = {
    "word": ("Заставь {need} разных людей написать слово «{arg}» в чате (сам не считаешься)", 2),
    "replies": ("Получи ответы (reply) на свои сообщения от {need} разных людей", 3),
    "laugh": ("Рассмешить {need} разных людей: в ответ на твои сообщения должны написать «хаха», «ахах» или 😂", 2),
    "mention": ("Заставь {need} разных людей упомянуть тебя по @нику", 2),
    "avoid": ("Целый день ни разу не напиши слово «{arg}». Выдержишь?", 1),
}


def _text(kind: str, arg: str, need: int) -> str:
    return KINDS[kind][0].format(need=need, arg=arg)


async def _assign(conn, chat_id: int, uid: int, name: str) -> dict:
    day = day_str()
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM mission WHERE day = ? AND chat_id = ? AND uid = ?", (day, chat_id, uid))
    row = await cur.fetchone()
    if row:
        return dict(row)
    kinds = list(KINDS)
    if not name.startswith("@"):
        kinds.remove("mention")  # упоминать можно только по @нику
    kind = random.choice(kinds)
    arg = random.choice(WORDS) if kind == "word" else random.choice(AVOID) if kind == "avoid" else ""
    need = KINDS[kind][1]
    await conn.execute("INSERT INTO mission (day, chat_id, uid, name, kind, arg, need, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (day, chat_id, uid, name, kind, arg, need, int(time.time())))
    await conn.commit()
    return {"day": day, "chat_id": chat_id, "uid": uid, "name": name, "kind": kind, "arg": arg, "need": need, "seen": "[]", "failed": 0}


def _done(m: dict) -> bool:
    if m["kind"] == "avoid":
        return not m["failed"]
    return len(json.loads(m["seen"])) >= m["need"]


async def on_message(message: Message) -> None:
    """Вызывается на каждое сообщение группы: двигает прогресс активных миссий."""
    if not message.from_user or message.from_user.is_bot:
        return
    text = (message.text or message.caption or "").lower()
    if not text:
        return
    chat_id, sender, day = message.chat.id, message.from_user.id, day_str()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM mission WHERE day = ? AND chat_id = ?", (day, chat_id))
        rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            return
        replied_to = message.reply_to_message.from_user.id if message.reply_to_message and message.reply_to_message.from_user else None
        for m in rows:
            seen = json.loads(m["seen"])
            changed = False
            if m["kind"] == "avoid":
                if sender == m["uid"] and not m["failed"] and re.search(rf"(?<!\w){re.escape(m['arg'])}(?!\w)", text):
                    await conn.execute("UPDATE mission SET failed = 1 WHERE day = ? AND chat_id = ? AND uid = ?", (day, chat_id, m["uid"]))
                continue
            if sender == m["uid"] or sender in seen:
                continue
            hit = False
            if m["kind"] == "word":
                hit = m["arg"] in text
            elif m["kind"] == "replies":
                hit = replied_to == m["uid"]
            elif m["kind"] == "laugh":
                hit = replied_to == m["uid"] and bool(LAUGH.search(text))
            elif m["kind"] == "mention":
                hit = m["name"].lower() in text
            if hit:
                seen.append(sender)
                changed = True
            if changed:
                await conn.execute("UPDATE mission SET seen = ? WHERE day = ? AND chat_id = ? AND uid = ?", (json.dumps(seen), day, chat_id, m["uid"]))
        await conn.commit()


def _kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🕵 Взять тайную миссию", callback_data="msn:take")]])


async def post(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_message(
            chat_id,
            "🕵 <b>Тайные миссии дня!</b>\nНажми кнопку, и бот покажет тебе секретное задание, которое видишь только ты (всплывающим окном). "
            f"Выполни его незаметно: вечером в {REVEAL_HOUR}:00 раскроем, кто что делал. Награда: {REWARD_RUB} ₽ и +{REWARD_CM} см.\n"
            "<i>Берут все, кто хочет. Задание можно посмотреть повторно той же кнопкой.</i>",
            reply_markup=_kb(),
        )
    except TelegramAPIError as exc:
        log.warning("Миссии не объявлены: %s", exc)


@router.callback_query(F.data == "msn:take")
async def cb_take(cb: CallbackQuery) -> None:
    if not cb.message:
        await cb.answer()
        return
    chat_id, uid = cb.message.chat.id, cb.from_user.id
    name = await _display(chat_id, uid, cb)
    async with aiosqlite.connect(DB_PATH) as conn:
        m = await _assign(conn, chat_id, uid, name)
    prog = ""
    if m["kind"] != "avoid":
        prog = f"\nСделано: {len(json.loads(m['seen']))}/{m['need']}"
    elif m["failed"]:
        prog = "\nТы уже сорвался."
    await cb.answer(f"🕵 {_text(m['kind'], m['arg'], m['need'])}{prog}", show_alert=True)


async def _display(chat_id: int, uid: int, cb: CallbackQuery) -> str:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT username FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        r = await cur.fetchone()
    if r and r[0]:
        return r[0]
    return ("@" + cb.from_user.username) if cb.from_user.username else cb.from_user.full_name


@router.message(Command("mission", "missiya"))
async def cmd_mission(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    await post(message.bot, message.chat.id)


async def reveal(bot: Bot, chat_id: int) -> None:
    day = day_str()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM mission WHERE day = ? AND chat_id = ?", (day, chat_id))
        rows = [dict(r) for r in await cur.fetchall()]
    if not rows:
        return
    lines = []
    for m in rows:
        ok = _done(m)
        if ok:
            async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
                await rp._load(conn, chat_id, m["uid"])
                await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (REWARD_RUB, chat_id, m["uid"]))
                await conn.commit()
            await db.add_size(m["uid"], chat_id, m["name"], REWARD_CM, "mission")
        lines.append(f"{'✅' if ok else '❌'} <b>{esc(m['name'])}</b>: {esc(_text(m['kind'], m['arg'], m['need']))}")
    done = sum(1 for m in rows if _done(m))
    await bot.send_message(chat_id, f"🕵 <b>Тайные миссии раскрыты!</b> Выполнили {done} из {len(rows)}.\n\n" + "\n".join(lines) +
                           (f"\n\nПобедителям: +{REWARD_RUB} ₽ и +{REWARD_CM} см." if done else "\n\nНикто не справился. Позор шпионам."))


async def tick(bot: Bot, chats) -> None:
    n = now_local()
    for chat_id in chats:
        if POST_HOUR <= n.hour < REVEAL_HOUR and not await db.kv_get(f"msn_post:{day_str()}:{chat_id}"):
            if await db.total_messages(chat_id, day_str(-6)) > 0:
                await db.kv_set(f"msn_post:{day_str()}:{chat_id}", "1")
                await post(bot, chat_id)
        if n.hour >= REVEAL_HOUR and await db.kv_get(f"msn_post:{day_str()}:{chat_id}") and not await db.kv_get(f"msn_rev:{day_str()}:{chat_id}"):
            await db.kv_set(f"msn_rev:{day_str()}:{chat_id}", "1")
            try:
                await reveal(bot, chat_id)
            except TelegramAPIError as exc:
                log.warning("Миссии не раскрыты: %s", exc)
