"""Живые события района (раз в 2–4 часа, 30 минут) и народный сход (закон на неделю)."""
import json
import logging
import random
import time
from typing import Optional

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc, now_local

log = logging.getLogger(__name__)
router = Router()


def now() -> int:
    return int(time.time())


async def _kget(conn, key: str) -> int:
    return int(await gangwar.kget(conn, key) or 0)


async def _kset(conn, key: str, val) -> None:
    await gangwar.kset(conn, key, str(val))


# ═════════════════════ законы и модификаторы ═════════════════════
LAWS = {
    "happy": ("🍻", "Счастливый час", "Бар дешевле на 30%, полиция получает +10% бюджета, но работа платит на 10% меньше", {"bar_discount": 0.3, "work_mult": 0.9, "police_fund_mult": 1.1}),
    "curfew": ("🌙", "Комендантский час", "С 23:00 до 7:00 дела запрещены, одиночек атакуют реже, полиция получает +20% бюджета", {"night_block": True, "attack_user_gap_mult": 1.5, "police_fund_mult": 1.2}),
    "trade": ("💼", "Свободная торговля", "Продажа и зарплата +10%, налоговая берёт больше, полиция получает +15% бюджета", {"sell_mult": 1.1, "work_mult": 1.1, "tax_mult": 1.5, "police_fund_mult": 1.15}),
    "arms": ("🔫", "Разрешение на оружие", "Оружие дешевле на 25%, шанс отбиться +10%, полиция получает +15% бюджета, но сроки длиннее", {"weapon_discount": 0.25, "repel_bonus": 0.10, "jail_mult": 1.3, "police_fund_mult": 1.15}),
    "amnesty": ("🕊", "Амнистия", "Срок за дела вдвое короче, добыча меньше на 10%, полиция получает +20% бюджета", {"jail_mult": 0.5, "crime_pay_mult": 0.9, "police_fund_mult": 1.2}),
}
LAW_DAYS, VOTE_HOURS, VOTE_MIN = 7, 24, 3
DEFAULT = {"bar_discount": 0.0, "work_mult": 1.0, "night_block": False, "attack_user_gap_mult": 1.0, "sell_mult": 1.0, "tax_mult": 1.0,
           "weapon_discount": 0.0, "repel_bonus": 0.0, "jail_mult": 1.0, "crime_pay_mult": 1.0, "crime_penalty": 0.0, "police_fund_mult": 1.0}


async def mods(conn, chat_id: int) -> dict:
    """Действующие модификаторы: закон недели и события (например, отключение света)."""
    out = dict(DEFAULT)
    t = now()
    try:
        raw = await gangwar.kget(conn, f"law:{chat_id}")
        law = json.loads(raw) if raw else None
        if law and law.get("until", 0) > t and law["key"] in LAWS:
            for k, v in LAWS[law["key"]][3].items():
                out[k] = v
        import gangplus10
        mm = await gangplus10.mayor_mods(conn, chat_id)
        out["tax_mult"] *= mm["tax"]
        out["night_block"] = out["night_block"] or mm["curfew"]
        import world
        wm = await world.mods(conn, chat_id)
        out["work_mult"] *= wm.get("work_mult", 1.0)
        out["sell_mult"] *= wm.get("sell_mult", 1.0)
        import doom
        dm = await doom.mods(conn, chat_id)
        out["work_mult"] *= dm["work_mult"]
        out["sell_mult"] *= dm["sell_mult"]
        out["tax_mult"] *= dm["tax_mult"]
        out["jail_mult"] *= dm["jail_mult"]
        out["night_block"] = out["night_block"] or dm["night_block"]
        out["crime_pay_mult"] *= dm["crime_pay_mult"]
        out["crime_penalty"] += dm["crime_penalty"]
        cur = await conn.execute("SELECT 1 FROM live_event WHERE chat_id = ? AND kind = 'blackout' AND status = 'open' AND ends > ?", (chat_id, t))
        if await cur.fetchone():
            out["bar_discount"] = max(out["bar_discount"], 0.3)
            out["crime_penalty"] = 0.15
    except Exception:
        log.exception("Не удалось прочитать модификаторы")
    return out


async def get_mods(chat_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        return await mods(conn, chat_id)


# ═════════════════════ события ═════════════════════
EVENTS = {
    "crash": {"icon": "🚗", "title": "Авария на трассе", "mean": 3,
              "text": "На трассе столкнулись машины, дорогу перекрыло. Нужно всем чатом растащить обломки (каждый может помочь до 3 раз, ⚡5).",
              "buttons": [("tow", "🚗 Растащить")]},
    "boss": {"icon": "🕴", "title": "Приезжий авторитет", "mean": 3,
             "text": "В район приехал авторитет и требует дань. Скиньтесь, откупитесь или попробуйте «отжать» у него (⚡15, 40% удачи). Не соберёте: он обчистит всех.",
             "buttons": [("pay100", "💰 Скинуться 100 ₽"), ("pay500", "💰 500 ₽"), ("push", "👊 Отжать")]},
    "shawarma": {"icon": "🌯", "title": "Налёт на шаурмичную", "mean": 3,
                 "text": "Хулиганы громят любимую шаурмичную района! Отбейте её всем чатом (каждый до 4 раз, ⚡10).",
                 "buttons": [("defend", "🌯 Отбиваться")]},
    "blackout": {"icon": "💡", "title": "Отключили свет", "mean": 2,
                 "text": "В районе погас свет. В баре скидка 30%, но в темноте дела проваливаются чаще (шанс −15%). Действует, пока не включат.",
                 "buttons": []},
}
EV_MINUTES = 30
EV_ACTS = {
    "crash": {"tow": (5, 3)},
    "boss": {"pay100": (0, 99), "pay500": (0, 99), "push": (15, 3)},
    "shawarma": {"defend": (10, 4)},
}


def _bar(p: int, g: int, n: int = 10) -> str:
    f = min(n, int(n * p / max(1, g)))
    return "▓" * f + "░" * (n - f)


async def _nplayers(conn, chat_id: int) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM rp_player WHERE chat_id = ?", (chat_id,))
    return (await cur.fetchone())[0]


def _goal(kind: str, n: int) -> int:
    if kind == "crash":
        return max(6, min(20, 2 * n))
    if kind == "boss":
        return min(6000, 2000 + 400 * n)
    if kind == "shawarma":
        return max(8, min(20, 2 * n))
    return 0


def _text(ev: dict, final: str = "") -> str:
    sp = EVENTS[ev["kind"]]
    left = max(0, ev["ends"] - now())
    out = f"{sp['icon']} <b>{sp['title']}</b>\n\n{sp['text']}"
    if ev["goal"]:
        unit = " ₽" if ev["kind"] == "boss" else ""
        out += f"\n\n{_bar(ev['progress'], ev['goal'])} {ev['progress']}/{ev['goal']}{unit}"
    if final:
        return out + f"\n\n{final}"
    return out + f"\n⏳ Осталось {max(1, left // 60)} мин."


def _kb(ev: dict, chat_id: int) -> InlineKeyboardMarkup:
    import keyboards as K
    rows = []
    sp = EVENTS[ev["kind"]]
    if ev["status"] == "open" and sp.get("buttons"):
        rows.append([InlineKeyboardButton(text=t, callback_data=f"ev:{ev['id']}:{a}") for a, t in sp["buttons"]])
    rows.append([K.app_link_button("📱 Открыть Район", "rp", chat_id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ev(conn, eid: int, chat_id: int) -> Optional[dict]:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM live_event WHERE id = ? AND chat_id = ?", (eid, chat_id))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _finish(conn, ev: dict, won: bool) -> tuple:
    """Закрывает событие. Возвращает (итог-текст, список выдач см)."""
    kind, chat_id = ev["kind"], ev["chat_id"]
    await conn.execute("UPDATE live_event SET status = ? WHERE id = ?", ("won" if won else "lost", ev["id"]))
    cur = await conn.execute("SELECT user_id, n, amount FROM live_event_part WHERE event_id = ?", (ev["id"],))
    parts = await cur.fetchall()
    grants = []
    if kind == "blackout":
        return "💡 Свет дали. Всё вернулось на круги своя.", grants
    if won:
        for uid, n, amount in parts:
            await rp._load(conn, chat_id, uid)
            if kind == "crash":
                await conn.execute("UPDATE rp_player SET money = money + 100 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
                grants.append((chat_id, uid, 2))
            elif kind == "boss":
                await conn.execute("UPDATE rp_player SET money = money + 300, energy = MIN(energy + 20, 150) WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            elif kind == "shawarma":
                await conn.execute("UPDATE rp_player SET money = money + 400, energy = MIN(energy + 20, 150) WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
                grants.append((chat_id, uid, 1))
        reward = {"crash": "каждому участнику +100 ₽ и +2 см", "boss": "авторитет уехал, каждому участнику +300 ₽ и ⚡+20",
                  "shawarma": "шаурмичная спасена: каждому участнику +400 ₽, +1 см и ⚡+20"}[kind]
        text = f"🏆 <b>Успех!</b> {len(parts)} чел. справились: {reward}."
    else:
        text = "💥 Не справились."
        if kind == "boss":
            cur = await conn.execute("SELECT user_id, money FROM rp_player WHERE chat_id = ? AND money > 0", (chat_id,))
            total = 0
            for uid, money in await cur.fetchall():
                loss = min(400, int(money * 0.03))
                if loss:
                    await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (loss, chat_id, uid))
                    total += loss
            text += f" Авторитет обчистил район на {total} ₽."
        elif kind == "crash":
            text += " Пробка стояла полдня, награды не будет."
        else:
            text += " Шаурмичную разнесли в щепки."
    await rp._news(conn, chat_id, f"{EVENTS[kind]['icon']} {EVENTS[kind]['title']}: {'успех' if won else 'провал'}")
    return text, grants


async def _grant_cm(grants: list) -> None:
    if not grants:
        return
    from database import add_size
    for chat_id, uid, delta in grants:
        try:
            async with aiosqlite.connect(DB_PATH) as conn:
                cur = await conn.execute("SELECT username FROM users WHERE user_id = ? AND chat_id = ?", (uid, chat_id))
                row = await cur.fetchone()
            await add_size(uid, chat_id, row[0] if row else "", delta, "event")
        except Exception:
            log.exception("Не удалось выдать см за событие")


async def act(chat_id: int, uid: int, name: str, eid, action: str) -> dict:
    """Действие в событии (вызывается и из чата, и из мини-аппа). Возвращает {text, good, event}."""
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        ev = await _ev(conn, int(eid), chat_id)
        if not ev or ev["status"] != "open" or ev["ends"] <= now():
            raise GameError("Событие уже закончилось")
        spec = EV_ACTS.get(ev["kind"], {})
        if action not in spec:
            raise GameError("Здесь так не получится")
        cost, per = spec[action]
        p = await rp._load(conn, chat_id, uid)
        if p["jail_until"] > now():
            raise GameError("Ты за решёткой: событиям не до тебя")
        cur = await conn.execute("SELECT n, amount FROM live_event_part WHERE event_id = ? AND user_id = ?", (ev["id"], uid))
        row = await cur.fetchone()
        n, amount = (row[0], row[1]) if row else (0, 0)
        if n >= per:
            raise GameError("Ты уже внёс свой вклад: пусть помогут другие")
        if cost:
            rp._need(p, cost)
            p["energy"] -= cost
        good, text, gain, paid = True, "", 0, 0
        if action == "tow":
            gain, text = 1, "🚗 Ты оттащил машину: +1 к общему прогрессу."
        elif action in ("pay100", "pay500"):
            paid = 100 if action == "pay100" else 500
            if p["money"] < paid:
                raise GameError(f"Нужно {paid} ₽")
            p["money"] -= paid
            gain, text = paid, f"💰 Ты скинулся на {paid} ₽ в общую дань."
        elif action == "push":
            if random.random() < 0.40:
                gain, text = 700, "👊 Отжал у авторитета! +700 ₽ в общую копилку."
            else:
                fine = min(p["money"], 150)
                p["money"] -= fine
                p["wanted"], p["wanted_ts"] = min(rp.MAX_WANTED, p["wanted"] + 1), now()
                text, good = f"👊 Не вышло: авторитет отвесил подзатыльник, штраф {fine} ₽, розыск +1⭐.", False
        elif action == "defend":
            gain = 1 + (1 if random.random() < 0.30 else 0)
            text = f"🌯 Ты отбил налёт: +{gain} к общему прогрессу."
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("INSERT INTO live_event_part (event_id, user_id, n, amount) VALUES (?, ?, 1, ?) ON CONFLICT(event_id, user_id) DO UPDATE SET n = n + 1, amount = amount + ?", (ev["id"], uid, paid, paid))
        prog = ev["progress"] + gain
        await conn.execute("UPDATE live_event SET progress = ? WHERE id = ?", (prog, ev["id"]))
        ev["progress"] = prog
        grants = []
        if prog >= ev["goal"] > 0:
            summary, grants = await _finish(conn, ev, True)
            ev["status"] = "won"
            await gangwar.say(conn, chat_id, f"{EVENTS[ev['kind']]['icon']} <b>{EVENTS[ev['kind']]['title']}</b>: {summary}")
            text += " Цель достигнута!"
        await conn.commit()
    await _grant_cm(grants)
    return {"text": text, "good": good, "delta": 0, "event": ev}


async def act_app(chat_id: int, uid: int, name: str, eid, action: str) -> dict:
    res = await act(chat_id, uid, name, eid, action)
    res.pop("event", None)
    res["state"] = await rp.state(chat_id, uid)
    return res


@router.callback_query(F.data.startswith("ev:"))
async def cb_event(callback: CallbackQuery) -> None:
    _, eid, action = callback.data.split(":", 2)
    chat_id = callback.message.chat.id
    u = callback.from_user
    from database import ensure_user
    name = f"@{u.username}" if u.username else u.full_name
    await ensure_user(u.id, chat_id, name)
    try:
        res = await act(chat_id, u.id, name, eid, action)
    except GameError as exc:
        await callback.answer(str(exc)[:190], show_alert=False)
        return
    await callback.answer(res["text"][:190])
    ev = res["event"]
    async with aiosqlite.connect(DB_PATH) as conn:
        last = await _kget(conn, f"ev:edit:{ev['id']}")
        if now() - last >= 6 or ev["status"] != "open":
            await _kset(conn, f"ev:edit:{ev['id']}", now())
            await conn.commit()
        else:
            return
    try:
        await callback.message.edit_text(_text(ev, "🏆 Событие выполнено!" if ev["status"] == "won" else ""), reply_markup=_kb(ev, chat_id))
    except TelegramAPIError:
        pass


# ═════════════════════ народный сход ═════════════════════
def _vote_kb(vid: int, chat_id: int) -> InlineKeyboardMarkup:
    import keyboards as K
    rows = [[InlineKeyboardButton(text=f"{v[0]} {v[1]}", callback_data=f"law:{vid}:{k}")] for k, v in LAWS.items()]
    rows.append([K.app_link_button("📱 Подробности", "rp", chat_id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _vote_text(tally: dict, left: int) -> str:
    lines = ["🏛 <b>Народный сход!</b> Чат голосует за закон района на неделю (нужно минимум 3 голоса).\n"]
    for k, v in LAWS.items():
        lines.append(f"{v[0]} <b>{v[1]}</b> ({tally.get(k, 0)}): {v[2]}")
    lines.append(f"\n⏳ До конца голосования {max(1, left // 3600)} ч. Голос можно менять.")
    return "\n".join(lines)


async def _tally(conn, vid: int) -> dict:
    cur = await conn.execute("SELECT law, COUNT(*) FROM law_ballot WHERE vote_id = ? GROUP BY law", (vid,))
    return {r[0]: r[1] for r in await cur.fetchall()}


async def law_vote(chat_id: int, uid: int, name: str, vid, law: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM law_vote WHERE id = ? AND chat_id = ?", (int(vid), chat_id))
        v = await cur.fetchone()
        if not v or v["status"] != "open" or v["closes"] <= now():
            raise GameError("Голосование уже закончилось")
        if law not in LAWS:
            raise GameError("Такого закона нет")
        cur = await conn.execute("SELECT 1 FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        if not await cur.fetchone():
            await rp._load(conn, chat_id, uid)
        await conn.execute("INSERT INTO law_ballot (vote_id, user_id, law) VALUES (?, ?, ?) ON CONFLICT(vote_id, user_id) DO UPDATE SET law = excluded.law", (v["id"], uid, law))
        await conn.commit()
        tally = await _tally(conn, v["id"])
    return {"text": f"🏛 Твой голос: {LAWS[law][0]} {LAWS[law][1]}", "good": True, "delta": 0, "tally": tally, "closes": v["closes"], "msg": v["msg_id"]}


async def law_vote_app(chat_id: int, uid: int, name: str, vid, law: str) -> dict:
    res = await law_vote(chat_id, uid, name, vid, law)
    for k in ("tally", "closes", "msg"):
        res.pop(k, None)
    res["state"] = await rp.state(chat_id, uid)
    return res


@router.callback_query(F.data.startswith("law:"))
async def cb_law(callback: CallbackQuery) -> None:
    _, vid, law = callback.data.split(":", 2)
    chat_id = callback.message.chat.id
    u = callback.from_user
    from database import ensure_user
    name = f"@{u.username}" if u.username else u.full_name
    await ensure_user(u.id, chat_id, name)
    try:
        res = await law_vote(chat_id, u.id, name, vid, law)
    except GameError as exc:
        await callback.answer(str(exc)[:190])
        return
    await callback.answer(res["text"][:190])
    try:
        await callback.message.edit_text(_vote_text(res["tally"], res["closes"] - now()), reply_markup=_vote_kb(int(vid), chat_id))
    except TelegramAPIError:
        pass


async def _law_tick(bot: Bot, conn, chat_id: int) -> None:
    t = now()
    n = now_local()
    week = f"{n.isocalendar()[0]}-{n.isocalendar()[1]}"
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM law_vote WHERE chat_id = ? AND status = 'open'", (chat_id,))
    v = await cur.fetchone()
    if v and v["closes"] <= t:
        v = dict(v)
        tally = await _tally(conn, v["id"])
        total = sum(tally.values())
        if total < VOTE_MIN:
            await conn.execute("UPDATE law_vote SET status = 'done', result = '' WHERE id = ?", (v["id"],))
            text = f"🏛 Сход не состоялся: проголосовало {total} из {VOTE_MIN} нужных. Неделя пройдёт без нового закона."
        else:
            top = max(tally.values())
            key = random.choice([k for k, c in tally.items() if c == top])
            await conn.execute("UPDATE law_vote SET status = 'done', result = ? WHERE id = ?", (key, v["id"]))
            await _kset(conn, f"law:{chat_id}", json.dumps({"key": key, "until": t + LAW_DAYS * 86400}))
            ic, title, desc, _m = LAWS[key]
            text = f"🏛 <b>Закон принят: {ic} {title}!</b>\n{desc}.\nДействует {LAW_DAYS} дней. Голосов за него: {top} из {total}."
            await rp._news(conn, chat_id, f"🏛 Принят закон: {title}")
        await conn.commit()
        try:
            await bot.send_message(chat_id, text)
            if v.get("msg_id"):
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=v["msg_id"], reply_markup=None)
        except TelegramAPIError:
            pass
        return
    if v:
        return
    cur = await conn.execute("SELECT 1 FROM law_vote WHERE chat_id = ? AND week = ?", (chat_id, week))
    if await cur.fetchone():
        return
    cur = await conn.execute("INSERT INTO law_vote (chat_id, week, opens, closes, status) VALUES (?, ?, ?, ?, 'open')", (chat_id, week, t, t + VOTE_HOURS * 3600))
    vid = cur.lastrowid
    await conn.commit()
    try:
        m = await bot.send_message(chat_id, _vote_text({}, VOTE_HOURS * 3600), reply_markup=_vote_kb(vid, chat_id))
        await conn.execute("UPDATE law_vote SET msg_id = ? WHERE id = ?", (m.message_id, vid))
        await conn.commit()
    except TelegramAPIError:
        log.exception("Не удалось отправить сход")


# ═════════════════════ планировщик ═════════════════════
async def _event_tick(bot: Bot, conn, chat_id: int) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM live_event WHERE chat_id = ? AND status = 'open'", (chat_id,))
    ev = await cur.fetchone()
    if ev:
        ev = dict(ev)
        if ev["ends"] <= t:
            summary, grants = await _finish(conn, ev, ev["goal"] > 0 and ev["progress"] >= ev["goal"])
            await conn.commit()
            await _grant_cm(grants)
            ev["status"] = "done"
            try:
                if ev["msg_id"]:
                    await bot.edit_message_text(_text(ev, summary), chat_id=chat_id, message_id=ev["msg_id"], reply_markup=_kb(ev, chat_id))
                await bot.send_message(chat_id, f"{EVENTS[ev['kind']]['icon']} {summary}")
            except TelegramAPIError:
                pass
        elif ev["msg_id"] and ev["goal"] and t - await _kget(conn, f"ev:edit:{ev['id']}") >= 120:
            await _kset(conn, f"ev:edit:{ev['id']}", t)
            await conn.commit()
            try:
                await bot.edit_message_text(_text(ev), chat_id=chat_id, message_id=ev["msg_id"], reply_markup=_kb(ev, chat_id))
            except TelegramAPIError:
                pass
        return
    nxt = await _kget(conn, f"ev:next:{chat_id}")
    if not nxt:
        await _kset(conn, f"ev:next:{chat_id}", t + random.randint(1800, 3600))
        await conn.commit()
        return
    if t < nxt or not 10 <= now_local().hour < 23:
        return
    await start_event(bot, conn, chat_id)


async def start_event(bot: Bot, conn, chat_id: int, kind: str = "") -> Optional[int]:
    t = now()
    kind = kind or random.choices(list(EVENTS), weights=[EVENTS[k]["mean"] for k in EVENTS])[0]
    goal = _goal(kind, await _nplayers(conn, chat_id))
    cur = await conn.execute("INSERT INTO live_event (chat_id, kind, started, ends, progress, goal, status) VALUES (?, ?, ?, ?, 0, ?, 'open')", (chat_id, kind, t, t + EV_MINUTES * 60, goal))
    eid = cur.lastrowid
    await _kset(conn, f"ev:next:{chat_id}", t + random.randint(2 * 3600, 4 * 3600))
    await rp._news(conn, chat_id, f"{EVENTS[kind]['icon']} {EVENTS[kind]['title']}!")
    await conn.commit()
    ev = {"id": eid, "chat_id": chat_id, "kind": kind, "started": t, "ends": t + EV_MINUTES * 60, "progress": 0, "goal": goal, "status": "open"}
    try:
        m = await bot.send_message(chat_id, _text(ev), reply_markup=_kb(ev, chat_id))
        await conn.execute("UPDATE live_event SET msg_id = ? WHERE id = ?", (m.message_id, eid))
        await conn.commit()
    except TelegramAPIError:
        log.exception("Не удалось отправить событие")
    return eid


async def tick(bot: Bot, chats) -> None:
    for chat_id in chats:
        try:
            async with aiosqlite.connect(DB_PATH) as conn:
                cur = await conn.execute("SELECT COUNT(*) FROM rp_player WHERE chat_id = ?", (chat_id,))
                if (await cur.fetchone())[0] < 2:
                    continue
                await _event_tick(bot, conn, chat_id)
                await _law_tick(bot, conn, chat_id)
        except Exception:
            log.exception("Сбой событий района в чате %s", chat_id)


# ═════════════════════ интерфейс ═════════════════════
async def extras(conn, chat_id: int, uid: int, out: dict) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    p7 = {"events": [], "law": None, "vote": None}
    cur = await conn.execute("SELECT * FROM live_event WHERE chat_id = ? AND status = 'open' AND ends > ?", (chat_id, t))
    for ev in [dict(r) for r in await cur.fetchall()]:
        sp = EVENTS[ev["kind"]]
        cur2 = await conn.execute("SELECT n, amount FROM live_event_part WHERE event_id = ? AND user_id = ?", (ev["id"], uid))
        row = await cur2.fetchone()
        n = row[0] if row else 0
        cur2 = await conn.execute("SELECT COUNT(*) FROM live_event_part WHERE event_id = ?", (ev["id"],))
        p7["events"].append({"id": ev["id"], "kind": ev["kind"], "icon": sp["icon"], "title": sp["title"], "text": sp["text"], "left": ev["ends"] - t,
                             "progress": ev["progress"], "goal": ev["goal"], "people": (await cur2.fetchone())[0], "my_n": n,
                             "buttons": [{"key": a, "title": b, "left": max(0, EV_ACTS[ev["kind"]][a][1] - n)} for a, b in sp.get("buttons", [])]})
    raw = await gangwar.kget(conn, f"law:{chat_id}")
    law = json.loads(raw) if raw else None
    if law and law.get("until", 0) > t and law["key"] in LAWS:
        v = LAWS[law["key"]]
        p7["law"] = {"key": law["key"], "icon": v[0], "title": v[1], "desc": v[2], "left": law["until"] - t}
    cur = await conn.execute("SELECT * FROM law_vote WHERE chat_id = ? AND status = 'open' AND closes > ?", (chat_id, t))
    v = await cur.fetchone()
    if v:
        tally = await _tally(conn, v["id"])
        cur = await conn.execute("SELECT law FROM law_ballot WHERE vote_id = ? AND user_id = ?", (v["id"], uid))
        mine = await cur.fetchone()
        p7["vote"] = {"id": v["id"], "left": v["closes"] - t, "mine": mine[0] if mine else "", "total": sum(tally.values()), "need": VOTE_MIN,
                      "laws": [{"key": k, "icon": x[0], "title": x[1], "desc": x[2], "votes": tally.get(k, 0)} for k, x in LAWS.items()]}
    out["p7"] = p7
