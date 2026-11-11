"""ОПГ-плюс 10: тюремный слой (сигареты, работы, контрабанда, побег) и совет боссов с мэром района."""
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


# ═════════════════════ тюремный слой ═════════════════════
CIG_PER_MIN, WORK_GAP, SMUGGLE_GAP, PARCEL_COST, PARCEL_CIGS = 4, 5 * 60, 10 * 60, 150, 8
BREAKOUT_COST, LAWYER_COST = 1500, 700


async def _prow(conn, chat_id: int, uid: int) -> dict:
    cur = await conn.execute("SELECT cigs, rep, work_ts FROM jail_cig WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return {"cigs": r[0], "rep": r[1], "work_ts": r[2]} if r else {"cigs": 0, "rep": 0, "work_ts": 0}


async def _psave(conn, chat_id: int, uid: int, pr: dict) -> None:
    await conn.execute("INSERT INTO jail_cig (chat_id, user_id, cigs, rep, work_ts) VALUES (?, ?, ?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET cigs = excluded.cigs, rep = excluded.rep, work_ts = excluded.work_ts",
                       (chat_id, uid, max(0, pr["cigs"]), max(0, pr["rep"]), pr["work_ts"]))


async def _need_jail(conn, chat_id: int, uid: int):
    p = await rp._load(conn, chat_id, uid)
    if p["jail_until"] <= now():
        raise GameError("Ты на свободе: тюремные дела не для тебя")
    return p


async def prison_work(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        p = await _need_jail(conn, chat_id, uid)
        pr = await _prow(conn, chat_id, uid)
        left = pr["work_ts"] + WORK_GAP - now()
        if left > 0:
            raise GameError(f"Смена закончилась: следующая через {left // 60 + 1} мин.")
        gain = random.randint(2, 4) + (1 if pr["rep"] >= 5 else 0)
        pr["cigs"] += gain
        pr["work_ts"] = now()
        text = f"🧹 Тюремная работа: +{gain} 🚬."
        if random.random() < 0.3:
            pr["rep"] += 1
            text += " Авторитет в камере +1."
        r = random.random()
        if r < 0.12 and pr["rep"] < 3 and pr["cigs"] >= 2:
            pr["cigs"] -= 2
            text += " Сокамерник «одолжил» 2 сигареты: пока ты слабый, с тобой не церемонятся."
        elif r > 0.88:
            pr["cigs"] += 2
            text += " Сокамерник поделился наводкой: +2 🚬."
        await _psave(conn, chat_id, uid, pr)
        return P3._res(text)
    return await P3._act(chat_id, uid, run)


async def prison_smuggle(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        p = await _need_jail(conn, chat_id, uid)
        pr = await _prow(conn, chat_id, uid)
        if await P3.kget(conn, f"p10smug:{chat_id}:{uid}") > now():
            raise GameError("Охрана насторожилась: контрабанда через 20 минут")
        await GP._kset(conn, f"p10smug:{chat_id}:{uid}", now() + SMUGGLE_GAP)
        if random.random() < max(0.35, 0.55 + 0.03 * pr["rep"]):
            gain = random.randint(8, 15)
            pr["cigs"] += gain
            pr["rep"] += 2
            await _psave(conn, chat_id, uid, pr)
            return P3._res(f"📦 Контрабанда прошла: +{gain} 🚬 и авторитет +2.")
        await conn.execute("UPDATE rp_player SET jail_until = jail_until + 300 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        return P3._res("🚨 Контрабанду нашли: +5 минут к сроку.", False)
    return await P3._act(chat_id, uid, run)


async def prison_buy_time(chat_id: int, uid: int, name: str, minutes) -> dict:
    minutes = int(minutes or 0)

    async def run(conn):
        p = await _need_jail(conn, chat_id, uid)
        pr = await _prow(conn, chat_id, uid)
        left_min = (p["jail_until"] - now()) // 60
        cut = min(minutes, left_min // 2)
        if cut < 1:
            raise GameError("Нельзя сократить больше половины оставшегося срока")
        cost = cut * CIG_PER_MIN
        if pr["cigs"] < cost:
            raise GameError(f"Нужно {cost} 🚬, у тебя {pr['cigs']}")
        pr["cigs"] -= cost
        await _psave(conn, chat_id, uid, pr)
        await conn.execute("UPDATE rp_player SET jail_until = jail_until - ? WHERE chat_id = ? AND user_id = ?", (cut * 60, chat_id, uid))
        return P3._res(f"🚬 Договорился с надзирателем: срок короче на {cut} мин (−{cost} 🚬).")
    return await P3._act(chat_id, uid, run)


async def prison_parcel(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        if target == uid:
            raise GameError("Себе передачу не передашь")
        tp = await rp._load(conn, chat_id, target)
        if tp["jail_until"] <= now():
            raise GameError("Он на свободе")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < PARCEL_COST:
            raise GameError(f"Передача стоит {PARCEL_COST} ₽")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (PARCEL_COST, chat_id, uid))
        pr = await _prow(conn, chat_id, target)
        pr["cigs"] += PARCEL_CIGS
        await _psave(conn, chat_id, target, pr)
        tn = await P3._uname(conn, chat_id, target)
        return P3._res(f"📦 Передача для {tn}: +{PARCEL_CIGS} 🚬 (−{PARCEL_COST} ₽).", True, -PARCEL_COST)
    return await P3._act(chat_id, uid, run)


async def prison_breakout(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        g, _, _r = await P8._need_dep(conn, chat_id, uid)
        cur = await conn.execute("SELECT 1 FROM gang_member WHERE gang_id = ? AND user_id = ?", (g["id"], target))
        if not await cur.fetchone():
            raise GameError("Вытаскивать можно только своих")
        tp = await rp._load(conn, chat_id, target)
        if tp["jail_until"] <= now():
            raise GameError("Он уже на свободе")
        if (g["treasury"] or 0) < BREAKOUT_COST:
            raise GameError(f"Побег стоит {BREAKOUT_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (BREAKOUT_COST, g["id"]))
        pr = await _prow(conn, chat_id, target)
        tn = await P3._uname(conn, chat_id, target)
        if random.random() < min(0.85, 0.6 + 0.02 * pr["rep"]):
            await conn.execute("UPDATE rp_player SET jail_until = 0, esc_used = 0, wanted = MIN(?, wanted + 1) WHERE chat_id = ? AND user_id = ?", (rp.MAX_WANTED, chat_id, target))
            await gangwar.say(conn, chat_id, f"🏃 Банда «{esc(g['name'])}» устроила побег: {esc(tn)} на свободе!")
            return P3._res(f"🏃 Побег удался: {tn} на свободе (розыск +1⭐).")
        await conn.execute("UPDATE rp_player SET jail_until = jail_until + 600 WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        return P3._res(f"🚨 Побег сорвался: {tn} получил +10 минут, деньги пропали.", False)
    return await P3._act(chat_id, uid, run)


async def prison_lawyer(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        g, _, _r = await P8._need_dep(conn, chat_id, uid)
        cur = await conn.execute("SELECT 1 FROM gang_member WHERE gang_id = ? AND user_id = ?", (g["id"], target))
        if not await cur.fetchone():
            raise GameError("Адвокат нанимается только для своих")
        tp = await rp._load(conn, chat_id, target)
        if tp["jail_until"] <= now():
            raise GameError("Он уже на свободе")
        if (g["treasury"] or 0) < LAWYER_COST:
            raise GameError(f"Адвокат стоит {LAWYER_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (LAWYER_COST, g["id"]))
        tn = await P3._uname(conn, chat_id, target)
        if random.random() < 0.85:
            await conn.execute("UPDATE rp_player SET jail_until = 0, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, target))
            return P3._res(f"⚖️ Адвокат банды вытащил {tn} из тюрьмы.")
        return P3._res("⚖️ Судья не поддался: адвокат ничего не добился, деньги потрачены.", False)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ совет боссов и мэр ═════════════════════
TERM_DAYS, ELECT_HOURS = 7, 6


def _week() -> str:
    n = now_local().isocalendar()
    return f"{n[0]}-{n[1]}"


async def _mayor(conn, chat_id: int) -> Optional[dict]:
    raw = await gangwar.kget(conn, f"mayor:{chat_id}")
    m = json.loads(raw) if raw else None
    if not m or m.get("until", 0) <= now():
        return None
    day = time.strftime("%Y-%m-%d", time.localtime())
    if m.get("lim_day") != day:
        # Помилования и отмена войны — на сутки, а не «на срок»: иначе у вечного мэра они бесконечны.
        m.update({"lim_day": day, "pardons": MAYOR_PARDONS_DAY, "peace": MAYOR_PEACE_DAY})
        await gangwar.kset(conn, f"mayor:{chat_id}", json.dumps(m))
    return m


MAYOR_PARDONS_DAY, MAYOR_PEACE_DAY, CRACKDOWN_CD = 2, 1, 72 * 3600


async def mayor_mods(conn, chat_id: int) -> dict:
    m = await _mayor(conn, chat_id)
    if not m:
        try:
            import patrons
            ai = await patrons.ai_mayor_mods(conn, chat_id)
            if ai and (ai.get("police", 1.0) != 1.0 or ai.get("tax", 1.0) != 1.0 or ai.get("curfew")):
                return ai
        except Exception:
            pass
        return {"tax": 1.0, "curfew": False, "police": 1.0}
    pol = m.get("police", 1.0) * (1.5 if m.get("crackdown", 0) > now() else 1.0)
    tax = m.get("tax", 1.0)
    align = m.get("align", "neutral")
    if align == "police":
        pol *= 1.2
        tax *= 1.1
    elif align == "corrupt":
        pol *= 0.8
        tax *= 0.9
    return {"tax": tax, "curfew": m.get("curfew", False), "police": pol}


async def police_mult(conn, chat_id: int) -> float:
    m = (await mayor_mods(conn, chat_id))["police"]
    try:
        import doom
        m *= await doom.police_mult(conn, chat_id)
    except Exception:
        pass
    return m


async def _bosses(conn, chat_id: int) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT id, name, boss, is_ai, treasury, respect, fear FROM gang WHERE chat_id = ?", (chat_id,))
    return [dict(r) for r in await cur.fetchall()]


async def _officers(conn, chat_id: int) -> list:
    """Кандидаты в мэры — только реальные полицейские."""
    cur = await conn.execute(
        """SELECT pf.user_id, COALESCE(u.username, 'офицер') FROM police_force pf
           LEFT JOIN users u ON u.user_id = pf.user_id AND u.chat_id = pf.chat_id
           WHERE pf.chat_id = ? AND pf.user_id > 0 ORDER BY pf.rank DESC, pf.joined""", (chat_id,))
    return [{"id": r[0], "name": r[1], "ai": False} for r in await cur.fetchall()]


async def _elect_tick(bot_say, conn, chat_id: int) -> None:
    t = now()
    raw = await gangwar.kget(conn, f"council:{chat_id}")
    el = json.loads(raw) if raw else None
    if el and el["closes"] <= t:
        officers = await _officers(conn, chat_id)
        if not officers:
            await gangwar.kset(conn, f"council:{chat_id}", "")
            return
        ids = [x["id"] for x in officers]
        votes = {}
        cur = await conn.execute("SELECT voter, cand FROM council_vote WHERE chat_id = ? AND week = ?", (chat_id, el["week"]))
        for voter, cand in await cur.fetchall():
            if cand in ids:
                votes[cand] = votes.get(cand, 0) + 1
        gangs = await _bosses(conn, chat_id)
        for g in gangs:
            if g["is_ai"]:
                pick = random.choice(ids)
                votes[pick] = votes.get(pick, 0) + 1
        if not votes:
            votes[random.choice(ids)] = 1
        top = max(votes.values())
        win = random.choice([k for k, v in votes.items() if v == top])
        officer = next(x for x in officers if x["id"] == win)
        mayor = {"uid": win, "name": officer["name"], "gname": officer["name"], "gid": 0, "ai": False,
                 "until": t + TERM_DAYS * 86400, "tax": 1.0, "curfew": False, "police": 1.0, "align": "neutral",
                 "pardons": 1, "peace": 1, "crackdown": 0, "set_ts": 0, "impeach": []}
        await gangwar.kset(conn, f"mayor:{chat_id}", json.dumps(mayor))
        await gangwar.kset(conn, f"council:{chat_id}", "")
        await gangwar.say(conn, chat_id, f"🏛 <b>Жители выбрали мэра района: {esc(officer['name'])}</b> ({top} голос.). Мэр — из полиции: может миловать, менять налог/полицию, отменять войны и бить по Крёстному.")
        await rp._news(conn, chat_id, f"🏛 Мэр района: {officer['name']}")
        return
    if el:
        return
    mayor = await _mayor(conn, chat_id)
    raw_m = await gangwar.kget(conn, f"mayor:{chat_id}")
    if mayor:
        return
    if raw_m:
        old = json.loads(raw_m)
        await gangwar.say(conn, chat_id, f"🏛 Срок мэра «{esc(old.get('gname', '?'))}» закончился. Скоро новый совет боссов.")
        await gangwar.kset(conn, f"mayor:{chat_id}", "")
    n = now_local()
    if not await _officers(conn, chat_id):
        return  # некому баллотироваться: мэром остаётся ИИ (Архип)
    if (n.weekday() == 6 and n.hour >= 18) or not await gangwar.kget(conn, f"council_seen:{chat_id}"):
        if await gangwar.kget(conn, f"council_wk:{chat_id}") == _week():
            return
        await gangwar.kset(conn, f"council_wk:{chat_id}", _week())
        await gangwar.kset(conn, f"council_seen:{chat_id}", "1")
        await gangwar.kset(conn, f"council:{chat_id}", json.dumps({"week": _week(), "closes": t + ELECT_HOURS * 3600}))
        await gangwar.say(conn, chat_id, f"🏛 <b>Совет боссов собрался!</b> Боссы выбирают мэра района на неделю: {ELECT_HOURS} часов на голосование (мини-апп → Район → Город). Боссы ИИ-банд тоже голосуют.")


async def council_vote(chat_id: int, uid: int, name: str, cand) -> dict:
    cand = int(cand)

    async def run(conn):
        raw = await gangwar.kget(conn, f"council:{chat_id}")
        el = json.loads(raw) if raw else None
        if not el or el["closes"] <= now():
            raise GameError("Выборы сейчас не идут")
        officers = {x["id"]: x for x in await _officers(conn, chat_id)}
        if cand not in officers:
            raise GameError("Кандидат должен быть полицейским")
        await conn.execute("INSERT INTO council_vote (chat_id, week, voter, cand) VALUES (?, ?, ?, ?) ON CONFLICT(chat_id, week, voter) DO UPDATE SET cand = excluded.cand", (chat_id, el["week"], uid, cand))
        return P3._res(f"🏛 Твой голос за {officers[cand]['name']} учтён.")
    return await P3._act(chat_id, uid, run)


async def _need_mayor(conn, chat_id: int, uid: int):
    import police
    m = await _mayor(conn, chat_id)
    if not m or m.get("uid") != uid or not await police._role(conn, chat_id, uid):
        raise GameError("Это может только мэр района из полиции")
    return None, m


async def _save_mayor(conn, chat_id: int, m: dict) -> None:
    await gangwar.kset(conn, f"mayor:{chat_id}", json.dumps(m))


async def mayor_set(chat_id: int, uid: int, name: str, tax=1.0, curfew=False, police=1.0, align="") -> dict:
    async def run(conn):
        g, m = await _need_mayor(conn, chat_id, uid)
        tax_v = float(m.get("tax", 1.0) if tax is None else tax)
        pol_v = float(m.get("police", 1.0) if police is None else police)
        curfew_v = bool(m.get("curfew", False) if curfew is None else curfew)
        if tax_v not in (0.5, 0.75, 1.0, 1.25, 1.5) or pol_v not in (0.5, 0.75, 1.0, 1.25, 1.5):
            raise GameError("Допустимые значения: от 0.5 до 1.5 с шагом 0.25")
        if align and align not in ("police", "corrupt", "neutral"):
            raise GameError("Курс: police, corrupt или neutral")
        if m.get("set_ts", 0) > now() - 8 * 3600:
            raise GameError("Указ можно менять раз в 8 часов")
        m["tax"], m["police"], m["curfew"], m["set_ts"] = tax_v, pol_v, curfew_v, now()
        if align:
            m["align"] = align
            if align == "corrupt":
                try:
                    import justice
                    await justice.report(conn, chat_id, uid, "mayor", "подкупной курс мэра", 3)
                except Exception:
                    pass
        await _save_mayor(conn, chat_id, m)
        await gangwar.say(conn, chat_id, f"🏛 Указ мэра {esc(m.get('gname', '?'))}: налог ×{tax_v}, комендантский час {'вводится' if m['curfew'] else 'отменён'}, полиция ×{pol_v}.")
        return P3._res("🏛 Указ подписан.")
    return await P3._act(chat_id, uid, run)


async def mayor_campaign(chat_id: int, uid: int, name: str) -> dict:
    """Досрочные выборы: объявляет полицейский, не дожидаясь воскресенья."""
    async def run(conn):
        import police
        if not await police._role(conn, chat_id, uid):
            raise GameError("Выборы объявляет только полиция")
        if await _mayor(conn, chat_id):
            raise GameError("Мэр уже есть: сначала импичмент")
        raw = await gangwar.kget(conn, f"council:{chat_id}")
        el = json.loads(raw) if raw else None
        if el and el.get("closes", 0) > now():
            raise GameError("Выборы уже идут")
        if int(await gangwar.kget(conn, f"elcd:{chat_id}") or 0) > now():
            raise GameError("Досрочные выборы раз в 6 часов")
        t = now()
        await gangwar.kset(conn, f"elcd:{chat_id}", t + 6 * 3600)
        await gangwar.kset(conn, f"council:{chat_id}", json.dumps({"week": _week(), "closes": t + ELECT_HOURS * 3600}))
        await gangwar.kset(conn, f"council_wk:{chat_id}", _week())
        await gangwar.kset(conn, f"council_seen:{chat_id}", "1")
        await gangwar.say(conn, chat_id, f"🏛 <b>Полиция объявила досрочные выборы мэра!</b> {ELECT_HOURS} часов на голосование. Голосуют все жители: мини-апп → Город.")
        return P3._res("🏛 Досрочные выборы начались. Агитируй боссов.")
    return await P3._act(chat_id, uid, run)


async def mayor_pardon(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        g, m = await _need_mayor(conn, chat_id, uid)
        if m.get("pardons", 0) < 1:
            raise GameError("Помилование уже использовано")
        tp = await rp._load(conn, chat_id, target)
        if tp["jail_until"] <= now():
            raise GameError("Он не в тюрьме")
        await conn.execute("UPDATE rp_player SET jail_until = 0, esc_used = 0, wanted = MAX(0, wanted - 2) WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        m["pardons"] -= 1
        await _save_mayor(conn, chat_id, m)
        tn = await P3._uname(conn, chat_id, target)
        await gangwar.say(conn, chat_id, f"🏛 Мэр помиловал {esc(tn)}: выходит на свободу.")
        return P3._res(f"🏛 {tn} помилован.")
    return await P3._act(chat_id, uid, run)


async def mayor_peace(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)

    async def run(conn):
        g, m = await _need_mayor(conn, chat_id, uid)
        if m.get("peace", 0) < 1:
            raise GameError("Право отменить войну уже использовано")
        war = await gangwar._active_war(conn, gid)
        if not war:
            raise GameError("У этой банды нет войны")
        a, b = await gangwar._gang_row(conn, war["a"]), await gangwar._gang_row(conn, war["b"])
        await conn.execute("UPDATE gang_war SET status = 'done' WHERE id = ?", (war["id"],))
        for x in (a, b):
            await GP._kset(conn, f"gwcd:{x['id']}", now() + gangwar.WAR_COOLDOWN)
        m["peace"] -= 1
        await _save_mayor(conn, chat_id, m)
        await gangwar.say(conn, chat_id, f"🏛 Мэр отменил войну «{esc(a['name'])}» и «{esc(b['name'])}» без победителя.")
        return P3._res("🏛 Война отменена.")
    return await P3._act(chat_id, uid, run)


async def mayor_crackdown(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, m = await _need_mayor(conn, chat_id, uid)
        if m.get("crackdown", 0) + CRACKDOWN_CD - 12 * 3600 > now():
            raise GameError("Зачистку можно объявлять раз в трое суток")
        m["crackdown"] = now() + 12 * 3600
        m["crackdown_used"] = True
        await conn.execute("UPDATE rp_player SET wanted = MIN(?, wanted + 1), wanted_ts = ? WHERE chat_id = ? AND wanted >= 3", (rp.MAX_WANTED, now(), chat_id))
        await _save_mayor(conn, chat_id, m)
        await gangwar.say(conn, chat_id, f"🏛 Мэр {esc(m.get('gname', '?'))} объявил зачистку: у всех, кто в серьёзном розыске, +1⭐, а полиция в 1.5 раза активнее 12 часов.")
        return P3._res("🏛 Зачистка объявлена.")
    return await P3._act(chat_id, uid, run)


async def mayor_impeach(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Импичмент объявляют боссы")
        m = await _mayor(conn, chat_id)
        if not m:
            raise GameError("Мэра сейчас нет")
        if m.get("uid") == uid:
            raise GameError("Мэр не может объявить импичмент сам себе")
        if g["id"] in m["impeach"]:
            raise GameError("Ты уже голосовал за импичмент")
        m["impeach"].append(g["id"])
        gangs = await _bosses(conn, chat_id)
        real = [x for x in gangs if not x["is_ai"]]
        text = f"Голосов за импичмент: {len(m['impeach'])} из {len(real)}."
        if len(m["impeach"]) > len(real) / 2:
            await gangwar.kset(conn, f"mayor:{chat_id}", "")
            if m.get("uid"):
                await conn.execute("UPDATE rp_player SET cop_rep = MAX(0, cop_rep - 3) WHERE chat_id = ? AND user_id = ?", (chat_id, m["uid"]))
            await gangwar.say(conn, chat_id, f"🏛 <b>Импичмент!</b> Мэр {esc(m.get('gname', '?'))} смещён. Скоро новые выборы.")
            return P3._res("🏛 Импичмент удался: мэр смещён.")
        await _save_mayor(conn, chat_id, m)
        return P3._res("🏛 " + text)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ интерфейс и фон ═════════════════════
async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    t = now()
    pc = out.setdefault("pc", {})
    p = await rp._load(conn, chat_id, uid)
    jailed = p["jail_until"] > t
    pr = await _prow(conn, chat_id, uid)
    cur = await conn.execute("SELECT r.user_id, u.username, r.jail_until FROM rp_player r JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id WHERE r.chat_id = ? AND r.jail_until > ? AND r.user_id > 0", (chat_id, t))
    inmates = []
    for u2, n2, ju in await cur.fetchall():
        r2 = await _prow(conn, chat_id, u2)
        inmates.append({"id": u2, "name": n2, "rep": r2["rep"], "left": ju - t})
    mates = []
    if g and not g.get("is_ai"):
        gm = {x["id"] for x in (out.get("mine") or {}).get("members", [])}
        mates = [x for x in inmates if x["id"] in gm and x["id"] != uid]
    pc["prison"] = {"jailed": jailed, "cigs": pr["cigs"], "rep": pr["rep"], "work_wait": max(0, pr["work_ts"] + WORK_GAP - t), "smuggle_wait": max(0, await P3.kget(conn, f"p10smug:{chat_id}:{uid}") - t),
                    "cellmates": [x for x in inmates if x["id"] != uid], "mates": mates, "per_min": CIG_PER_MIN, "parcel_cost": PARCEL_COST, "breakout_cost": BREAKOUT_COST, "lawyer_cost": LAWYER_COST,
                    "can_help": bool(g and not g.get("is_ai") and my_role != "grunt")}
    # совет
    raw = await gangwar.kget(conn, f"council:{chat_id}")
    el = json.loads(raw) if raw else None
    gangs = await _bosses(conn, chat_id)
    council = {"election": None, "mayor": None}
    if el and el["closes"] > t:
        cur = await conn.execute("SELECT cand FROM council_vote WHERE chat_id = ? AND week = ? AND voter = ?", (chat_id, el["week"], uid))
        mine = await cur.fetchone()
        council["election"] = {"left": el["closes"] - t, "mine": mine[0] if mine else 0, "can_vote": bool(not jailed),
                               "candidates": await _officers(conn, chat_id)}
    m = await _mayor(conn, chat_id)
    if m:
        is_me = bool(m.get("uid") == uid)
        council["mayor"] = {"gname": m.get("gname", m.get("name", "?")), "ai": m.get("ai", False), "left": m["until"] - t, "tax": m["tax"], "curfew": m["curfew"], "police": m["police"], "pardons": m["pardons"],
                            "peace": m["peace"], "is_me": is_me, "impeach": len(m["impeach"]), "can_impeach": bool(g and not g.get("is_ai") and my_role == "boss" and not is_me and g["id"] not in m["impeach"]),
                            "crackdown_used": bool(m.get("crackdown", 0) + CRACKDOWN_CD - 12 * 3600 > t), "crackdown_wait": max(0, int(m.get("crackdown", 0)) + CRACKDOWN_CD - 12 * 3600 - t), "limits": {"pardons": MAYOR_PARDONS_DAY, "peace": MAYOR_PEACE_DAY}, "set_wait": max(0, m.get("set_ts", 0) + 8 * 3600 - t),
                            "align": m.get("align", "neutral"), "anti_wait": max(0, int(m.get("anti_god_ts", 0)) + 12 * 3600 - t),
                            "inmates": inmates if is_me else [], "wars": [{"gid": x["id"], "name": x["name"]} for x in gangs if x["is_ai"] is not None] if is_me else []}
    pc["council"] = council


async def tick(conn) -> None:
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM gang WHERE is_ai = 0")
        for (chat_id,) in await cur.fetchall():
            await _elect_tick(None, conn, chat_id)
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 10")
