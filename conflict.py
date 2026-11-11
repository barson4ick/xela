"""Конфликты района: блиц-захват, налёт на точку, караваны и контрабанда, засады и блокпосты полиции, чёрный рынок,
внедрение, спецоперация, стычки банды и полиции, сделки полиции и ОПГ (зачистка конкурента, слив), возрождение выбывших ИИ-банд."""
import json
import logging
import random
import time

import aiosqlite

import gangplus3 as P3
import gangplus5 as P5
import gangplus8 as P8
import gangwar
import rp
from rooms import GameError
from utils import esc, now_local
from world import _beat, _fund_add, _i, _is, _j, _jail, _js, _mode, _my_gang, _name, _need_dep, _say

log = logging.getLogger("conflict")


def now() -> int:
    return int(time.time())


def tname(key: str) -> str:
    t = gangwar.TERR[key]
    return f"{t[1]} {t[2]}"


async def _energy(conn, chat_id: int, uid: int, cost: int) -> None:
    p = await rp._load(conn, chat_id, uid)
    rp._need(p, cost)
    p["energy"] -= cost
    await rp._save(conn, chat_id, uid, p)


async def _cop(conn, chat_id: int, uid: int) -> str:
    import police
    return await police._role(conn, chat_id, uid)


async def _need_cop(conn, chat_id: int, uid: int, chief: bool = False) -> str:
    role = await _cop(conn, chat_id, uid)
    if not role or (chief and role != "chief"):
        raise GameError("Это может только капитан полиции" if chief else "Ты не служишь в полиции")
    return role


async def _cop_rep(conn, chat_id: int, uid: int, delta: int) -> None:
    await conn.execute("UPDATE rp_player SET cop_rep = MAX(0, cop_rep + ?) WHERE chat_id = ? AND user_id = ?", (delta, chat_id, uid))


async def _terr_owner(conn, chat_id: int, key: str):
    cur = await conn.execute("SELECT owner, control FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
    r = await cur.fetchone()
    return (r[0], r[1]) if r else (None, 0)


async def _gname(conn, gid: int) -> str:
    g = await gangwar._gang_row(conn, gid)
    return g["name"] if g else "?"


async def _is_member(conn, gid: int, uid: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM gang_member WHERE gang_id = ? AND user_id = ?", (gid, uid))
    return bool(await cur.fetchone())


_ready = False


async def _ensure(conn) -> None:
    global _ready
    if _ready:
        return
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS convoy (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gid INTEGER NOT NULL, kind TEXT NOT NULL,
           size TEXT NOT NULL, cargo INTEGER NOT NULL, escort INTEGER NOT NULL DEFAULT 0, bribed INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL,
           arrives INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'way', tried TEXT NOT NULL DEFAULT '[]', result TEXT NOT NULL DEFAULT '')""")
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS deal (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, kind TEXT NOT NULL, gid INTEGER NOT NULL,
           target INTEGER NOT NULL, price INTEGER NOT NULL, by_uid INTEGER NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open')""")
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS lot (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gid INTEGER NOT NULL, value INTEGER NOT NULL,
           created INTEGER NOT NULL, expires INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '')""")
    _ready = True


# ═════════════════════ 1. Блиц-захват района ═════════════════════
BLITZ_MIN, BLITZ_COST, BLITZ_CD, BLITZ_ENERGY = 10 * 60, 500, 3600, 5


async def blitz_start(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        await _ensure(conn)
        g, role = await _need_dep(conn, chat_id, uid)
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        owner, control = await _terr_owner(conn, chat_id, key)
        if not owner or owner == g["id"]:
            raise GameError("Блиц-захват только на район, который держит другая банда")
        if (await _j(conn, f"blitz:{chat_id}:{key}")).get("until", 0) > now():
            raise GameError("На этом районе уже идёт схватка")
        if await _i(conn, f"bzcd:{g['id']}") > now():
            raise GameError("Банда перевела дух только после прошлого захвата: подожди час")
        if (g["treasury"] or 0) < BLITZ_COST:
            raise GameError(f"В общаке нужно {BLITZ_COST} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (BLITZ_COST, g["id"]))
        await _is(conn, f"bzcd:{g['id']}", now() + BLITZ_CD)
        og = await gangwar._gang_row(conn, owner)
        need = 5 + int(control) // 20 + (2 if og and og["is_ai"] else 0)
        reinf = random.randint(1, 3) if og and og["is_ai"] else 0
        await _js(conn, f"blitz:{chat_id}:{key}", {"att": g["id"], "attn": g["name"], "def": owner, "defn": og["name"] if og else "?", "until": now() + BLITZ_MIN, "need": need, "prog": 0, "reinf": reinf, "hold": {}, "cut": 0})
        await _say(conn, chat_id, f"⚡ <b>Блиц-захват!</b> «{esc(g['name'])}» штурмует район {tname(key)} (хозяева «{esc(og['name'] if og else '?')}»). 10 минут: нападающим нужно набрать {need} очков (мини-апп → Район → Операции). Хозяева могут подтянуть подкрепление, полиция разогнать.")
        import world
        await world.police_react(conn, chat_id, [g["id"], owner], "блиц-захват", 0.35)
        return P3._res(f"⚡ Блиц-захват начат: набери {need} очков за 10 минут. Участники банды жмут «Держать».", True, -BLITZ_COST)
    return await P3._act(chat_id, uid, run)


async def _blitz_side(conn, chat_id, uid, key, side):
    b = await _j(conn, f"blitz:{chat_id}:{key}")
    if b.get("until", 0) <= now():
        raise GameError("Схватки на этом районе нет")
    if not await _is_member(conn, b[side], uid):
        raise GameError("Это не твоя сторона")
    return b


async def _blitz_check(conn, chat_id, key, b) -> bool:
    if b["prog"] - b["reinf"] - b.get("cut", 0) >= b["need"]:
        await _blitz_finish(conn, chat_id, key, b, True)
        return True
    return False


async def blitz_hold(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        b = await _blitz_side(conn, chat_id, uid, key, "att")
        if b["hold"].get(str(uid), 0) >= 3:
            raise GameError("Больше трёх рывков на человека нельзя")
        await _energy(conn, chat_id, uid, BLITZ_ENERGY)
        b["hold"][str(uid)] = b["hold"].get(str(uid), 0) + 1
        b["prog"] += 1
        if await _blitz_check(conn, chat_id, key, b):
            return P3._res("⚡ Район захвачен!", True, 0)
        await _js(conn, f"blitz:{chat_id}:{key}", b)
        return P3._res(f"⚡ Держим район: {b['prog'] - b['reinf'] - b.get('cut', 0)} из {b['need']} очков.", True, 0)
    return await P3._act(chat_id, uid, run)


async def blitz_reinforce(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        b = await _blitz_side(conn, chat_id, uid, key, "def")
        h = b.setdefault("rhold", {})
        if h.get(str(uid), 0) >= 3:
            raise GameError("Больше трёх раз на человека нельзя")
        await _energy(conn, chat_id, uid, BLITZ_ENERGY)
        h[str(uid)] = h.get(str(uid), 0) + 1
        b["reinf"] += 1
        await _js(conn, f"blitz:{chat_id}:{key}", b)
        return P3._res("🛡 Подкрепление подошло: нападающим труднее.", True, 0)
    return await P3._act(chat_id, uid, run)


async def blitz_disperse(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        import police
        role = await _need_cop(conn, chat_id, uid)
        b = await _j(conn, f"blitz:{chat_id}:{key}")
        if b.get("until", 0) <= now():
            raise GameError("Схватки на этом районе нет")
        pc = b.setdefault("police", {})
        if str(uid) in pc:
            raise GameError("Ты уже разгонял эту схватку")
        await _energy(conn, chat_id, uid, 10)
        pc[str(uid)] = 1
        b["cut"] = b.get("cut", 0) + (4 if role == "chief" else 2)
        await _cop_rep(conn, chat_id, uid, 1)
        await _js(conn, f"blitz:{chat_id}:{key}", b)
        g = await gangwar._gang_row(conn, b["att"])
        if g and not g["is_ai"]:
            await police.case_add(conn, chat_id, g["id"], 2)
        return P3._res(f"🚓 Схватку разгоняют: очки нападающих −{4 if role == 'chief' else 2}.", True, 0)
    return await P3._act(chat_id, uid, run)


async def _blitz_finish(conn, chat_id: int, key: str, b: dict, win: bool) -> None:
    import gangplus5 as P5_
    await _js(conn, f"blitz:{chat_id}:{key}", {})
    if win:
        await conn.execute("UPDATE territory SET owner = ?, control = 40, last_pay = ? WHERE chat_id = ? AND key = ?", (b["att"], now(), chat_id, key))
        await P5_.rep_add(conn, b["att"], 2, 2)
        await P5_.rep_add(conn, b["def"], -3, 0)
        await _say(conn, chat_id, f"⚡ <b>«{esc(b['attn'])}» взяла район {tname(key)}</b> блицем у «{esc(b['defn'])}»!")
        await rp._news(conn, chat_id, f"⚡ «{b['attn']}» блицем взяла {tname(key)}")
    else:
        await P5_.rep_add(conn, b["att"], -2, 0)
        await _say(conn, chat_id, f"🛡 Блиц-захват {tname(key)} провалился: «{esc(b['defn'])}» устояла.")


async def _blitz_tick(conn, chat_id: int) -> None:
    for key in gangwar.TERR:
        b = await _j(conn, f"blitz:{chat_id}:{key}")
        if b and b.get("until", 0) and b["until"] <= now():
            win = b["prog"] - b["reinf"] - b.get("cut", 0) >= b["need"]
            await _blitz_finish(conn, chat_id, key, b, win)


# ═════════════════════ 2. Налёт на точку ═════════════════════
RAID_COST, RAID_CD, RAID_ENERGY, RAID_HOURS = 400, 2 * 3600, 10, 3


async def point_raid(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        g, role = await _need_dep(conn, chat_id, uid)
        if key not in P8.POINTS:
            raise GameError("Такой точки нет")
        cur = await conn.execute("SELECT owner FROM racket WHERE chat_id = ? AND key = ?", (chat_id, key))
        r = await cur.fetchone()
        if not r or not r[0] or r[0] == g["id"]:
            raise GameError("Налёт только на точку, которую держит другая банда")
        if (await _j(conn, f"rtemp:{chat_id}:{key}")).get("until", 0) > now():
            raise GameError("Точку уже отжали")
        if await _i(conn, f"prcd:{g['id']}") > now():
            raise GameError("Банда отдыхает после налёта (2 часа)")
        if (g["treasury"] or 0) < RAID_COST:
            raise GameError(f"В общаке нужно {RAID_COST} ₽")
        await _energy(conn, chat_id, uid, RAID_ENERGY)
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (RAID_COST, g["id"]))
        await _is(conn, f"prcd:{g['id']}", now() + RAID_CD)
        ap, dp = await P8._power(conn, g["id"]), await P8._power(conn, r[0])
        chance = max(0.2, min(0.75, 0.45 + 0.05 * (ap - dp)))
        ic, title, _t = P8.POINTS[key]
        og = await _gname(conn, r[0])
        if random.random() < chance:
            await conn.execute("UPDATE racket SET owner = ?, pending = 0, last_ts = ?, rebel_until = 0 WHERE chat_id = ? AND key = ?", (g["id"], now(), chat_id, key))
            await _js(conn, f"rtemp:{chat_id}:{key}", {"orig": r[0], "thief": g["id"], "until": now() + RAID_HOURS * 3600})
            await P5.rep_add(conn, g["id"], 1, 1)
            await P5.rep_add(conn, r[0], -2, 0)
            await _say(conn, chat_id, f"{ic} <b>«{esc(g['name'])}» отжала точку «{title}»</b> у «{esc(og)}» на {RAID_HOURS} часа: дань и налог идут ей.")
            return P3._res(f"{ic} Налёт удался (шанс ~{int(chance * 100)}%): точка твоя на {RAID_HOURS} часа.", True, -RAID_COST)
        await _beat(conn, chat_id, uid, 15)
        await P5.rep_add(conn, g["id"], -2, 0)
        await _say(conn, chat_id, f"{ic} Налёт «{esc(g['name'])}» на точку «{title}» провалился: охрана «{esc(og)}» отбилась.")
        return P3._res(f"{ic} Налёт провалился (шанс был ~{int(chance * 100)}%): ты лежишь 15 минут, уважение −2.", False, -RAID_COST)
    return await P3._act(chat_id, uid, run)


async def _rtemp_tick(conn, chat_id: int) -> None:
    for key in P8.POINTS:
        d = await _j(conn, f"rtemp:{chat_id}:{key}")
        if d and d.get("until", 0) <= now():
            await _js(conn, f"rtemp:{chat_id}:{key}", {})
            cur = await conn.execute("SELECT owner FROM racket WHERE chat_id = ? AND key = ?", (chat_id, key))
            r = await cur.fetchone()
            if r and r[0] == d["thief"]:
                await conn.execute("UPDATE racket SET owner = ?, pending = 0, rebel_until = 0 WHERE chat_id = ? AND key = ?", (d["orig"], chat_id, key))
                await _say(conn, chat_id, f"{P8.POINTS[key][0]} Точка «{P8.POINTS[key][1]}» вернулась к «{esc(await _gname(conn, d['orig']))}»: налёт закончился.")


# ═════════════════════ 3–7. Караваны, контрабанда, засады, блокпост, конфискат ═════════════════════
SIZES = {"s": (1000, 20, 1.15, "малый"), "m": (3000, 30, 1.25, "средний"), "l": (6000, 40, 1.40, "большой")}
SMUGGLE_BONUS = 0.35
ESCORT_COST = 300
AMBUSH_ENERGY = 10


async def convoy_send(chat_id: int, uid: int, name: str, kind: str, size: str, escort=0, bribe=0) -> dict:
    escort, bribe = int(bool(escort)), int(bool(bribe))

    async def run(conn):
        await _ensure(conn)
        g, role = await _need_dep(conn, chat_id, uid)
        if kind not in ("legal", "smuggle") or size not in SIZES:
            raise GameError("Выбери вид и размер каравана")
        cur = await conn.execute("SELECT COUNT(*) FROM convoy WHERE chat_id = ? AND gid = ? AND status = 'way'", (chat_id, g["id"]))
        if (await cur.fetchone())[0] >= 1:
            raise GameError("Один караван уже в пути")
        cargo, minutes, mult, label = SIZES[size]
        cost = cargo + (ESCORT_COST if escort else 0) + (cargo // 10 if bribe else 0)
        if (g["treasury"] or 0) < cost:
            raise GameError(f"В общаке нужно {cost} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, g["id"]))
        if bribe:
            await _fund_add(conn, chat_id, cargo // 10)
        t = now()
        await conn.execute("INSERT INTO convoy (chat_id, gid, kind, size, cargo, escort, bribed, sent, arrives) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (chat_id, g["id"], kind, size, cargo, escort, bribe, t, t + minutes * 60))
        tag = "🕶 серый" if kind == "smuggle" else "🚛"
        await _say(conn, chat_id, f"{tag} «{esc(g['name'])}» отправила {label} караван, в пути {minutes} минут. Перехватить можно засадой (мини-апп → Операции).")
        return P3._res(f"{tag} Караван ушёл: ждём {minutes} минут. Прибыль при успехе ×{mult + (SMUGGLE_BONUS if kind == 'smuggle' else 0):.2f} от груза.", True, -cost)
    return await P3._act(chat_id, uid, run)


async def _make_lot(conn, chat_id: int, gid: int, value: int, note: str) -> int:
    cur = await conn.execute("INSERT INTO lot (chat_id, gid, value, created, expires, note) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, gid, value, now(), now() + 3 * 3600, note))
    return cur.lastrowid


async def convoy_ambush(chat_id: int, uid: int, name: str, cid) -> dict:
    cid = int(cid)

    async def run(conn):
        import police
        await _ensure(conn)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM convoy WHERE id = ? AND chat_id = ?", (cid, chat_id))
        r = await cur.fetchone()
        c = dict(r) if r else None
        if not c or c["status"] != "way" or c["arrives"] <= now():
            raise GameError("Этот караван уже прошёл")
        g, role = await _my_gang(conn, chat_id, uid)
        cop = await _cop(conn, chat_id, uid)
        if not g and not cop:
            raise GameError("Засаду ставят банда или полиция")
        if g and g["id"] == c["gid"]:
            raise GameError("Свой караван перехватывать нельзя")
        tried = json.loads(c["tried"])
        if uid in tried:
            raise GameError("Ты уже пробовал перехватить этот караван")
        await _energy(conn, chat_id, uid, AMBUSH_ENERGY)
        tried.append(uid)
        chance = 0.30 - (0.15 if c["escort"] else 0) + (0.10 if (cop and c["kind"] == "smuggle") else 0)
        ok = random.random() < chance
        gn = await _gname(conn, c["gid"])
        if ok:
            if cop and not g:
                lot = await _make_lot(conn, chat_id, c["gid"], c["cargo"], "засада")
                await police.case_add(conn, chat_id, c["gid"], 4 + (4 if c["kind"] == "smuggle" else 0))
                await _cop_rep(conn, chat_id, uid, 3)
                await conn.execute("UPDATE convoy SET status = 'seized', tried = ?, result = 'изъят полицией' WHERE id = ?", (json.dumps(tried), cid))
                await _say(conn, chat_id, f"🚓 Засада сработала: полиция изъяла караван «{esc(gn)}» на {c['cargo']} ₽. Груз на разборе (аукцион или выкуп).")
                return P3._res(f"🚓 Засада сработала: груз изъят (лот #{lot}), репутация +3.", True, 0)
            loot = c["cargo"] // 2
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (loot, g["id"]))
            await P5.rep_add(conn, c["gid"], -2, 0)
            await P5.rep_add(conn, g["id"], 1, 2)
            await conn.execute("UPDATE convoy SET status = 'seized', tried = ?, result = ? WHERE id = ?", (json.dumps(tried), f"перехвачен бандой «{g['name']}»", cid))
            await _say(conn, chat_id, f"🕴 Засада удалась: «{esc(g['name'])}» перехватила караван «{esc(gn)}» и забрала {loot} ₽.")
            return P3._res(f"🕴 Караван перехвачен: +{loot} ₽ в общак.", True, 0)
        await conn.execute("UPDATE convoy SET tried = ? WHERE id = ?", (json.dumps(tried), cid))
        if cop and not g:
            await _cop_rep(conn, chat_id, uid, -1)
            return P3._res("🚓 Засада впустую: караван проскочил (репутация −1).", False, 0)
        await _beat(conn, chat_id, uid, 10)
        return P3._res("🕴 Засада провалилась: охрана отбилась, ты лежишь 10 минут.", False, 0)
    return await P3._act(chat_id, uid, run)


ROADPOST_COST, ROADPOST_HOURS, ROADPOST_CD = 150, 1, 900


async def roadpost(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        import police
        await _need_cop(conn, chat_id, uid)
        if (await _j(conn, f"roadpost:{chat_id}")).get("until", 0) > now():
            raise GameError("Пост на дороге уже стоит")
        if await _i(conn, f"rpcd:{chat_id}") > now():
            raise GameError("Пост можно ставить раз в полчаса")
        if await police.fund(conn, chat_id) < ROADPOST_COST:
            raise GameError(f"В фонде нужно {ROADPOST_COST} ₽")
        await _fund_add(conn, chat_id, -ROADPOST_COST)
        await _is(conn, f"rpcd:{chat_id}", now() + ROADPOST_CD)
        await _js(conn, f"roadpost:{chat_id}", {"until": now() + ROADPOST_HOURS * 3600, "by": uid})
        await _say(conn, chat_id, f"🚧 <b>Полиция выставила пост на дороге</b> на {ROADPOST_HOURS} час: караваны проверяют. Можно заплатить за проезд (10% груза при отправке).")
        return P3._res(f"🚧 Пост выставлен на час. Караваны без взятки попадают под проверку.", True, -ROADPOST_COST)
    return await P3._act(chat_id, uid, run)


async def _convoy_tick(conn, chat_id: int) -> None:
    import police
    await _ensure(conn)
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM convoy WHERE chat_id = ? AND status = 'way' AND arrives <= ?", (chat_id, now()))
    mode = await _mode(conn, chat_id)
    for c in [dict(r) for r in await cur.fetchall()]:
        g = await gangwar._gang_row(conn, c["gid"])
        if not g:
            await conn.execute("UPDATE convoy SET status = 'lost' WHERE id = ?", (c["id"],))
            continue
        smug = c["kind"] == "smuggle"
        half = 0.5 if c["escort"] else 1.0
        rpst = await _j(conn, f"roadpost:{chat_id}")
        post = rpst.get("until", 0) > c["arrives"] - 3600 and rpst.get("until", 0) >= c["sent"]
        seized = None
        if post and not c["bribed"] and random.random() < (0.7 if smug else 0.35) * half:
            seized = "пост на дороге"
        elif mode != "off" and random.random() < (0.25 if smug else 0.10) * half:
            seized = "ИИ-патруль"
        if seized:
            lot = await _make_lot(conn, chat_id, c["gid"], c["cargo"], seized)
            await police.case_add(conn, chat_id, c["gid"], 4 + (4 if smug else 0))
            await conn.execute("UPDATE convoy SET status = 'seized', result = ? WHERE id = ?", (f"изъят: {seized}", c["id"]))
            await _say(conn, chat_id, f"🚓 Караван «{esc(g['name'])}» остановили ({seized}): груз на {c['cargo']} ₽ изъят. Лот #{lot} можно выкупить за 50% или он уйдёт на аукцион.")
            continue
        if random.random() < 0.12 * half:
            cur2 = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 1 ORDER BY RANDOM() LIMIT 1", (chat_id,))
            ai = await cur2.fetchone()
            if ai:
                loot = c["cargo"] // 2
                await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (loot, ai[0]))
                await conn.execute("UPDATE convoy SET status = 'seized', result = ? WHERE id = ?", (f"перехвачен «{ai[1]}»", c["id"]))
                await _say(conn, chat_id, f"🕴 Караван «{esc(g['name'])}» перехватила ИИ-банда «{esc(ai[1])}»: ушло {loot} ₽ груза.")
                continue
        mult = SIZES[c["size"]][2] + (SMUGGLE_BONUS if smug else 0)
        payout = int(c["cargo"] * mult)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (payout, c["gid"]))
        await conn.execute("UPDATE convoy SET status = 'done', result = ? WHERE id = ?", (f"доставлен: {payout} ₽", c["id"]))
        import gangplus2 as G2
        cur3 = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id > 0 LIMIT 1", (c["gid"],))
        mem = await cur3.fetchone()
        if mem:
            await G2.log_event(conn, chat_id, mem[0], "trade", payout)
        if smug:
            await police.case_add(conn, chat_id, c["gid"], 2)
        await _say(conn, chat_id, f"🚛 Караван «{esc(g['name'])}» дошёл: в общак {payout} ₽ (прибыль {payout - c['cargo']} ₽).")
    # лоты, которые никто не выкупил и не продал
    cur = await conn.execute("SELECT * FROM lot WHERE chat_id = ? AND status = 'open' AND expires <= ?", (chat_id, now()))
    for l in [dict(r) for r in await cur.fetchall()]:
        await _fund_add(conn, chat_id, l["value"] // 2)
        await conn.execute("UPDATE lot SET status = 'sold' WHERE id = ?", (l["id"],))
        await _say(conn, chat_id, f"🧾 Конфискат на {l['value']} ₽ ушёл на аукцион сам: в фонд полиции {l['value'] // 2} ₽.")


async def lot_auction(chat_id: int, uid: int, name: str, lid) -> dict:
    lid = int(lid)

    async def run(conn):
        await _ensure(conn)
        await _need_cop(conn, chat_id, uid, chief=True)
        cur = await conn.execute("SELECT value FROM lot WHERE id = ? AND chat_id = ? AND status = 'open'", (lid, chat_id))
        r = await cur.fetchone()
        if not r:
            raise GameError("Такого лота нет")
        pay = int(r[0] * 0.7)
        await _fund_add(conn, chat_id, pay)
        await conn.execute("UPDATE lot SET status = 'sold' WHERE id = ?", (lid,))
        return P3._res(f"🔨 Конфискат продан: в фонд полиции {pay} ₽.", True, 0)
    return await P3._act(chat_id, uid, run)


async def lot_redeem(chat_id: int, uid: int, name: str, lid) -> dict:
    lid = int(lid)

    async def run(conn):
        await _ensure(conn)
        g, role = await _need_dep(conn, chat_id, uid)
        cur = await conn.execute("SELECT gid, value FROM lot WHERE id = ? AND chat_id = ? AND status = 'open'", (lid, chat_id))
        r = await cur.fetchone()
        if not r or r[0] != g["id"]:
            raise GameError("Это не ваш лот")
        fee = r[1] // 2
        if (g["treasury"] or 0) < fee:
            raise GameError(f"Нужно {fee} ₽ в общаке")
        await conn.execute("UPDATE gang SET treasury = treasury - ? + ? WHERE id = ?", (fee, r[1], g["id"]))
        await _fund_add(conn, chat_id, fee)
        await conn.execute("UPDATE lot SET status = 'redeemed' WHERE id = ?", (lid,))
        return P3._res(f"🧾 Груз выкуплен: заплатили {fee} ₽, вернули {r[1]} ₽ груза.", True, r[1] - fee)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ 5. Чёрный рынок ═════════════════════
BM_MEAN, BM_MIN = 3 * 3600, 15 * 60


async def _bm_start(conn, chat_id: int) -> None:
    if (await _j(conn, f"bm:{chat_id}")).get("ends", 0) > now():
        return
    res = random.choice(list(P5.ITEMS))
    qty = random.randint(4, 12)
    price = P5.ITEMS[res][2]
    mn = int(price * qty * 0.95)
    await _js(conn, f"bm:{chat_id}", {"res": res, "qty": qty, "min": mn, "best": 0, "gid": 0, "ends": now() + BM_MIN})
    ic, title, _p = P5.ITEMS[res]
    await _say(conn, chat_id, f"🕶 <b>Чёрный рынок:</b> лот {ic} {title} ×{qty}, старт {mn} ₽. Ставки 15 минут: боссы и замы банд (мини-апп → Район → Операции).")


async def bm_bid(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount)

    async def run(conn):
        g, role = await _need_dep(conn, chat_id, uid)
        bm = await _j(conn, f"bm:{chat_id}")
        if bm.get("ends", 0) <= now() or bm.get("done"):
            raise GameError("Лота сейчас нет")
        floor = bm["min"] if not bm["best"] else int(bm["best"] * 1.1) + 1
        if amount < floor:
            raise GameError(f"Минимальная ставка сейчас {floor} ₽")
        refund_same = bm["best"] if bm["gid"] == g["id"] else 0
        if (g["treasury"] or 0) + refund_same < amount:
            raise GameError(f"В общаке нужно {amount} ₽")
        if bm["gid"]:
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (bm["best"], bm["gid"]))
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (amount, g["id"]))
        bm["best"], bm["gid"], bm["gname"] = amount, g["id"], g["name"]
        await _js(conn, f"bm:{chat_id}", bm)
        return P3._res(f"🕶 Твоя ставка {amount} ₽ лидирует. Если перебьют, деньги вернутся.", True, -amount)
    return await P3._act(chat_id, uid, run)


async def _bm_tick(conn, chat_id: int) -> None:
    bm = await _j(conn, f"bm:{chat_id}")
    if not bm or bm.get("done") or bm.get("ends", 0) > now():
        return
    ic, title, _p = P5.ITEMS[bm["res"]]
    if bm["gid"]:
        await P5._stock_add(conn, bm["gid"], bm["res"], bm["qty"])
        await _say(conn, chat_id, f"🕶 Чёрный рынок закрыт: {ic} {title} ×{bm['qty']} уходит «{esc(bm.get('gname', '?'))}» за {bm['best']} ₽.")
    else:
        await _say(conn, chat_id, f"🕶 Чёрный рынок закрыт: лот {ic} {title} никто не купил.")
    bm["done"] = True
    await _js(conn, f"bm:{chat_id}", bm)


# ═════════════════════ 9. Внедрение ═════════════════════
INFIL_HOURS, INFIL_ENERGY, INFIL_CD = 24, 15, 6 * 3600


async def infiltrate(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)

    async def run(conn):
        await _need_cop(conn, chat_id, uid)
        g = await gangwar._gang_row(conn, gid)
        if not g or int(g["is_ai"] or 0) >= 2 or g["chat_id"] != chat_id:
            raise GameError("Такой банды нет")
        if (await _j(conn, f"infil:{chat_id}:{uid}")).get("until", 0) > now():
            raise GameError("Ты уже внедрён в банду")
        if await _i(conn, f"infcd:{chat_id}:{uid}") > now():
            raise GameError("После прошлого внедрения нужно отлежаться 6 часов")
        await _energy(conn, chat_id, uid, INFIL_ENERGY)
        await _is(conn, f"infcd:{chat_id}:{uid}", now() + INFIL_CD)
        await _js(conn, f"infil:{chat_id}:{uid}", {"gid": gid, "until": now() + INFIL_HOURS * 3600, "gname": g["name"]})
        return P3._res(f"🕵 Ты внедрён в «{g['name']}» на {INFIL_HOURS} часа: видишь её караваны и планы. Если вычислят, репутация −3.", True, 0)
    return await P3._act(chat_id, uid, run)


async def _infil_tick(conn, chat_id: int) -> None:
    import police
    import police2
    cur = await conn.execute("SELECT key, value FROM kv WHERE key LIKE ?", (f"infil:{chat_id}:%",))
    for key, raw in await cur.fetchall():
        d = json.loads(raw) if raw else {}
        if not d:
            continue
        uid = int(key.rsplit(":", 1)[1])
        if d.get("until", 0) > now() and now() - await _i(conn, f"infhr:{chat_id}:{uid}") >= 1800:
            await _is(conn, f"infhr:{chat_id}:{uid}", now())
            await police.case_add(conn, chat_id, d["gid"], 1.5)
            await police2.alert(conn, chat_id, d["gid"], "tip", f"🕵 Агент донёс на «{d['gname']}»: новые сведения в деле.")
        if d.get("until", 0) <= now():
            await conn.execute("UPDATE territory SET police = MIN(100, police + 6), control = MAX(0, control - 6) WHERE chat_id = ? AND key IN (SELECT key FROM territory WHERE chat_id = ? AND owner = ? ORDER BY RANDOM() LIMIT 2)", (chat_id, chat_id, d["gid"]))
            await police.case_add(conn, chat_id, d["gid"], 6)
            await _cop_rep(conn, chat_id, uid, 3)
            await police2.alert(conn, chat_id, d["gid"], "tip", f"🕵 Внедрённый офицер передал материалы на «{d['gname']}»: дело +6.")
            await _js(conn, key, {})
            await _say(conn, chat_id, f"🕵 Внедрённый офицер собрал материалы на «{esc(d['gname'])}»: дело +6.")
        elif await P3._roll(conn, f"infx:{chat_id}:{uid}", 20 * 3600):
            await _cop_rep(conn, chat_id, uid, -3)
            await conn.execute("UPDATE gang SET respect = MIN(100, respect + 1) WHERE id = ?", (d["gid"],))
            await _js(conn, key, {})
            await _say(conn, chat_id, f"🕵 В «{esc(d['gname'])}» вычислили внедрённого офицера полиции. Банда довольна собой, у полиции провал.")


# ═════════════════════ 10. Спецоперация по доносам ═════════════════════
SPECIAL_COST, SPECIAL_CD, SPECIAL_TIPS = 600, 6 * 3600, 3


async def _tip_count(conn, chat_id: int, gid: int) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM police_alert WHERE chat_id = ? AND gang_id = ? AND kind = 'tip' AND ts > ?", (chat_id, gid, now() - 86400))
    return (await cur.fetchone())[0]


async def special_op(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)

    async def run(conn):
        import police
        await _need_cop(conn, chat_id, uid, chief=True)
        g = await gangwar._gang_row(conn, gid)
        if not g or int(g["is_ai"] or 0) >= 2 or g["chat_id"] != chat_id:
            raise GameError("Такой банды нет")
        tips = await _tip_count(conn, chat_id, gid)
        if tips < SPECIAL_TIPS:
            raise GameError(f"Нужно минимум {SPECIAL_TIPS} доноса за сутки, сейчас {tips}")
        if await _i(conn, f"spcd:{chat_id}") > now():
            raise GameError("Спецоперации раз в 6 часов")
        if await police.fund(conn, chat_id) < SPECIAL_COST:
            raise GameError(f"В фонде нужно {SPECIAL_COST} ₽")
        await _fund_add(conn, chat_id, -SPECIAL_COST)
        await _is(conn, f"spcd:{chat_id}", now() + SPECIAL_CD)
        fine = min(2500, int((g["treasury"] or 0) * 0.15))
        if fine:
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (fine, gid))
            await _fund_add(conn, chat_id, fine)
        cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id > 0 ORDER BY RANDOM() LIMIT 2", (gid,))
        jailed = []
        for (u,) in await cur.fetchall():
            await _jail(conn, chat_id, u, 10)
            jailed.append(esc(await _name(conn, chat_id, u)))
        c = await police._case(conn, chat_id, gid)
        await police.case_add(conn, chat_id, gid, -int((c["pts"] or 0) * 0.6))
        await P5.rep_add(conn, gid, -3, 0)
        if g["is_ai"]:
            await conn.execute("UPDATE territory SET police = MIN(100, police + 8), control = MAX(0, control - 8) WHERE chat_id = ? AND owner = ?", (chat_id, gid))
        await _cop_rep(conn, chat_id, uid, 3)
        await _say(conn, chat_id, f"🚨 <b>Спецоперация по доносам:</b> «{esc(g['name'])}» — штраф {fine} ₽ в фонд полиции, задержаны на 10 минут: {', '.join(jailed) or 'никто'}. Дело закрыто на 60%, уважение банды −3.")
        return P3._res(f"🚨 Спецоперация проведена: штраф {fine} ₽ в фонд.", True, 0)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ 12. Стычка банды и полиции ═════════════════════
CLASH_MIN, CLASH_COST, CLASH_ENERGY, CLASH_CD = 5 * 60, 300, 5, 2 * 3600


async def clash_start(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        import police
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        owner, control = await _terr_owner(conn, chat_id, key)
        og = await gangwar._gang_row(conn, owner) if owner else None
        if not og or og["is_ai"]:
            raise GameError("Стычка только на районе банды игроков")
        if (await _j(conn, f"clash:{chat_id}:{key}")).get("until", 0) > now():
            raise GameError("Стычка на этом районе уже идёт")
        if await _i(conn, f"clcd:{chat_id}:{key}") > now():
            raise GameError("Район ещё не остыл после прошлой стычки (2 часа)")
        g, role = await _my_gang(conn, chat_id, uid)
        cop = await _cop(conn, chat_id, uid)
        if cop == "chief":
            by = "police"
            if await police.fund(conn, chat_id) < CLASH_COST:
                raise GameError(f"В фонде нужно {CLASH_COST} ₽")
            await _fund_add(conn, chat_id, -CLASH_COST)
        elif g and g["id"] == owner and role in ("boss", "deputy"):
            by = "gang"
            if (g["treasury"] or 0) < CLASH_COST:
                raise GameError(f"В общаке нужно {CLASH_COST} ₽")
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (CLASH_COST, g["id"]))
        else:
            raise GameError("Стычку начинает капитан полиции или босс (зам) банды-хозяина района")
        await _is(conn, f"clcd:{chat_id}:{key}", now() + CLASH_CD)
        await _js(conn, f"clash:{chat_id}:{key}", {"gid": owner, "gname": og["name"], "by": by, "until": now() + CLASH_MIN, "pg": 0, "pp": 0, "n": {}})
        who = "Капитан полиции бросил наряд на" if by == "police" else f"«{esc(og['name'])}» встречает полицию на"
        await _say(conn, chat_id, f"💥 <b>Стычка!</b> {who} район {tname(key)}. 5 минут: банда и полиция жмут «В бой» (мини-апп → Операции).")
        return P3._res(f"💥 Стычка началась: 5 минут, подтягивай своих.", True, -CLASH_COST)
    return await P3._act(chat_id, uid, run)


async def clash_join(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        d = await _j(conn, f"clash:{chat_id}:{key}")
        if d.get("until", 0) <= now():
            raise GameError("Стычки на этом районе нет")
        if await _is_member(conn, d["gid"], uid):
            side = "pg"
        elif await _cop(conn, chat_id, uid):
            side = "pp"
        else:
            raise GameError("Это стычка банды-хозяина и полиции")
        if d["n"].get(str(uid), 0) >= 3:
            raise GameError("Больше трёх рывков на человека нельзя")
        await _energy(conn, chat_id, uid, CLASH_ENERGY)
        d["n"][str(uid)] = d["n"].get(str(uid), 0) + 1
        d[side] += 1
        await _js(conn, f"clash:{chat_id}:{key}", d)
        return P3._res(f"💥 В бой: очки {'банды' if side == 'pg' else 'полиции'} {d[side]}.", True, 0)
    return await P3._act(chat_id, uid, run)


async def _clash_tick(conn, chat_id: int) -> None:
    import police
    for key in gangwar.TERR:
        d = await _j(conn, f"clash:{chat_id}:{key}")
        if not d or not d.get("until") or d["until"] > now():
            continue
        await _js(conn, f"clash:{chat_id}:{key}", {})
        gid = d["gid"]
        owner, control = await _terr_owner(conn, chat_id, key)
        members = len(await gangwar._real_members(conn, gid))
        mode = await _mode(conn, chat_id)
        gscore = d["pg"] + min(6, members) * 0.5 + control / 40
        pscore = d["pp"] + (2 if mode != "off" else 0) + min(3, await police.fund(conn, chat_id) // 2000)
        if gscore >= pscore:
            await conn.execute("UPDATE territory SET control = MIN(100, control + 8), police = MAX(0, police - 10) WHERE chat_id = ? AND key = ?", (chat_id, key))
            await police.case_add(conn, chat_id, gid, -5)
            await P5.rep_add(conn, gid, 2, 1)
            await _say(conn, chat_id, f"💥 Стычка за {tname(key)}: <b>банда «{esc(d['gname'])}» отбилась</b> ({gscore:.1f} против {pscore:.1f}). Контроль +8, давление полиции −10.")
        else:
            await conn.execute("UPDATE territory SET control = MAX(0, control - 8), police = MIN(100, police + 10) WHERE chat_id = ? AND key = ?", (chat_id, key))
            await police.case_add(conn, chat_id, gid, 6)
            g = await gangwar._gang_row(conn, gid)
            fine = min(600, int((g["treasury"] or 0) * 0.05)) if g else 0
            if fine:
                await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (fine, gid))
                await _fund_add(conn, chat_id, fine)
            cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id > 0 ORDER BY RANDOM() LIMIT 1", (gid,))
            r = await cur.fetchone()
            if r:
                await _jail(conn, chat_id, r[0], 6)
            await P5.rep_add(conn, gid, -2, 0)
            await _say(conn, chat_id, f"💥 Стычка за {tname(key)}: <b>полиция одолела «{esc(d['gname'])}»</b> ({pscore:.1f} против {gscore:.1f}). Контроль −8, дело +6, штраф {fine} ₽, один задержан на 6 минут.")


# ═════════════════════ Сделки полиции и ОПГ ═════════════════════
PURGE_MIN, PURGE_MAX, PURGE_CD, DEAL_MIN = 2500, 8000, 12 * 3600, 30
TIP_REWARD, TIP_CD = 500, 3 * 3600


async def collude_order(chat_id: int, uid: int, name: str, target, price) -> dict:
    target, price = int(target), int(price)

    async def run(conn):
        await _ensure(conn)
        import police2
        g, role = await _need_dep(conn, chat_id, uid)
        tg = await gangwar._gang_row(conn, target)
        if not tg or tg["chat_id"] != chat_id or target == g["id"] or tg["is_ai"] == 2:
            raise GameError("Выбери другую банду-конкурента")
        if not PURGE_MIN <= price <= PURGE_MAX:
            raise GameError(f"Плата капитану: от {PURGE_MIN} до {PURGE_MAX} ₽")
        if await _i(conn, f"cdeal:{g['id']}") > now():
            raise GameError("С полицией можно договариваться раз в 12 часов")
        cur = await conn.execute("SELECT 1 FROM deal WHERE chat_id = ? AND gid = ? AND status = 'open'", (chat_id, g["id"]))
        if await cur.fetchone():
            raise GameError("Прошлое предложение ещё на рассмотрении")
        if (g["treasury"] or 0) < price:
            raise GameError(f"В общаке нужно {price} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (price, g["id"]))
        t = now()
        await _is(conn, f"cdeal:{g['id']}", t + PURGE_CD)
        await conn.execute("INSERT INTO deal (chat_id, kind, gid, target, price, by_uid, created, expires) VALUES (?, 'purge', ?, ?, ?, ?, ?, ?)", (chat_id, g["id"], target, price, uid, t, t + DEAL_MIN * 60))
        await police2.alert(conn, chat_id, g["id"], "deal", f"🕶 «{g['name']}» предлагает {price} ₽ за зачистку конкурента «{tg['name']}». Решить в течение {DEAL_MIN} минут.")
        try:
            import justice
            await justice.report(conn, chat_id, int(g["boss"]), "gang", "заказ зачистки конкурента", 4)
        except Exception:
            pass
        return P3._res(f"🕶 Предложение ушло капитану: {price} ₽ в залоге. Если откажет или промолчит, ИИ-капитан решит сам (шанс около 50%). Деньги вернутся при отказе.", True, -price)
    return await P3._act(chat_id, uid, run)


async def _purge(conn, chat_id: int, d: dict) -> None:
    import police
    payer = await gangwar._gang_row(conn, d["gid"])
    tg = await gangwar._gang_row(conn, d["target"])
    if not payer or not tg:
        return
    await _fund_add(conn, chat_id, d["price"])
    lines = []
    cut = min(4000, int((tg["treasury"] or 0) * 0.20))
    if cut:
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cut, tg["id"]))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (cut // 2, payer["id"]))
        await _fund_add(conn, chat_id, cut // 2)
        lines.append(f"конфискат {cut} ₽ (половина заказчику)")
    if not tg["is_ai"]:
        await police.case_add(conn, chat_id, tg["id"], 25)
        cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id > 0 ORDER BY RANDOM() LIMIT 2", (tg["id"],))
        for (u,) in await cur.fetchall():
            await _jail(conn, chat_id, u, 15)
        lines.append("двое задержаны на 15 минут, дело +25")
    await P5.rep_add(conn, tg["id"], -5, 0)
    cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ? ORDER BY control ASC LIMIT 1", (chat_id, tg["id"]))
    r = await cur.fetchone()
    if r:
        await conn.execute("UPDATE territory SET owner = ?, control = 35, last_pay = ? WHERE chat_id = ? AND key = ?", (payer["id"], now(), chat_id, r[0]))
        lines.append(f"район {tname(r[0])} освободился и отошёл к заказчику")
    else:
        cur = await conn.execute("SELECT key FROM racket WHERE chat_id = ? AND owner = ? LIMIT 1", (chat_id, tg["id"]))
        r = await cur.fetchone()
        if r:
            await conn.execute("UPDATE racket SET owner = ?, pending = 0, rebel_until = 0 WHERE chat_id = ? AND key = ?", (payer["id"], chat_id, r[0]))
            lines.append(f"точка «{P8.POINTS[r[0]][1]}» перешла заказчику")
    await _say(conn, chat_id, f"🚨 <b>Зачистка:</b> полиция накрыла «{esc(tg['name'])}»: " + "; ".join(lines) + ".")
    if random.random() < 0.25:
        await police.case_add(conn, chat_id, payer["id"], 15)
        chief = await police.chief_of(conn, chat_id)
        if chief:
            await _cop_rep(conn, chat_id, chief, -3)
        await _say(conn, chat_id, f"📰 <b>Скандал:</b> всплыло, что зачистку «{esc(tg['name'])}» заказала «{esc(payer['name'])}». Дело на заказчика +15, у капитана репутация −3.")


async def collude_answer(chat_id: int, uid: int, name: str, did, accept) -> dict:
    async def run(conn):
        await _ensure(conn)
        await _need_cop(conn, chat_id, uid, chief=True)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM deal WHERE id = ? AND chat_id = ? AND status = 'open'", (int(did), chat_id))
        r = await cur.fetchone()
        if not r:
            raise GameError("Предложения уже нет")
        d = dict(r)
        if accept in (True, "true", 1, "1"):
            await conn.execute("UPDATE deal SET status = 'done' WHERE id = ?", (d["id"],))
            await _purge(conn, chat_id, d)
            return P3._res(f"🕶 Сделка принята: {d['price']} ₽ в фонд, конкурент зачищен.", True, 0)
        await conn.execute("UPDATE deal SET status = 'refused' WHERE id = ?", (d["id"],))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (d["price"], d["gid"]))
        return P3._res("🕶 Ты отказал: залог возвращён заказчику.", True, 0)
    return await P3._act(chat_id, uid, run)


async def _deals_tick(conn, chat_id: int) -> None:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM deal WHERE chat_id = ? AND status = 'open' AND expires <= ?", (chat_id, now()))
    for d in [dict(r) for r in await cur.fetchall()]:
        ok = random.random() < (0.6 if d["price"] >= 3500 else 0.4)
        if ok:
            await conn.execute("UPDATE deal SET status = 'done' WHERE id = ?", (d["id"],))
            await _purge(conn, chat_id, d)
        else:
            await conn.execute("UPDATE deal SET status = 'refused' WHERE id = ?", (d["id"],))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (d["price"], d["gid"]))


async def collude_tip(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        import police
        import police2
        g, role = await _need_dep(conn, chat_id, uid)
        tg = await gangwar._gang_row(conn, target)
        if not tg or tg["is_ai"] or tg["chat_id"] != chat_id or target == g["id"]:
            raise GameError("Слить можно другую банду игроков")
        if await _i(conn, f"ctip:{g['id']}") > now():
            raise GameError("Информацию можно сливать раз в 3 часа")
        if await police.fund(conn, chat_id) < TIP_REWARD:
            raise GameError("У полиции нет денег на награду")
        await _is(conn, f"ctip:{g['id']}", now() + TIP_CD)
        await _fund_add(conn, chat_id, -TIP_REWARD)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (TIP_REWARD, g["id"]))
        await police.case_add(conn, chat_id, target, 10)
        await police2.alert(conn, chat_id, target, "tip", f"📞 Информатор слил на «{tg['name']}»: дело +10.")
        if random.random() < 0.30:
            await P5.rep_add(conn, g["id"], -3, 0)
            await _say(conn, chat_id, f"📞 В «{esc(tg['name'])}» узнали, что на них стучит «{esc(g['name'])}». Уважение стукачей −3.")
            return P3._res(f"📞 Слив прошёл (+{TIP_REWARD} ₽), но тебя вычислили: уважение −3.", False, TIP_REWARD)
        return P3._res(f"📞 Слив прошёл тихо: +{TIP_REWARD} ₽ от полиции, на «{tg['name']}» дело +10.", True, TIP_REWARD)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ Падение и возрождение ИИ-банд ═════════════════════
NEW_GANGS = [("Мойдодыры", "🧼", -9811, "Умывальников"), ("Ночные курьеры", "🌙", -9812, "Серёжа Экспресс"), ("Картельчики", "🥐", -9813, "Дон Круассан"),
             ("Дачники", "🥒", -9814, "Палыч Грядкин"), ("Тихие бухгалтеры", "🧮", -9815, "Главбух"), ("Охотники за скидками", "🏷", -9816, "Купон")]
AI_MAX_ACTIVE = 5
try:
    import gang as _G
    for _n, _ic, _u, _bn in NEW_GANGS:
        _G.NPC_NAMES[_u] = _bn
        P3.AI_ICON[_n] = _ic
except Exception:  # pragma: no cover
    log.exception("Регистрация новых ИИ-банд")


async def _birth(conn, chat_id: int, old: str) -> bool:
    cur = await conn.execute("SELECT COUNT(*) FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    if (await cur.fetchone())[0] >= AI_MAX_ACTIVE:
        return False
    cur = await conn.execute("SELECT name FROM gang WHERE chat_id = ?", (chat_id,))
    used = {r[0] for r in await cur.fetchall()}
    free = [s for s in NEW_GANGS if s[0] not in used]
    if free:
        name, icon, boss, bname = random.choice(free)
    else:
        name, icon, boss, bname = random.choice(NEW_GANGS)
        name += " II"
    t = now()
    cur = await conn.execute("INSERT INTO gang (chat_id, name, boss, created, is_ai, last_act, treasury, respect, fear) VALUES (?, ?, ?, ?, 1, ?, 1500, 30, 30)", (chat_id, name, boss, t, t))
    gid = cur.lastrowid
    await conn.execute("INSERT OR IGNORE INTO gang_member (chat_id, user_id, gang_id, joined, role) VALUES (?, ?, ?, ?, 'boss')", (chat_id, boss, gid, t))
    # Новая банда не захватывает район/точку сразу: она выбирает цель и объявляет войну.
    target, target_name, target_kind = 0, "", ""
    cur = await conn.execute("SELECT key, owner FROM racket WHERE chat_id = ? AND owner NOT IN (0, ?) ORDER BY RANDOM() LIMIT 1", (chat_id, gid))
    r = await cur.fetchone()
    if r and random.random() < 0.65:
        target = int(r[1])
        target_kind = f"точку «{P8.POINTS[r[0]][1]}»"
        vg = await gangwar._gang_row(conn, target)
        target_name = vg["name"] if vg else "?"
    else:
        cur = await conn.execute("SELECT key, owner, control FROM territory WHERE chat_id = ? AND owner != 0 AND owner != ?", (chat_id, gid))
        terr = sorted([tuple(x) for x in await cur.fetchall()], key=lambda x: x[2])
        if terr:
            k, o, c = terr[0]
            target = int(o)
            target_kind = f"район {tname(k)}"
            vg = await gangwar._gang_row(conn, target)
            target_name = vg["name"] if vg else "?"
    if target:
        await conn.execute("INSERT INTO gang_war (chat_id, a, b, started, ends, score_a) VALUES (?, ?, ?, ?, ?, 0)",
                           (chat_id, gid, target, t, t + gangwar.WAR_HOURS * 3600))
    await rp._news(conn, chat_id, f"{icon} Из тени вышла новая ОПГ «{name}»")
    if target:
        await _say(conn, chat_id, f"{icon} <b>Через несколько дней на месте «{esc(old)}» появилась ОПГ «{esc(name)}»</b> (главный — {esc(bname)}). Сразу захватить {target_kind} она не смогла — объявила войну «{esc(target_name)}».")
    else:
        await _say(conn, chat_id, f"{icon} <b>На месте «{esc(old)}» появилась ОПГ «{esc(name)}»</b> (главный — {esc(bname)}), но без района: осматривается и ищет слабое место.")
    return True


async def _succession(conn, chat_id: int) -> None:
    cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 1 AND name != ?", (chat_id, gangwar.AI_NAME))
    for gid, name in await cur.fetchall():
        c1 = (await (await conn.execute("SELECT COUNT(*) FROM territory WHERE owner = ?", (gid,))).fetchone())[0]
        c2 = (await (await conn.execute("SELECT COUNT(*) FROM racket WHERE owner = ?", (gid,))).fetchone())[0]
        key = f"nofall:{gid}"
        if c1 or c2:
            if await _i(conn, key):
                await _is(conn, key, 0)
            continue
        curw = await conn.execute("SELECT 1 FROM gang_war WHERE status = 'active' AND (a = ? OR b = ?)", (gid, gid))
        at_war = bool(await curw.fetchone())
        curc = await conn.execute("SELECT created FROM gang WHERE id = ?", (gid,))
        crow = await curc.fetchone()
        young = bool(crow and now() - int(crow[0] or 0) < 86400)
        if at_war or young:
            if await _i(conn, key):
                await _is(conn, key, 0)
            continue
        first = await _i(conn, key)
        if not first:
            await _is(conn, key, now())
        elif now() - first >= 1800:
            await conn.execute("UPDATE gang SET is_ai = 2, treasury = 0 WHERE id = ?", (gid,))
            await conn.execute("UPDATE gang_war SET status = 'done' WHERE status = 'active' AND (a = ? OR b = ?)", (gid, gid))
            born = (await _j(conn, f"born:{chat_id}")).get("list", [])
            born.append({"at": now() + random.randint(2 * 86400, 4 * 86400), "old": name})
            await _js(conn, f"born:{chat_id}", {"list": born})
            await rp._news(conn, chat_id, f"💀 ОПГ «{name}» разгромлена и исчезла")
            await _say(conn, chat_id, f"💀 <b>ОПГ «{esc(name)}» разгромлена</b> и исчезла из города: ни районов, ни точек. Свято место пусто не бывает, скоро объявится кто-то новый.")
    data = await _j(conn, f"born:{chat_id}")
    left = []
    for b in data.get("list", []):
        if b["at"] <= now():
            if not await _birth(conn, chat_id, b["old"]):
                b["at"] = now() + 3600
                left.append(b)
        else:
            left.append(b)
    if left != data.get("list", []):
        await _js(conn, f"born:{chat_id}", {"list": left})


async def _player_succession(conn, chat_id: int) -> None:
    """Игровая ОПГ без районов и точек 30 минут разваливается: полиция получает долю,
    а через 1–3 часа на её месте появляется новая ИИ-банда и сразу отжимает точку/район."""
    t = now()
    cur = await conn.execute("SELECT id, name, treasury, created FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
    for gid, name, treasury, created in await cur.fetchall():
        c1 = (await (await conn.execute("SELECT COUNT(*) FROM territory WHERE owner = ?", (gid,))).fetchone())[0]
        c2 = (await (await conn.execute("SELECT COUNT(*) FROM racket WHERE owner = ?", (gid,))).fetchone())[0]
        key = f"nofallp:{gid}"
        if c1 or c2 or t - int(created or 0) < 3600:
            if await _i(conn, key):
                await _is(conn, key, 0)
            continue
        first = await _i(conn, key)
        if not first:
            await _is(conn, key, t)
            continue
        if t - first < 1800:
            continue
        import police
        cut = min(4000, int((treasury or 0) * 0.5))
        if cut:
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (cut, gid))
            await police.fund_add(conn, chat_id, cut)
        await conn.execute("DELETE FROM gang_member WHERE gang_id = ?", (gid,))
        await conn.execute("DELETE FROM gang_invite WHERE gang_id = ?", (gid,))
        await conn.execute("UPDATE gang_pact SET status = 'broken' WHERE a = ? OR b = ?", (gid, gid))
        await conn.execute("UPDATE gang_war SET status = 'done' WHERE status = 'active' AND (a = ? OR b = ?)", (gid, gid))
        await conn.execute("UPDATE police_case SET pts = 0 WHERE chat_id = ? AND gang_id = ?", (chat_id, gid))
        await conn.execute("UPDATE gang SET is_ai = 2, treasury = 0, last_act = ? WHERE id = ?", (t, gid))
        born = (await _j(conn, f"born:{chat_id}")).get("list", [])
        born.append({"at": t + random.randint(2 * 86400, 4 * 86400), "old": name})
        await _js(conn, f"born:{chat_id}", {"list": born})
        await rp._news(conn, chat_id, f"💀 ОПГ «{name}» развалилась: полиция забрала {cut} ₽")
        await _say(conn, chat_id, f"💀 <b>ОПГ «{esc(name)}» развалилась</b>: ни районов, ни точек, люди разбежались. Полиция забрала {cut} ₽. Через время на этом месте появится новая банда.")


# ═════════════════════ состояние для мини-аппа ═════════════════════
async def state(conn, chat_id: int, uid: int) -> dict:
    import police
    await _ensure(conn)
    t = now()
    g, role = await _my_gang(conn, chat_id, uid)
    cop = await _cop(conn, chat_id, uid)
    gid = g["id"] if g else 0
    dep = role in ("boss", "deputy")
    out = {"role": role, "cop": cop, "my_gid": gid}
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT id, name, is_ai FROM gang WHERE chat_id = ? AND is_ai IN (0, 1)", (chat_id,))
    gangs = [dict(r) for r in await cur.fetchall()]
    gname = {x["id"]: x["name"] for x in gangs}
    out["gangs"] = [{"id": x["id"], "name": x["name"], "ai": bool(x["is_ai"]), "mine": x["id"] == gid} for x in gangs]
    cur = await conn.execute("SELECT key, owner, control FROM territory WHERE chat_id = ?", (chat_id,))
    terrs = []
    for r in await cur.fetchall():
        o = r["owner"]
        terrs.append({"key": r["key"], "title": tname(r["key"]), "owner": o, "owner_name": gname.get(o, "ничья"), "control": r["control"], "mine": o == gid and gid != 0, "ai": any(x["id"] == o and x["is_ai"] for x in gangs)})
    blitz = []
    busy = set()
    for key in gangwar.TERR:
        b = await _j(conn, f"blitz:{chat_id}:{key}")
        if b.get("until", 0) > t:
            busy.add(key)
            side = "att" if gid == b["att"] else "def" if gid == b["def"] else ""
            blitz.append({"key": key, "title": tname(key), "att": b["attn"], "def": b["defn"], "left": b["until"] - t, "need": b["need"], "score": b["prog"] - b["reinf"] - b.get("cut", 0), "side": side, "my_n": b["hold"].get(str(uid), 0) if side == "att" else b.get("rhold", {}).get(str(uid), 0)})
    out["blitz"] = blitz
    out["blitz_targets"] = [x for x in terrs if dep and x["owner"] and not x["mine"] and x["key"] not in busy]
    cur = await conn.execute("SELECT key, owner FROM racket WHERE chat_id = ?", (chat_id,))
    points = []
    for r in await cur.fetchall():
        tmp = await _j(conn, f"rtemp:{chat_id}:{r['key']}")
        ic, title, _t = P8.POINTS[r["key"]]
        points.append({"key": r["key"], "title": f"{ic} {title}", "owner_name": gname.get(r["owner"], "?"), "mine": r["owner"] == gid and gid != 0, "temp": max(0, tmp.get("until", 0) - t) if tmp else 0})
    out["points"] = points
    cur = await conn.execute("SELECT * FROM convoy WHERE chat_id = ? AND status = 'way' ORDER BY arrives", (chat_id,))
    convoys = []
    for c in [dict(r) for r in await cur.fetchall()]:
        mine = c["gid"] == gid and gid != 0
        tried = uid in json.loads(c["tried"])
        convoys.append({"id": c["id"], "gang": gname.get(c["gid"], "?"), "size": SIZES[c["size"]][3], "left": max(0, c["arrives"] - t), "mine": mine, "tried": tried,
                        "can": bool((g or cop) and not mine and not tried), "kind": c["kind"] if mine else "", "cargo": c["cargo"] if mine else 0})
    out["convoys"] = convoys
    if gid:
        cur = await conn.execute("SELECT kind, cargo, status, result FROM convoy WHERE chat_id = ? AND gid = ? AND status != 'way' ORDER BY id DESC LIMIT 4", (chat_id, gid))
        out["convoy_recent"] = [dict(r) for r in await cur.fetchall()]
    out["sizes"] = [{"key": k, "cargo": v[0], "min": v[1], "mult": v[2], "label": v[3]} for k, v in SIZES.items()]
    out["bonus_smuggle"] = SMUGGLE_BONUS
    cur = await conn.execute("SELECT * FROM lot WHERE chat_id = ? AND status = 'open' ORDER BY id", (chat_id,))
    out["lots"] = [{"id": l["id"], "value": l["value"], "gang": gname.get(l["gid"], "?"), "left": max(0, l["expires"] - t), "mine": l["gid"] == gid and gid != 0, "note": l["note"]} for l in await cur.fetchall()]
    bm = await _j(conn, f"bm:{chat_id}")
    if bm.get("ends", 0) > t and not bm.get("done"):
        ic, title, _p = P5.ITEMS[bm["res"]]
        out["bm"] = {"item": f"{ic} {title}", "qty": bm["qty"], "min": bm["min"], "best": bm["best"], "gname": bm.get("gname", ""), "left": bm["ends"] - t, "mine": bm["gid"] == gid and gid != 0,
                     "floor": bm["min"] if not bm["best"] else int(bm["best"] * 1.1) + 1}
    else:
        out["bm"] = None
    rpst = await _j(conn, f"roadpost:{chat_id}")
    out["roadpost"] = {"left": max(0, rpst.get("until", 0) - t), "cd": max(0, await _i(conn, f"rpcd:{chat_id}") - t), "cost": ROADPOST_COST}
    if cop:
        inf = await _j(conn, f"infil:{chat_id}:{uid}")
        if inf.get("until", 0) > t:
            ig = await gangwar._gang_row(conn, inf["gid"])
            icv = [{"size": SIZES[c["size"]][3], "cargo": c["cargo"], "kind": c["kind"], "left": max(0, c["arrives"] - t)} for c in convoys_raw(await _convoys_of(conn, chat_id, inf["gid"]))]
            out["infil"] = {"gang": inf["gname"], "left": inf["until"] - t, "treasury": ig["treasury"] if ig else 0, "convoys": icv}
        else:
            out["infil"] = None
        out["infil_cd"] = max(0, await _i(conn, f"infcd:{chat_id}:{uid}") - t)
        out["tips"] = [{"id": x["id"], "name": x["name"], "tips": await _tip_count(conn, chat_id, x["id"])} for x in gangs if not x["is_ai"]]
        out["special_cd"] = max(0, await _i(conn, f"spcd:{chat_id}") - t)
        out["fund"] = await police.fund(conn, chat_id)
    clash = []
    for key in gangwar.TERR:
        d = await _j(conn, f"clash:{chat_id}:{key}")
        if d.get("until", 0) > t:
            side = "gang" if (gid and gid == d["gid"]) else "police" if cop else ""
            clash.append({"key": key, "title": tname(key), "gang": d["gname"], "by": d["by"], "left": d["until"] - t, "pg": d["pg"], "pp": d["pp"], "side": side, "my_n": d["n"].get(str(uid), 0)})
    out["clash"] = clash
    clash_busy = {c["key"] for c in clash}
    out["clash_targets"] = [x for x in terrs if x["owner"] and not x["ai"] and x["key"] not in clash_busy and ((cop == "chief") or (dep and x["mine"]))]
    deals_in, deals_out = [], []
    if cop == "chief":
        cur = await conn.execute("SELECT * FROM deal WHERE chat_id = ? AND status = 'open' ORDER BY id", (chat_id,))
        deals_in = [{"id": d["id"], "gang": gname.get(d["gid"], "?"), "target": gname.get(d["target"], "?"), "price": d["price"], "left": max(0, d["expires"] - t)} for d in await cur.fetchall()]
    if gid and dep:
        cur = await conn.execute("SELECT * FROM deal WHERE chat_id = ? AND gid = ? ORDER BY id DESC LIMIT 3", (chat_id, gid))
        deals_out = [{"target": gname.get(d["target"], "?"), "price": d["price"], "status": d["status"], "left": max(0, d["expires"] - t)} for d in await cur.fetchall()]
    out["deals_in"], out["deals_out"] = deals_in, deals_out
    out["deal_range"] = [PURGE_MIN, PURGE_MAX]
    out["cd"] = {"deal": max(0, await _i(conn, f"cdeal:{gid}") - t) if gid else 0, "tip": max(0, await _i(conn, f"ctip:{gid}") - t) if gid else 0,
                 "blitz": max(0, await _i(conn, f"bzcd:{gid}") - t) if gid else 0, "raid": max(0, await _i(conn, f"prcd:{gid}") - t) if gid else 0}
    out["costs"] = {"blitz": BLITZ_COST, "raid": RAID_COST, "escort": ESCORT_COST, "tip_reward": TIP_REWARD, "special": SPECIAL_COST, "clash": CLASH_COST}
    return out


async def _convoys_of(conn, chat_id: int, gid: int) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM convoy WHERE chat_id = ? AND gid = ? AND status = 'way'", (chat_id, gid))
    return [dict(r) for r in await cur.fetchall()]


def convoys_raw(x):
    return x


# ═════════════════════ фон ═════════════════════
async def tick(conn) -> None:
    try:
        await _ensure(conn)
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
        for (chat_id,) in await cur.fetchall():
            try:
                await _blitz_tick(conn, chat_id)
                await _rtemp_tick(conn, chat_id)
                await _convoy_tick(conn, chat_id)
                await _bm_tick(conn, chat_id)
                await _infil_tick(conn, chat_id)
                await _clash_tick(conn, chat_id)
                await _deals_tick(conn, chat_id)
                await _succession(conn, chat_id)
                await _player_succession(conn, chat_id)
                cur2 = await conn.execute("SELECT COUNT(*) FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
                if (await cur2.fetchone())[0] and 10 <= now_local().hour < 23 and await P3._roll(conn, f"wbm:{chat_id}", BM_MEAN):
                    await _bm_start(conn, chat_id)
            except Exception:
                log.exception("Сбой конфликтов в чате %s", chat_id)
    except Exception:
        log.exception("Сбой конфликтов")
