"""Жители районов: игроки вне банд и полиции живут в районе и поддерживают банду-хозяина или полицию.

kv `res:{chat}` — JSON {uid: {home, moved, ts, side}}; kv `rgift:{chat}:{key}` — JSON {side, until, by}.
Поддержка двигает контроль/давление сразу и ещё сутки «настроением» (tick раз в 30 минут).
Банда (босс/зам) и мэрия (мэр/офицеры) могут задобрить жителей раздачей: деньги жителям и усиленная поддержка 12 часов.
"""
import json
import logging
import time

import gang as G
import gangwar
import police
from rooms import GameError

log = logging.getLogger(__name__)

SUPPORT_CD = 4 * 3600
MOVE_CD = 24 * 3600
REWARD = 80
GIFT_COST = 400
GIFT_HOURS = 12
GIFT_SHARE_CAP = 150
MOOD_HOURS = 24
TICK_GAP = 30 * 60


def now() -> int:
    return int(time.time())


async def _j(conn, key: str) -> dict:
    raw = await gangwar.kget(conn, key)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


async def _sj(conn, key: str, data: dict) -> None:
    await gangwar.kset(conn, key, json.dumps(data, ensure_ascii=False))


async def _status(conn, chat_id: int, uid: int) -> tuple:
    """(житель ли, причина, банда, роль в банде, роль в полиции)."""
    g, members = await G._mine(conn, chat_id, uid)
    grole = next((m["role"] for m in members if m["user_id"] == uid), "") if g else ""
    prole = await police._role(conn, chat_id, uid)
    if g:
        return False, "Ты в ОПГ и влияешь на районы через банду. Жители — это те, кто вне банд и полиции.", g, grole, prole
    if prole:
        return False, "Ты в полиции и давишь на районы операциями. Жители — это те, кто вне банд и полиции.", g, grole, prole
    return True, "", g, grole, prole


async def _owner(conn, chat_id: int, key: str) -> int:
    row = await (await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))).fetchone()
    return int(row[0]) if row and row[0] else 0


async def _gift(conn, chat_id: int, key: str) -> dict:
    g = await _j(conn, f"rgift:{chat_id}:{key}")
    return g if g and int(g.get("until", 0)) > now() else {}


def _tname(key: str) -> str:
    t = gangwar.TERR[key]
    return f"{t[1]} {t[2]}"


async def settle(chat_id: int, uid: int, name: str, key: str) -> dict:
    import gangplus3 as P3
    import rp

    async def run(conn):
        ok, why, *_ = await _status(conn, chat_id, uid)
        if not ok:
            raise GameError(why)
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        data = await _j(conn, f"res:{chat_id}")
        rec = data.get(str(uid)) or {}
        if rec.get("home") == key:
            raise GameError("Ты и так здесь живёшь")
        if rec.get("home") and int(rec.get("moved", 0)) + MOVE_CD > now():
            raise GameError(f"Переезжать можно раз в сутки: ещё {max(1, (int(rec['moved']) + MOVE_CD - now()) // 60)} мин")
        first = not rec.get("home")
        rec.update({"home": key, "moved": now()})
        data[str(uid)] = rec
        await _sj(conn, f"res:{chat_id}", data)
        await rp._news(conn, chat_id, f"🏘 {name} {'поселился' if first else 'переехал'} в район {_tname(key)}")
        return P3._res(f"🏘 Теперь ты живёшь в районе {_tname(key)}. Раз в 4 часа можно поддержать банду-хозяина или полицию.", True, 0)
    return await P3._act(chat_id, uid, run)


async def support(chat_id: int, uid: int, name: str, side: str) -> dict:
    import gangplus3 as P3
    import rp

    async def run(conn):
        ok, why, *_ = await _status(conn, chat_id, uid)
        if not ok:
            raise GameError(why)
        if side not in ("gang", "police"):
            raise GameError("Выбери сторону: банда или полиция")
        data = await _j(conn, f"res:{chat_id}")
        rec = data.get(str(uid)) or {}
        key = rec.get("home")
        if not key or key not in gangwar.TERR:
            raise GameError("Сначала выбери район, где живёшь")
        if int(rec.get("ts", 0)) + SUPPORT_CD > now():
            raise GameError(f"Ты уже высказался: снова через {max(1, (int(rec['ts']) + SUPPORT_CD - now()) // 60)} мин")
        owner = await _owner(conn, chat_id, key)
        if not owner:
            raise GameError("Район свободен: поддерживать некого и давить не на кого")
        g = await gangwar._gang_row(conn, owner)
        gift = await _gift(conn, chat_id, key)
        boosted = bool(gift and gift.get("side") == side)
        step = 6 if boosted else 4
        pay = REWARD * (2 if boosted else 1)
        if side == "gang":
            if int(g["treasury"] or 0) >= pay:
                await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (pay, owner))
            else:
                pay = 0
            await conn.execute("UPDATE territory SET control = MIN(100, control + ?), police = MAX(0, police - 3) WHERE chat_id = ? AND key = ?", (step, chat_id, key))
            text = f"🏘 Ты поддержал «{g['name']}» в районе {_tname(key)}: контроль +{step}, давление полиции −3."
            news = f"🏘 Житель {name} поддержал «{g['name']}» в районе {_tname(key)}: контроль +{step}"
        else:
            if await police.fund(conn, chat_id) >= pay:
                await police.fund_add(conn, chat_id, -pay)
            else:
                pay = 0
            await conn.execute("UPDATE territory SET police = MIN(100, police + ?), control = MAX(0, control - 3) WHERE chat_id = ? AND key = ?", (step, chat_id, key))
            await police.case_add(conn, chat_id, owner, 1)
            text = f"🏘 Ты помог полиции в районе {_tname(key)}: давление +{step}, контроль «{g['name']}» −3, дело +1."
            news = f"🏘 Житель {name} помог полиции в районе {_tname(key)}: давление +{step}"
        if pay:
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (pay, chat_id, uid))
            text += f" Благодарность: +{pay} ₽."
        rec.update({"ts": now(), "side": side})
        data[str(uid)] = rec
        await _sj(conn, f"res:{chat_id}", data)
        await rp._news(conn, chat_id, news)
        return P3._res(text, True, pay)
    return await P3._act(chat_id, uid, run)


async def _gift_side(conn, chat_id: int, uid: int) -> tuple:
    """Кто может задабривать жителей: ('gang', gid) для босса/зама, ('police', 0) для мэра и офицеров."""
    import power
    _ok, _why, g, grole, prole = await _status(conn, chat_id, uid)
    if g and not g.get("is_ai") and grole in ("boss", "deputy"):
        return "gang", int(g["id"])
    m = await power._mayor(conn, chat_id)
    if prole or (m and int(m.get("uid", 0)) == uid):
        return "police", 0
    return "", 0


async def gift(chat_id: int, uid: int, name: str, key: str) -> dict:
    import gangplus3 as P3
    import rp

    async def run(conn):
        side, gid = await _gift_side(conn, chat_id, uid)
        if not side:
            raise GameError("Задабривать жителей могут босс или зам банды, мэр и офицеры полиции")
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        owner = await _owner(conn, chat_id, key)
        if not owner:
            raise GameError("Район свободен: бороться за жителей не с кем")
        if side == "gang" and owner != gid:
            raise GameError("Банда раздаёт подарки только в своих районах")
        if await _gift(conn, chat_id, key):
            raise GameError("В этом районе раздача уже идёт: дождись, пока закончится")
        data = await _j(conn, f"res:{chat_id}")
        people = [int(u) for u, r in data.items() if r.get("home") == key]
        if not people:
            raise GameError("В районе пока никто не живёт: раздавать некому")
        if side == "gang":
            g = await gangwar._gang_row(conn, gid)
            if int(g["treasury"] or 0) < GIFT_COST:
                raise GameError(f"Нужно {GIFT_COST} ₽ в общаке")
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (GIFT_COST, gid))
            who = f"«{g['name']}»"
        else:
            if await police.fund(conn, chat_id) < GIFT_COST:
                raise GameError(f"Нужно {GIFT_COST} ₽ в фонде полиции")
            await police.fund_add(conn, chat_id, -GIFT_COST)
            who = "Мэрия"
        share = min(GIFT_SHARE_CAP, GIFT_COST // len(people))  # остальное уходит на праздник двора
        for u in people:
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (share, chat_id, u))
        await _sj(conn, f"rgift:{chat_id}:{key}", {"side": side, "until": now() + GIFT_HOURS * 3600, "by": uid})
        await rp._news(conn, chat_id, f"🎁 {who} задабривает жителей района {_tname(key)}: по {share} ₽ каждому, {GIFT_HOURS} ч их поддержка {'банды' if side == 'gang' else 'полиции'} сильнее")
        return P3._res(f"🎁 Жители района {_tname(key)} получили по {share} ₽. {GIFT_HOURS} часов их поддержка твоей стороны даёт +6 вместо +4, а им платят вдвое.", True, -GIFT_COST)
    return await P3._act(chat_id, uid, run)


async def tick(conn) -> None:
    """Настроение жителей: кто кого поддержал за последние сутки, тот и тянет район каждые 30 минут."""
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM territory")
        for (chat_id,) in await cur.fetchall():
            t = now()
            if t - int(await gangwar.kget(conn, f"restick:{chat_id}") or 0) < TICK_GAP:
                continue
            await gangwar.kset(conn, f"restick:{chat_id}", str(t))
            mood = {}
            for _u, r in (await _j(conn, f"res:{chat_id}")).items():
                if r.get("home") and int(r.get("ts", 0)) > t - MOOD_HOURS * 3600:
                    mood[r["home"]] = mood.get(r["home"], 0) + (1 if r.get("side") == "gang" else -1)
            for key, net in mood.items():
                net = max(-2, min(2, net))
                if net > 0:
                    await conn.execute("UPDATE territory SET control = MIN(100, control + ?) WHERE chat_id = ? AND key = ? AND owner != 0", (net, chat_id, key))
                elif net < 0:
                    await conn.execute("UPDATE territory SET police = MIN(100, police + ?) WHERE chat_id = ? AND key = ? AND owner != 0", (-net, chat_id, key))
    except Exception:
        log.exception("Сбой настроения жителей")


async def state(conn, chat_id: int, uid: int) -> dict:
    t = now()
    ok, why, _g, _gr, _pr = await _status(conn, chat_id, uid)
    data = await _j(conn, f"res:{chat_id}")
    rec = data.get(str(uid)) or {}
    counts = {}
    for _u, r in data.items():
        if r.get("home"):
            counts[r["home"]] = counts.get(r["home"], 0) + 1
    names = {r[0]: r[1] for r in await (await conn.execute("SELECT id, name FROM gang WHERE chat_id = ?", (chat_id,))).fetchall()}
    rows = {r[0]: r for r in await (await conn.execute("SELECT key, owner, control, police FROM territory WHERE chat_id = ?", (chat_id,))).fetchall()}
    side, gid = await _gift_side(conn, chat_id, uid)
    dist, gift_keys = [], []
    for key, tinfo in gangwar.TERR.items():
        _k, owner, control, pol = rows.get(key, (key, 0, 0, 0))
        g = await _gift(conn, chat_id, key)
        dist.append({"key": key, "icon": tinfo[1], "name": tinfo[2], "owner": int(owner or 0), "owner_name": names.get(owner, ""),
                     "control": int(control or 0), "police": int(pol or 0), "residents": counts.get(key, 0),
                     "gift": {"side": g["side"], "left": int(g["until"]) - t} if g else None})
        if side and owner and not g and counts.get(key, 0) and (side == "police" or owner == gid):
            gift_keys.append(key)
    return {"eligible": ok, "why": why, "home": rec.get("home", "") if ok else "", "last_side": rec.get("side", ""),
            "wait": max(0, int(rec.get("ts", 0)) + SUPPORT_CD - t) if ok else 0,
            "move_wait": max(0, int(rec.get("moved", 0)) + MOVE_CD - t) if ok and rec.get("home") else 0,
            "reward": REWARD, "districts": dist, "gift_side": side, "gift_keys": gift_keys, "gift_cost": GIFT_COST, "gift_hours": GIFT_HOURS,
            "total": sum(counts.values())}
