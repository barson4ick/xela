"""ИИ-граждане района: работают, получают уличный авторитет, вступают в ОПГ или полицию.
Максимум 6 человек в группе — как у игроков. Плюс три ИИ-помощника в «Законченных»."""
import logging
import random
import time
from typing import Optional

import aiosqlite

from config import DB_PATH

log = logging.getLogger(__name__)

CITIZENS = [
    (-9501, "Витёк", "🧢"),
    (-9502, "Гоша", "👦"),
    (-9503, "Толян", "🧔"),
    (-9504, "Серёга", "🧑‍🔧"),
    (-9505, "Макс", "🕶️"),
    (-9506, "Димон", "🧑‍🎤"),
]
HELPERS = [
    (-9601, "Кувалда", "💪"),
    (-9602, "Шустрый", "🏃"),
    (-9603, "Смета", "🧮"),
]
PATRONS = [
    (-9701, "Крёстный отец", "👑"),
    (-9702, "Мэр Архип", "🎩"),
]
MAX_GROUP = 6
MAX_HELPERS = 3
MAX_CITIZENS_IN_GANG = 5  # ИИ-граждан в одной игровой банде: дальше они идут в полицию
AI_HELPER_DAY_CAP = 500
AI_POLICE_DAY_CAP = 300
TICK_GAP = 60 * 60
GANG_TICK_GAP = 30 * 60  # ИИ внутри игровой банды тикают чаще остальных


def now() -> int:
    return int(time.time())


async def _kget(conn, key: str) -> int:
    cur = await conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
    r = await cur.fetchone()
    try:
        return int(r[0]) if r else 0
    except Exception:
        return 0


async def _kset(conn, key: str, val) -> None:
    await conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(val)))


async def _capped(conn, chat_id: int, key: str, amount: int, cap: int, flow_key: str) -> int:
    """Не даёт ИИ печатать деньги бесконечно: дневной лимит на чат/ключ."""
    if amount <= 0:
        return 0
    day = time.strftime("%Y-%m-%d")
    k = f"aicap:{chat_id}:{key}:{day}"
    used = await _kget(conn, k)
    give = min(int(amount), max(0, int(cap) - used))
    if give <= 0:
        return 0
    await _kset(conn, k, used + give)
    try:
        import balance
        await balance.flow(conn, chat_id, flow_key, give)
    except Exception:
        pass
    return give


async def _ensure_person(conn, chat_id: int, uid: int, name: str, icon: str, kind: str, money: int = 400) -> None:
    await conn.execute("INSERT OR IGNORE INTO users (user_id, chat_id, username, emoji) VALUES (?, ?, ?, ?)", (uid, chat_id, name, icon))
    await conn.execute(
        "INSERT OR IGNORE INTO rp_player (chat_id, user_id, money, energy, energy_ts, wanted_ts, collect_ts) VALUES (?, ?, ?, 100, ?, ?, ?)",
        (chat_id, uid, money, now(), now(), now()),
    )
    await conn.execute(
        "INSERT OR IGNORE INTO ai_citizen (chat_id, user_id, name, icon, kind, money, cred, last_act) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (chat_id, uid, name, icon, kind, money),
    )


async def ensure(conn, chat_id: int) -> None:
    cur = await conn.execute("SELECT COUNT(*) FROM ai_citizen WHERE chat_id = ?", (chat_id,))
    count = (await cur.fetchone())[0]
    if count == 0:
        for uid, name, icon in CITIZENS:
            await _ensure_person(conn, chat_id, uid, name, icon, "citizen", random.randint(300, 900))
    for uid, name, icon in PATRONS:
        await _ensure_person(conn, chat_id, uid, name, icon, "patron", 0)
    # Три помощника — только в банду «Законченные», и только если есть место.
    cur = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 0 AND name = ? LIMIT 1", (chat_id, "Законченные"))
    row = await cur.fetchone()
    if not row:
        return
    gid = row[0]
    if await _kget(conn, f"recruitlock:{chat_id}:gang") > now():
        return
    cur = await conn.execute("SELECT COUNT(*) FROM gang_member WHERE gang_id = ? AND user_id < 0", (gid,))
    helpers = (await cur.fetchone())[0]
    for uid, name, icon in HELPERS:
        if helpers >= MAX_HELPERS:
            break
        await _ensure_person(conn, chat_id, uid, name, icon, "helper", 0)
        cur = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        if await cur.fetchone():
            continue
        await conn.execute("INSERT INTO gang_member (chat_id, user_id, gang_id, joined, role, merit) VALUES (?, ?, ?, ?, 'grunt', 0)", (chat_id, uid, gid, now()))
        helpers += 1
        await _announce(conn, chat_id, f"🤖 В ОПГ «Законченные» появился свой человек: {icon} {name}. Теперь он помогает банде.")


async def _announce(conn, chat_id: int, text: str) -> None:
    try:
        import gangwar
        await gangwar.say_ai(conn, chat_id, text)
    except Exception:
        log.debug("не удалось отправить сообщение", exc_info=True)


async def _membership(conn, chat_id: int, uid: int) -> tuple:
    cur = await conn.execute("SELECT g.id, g.name FROM gang_member m JOIN gang g ON g.id = m.gang_id WHERE m.chat_id = ? AND m.user_id = ?", (chat_id, uid))
    row = await cur.fetchone()
    if row:
        return "gang", row[0], row[1]
    cur = await conn.execute("SELECT 1 FROM police_force WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    if await cur.fetchone():
        return "police", 0, "Полиция"
    return "citizen", 0, ""


async def view(conn, chat_id: int) -> list:
    await ensure(conn, chat_id)
    cur = await conn.execute("SELECT user_id, name, icon, kind, money, cred FROM ai_citizen WHERE chat_id = ? ORDER BY kind DESC, user_id", (chat_id,))
    out = []
    for uid, name, icon, kind, money, cred in await cur.fetchall():
        if kind == "patron":
            continue
        state, gid, gname = await _membership(conn, chat_id, uid)
        out.append({"id": uid, "name": name, "icon": icon, "kind": kind, "money": money, "cred": cred,
                    "state": state, "group": gname, "ai": True})
    return out


async def tick(conn) -> None:
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM ai_citizen")
        for (chat_id,) in await cur.fetchall():
            await ensure(conn, chat_id)
            last = await _kget(conn, f"aictick:{chat_id}")
            if not last or now() - last >= TICK_GAP:
                await _kset(conn, f"aictick:{chat_id}", now())
                await _tick_chat(conn, chat_id)
            lastg = await _kget(conn, f"aigtick:{chat_id}")
            if not lastg or now() - lastg >= GANG_TICK_GAP:
                await _kset(conn, f"aigtick:{chat_id}", now())
                await _tick_gang_ai(conn, chat_id)
    except Exception:
        log.exception("Сбой ИИ-граждан")


async def _tick_chat(conn, chat_id: int) -> None:
    cur = await conn.execute("SELECT user_id, name, icon, kind, money, cred FROM ai_citizen WHERE chat_id = ?", (chat_id,))
    for uid, name, icon, kind, money, cred in await cur.fetchall():
        if kind == "patron":
            continue
        state, gid, gname = await _membership(conn, chat_id, uid)
        if state == "gang":
            continue  # ИИ в игровых бандах обрабатывает _tick_gang_ai (чаще и с подтяжкой)
        if state == "police":
            add = await _capped(conn, chat_id, "police", random.randint(10, 20), AI_POLICE_DAY_CAP, "ai_police")
            if add:
                await _kset(conn, f"pfund:{chat_id}", await _kget(conn, f"pfund:{chat_id}") + add)
            await _kset(conn, f"porder:{chat_id}", min(100, (await _kget(conn, f"porder:{chat_id}") or 40) + 1))
            if random.random() < 0.18:
                cur2 = await conn.execute("""SELECT s.gang_id, s.res, s.qty, g.name FROM gang_stock s JOIN gang g ON g.id = s.gang_id
                                             WHERE g.chat_id = ? AND g.is_ai = 0 AND s.qty > 2 ORDER BY RANDOM() LIMIT 1""", (chat_id,))
                row2 = await cur2.fetchone()
                if row2:
                    gid2, res2, qty2, gname2 = row2
                    stolen = min(int(qty2), random.randint(3, 8))
                    await conn.execute("UPDATE gang_stock SET qty = MAX(0, qty - ?) WHERE gang_id = ? AND res = ?", (stolen, gid2, res2))
                    await _kset(conn, f"pfund:{chat_id}", await _kget(conn, f"pfund:{chat_id}") + stolen * 50)
                    await _announce(conn, chat_id, f"🚓 ИИ-офицер {name} перехватил караван «{esc(gname2)}»: изъято {stolen} ед. груза, фонд +{stolen * 50} ₽.")
            if random.random() < 0.35:
                cur2 = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner != 0", (chat_id,))
                keys = [r[0] for r in await cur2.fetchall()]
                if keys:
                    key = random.choice(keys)
                    await conn.execute("UPDATE territory SET police = MIN(100, police + 8), control = MAX(0, control - 4) WHERE chat_id = ? AND key = ?", (chat_id, key))
                    tname = "?"
                    try:
                        import gangwar
                        tname = gangwar.TERR[key][1]
                    except Exception:
                        pass
                    await _announce(conn, chat_id, f"🚓 ИИ-офицер {name} прошёл по району {tname}: полиция +8, контроль банды −4.")
            await conn.execute("UPDATE ai_citizen SET last_act = ? WHERE chat_id = ? AND user_id = ?", (now(), chat_id, uid))
            continue
        # Обычный гражданин: работает, копит авторитет, затем пробует вступить.
        cred = int(cred or 0) + random.randint(1, 2)
        money = int(money or 0) + random.randint(60, 160)
        await conn.execute("UPDATE ai_citizen SET cred = ?, money = ?, last_act = ? WHERE chat_id = ? AND user_id = ?", (cred, money, now(), chat_id, uid))
        if cred < 10 or random.random() > 0.65:
            continue
        if await _join_gang(conn, chat_id, uid, name, icon):
            continue
        await _join_police(conn, chat_id, uid, name)


async def _catchup(conn, chat_id: int, gid: int) -> float:
    """1.0 — банда впереди; до 2.0 — сильно отстаёт от лучшей соперницы (тогда ИИ внутри банды работают активнее)."""
    try:
        import gangplus8
        mine = await gangplus8._power(conn, gid)
        cur = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND id != ?", (chat_id, gid))
        best = 0.0
        for (oid,) in await cur.fetchall():
            best = max(best, await gangplus8._power(conn, oid))
        if best <= mine:
            return 1.0
        return 1.0 + min(1.0, (best - mine) / max(best, 1.0))
    except Exception:
        log.debug("не удалось посчитать отставание банды", exc_info=True)
        return 1.5


async def _tick_gang_ai(conn, chat_id: int) -> None:
    """ИИ-помощники и ИИ-граждане в игровых ОПГ: общак, авторитет, контроль района. Чем сильнее банда отстаёт, тем активнее."""
    cur = await conn.execute(
        """SELECT m.user_id, a.name, a.icon, a.kind, m.gang_id, g.name FROM gang_member m
           JOIN ai_citizen a ON a.chat_id = m.chat_id AND a.user_id = m.user_id
           JOIN gang g ON g.id = m.gang_id
           WHERE m.chat_id = ? AND g.is_ai = 0""", (chat_id,))
    rows = await cur.fetchall()
    boosts = {}
    for uid, name, icon, kind, gid, gname in rows:
        if gid not in boosts:
            boosts[gid] = await _catchup(conn, chat_id, gid)
        b = boosts[gid]
        # Крыса: прежние 4% в час => ~2% за получасовой тик.
        if kind == "citizen" and random.random() < 0.02:
            cur2 = await conn.execute("SELECT treasury FROM gang WHERE id = ?", (gid,))
            row2 = await cur2.fetchone()
            treasury = int((row2[0] if row2 else 0) or 0)
            if treasury > 500:
                steal = max(100, int(treasury * random.uniform(0.04, 0.08)))
                await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (steal, gid))
                await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
                await conn.execute("UPDATE ai_citizen SET last_act = ? WHERE chat_id = ? AND user_id = ?", (now(), chat_id, uid))
                await _announce(conn, chat_id, f"🐀 {icon} {name} оказался крысой: слинял из «{esc(gname)}» с {steal} ₽ из общака.")
                continue
        if random.random() > 0.8:
            continue
        cap = int(AI_HELPER_DAY_CAP * (1 + 2 * (b - 1)))
        _st = await (await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = 'station'", (chat_id,))).fetchone()
        if _st and _st[0] == gid:
            cap = int(cap * 1.5)  # Вокзал: поток новобранцев
        add = await _capped(conn, chat_id, f"g{gid}", int(random.randint(10, 30) * b), cap, "ai_help")
        await conn.execute("UPDATE gang SET treasury = treasury + ?, last_act = ? WHERE id = ?", (add, now(), gid))
        await conn.execute("UPDATE gang_member SET merit = merit + ? WHERE chat_id = ? AND user_id = ?", (2 if b > 1.3 else 1, chat_id, uid))
        if random.random() < min(0.8, 0.4 * b):
            cur2 = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ?", (chat_id, gid))
            keys = [r[0] for r in await cur2.fetchall()]
            if keys:
                await conn.execute("UPDATE territory SET control = MIN(100, control + ?) WHERE chat_id = ? AND key = ?",
                                   (2 if b > 1.5 else 1, chat_id, random.choice(keys)))
        await conn.execute("UPDATE ai_citizen SET last_act = ? WHERE chat_id = ? AND user_id = ?", (now(), chat_id, uid))


async def _patron_tick(conn, chat_id: int, uid: int, name: str, icon: str) -> None:
    if uid == -9701:
        cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 0 ORDER BY treasury ASC LIMIT 1", (chat_id,))
        row = await cur.fetchone()
        if not row:
            return
        add = random.randint(250, 500)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (add, row[0]))
        if random.random() < 0.35:
            await _announce(conn, chat_id, f"👑 Крёстный отец занёс «{esc(row[1])}» {add} ₽: «На район, пацаны. Потом сочтёмся».")
    elif uid == -9702:
        # Пока мэр не избран игроками, Архип держит полицию на плаву.
        add = random.randint(150, 300)
        await _kset(conn, f"pfund:{chat_id}", await _kget(conn, f"pfund:{chat_id}") + add)
        await _kset(conn, f"porder:{chat_id}", min(100, (await _kget(conn, f"porder:{chat_id}") or 40) + 1))
        if random.random() < 0.35:
            await _announce(conn, chat_id, f"🎩 Мэр Архип выделил полиции {add} ₽ и напомнил: «Порядок — лицо района».")


async def _join_gang(conn, chat_id: int, uid: int, name: str, icon: str) -> bool:
    if await _kget(conn, f"recruitlock:{chat_id}:gang") > now():
        return False
    cur = await conn.execute(
        """SELECT g.id, g.name, COUNT(CASE WHEN m.user_id > 0 THEN 1 END) AS n,
                  COUNT(CASE WHEN m.user_id < 0 THEN 1 END) AS na FROM gang g
           LEFT JOIN gang_member m ON m.gang_id = g.id
           WHERE g.chat_id = ? AND g.is_ai = 0 GROUP BY g.id HAVING n < ? AND na < ?""", (chat_id, MAX_GROUP, MAX_HELPERS + MAX_CITIZENS_IN_GANG))
    rows = await cur.fetchall()
    if not rows:
        return False
    gid, gname = random.choice(rows)[:2]
    await conn.execute("INSERT OR IGNORE INTO gang_member (chat_id, user_id, gang_id, joined, role, merit) VALUES (?, ?, ?, ?, 'grunt', 0)", (chat_id, uid, gid, now()))
    await _announce(conn, chat_id, f"🤖 {icon} {name} вступил в ОПГ «{esc(gname)}»: сначала холопом, но с улицы пришёл не просто так.")
    return True


async def _join_police(conn, chat_id: int, uid: int, name: str) -> bool:
    cur = await conn.execute("SELECT COUNT(*) FROM police_force WHERE chat_id = ?", (chat_id,))
    if (await cur.fetchone())[0] >= MAX_GROUP:
        return False
    await conn.execute("INSERT OR IGNORE INTO police_force (chat_id, user_id, rank, joined) VALUES (?, ?, 'officer', ?)", (chat_id, uid, now()))
    await _announce(conn, chat_id, f"🚓 {name} надел погоны: ИИ-гражданин теперь служит в полиции района.")
    return True


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
