"""Задания: босс (и замы) ОПГ дают поручения участникам, капитан полиции — офицерам.
Не выполнил за срок — задание уходит автоматом: за тебя работает исполнитель-ИИ, но шанс ниже (×0.55), добыча меньше (×0.7), а за игнор снимают авторитет."""
import json
import logging
import random
import time

import aiosqlite

import gangplus3 as P3
import gangwar
import rp
from rooms import GameError
from utils import esc
from world import _i, _is, _my_gang, _name, _say

log = logging.getLogger("assign")

DEADLINE = 30 * 60
AUTO_CHANCE, AUTO_REWARD = 0.55, 0.7
ENERGY = 10
ENERGY_BY = {"collect": 15, "heist": 20, "guard": 10, "cover": 10, "patrol": 10, "tip": 10, "arrest": 15, "stall": 10}
DAILY_GIVE = 12
SYS_GAP, SYS_DEADLINE, SYS_DAILY, SYS_SCALE = 25 * 60, 20 * 60, 6, 0.6
GIVE_GAP = 120
MAX_OPEN_PER_ORG = 4

GANG_KINDS = {
    "collect": ("💰", "Собрать выручку", "Обойти точки и принести деньги в общак (100–250 ₽, ⚡15)", 0.70),
    "heist": ("🥷", "Мелкое дело", "Тихо провернуть дело: 200–450 ₽ в общак, при провале срок (⚡20)", 0.55),
    "guard": ("🛡", "Охранять район", "Контроль района банды +3", 0.85),
    "cover": ("🕶", "Снять хвост", "Замести следы: дело на банду −4", 0.75),
}
POLICE_KINDS = {
    "patrol": ("🚓", "Патруль района", "Давление полиции на район банды +6, контроль банды −3", 0.80),
    "tip": ("📞", "Проверить наводку", "Дело на самую подозрительную банду +5", 0.70),
    "arrest": ("🚔", "Задержать розыскника", "Игрок с розыском 2⭐ и больше уходит в камеру на 8 минут", 0.55),
    "stall": ("🏪", "Проверить ларёк", "Штраф владельцу ларька в фонд полиции", 0.70),
}
KINDS = {"gang": GANG_KINDS, "police": POLICE_KINDS}
_ready = False


def now() -> int:
    return int(time.time())


async def _mayor_uid(conn, chat_id: int) -> int:
    raw = await gangwar.kget(conn, f"mayor:{chat_id}")
    try:
        m = json.loads(raw) if raw else {}
    except Exception:
        m = {}
    return int(m.get("uid", 0) or 0) if m and m.get("until", 0) > now() and not m.get("ai") else 0


async def _god(conn, chat_id: int) -> dict:
    raw = await gangwar.kget(conn, f"don:{chat_id}")
    try:
        d = json.loads(raw) if raw else {}
    except Exception:
        d = {}
    return d if d and d.get("until", 0) > now() else {}


async def _ensure(conn) -> None:
    global _ready
    if _ready:
        return
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS assignment (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, org TEXT NOT NULL, gid INTEGER NOT NULL DEFAULT 0,
            by_uid INTEGER NOT NULL, to_uid INTEGER NOT NULL, kind TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', result TEXT NOT NULL DEFAULT '')""")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_assign_open ON assignment (status, expires)")
    _ready = True


def _spec(org: str, kind: str):
    return KINDS[org][kind]


# ═════════════════════ исполнение ═════════════════════
async def _effect(conn, a: dict, auto: bool) -> tuple:
    """Возвращает (успех, текст). auto — задание отправили автоматом: шанс и добыча ниже."""
    import gangplus2 as G2
    import police
    chat_id, kind, uid = a["chat_id"], a["kind"], a["to_uid"]
    chance = _spec(a["org"], kind)[3] * (AUTO_CHANCE if auto else 1.0)
    r = (AUTO_REWARD if auto else 1.0) * (SYS_SCALE if not a.get("by_uid") else 1.0)
    ok = random.random() < chance
    gid = a["gid"]
    if a["org"] == "gang":
        if kind == "collect":
            amount = int(random.randint(100, 250) * r)
            if ok:
                await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (amount, gid))
                await G2.log_event(conn, chat_id, uid, "trade", amount)
                return True, f"собрал {amount} ₽ в общак"
            await police.case_add(conn, chat_id, gid, 2)
            if not auto:
                await G2._beat(conn, chat_id, uid, 10)
            return False, "выручку не отдали" + ("" if auto else ", исполнитель лежит 10 минут")
        if kind == "heist":
            loot = int(random.randint(200, 450) * r)
            if ok:
                await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (loot, gid))
                await G2.log_event(conn, chat_id, uid, "crime", loot)
                return True, f"дело удалось: {loot} ₽ в общак"
            if auto:
                await police.case_add(conn, chat_id, gid, 3)
                return False, "дело сорвалось, полиция заинтересовалась (дело +3)"
            await conn.execute("UPDATE rp_player SET jail_until = ?, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (now() + 5 * 60, chat_id, uid))
            await G2.log_event(conn, chat_id, uid, "jail", 1)
            return False, "дело провалилось, исполнитель сел на 5 минут"
        if kind == "guard":
            cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ?", (chat_id, gid))
            keys = [x[0] for x in await cur.fetchall()]
            if ok and keys:
                add = max(1, int(3 * r))
                await conn.execute("UPDATE territory SET control = MIN(100, control + ?) WHERE chat_id = ? AND key = ?", (add, chat_id, random.choice(keys)))
                return True, f"район охранён: контроль +{add}"
            if ok:
                await conn.execute("UPDATE gang SET respect = MIN(100, respect + 1) WHERE id = ?", (gid,))
                return True, "районов нет, зато авторитет банды +1"
            return False, "охрана проспала"
        if kind == "cover":
            if ok:
                await police.case_add(conn, chat_id, gid, -max(1, int(4 * r)))
                return True, f"хвост снят: дело −{max(1, int(4 * r))}"
            return False, "следы замести не вышло"
    else:
        if kind == "patrol":
            cur = await conn.execute("SELECT t.key FROM territory t JOIN gang g ON g.id = t.owner WHERE t.chat_id = ? AND g.is_ai = 0", (chat_id,))
            keys = [x[0] for x in await cur.fetchall()]
            if not ok:
                return False, "патруль прошёл впустую"
            if keys:
                up, down = max(1, int(6 * r)), max(1, int(3 * r))
                await conn.execute("UPDATE territory SET police = MIN(100, police + ?), control = MAX(0, control - ?) WHERE chat_id = ? AND key = ?", (up, down, chat_id, random.choice(keys)))
                return True, f"район прочёсан: давление полиции +{up}, контроль банды −{down}"
            await police.fund_add(conn, chat_id, int(100 * r))
            return True, f"банд на районах нет, в фонд пошло {int(100 * r)} ₽"
        if kind == "tip":
            if not ok:
                return False, "наводка не подтвердилась"
            cur = await conn.execute("SELECT g.id, g.name FROM gang g LEFT JOIN police_case c ON c.gang_id = g.id WHERE g.chat_id = ? AND g.is_ai = 0 ORDER BY COALESCE(c.pts, 0) DESC LIMIT 1", (chat_id,))
            row = await cur.fetchone()
            if not row:
                return False, "банд для проверки нет"
            pts = max(1, int(5 * r))
            await police.case_add(conn, chat_id, row[0], pts)
            return True, f"наводка подтвердилась: дело на «{row[1]}» +{pts}"
        if kind == "arrest":
            if not ok:
                return False, "розыскник ушёл"
            cur = await conn.execute(
                """SELECT user_id FROM rp_player WHERE chat_id = ? AND user_id > 0 AND wanted >= 2 AND jail_until <= ?
                   AND user_id NOT IN (SELECT user_id FROM police_force WHERE chat_id = ?) ORDER BY RANDOM() LIMIT 1""", (chat_id, now(), chat_id))
            row = await cur.fetchone()
            if not row:
                await police.fund_add(conn, chat_id, int(50 * r))
                return True, "розыскников нет, зато фонд +50 ₽"
            await conn.execute("UPDATE rp_player SET jail_until = ?, esc_used = 0, wanted = MAX(0, wanted - 1) WHERE chat_id = ? AND user_id = ?", (now() + 8 * 60, chat_id, row[0]))
            return True, f"задержан {await _name(conn, chat_id, row[0])}: 8 минут в камере"
        if kind == "stall":
            if not ok:
                return False, "в ларьке всё чисто"
            cur = await conn.execute(
                """SELECT user_id, stall, money FROM rp_player WHERE chat_id = ? AND user_id > 0 AND stall >= 1
                   AND user_id NOT IN (SELECT user_id FROM police_force WHERE chat_id = ?) ORDER BY RANDOM() LIMIT 1""", (chat_id, chat_id))
            row = await cur.fetchone()
            if not row:
                return False, "ларьков для проверки нет"
            fine = min(row[2], int(rp.STALL_INCOME[row[1]] * 3 * r))
            if fine > 0:
                await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, chat_id, row[0]))
                await police.fund_add(conn, chat_id, fine)
            return True, f"оштрафован {await _name(conn, chat_id, row[0])}: {fine} ₽ в фонд"
    return False, "ничего не вышло"


async def _reward(conn, a: dict, auto: bool) -> None:
    import gangplus2 as G2
    gain = 2 if a.get("by_uid") else 1
    if a["org"] == "gang":
        await G2._merit(conn, a["chat_id"], a["to_uid"], -1 if auto else gain)
    else:
        delta = -1 if auto else gain
        await conn.execute("UPDATE rp_player SET cop_rep = MAX(0, cop_rep + ?) WHERE chat_id = ? AND user_id = ?", (delta, a["chat_id"], a["to_uid"]))


# ═════════════════════ действия игроков ═════════════════════
async def give(chat_id: int, uid: int, name: str, target, kind: str) -> dict:
    target = int(target)

    async def run(conn):
        import police
        await _ensure(conn)
        org = "gang" if kind in GANG_KINDS else "police" if kind in POLICE_KINDS else ""
        if not org:
            raise GameError("Такого задания нет")
        gid = 0
        god = await _god(conn, chat_id)
        is_god = bool(god.get("uid") == uid)
        is_mayor = bool(uid == await _mayor_uid(conn, chat_id))
        if org == "gang":
            g, role = await _my_gang(conn, chat_id, uid)
            if not g or (role not in ("boss", "deputy") and not is_god):
                raise GameError("Задания даёт босс, зам или Крёстный отец")
            gid = int(god.get("gid") or g["id"]) if is_god else g["id"]
            cur = await conn.execute("SELECT 1 FROM gang_member WHERE gang_id = ? AND user_id = ?", (gid, target))
            if not await cur.fetchone() or target == uid:
                raise GameError("Задание можно дать другому участнику банды")
        else:
            if uid != await police.chief_of(conn, chat_id) and not is_mayor:
                raise GameError("Задания офицерам даёт капитан или мэр")
            if target == uid or not await police.is_officer(conn, chat_id, target):
                raise GameError("Задание можно дать другому офицеру")
        if await _i(conn, f"asgcd:{chat_id}:{uid}") > now():
            raise GameError("Подожди пару минут между заданиями")
        cur = await conn.execute("SELECT 1 FROM assignment WHERE chat_id = ? AND to_uid = ? AND status = 'open'", (chat_id, target))
        if await cur.fetchone():
            raise GameError("У него уже есть задание")
        cur = await conn.execute("SELECT COUNT(*) FROM assignment WHERE chat_id = ? AND org = ? AND gid = ? AND status = 'open'", (chat_id, org, gid))
        if (await cur.fetchone())[0] >= MAX_OPEN_PER_ORG:
            raise GameError(f"Одновременно можно держать не больше {MAX_OPEN_PER_ORG} заданий")
        from utils import now_local as _nl
        dkey = f"asgday:{chat_id}:{uid}:{_nl().strftime('%Y-%m-%d')}"
        if await _i(conn, dkey) >= DAILY_GIVE:
            raise GameError(f"Не больше {DAILY_GIVE} заданий в сутки")
        await _is(conn, dkey, await _i(conn, dkey) + 1)
        await _is(conn, f"asgcd:{chat_id}:{uid}", now() + GIVE_GAP)
        t = now()
        await conn.execute("INSERT INTO assignment (chat_id, org, gid, by_uid, to_uid, kind, created, expires) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chat_id, org, gid, uid, target, kind, t, t + DEADLINE))
        icon, title, _d, _c = _spec(org, kind)
        tn = await _name(conn, chat_id, target)
        if org == "gang":
            who = "Крёстный отец" if is_god else "Босс банды"
        else:
            who = "Мэр" if is_mayor else "Капитан полиции"
        await _say(conn, chat_id, f"📋 {who} {esc(name)} дал задание {esc(tn)}: {icon} {title}. Срок 30 минут (мини-апп → Район). Проигнорирует — отправят автоматом, но шанс ниже.")
        return P3._res(f"📋 Задание «{title}» выдано: {tn} получит его в мини-аппе. Не выполнит за 30 минут — уйдёт автоматом с шансом ×{AUTO_CHANCE}.")
    return await P3._act(chat_id, uid, run)


async def do(chat_id: int, uid: int, name: str, tid) -> dict:
    async def run(conn):
        await _ensure(conn)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM assignment WHERE id = ?", (int(tid),))
        r = await cur.fetchone()
        a = dict(r) if r else None
        if not a or a["chat_id"] != chat_id or a["to_uid"] != uid or a["status"] != "open":
            raise GameError("Такого задания у тебя нет")
        if a["expires"] <= now():
            raise GameError("Срок вышел: задание уже уходит автоматом")
        p = await rp._load(conn, chat_id, uid)
        cost = ENERGY_BY.get(a["kind"], ENERGY)
        rp._need(p, cost)
        p["energy"] -= cost
        await rp._save(conn, chat_id, uid, p)
        ok, text = await _effect(conn, a, False)
        await _reward(conn, a, False)
        await conn.execute("UPDATE assignment SET status = 'done', result = ? WHERE id = ?", (("✅ " if ok else "❌ ") + text, a["id"]))
        icon, title, _d, _c = _spec(a["org"], a["kind"])
        return P3._res(f"{icon} {title}: {text}.", ok, 0)
    return await P3._act(chat_id, uid, run)


async def _auto_one(conn, a: dict) -> None:
    import police
    if not a.get("by_uid"):
        await conn.execute("UPDATE assignment SET status = 'expired', result = 'поручение района пропало' WHERE id = ?", (a["id"],))
        return
    org, uid = a["org"], a["to_uid"]
    if org == "gang":
        cur = await conn.execute("SELECT 1 FROM gang_member WHERE gang_id = ? AND user_id = ?", (a["gid"], uid))
        valid = bool(await cur.fetchone())
    else:
        valid = await police.is_officer(conn, a["chat_id"], uid)
    if not valid:
        await conn.execute("UPDATE assignment SET status = 'expired', result = 'исполнитель ушёл' WHERE id = ?", (a["id"],))
        return
    ok, text = await _effect(conn, a, True)
    await _reward(conn, a, True)
    await conn.execute("UPDATE assignment SET status = 'auto', result = ? WHERE id = ?", (("🤖 ✅ " if ok else "🤖 ❌ ") + text, a["id"]))
    icon, title, _d, _c = _spec(org, a["kind"])
    await _say(conn, a["chat_id"], f"📋 {esc(await _name(conn, a['chat_id'], uid))} проигнорировал задание «{icon} {title}»: за него отправили ИИ из {'отряда' if org == 'police' else 'банды'} с пониженным шансом. Итог: {text}.")


async def tick(conn) -> None:
    try:
        await _ensure(conn)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM assignment WHERE status = 'open' AND expires <= ? LIMIT 20", (now(),))
        for a in [dict(r) for r in await cur.fetchall()]:
            try:
                await _auto_one(conn, a)
            except Exception:
                log.exception("Сбой автоисполнения задания %s", a.get("id"))
                await conn.execute("UPDATE assignment SET status = 'expired' WHERE id = ?", (a["id"],))
        await _sys_tasks(conn)
        await conn.execute("DELETE FROM assignment WHERE status != 'open' AND created < ?", (now() - 3 * 86400,))
    except Exception:
        log.exception("Сбой заданий")


# ═════════════════════ состояние ═════════════════════
async def state(conn, chat_id: int, uid: int) -> dict:
    import police
    await _ensure(conn)
    conn.row_factory = aiosqlite.Row
    t = now()
    out = {"incoming": [], "recent": [], "orgs": [], "auto_chance": int(AUTO_CHANCE * 100), "energy": ENERGY}
    cur = await conn.execute("SELECT * FROM assignment WHERE chat_id = ? AND to_uid = ? AND status = 'open' ORDER BY id", (chat_id, uid))
    for a in [dict(r) for r in await cur.fetchall()]:
        icon, title, desc, _c = _spec(a["org"], a["kind"])
        out["incoming"].append({"id": a["id"], "icon": icon, "title": title, "desc": desc, "by": "Район" if not a["by_uid"] else await _name(conn, chat_id, a["by_uid"]), "left": max(0, a["expires"] - t), "org": a["org"], "sys": not a["by_uid"], "energy": ENERGY_BY.get(a["kind"], ENERGY)})
    g, role = await _my_gang(conn, chat_id, uid)
    god = await _god(conn, chat_id)
    is_god = bool(god.get("uid") == uid)
    is_mayor = bool(uid == await _mayor_uid(conn, chat_id))
    orgs = []
    if g and (role in ("boss", "deputy") or is_god):
        gid = int(god.get("gid") or g["id"]) if is_god else g["id"]
        cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id != ?", (gid, uid))
        orgs.append(("gang", gid, [r[0] for r in await cur.fetchall()]))
    if uid == await police.chief_of(conn, chat_id) or is_mayor:
        cur = await conn.execute("SELECT user_id FROM police_force WHERE chat_id = ? AND user_id != ?", (chat_id, uid))
        orgs.append(("police", 0, [r[0] for r in await cur.fetchall()]))
    for org, gid, uids in orgs:
        targets = [{"id": u, "name": await _name(conn, chat_id, u)} for u in uids]
        cur = await conn.execute("SELECT * FROM assignment WHERE chat_id = ? AND org = ? AND gid = ? AND by_uid = ? ORDER BY id DESC LIMIT 8", (chat_id, org, gid, uid))
        rows = [dict(r) for r in await cur.fetchall()]
        hist = []
        for a in rows:
            icon, title, _d, _c = _spec(org, a["kind"])
            hist.append({"to": await _name(conn, chat_id, a["to_uid"]), "icon": icon, "title": title, "status": a["status"], "left": max(0, a["expires"] - t) if a["status"] == "open" else 0, "result": a["result"]})
        orgs_kinds = [{"key": k, "icon": v[0], "title": v[1], "desc": v[2]} for k, v in KINDS[org].items()]
        out["orgs"].append({"org": org, "targets": targets, "kinds": orgs_kinds, "history": hist, "wait": max(0, await _i(conn, f"asgcd:{chat_id}:{uid}") - t)})
    return out


async def _sys_tasks(conn) -> None:
    """Каждые 25 минут район сам предлагает одному участнику каждой банды и одному офицеру короткое поручение (только вручную, без автоотправки)."""
    from utils import now_local
    cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
    day = now_local().strftime("%Y-%m-%d")
    for (chat_id,) in await cur.fetchall():
        if await _i(conn, f"asgsys:{chat_id}") > now() - SYS_GAP:
            continue
        await _is(conn, f"asgsys:{chat_id}", now())
        pools = []
        cur2 = await conn.execute("SELECT g.id FROM gang g WHERE g.chat_id = ? AND g.is_ai = 0", (chat_id,))
        for (gid,) in await cur2.fetchall():
            cur3 = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id > 0", (gid,))
            pools.append(("gang", gid, [r[0] for r in await cur3.fetchall()]))
        import police
        chief = await police.chief_of(conn, chat_id)
        cur3 = await conn.execute("SELECT user_id FROM police_force WHERE chat_id = ? AND user_id > 0", (chat_id,))
        pools.append(("police", 0, [r[0] for r in await cur3.fetchall() if r[0] != chief]))
        for org, gid, uids in pools:
            random.shuffle(uids)
            for u in uids:
                cur4 = await conn.execute("SELECT 1 FROM assignment WHERE chat_id = ? AND to_uid = ? AND status = 'open'", (chat_id, u))
                if await cur4.fetchone():
                    continue
                dk = f"sysd:{chat_id}:{u}:{day}"
                if await _i(conn, dk) >= SYS_DAILY:
                    continue
                await _is(conn, dk, await _i(conn, dk) + 1)
                kind = random.choice(list(KINDS[org]))
                t = now()
                await conn.execute("INSERT INTO assignment (chat_id, org, gid, by_uid, to_uid, kind, created, expires) VALUES (?, ?, ?, 0, ?, ?, ?, ?)", (chat_id, org, gid, u, kind, t, t + SYS_DEADLINE))
                break
