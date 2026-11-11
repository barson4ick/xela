"""Споры на см, предсказания и суд чата: ставки между друзьями, исход решает чат."""
import asyncio
import json
import logging
import random
import re
import time
from typing import Optional

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message

import chat_events
import database as db
import highlights
from config import DB_PATH, GEMINI_API_KEY
from utils import display_name, esc
from webdb import apply_bet

log = logging.getLogger(__name__)
router = Router()

ANON = {1087968824, 136817688}
MAX_STAKE = 20
DISPUTE_VOTES = 3          # столько голосов решают спор
DISPUTE_OFFER_TTL = 3600
DISPUTE_TTL = 48 * 3600
PREDICT_STAKE = 2
PREDICT_TTL = 7 * 86400
TRIAL_VOTES = 3
TRIAL_FINE = 3
FALSE_FINE = 1
TRIAL_TTL = 900

SPOR_USAGE = (
    "🤝 <b>Спор на см</b>\n"
    "Ответь на сообщение человека или назови его: <code>/spor @username 5 кто быстрее съест шаурму</code>\n"
    "Оба вкладывают по столько же см (от 1 до 20). Остальные голосуют, кто прав: кто первым соберёт 3 голоса, забирает банк."
)
PREDICT_USAGE = (
    "🔮 <b>Предсказание</b>\n"
    "<code>/predict дойдём ли мы сегодня до бара</code>\n"
    "Каждый ставит 2 см на «да» или «нет». Автор (или админ) подводит итог, победители делят банк."
)
SUD_USAGE = (
    "⚖️ <b>Суд чата</b>\n"
    "<code>/sud @username украл мою шаурму</code> (или ответом на сообщение)\n"
    "Присяжные голосуют: виновен — штраф 3 см, невиновен — истец платит 1 см за ложный донос. Подать иск можно раз в сутки."
)


def _is_player(u) -> bool:
    return bool(u) and not u.is_bot and u.id not in ANON


async def _parse(message: Message, command: CommandObject, want_amount: bool):
    """Возвращает (id, имя, сумма, текст): цель — из reply или @username."""
    amount, name_token, rest = None, None, []
    for a in (command.args or "").split():
        if want_amount and amount is None and re.fullmatch(r"\d+", a):
            amount = int(a)
        elif name_token is None and a.startswith("@") and len(a) > 1:
            name_token = a
        else:
            rest.append(a)
    target_id = target_name = None
    reply = message.reply_to_message
    if reply and _is_player(reply.from_user):
        target_id, target_name = reply.from_user.id, display_name(reply.from_user)
        await db.ensure_user(target_id, message.chat.id, target_name)
    elif name_token:
        found = await db.find_user_by_name(message.chat.id, name_token)
        if found:
            target_id, target_name = found
    return target_id, target_name, amount, " ".join(rest).strip()


async def _edit(bot: Bot, chat_id: int, msg_id: int, text: str, kb: Optional[Kb] = None) -> None:
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=kb)
    except TelegramAPIError:
        pass  # то же самое содержимое или сообщение удалено


async def _row(conn, table: str, rid: int) -> Optional[dict]:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def _load(table: str, rid: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        return await _row(conn, table, rid)


async def _set_status(table: str, rid: int, old: str, new: str) -> bool:
    """Атомарно меняет статус: только один обработчик выигрывает гонку."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(f"UPDATE {table} SET status = ? WHERE id = ? AND status = ?", (new, rid, old))
        await conn.commit()
        return cur.rowcount > 0


async def _name_of(chat_id: int, uid: int) -> str:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT username FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        row = await cur.fetchone()
    return row[0] if row else "игрок"


# ═══════════════════════ споры ═══════════════════════

def _dispute_text(d: dict, votes: Optional[dict] = None) -> str:
    head = f"🤝 <b>Спор на {d['stake']} см</b>\n<b>{esc(d['a_name'])}</b> против <b>{esc(d['b_name'])}</b>\n«{esc(d['text'])}»\n"
    if d["status"] == "offer":
        return head + f"\n{esc(d['b_name'])}, принимаешь? Ставки: по {d['stake']} см с каждого."
    votes = votes or {}
    na = sum(1 for v in votes.values() if v == "a")
    nb = sum(1 for v in votes.values() if v == "b")
    return (head + f"\n⚖️ Банк {2 * d['stake']} см. Голосуют остальные: первый, кто соберёт {DISPUTE_VOTES} голоса, забирает банк.\n"
            f"👉 {esc(d['a_name'])}: <b>{na}</b> · {esc(d['b_name'])}: <b>{nb}</b>")


def _dispute_kb(d: dict, votes: dict) -> Kb:
    na = sum(1 for v in votes.values() if v == "a")
    nb = sum(1 for v in votes.values() if v == "b")
    i = d["id"]
    return Kb(inline_keyboard=[
        [Btn(text=f"За {d['a_name']} ({na})", callback_data=f"dsp:v:{i}:a"), Btn(text=f"За {d['b_name']} ({nb})", callback_data=f"dsp:v:{i}:b")],
        [Btn(text="🏳 Сдаюсь (для участников)", callback_data=f"dsp:ff:{i}")],
    ])




async def _refund_dispute(d: dict) -> None:
    for uid, name in ((d["a_id"], d["a_name"]), (d["b_id"], d["b_name"])):
        await db.add_size(uid, d["chat_id"], name, d["stake"], "dispute_out")


async def _resolve_dispute(bot: Bot, d: dict, side: str, how: str = "") -> None:
    if not await _set_status("dispute", d["id"], "active", "done"):
        return
    winner_id, winner_name = (d["a_id"], d["a_name"]) if side == "a" else (d["b_id"], d["b_name"])
    loser_name = d["b_name"] if side == "a" else d["a_name"]
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE dispute SET winner = ? WHERE id = ?", (winner_id, d["id"]))
        await conn.commit()
    await db.add_size(winner_id, d["chat_id"], winner_name, 2 * d["stake"], "dispute_out")
    text = (f"🏆 <b>Спор решён!</b> «{esc(d['text'])}»\n<b>{esc(winner_name)}</b> побеждает {esc(loser_name)}{how} и забирает <b>{2 * d['stake']} см</b>.")
    await _edit(bot, d["chat_id"], d["msg_id"], text)
    if d["stake"] >= 3:
        await highlights.add(d["chat_id"], "🤝", f"{winner_name} выиграл спор у {loser_name}: +{d['stake']} см")


@router.callback_query(F.data.startswith("dsp:"))
async def cb_dispute(cb: CallbackQuery, bot: Bot) -> None:
    parts = cb.data.split(":")
    action, did = parts[1], int(parts[2])
    d = await _load("dispute", did)
    if not d or not cb.message:
        await cb.answer("Спор уже закрыт")
        return
    uid = cb.from_user.id
    chat_id = d["chat_id"]
    if action == "no":
        if uid not in (d["a_id"], d["b_id"]):
            await cb.answer("Это не твой спор")
            return
        if await _set_status("dispute", did, "offer", "cancel"):
            await _edit(bot, chat_id, d["msg_id"], f"🤝 Спор «{esc(d['text'])}» отменён.")
        await cb.answer()
        return
    if action == "acc":
        if uid != d["b_id"]:
            await cb.answer("Принять может только тот, кого позвали")
            return
        if not await _set_status("dispute", did, "offer", "active"):
            await cb.answer("Спор уже не актуален")
            return
        ok_b = await apply_bet(d["b_id"], chat_id, d["stake"], 0, "dispute_in")
        ok_a = await apply_bet(d["a_id"], chat_id, d["stake"], 0, "dispute_in") if ok_b is not None else None
        if ok_b is None or ok_a is None:
            if ok_b is not None:
                await db.add_size(d["b_id"], chat_id, d["b_name"], d["stake"], "dispute_out")
            await _set_status("dispute", did, "active", "cancel")
            await _edit(bot, chat_id, d["msg_id"], "🤝 Спор сорвался: у кого-то не хватило см.")
            await cb.answer("Не хватает см")
            return
        d["status"] = "active"
        await _edit(bot, chat_id, d["msg_id"], _dispute_text(d, {}), _dispute_kb(d, {}))
        await cb.answer("Спор принят!")
        return
    if d["status"] != "active":
        await cb.answer("Спор уже закрыт")
        return
    if action == "ff":
        if uid not in (d["a_id"], d["b_id"]):
            await cb.answer("Сдаться могут только участники")
            return
        await _resolve_dispute(bot, d, "b" if uid == d["a_id"] else "a", " (соперник сдался)")
        await cb.answer()
        return
    if action == "v":
        if uid in (d["a_id"], d["b_id"]):
            await cb.answer("Участники не голосуют")
            return
        side = parts[3]
        votes = json.loads(d["votes"] or "{}")
        votes[str(uid)] = side
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("UPDATE dispute SET votes = ? WHERE id = ? AND status = 'active'", (json.dumps(votes), did))
            await conn.commit()
        na = sum(1 for v in votes.values() if v == "a")
        nb = sum(1 for v in votes.values() if v == "b")
        if na >= DISPUTE_VOTES or nb >= DISPUTE_VOTES:
            await _resolve_dispute(bot, d, "a" if na >= DISPUTE_VOTES else "b", " по решению чата")
        else:
            await _edit(bot, chat_id, d["msg_id"], _dispute_text(d, votes), _dispute_kb(d, votes))
        await cb.answer("Голос принят")


# ═══════════════════════ предсказания ═══════════════════════

async def _prediction_votes(pid: int) -> list:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT user_id, name, side FROM prediction_vote WHERE pid = ?", (pid,))
        return await cur.fetchall()


async def _prediction_text(p: dict) -> str:
    votes = await _prediction_votes(p["id"])
    ny = sum(1 for _, _, s in votes if s == "y")
    nn = sum(1 for _, _, s in votes if s == "n")
    return (f"🔮 <b>Предсказание</b> от <b>{esc(p['author_name'])}</b>\n«{esc(p['text'])}»\n\n"
            f"Ставка {PREDICT_STAKE} см. 👍 Да: <b>{ny}</b> · 👎 Нет: <b>{nn}</b> · банк {PREDICT_STAKE * len(votes)} см\n"
            "Итог подведёт автор (или админ чата).")


def _prediction_kb(pid: int) -> Kb:
    return Kb(inline_keyboard=[
        [Btn(text=f"👍 Да · {PREDICT_STAKE} см", callback_data=f"prd:y:{pid}"), Btn(text=f"👎 Нет · {PREDICT_STAKE} см", callback_data=f"prd:n:{pid}")],
        [Btn(text="✅ Сбылось", callback_data=f"prd:ok:{pid}"), Btn(text="❌ Не сбылось", callback_data=f"prd:no:{pid}"), Btn(text="🚫 Отмена", callback_data=f"prd:x:{pid}")],
    ])




async def _settle_prediction(bot: Bot, p: dict, outcome: str) -> None:
    """outcome: y — сбылось, n — не сбылось, x — отмена (возврат ставок)."""
    if not await _set_status("prediction", p["id"], "open", "done"):
        return
    votes = await _prediction_votes(p["id"])
    pool = PREDICT_STAKE * len(votes)
    winners = [v for v in votes if v[2] == outcome] if outcome in ("y", "n") else []
    losers = [v for v in votes if v[2] != outcome] if outcome in ("y", "n") else []
    if not winners or not losers:  # некому делить — возвращаем ставки
        for uid, name, _ in votes:
            await db.add_size(uid, p["chat_id"], name, PREDICT_STAKE, "predict_out")
        verdict = "Ставки возвращены" + (" (предсказание отменено)." if outcome == "x" else ": на другой стороне никого не было.")
        text = f"🔮 <b>Предсказание закрыто.</b> «{esc(p['text'])}»\n{verdict}"
    else:
        share, extra = divmod(pool, len(winners))
        for i, (uid, name, _) in enumerate(winners):
            await db.add_size(uid, p["chat_id"], name, share + (1 if i < extra else 0), "predict_out")
        word = "сбылось ✅" if outcome == "y" else "не сбылось ❌"
        names = ", ".join(esc(n) for _, n, _ in winners[:8])
        text = (f"🔮 <b>Итог предсказания:</b> «{esc(p['text'])}» — {word}\n"
                f"Банк {pool} см делят угадавшие: {names} (по {share} см).")
        if len(votes) >= 3:
            await highlights.add(p["chat_id"], "🔮", f"Предсказание «{p['text'][:60]}» {'сбылось' if outcome == 'y' else 'не сбылось'}: банк {pool} см")
    await _edit(bot, p["chat_id"], p["msg_id"], text)


@router.callback_query(F.data.startswith("prd:"))
async def cb_prediction(cb: CallbackQuery, bot: Bot) -> None:
    _, action, pid_s = cb.data.split(":")
    p = await _load("prediction", int(pid_s))
    if not p or p["status"] != "open" or not cb.message:
        await cb.answer("Предсказание уже закрыто")
        return
    uid = cb.from_user.id
    if action in ("y", "n"):
        if uid == p["author_id"]:
            await cb.answer("Автор не ставит на своё предсказание")
            return
        name = display_name(cb.from_user)
        await db.ensure_user(uid, p["chat_id"], name)
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute("INSERT OR IGNORE INTO prediction_vote (pid, user_id, name, side) VALUES (?, ?, ?, ?)", (p["id"], uid, name, action))
            await conn.commit()
            fresh = cur.rowcount > 0
        if not fresh:
            await cb.answer("Ты уже поставил")
            return
        if await apply_bet(uid, p["chat_id"], PREDICT_STAKE, 0, "predict_in") is None:
            async with aiosqlite.connect(DB_PATH) as conn:
                await conn.execute("DELETE FROM prediction_vote WHERE pid = ? AND user_id = ?", (p["id"], uid))
                await conn.commit()
            await cb.answer(f"Нужно {PREDICT_STAKE} см")
            return
        await _edit(bot, p["chat_id"], p["msg_id"], await _prediction_text(p), _prediction_kb(p["id"]))
        await cb.answer(f"Ставка принята: {'да' if action == 'y' else 'нет'}")
        return
    allowed = uid == p["author_id"]
    if not allowed:
        try:
            member = await bot.get_chat_member(p["chat_id"], uid)
            allowed = member.status in ("creator", "administrator")
        except TelegramAPIError:
            allowed = False
    if not allowed:
        await cb.answer("Итог подводит автор или админ")
        return
    await _settle_prediction(bot, p, {"ok": "y", "no": "n", "x": "x"}[action])
    await cb.answer()


# ═══════════════════════ суд чата ═══════════════════════

FALLBACK_GUILTY = ["Улики железные, суд в восторге от наглости. Виновен!", "Присяжные кивнули хором: виновен, и спорить бесполезно."]
FALLBACK_FREE = ["Суд не нашёл состава, зато нашёл повод посмеяться. Свободен!", "Дело развалилось на глазах: ответчик невиновен."]


async def _judge_line(text: str, plaintiff: str, defendant: str, guilty: bool) -> str:
    if GEMINI_API_KEY:
        try:
            from handlers.chatai import _complete_gemini
            system = "Ты добродушный шуточный судья в чате друзей. Пиши по-русски живо, коротко и без злобы."
            prompt = (f"Дело: {plaintiff} обвиняет {defendant}: «{text}». Приговор: {'виновен' if guilty else 'невиновен'}. "
                      "Напиши 1–2 коротких предложения вердикта от лица судьи с юмором и добавь абсурдное безобидное дополнительное наказание (например: неделю желать всем доброго утра голосом пирата). Без кавычек и без эмодзи.")
            line = await asyncio.wait_for(_complete_gemini(system, prompt, max_tokens=140), 12)
            if line:
                return line.strip()
        except Exception:
            log.warning("Судья-ИИ недоступен", exc_info=True)
    return random.choice(FALLBACK_GUILTY if guilty else FALLBACK_FREE)


def _trial_text(t: dict, votes: dict) -> str:
    ng = sum(1 for v in votes.values() if v == "g")
    ni = sum(1 for v in votes.values() if v == "i")
    return (f"⚖️ <b>Суд чата</b>\nИстец: <b>{esc(t['plaintiff_name'])}</b> · Ответчик: <b>{esc(t['defendant_name'])}</b>\n"
            f"Обвинение: «{esc(t['text'])}»\n\nПрисяжные (все, кроме сторон), голосуйте! {TRIAL_VOTES} голоса «виновен» — штраф {TRIAL_FINE} см, "
            f"{TRIAL_VOTES} голоса «невиновен» — истец платит {FALSE_FINE} см за ложный донос. Времени на суд — 15 минут.\n"
            f"⚖️ Виновен: <b>{ng}</b> · 😇 Невиновен: <b>{ni}</b>")


def _trial_kb(tid: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="⚖️ Виновен", callback_data=f"trl:g:{tid}"), Btn(text="😇 Невиновен", callback_data=f"trl:i:{tid}")]])


@router.message(Command("sud", "court"))
async def cmd_sud(message: Message, command: CommandObject) -> None:
    if message.chat.type == "private" or not _is_player(message.from_user):
        await message.reply("Суд заседает только в групповом чате.")
        return
    tid, tname, _, text = await _parse(message, command, False)
    if not tid or len(text) < 3:
        await message.reply(SUD_USAGE)
        return
    me = message.from_user
    if tid == me.id:
        await message.reply("Подавать иск на себя нельзя, самосуд отменяется 🙂")
        return
    await db.ensure_user(me.id, message.chat.id, display_name(me))
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM trial WHERE chat_id = ? AND plaintiff_id = ? AND created >= ?", (message.chat.id, me.id, int(time.time()) - 86400))
        if (await cur.fetchone())[0] >= 1:
            await message.reply("Иск можно подавать раз в сутки: суд тоже устаёт.")
            return
        cur = await conn.execute("SELECT COUNT(*) FROM trial WHERE chat_id = ? AND defendant_id = ? AND status = 'open'", (message.chat.id, tid))
        if (await cur.fetchone())[0]:
            await message.reply("На этого человека уже идёт суд.")
            return
        cur = await conn.execute(
            """INSERT INTO trial (chat_id, plaintiff_id, plaintiff_name, defendant_id, defendant_name, text, created)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (message.chat.id, me.id, display_name(me), tid, tname, text[:200], int(time.time())),
        )
        await conn.commit()
        trid = cur.lastrowid
    t = await _load("trial", trid)
    sent = await message.answer(_trial_text(t, {}), reply_markup=_trial_kb(trid))
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE trial SET msg_id = ? WHERE id = ?", (sent.message_id, trid))
        await conn.commit()


async def _fine(chat_id: int, uid: int, name: str, amount: int) -> int:
    size = (await db.ensure_user(uid, chat_id, name))[0]
    fine = min(amount, max(0, size))
    if fine:
        await db.add_size(uid, chat_id, name, -fine, "court")
    return fine


async def _close_trial(bot: Bot, t: dict, guilty: Optional[bool]) -> None:
    if not await _set_status("trial", t["id"], "open", "done"):
        return
    if guilty is None:
        text = f"⚖️ <b>Суд закрыт за неявкой присяжных.</b> Дело «{esc(t['text'])}» развалилось, все свободны."
    else:
        line = await _judge_line(t["text"], t["plaintiff_name"], t["defendant_name"], guilty)
        if guilty:
            fine = await _fine(t["chat_id"], t["defendant_id"], t["defendant_name"], TRIAL_FINE)
            term = random.randint(10, 25)
            async with aiosqlite.connect(DB_PATH) as conn:  # срок отбывается в «Районе»
                await conn.execute("UPDATE rp_player SET jail_until = MAX(jail_until, ?), esc_used = 0 WHERE chat_id = ? AND user_id = ?",
                                   (int(time.time()) + term * 60, t["chat_id"], t["defendant_id"]))
                await conn.commit()
            text = (f"⚖️ <b>Приговор:</b> {esc(t['defendant_name'])} виновен! Штраф {fine} см и срок {term} мин. в тюрьме Района.\n{esc(line)}")
            await highlights.add(t["chat_id"], "⚖️", f"Суд признал {t['defendant_name']} виновным: «{t['text'][:60]}»")
        else:
            fine = await _fine(t["chat_id"], t["plaintiff_id"], t["plaintiff_name"], FALSE_FINE)
            text = f"⚖️ <b>Приговор:</b> {esc(t['defendant_name'])} невиновен! {esc(t['plaintiff_name'])} платит {fine} см за ложный донос.\n{esc(line)}"
            await highlights.add(t["chat_id"], "😇", f"Суд оправдал {t['defendant_name']}: «{t['text'][:60]}»")
    await _edit(bot, t["chat_id"], t["msg_id"], text)


@router.callback_query(F.data.startswith("trl:"))
async def cb_trial(cb: CallbackQuery, bot: Bot) -> None:
    _, side, tid_s = cb.data.split(":")
    t = await _load("trial", int(tid_s))
    if not t or t["status"] != "open":
        await cb.answer("Суд уже закрыт")
        return
    uid = cb.from_user.id
    if uid in (t["plaintiff_id"], t["defendant_id"]):
        await cb.answer("Стороны не голосуют")
        return
    votes = json.loads(t["votes"] or "{}")
    votes[str(uid)] = side
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE trial SET votes = ? WHERE id = ? AND status = 'open'", (json.dumps(votes), t["id"]))
        await conn.commit()
    ng = sum(1 for v in votes.values() if v == "g")
    ni = sum(1 for v in votes.values() if v == "i")
    if ng >= TRIAL_VOTES or ni >= TRIAL_VOTES:
        await _close_trial(bot, t, ng >= TRIAL_VOTES)
    else:
        await _edit(bot, t["chat_id"], t["msg_id"], _trial_text(t, votes), _trial_kb(t["id"]))
    await cb.answer("Голос принят")


# ═══════════════════════ клад ═══════════════════════

@router.callback_query(F.data == "ev:tr")
async def cb_treasure(cb: CallbackQuery, bot: Bot) -> None:
    if not cb.message or not _is_player(cb.from_user):
        await cb.answer()
        return
    name = display_name(cb.from_user)
    await db.ensure_user(cb.from_user.id, cb.message.chat.id, name)
    result = await chat_events.claim_treasure(bot, cb.message.chat.id, cb.from_user.id, name, cb.message.message_id)
    if result:
        await _edit(bot, cb.message.chat.id, cb.message.message_id, result)
        await cb.answer("Твой клад!")
    else:
        await cb.answer("Опоздал: клад уже забрали")


# ═══════════════════════ просрочка ═══════════════════════

async def expire(bot: Bot) -> None:
    """Раз в минуту: закрывает просроченные споры, предсказания и суды."""
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        offers = [dict(r) for r in await (await conn.execute("SELECT * FROM dispute WHERE status = 'offer' AND created < ?", (now - DISPUTE_OFFER_TTL,))).fetchall()]
        actives = [dict(r) for r in await (await conn.execute("SELECT * FROM dispute WHERE status = 'active' AND created < ?", (now - DISPUTE_TTL,))).fetchall()]
        preds = [dict(r) for r in await (await conn.execute("SELECT * FROM prediction WHERE status = 'open' AND created < ?", (now - PREDICT_TTL,))).fetchall()]
        trials = [dict(r) for r in await (await conn.execute("SELECT * FROM trial WHERE status = 'open' AND created < ?", (now - TRIAL_TTL,))).fetchall()]
    for d in offers:
        if await _set_status("dispute", d["id"], "offer", "cancel"):
            await _edit(bot, d["chat_id"], d["msg_id"], f"🤝 Спор «{esc(d['text'])}» не приняли вовремя.")
    for d in actives:
        if await _set_status("dispute", d["id"], "active", "cancel"):
            await _refund_dispute(d)
            await _edit(bot, d["chat_id"], d["msg_id"], f"🤝 Спор «{esc(d['text'])}» никто не рассудил за двое суток, ставки возвращены.")
    for p in preds:
        await _settle_prediction(bot, p, "x")
    for t in trials:
        votes = json.loads(t["votes"] or "{}")
        ng = sum(1 for v in votes.values() if v == "g")
        ni = sum(1 for v in votes.values() if v == "i")
        await _close_trial(bot, t, None if ng + ni == 0 else ng > ni)
