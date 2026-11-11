"""ОПГ-плюс 9: долговая книга, династия и зал славы, эмблема, кодекс и суд, сезон контроля, перебежчики,
комнаты штаба, штурм и мирные договоры."""
import json
import logging
import random
import time
from typing import Optional

import aiosqlite

import gang as G
import gangplus as GP
import gangplus2 as G2
import gangplus3 as P3
import gangplus5 as P5
import gangplus8 as P8
import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc, now_local

log = logging.getLogger(__name__)


def now() -> int:
    return int(time.time())


async def _gx(conn, gid: int) -> Optional[dict]:
    return await gangwar._gang_row(conn, gid)


async def hall_add(conn, gid: int, chat_id: int, kind: str, uid: int = 0, name: str = "", text: str = "") -> None:
    await conn.execute("INSERT INTO gang_hall (gang_id, chat_id, kind, uid, name, text, ts) VALUES (?, ?, ?, ?, ?, ?, ?)", (gid, chat_id, kind, uid, name, text, now()))


# ═════════════════════ долговая книга ═════════════════════
DEBT_MAX, DEBT_RATE, DEBT_HOURS, DEBT_WRITE_OFF = 2000, 0.2, 48, 3 * 86400


async def debt_lend(chat_id: int, uid: int, name: str, target, amount) -> dict:
    target, amount = int(target), int(amount or 0)

    async def run(conn):
        g, _, _r = await P8._need_dep(conn, chat_id, uid)
        if not 100 <= amount <= DEBT_MAX:
            raise GameError(f"Заём от 100 до {DEBT_MAX} ₽")
        if (g["treasury"] or 0) < amount:
            raise GameError("В общаке нет столько")
        cur = await conn.execute("SELECT 1 FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        if not await cur.fetchone() or target == uid:
            raise GameError("Выбери другого игрока")
        cur = await conn.execute("SELECT 1 FROM debt WHERE chat_id = ? AND debtor = ? AND status IN ('open', 'late')", (chat_id, target))
        if await cur.fetchone():
            raise GameError("У него уже есть долг")
        owed = int(amount * (1 + DEBT_RATE))
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (amount, g["id"]))
        await rp._load(conn, chat_id, target)
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, target))
        await conn.execute("INSERT INTO debt (chat_id, gang_id, debtor, principal, owed, due, status, created) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)", (chat_id, g["id"], target, amount, owed, now() + DEBT_HOURS * 3600, now()))
        tn = await P3._uname(conn, chat_id, target)
        await gangwar.say(conn, chat_id, f"📒 «{esc(g['name'])}» выдала заём {esc(tn)}: {amount} ₽, вернуть {owed} ₽ за {DEBT_HOURS} ч. Должник виден всем.")
        return P3._res(f"📒 Заём выдан: {tn} вернёт {owed} ₽ за {DEBT_HOURS} часов.")
    return await P3._act(chat_id, uid, run)


async def debt_repay(chat_id: int, uid: int, name: str, did) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM debt WHERE id = ? AND chat_id = ? AND debtor = ? AND status IN ('open', 'late')", (int(did), chat_id, uid))
        r = await cur.fetchone()
        if not r:
            raise GameError("Долга нет")
        d = dict(r)
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < d["owed"]:
            raise GameError(f"Нужно {d['owed']} ₽")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (d["owed"], chat_id, uid))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (d["owed"], d["gang_id"]))
        await conn.execute("UPDATE debt SET status = 'paid' WHERE id = ?", (d["id"],))
        await P5.rep_add(conn, d["gang_id"], 1, 0)
        return P3._res(f"📒 Долг закрыт: −{d['owed']} ₽. Книга чиста.", True, -d["owed"])
    return await P3._act(chat_id, uid, run)


async def debt_audit(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Ревизию проводит босс")
        if await P3.kget(conn, f"p9audit:{g['id']}") > now():
            raise GameError("Ревизия раз в 6 часов")
        await GP._kset(conn, f"p9audit:{g['id']}", now() + 6 * 3600)
        cur = await conn.execute("SELECT COUNT(*), COALESCE(SUM(owed), 0), COALESCE(SUM(CASE WHEN status = 'late' THEN 1 ELSE 0 END), 0) FROM debt WHERE gang_id = ? AND status IN ('open', 'late')", (g["id"],))
        n, owed, late = await cur.fetchone()
        text = f"📒 Ревизия: в общаке {g['treasury']} ₽, в долг выдано на {owed} ₽ ({n} шт., просрочено {late})."
        mole = await P5._mole(conn, g["id"], ("active",))
        if mole and mole["leaks"] and random.random() < 0.5:
            await conn.execute("UPDATE gang_mole SET status = 'found', found = ? WHERE id = ?", (now(), mole["id"]))
            text += f" ⚠️ Выявлена недостача: кто-то тянет деньги. Это чужой агент! Реши его судьбу во вкладке «Разведка»."
        elif mole and mole["leaks"]:
            text += " ⚠️ Есть недостача, которую не удалось объяснить."
        return P3._res(text)
    return await P3._act(chat_id, uid, run)


async def _debts_tick(conn) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM debt WHERE status IN ('open', 'late') AND due <= ?", (t,))
    for d in [dict(r) for r in await cur.fetchall()]:
        g = await _gx(conn, d["gang_id"])
        if not g:
            continue
        name = await P3._uname(conn, d["chat_id"], d["debtor"])
        if d["status"] == "open":
            owed = int(d["owed"] * 1.1)
            await conn.execute("UPDATE debt SET status = 'late', owed = ? WHERE id = ?", (owed, d["id"]))
            await G2._beat(conn, d["chat_id"], d["debtor"], 20)
            await G2._merit(conn, d["chat_id"], d["debtor"], -5)
            await gangwar.say(conn, d["chat_id"], f"🗣 К {esc(name)} пришли «поговорить» от «{esc(g['name'])}»: долг просрочен, теперь {owed} ₽, а ещё 20 минут отлёживаться.")
        elif t - d["due"] > DEBT_WRITE_OFF:
            await conn.execute("UPDATE debt SET status = 'written' WHERE id = ?", (d["id"],))
            await P5.rep_add(conn, d["gang_id"], -2, 0)
            await gangwar.say(conn, d["chat_id"], f"📒 «{esc(g['name'])}» списала безнадёжный долг {esc(name)}. Уважение к банде слегка упало.")


# ═════════════════════ династия ═════════════════════
INACTIVE_DAYS = 5


async def heir_set(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        g, members, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Преемника назначает босс")
        if target == uid or not any(m["user_id"] == target for m in members):
            raise GameError("Преемник должен быть из вашей банды")
        await conn.execute("UPDATE gang SET heir = ? WHERE id = ?", (target, g["id"]))
        return P3._res(f"🧬 Преемник: {await P3._uname(conn, chat_id, target)}. Если ты пропадёшь, банда перейдёт к нему.")
    return await P3._act(chat_id, uid, run)


async def _succeed(conn, chat_id: int, g: dict, new_uid: int, old_uid: int, why: str) -> None:
    oname, nname = await P3._uname(conn, chat_id, old_uid), await P3._uname(conn, chat_id, new_uid)
    await conn.execute("UPDATE gang_member SET role = 'deputy' WHERE chat_id = ? AND user_id = ?" if why == "abdicate" else "UPDATE gang_member SET role = 'grunt' WHERE chat_id = ? AND user_id = ?", (chat_id, old_uid))
    await conn.execute("UPDATE gang_member SET role = 'boss', merit = merit + 10 WHERE chat_id = ? AND user_id = ?", (chat_id, new_uid))
    await conn.execute("UPDATE gang SET boss = ?, heir = 0, legacy = legacy + 1, street = ?, treasury = treasury + 500 WHERE id = ?", (new_uid, f"улица имени {oname}", g["id"]))
    await hall_add(conn, g["id"], chat_id, "boss", old_uid, oname, "правил бандой")
    await rp._news(conn, chat_id, f"🧬 В «{g['name']}» новый босс: {nname}")
    await gangwar.say(conn, chat_id, f"🧬 <b>Смена поколений в «{esc(g['name'])}».</b> Новый босс: {esc(nname)}. Бывшему боссу {esc(oname)} посвятили «улицу имени {esc(oname)}»; наследство: +500 ₽ в общак и +10 авторитета новому боссу.")


async def abdicate(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, members, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Отрекаться может только босс")
        heir = g.get("heir") or 0
        if not heir or not any(m["user_id"] == heir for m in members):
            raise GameError("Сначала назначь преемника")
        await _succeed(conn, chat_id, g, heir, uid, "abdicate")
        return P3._res("🧬 Ты передал власть преемнику и стал замом. Улица названа твоим именем.")
    return await P3._act(chat_id, uid, run)


async def _dynasty_tick(conn) -> None:
    t = now()
    cur = await conn.execute("SELECT id, chat_id, heir FROM gang WHERE is_ai = 0")
    for gid, chat_id, heir in await cur.fetchall():
        g = await _gx(conn, gid)
        cur2 = await conn.execute("SELECT m.user_id, r.energy_ts FROM gang_member m JOIN rp_player r ON r.chat_id = m.chat_id AND r.user_id = m.user_id WHERE m.gang_id = ? AND m.role = 'boss'", (gid,))
        row = await cur2.fetchone()
        if not row or t - (row[1] or t) < INACTIVE_DAYS * 86400:
            continue
        real = await gangwar._real_members(conn, gid)
        if len(real) < 2:
            continue
        new = heir if heir in real and heir != row[0] else None
        if not new:
            cur3 = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id > 0 AND user_id != ? ORDER BY merit DESC LIMIT 1", (gid, row[0]))
            r3 = await cur3.fetchone()
            new = r3[0] if r3 else None
        if new:
            await _succeed(conn, chat_id, g, new, row[0], "gone")


# ═════════════════════ эмблема ═════════════════════
EMBLEMS = ["🐺", "🦅", "🐍", "🦈", "🐻", "🦂", "🐯", "🦁", "🐗", "🦇", "💀", "☠️", "🔥", "⚡", "👑", "🗡", "🎯", "♠️", "♣️", "🌑", "🌟", "🚀", "🏴", "💎"]
COLORS = ["#c0392b", "#e67e22", "#f1c40f", "#27ae60", "#16a085", "#2980b9", "#8e44ad", "#2c3e50", "#7f8c8d", "#e84393"]
AI_LOOK = {gangwar.AI_NAME: ("🧓", "#b07fd6", "#6b3fa0"), "Шиномонтажная мафия": ("🛞", "#e67e22", "#7f4a0c"), "Рыбнадзор": ("🎣", "#2e86c1", "#1b4f72")}
EMBLEM_COST = 500


async def emblem_set(chat_id: int, uid: int, name: str, emoji: str, c1: str, c2: str, motto="") -> dict:
    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Эмблему рисует босс")
        if emoji not in EMBLEMS or c1 not in COLORS or c2 not in COLORS:
            raise GameError("Выбери эмблему и цвета из списка")
        motto_c = " ".join(str(motto or "").split())[:40]
        cost = EMBLEM_COST if g.get("emblem") else 0
        if (g["treasury"] or 0) < cost:
            raise GameError(f"Перерисовать эмблему стоит {cost} ₽ из общака")
        await conn.execute("UPDATE gang SET emblem = ?, color1 = ?, color2 = ?, motto = ?, treasury = treasury - ? WHERE id = ?", (emoji, c1, c2, motto_c, cost, g["id"]))
        return P3._res(f"{emoji} Эмблема обновлена" + (f" (−{cost} ₽)" if cost else " (первый раз бесплатно)") + ". Теперь можно нарисовать граффити.")
    return await P3._act(chat_id, uid, run)


async def graffiti(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if not g.get("emblem"):
            raise GameError("Сначала придумайте эмблему")
        if await P3.kget(conn, f"p9graf:{g['id']}") > now():
            raise GameError("Граффити можно раз в сутки")
        await GP._kset(conn, f"p9graf:{g['id']}", now() + 86400)
        await P5.rep_add(conn, g["id"], 1, 1)
        await gangwar.say(conn, chat_id, f"{g['emblem']}{g['emblem']}{g['emblem']}\n<b>«{esc(g['name'])}»</b>" + (f"\n<i>«{esc(g['motto'])}»</i>" if g.get("motto") else "") + f"\nГраффити появилось на стене района. {g['emblem']}")
        return P3._res("🎨 Граффити нарисовано: весь район увидел ваш знак (+1 уважение, +1 страх).")
    return await P3._act(chat_id, uid, run)


def look(gang: dict) -> tuple:
    if gang.get("emblem"):
        return gang["emblem"], gang.get("color1") or "#7f8c8d", gang.get("color2") or "#2c3e50"
    if gang["name"] in AI_LOOK:
        return AI_LOOK[gang["name"]]
    return "🕴", "#7f8c8d", "#2c3e50"


# ═════════════════════ кодекс и суд ═════════════════════
TRIAL_HOURS = 6


async def rules_set(chat_id: int, uid: int, name: str, text="") -> dict:
    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Кодекс пишет босс")
        rules = [" ".join(x.split())[:60] for x in str(text or "").split("\n") if x.strip()][:5]
        if not rules:
            raise GameError("Напиши хотя бы одно правило (до 5, каждое с новой строки)")
        await conn.execute("DELETE FROM gang_rule WHERE gang_id = ?", (g["id"],))
        for i, r in enumerate(rules):
            await conn.execute("INSERT INTO gang_rule (gang_id, idx, text) VALUES (?, ?, ?)", (g["id"], i, r))
        return P3._res(f"📜 Кодекс обновлён: {len(rules)} правил(а).")
    return await P3._act(chat_id, uid, run)


async def trial_open(chat_id: int, uid: int, name: str, defendant, rule, evidence="") -> dict:
    defendant, rule = int(defendant), int(rule)

    async def run(conn):
        g, members = await G._mine(conn, chat_id, uid)
        if not g or g.get("is_ai"):
            raise GameError("Ты не в банде")
        d = next((m for m in members if m["user_id"] == defendant), None)
        if not d or defendant == uid or d["role"] == "boss":
            raise GameError("Судить можно только другого участника, кроме босса")
        cur = await conn.execute("SELECT text FROM gang_rule WHERE gang_id = ? AND idx = ?", (g["id"], rule))
        r = await cur.fetchone()
        if not r:
            raise GameError("Такого правила в кодексе нет")
        cur = await conn.execute("SELECT 1 FROM gang_trial WHERE gang_id = ? AND status = 'open'", (g["id"],))
        if await cur.fetchone():
            raise GameError("В банде уже идёт разбор")
        if await P3.kget(conn, f"p9trial:{chat_id}:{uid}") > now():
            raise GameError("Обвинять можно раз в 6 часов")
        ev = " ".join(str(evidence or "").split())[:100]
        if not ev:
            raise GameError("Нужны улики: напиши, что именно произошло")
        await GP._kset(conn, f"p9trial:{chat_id}:{uid}", now() + 6 * 3600)
        await conn.execute("INSERT INTO gang_trial (gang_id, chat_id, accuser, defendant, rule, evidence, created, ends, status, votes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', '{}')",
                           (g["id"], chat_id, uid, defendant, rule, ev, now(), now() + TRIAL_HOURS * 3600))
        dn = await P3._uname(conn, chat_id, defendant)
        await gangwar.say(conn, chat_id, f"⚖️ Разбор в «{esc(g['name'])}»: {esc(name)} обвиняет {esc(dn)} в нарушении правила «{esc(r[0])}». Улики: {esc(ev)}. Банда голосует {TRIAL_HOURS} ч.")
        return P3._res(f"⚖️ Разбор начат: банда голосует {TRIAL_HOURS} ч.")
    return await P3._act(chat_id, uid, run)


async def trial_vote(chat_id: int, uid: int, name: str, tid, verdict: str) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM gang_trial WHERE id = ? AND chat_id = ? AND status = 'open'", (int(tid), chat_id))
        r = await cur.fetchone()
        if not r:
            raise GameError("Разбор уже закончен")
        t = dict(r)
        g, members = await G._mine(conn, chat_id, uid)
        if not g or g["id"] != t["gang_id"] or uid in (t["accuser"], t["defendant"]):
            raise GameError("Ты не можешь голосовать в этом разборе")
        if verdict not in ("guilty", "innocent"):
            raise GameError("Выбери вердикт")
        votes = json.loads(t["votes"] or "{}")
        votes[str(uid)] = verdict
        await conn.execute("UPDATE gang_trial SET votes = ? WHERE id = ?", (json.dumps(votes), t["id"]))
        return P3._res("⚖️ Твой голос учтён.")
    return await P3._act(chat_id, uid, run)


async def _trials_tick(conn) -> None:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_trial WHERE status = 'open' AND ends <= ?", (now(),))
    for t in [dict(r) for r in await cur.fetchall()]:
        g = await _gx(conn, t["gang_id"])
        votes = json.loads(t["votes"] or "{}")
        guilty = sum(1 for v in votes.values() if v == "guilty")
        inn = len(votes) - guilty
        dn = await P3._uname(conn, t["chat_id"], t["defendant"])
        if len(votes) < 2:
            res, text = "void", f"⚖️ Разбор в «{esc(g['name'])}» закрыт: слишком мало голосов."
        elif guilty > inn:
            p = await rp._load(conn, t["chat_id"], t["defendant"])
            fine = min(300, p["money"])
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, t["chat_id"], t["defendant"]))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (fine, g["id"]))
            await G2._merit(conn, t["chat_id"], t["defendant"], -10)
            res, text = "guilty", f"⚖️ Банда «{esc(g['name'])}» признала {esc(dn)} виновным ({guilty}:{inn}): штраф {fine} ₽ в общак и −10 авторитета."
        else:
            await G2._merit(conn, t["chat_id"], t["accuser"], -5)
            res, text = "innocent", f"⚖️ {esc(dn)} оправдан ({guilty}:{inn}). Обвинителю за пустые обвинения −5 авторитета."
        await conn.execute("UPDATE gang_trial SET status = ? WHERE id = ?", (res, t["id"]))
        await gangwar.say(conn, t["chat_id"], text)


# ═════════════════════ сезон контроля ═════════════════════
def season_key() -> str:
    return now_local().strftime("%Y-%m")


async def _season_tick(conn) -> None:
    cur = await conn.execute("SELECT DISTINCT chat_id FROM territory")
    for (chat_id,) in await cur.fetchall():
        cur_s = season_key()
        last = await gangwar.kget(conn, f"p9season:{chat_id}")
        if last and last != cur_s:
            cur2 = await conn.execute("SELECT s.gang_id, s.pts, g.name, g.is_ai FROM season_ctl s JOIN gang g ON g.id = s.gang_id WHERE s.chat_id = ? AND s.season = ? ORDER BY s.pts DESC LIMIT 1", (chat_id, last))
            top = await cur2.fetchone()
            if top and top[1] > 0:
                if not top[3]:
                    await conn.execute("UPDATE gang SET treasury = treasury + 3000 WHERE id = ?", (top[0],))
                await hall_add(conn, top[0], chat_id, "season", 0, top[2], f"Сезон {last}: {top[1]} очков контроля")
                await gangwar.kset(conn, f"crown:{chat_id}", str(top[0]))
                await gangwar.say(conn, chat_id, f"👑 <b>Итоги сезона {last}.</b> Район контролировала «{esc(top[2])}» ({top[1]} очков)" + ("." if top[3] else ": +3000 ₽ в общак и корона рядом с названием на месяц."))
        await gangwar.kset(conn, f"p9season:{chat_id}", cur_s)
        hk = f"p9shour:{chat_id}"
        t = now()
        lastc = int(await gangwar.kget(conn, hk) or 0)
        hours = min(6, (t - lastc) // 3600) if lastc else 0
        if not lastc or hours >= 1:
            await gangwar.kset(conn, hk, str(t if not lastc else lastc + hours * 3600))
            if hours:
                own = await P5._owners(conn, chat_id)
                for key, owner in own.items():
                    if not owner:
                        continue
                    pts = hours * (2 if any(own.get(n) == owner for n in P5.NEIGH.get(key, ())) else 1)
                    await conn.execute("INSERT INTO season_ctl (chat_id, season, gang_id, pts) VALUES (?, ?, ?, ?) ON CONFLICT(chat_id, season, gang_id) DO UPDATE SET pts = pts + ?", (chat_id, cur_s, owner, pts, pts))


# ═════════════════════ перебежчики ═════════════════════
DEFECT_CD, DEFECT_HOURS = 6 * 3600, 24


async def defect_offer(chat_id: int, uid: int, name: str, target, offer, role="grunt") -> dict:
    target, offer = int(target), int(offer or 0)

    async def run(conn):
        g, members, mrole = await P8._need_dep(conn, chat_id, uid)
        if mrole != "boss":
            raise GameError("Переманивать может только босс")
        if role not in ("grunt", "deputy"):
            raise GameError("Должность: холоп или зам")
        if not 500 <= offer <= 3000:
            raise GameError("Предложение от 500 до 3000 ₽")
        if (g["treasury"] or 0) < offer:
            raise GameError("В общаке нет такой суммы")
        if sum(1 for m in members if m["user_id"] > 0) >= G.MAX_MEMBERS:
            raise GameError("В банде нет мест для игроков")
        if await P3.kget(conn, f"p9def:{g['id']}") > now():
            raise GameError("Вербовка раз в 6 часов")
        tg, _ = await G._mine(conn, chat_id, target)
        if not tg or tg["id"] == g["id"] or tg.get("is_ai"):
            raise GameError("Нужен участник другой банды игроков")
        cur = await conn.execute("SELECT role FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        if (await cur.fetchone())[0] == "boss":
            raise GameError("Босса не переманить")
        if await P3.pact_between(conn, g["id"], tg["id"], ("active",)):
            raise GameError("Союзников не переманивают")
        cur = await conn.execute("SELECT 1 FROM defect WHERE to_user = ? AND status = 'open'", (target,))
        if await cur.fetchone():
            raise GameError("Ему уже сделали предложение")
        await GP._kset(conn, f"p9def:{g['id']}", now() + DEFECT_CD)
        await conn.execute("INSERT INTO defect (chat_id, from_gang, to_user, old_gang, offer, role, status, expires, loyalty, created) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                           (chat_id, g["id"], target, tg["id"], offer, role, now() + DEFECT_HOURS * 3600, random.randint(25, 85), now()))
        tn = await P3._uname(conn, chat_id, target)
        return P3._res(f"🕵 Предложение отправлено {tn}: {offer} ₽ и должность. Ответ в течение {DEFECT_HOURS} часов.")
    return await P3._act(chat_id, uid, run)


async def defect_answer(chat_id: int, uid: int, name: str, did, accept=True) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM defect WHERE id = ? AND chat_id = ? AND to_user = ? AND status = 'open'", (int(did), chat_id, uid))
        r = await cur.fetchone()
        if not r or r["expires"] < now():
            raise GameError("Предложение уже неактуально")
        d = dict(r)
        if not accept:
            await conn.execute("UPDATE defect SET status = 'declined' WHERE id = ?", (d["id"],))
            return P3._res("Ты отказался от предложения.", False)
        new = await _gx(conn, d["from_gang"])
        old = await _gx(conn, d["old_gang"])
        if not new or (new["treasury"] or 0) < d["offer"] or len(await gangwar._real_members(conn, new["id"])) >= G.MAX_MEMBERS:
            await conn.execute("UPDATE defect SET status = 'void' WHERE id = ?", (d["id"],))
            raise GameError("Предложение сорвалось: у вербовщиков нет денег или мест")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (d["offer"], new["id"]))
        p = await rp._load(conn, chat_id, uid)
        p["money"] += d["offer"]
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        await conn.execute("INSERT INTO gang_member (chat_id, user_id, gang_id, joined, role, probation_until) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, uid, new["id"], now(), d["role"], now() + 24 * 3600))
        await conn.execute("UPDATE defect SET status = 'done' WHERE id = ?", (d["id"],))
        await P5.rep_add(conn, new["id"], 0, 2)
        if old:
            await P5.rep_add(conn, old["id"], -1, 0)
        await gangwar.say(conn, chat_id, f"🕵 <b>Перебежчик!</b> {esc(name)} ушёл из «{esc(old['name'] if old else '?')}» в «{esc(new['name'])}» за {d['offer']} ₽. Старая банда может назначить награду за голову предателя.")
        return P3._res(f"🕵 Ты перешёл в «{new['name']}» и получил {d['offer']} ₽. Испытательный срок 24 часа. Старая банда будет в обиде.")
    return await P3._act(chat_id, uid, run)


async def defect_bounty(chat_id: int, uid: int, name: str, did, amount) -> dict:
    amount = int(amount or 0)

    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Награду назначает босс")
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM defect WHERE id = ? AND old_gang = ? AND status = 'done' AND created > ?", (int(did), g["id"], now() - 7 * 86400))
        r = await cur.fetchone()
        if not r:
            raise GameError("Такого перебежчика нет или срок вышел")
        if not 200 <= amount <= 1500 or (g["treasury"] or 0) < amount:
            raise GameError("Награда от 200 до 1500 ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (amount, g["id"]))
        await conn.execute("UPDATE defect SET bounty = bounty + ? WHERE id = ?", (amount, r["id"]))
        tn = await P3._uname(conn, chat_id, r["to_user"])
        await gangwar.say(conn, chat_id, f"🎯 «{esc(g['name'])}» назначила награду {amount} ₽ за голову предателя {esc(tn)}!")
        return P3._res(f"🎯 Награда {amount} ₽ назначена. Выслеживают участники вашей банды.")
    return await P3._act(chat_id, uid, run)


async def defect_hunt(chat_id: int, uid: int, name: str, did) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM defect WHERE id = ? AND status = 'done' AND bounty > 0", (int(did),))
        r = await cur.fetchone()
        if not r:
            raise GameError("Награды нет")
        d = dict(r)
        g, _ = await G._mine(conn, chat_id, uid)
        if not g or g["id"] != d["old_gang"]:
            raise GameError("Выслеживают только участники старой банды")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, 15)
        p["energy"] -= 15
        tn = await P3._uname(conn, chat_id, d["to_user"])
        if random.random() < 0.5:
            fine = 0
            tp = await rp._load(conn, chat_id, d["to_user"])
            fine = min(300, tp["money"])
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, chat_id, d["to_user"]))
            await G2._beat(conn, chat_id, d["to_user"], 30)
            p["money"] += d["bounty"]
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (fine, g["id"]))
            await conn.execute("UPDATE defect SET bounty = 0 WHERE id = ?", (d["id"],))
            await rp._save(conn, chat_id, uid, p)
            await gangwar.say(conn, chat_id, f"🎯 {esc(name)} выследил предателя {esc(tn)}: награда {d['bounty']} ₽, предатель побит на 30 минут.")
            return P3._res(f"🎯 Нашёл! Награда {d['bounty']} ₽ твоя.", True, d["bounty"])
        await rp._save(conn, chat_id, uid, p)
        return P3._res(f"🎯 {tn} ускользнул. Попробуй позже.", False)
    return await P3._act(chat_id, uid, run)


async def _defect_tick(conn) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    await conn.execute("UPDATE defect SET status = 'expired' WHERE status = 'open' AND expires <= ?", (t,))
    cur = await conn.execute("SELECT * FROM defect WHERE status = 'done' AND created > ?", (t - 14 * 86400,))
    for d in [dict(r) for r in await cur.fetchall()]:
        if not await P3._roll(conn, f"p9loy:{d['id']}", 86400):
            continue
        new = await _gx(conn, d["from_gang"])
        if not new:
            continue
        cur2 = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ? AND gang_id = ?", (d["chat_id"], d["to_user"], new["id"]))
        if not await cur2.fetchone():
            continue
        name = await P3._uname(conn, d["chat_id"], d["to_user"])
        loy = d["loyalty"]
        if loy < 35 and random.random() < 0.6:
            stolen = min(800, int((new["treasury"] or 0) * 0.10))
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (stolen, new["id"]))
            await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (d["chat_id"], d["to_user"]))
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (stolen, d["chat_id"], d["to_user"]))
            await conn.execute("UPDATE defect SET status = 'fled' WHERE id = ?", (d["id"],))
            await gangwar.say(conn, d["chat_id"], f"🏃 Перебежчик {esc(name)} ограбил «{esc(new['name'])}» на {stolen} ₽ и скрылся: ни в одной банде его больше нет.")
        elif loy < 60 and random.random() < 0.6:
            war = await gangwar._active_war(conn, new["id"])
            if war and d["old_gang"] in (war["a"], war["b"]):
                col = "score_a" if war["a"] == new["id"] else "score_b"
                await conn.execute(f"UPDATE gang_war SET {col} = MAX(0, {col} - 5) WHERE id = ?", (war["id"],))
            await P5.rep_add(conn, new["id"], -2, 0)
            await gangwar.say(conn, d["chat_id"], f"🕵 Перебежчик {esc(name)} слил планы «{esc(new['name'])}» старой банде: уважение к «{esc(new['name'])}» упало.")
        elif loy >= 60:
            await G2._merit(conn, d["chat_id"], d["to_user"], 3)


# ═════════════════════ комнаты штаба ═════════════════════
ROOMS = {
    "office": ("👔", "Кабинет босса", "договоры, союзы и большие дела", "без кабинета нельзя затевать большие дела"),
    "vault": ("💰", "Общак", "деньги банды и бар", "при налётах уносят вдвое больше"),
    "armory": ("🔫", "Оружейка", "охрана банды", "шанс отбиться от нападения −10%"),
    "kitchen": ("🍽", "Кухня", "кок и сходки", "нет банкетов и праздничных столов"),
    "basement": ("⛓", "Подвал", "допросы и контрразведка", "допросы невозможны"),
    "garage": ("🚗", "Гараж", "водила и налёты", "налёт −10% к шансу, побег без бонуса"),
    "yard": ("🌳", "Двор", "дозор и свои на связи", "дозор не работает"),
}
REPAIR_COST, RANSACK_ENERGY, RANSACK_CD, WRECK_HOURS = 800, 20, 3600, 24


async def wrecked(conn, gid: int, room: str) -> bool:
    return await P3.kget(conn, f"p9room:{gid}:{room}") > now()


async def room_repair(chat_id: int, uid: int, name: str, room: str) -> dict:
    async def run(conn):
        g, _, _r = await P8._need_dep(conn, chat_id, uid)
        if room not in ROOMS or not await wrecked(conn, g["id"], room):
            raise GameError("Комната цела")
        if (g["treasury"] or 0) < REPAIR_COST:
            raise GameError(f"Ремонт стоит {REPAIR_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (REPAIR_COST, g["id"]))
        await GP._kset(conn, f"p9room:{g['id']}:{room}", 0)
        return P3._res(f"🔧 {ROOMS[room][0]} {ROOMS[room][1]} отремонтирована (−{REPAIR_COST} ₽).")
    return await P3._act(chat_id, uid, run)


async def ransack(chat_id: int, uid: int, name: str, room: str) -> dict:
    async def run(conn):
        g, _ = await G._mine(conn, chat_id, uid)
        if not g or g.get("is_ai"):
            raise GameError("Ты не в банде")
        war = await gangwar._active_war(conn, g["id"])
        if not war:
            raise GameError("Громить штаб можно только в войне")
        enemy = war["b"] if war["a"] == g["id"] else war["a"]
        if room not in ROOMS:
            raise GameError("Такой комнаты нет")
        if await P3.kget(conn, f"p9ran:{g['id']}") > now():
            raise GameError("Банда ещё не оправилась после прошлого погрома")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, RANSACK_ENERGY)
        p["energy"] -= RANSACK_ENERGY
        await GP._kset(conn, f"p9ran:{g['id']}", now() + RANSACK_CD)
        ap, dp = await P8._power(conn, g["id"]), await P8._power(conn, enemy)
        chance = max(0.2, min(0.75, 0.45 + 0.05 * (ap - dp)))
        eg = await _gx(conn, enemy)
        if random.random() < chance:
            await GP._kset(conn, f"p9room:{enemy}:{room}", now() + WRECK_HOURS * 3600)
            await rp._save(conn, chat_id, uid, p)
            await gangwar.say(conn, chat_id, f"💥 «{esc(g['name'])}» разгромила {ROOMS[room][0]} {ROOMS[room][1]} у «{esc(eg['name'] if eg else '?')}»: {ROOMS[room][3]} на сутки или до ремонта.")
            return P3._res(f"💥 Погром удался: {ROOMS[room][1]} врага разбита на сутки.")
        p["wanted"], p["wanted_ts"] = min(rp.MAX_WANTED, p["wanted"] + 1), now()
        await rp._save(conn, chat_id, uid, p)
        return P3._res(f"🚔 Погром сорвался (шанс был ~{int(chance * 100)}%): охрана отбила, розыск +1⭐.", False)
    return await P3._act(chat_id, uid, run)


async def _ai_ransack_tick(conn) -> None:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_war WHERE status = 'active'")
    for w in [dict(r) for r in await cur.fetchall()]:
        a, b = await _gx(conn, w["a"]), await _gx(conn, w["b"])
        if not a or not b or bool(a["is_ai"]) == bool(b["is_ai"]):
            continue
        ai, real = (a, b) if a["is_ai"] else (b, a)
        if await P3._roll(conn, f"p9airan:{w['id']}", 5 * 3600):
            room = random.choice(list(ROOMS))
            if not await wrecked(conn, real["id"], room) and await P8._power(conn, ai["id"]) + random.random() * 3 > await P8._power(conn, real["id"]):
                await GP._kset(conn, f"p9room:{real['id']}:{room}", now() + WRECK_HOURS * 3600)
                await gangwar.say(conn, w["chat_id"], f"💥 «{esc(ai['name'])}» разгромила {ROOMS[room][0]} {ROOMS[room][1]} у «{esc(real['name'])}»: {ROOMS[room][3]} на сутки или до ремонта.")


# ═════════════════════ штурм и мирные договоры ═════════════════════
ASSAULT_LEAD, ASSAULT_ENERGY = 20, 20


async def assault(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, members, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Штурм объявляет босс")
        war = await gangwar._active_war(conn, g["id"])
        if not war:
            raise GameError("Штурмовать можно только в войне")
        mine_a = war["a"] == g["id"]
        enemy = war["b"] if mine_a else war["a"]
        lead = (war["score_a"] - war["score_b"]) * (1 if mine_a else -1)
        if lead < ASSAULT_LEAD:
            raise GameError(f"Для штурма нужен перевес в {ASSAULT_LEAD} очков (сейчас {lead})")
        if await P3.kget(conn, f"p9as:{war['id']}") or await P3.kget(conn, f"p9ascd:{g['id']}") > now():
            raise GameError("Штурм уже был в этой войне или банда отдыхает после неудачи")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, ASSAULT_ENERGY)
        p["energy"] -= ASSAULT_ENERGY
        await rp._save(conn, chat_id, uid, p)
        await GP._kset(conn, f"p9as:{war['id']}", now())
        import world
        await world.police_react(conn, chat_id, [g["id"], enemy], "штурм штаба", 0.7)
        eg = await _gx(conn, enemy)
        ap = await P8._power(conn, g["id"])
        dp = await P8._power(conn, enemy) + 0.5 * await P3.proj_level(conn, enemy, "guard") + (-1.5 if await wrecked(conn, enemy, "armory") else 0)
        chance = max(0.2, min(0.85, 0.5 + 0.06 * (ap - dp) + min(0.15, (lead - ASSAULT_LEAD) / 200)))
        if random.random() < chance:
            loot = min(4000, int((eg["treasury"] or 0) * 0.20))
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (loot, enemy))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (loot, g["id"]))
            lines = [f"из общака вынесли {loot} ₽"]
            cur = await conn.execute("SELECT key, level FROM gang_project WHERE gang_id = ? AND level > 0", (enemy,))
            projs = await cur.fetchall()
            if projs:
                key, lvl = random.choice(projs)
                await conn.execute("UPDATE gang_project SET level = level - 1 WHERE gang_id = ? AND key = ?", (enemy, key))
                if await P3.proj_level(conn, g["id"], key) < 3:
                    await conn.execute("INSERT INTO gang_project (gang_id, key, level) VALUES (?, ?, 1) ON CONFLICT(gang_id, key) DO UPDATE SET level = level + 1", (g["id"], key))
                lines.append(f"проект «{P3.PROJECTS[key][1]}» у врага понижен, у вас повышен")
            em, c1, c2 = look(eg)
            await hall_add(conn, g["id"], chat_id, "trophy", 0, eg["name"], f"эмблема {em} «{eg['name']}»")
            lines.append(f"эмблема {em} «{eg['name']}» теперь висит в вашем штабе")
            cur = await conn.execute("SELECT id FROM brigade WHERE gang_id = ? AND status = 'free' ORDER BY RANDOM() LIMIT 1", (enemy,))
            pr = await cur.fetchone()
            if pr:
                cur = await conn.execute("SELECT role FROM brigade WHERE id = ?", (pr[0],))
                prole = (await cur.fetchone())[0]
                if not any(b["role"] == prole for b in await P8._brig(conn, g["id"])):
                    await conn.execute("UPDATE brigade SET gang_id = ?, loyalty = 40, status = 'free' WHERE id = ?", (g["id"], pr[0]))
                    lines.append("пленный шнырь теперь в вашей бригаде")
                else:
                    await conn.execute("UPDATE brigade SET status = 'fired' WHERE id = ?", (pr[0],))
                    lines.append("пленного отпустили за выкуп 500 ₽")
                    await conn.execute("UPDATE gang SET treasury = treasury + 500 WHERE id = ?", (g["id"],))
            col = "score_a" if mine_a else "score_b"
            await conn.execute(f"UPDATE gang_war SET {col} = {col} + 30 WHERE id = ?", (war["id"],))
            await P5.rep_add(conn, g["id"], 5, 5)
            await P5.rep_add(conn, enemy, -5, 0)
            await gangwar.say(conn, chat_id, f"🏰 <b>Штурм удался!</b> «{esc(g['name'])}» взяла штаб «{esc(eg['name'])}»: " + "; ".join(esc(x) for x in lines) + ". +30 очков в войне.")
            return P3._res("🏰 Штурм удался: " + "; ".join(lines) + ".", True, 0)
        await G2._merit(conn, chat_id, uid, -15)
        real = [m["user_id"] for m in members if m["user_id"] > 0 and m["user_id"] != uid]
        for u in random.sample(real, k=min(2, len(real))):
            await G2._beat(conn, chat_id, u, 30)
        await conn.execute("UPDATE brigade SET status = 'injured', injured_until = ? WHERE gang_id = ? AND role = 'fighter' AND status = 'free'", (now() + 24 * 3600, g["id"]))
        await GP._kset(conn, f"p9ascd:{g['id']}", now() + 7 * 86400)
        await P5.rep_add(conn, g["id"], -4, 0)
        await gangwar.say(conn, chat_id, f"💥 <b>Штурм провалился!</b> «{esc(g['name'])}» отбили от штаба «{esc(eg['name'])}»: бойцы побиты, неделя отдыха.")
        return P3._res(f"💥 Штурм провалился (шанс был ~{int(chance * 100)}%): авторитет −15, двое побиты, бойцы бригады ранены, неделя отдыха.", False)
    return await P3._act(chat_id, uid, run)


def _end_war(conn_exec, war_id):
    return conn_exec("UPDATE gang_war SET status = 'done' WHERE id = ?", (war_id,))


async def _apply_peace(conn, chat_id: int, a: dict, b: dict, kind: str, amount: int, ka: str, kb: str, war: dict) -> str:
    """a — предложившая, b — вторая. demand: b платит a; tribute: a платит b."""
    t = now()
    if kind == "demand":
        pay = min(amount, b["treasury"] or 0)
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (pay, b["id"]))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (pay, a["id"]))
        text = f"«{b['name']}» выплачивает контрибуцию {pay} ₽ банде «{a['name']}»"
    elif kind == "tribute":
        pay = min(amount, a["treasury"] or 0)
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (pay, a["id"]))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (pay, b["id"]))
        text = f"«{a['name']}» откупается от войны: {pay} ₽ банде «{b['name']}»"
    elif kind == "vassal":
        await conn.execute("INSERT INTO peace (chat_id, a, b, kind, amount, status, expires, ends) VALUES (?, ?, ?, 'vassal', 150, 'active', ?, ?)", (chat_id, a["id"], b["id"], t, t + 3 * 86400))
        text = f"«{b['name']}» становится вассалом «{a['name']}» на 3 дня (150 ₽ в сутки)"
    else:
        await conn.execute("UPDATE territory SET owner = ? WHERE chat_id = ? AND key = ? AND owner = ?", (b["id"], chat_id, ka, a["id"]))
        await conn.execute("UPDATE territory SET owner = ? WHERE chat_id = ? AND key = ? AND owner = ?", (a["id"], chat_id, kb, b["id"]))
        text = f"банды меняются районами"
    await conn.execute("UPDATE gang_war SET status = 'done' WHERE id = ?", (war["id"],))
    for x in (a, b):
        await GP._kset(conn, f"gwcd:{x['id']}", t + gangwar.WAR_COOLDOWN)
    await conn.execute("INSERT INTO gang_deal (chat_id, a, b, kind, item, qty, price, created, expires, ends, status) VALUES (?, ?, ?, 'truce', '', 0, 0, ?, ?, ?, 'active')", (chat_id, a["id"], b["id"], t, t + 86400, t + 86400))
    await gangwar.say(conn, chat_id, f"🕊 <b>Война окончена миром:</b> {esc(text)}. Сутки перемирия.")
    return text


async def peace_offer(chat_id: int, uid: int, name: str, kind: str, amount=0, key_a="", key_b="") -> dict:
    amount = int(amount or 0)

    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Мир предлагает босс")
        war = await gangwar._active_war(conn, g["id"])
        if not war:
            raise GameError("Войны нет")
        mine_a = war["a"] == g["id"]
        enemy = await _gx(conn, war["b"] if mine_a else war["a"])
        lead = (war["score_a"] - war["score_b"]) * (1 if mine_a else -1)
        if kind in ("demand", "tribute") and not 100 <= amount <= 5000:
            raise GameError("Сумма от 100 до 5000 ₽")
        if kind == "swap":
            own = await P5._owners(conn, chat_id)
            if own.get(key_a) != g["id"] or own.get(key_b) != enemy["id"]:
                raise GameError("Выбери один свой район и один вражеский")
        if kind not in ("demand", "tribute", "vassal", "swap"):
            raise GameError("Неизвестные условия")
        if kind == "tribute" and (g["treasury"] or 0) < amount:
            raise GameError("В общаке нет такой суммы")
        if enemy["is_ai"]:
            prob = {"tribute": 0.9, "swap": 0.5}.get(kind, 0.7 if lead >= 15 else 0.2)
            if random.random() > prob:
                return P3._res(f"«{enemy['name']}» отвергла условия мира.", False)
            text = await _apply_peace(conn, chat_id, g, enemy, kind, amount, key_a, key_b, war)
            return P3._res(f"🕊 Мир заключён: {text}.")
        await conn.execute("INSERT INTO peace (chat_id, a, b, kind, amount, key_a, key_b, status, expires) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)", (chat_id, g["id"], enemy["id"], kind, amount, key_a, key_b, now() + 12 * 3600))
        await gangwar.say(conn, chat_id, f"🕊 «{esc(g['name'])}» предлагает «{esc(enemy['name'])}» мир на условиях: {kind}. Босс решает в мини-аппе.")
        return P3._res("🕊 Предложение мира отправлено боссу врага (12 часов).")
    return await P3._act(chat_id, uid, run)


async def peace_answer(chat_id: int, uid: int, name: str, pid, accept=True) -> dict:
    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Отвечает босс")
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM peace WHERE id = ? AND b = ? AND status = 'open'", (int(pid), g["id"]))
        r = await cur.fetchone()
        if not r or r["expires"] < now():
            raise GameError("Предложение неактуально")
        p = dict(r)
        if not accept:
            await conn.execute("UPDATE peace SET status = 'declined' WHERE id = ?", (p["id"],))
            return P3._res("Мир отвергнут: война продолжается.", False)
        a = await _gx(conn, p["a"])
        war = await gangwar._active_war(conn, g["id"])
        if not a or not war:
            raise GameError("Войны уже нет")
        await conn.execute("UPDATE peace SET status = 'done' WHERE id = ?", (p["id"],))
        text = await _apply_peace(conn, chat_id, a, g, p["kind"], p["amount"], p["key_a"], p["key_b"], war)
        return P3._res(f"🕊 Мир заключён: {text}.")
    return await P3._act(chat_id, uid, run)


async def _peace_tick(conn) -> None:
    t = now()
    await conn.execute("UPDATE peace SET status = 'expired' WHERE status = 'open' AND expires <= ?", (t,))
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM peace WHERE kind = 'vassal' AND status = 'active'")
    for p in [dict(r) for r in await cur.fetchall()]:
        if p["ends"] <= t:
            await conn.execute("UPDATE peace SET status = 'done' WHERE id = ?", (p["id"],))
            continue
        if await P3.kget(conn, f"p9vas:{p['id']}") > t - 86400:
            continue
        await GP._kset(conn, f"p9vas:{p['id']}", t)
        v = await _gx(conn, p["b"])
        pay = min(p["amount"], (v or {}).get("treasury") or 0)
        if pay:
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (pay, p["b"]))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (pay, p["a"]))


# ═════════════════════ интерфейс и фон ═════════════════════
async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    t = now()
    names = {x["id"]: x["name"] for x in out.get("gangs", [])}
    crown = int(await gangwar.kget(conn, f"crown:{chat_id}") or 0)
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang WHERE chat_id = ?", (chat_id,))
    full = {r["id"]: dict(r) for r in await cur.fetchall()}
    for x in out.get("gangs", []):
        em, c1, c2 = look(full.get(x["id"], {"name": x["name"]}))
        x["emblem"], x["color1"], x["color2"], x["icon"] = em, c1, c2, em
        x["motto"] = (full.get(x["id"]) or {}).get("motto", "")
        x["crown"] = x["id"] == crown
    pc = out.setdefault("pc", {})
    # долговая книга: см. debts.py
    import debts
    await debts.fill(conn, chat_id, uid, pc)
    # перебежчики: предложение мне
    cur = await conn.execute("SELECT * FROM defect WHERE chat_id = ? AND to_user = ? AND status = 'open' AND expires > ?", (chat_id, uid, t))
    dr = await cur.fetchone()
    pc["defect_in"] = None
    if dr:
        fg = full.get(dr["from_gang"], {})
        pc["defect_in"] = {"id": dr["id"], "gang": fg.get("name", "?"), "offer": dr["offer"], "role": dr["role"], "left": dr["expires"] - t}
    # тюремный слой и совет
    # (заполняется в gangplus10)
    # сезон
    season = []
    cur = await conn.execute("SELECT gang_id, pts FROM season_ctl WHERE chat_id = ? AND season = ? ORDER BY pts DESC LIMIT 5", (chat_id, season_key()))
    for gid, pts in await cur.fetchall():
        season.append({"name": names.get(gid, "?"), "pts": pts, "color": look(full.get(gid, {"name": names.get(gid, "?")}))[1]})
    pc["season"] = {"key": season_key(), "top": season}
    m = out.get("mine")
    if not g or not m or g.get("is_ai"):
        return
    p8 = m.setdefault("p8", {})
    cur = await conn.execute("SELECT * FROM debt WHERE gang_id = ? AND status IN ('open', 'late') ORDER BY due", (g["id"],))
    p8["lent"] = sum(r["owed"] for r in await cur.fetchall())
    cur = await conn.execute("SELECT user_id, username FROM users WHERE chat_id = ? AND user_id IN (SELECT user_id FROM rp_player WHERE chat_id = ?)", (chat_id, chat_id))
    p8["borrowers"] = [{"id": r[0], "name": r[1]} for r in await cur.fetchall() if r[0] != uid]
    cur = await conn.execute("SELECT * FROM gang_hall WHERE gang_id = ? ORDER BY id DESC LIMIT 8", (g["id"],))
    hall = [{"kind": r["kind"], "name": r["name"], "text": r["text"], "ts": r["ts"]} for r in await cur.fetchall()]
    heir = g.get("heir") or 0
    full_g = full.get(g["id"], g)
    p8["dyn"] = {"heir": heir, "heir_name": await P3._uname(conn, chat_id, heir) if heir else "", "legacy": full_g.get("legacy", 0), "street": full_g.get("street", ""), "hall": hall,
                 "candidates": [{"id": x["id"], "name": x["name"]} for x in m["members"] if x["id"] != uid and x["id"] > 0]}
    p8["emblem"] = {"emoji": full_g.get("emblem", ""), "c1": full_g.get("color1", ""), "c2": full_g.get("color2", ""), "motto": full_g.get("motto", ""), "emojis": EMBLEMS, "colors": COLORS,
                    "cost": EMBLEM_COST if full_g.get("emblem") else 0, "graffiti_wait": max(0, await P3.kget(conn, f"p9graf:{g['id']}") - t)}
    cur = await conn.execute("SELECT idx, text FROM gang_rule WHERE gang_id = ? ORDER BY idx", (g["id"],))
    rules = [{"idx": r[0], "text": r[1]} for r in await cur.fetchall()]
    cur = await conn.execute("SELECT * FROM gang_trial WHERE gang_id = ? AND status = 'open'", (g["id"],))
    tr = await cur.fetchone()
    trial = None
    if tr:
        votes = json.loads(tr["votes"] or "{}")
        trial = {"id": tr["id"], "accuser": await P3._uname(conn, chat_id, tr["accuser"]), "defendant": await P3._uname(conn, chat_id, tr["defendant"]),
                 "rule": next((r["text"] for r in rules if r["idx"] == tr["rule"]), "?"), "evidence": tr["evidence"], "left": tr["ends"] - t,
                 "guilty": sum(1 for v in votes.values() if v == "guilty"), "innocent": sum(1 for v in votes.values() if v == "innocent"),
                 "can_vote": uid not in (tr["accuser"], tr["defendant"]) and str(uid) not in votes, "mine": votes.get(str(uid), "")}
    p8["codex"] = {"rules": rules, "trial": trial, "targets": p8["dyn"]["candidates"]}
    # перебежчики
    others = []
    cur = await conn.execute("SELECT m.user_id, m.gang_id FROM gang_member m JOIN gang g ON g.id = m.gang_id WHERE m.chat_id = ? AND g.is_ai = 0 AND m.gang_id != ? AND m.role != 'boss' AND m.user_id > 0", (chat_id, g["id"]))
    for uid2, gid2 in await cur.fetchall():
        others.append({"id": uid2, "name": await P3._uname(conn, chat_id, uid2), "gang": names.get(gid2, "?")})
    cur = await conn.execute("SELECT * FROM defect WHERE old_gang = ? AND status = 'done' AND created > ?", (g["id"], t - 7 * 86400))
    bounties = [{"id": r["id"], "name": await P3._uname(conn, chat_id, r["to_user"]), "bounty": r["bounty"], "gang": names.get(r["from_gang"], "?")} for r in await cur.fetchall()]
    p8["defect"] = {"targets": others, "bounties": bounties, "wait": max(0, await P3.kget(conn, f"p9def:{g['id']}") - t)}
    # комнаты штаба
    br = await P8._brig(conn, g["id"])
    cur = await conn.execute("SELECT user_id, spec FROM gang_member WHERE gang_id = ? AND user_id > 0", (g["id"],))
    specs = {r[0]: r[1] for r in await cur.fetchall()}
    online = []
    for x in m["members"]:
        if x["id"] > 0:
            cur = await conn.execute("SELECT energy_ts FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, x["id"]))
            r = await cur.fetchone()
            online.append({"name": x["name"], "online": bool(r and t - (r[0] or 0) < 15 * 60), "spec": specs.get(x["id"], "")})
    rooms = []
    stats = {
        "office": f"союзов {len(m.get('pacts', []))}, договоров {len((m.get('p5') or {}).get('deals', []))}",
        "vault": f"{g['treasury']} ₽, выдано в долг {p8['lent']} ₽",
        "armory": f"охрана {await P3.proj_level(conn, g['id'], 'guard')}/3, бойцов бригады {sum(1 for b in br if b['role'] == 'fighter')}",
        "kitchen": f"коков {sum(1 for s in specs.values() if s == 'cook')}",
        "basement": "допросы и контрразведка" + (", крот вскрыт!" if (m.get('p5') or {}).get('ci', {}).get('found') else ""),
        "garage": f"водил {sum(1 for s in specs.values() if s == 'driver')}",
        "yard": f"на связи {sum(1 for o in online if o['online'])} из {len(online)}",
    }
    for key, (ic, title, desc, eff) in ROOMS.items():
        left = max(0, await P3.kget(conn, f"p9room:{g['id']}:{key}") - t)
        rooms.append({"key": key, "icon": ic, "title": title, "desc": desc, "stat": stats[key], "wrecked": left, "effect": eff})
    p8["hq"] = {"rooms": rooms, "online": online, "repair_cost": REPAIR_COST, "emblem": full_g.get("emblem", "") or "🕴", "color": full_g.get("color1") or "#7f8c8d",
                "trophies": [h for h in hall if h["kind"] in ("trophy", "season")], "street": full_g.get("street", "")}
    war = await gangwar._active_war(conn, g["id"])
    if war:
        mine_a = war["a"] == g["id"]
        enemy = war["b"] if mine_a else war["a"]
        lead = (war["score_a"] - war["score_b"]) * (1 if mine_a else -1)
        own = await P5._owners(conn, chat_id)
        cur = await conn.execute("SELECT * FROM peace WHERE chat_id = ? AND status = 'open' AND expires > ? AND (a = ? OR b = ?)", (chat_id, t, g["id"], g["id"]))
        peaces = [{"id": r["id"], "kind": r["kind"], "amount": r["amount"], "incoming": r["b"] == g["id"], "other": names.get(r["a"] if r["b"] == g["id"] else r["b"], "?")} for r in await cur.fetchall()]
        p8["war"] = {"enemy": names.get(enemy, "?"), "lead": lead, "can_assault": lead >= ASSAULT_LEAD and not await P3.kget(conn, f"p9as:{war['id']}") and await P3.kget(conn, f"p9ascd:{g['id']}") <= t,
                     "assault_need": ASSAULT_LEAD, "assault_cd": max(0, await P3.kget(conn, f"p9ascd:{g['id']}") - t), "peaces": peaces, "enemy_id": enemy,
                     "my_terr": [{"key": k, "name": gangwar.TERR[k][2]} for k, o in own.items() if o == g["id"]], "their_terr": [{"key": k, "name": gangwar.TERR[k][2]} for k, o in own.items() if o == enemy],
                     "ransack_wait": max(0, await P3.kget(conn, f"p9ran:{g['id']}") - t), "rooms": [{"key": k, "title": v[1], "icon": v[0]} for k, v in ROOMS.items()]}


async def tick(conn) -> None:
    try:
        import debts
        await debts.tick(conn)
        await _dynasty_tick(conn)
        await _trials_tick(conn)
        await _season_tick(conn)
        await _defect_tick(conn)
        await _ai_ransack_tick(conn)
        await _peace_tick(conn)
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 9")
