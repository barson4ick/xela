"""Ограбление банка всем чатом: у банка полоска прочности, у охраны — тревоги. Вскрываем и отвлекаем, пока не приехала полиция."""
import logging
import random
import time

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message

import rp
from config import DB_PATH
from database import kv_get, kv_set
from utils import day_str, display_name, esc

log = logging.getLogger(__name__)
router = Router()

DURATION = 15 * 60
CLICK_GAP = 6.0
BASE_HP = 260
HP_PER_PLAYER = 90
BASE_LOOT = 5000
LOOT_PER_PLAYER = 500
MVP_BONUS = 500
FAIL_FINE = 80
EVENTS = [
    "Охранник пошёл за пончиками", "Сигнализация пискнула и передумала", "Кассирша уснула на смене",
    "Кошка пробежала по камерам", "Директор банка застрял в лифте", "Инкассатор опоздал на час",
]
_last_edit: dict = {}
_clicks: dict = {}


def _bar(v: int, total: int, n: int = 10) -> str:
    filled = max(0, min(n, round(n * v / total))) if total else 0
    return "█" * filled + "░" * (n - filled)


def _kb(rid: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🔨 Вскрыть сейф", callback_data=f"bk:h:{rid}"), Btn(text="🕶 Отвлечь охрану", callback_data=f"bk:d:{rid}")]])


async def _raid(conn, rid: int):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM bank_raid WHERE id = ?", (rid,))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _players(conn, rid: int, limit: int = 6) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM bank_hit WHERE raid_id = ? ORDER BY dmg DESC LIMIT ?", (rid, limit))
    return [dict(r) for r in await cur.fetchall()]


async def _text(conn, r: dict) -> str:
    left = max(0, r["ends"] - int(time.time()))
    top = await _players(conn, r["id"])
    lines = [
        "🏦 <b>ОГРАБЛЕНИЕ БАНКА «Золотой кабан»</b>",
        f"Сейф:   {_bar(r['hp'], r['max_hp'])} {r['hp']}/{r['max_hp']}",
        f"Тревога: {_bar(r['alert'], 100)} {r['alert']}%",
        f"⏱ Осталось {left // 60}:{left % 60:02d}. Тревога 100% — приедет полиция, и все сядут.",
    ]
    if r["note"]:
        lines.append(f"📻 <i>{esc(r['note'])}</i>")
    if top:
        lines.append("\n👥 В деле: " + ", ".join(f"{esc(x['name'])} ({x['hits']})" for x in top))
    lines.append("\n🔨 Вскрыть: урон сейфу, но растёт тревога. 🕶 Отвлечь: тревога падает.")
    return "\n".join(lines)


async def start(bot: Bot, chat_id: int, force: bool = False) -> str | None:
    """Запускает ограбление; возвращает текст ошибки, если нельзя."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT 1 FROM bank_raid WHERE chat_id = ? AND status = 'active'", (chat_id,))
        if await cur.fetchone():
            return "Банк уже грабят прямо сейчас."
        cur = await conn.execute("SELECT 1 FROM bank_raid WHERE chat_id = ? AND day = ?", (chat_id, day_str()))
        if await cur.fetchone() and not force:
            return "Сегодня банк уже грабили, охрана усилена. Приходите завтра."
        cur = await conn.execute("SELECT COUNT(*) FROM rp_player WHERE chat_id = ?", (chat_id,))
        players = max(2, min(8, (await cur.fetchone())[0]))
        hp = BASE_HP + HP_PER_PLAYER * players
        t = int(time.time())
        cur = await conn.execute("INSERT INTO bank_raid (chat_id, hp, max_hp, started, ends, day) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, hp, hp, t, t + DURATION, day_str()))
        rid = cur.lastrowid
        await conn.commit()
        r = await _raid(conn, rid)
        text = await _text(conn, r)
        try:
            sent = await bot.send_message(chat_id, text + "\n\n<b>Всем в деле! Кто успел, тот и поел.</b>", reply_markup=_kb(rid))
        except TelegramAPIError as exc:
            await conn.execute("UPDATE bank_raid SET status = 'cancel' WHERE id = ?", (rid,))
            await conn.commit()
            log.warning("Ограбление банка не запущено: %s", exc)
            return "Не получилось написать в чат."
        await conn.execute("UPDATE bank_raid SET msg_id = ? WHERE id = ?", (sent.message_id, rid))
        await conn.commit()
    return None


async def _edit(bot: Bot, r: dict, text: str, kb=None) -> None:
    try:
        await bot.edit_message_text(text, chat_id=r["chat_id"], message_id=r["msg_id"], reply_markup=kb)
    except TelegramAPIError as exc:
        if "not modified" not in str(exc):
            log.warning("Сообщение ограбления не обновилось: %s", exc)


async def _finish(bot: Bot, conn, r: dict, win: bool) -> None:
    cur = await conn.execute("UPDATE bank_raid SET status = ? WHERE id = ? AND status = 'active'", ("win" if win else "fail", r["id"]))
    if cur.rowcount == 0:
        return
    chat_id = r["chat_id"]
    parts = await _players(conn, r["id"], 50)
    if not parts:
        await conn.commit()
        await _edit(bot, r, "🏦 Банк так никто и не ограбил: все струсили. Охрана торжествует.")
        return
    if win:
        loot = BASE_LOOT + LOOT_PER_PLAYER * len(parts)
        total_hits = sum(x["hits"] for x in parts) or 1
        mvp = parts[0]
        lines = []
        for x in parts:
            share = int(loot * x["hits"] / total_hits) + (MVP_BONUS if x is mvp else 0)
            await rp._load(conn, chat_id, x["user_id"])
            await conn.execute("UPDATE rp_player SET money = money + ?, wanted = MIN(?, wanted + 1) WHERE chat_id = ? AND user_id = ?", (share, rp.MAX_WANTED, chat_id, x["user_id"]))
            lines.append(f"{'🏆' if x is mvp else '•'} {esc(x['name'])}: {share} ₽ ({x['hits']} удар.)")
        await rp._news(conn, chat_id, f"🏦 Банк «Золотой кабан» ограблен: {loot} ₽ на {len(parts)} человек, герой — {mvp['name']}")
        text = f"🏦 <b>БАНК ВЗЯТ!</b> Общая добыча {loot} ₽. Герой дня: <b>{esc(mvp['name'])}</b> (+{MVP_BONUS} ₽ бонус)\n\n" + "\n".join(lines)
    else:
        jail = int(time.time()) + 10 * 60
        for x in parts:
            await rp._load(conn, chat_id, x["user_id"])
            await conn.execute("UPDATE rp_player SET money = MAX(0, money - ?), jail_until = MAX(jail_until, ?) WHERE chat_id = ? AND user_id = ?", (FAIL_FINE, jail, chat_id, x["user_id"]))
        await rp._news(conn, chat_id, f"🚔 Ограбление банка сорвалось, приехала полиция: {len(parts)} человек сели на 10 мин")
        text = (f"🚔 <b>ПОЛИЦИЯ!</b> Ограбление банка провалено. {len(parts)} участников получили штраф {FAIL_FINE} ₽ и 10 минут в тюрьме.\n"
                f"Сейф остался на {r['hp']}/{r['max_hp']}. В следующий раз отвлекайте охрану вовремя.")
    await conn.commit()
    await _edit(bot, r, text)


@router.callback_query(F.data.startswith("bk:"))
async def cb_raid(cb: CallbackQuery, bot: Bot) -> None:
    _, act, rid_s = cb.data.split(":")
    uid = cb.from_user.id
    now = time.monotonic()
    if now - _clicks.get(uid, 0) < CLICK_GAP:
        await cb.answer(f"Не так быстро, подожди {int(CLICK_GAP - (now - _clicks.get(uid, 0))) + 1} с", show_alert=False)
        return
    name = display_name(cb.from_user)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        r = await _raid(conn, int(rid_s))
        if not r or r["status"] != "active":
            await cb.answer("Ограбление уже закончилось")
            return
        if r["ends"] <= int(time.time()):
            await _finish(bot, conn, r, False)
            await cb.answer("Время вышло")
            return
        p = await rp._load(conn, r["chat_id"], uid)
        if p["jail_until"] > int(time.time()):
            await cb.answer("Ты в тюрьме, на дело не пустят", show_alert=True)
            return
        _clicks[uid] = now
        note = r["note"]
        if act == "h":
            dmg = random.randint(10, 22)
            gang_row = await (await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (r["chat_id"], uid))).fetchone()
            if gang_row:
                dmg = int(dmg * 1.1)
            crit = random.random() < 0.12
            if crit:
                dmg *= 2
            r["hp"] = max(0, r["hp"] - dmg)
            r["alert"] = min(100, r["alert"] + random.randint(3, 7))
            await conn.execute(
                """INSERT INTO bank_hit (raid_id, user_id, name, hits, dmg) VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(raid_id, user_id) DO UPDATE SET hits = hits + 1, dmg = dmg + ?, name = ?""", (r["id"], uid, name, dmg, dmg, name))
            if random.random() < 0.2:
                note = random.choice(EVENTS)
            answer = f"Крит! −{dmg}" if crit else f"−{dmg} сейфу"
        else:
            drop = random.randint(10, 18)
            r["alert"] = max(0, r["alert"] - drop)
            await conn.execute(
                """INSERT INTO bank_hit (raid_id, user_id, name, hits, dmg) VALUES (?, ?, ?, 1, 0)
                   ON CONFLICT(raid_id, user_id) DO UPDATE SET hits = hits + 1, name = ?""", (r["id"], uid, name, name))
            answer = f"Охрана отвлечена, тревога −{drop}"
        await conn.execute("UPDATE bank_raid SET hp = ?, alert = ?, note = ? WHERE id = ?", (r["hp"], r["alert"], note, r["id"]))
        r["note"] = note
        await conn.commit()
        if r["hp"] <= 0:
            await _finish(bot, conn, r, True)
        elif r["alert"] >= 100:
            await _finish(bot, conn, r, False)
        elif now - _last_edit.get(r["id"], 0) > 1.5:
            _last_edit[r["id"]] = now
            await _edit(bot, r, await _text(conn, r), _kb(r["id"]))
    await cb.answer(answer)


@router.message(Command("bank", "bankraid"))
async def cmd_bank(message: Message, bot: Bot) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Банки грабят только всем чатом.")
        return
    err = await start(bot, message.chat.id)
    if err:
        await message.reply(err)


async def tick(bot: Bot, chats) -> None:
    """Раз в минуту: просрочка ограбления и вечерний запуск (20:30–22:00 в живых чатах)."""
    from utils import now_local
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT id FROM bank_raid WHERE status = 'active' AND ends <= ?", (int(time.time()),))
        for (rid,) in await cur.fetchall():
            r = await _raid(conn, rid)
            if r:
                await _finish(bot, conn, r, False)
    n = now_local()
    if not (n.hour == 20 and n.minute >= 30 or n.hour == 21):
        return
    for chat_id in await rp.active_chats():
        key = f"bank_auto:{day_str()}:{chat_id}"
        if await kv_get(key):
            continue
        await kv_set(key, "1")
        if await start(bot, chat_id) is None:
            log.info("Вечернее ограбление банка запущено в чате %s", chat_id)
