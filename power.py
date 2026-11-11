"""Игровые роли власти: Крёстный отец и мэр.
Крёстный — отдельная касса, сбор дани, финансирование ОПГ/полиции и давление на мэра.
Мэр — политика «за полицию» или «подкупной», борьба с Крёстным.
Если реального игрока на роли нет, ИИ-версия получает усиление.
"""
import json
import logging
import random
import time

import aiosqlite

import gang as G
import gangwar
from config import DB_PATH
from rooms import GameError

log = logging.getLogger(__name__)

GOD_COST = 5000
GOD_MIN_RESPECT = 0
GOD_MIN_TERR = 2
GOD_CUT = 0.03
GOD_CAP = 1200
COLLECT_CD = 6 * 3600
FUND_CD = 6 * 3600
PRESSURE_CD = 12 * 3600


def now() -> int:
    return int(time.time())


async def _get(conn, key: str) -> dict:
    raw = await gangwar.kget(conn, key)
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        return {}


async def _set(conn, key: str, data: dict) -> None:
    await gangwar.kset(conn, key, json.dumps(data, ensure_ascii=False))


async def _act(chat_id: int, uid: int, fn):
    import gangplus3 as P3
    return await P3._act(chat_id, uid, fn)


async def _mayor(conn, chat_id: int) -> dict:
    m = await _get(conn, f"mayor:{chat_id}")
    return m if m and m.get("until", 0) > now() else {}


async def _god(conn, chat_id: int) -> dict:
    g = await _get(conn, f"don:{chat_id}")
    return g if g and g.get("until", 0) > now() else {}


async def _mg(conn, chat_id: int) -> dict:
    return await _get(conn, f"mg:{chat_id}")


async def has_roof(conn, chat_id: int, gid: int) -> bool:
    mg = await _mg(conn, chat_id)
    return bool(mg.get("roof_until", 0) > now() and int(mg.get("roof_gid", 0)) == int(gid))


async def _boss(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g or g.get("is_ai"):
        raise GameError("Ты не в банде игроков")
    role = next((m["role"] for m in members if m["user_id"] == uid), "grunt")
    if role != "boss":
        raise GameError("Это решает только босс банды")
    return g


async def _mg_state(conn, chat_id: int) -> dict:
    mg = await _mg(conn, chat_id)
    t = now()
    return {
        "war_until": max(0, int(mg.get("war_until", 0)) - t),
        "war_by": mg.get("war_by", ""),
        "roof_until": max(0, int(mg.get("roof_until", 0)) - t),
        "riot_until": max(0, int(mg.get("riot_until", 0)) - t),
        "mayor_score": int(mg.get("mayor_score", 0)),
        "god_score": int(mg.get("god_score", 0)),
        "war_cd": max(0, int(mg.get("war_cd", 0)) - t),
        "last": mg.get("last") or None,
    }


async def state(conn, chat_id: int, uid: int) -> dict:
    m = await _mayor(conn, chat_id)
    d = await _god(conn, chat_id)
    cand = await _get(conn, f"godcand:{chat_id}")
    if cand and cand.get("until", 0) <= now():
        cand = {}
    g, members = await G._mine(conn, chat_id, uid)
    role = next((x["role"] for x in members if x["user_id"] == uid), "") if g else ""
    own = bool(g and role == "boss")
    terr = 0
    if g:
        terr = (await (await conn.execute("SELECT COUNT(*) FROM territory WHERE owner = ?", (g["id"],))).fetchone())[0]
    can_god = bool(own and not d and (g["respect"] or 0) >= GOD_MIN_RESPECT and terr >= GOD_MIN_TERR and (g["treasury"] or 0) >= GOD_COST)
    why = ""
    cand_me = bool(cand and cand.get("uid") == uid)
    if not d:
        if cand and not cand_me:
            why = f"Кандидат сейчас — {cand.get('name', 'другой игрок')}"
        elif own:
            if terr < GOD_MIN_TERR:
                why = f"Нужно {GOD_MIN_TERR} района, у банды {terr}"
            elif (g["treasury"] or 0) < GOD_COST:
                why = f"В общаке нужно {GOD_COST} ₽, сейчас {g['treasury'] or 0} ₽"
        elif cand_me and not g:
            cash = (await (await conn.execute("SELECT money FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))).fetchone() or [0])[0]
            can_god = cash >= GOD_COST
            why = "" if can_god else f"Нужно {GOD_COST} ₽ наличными на коронацию, у тебя {int(cash)} ₽"
        elif cand_me:
            why = "Ты в банде, но не босс: стань боссом или выйди из банды"
        else:
            why = "Крёстным становится босс банды или назначенный кандидат"
    mayor_is_me = bool(m and m.get("uid") == uid)
    is_cop = False
    try:
        import police
        is_cop = bool(await police._role(conn, chat_id, uid))
    except Exception:
        pass
    ctl = {}
    try:
        import control
        ctl = await control.state(conn, chat_id, uid)
    except Exception:
        log.exception("control.state")
    return {
        "ctl": ctl,
        "mayor": {
            "exists": bool(m), "ai": bool(m.get("ai")), "is_me": mayor_is_me,
            "align": m.get("align", "neutral"), "police": m.get("police", 1.0), "tax": m.get("tax", 1.0),
            "anti_wait": max(0, int(m.get("anti_god_ts", 0)) + PRESSURE_CD - now()),
            "set_wait": max(0, int(m.get("set_ts", 0)) + 8 * 3600 - now()),
            "budget_wait": max(0, int(m.get("budget_ts", 0)) + BUDGET_CD - now()),
            "can_campaign": bool(not m and is_cop),
        },
        "godfather": {
            "player": bool(d), "ai": not bool(d), "is_me": bool(d and d.get("uid") == uid),
            "name": d.get("name", "Крёстный отец"), "treasury": int(d.get("treasury", 0)),
            "influence": int(d.get("influence", 0)),
            "can_become": bool(can_god and (not cand or cand.get("uid") == uid)),
            "why": why, "solo": bool(cand_me and not g),
            "candidate": {"uid": cand.get("uid", 0), "name": cand.get("name", ""), "is_me": cand.get("uid") == uid} if cand else None,
            "has_gang": bool(d and d.get("gid")), "cost": GOD_COST, "min_respect": GOD_MIN_RESPECT, "min_terr": GOD_MIN_TERR,
            "terr": terr, "respect": int(g["respect"] or 0) if g else 0,
            "collect_wait": max(0, int(d.get("collect_ts", 0)) + COLLECT_CD - now()) if d else 0,
            "fund_wait": max(0, int(d.get("fund_ts", 0)) + FUND_CD - now()) if d else 0,
            "pressure_wait": max(0, int(d.get("pressure_ts", 0)) + PRESSURE_CD - now()) if d else 0,
            "ai_power": 1.5 if not d else 1.0,
        },
        "mg": await _mg_state(conn, chat_id),
    }


async def nominate(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g = await _boss(conn, chat_id, uid)
        if await _god(conn, chat_id):
            raise GameError("Крёстный уже есть")
        await _set(conn, f"godcand:{chat_id}", {"uid": uid, "name": name, "until": now() + 7 * 86400})
        await gangwar.say(conn, chat_id, f"👑 «{g['name']}» выдвинула {name} кандидатом в Крёстные отцы. Кандидат может принять должность в «Власть района».")
        return {"text": "👑 Ты кандидат в Крёстные отцы. Прими должность в «Власть района».", "good": True, "delta": 0}
    return await _act(chat_id, uid, run)


async def become_godfather(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        d = await _god(conn, chat_id)
        if d:
            raise GameError("Крёстный отец уже есть: сначала сбрось его")
        cand = await _get(conn, f"godcand:{chat_id}")
        if cand and cand.get("until", 0) <= now():
            cand = {}
        if cand and cand.get("uid") != uid:
            raise GameError(f"Сейчас кандидат — {cand.get('name', 'другой игрок')}")
        mg_, _mm = await G._mine(conn, chat_id, uid)
        if not mg_ and cand and cand.get("uid") == uid:
            # Назначенный кандидат без банды: коронация за свои, семьи-банды нет (gid = 0).
            cash = (await (await conn.execute("SELECT money FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))).fetchone() or [0])[0]
            if cash < GOD_COST:
                raise GameError(f"Нужно {GOD_COST} ₽ наличными на «коронацию», у тебя {int(cash)} ₽")
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (GOD_COST, chat_id, uid))
            await _set(conn, f"don:{chat_id}", {"uid": uid, "name": name, "gid": 0, "since": now(), "until": now() + 30 * 86400, "treasury": 0, "influence": 50})
            await gangwar.kset(conn, f"godcand:{chat_id}", "")
            await gangwar.kset(conn, f"god:{chat_id}", "")
            await gangwar.say(conn, chat_id, f"👑 <b>У района новый Крёстный отец — {name}.</b> Он не состоит в банде и держит тёмную кассу сам. Полиция и мэр нервно курят.")
            return {"text": f"👑 Ты стал Крёстным отцом. Коронация стоила {GOD_COST} ₽. Теперь можно собирать дань и давить на банды и мэра.", "good": True, "delta": -GOD_COST}
        g = await _boss(conn, chat_id, uid)
        terr = (await (await conn.execute("SELECT COUNT(*) FROM territory WHERE owner = ?", (g["id"],))).fetchone())[0]
        if (g["respect"] or 0) < GOD_MIN_RESPECT:
            raise GameError(f"Нужно уважение {GOD_MIN_RESPECT}, у банды {g['respect'] or 0}")
        if terr < GOD_MIN_TERR:
            raise GameError(f"Нужно {GOD_MIN_TERR} района, у банды {terr}")
        if (g["treasury"] or 0) < GOD_COST:
            raise GameError(f"В общаке нужно {GOD_COST} ₽ на «коронацию»")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (GOD_COST, g["id"]))
        await _set(conn, f"don:{chat_id}", {"uid": uid, "name": name, "gid": g["id"], "since": now(), "until": now() + 30 * 86400, "treasury": 0, "influence": 50})
        await gangwar.kset(conn, f"godcand:{chat_id}", "")
        await gangwar.kset(conn, f"god:{chat_id}", "")
        await gangwar.say(conn, chat_id, f"👑 <b>У района новый Крёстный отец — {name}.</b> Семья «{g['name']}» берёт тёмную кассу под контроль. Полиция и мэр нервно курят.")
        return {"text": f"👑 Ты стал Крёстным отцом. Взнос {GOD_COST} ₽ ушёл в тёмную кассу. Теперь можно собирать дань и финансировать стороны.", "good": True, "delta": -GOD_COST}
    return await _act(chat_id, uid, run)


async def collect(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        d = await _god(conn, chat_id)
        if not d or d.get("uid") != uid:
            raise GameError("Ты не Крёстный отец")
        if now() - int(d.get("collect_ts", 0)) < COLLECT_CD:
            raise GameError("Дань собирают раз в 6 часов")
        cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 0 AND id != ?", (chat_id, d["gid"]))
        rows = await cur.fetchall()
        total = 0
        for gid, gname, treasury in rows:
            cut = min(GOD_CAP // max(1, len(rows)), int((treasury or 0) * GOD_CUT))
            if cut <= 0:
                continue
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cut, gid))
            total += cut
        d["treasury"] = int(d.get("treasury", 0)) + total
        d["collect_ts"] = now()
        await _set(conn, f"don:{chat_id}", d)
        await gangwar.say(conn, chat_id, f"👑 Крёстный отец собрал дань: {total} ₽ в тёмную кассу.")
        try:
            import justice
            await justice.report(conn, chat_id, uid, "godfather", "сбор дани", 2)
        except Exception:
            pass
        return {"text": f"👑 Собрано {total} ₽ дани.", "good": True, "delta": total}
    return await _act(chat_id, uid, run)


async def fund(chat_id: int, uid: int, name: str, side: str, amount, gid=0) -> dict:
    amount = int(amount or 0)
    gid = int(gid or 0)
    async def run(conn):
        d = await _god(conn, chat_id)
        if not d or d.get("uid") != uid:
            raise GameError("Ты не Крёстный отец")
        if side not in ("gang", "police"):
            raise GameError("Выбери сторону: gang или police")
        if not 500 <= amount <= 5000:
            raise GameError("Сумма от 500 до 5000 ₽")
        if int(d.get("treasury", 0)) < amount:
            raise GameError("В тёмной кассе нет столько")
        if now() - int(d.get("fund_ts", 0)) < FUND_CD:
            raise GameError("Финансирование раз в 6 часов")
        d["treasury"] -= amount
        d["fund_ts"] = now()
        if side == "police":
            await gangwar.kset(conn, f"pfund:{chat_id}", int(await gangwar.kget(conn, f"pfund:{chat_id}") or 0) + amount)
            await gangwar.say(conn, chat_id, f"👑 Крёстный отец занёс {amount} ₽ в фонд полиции. «Служите, ребята».")
        else:
            if gid:
                cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND id = ? AND is_ai < 2", (chat_id, gid))
            else:
                cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 0 ORDER BY treasury ASC LIMIT 1", (chat_id,))
            row = await cur.fetchone()
            if not row:
                raise GameError("Такой банды нет")
            await conn.execute("UPDATE gang SET treasury = treasury + ?, respect = MIN(100, respect + 2) WHERE id = ?", (amount, row[0]))
            await gangwar.say(conn, chat_id, f"👑 Крёстный отец выдал «{row[1]}» {amount} ₽. Долг он найдёт, где записать.")
            try:
                import justice
                await justice.report(conn, chat_id, uid, "godfather", "финансирование ОПГ", 3)
            except Exception:
                pass
        await _set(conn, f"don:{chat_id}", d)
        return {"text": f"👑 {amount} ₽ из тёмной кассы ушли " + ("в фонд полиции." if side == "police" else f"в общак «{row[1]}»: уважение банды +2."), "good": True, "delta": -amount}
    return await _act(chat_id, uid, run)


async def pressure(chat_id: int, uid: int, name: str, target: str = "mayor") -> dict:
    target = target or "mayor"
    async def run(conn):
        d = await _god(conn, chat_id)
        if not d or d.get("uid") != uid:
            raise GameError("Ты не Крёстный отец")
        if now() - int(d.get("pressure_ts", 0)) < PRESSURE_CD:
            raise GameError("Давление раз в 12 часов")
        if target == "mayor":
            m = await _mayor(conn, chat_id)
            if m:
                m["police"] = max(0.3, float(m.get("police", 1.0)) - 0.3)
                m["tax"] = max(0.5, float(m.get("tax", 1.0)) - 0.1)
                await _set(conn, f"mayor:{chat_id}", m)
                await gangwar.say(conn, chat_id, "👑 Крёстный отец надавил на мэра: налог и полиция ослабли. Мэр делает вид, что так и надо.")
                try:
                    import justice
                    await justice.report(conn, chat_id, uid, "godfather", "давление на мэра", 4)
                except Exception:
                    pass
            else:
                aim = await _get(conn, f"aimayor:{chat_id}")
                aim["police"] = 0.5
                aim["pressed"] = now() + 3600
                aim["until"] = max(int(aim.get("until", 0)), now() + 3600)
                await _set(conn, f"aimayor:{chat_id}", aim)
                await gangwar.say(conn, chat_id, "👑 Крёстный отец надавил на мэрию: полиция ×0.5 на час.")
        else:
            await gangwar.kset(conn, f"pfund:{chat_id}", max(0, int(await gangwar.kget(conn, f"pfund:{chat_id}") or 0) - 1000))
            await gangwar.say(conn, chat_id, "👑 Крёстный отец перекрыл поток налички полиции: фонд −1000 ₽.")
        d["pressure_ts"] = now()
        await _set(conn, f"don:{chat_id}", d)
        return {"text": "👑 Давление сработало: мэр/полиция ослаблены.", "good": True, "delta": 0}
    return await _act(chat_id, uid, run)


BUDGET_CD = 12 * 3600


async def mayor_budget(chat_id: int, uid: int, name: str, amount) -> dict:
    """Мэр переводит часть фонда полиции в фонд пособий: порядок в районе растёт, но полиции остаётся меньше."""
    amount = int(amount or 0)

    async def run(conn):
        import balance
        import police
        m = await _mayor(conn, chat_id)
        if not m or m.get("ai") or int(m.get("uid", 0)) != uid:
            raise GameError("Бюджетом распоряжается мэр")
        if not 500 <= amount <= 3000:
            raise GameError("Сумма от 500 до 3000 ₽")
        if now() - int(m.get("budget_ts", 0)) < BUDGET_CD:
            raise GameError("Бюджет делят раз в 12 часов")
        if await police.fund(conn, chat_id) < amount + 1000:
            raise GameError("В фонде полиции должно остаться хотя бы 1000 ₽")
        await police.fund_add(conn, chat_id, -amount)
        await balance.welfare_add(conn, chat_id, amount)
        order = min(100, int(await gangwar.kget(conn, f"porder:{chat_id}") or 40) + amount // 500)
        await gangwar.kset(conn, f"porder:{chat_id}", order)
        m["budget_ts"] = now()
        await _set(conn, f"mayor:{chat_id}", m)
        await gangwar.say(conn, chat_id, f"🏛 Мэр {name} перевёл {amount} ₽ из фонда полиции в фонд пособий. Порядок в районе: {order}%.")
        return {"text": f"🏛 {amount} ₽ ушли в фонд пособий, порядок вырос до {order}%. У полиции стало меньше денег на операции.", "good": True, "delta": 0}
    return await _act(chat_id, uid, run)


async def mayor_war(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        m = await _mayor(conn, chat_id)
        if not m or m.get("uid") != uid:
            raise GameError("Войну Крёстному объявляет мэр")
        if not await _god(conn, chat_id):
            raise GameError("Крёстного сейчас нет")
        mg = await _mg(conn, chat_id)
        _war_guard(mg)
        mg["war_until"] = now() + 24 * 3600
        mg["war_by"] = "mayor"
        mg["mayor_score"], mg["god_score"], mg["war_tick"] = 5, 0, now()
        await _set(conn, f"mg:{chat_id}", mg)
        await gangwar.say(conn, chat_id, f"🏛 <b>Мэр {name} объявил войну Крёстному отцу!</b> Полиция начинает давить семью. 24 часа.")
        return {"text": "🏛 Война Крёстному объявлена на 24 часа.", "good": True, "delta": 0}
    return await _act(chat_id, uid, run)


async def god_war(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        d = await _god(conn, chat_id)
        if not d or d.get("uid") != uid:
            raise GameError("Войну мэру объявляет Крёстный")
        mg = await _mg(conn, chat_id)
        _war_guard(mg)
        mg["war_until"] = now() + 24 * 3600
        mg["war_by"] = "godfather"
        mg["god_score"], mg["mayor_score"], mg["war_tick"] = 5, 0, now()
        await _set(conn, f"mg:{chat_id}", mg)
        await gangwar.say(conn, chat_id, f"👑 <b>Крёстный отец {name} объявил войну мэру!</b> Семья поднимает район. 24 часа.")
        return {"text": "👑 Война мэру объявлена на 24 часа.", "good": True, "delta": 0}
    return await _act(chat_id, uid, run)


async def mayor_raid(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        import police
        m = await _mayor(conn, chat_id)
        d = await _god(conn, chat_id)
        if not m or m.get("uid") != uid:
            raise GameError("Рейд на семью проводит мэр")
        if not d:
            raise GameError("Крёстного сейчас нет")
        if await police.fund(conn, chat_id) < 1000:
            raise GameError("Нужно 1000 ₽ в фонде полиции")
        if now() - int(await gangwar.kget(conn, f"mg_raid:{chat_id}") or 0) < 6 * 3600:
            raise GameError("Рейд на семью раз в 6 часов")
        await police.fund_add(conn, chat_id, -1000)
        g = await gangwar._gang_row(conn, int(d.get("gid", 0))) if int(d.get("gid", 0)) else None
        if not g:
            # У Крёстного нет банды: рейд бьёт прямо по тёмной кассе.
            conf = int(int(d.get("treasury", 0)) * 0.15)
            d["treasury"] = int(d.get("treasury", 0)) - conf
            d["influence"] = max(0, int(d.get("influence", 0)) - 5)
            await _set(conn, f"don:{chat_id}", d)
            await police.fund_add(conn, chat_id, conf)
            await gangwar.kset(conn, f"mg_raid:{chat_id}", str(now()))
            await mg_score(conn, chat_id, "mayor", 6)
            await gangwar.say(conn, chat_id, f"🚔 Мэр {name} провёл рейд на тёмную кассу Крёстного: изъято {conf} ₽, влияние −5.")
            return {"text": f"🚔 Рейд: из тёмной кассы изъято {conf} ₽, влияние Крёстного −5.", "good": True, "delta": 0}
        conf = int((g["treasury"] or 0) * 0.15)
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (conf, g["id"]))
        await police.fund_add(conn, chat_id, conf)
        await police.case_add(conn, chat_id, g["id"], 10)
        await conn.execute("UPDATE territory SET police = MIN(100, police + 10), control = MAX(0, control - 5) WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        await gangwar.kset(conn, f"mg_raid:{chat_id}", str(now()))
        await mg_score(conn, chat_id, "mayor", 6)
        await gangwar.say(conn, chat_id, f"🚔 Мэр {name} провёл рейд на «{g['name']}»: изъято {conf} ₽, дело +10, давление +10.")
        return {"text": f"🚔 Рейд: изъято {conf} ₽, давление на семью +10.", "good": True, "delta": 0}
    return await _act(chat_id, uid, run)


async def god_riot(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        d = await _god(conn, chat_id)
        m = await _mayor(conn, chat_id)
        if not d or d.get("uid") != uid:
            raise GameError("Бунт поднимает Крёстный")
        if int(d.get("treasury", 0)) < 2000:
            raise GameError("Нужно 2000 ₽ из тёмной кассы")
        if now() - int(await gangwar.kget(conn, f"mg_riot:{chat_id}") or 0) < 12 * 3600:
            raise GameError("Бунт раз в 12 часов")
        d["treasury"] -= 2000
        await _set(conn, f"don:{chat_id}", d)
        mg = await _mg(conn, chat_id)
        if m:
            m["police"] = max(0.3, float(m.get("police", 1.0)) - 0.3)
            m["tax"] = max(0.5, float(m.get("tax", 1.0)) - 0.1)
            await _set(conn, f"mayor:{chat_id}", m)
            mg["riot_restore"] = True
        else:
            # ИИ-мэр Архип: полиция вполсилы, пока бунт не уляжется (patrons._tick_chat читает pressed)
            aim = await _get(conn, f"aimayor:{chat_id}")
            aim["pressed"] = now() + 6 * 3600
            aim["police"] = 0.5
            aim["until"] = max(int(aim.get("until", 0)), now() + 6 * 3600)
            await _set(conn, f"aimayor:{chat_id}", aim)
        mg["riot_until"] = now() + 6 * 3600
        if int(mg.get("war_until", 0)) > now():
            mg["god_score"] = int(mg.get("god_score", 0)) + 8
        await _set(conn, f"mg:{chat_id}", mg)
        await gangwar.kset(conn, f"mg_riot:{chat_id}", str(now()))
        await gangwar.say(conn, chat_id, "🔥 <b>Крёстный подкупил граждан!</b> На районе бунт: налог и полиция мэра ослаблены на 6 часов.")
        return {"text": "🔥 Граждане подняли бунт: мэр ослаблен на 6 часов.", "good": True, "delta": -2000}
    return await _act(chat_id, uid, run)


async def roof_pay(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        d = await _god(conn, chat_id)
        m = await _mayor(conn, chat_id)
        if not d or d.get("uid") != uid:
            raise GameError("Крышу у мэра покупает Крёстный")
        if int(d.get("treasury", 0)) < 3000:
            raise GameError("Нужно 3000 ₽ из тёмной кассы")
        if not int(d.get("gid", 0)):
            raise GameError("Крыша защищает банду, а у тебя её нет: полиции нечего не трогать")
        if await has_roof(conn, chat_id, int(d.get("gid", 0))):
            raise GameError("Крыша уже оплачена")
        d["treasury"] -= 3000
        await _set(conn, f"don:{chat_id}", d)
        if m and not m.get("ai"):
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (3000, chat_id, int(m["uid"])))
        else:
            try:
                import justice
                await justice.report(conn, chat_id, uid, "godfather", "подкуп мэрии", 2)
            except Exception:
                pass
        await conn.execute("UPDATE territory SET police = 0 WHERE chat_id = ? AND owner = ?", (chat_id, int(d.get("gid", 0))))
        await conn.execute("UPDATE police_case SET pts = 0 WHERE chat_id = ? AND gang_id = ?", (chat_id, int(d.get("gid", 0))))
        mg = await _mg(conn, chat_id)
        mg["roof_until"] = now() + 24 * 3600
        mg["roof_gid"] = int(d.get("gid", 0))
        await _set(conn, f"mg:{chat_id}", mg)
        await gangwar.say(conn, chat_id, f"🤝 Крёстный заплатил мэру 3 000 ₽ за крышу: полиция не трогает «{d.get('name', 'семью')}» 24 часа.")
        return {"text": "🤝 Крыша оплачена: мэр получил 3 000 ₽, полиция не трогает семью 24 часа.", "good": True, "delta": -3000}
    return await _act(chat_id, uid, run)


async def tick(conn) -> None:
    """Крыша: пока оплачена, давление и дело семьи чистятся."""
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
        for (chat_id,) in await cur.fetchall():
            mg = await _mg(conn, chat_id)
            if mg.get("roof_until", 0) > now() and mg.get("roof_gid"):
                gid = int(mg["roof_gid"])
                await conn.execute("UPDATE territory SET police = 0 WHERE chat_id = ? AND owner = ?", (chat_id, gid))
                await conn.execute("UPDATE police_case SET pts = MAX(0, pts - 2) WHERE chat_id = ? AND gang_id = ?", (chat_id, gid))
            try:
                await _mg_tick(conn, chat_id)
                await _ai_mayor_vs_god(conn, chat_id)
            except Exception:
                log.exception("Сбой войны мэра и Крёстного")
    except Exception:
        log.exception("Сбой крыши мэра")


MG_GAP = 30 * 60
AI_MAYOR_GAP = 12 * 3600


async def _ai_mayor_vs_god(conn, chat_id: int) -> None:
    """Мэра-игрока нет: Архип сам давит на Крёстного-игрока. Раз в 12 часов — рейд на кассу или война."""
    d = await _god(conn, chat_id)
    if not d or await _mayor(conn, chat_id):
        return
    t = now()
    if t - int(await gangwar.kget(conn, f"aimg:{chat_id}") or 0) < AI_MAYOR_GAP:
        return
    await gangwar.kset(conn, f"aimg:{chat_id}", str(t))
    mg = await _mg(conn, chat_id)
    gid = int(d.get("gid", 0))
    if gid and mg.get("roof_until", 0) > t and int(mg.get("roof_gid", 0)) == gid:
        return  # крыша оплачена: Архип делает вид, что ничего не видит
    infl = int(d.get("influence", 0))
    if infl >= 65 and int(mg.get("war_until", 0)) <= t and int(mg.get("war_cd", 0)) <= t and random.random() < 0.25:
        mg.update({"war_until": t + 24 * 3600, "war_by": "mayor", "mayor_score": 5, "god_score": 0, "war_tick": t})
        await _set(conn, f"mg:{chat_id}", mg)
        await gangwar.say(conn, chat_id, "🎩 <b>Мэр Архип объявил войну Крёстному отцу!</b> Полиция начинает давить семью. 24 часа.")
        return
    if infl >= 40 and random.random() < 0.5:
        import police
        cut = min(1500, int(int(d.get("treasury", 0)) * 0.15))
        d["treasury"] = int(d.get("treasury", 0)) - cut
        d["influence"] = max(0, infl - 8)
        await _set(conn, f"don:{chat_id}", d)
        if cut:
            await police.fund_add(conn, chat_id, cut)
        await gangwar.say(conn, chat_id, f"🎩 Мэр Архип накрыл тёмную кассу Крёстного: изъято {cut} ₽, влияние −8.")
MG_WAR_CD = 12 * 3600


def _war_guard(mg: dict) -> None:
    if int(mg.get("war_until", 0)) > now():
        raise GameError("Война уже идёт: исход решит счёт")
    if int(mg.get("war_cd", 0)) > now():
        raise GameError(f"После прошлой войны перемирие ещё {max(1, (int(mg['war_cd']) - now()) // 60)} мин")


async def mg_score(conn, chat_id: int, side: str, pts: int) -> None:
    """Очки в войне мэра и Крёстного: считаются только пока война идёт."""
    mg = await _mg(conn, chat_id)
    if int(mg.get("war_until", 0)) <= now():
        return
    key = "mayor_score" if side == "mayor" else "god_score"
    mg[key] = int(mg.get(key, 0)) + int(pts)
    await _set(conn, f"mg:{chat_id}", mg)


async def _mg_finish(conn, chat_id: int, mg: dict, d: dict, m: dict) -> None:
    import police
    ms, gs = int(mg.get("mayor_score", 0)), int(mg.get("god_score", 0))
    winner = "mayor" if (not d or ms > gs) else "god" if gs > ms else ""
    if winner == "mayor" and d:
        cut = min(3000, int(int(d.get("treasury", 0)) * 0.3))
        d["treasury"] = int(d.get("treasury", 0)) - cut
        d["influence"] = max(0, int(d.get("influence", 0)) - 25)
        await police.fund_add(conn, chat_id, cut)
        if d["influence"] <= 0:
            await gangwar.kset(conn, f"don:{chat_id}", "")
            text = f"🏛 Мэр выиграл войну у Крёстного {ms}:{gs}: изъято {cut} ₽, Крёстный лишился власти."
        else:
            await _set(conn, f"don:{chat_id}", d)
            text = f"🏛 Мэр выиграл войну у Крёстного {ms}:{gs}: {cut} ₽ тёмной кассы ушли в фонд полиции, влияние Крёстного −25."
    elif winner == "mayor":
        text = "🏛 Война окончена: Крёстного больше нет, мэр остался хозяином района."
    elif winner == "god":
        fund = await police.fund(conn, chat_id)
        cut = min(3000, int(fund * 0.2))
        await police.fund_add(conn, chat_id, -cut)
        d["treasury"] = int(d.get("treasury", 0)) + cut
        d["influence"] = min(100, int(d.get("influence", 0)) + 15)
        await _set(conn, f"don:{chat_id}", d)
        if m:
            m["police"] = max(0.3, round(float(m.get("police", 1.0)) - 0.2, 2))
            await _set(conn, f"mayor:{chat_id}", m)
        else:
            aim = await _get(conn, f"aimayor:{chat_id}")
            aim["pressed"] = now() + 12 * 3600
            aim["until"] = max(int(aim.get("until", 0)), now() + 12 * 3600)
            await _set(conn, f"aimayor:{chat_id}", aim)
        text = f"👑 Крёстный выиграл войну у мэра {gs}:{ms}: {cut} ₽ из фонда полиции ушли в тёмную кассу, влияние +15, полиция мэра ослаблена."
    else:
        text = f"⚖️ Война мэра и Крёстного закончилась вничью {ms}:{gs}: каждый остался при своём."
    mg.update({"war_until": 0, "war_by": "", "mayor_score": 0, "god_score": 0, "war_cd": now() + MG_WAR_CD, "last": {"winner": winner, "mayor": ms, "god": gs, "ts": now()}})
    await _set(conn, f"mg:{chat_id}", mg)
    await gangwar.say(conn, chat_id, text)


async def _mg_tick(conn, chat_id: int) -> None:
    import police
    mg = await _mg(conn, chat_id)
    if not mg:
        return
    t = now()
    if mg.get("riot_restore") and int(mg.get("riot_until", 0)) <= t:
        m = await _mayor(conn, chat_id)
        if m:
            m["police"] = min(1.5, round(float(m.get("police", 1.0)) + 0.3, 2))
            m["tax"] = min(1.5, round(float(m.get("tax", 1.0)) + 0.1, 2))
            await _set(conn, f"mayor:{chat_id}", m)
        mg["riot_restore"] = False
        await _set(conn, f"mg:{chat_id}", mg)
        await gangwar.say(conn, chat_id, "🔥 Бунт против мэра утих: налог и полиция вернулись к прежней силе.")
    if not int(mg.get("war_until", 0)):
        return
    d, m = await _god(conn, chat_id), await _mayor(conn, chat_id)
    if int(mg["war_until"]) <= t or not d:
        await _mg_finish(conn, chat_id, mg, d, m)
        return
    if t - int(mg.get("war_tick", 0)) < MG_GAP:
        return
    mg["war_tick"] = t
    gid = int(d.get("gid", 0))
    terr = (await (await conn.execute("SELECT COUNT(*) FROM territory WHERE chat_id = ? AND owner = ?", (chat_id, gid))).fetchone())[0] if gid else 0
    fund = await police.fund(conn, chat_id)
    # Мэр давит семью: дело и патрули на её районах (крыша от этого защищает).
    if gid and int(mg.get("roof_until", 0)) <= t:
        await police.case_add(conn, chat_id, gid, 2)
        await conn.execute("UPDATE territory SET police = MIN(100, police + 2) WHERE chat_id = ? AND owner = ?", (chat_id, gid))
    mg["mayor_score"] = int(mg.get("mayor_score", 0)) + 1 + (1 if fund >= 5000 else 0)
    # Семья бьёт по фонду полиции: половина украденного оседает в тёмной кассе.
    drain = min(fund, 40 + 20 * terr)
    if drain > 0:
        await police.fund_add(conn, chat_id, -drain)
        d["treasury"] = int(d.get("treasury", 0)) + drain // 2
        await _set(conn, f"don:{chat_id}", d)
    mg["god_score"] = int(mg.get("god_score", 0)) + 1 + terr // 2 + int(d.get("influence", 0)) // 50
    await _set(conn, f"mg:{chat_id}", mg)


async def mayor_anti_godfather(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        import police
        m = await _mayor(conn, chat_id)
        if not m or m.get("uid") != uid or m.get("ai") or not await police._role(conn, chat_id, uid):
            raise GameError("Это может только действующий мэр-полицейский")
        if now() - int(m.get("anti_god_ts", 0)) < PRESSURE_CD:
            raise GameError("Операция против Крёстного раз в 12 часов")
        if await police.fund(conn, chat_id) < 2500:
            raise GameError("Нужно 2500 ₽ в фонде полиции")
        await police.fund_add(conn, chat_id, -2500)
        d = await _god(conn, chat_id)
        if d:
            cut = min(3000, int(d.get("treasury", 0) * 0.5))
            d["treasury"] = max(0, int(d.get("treasury", 0)) - cut)
            d["influence"] = max(0, int(d.get("influence", 0)) - 30)
            await police.fund_add(conn, chat_id, cut)
            if d["influence"] <= 0:
                await gangwar.kset(conn, f"don:{chat_id}", "")
                text = f"🚔 Мэр разгромил семью Крёстного: изъято {cut} ₽, Крёстный потерял власть."
            else:
                await _set(conn, f"don:{chat_id}", d)
                text = f"🚔 Мэр накрыл тёмную кассу: изъято {cut} ₽, влияние Крёстного −30."
        else:
            aim = await _get(conn, f"god:{chat_id}")
            if aim:
                aim["suppressed"] = now() + 6 * 3600
                await _set(conn, f"god:{chat_id}", aim)
            text = "🚔 Мэр провёл облаву на семью Крёстного: ИИ-патрон ослаблен на 6 часов."
        m["anti_god_ts"] = now()
        await _set(conn, f"mayor:{chat_id}", m)
        await mg_score(conn, chat_id, "mayor", 8)
        await gangwar.say(conn, chat_id, text)
        return {"text": text, "good": True, "delta": 0}
    return await _act(chat_id, uid, run)
