"""Браки: свадьба по согласию, общий счёт, дети, развод с разделом имущества."""
import logging
import random
import time

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message

import relations
import rp
from config import DB_PATH
from utils import display_name, esc

log = logging.getLogger(__name__)
router = Router()

MIN_REL = 5            # очков отношений (стадия «Друзья»)
WEDDING_GIFT = 300     # ₽ каждому
KID_COST = 500         # ₽ из общего счёта
KID_GAP = 3 * 86400
MAX_KIDS = 4
KID_INCOME = 25        # ₽ в сутки с ребёнка на общий счёт
ALIMONY = 0.15
BOYS = ["Вася", "Петя", "Гоша", "Тимоша", "Ярик", "Кузя", "Борька", "Ромка"]
GIRLS = ["Маша", "Даша", "Люся", "Соня", "Алёна", "Зоя", "Тося", "Варя"]
_pending: dict = {}  # (chat, msg) -> (kind, a, b)


def _pair(a: int, b: int) -> tuple:
    return (a, b) if a < b else (b, a)


async def get(conn, chat_id: int, uid: int):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM marriage WHERE chat_id = ? AND (a = ? OR b = ?)", (chat_id, uid, uid))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _name(conn, chat_id: int, uid: int) -> str:
    cur = await conn.execute("SELECT username FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return r[0] if r and r[0] else "игрок"


async def _accrue(conn, m: dict) -> dict:
    """Дети приносят карманные деньги на общий счёт (ленивое начисление по дням)."""
    now = int(time.time())
    cur = await conn.execute("SELECT COUNT(*) FROM family_kid WHERE chat_id = ? AND a = ? AND b = ?", (m["chat_id"], m["a"], m["b"]))
    kids = (await cur.fetchone())[0]
    base = m["last_accr"] or m["ts"]
    days = min(14, (now - base) // 86400)
    if days > 0:
        add = days * kids * KID_INCOME
        m["joint"] += add
        m["last_accr"] = base + days * 86400
        await conn.execute("UPDATE marriage SET joint = ?, last_accr = ? WHERE chat_id = ? AND a = ? AND b = ?", (m["joint"], m["last_accr"], m["chat_id"], m["a"], m["b"]))
    return m


def _days(ts: int) -> int:
    return max(0, (int(time.time()) - ts) // 86400)


# ─────────── свадьба ───────────

@router.message(Command("brak", "marry"))
async def cmd_brak(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup") or not message.from_user:
        await message.reply("Свадьбы играют только в групповом чате.")
        return
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target or target.is_bot or target.id == message.from_user.id:
        await message.reply("Ответь командой /brak на сообщение того, кому делаешь предложение 💍")
        return
    chat_id, me = message.chat.id, message.from_user
    info = await relations.get(chat_id, me.id, target.id)
    if info["pts"] < MIN_REL:
        await message.reply(f"Рановато! Сначала подружитесь (нужно {MIN_REL} очков отношений, у вас {info['pts']}): обнимашки /hug и прочие РП-команды помогут.")
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        if await get(conn, chat_id, me.id):
            await message.reply("Ты уже женат(а). Сначала развод: /razvod")
            return
        if await get(conn, chat_id, target.id):
            await message.reply(f"{esc(display_name(target))} уже в браке.")
            return
    kb = Kb(inline_keyboard=[[Btn(text="💍 Согласен(на)", callback_data=f"mr:y:{me.id}:{target.id}"), Btn(text="💔 Отказать", callback_data=f"mr:n:{me.id}:{target.id}")]])
    await message.answer(f"💍 <b>{esc(display_name(me))}</b> делает предложение <b>{esc(display_name(target))}</b>!\nОтвечать может только жених/невеста.", reply_markup=kb)


@router.callback_query(F.data.startswith("mr:"))
async def cb_marry(cb: CallbackQuery) -> None:
    _, ans, a_s, b_s = cb.data.split(":")
    a, b = int(a_s), int(b_s)
    if cb.from_user.id != b:
        await cb.answer("Отвечает только тот, кому сделали предложение", show_alert=True)
        return
    chat_id = cb.message.chat.id
    if ans == "n":
        await cb.message.edit_text(cb.message.html_text + "\n\n💔 Предложение отклонено. Держись, герой.")
        await cb.answer()
        return
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        if await get(conn, chat_id, a) or await get(conn, chat_id, b):
            await cb.answer("Кто-то из вас уже в браке", show_alert=True)
            return
        x, y = _pair(a, b)
        t = int(time.time())
        await conn.execute("INSERT INTO marriage (chat_id, a, b, ts, last_accr) VALUES (?, ?, ?, ?, ?)", (chat_id, x, y, t, t))
        na, nb = await _name(conn, chat_id, a), await _name(conn, chat_id, b)
        for u in (a, b):
            await rp._load(conn, chat_id, u)
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (WEDDING_GIFT, chat_id, u))
        await rp._news(conn, chat_id, f"💒 Свадьба! {na} и {nb} теперь муж(жена) и жена(муж)")
        await conn.commit()
    await cb.message.edit_text(f"💒 <b>Свадьба!</b> {esc(na)} и {esc(nb)} теперь семья. Горько! 🥂\nПодарок молодожёнам: по {WEDDING_GIFT} ₽.\n"
                               "Дальше: /semya, /vznos, /snyat, /rebenok. А если что: /razvod")
    await cb.answer("Совет да любовь!")


# ─────────── семья ───────────

@router.message(Command("semya", "family"))
async def cmd_family(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup") or not message.from_user:
        return
    chat_id, uid = message.chat.id, message.from_user.id
    async with aiosqlite.connect(DB_PATH) as conn:
        m = await get(conn, chat_id, uid)
        if not m:
            await message.reply("Ты не женат(а). Предложение делают так: /brak ответом на сообщение друга 💍")
            return
        m = await _accrue(conn, m)
        await conn.commit()
        sp = m["b"] if m["a"] == uid else m["a"]
        sp_name = await _name(conn, chat_id, sp)
        cur = await conn.execute("SELECT name, icon, born FROM family_kid WHERE chat_id = ? AND a = ? AND b = ? ORDER BY born", (chat_id, m["a"], m["b"]))
        kids = await cur.fetchall()
    lines = [f"👨‍👩‍👧 <b>Семья</b>: {esc(display_name(message.from_user))} + {esc(sp_name)}",
             f"💒 В браке {_days(m['ts'])} дн. · 💰 Общий счёт: <b>{m['joint']} ₽</b>"]
    if kids:
        lines.append("Дети: " + ", ".join(f"{k[1]} {esc(k[0])} ({_days(k[2])} дн.)" for k in kids) + f"\n<i>Каждый ребёнок приносит {KID_INCOME} ₽ в сутки.</i>")
    else:
        lines.append(f"Детей нет. Завести: /rebenok (−{KID_COST} ₽ с общего счёта)")
    lines.append("/vznos N · /snyat N · /rebenok · /razvod")
    await message.reply("\n".join(lines))


async def _money_cmd(message: Message, command: CommandObject, deposit: bool) -> None:
    if message.chat.type not in ("group", "supergroup") or not message.from_user:
        return
    try:
        amount = int((command.args or "").split()[0])
    except (ValueError, IndexError):
        await message.reply(f"Сколько? Например: /{'vznos' if deposit else 'snyat'} 300")
        return
    if amount < 1 or amount > 100000:
        await message.reply("Сумма должна быть от 1 до 100000 ₽.")
        return
    chat_id, uid = message.chat.id, message.from_user.id
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        m = await get(conn, chat_id, uid)
        if not m:
            await message.reply("Общий счёт есть только у семейных. /brak в помощь 💍")
            return
        m = await _accrue(conn, m)
        p = await rp._load(conn, chat_id, uid)
        if deposit:
            if p["money"] < amount:
                await message.reply(f"У тебя только {p['money']} ₽.")
                return
            p["money"] -= amount
            m["joint"] += amount
        else:
            if m["joint"] < amount:
                await message.reply(f"На общем счёте только {m['joint']} ₽.")
                return
            m["joint"] -= amount
            p["money"] += amount
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE marriage SET joint = ? WHERE chat_id = ? AND a = ? AND b = ?", (m["joint"], chat_id, m["a"], m["b"]))
        await conn.commit()
    await message.reply(f"{'💰 Внесено' if deposit else '💸 Снято'} {amount} ₽. На общем счёте: <b>{m['joint']} ₽</b>")


@router.message(Command("vznos"))
async def cmd_vznos(message: Message, command: CommandObject) -> None:
    await _money_cmd(message, command, True)


@router.message(Command("snyat"))
async def cmd_snyat(message: Message, command: CommandObject) -> None:
    await _money_cmd(message, command, False)


@router.message(Command("rebenok", "kid"))
async def cmd_kid(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup") or not message.from_user:
        return
    chat_id, uid = message.chat.id, message.from_user.id
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        m = await get(conn, chat_id, uid)
        if not m:
            await message.reply("Детей заводят в браке. Сначала /brak 💍")
            return
        m = await _accrue(conn, m)
        cur = await conn.execute("SELECT COUNT(*) FROM family_kid WHERE chat_id = ? AND a = ? AND b = ?", (chat_id, m["a"], m["b"]))
        n = (await cur.fetchone())[0]
        if n >= MAX_KIDS:
            await message.reply(f"Четверо детей — это предел, больше в однушку не влезет ({MAX_KIDS}).")
            return
        wait = m["last_kid"] + KID_GAP - int(time.time())
        if wait > 0:
            await message.reply(f"Рановато! Следующий ребёнок через {wait // 3600 + 1} ч. Дайте аистам отдохнуть.")
            return
        if m["joint"] < KID_COST:
            await message.reply(f"На ребёнка нужно {KID_COST} ₽ на общем счёте, там {m['joint']}. Пополни: /vznos")
            return
        boy = random.random() < 0.5
        kid_name = random.choice(BOYS if boy else GIRLS)
        icon = "👦" if boy else "👧"
        m["joint"] -= KID_COST
        await conn.execute("UPDATE marriage SET joint = ?, last_kid = ? WHERE chat_id = ? AND a = ? AND b = ?", (m["joint"], int(time.time()), chat_id, m["a"], m["b"]))
        await conn.execute("INSERT INTO family_kid (chat_id, a, b, name, icon, born) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, m["a"], m["b"], kid_name, icon, int(time.time())))
        na, nb = await _name(conn, chat_id, m["a"]), await _name(conn, chat_id, m["b"])
        await rp._news(conn, chat_id, f"👶 В семье {na} и {nb} пополнение: {kid_name}")
        await conn.commit()
    await message.reply(f"👶 Поздравляем! У вас {'сын' if boy else 'дочь'}: {icon} <b>{kid_name}</b>. Расходы {KID_COST} ₽ списаны с общего счёта.")


# ─────────── развод ───────────

@router.message(Command("razvod", "divorce"))
async def cmd_divorce(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup") or not message.from_user:
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        m = await get(conn, message.chat.id, message.from_user.id)
    if not m:
        await message.reply("Разводиться не с кем: ты не женат(а).")
        return
    kb = Kb(inline_keyboard=[[Btn(text="💔 Да, развод", callback_data=f"dv:y:{message.from_user.id}"), Btn(text="Передумал(а)", callback_data=f"dv:n:{message.from_user.id}")]])
    await message.reply(f"💔 Точно развод? Общий счёт делится пополам, но ты платишь бывшему(ей) {int(ALIMONY * 100)}% своей доли, дети уходят к бабушке.", reply_markup=kb)


@router.callback_query(F.data.startswith("dv:"))
async def cb_divorce(cb: CallbackQuery) -> None:
    _, ans, uid_s = cb.data.split(":")
    uid = int(uid_s)
    if cb.from_user.id != uid:
        await cb.answer("Это решает только инициатор", show_alert=True)
        return
    if ans == "n":
        await cb.message.edit_text("Любовь победила 💞 Развод отменён.")
        await cb.answer()
        return
    chat_id = cb.message.chat.id
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        m = await get(conn, chat_id, uid)
        if not m:
            await cb.answer("Вы уже не женаты")
            return
        m = await _accrue(conn, m)
        ex = m["b"] if m["a"] == uid else m["a"]
        half = m["joint"] // 2
        mine = half - int(half * ALIMONY)
        theirs = m["joint"] - mine
        for u, amt in ((uid, mine), (ex, theirs)):
            await rp._load(conn, chat_id, u)
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (amt, chat_id, u))
        await conn.execute("DELETE FROM marriage WHERE chat_id = ? AND a = ? AND b = ?", (chat_id, m["a"], m["b"]))
        await conn.execute("DELETE FROM family_kid WHERE chat_id = ? AND a = ? AND b = ?", (chat_id, m["a"], m["b"]))
        n1, n2 = await _name(conn, chat_id, uid), await _name(conn, chat_id, ex)
        await rp._news(conn, chat_id, f"💔 {n1} подал(а) на развод с {n2}")
        await conn.commit()
    await cb.message.edit_text(f"💔 <b>Развод.</b> {esc(n1)} получил(а) {mine} ₽, {esc(n2)} — {theirs} ₽ (с учётом алиментов). Дети уехали к Бабе Вале.")
    await cb.answer()
