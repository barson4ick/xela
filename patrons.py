"""ИИ-патроны: Крёстный отец (ОПГ) и Мэр Архип (полиция).
Дают займы/наёмников/крышу одной стороне и давление другой. Игроки могут брать, платить и подкупать.
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
LOAN_AMOUNT, LOAN_OWED = 3000, 3600
MERC_COST = 2000
BRIBE_COST = 2000
PATRON_GOD_CAP, PATRON_MAYOR_CAP = 1000, 700
TICK_GAP = 60 * 60


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


async def _capped(conn, chat_id: int, key: str, amount: int, cap: int, flow_key: str) -> int:
    if amount <= 0:
        return 0
    day = time.strftime("%Y-%m-%d")
    k = f"pcap:{chat_id}:{key}:{day}"
    used = int(await gangwar.kget(conn, k) or 0)
    give = min(int(amount), max(0, int(cap) - used))
    if give <= 0:
        return 0
    await gangwar.kset(conn, k, str(used + give))
    try:
        import balance
        await balance.flow(conn, chat_id, flow_key, give)
    except Exception:
        pass
    return give


async def ai_mayor_mods(conn, chat_id: int) -> dict:
    try:
        import world
        ov = await world.mayor_override(conn, chat_id)
        if ov is not None:
            return ov
    except Exception:
        log.exception("world.mayor_override")
    m = await _get(conn, f"aimayor:{chat_id}")
    if m.get("until", 0) <= now():
        return {"tax": 1.0, "curfew": False, "police": 1.0}
    return {"tax": m.get("tax", 1.0), "curfew": bool(m.get("curfew")), "police": m.get("police", 1.0)}


async def state(conn, chat_id: int, uid: int) -> dict:
    mayor_raw = await gangwar.kget(conn, f"mayor:{chat_id}")
    player_mayor = False
    if mayor_raw:
        try:
            pm = json.loads(mayor_raw)
            player_mayor = bool(pm and pm.get("until", 0) > now())
        except Exception:
            player_mayor = False
    god = await _get(conn, f"god:{chat_id}")
    don = await _get(conn, f"don:{chat_id}")
    aim = await _get(conn, f"aimayor:{chat_id}")
    if aim.get("until", 0) <= now():
        aim = {"tax": 1.0, "police": 1.0, "curfew": False, "until": 0}
    return {
        "godfather": {
            "name": "Крёстный отец", "icon": "👑",
            "offer": god.get("offer") if god.get("offer", {}).get("expires", 0) > now() else None,
            "tribute": god.get("tribute") if god.get("tribute", {}).get("expires", 0) > now() else None,
            "debt": god.get("debt") if god.get("debt", {}).get("due", 0) > 0 else None,
            "merc_cost": MERC_COST, "merc_until": max(0, god.get("merc_until", 0) - now()),
            "in_shadow": bool(don),
        },
        "mayor": {
            "name": "Мэр Архип", "icon": "🎩", "in_shadow": player_mayor,
            "tax": aim.get("tax", 1.0), "police": aim.get("police", 1.0), "curfew": bool(aim.get("curfew")),
            "bribe_cost": BRIBE_COST, "bribe_until": max(0, aim.get("bribe_until", 0) - now()),
        },
    }


async def _need_boss(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g or g.get("is_ai"):
        raise GameError("Ты не в банде игроков")
    role = next((m["role"] for m in members if m["user_id"] == uid), "grunt")
    if role != "boss":
        raise GameError("Решать с патронами может только босс")
    return g


async def loan_accept(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g = await _need_boss(conn, chat_id, uid)
        data = await _get(conn, f"god:{chat_id}")
        offer = data.get("offer") or {}
        if not offer or offer.get("expires", 0) <= now():
            raise GameError("Крёстный отец не предлагает заём")
        if (data.get("debt") or {}).get("due", 0) > 0:
            raise GameError("Сначала закрой прошлый долг")
        amount, owed = int(offer["amount"]), int(offer["owed"])
        data["debt"] = {"owed": owed, "due": now() + 24 * 3600, "amount": amount, "gid": g["id"]}
        data.pop("offer", None)
        await _set(conn, f"god:{chat_id}", data)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (amount, g["id"]))
        await gangwar.say_ai(conn, chat_id, f"👑 Крёстный отец выдал «{g['name']}» {amount} ₽. Вернуть до {owed} ₽ в течение суток.")
        return {"text": f"👑 Заём {amount} ₽ в общаке. Долг {owed} ₽, срок 24 часа.", "good": True, "delta": amount}
    return await _act(chat_id, uid, run)


async def loan_repay(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g = await _need_boss(conn, chat_id, uid)
        data = await _get(conn, f"god:{chat_id}")
        debt = data.get("debt") or {}
        if not debt:
            raise GameError("Долгов перед Крёстным отцом нет")
        owed = int(debt.get("owed", 0))
        if (g["treasury"] or 0) < owed:
            raise GameError(f"В общаке {g['treasury'] or 0} ₽, нужно {owed} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ?, respect = MIN(100, respect + 2) WHERE id = ?", (owed, g["id"]))
        data.pop("debt", None)
        await _set(conn, f"god:{chat_id}", data)
        return {"text": f"👑 Долг {owed} ₽ закрыт. Крёстный отец доволен: +2 уважения.", "good": True, "delta": -owed}
    return await _act(chat_id, uid, run)


async def merc_hire(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g = await _need_boss(conn, chat_id, uid)
        if (g["treasury"] or 0) < MERC_COST:
            raise GameError(f"Наёмники стоят {MERC_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (MERC_COST, g["id"]))
        data = await _get(conn, f"god:{chat_id}")
        if data.get("merc_until", 0) > now():
            raise GameError("Бригада уже работает")
        data["merc_until"] = now() + 24 * 3600
        await _set(conn, f"god:{chat_id}", data)
        cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        keys = [r[0] for r in await cur.fetchall()]
        if keys:
            await conn.execute("UPDATE territory SET control = MIN(100, control + 5) WHERE chat_id = ? AND key = ?", (chat_id, random.choice(keys)))
        await gangwar.say_ai(conn, chat_id, f"🥊 Крёстный отец прислал «{g['name']}» бригаду на сутки: контроль района +5.")
        return {"text": "🥊 Бригада наёмников на 24 часа: +5 контроля случайному району.", "good": True, "delta": -MERC_COST}
    return await _act(chat_id, uid, run)


async def mayor_bribe(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g = await _need_boss(conn, chat_id, uid)
        if (g["treasury"] or 0) < BRIBE_COST:
            raise GameError(f"Конверт мэру стоит {BRIBE_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (BRIBE_COST, g["id"]))
        aim = await _get(conn, f"aimayor:{chat_id}")
        if aim.get("bribe_until", 0) > now():
            raise GameError("Мэр уже в доле: второй конверт не нужен")
        if random.random() < 0.75:
            aim["police"] = 0.5
            aim["bribe_until"] = now() + 3600
            aim["until"] = max(aim.get("until", 0), now() + 3600)
            await _set(conn, f"aimayor:{chat_id}", aim)
            await gangwar.kset(conn, f"porder:{chat_id}", max(0, int(await gangwar.kget(conn, f"porder:{chat_id}") or 40) - 5))
            return {"text": "🎩 Мэр взял конверт: полиция ×0.5 на час, порядок −5.", "good": True, "delta": -BRIBE_COST}
        police = __import__("police")
        await police.case_add(conn, chat_id, g["id"], 10)
        await gangwar.say_ai(conn, chat_id, f"🎩 Мэр отказался от конверта «{g['name']}»: дело +10, полиция настороже.")
        return {"text": "🎩 Мэр отказался: дело +10, конверт ушёл в дело.", "good": False, "delta": -BRIBE_COST}
    return await _act(chat_id, uid, run)


async def _act(chat_id: int, uid: int, fn) -> dict:
    import gangplus3 as P3
    return await P3._act(chat_id, uid, fn)


async def tribute_pay(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g = await _need_boss(conn, chat_id, uid)
        data = await _get(conn, f"god:{chat_id}")
        trib = data.get("tribute") or {}
        if not trib or trib.get("expires", 0) <= now():
            raise GameError("Дани сейчас нет")
        amount = int(trib.get("amount", 0))
        if (g["treasury"] or 0) < amount:
            raise GameError(f"В общаке {g['treasury'] or 0} ₽, нужно {amount} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ?, respect = MIN(100, respect + 1) WHERE id = ?", (amount, g["id"]))
        data.pop("tribute", None)
        await _set(conn, f"god:{chat_id}", data)
        return {"text": f"👑 Дань {amount} ₽ ушла Крёстному отцу. Он доволен.", "good": True, "delta": -amount}
    return await _act(chat_id, uid, run)


async def tribute_refuse(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g = await _need_boss(conn, chat_id, uid)
        data = await _get(conn, f"god:{chat_id}")
        if not data.get("tribute"):
            raise GameError("Дани сейчас нет")
        data.pop("tribute", None)
        await _set(conn, f"god:{chat_id}", data)
        await gangwar.say_ai(conn, chat_id, f"👑 «{g['name']}» отказалась платить дань Крёстному отцу. Он этого не забудет.")
        import world
        await world.strike(conn, chat_id, g["id"], "отказалась платить дань")
        return {"text": "👑 Ты отказался платить дань. Крёстный отец прислал боевиков: смотри чат. Откупиться можно в разделе «Патроны».", "good": False, "delta": 0}
    return await _act(chat_id, uid, run)


async def tick(conn) -> None:
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
        for (chat_id,) in await cur.fetchall():
            last = int(await gangwar.kget(conn, f"patron:tick:{chat_id}") or 0)
            if last and now() - last < TICK_GAP:
                continue
            await gangwar.kset(conn, f"patron:tick:{chat_id}", str(now()))
            await _tick_chat(conn, chat_id)
    except Exception:
        log.exception("Сбой ИИ-патронов")


async def _tick_chat(conn, chat_id: int) -> None:
    mayor_raw = await gangwar.kget(conn, f"mayor:{chat_id}")
    player_mayor = False
    if mayor_raw:
        try:
            pm = json.loads(mayor_raw)
            player_mayor = bool(pm and pm.get("until", 0) > now())
        except Exception:
            pass
    suspended = int(await gangwar.kget(conn, f"aimsus:{chat_id}") or 0) > now()
    import power
    player_god = bool(await power._god(conn, chat_id))
    if not player_mayor and not suspended:
        aim = await _get(conn, f"aimayor:{chat_id}")
        # Мэр реагирует на беспредел: чем больше районов у банд, тем жёстче полиция.
        cur = await conn.execute("SELECT COUNT(*) FROM territory WHERE chat_id = ? AND owner != 0", (chat_id,))
        controlled = (await cur.fetchone())[0]
        cur = await conn.execute("SELECT COUNT(*) FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
        gangs = (await cur.fetchone())[0]
        aim["tax"] = 1.0 if controlled < 5 else 1.5
        aim["police"] = 1.0 if controlled < 4 else 1.5
        # Если мэра-игрока нет, Архип жмёт сильнее.
        aim["police"] = max(aim["police"], 1.5 if player_mayor else 1.75)
        aim["tax"] = max(aim["tax"], 1.5 if not player_mayor else 1.0)
        aim["curfew"] = random.random() < 0.25
        if int(aim.get("pressed", 0)) > now():
            aim["police"] = 0.5  # Крёстный надавил на мэрию: час полиция вполсилы
        aim["until"] = now() + 2 * 3600
        await _set(conn, f"aimayor:{chat_id}", aim)
        add = await _capped(conn, chat_id, "mayor", random.randint(150, 250), 1000 if not player_mayor else PATRON_MAYOR_CAP, "ai_patron")
        if add:
            await gangwar.kset(conn, f"pfund:{chat_id}", int(await gangwar.kget(conn, f"pfund:{chat_id}") or 0) + add)
        await gangwar.kset(conn, f"porder:{chat_id}", min(100, int(await gangwar.kget(conn, f"porder:{chat_id}") or 40) + 1))
        if gangs and random.random() < 0.45:
            cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner != 0", (chat_id,))
            keys = [r[0] for r in await cur.fetchall()]
            if keys:
                key = random.choice(keys)
                await conn.execute("UPDATE territory SET police = MIN(100, police + 10), control = MAX(0, control - 5) WHERE chat_id = ? AND key = ?", (chat_id, key))
                if random.random() < 0.35:
                    await gangwar.say_ai(conn, chat_id, f"🎩 Мэр Архип объявил рейд: полиция давит район {gangwar.TERR[key][1]} <b>{gangwar.TERR[key][2]}</b>.")
    # Крёстный отец: заём и долг.
    god = await _get(conn, f"god:{chat_id}")
    if player_god:
        return
    if int(god.get("suppressed", 0)) > now():
        return  # мэр провёл облаву на семью: ИИ-Крёстный 6 часов не требует дань и не давит
    debt = god.get("debt") or {}
    if debt and debt.get("due", 0) < now():
        gid = int(debt.get("gid") or 0)
        cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND id = ?", (chat_id, gid))
        row = await cur.fetchone()
        if row:
            take = int((row[2] or 0) * 0.15)
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (take, row[0]))
            await gangwar.say_ai(conn, chat_id, f"👑 Крёстный отец не дождался долга: «{row[1]}» потеряла {take} ₽. Следующий раз будет война.")
            import world
            await world.on_default(conn, chat_id, row[0])
        god.pop("debt", None)
        await _set(conn, f"god:{chat_id}", god)
    if god.get("insult") and random.random() < 0.5:
        cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 0 ORDER BY treasury DESC LIMIT 1", (chat_id,))
        row = await cur.fetchone()
        if row:
            take = int((row[2] or 0) * 0.10)
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (take, row[0]))
            await gangwar.say_ai(conn, chat_id, f"👑 Крёстный отец напомнил о неуважении: «{row[1]}» потеряла {take} ₽.")
        god.pop("insult", None)
        await _set(conn, f"god:{chat_id}", god)
    if not god.get("debt") and not god.get("offer") and not god.get("tribute") and random.random() < 0.35:
        cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 0 ORDER BY treasury DESC LIMIT 1", (chat_id,))
        row = await cur.fetchone()
        if row and (row[2] or 0) > 1000:
            god["tribute"] = {"amount": min(3000, int((row[2] or 0) * 0.15)), "expires": now() + 6 * 3600, "gid": row[0]}
            await _set(conn, f"god:{chat_id}", god)
            await gangwar.say_ai(conn, chat_id, f"👑 Крёстный отец требует дань с «{row[1]}»: {god['tribute']['amount']} ₽ за 6 часов.")
    if not god.get("debt") and not god.get("offer") and not god.get("tribute") and random.random() < 0.45:
        god["offer"] = {"amount": 4000, "owed": 4800, "expires": now() + 3600}
        await _set(conn, f"god:{chat_id}", god)
