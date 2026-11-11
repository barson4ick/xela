"""Фоновая жизнь бота: случайное событие дня и общий квест на весь чат."""
import asyncio
import hashlib
import json
import logging
import random
import time
from typing import Optional

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database as db
import highlights
from config import DB_PATH
from keyboards import app_link_button
from utils import day_str, esc, now_local

log = logging.getLogger(__name__)

EVENT_FROM, EVENT_TO = 12, 21      # событие дня происходит в случайный час этого окна
QUEST_FROM = 10                    # квест на всех появляется с 10:00
QUEST_REWARD = 2                   # см каждому, кто помог
TREASURE_REWARD = 3
TREASURE_TTL = 1800
EFFECT_TTL = 3600
EVENT_TYPES = ["free_bar", "double_pay", "tax", "treasure"]
EFFECT_TITLE = {"free_bar": "🍻 Счастливый час: бар бесплатный", "double_pay": "💼 Рабочий аврал: оплата ×2"}

# (ключ, иконка, что нужно сделать, минимум цели, максимум цели)
QUESTS = [
    ("rp", "💼", "совершить {n} действий в «Районе» (работа и дела)", 25, 40),
    ("poker", "🃏", "сыграть {n} раздач в покер", 8, 14),
    ("cmd", "⌨️", "использовать {n} команд бота в чате", 40, 70),
]
_treasure_lock = asyncio.Lock()


def _hash(chat_id: int, day: str, salt: str) -> int:
    return int(hashlib.md5(f"{chat_id}:{day}:{salt}".encode()).hexdigest(), 16)


# ───────────── активные эффекты ─────────────

async def active(chat_id: int, kind: str) -> bool:
    raw = await db.kv_get(f"cev:{chat_id}")
    if not raw:
        return False
    try:
        d = json.loads(raw)
    except ValueError:
        return False
    return d.get("type") == kind and d.get("until", 0) > time.time()


async def current_effect(chat_id: int) -> Optional[dict]:
    raw = await db.kv_get(f"cev:{chat_id}")
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    if d.get("until", 0) > time.time() and d.get("type") in EFFECT_TITLE:
        return {"type": d["type"], "title": EFFECT_TITLE[d["type"]], "left": int(d["until"] - time.time())}
    return None


# ───────────── общий квест ─────────────

async def bump(chat_id: int, user_id: int, kind: str, n: int = 1) -> None:
    """Засчитывает действие в квест дня; никогда не мешает игре."""
    try:
        raw = await db.kv_get(f"cq:{chat_id}:{day_str()}")
        if not raw:
            return
        q = json.loads(raw)
        if q.get("kind") != kind or q.get("done"):
            return
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                """INSERT INTO cq_prog (chat_id, day, user_id, n) VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, day, user_id) DO UPDATE SET n = n + excluded.n""",
                (chat_id, day_str(), user_id, n),
            )
            await conn.commit()
    except Exception:
        log.exception("Не удалось засчитать квест")


async def _progress(chat_id: int, day: str, uid: int = 0) -> tuple:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT COALESCE(SUM(n), 0) FROM cq_prog WHERE chat_id = ? AND day = ?", (chat_id, day))
        total = (await cur.fetchone())[0]
        mine = 0
        if uid:
            cur = await conn.execute("SELECT n FROM cq_prog WHERE chat_id = ? AND day = ? AND user_id = ?", (chat_id, day, uid))
            row = await cur.fetchone()
            mine = row[0] if row else 0
    return total, mine


async def view(chat_id: int, uid: int) -> dict:
    """Данные для мини-аппа: квест на всех и текущий эффект."""
    out = {"quest": None, "effect": await current_effect(chat_id)}
    raw = await db.kv_get(f"cq:{chat_id}:{day_str()}")
    if raw:
        q = json.loads(raw)
        total, mine = await _progress(chat_id, day_str(), uid)
        spec = next(s for s in QUESTS if s[0] == q["kind"])
        out["quest"] = {"icon": spec[1], "text": spec[2].format(n=q["target"]).capitalize(), "target": q["target"],
                        "progress": min(total, q["target"]), "mine": mine, "done": bool(q.get("done")), "reward": QUEST_REWARD}
    return out


async def _post_quest(bot: Bot, chat_id: int, day: str) -> None:
    kind, icon, text, lo, hi = QUESTS[_hash(chat_id, day, "q") % len(QUESTS)]
    target = lo + _hash(chat_id, day, "t") % (hi - lo + 1)
    await db.kv_set(f"cq:{chat_id}:{day}", json.dumps({"kind": kind, "target": target, "done": False}))
    msg = (f"🎯 <b>Квест дня на весь чат</b>\n{icon} Нужно {text.format(n=target)}.\n"
           f"Награда: +{QUEST_REWARD} см каждому, кто хоть раз помог.\n"
           "Как идёт — в мини-аппе, вкладка «Чат».")
    await _send(bot, chat_id, msg, InlineKeyboardMarkup(inline_keyboard=[[app_link_button("📱 Открыть", "rp", chat_id)]]))


async def _finish_quest(bot: Bot, chat_id: int, day: str, q: dict) -> None:
    q["done"] = True
    await db.kv_set(f"cq:{chat_id}:{day}", json.dumps(q))
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT p.user_id, u.username FROM cq_prog p JOIN users u ON u.user_id = p.user_id AND u.chat_id = p.chat_id
               WHERE p.chat_id = ? AND p.day = ? AND p.n >= 1""",
            (chat_id, day),
        )
        helpers = await cur.fetchall()
    for uid, name in helpers:
        await db.add_size(uid, chat_id, name, QUEST_REWARD, "cquest")
    spec = next(s for s in QUESTS if s[0] == q["kind"])
    names = ", ".join(esc(n) for _, n in helpers[:8])
    await _send(bot, chat_id, f"🎉 <b>Квест дня выполнен!</b> {spec[1]} Чат справился.\n+{QUEST_REWARD} см получили: {names}")
    await highlights.add(chat_id, "🎯", f"Чат выполнил общий квест дня ({len(helpers)} участников)")


# ───────────── событие дня ─────────────

async def _send(bot: Bot, chat_id: int, text: str, kb: Optional[InlineKeyboardMarkup] = None):
    try:
        return await bot.send_message(chat_id, text, reply_markup=kb)
    except TelegramAPIError as exc:
        log.warning("Не удалось отправить в чат %s: %s", chat_id, exc)
        return None


async def _fire(bot: Bot, chat_id: int, day: str) -> None:
    kind = EVENT_TYPES[_hash(chat_id, day, "e") % len(EVENT_TYPES)]
    if kind in ("free_bar", "double_pay"):
        await db.kv_set(f"cev:{chat_id}", json.dumps({"type": kind, "until": int(time.time()) + EFFECT_TTL}))
        if kind == "free_bar":
            text = "🍻 <b>Счастливый час!</b> Всё в баре бесплатно на ближайший час. Мини-апп → Район → Бар."
            kb = InlineKeyboardMarkup(inline_keyboard=[[app_link_button("🍻 В бар", "rp", chat_id)]])
        else:
            text = "💼 <b>Рабочий аврал!</b> В «Районе» работа оплачивается вдвое ближайший час."
            kb = InlineKeyboardMarkup(inline_keyboard=[[app_link_button("💼 На работу", "rp", chat_id)]])
        await _send(bot, chat_id, text, kb)
    elif kind == "tax":
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute("SELECT user_id, money FROM rp_player WHERE chat_id = ? AND money >= 100", (chat_id,))
            rows = await cur.fetchall()
            total = 0
            for uid, money in rows:
                cut = money // 10
                total += cut
                await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cut, chat_id, uid))
            await conn.commit()
        if not rows:
            return
        await _send(bot, chat_id, f"🧾 <b>Налоговая нагрянула в район!</b> С каждого, у кого от 100 ₽, списали по 10%. Собрали {total} ₽. Прячьте наличку!")
        await highlights.add(chat_id, "🧾", f"Налоговая собрала с района {total} ₽")
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 Схватить клад", callback_data="ev:tr")]])
        sent = await _send(bot, chat_id, f"💰 <b>В чате нашли клад!</b> Первый, кто нажмёт кнопку, получит {TREASURE_REWARD} см. У вас 30 минут.", kb)
        if sent:
            await db.kv_set(f"cev_tr:{chat_id}", json.dumps({"msg": sent.message_id, "until": int(time.time()) + TREASURE_TTL, "claimed": 0}))


async def claim_treasure(bot: Bot, chat_id: int, user_id: int, name: str, msg_id: int) -> Optional[str]:
    """Возвращает текст-итог, если клад достался этому игроку, иначе None."""
    async with _treasure_lock:
        raw = await db.kv_get(f"cev_tr:{chat_id}")
        if not raw:
            return None
        t = json.loads(raw)
        if t.get("msg") != msg_id or t.get("claimed") or t.get("until", 0) < time.time():
            return None
        t["claimed"] = user_id
        await db.kv_set(f"cev_tr:{chat_id}", json.dumps(t))
    await db.add_size(user_id, chat_id, name, TREASURE_REWARD, "event")
    await highlights.add(chat_id, "💰", f"{name} первым нашёл клад: +{TREASURE_REWARD} см")
    return f"💰 Клад достался <b>{esc(name)}</b>: +{TREASURE_REWARD} см!"


async def _close_treasure(bot: Bot, chat_id: int) -> None:
    raw = await db.kv_get(f"cev_tr:{chat_id}")
    if not raw:
        return
    t = json.loads(raw)
    if t.get("closed") or t.get("claimed") or t.get("until", 0) >= time.time():
        return
    t["closed"] = True
    await db.kv_set(f"cev_tr:{chat_id}", json.dumps(t))
    try:
        await bot.edit_message_text("💰 Клад так никто и не нашёл. Исчез в тумане.", chat_id, t["msg"])
    except TelegramAPIError:
        pass


# ───────────── планировщик ─────────────

async def _tick_chat(bot: Bot, chat_id: int, now) -> None:
    day = day_str()
    if await db.total_messages(chat_id, day_str(-6)) <= 0:
        return  # неживой чат
    raw = await db.kv_get(f"cq:{chat_id}:{day}")
    if not raw and now.hour >= QUEST_FROM:
        await _post_quest(bot, chat_id, day)
    elif raw:
        q = json.loads(raw)
        if not q.get("done"):
            total, _ = await _progress(chat_id, day)
            if total >= q["target"]:
                await _finish_quest(bot, chat_id, day, q)
    hour = EVENT_FROM + _hash(chat_id, day, "h") % (EVENT_TO - EVENT_FROM)
    key = f"cev_done:{day}:{chat_id}"
    if now.hour >= hour and not await db.kv_get(key):
        await db.kv_set(key, "1")
        await _fire(bot, chat_id, day)
    await _close_treasure(bot, chat_id)


async def tick(bot: Bot, chats) -> None:
    now = now_local()
    for chat_id in chats:
        try:
            await _tick_chat(bot, chat_id, now)
        except Exception:
            log.exception("Ошибка событий чата %s", chat_id)
