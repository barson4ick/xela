"""Полиция района (анти-ОПГ): начальник, офицеры, дела на банды, облавы, прослушка, блокпосты, патрули, взятки."""
import logging
import os
import random
import time
from typing import Optional

import aiosqlite

import gang as G
import gangplus as GP
import gangplus2 as G2
import gangplus3 as P3
import gangplus5 as P5
import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc

log = logging.getLogger(__name__)

OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # владелец проекта: получает ежедневный отчёт экономики, постов не занимает
CHIEF_ID = 0  # жёсткого капитана нет: капитан — kv captain:{chat} (мэр назначает, иначе ИИ)
CAPTAIN_ID = CHIEF_ID
MAX_OFFICERS = 6
CASE_CAP, CASE_RAID, CASE_BIG = 120, 25, 100
CASE_PTS = {"crime": 1, "heist": 6, "bigheist": 15, "jail": 2, "agent": 10, "rheist": 6}
FUND_DAILY, SALARY = 300, 100
RAID_COST, WIRETAP_COST, POST_COST = 800, 400, 300
RAID_CD, WIRETAP_CD, POST_CD, BIG_CD = 10 * 60, 10 * 60, 10 * 60, 10 * 60
PATROL_ENERGY, PATROL_CD, ARREST_ENERGY, ARREST_CD = 15, 10 * 60, 20, 30 * 60
PATROL_DISTRICT_COST, PATROL_DISTRICT_CD = 300, 10 * 60
SHIFT_ENERGY, SHIFT_CD = 10, 10 * 60
SABOTAGE_QUIET_COST, SABOTAGE_CD, SABOTAGE_ENERGY = 500, 20 * 60, 20


def now() -> int:
    return int(time.time())


async def _kget(conn, key: str) -> int:
    return int(await gangwar.kget(conn, key) or 0)


async def _kset(conn, key: str, val) -> None:
    await gangwar.kset(conn, key, str(val))


async def ensure_captain(conn, chat_id: int) -> None:
    """Звания в полиции: капитан задаётся в kv captain:{chat} (назначает мэр, иначе ИИ)."""
    cur = await conn.execute("SELECT 1 FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, CAPTAIN_ID))
    if await cur.fetchone():
        await conn.execute("INSERT OR IGNORE INTO police_force (chat_id, user_id, rank, joined) VALUES (?, ?, 'officer', ?)", (chat_id, CAPTAIN_ID, now()))
    try:
        cid = int(await gangwar.kget(conn, f"captain:{chat_id}") or 0)
    except Exception:
        cid = 0
    await conn.execute("UPDATE police_force SET rank = CASE WHEN user_id = ? THEN 'captain' ELSE 'officer' END WHERE chat_id = ?", (cid, chat_id))


async def chief_of(conn, chat_id: int) -> Optional[int]:
    """Капитан полиции: назначен мэром и всё ещё служит; иначе должность свободна (None)."""
    await ensure_captain(conn, chat_id)
    try:
        cid = int(await gangwar.kget(conn, f"captain:{chat_id}") or 0)
    except Exception:
        cid = 0
    if cid != 0 and await is_officer(conn, chat_id, cid):
        return cid
    return None


async def is_officer(conn, chat_id: int, uid: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM police_force WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    return bool(await cur.fetchone())


async def _role(conn, chat_id: int, uid: int) -> str:
    if uid == await chief_of(conn, chat_id):
        return "chief"
    return "officer" if await is_officer(conn, chat_id, uid) else ""


async def _need_cop(conn, chat_id: int, uid: int, chief_only: bool = False) -> str:
    role = await _role(conn, chat_id, uid)
    if not role or (chief_only and role != "chief"):
        if chief_only and role and not await chief_of(conn, chat_id):
            raise GameError("Должность капитана свободна: капитана назначает мэр")
        raise GameError("Это может только капитан полиции" if chief_only else "Ты не служишь в полиции")
    return role


async def fund(conn, chat_id: int) -> int:
    return await _kget(conn, f"pfund:{chat_id}")


async def fund_add(conn, chat_id: int, amount: int) -> None:
    await _kset(conn, f"pfund:{chat_id}", max(0, await fund(conn, chat_id) + amount))


async def _case(conn, chat_id: int, gid: int) -> dict:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM police_case WHERE chat_id = ? AND gang_id = ?", (chat_id, gid))
    r = await cur.fetchone()
    if r:
        return dict(r)
    await conn.execute("INSERT INTO police_case (chat_id, gang_id, pts, wiretap_until, updated) VALUES (?, ?, 0, 0, ?)", (chat_id, gid, now()))
    return {"chat_id": chat_id, "gang_id": gid, "pts": 0.0, "wiretap_until": 0, "updated": now()}


async def case_add(conn, chat_id: int, gid: int, pts: float) -> None:
    g = await gangwar._gang_row(conn, gid)
    if not g or int(g.get("is_ai") or 0) >= 2:
        return
    c = await _case(conn, chat_id, gid)
    if c["wiretap_until"] > now() and pts > 0:
        pts *= 1.5
    if pts > 0:
        import charter
        if await charter.chartered(conn, gid):
            pts *= charter.CASE_MULT
    await conn.execute("UPDATE police_case SET pts = MAX(0, MIN(?, pts + ?)), updated = ? WHERE chat_id = ? AND gang_id = ?", (CASE_CAP, pts, now(), chat_id, gid))


async def event(conn, chat_id: int, uid: int, kind: str) -> None:
    """Улики из жизни банды (вызывается из журнала событий)."""
    pts = CASE_PTS.get(kind)
    if not pts:
        return
    cur = await conn.execute("SELECT gang_id FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    row = await cur.fetchone()
    if row:
        await case_add(conn, chat_id, row[0], pts)


async def heist_penalty(conn, gid: int) -> float:
    cur = await conn.execute("SELECT wiretap_until FROM police_case WHERE gang_id = ?", (gid,))
    r = await cur.fetchone()
    return -0.05 if r and r[0] > now() else 0.0


async def patrol_penalty(conn, chat_id: int) -> float:
    cur = await conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE ? AND CAST(value AS INTEGER) > ?", (f"ppatrol:{chat_id}:%", now() - 3600))
    return min(0.12, 0.03 * (await cur.fetchone())[0])


async def _own_gang(conn, chat_id: int, uid: int) -> int:
    cur = await conn.execute("SELECT gang_id FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return r[0] if r else 0


async def _player_gang(conn, chat_id: int, gid: int) -> dict:
    g = await gangwar._gang_row(conn, gid)
    if not g or g["chat_id"] != chat_id or int(g.get("is_ai") or 0) >= 2:
        raise GameError("Такой банды нет")
    return g


# ═════════════════════ служба ═════════════════════
async def join_force(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        if await _role(conn, chat_id, uid):
            raise GameError("Ты уже в полиции")
        if await _own_gang(conn, chat_id, uid):
            raise GameError("Бандит в полицию не пойдёт: сначала выйди из ОПГ")
        cur = await conn.execute("SELECT COUNT(*) FROM police_force WHERE chat_id = ?", (chat_id,))
        if (await cur.fetchone())[0] >= MAX_OFFICERS:
            raise GameError(f"В штате не больше {MAX_OFFICERS} офицеров")
        p = await rp._load(conn, chat_id, uid)
        p["cop_rep"] = max(p.get("cop_rep", 0), 10)
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("INSERT INTO police_force (chat_id, user_id, rank, joined) VALUES (?, ?, 'officer', ?)", (chat_id, uid, now()))
        await gangwar.say(conn, chat_id, f"🚓 {esc(name)} принят в полицию района. Присягу принял капитан.")
        return P3._res("🚓 Ты офицер полиции: патрули, аресты, зарплата 100 ₽ в сутки. Бандиты тебя не полюбят.")
    return await P3._act(chat_id, uid, run)


async def leave_force(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        if uid == CHIEF_ID:
            raise GameError("Мэр служит в полиции постоянно")
        if not await is_officer(conn, chat_id, uid):
            raise GameError("Ты не служишь в полиции")
        if uid == await chief_of(conn, chat_id):
            await gangwar.kset(conn, f"captain:{chat_id}", "")
        await conn.execute("DELETE FROM police_force WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        return P3._res("Ты сдал значок.", False)
    return await P3._act(chat_id, uid, run)


async def dismiss(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        await _need_cop(conn, chat_id, uid, True)
        if target == CAPTAIN_ID:
            raise GameError("Мэра нельзя уволить из полиции")
        if target == uid:
            raise GameError("Себя уволить нельзя: сложи полномочия в мини-аппе")
        await conn.execute("DELETE FROM police_force WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        return P3._res("Офицер уволен.", False)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ патруль и аресты ═════════════════════
async def patrol(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        await _need_cop(conn, chat_id, uid)
        if await _kget(conn, f"pcd:patrol:{chat_id}:{uid}") > now():
            raise GameError("Ты только что патрулировал: следующий обход позже")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, PATROL_ENERGY)
        p["energy"] -= PATROL_ENERGY
        await _kset(conn, f"pcd:patrol:{chat_id}:{uid}", now() + PATROL_CD)
        await _kset(conn, f"ppatrol:{chat_id}:{uid}", now())
        cur = await conn.execute(
            """SELECT r.user_id, r.wanted, r.money FROM rp_player r WHERE r.chat_id = ? AND r.user_id != ? AND r.wanted >= 1 AND r.jail_until < ? AND r.user_id > 0
               AND r.user_id NOT IN (SELECT user_id FROM police_force WHERE chat_id = ?) AND r.user_id != ?""", (chat_id, uid, now(), chat_id, CHIEF_ID))
        rows = await cur.fetchall()
        text, good, delta = "🚓 Обход закончен: тихо. Одно твоё присутствие отпугивает дела на час (−3% к шансу бандитских дел).", True, 0
        if rows:
            tid, wanted, money = random.choice(rows)
            tn = await P3._uname(conn, chat_id, tid)
            if random.random() < min(0.8, 0.4 + 0.1 * wanted):
                fine = min(money, 100 * wanted)
                await conn.execute("UPDATE rp_player SET money = money - ?, jail_until = ?, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (fine, now() + 8 * 60 * wanted // 2 + 240, chat_id, tid))
                await fund_add(conn, chat_id, fine)
                p["money"] += 100
                p["cop_rep"] = p.get("cop_rep", 0) + 1
                delta = 100
                await event_gang_case(conn, chat_id, tid, 2)
                await gangwar.say(conn, chat_id, f"🚓 Патруль задержал {esc(tn)} (розыск {wanted}⭐): штраф {fine} ₽ в фонд полиции.")
                text = f"🚓 Ты задержал {tn}: награда 100 ₽, очки полицейского +1."
            else:
                text, good = f"🚓 {tn} ушёл дворами. Бывает.", False
        await rp._save(conn, chat_id, uid, p)
        return P3._res(text, good, delta)
    return await P3._act(chat_id, uid, run)


async def event_gang_case(conn, chat_id: int, uid: int, pts: float) -> None:
    gid = await _own_gang(conn, chat_id, uid)
    if gid:
        await case_add(conn, chat_id, gid, pts)


async def arrest(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        await _need_cop(conn, chat_id, uid)
        if await _kget(conn, f"pcd:arrest:{chat_id}:{uid}") > now():
            raise GameError("Задерживать можно раз в час")
        if target in (uid, CHIEF_ID) or await is_officer(conn, chat_id, target):
            raise GameError("Своих не арестовывают")
        tp = await rp._load(conn, chat_id, target)
        gid = await _own_gang(conn, chat_id, target)
        if tp["jail_until"] > now():
            raise GameError("Он уже за решёткой")
        case = await _case(conn, chat_id, gid) if gid else None
        if tp["wanted"] < 3 and not (case and case["pts"] >= 60 and tp["wanted"] >= 1):
            raise GameError("Основания нет: розыск от 3⭐ (или от 1⭐ у члена банды с делом 60+)")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, ARREST_ENERGY)
        p["energy"] -= ARREST_ENERGY
        await _kset(conn, f"pcd:arrest:{chat_id}:{uid}", now() + ARREST_CD)
        tn = await P3._uname(conn, chat_id, target)
        law = (tp.get("lawyer_until") or 0) > now()
        chance = 0.65 - (0.2 if law else 0.0) - 0.03 * (tp.get("guard") or 0)
        if random.random() < chance:
            fine = min(tp["money"], 200)
            await conn.execute("UPDATE rp_player SET money = money - ?, jail_until = ?, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (fine, now() + 15 * 60, chat_id, target))
            await fund_add(conn, chat_id, fine)
            if gid:
                await case_add(conn, chat_id, gid, 5)
            p["cop_rep"] = p.get("cop_rep", 0) + 2
            await rp._save(conn, chat_id, uid, p)
            await gangwar.say(conn, chat_id, f"🚔 Полиция арестовала {esc(tn)}: 15 минут и штраф {fine} ₽ в фонд.")
            return P3._res(f"🚔 {tn} арестован (очки полицейского +2).")
        await rp._save(conn, chat_id, uid, p)
        return P3._res(f"🏃 {tn} вырвался и скрылся (шанс был ~{int(chance * 100)}%).", False)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ операции начальника ═════════════════════
async def _check_target(conn, chat_id: int, uid: int, gid: int) -> dict:
    g = await _player_gang(conn, chat_id, int(gid))
    if g["id"] == await _own_gang(conn, chat_id, uid):
        raise GameError("Конфликт интересов: свою банду проверять нельзя")
    return g


async def raid(chat_id: int, uid: int, name: str, gid) -> dict:
    async def run(conn):
        await _need_cop(conn, chat_id, uid, True)
        g = await _check_target(conn, chat_id, uid, gid)
        import power
        if await power.has_roof(conn, chat_id, g["id"]):
            raise GameError("Мэр прикрыл эту семью: полиция не трогает её по условиям крыши")
        case = await _case(conn, chat_id, g["id"])
        if case["pts"] < CASE_RAID:
            raise GameError(f"Нет оснований для ордера: дело {int(case['pts'])} из {CASE_RAID}")
        if await _kget(conn, f"pcd:raid:{chat_id}") > now():
            raise GameError("Облавы раз в 10 минут")
        if await fund(conn, chat_id) < RAID_COST:
            raise GameError(f"В фонде полиции нет {RAID_COST} ₽ на операцию")
        if g.get("is_ai"):
            conf = min(2500, int((g["treasury"] or 0) * 0.15))
            await fund_add(conn, chat_id, -RAID_COST)
            await _kset(conn, f"pcd:raid:{chat_id}", now() + RAID_CD)
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (conf, g["id"]))
            await fund_add(conn, chat_id, conf)
            await conn.execute("UPDATE territory SET police = MIN(100, police + 8), control = MAX(0, control - 6) WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
            await conn.execute("UPDATE police_case SET pts = pts * 0.5 WHERE chat_id = ? AND gang_id = ?", (chat_id, g["id"]))
            await P5.rep_add(conn, g["id"], -2, 0)
            await gangwar.say(conn, chat_id, f"🚨 <b>Облава на «{esc(g['name'])}»!</b> ОМОН зачистил точки банды: в фонд полиции изъято {conf} ₽, давление +8, контроль −6.")
            return P3._res(f"🚨 Облава на «{g['name']}»: изъято {conf} ₽ в фонд (−{RAID_COST} ₽ на операцию).")
        if await P3._open_visit(conn, chat_id, 0, g["id"], "omon_gang"):
            raise GameError("К этой банде уже едет ОМОН")
        await fund_add(conn, chat_id, -RAID_COST)
        await _kset(conn, f"pcd:raid:{chat_id}", now() + RAID_CD)
        t = now()
        await conn.execute("INSERT INTO rp_visit (chat_id, user_id, gang_id, kind, amount, created, expires, status) VALUES (?, 0, ?, 'omon_gang', 0, ?, ?, 'open')", (chat_id, g["id"], t, t + P3.OMON_WINDOW))
        await gangwar.say(conn, chat_id, f"🚨 <b>Облава по ордеру капитана полиции!</b> ОМОН выехал к «{esc(g['name'])}»: босс, у вас {P3.OMON_WINDOW // 60} минут (мини-апп → Район → ОПГ).")
        return P3._res(f"🚨 Облава на «{g['name']}» объявлена (−{RAID_COST} ₽ из фонда). Изъятое пойдёт в фонд.")
    return await P3._act(chat_id, uid, run)


async def wiretap(chat_id: int, uid: int, name: str, gid) -> dict:
    async def run(conn):
        await _need_cop(conn, chat_id, uid, True)
        g = await _check_target(conn, chat_id, uid, gid)
        case = await _case(conn, chat_id, g["id"])
        if case["wiretap_until"] > now():
            raise GameError("Прослушка уже стоит")
        if await _kget(conn, f"pcd:wire:{chat_id}") > now():
            raise GameError("Прослушку можно ставить раз в 10 минут")
        if await fund(conn, chat_id) < WIRETAP_COST:
            raise GameError(f"В фонде нет {WIRETAP_COST} ₽")
        await fund_add(conn, chat_id, -WIRETAP_COST)
        await _kset(conn, f"pcd:wire:{chat_id}", now() + WIRETAP_CD)
        await conn.execute("UPDATE police_case SET wiretap_until = ? WHERE chat_id = ? AND gang_id = ?", (now() + 12 * 3600, chat_id, g["id"]))
        await case_add(conn, chat_id, g["id"], 3)
        return P3._res(f"🎧 Прослушка у «{g['name']}» на 12 часов: улики копятся в 1.5 раза быстрее, шанс налётов −5%. Банда об этом не знает.")
    return await P3._act(chat_id, uid, run)


async def checkpoint(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        await _need_cop(conn, chat_id, uid, True)
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        own = await P5._owners(conn, chat_id)
        if not own.get(key):
            raise GameError("Блокпосты ставят только на районах банд")
        g = await gangwar._gang_row(conn, own[key])
        if g and g["id"] == await _own_gang(conn, chat_id, uid):
            raise GameError("Конфликт интересов")
        if await _kget(conn, f"pcd:post:{chat_id}") > now():
            raise GameError("Блокпост раз в 10 минут")
        if await fund(conn, chat_id) < POST_COST:
            raise GameError(f"В фонде нет {POST_COST} ₽")
        await fund_add(conn, chat_id, -POST_COST)
        await _kset(conn, f"pcd:post:{chat_id}", now() + POST_CD)
        await _kset(conn, f"tblock:{chat_id}:{key}", now() + 4 * 3600)
        t = gangwar.TERR[key]
        await gangwar.say(conn, chat_id, f"🚧 Полиция поставила блокпост у района {t[1]} <b>{t[2]}</b>: доход и поставки вдвое меньше на 4 часа.")
        return P3._res(f"🚧 Блокпост у {t[2]} на 4 часа (−{POST_COST} ₽ из фонда).")
    return await P3._act(chat_id, uid, run)


async def shift(chat_id: int, uid: int, name: str) -> dict:
    """Полицейская смена: гарантированная зарплата, порядок и occasional улика."""
    async def run(conn):
        await _need_cop(conn, chat_id, uid)
        if await _kget(conn, f"pcd:shift:{chat_id}:{uid}") > now():
            raise GameError("Смена ещё не началась: отдохни немного")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, SHIFT_ENERGY)
        p["energy"] -= SHIFT_ENERGY
        rank = int(p.get("cop_rep", 0) or 0)
        pay = 80 + min(120, (rank // 10) * 20)
        if await fund(conn, chat_id) >= pay:
            await fund_add(conn, chat_id, -pay)
            source = "из фонда"
        else:
            pay = 40
            source = "из городского бюджета"
        p["money"] += pay
        p["cop_rep"] = rank + 1
        await rp._save(conn, chat_id, uid, p)
        await _kset(conn, f"pcd:shift:{chat_id}:{uid}", now() + SHIFT_CD)
        await _kset(conn, f"porder:{chat_id}", max(0, min(100, await order_base(conn, chat_id) + 2)))
        extra = ""
        if random.random() < 0.35:
            extra = " Нашёл мелкую улику для будущего дела."
        return P3._res(f"💼 Смена закрыта: +{pay} ₽ {source}, +1 очко полиции.{extra}")
    return await P3._act(chat_id, uid, run)


async def order_base(conn, chat_id: int) -> int:
    raw = await _kget(conn, f"porder:{chat_id}")
    return raw if raw > 0 else 40


async def order_of(conn, chat_id: int) -> int:
    """Городской порядок 0–100: чем выше, тем охотнее мэр даёт бюджет."""
    raw = await order_base(conn, chat_id)
    officers = 0
    cur = await conn.execute("SELECT COUNT(*) FROM police_force WHERE chat_id = ?", (chat_id,))
    row = await cur.fetchone()
    officers = int(row[0] or 0) if row else 0
    return max(0, min(100, raw + officers * 2 + await fund(conn, chat_id) // 200))


async def patrol_district(chat_id: int, uid: int, name: str, key: str) -> dict:
    """Полицейский обход конкретного района: давит контроль банды и оставляет патруль на карте."""
    async def run(conn):
        await _need_cop(conn, chat_id, uid)
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        own = await P5._owners(conn, chat_id)
        victim = own.get(key, 0)
        if not victim:
            raise GameError("В этом районе нет банды: патрулировать нечего")
        import power
        if await power.has_roof(conn, chat_id, victim):
            raise GameError("Мэр прикрыл этот район: патруль не трогает семью")
        if victim == await _own_gang(conn, chat_id, uid):
            raise GameError("Свою банду не патрулируем")
        if await _kget(conn, f"pcd:pdist:{chat_id}") > now():
            raise GameError("Патрули уже на маршруте: следующий обход позже")
        if await fund(conn, chat_id) < PATROL_DISTRICT_COST:
            raise GameError(f"В фонде нет {PATROL_DISTRICT_COST} ₽ на обход")
        await fund_add(conn, chat_id, -PATROL_DISTRICT_COST)
        await _kset(conn, f"pcd:pdist:{chat_id}", now() + PATROL_DISTRICT_CD)
        await conn.execute("UPDATE territory SET police = MIN(100, police + 18), control = MAX(0, control - 12) WHERE chat_id = ? AND key = ?", (chat_id, key))
        t = gangwar.TERR[key]
        await gangwar.say(conn, chat_id, f"🚓 Полиция патрулирует район {t[1]} <b>{t[2]}</b>: контроль банды слабеет, давление растёт.")
        return P3._res(f"🚓 Патруль в районе «{t[2]}»: контроль −12, давление полиции +18 (−{PATROL_DISTRICT_COST} ₽ из фонда).")
    return await P3._act(chat_id, uid, run)


async def sabotage(chat_id: int, uid: int, name: str, key: str, mode: str = "quiet") -> dict:
    """ОПГ мешает полиции: подкупить патруль или напасть на него."""
    async def run(conn):
        g, members = await G._mine(conn, chat_id, uid)
        if not g or g.get("is_ai"):
            raise GameError("Ты не в банде игроков")
        role = next((m["role"] for m in members if m["user_id"] == uid), "grunt")
        if role not in ("boss", "deputy"):
            raise GameError("Портить полиции может только босс или зам")
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        own = await P5._owners(conn, chat_id)
        owner = own.get(key, 0)
        if not owner:
            raise GameError("В этом районе нет банды")
        cur = await conn.execute("SELECT police, control FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
        row = await cur.fetchone()
        police = int(row[0] or 0) if row else 0
        control = int(row[1] or 0) if row else 50
        if police < 1:
            raise GameError("Здесь нет полицейского патруля: сначала полиция должна прийти")
        if await _kget(conn, f"gsab:{g['id']}") > now():
            raise GameError("Полиция уже настороже: следующая диверсия позже")
        force = str(mode) == "force"
        if force:
            p = await rp._load(conn, chat_id, uid)
            rp._need(p, SABOTAGE_ENERGY)
            p["energy"] -= SABOTAGE_ENERGY
            await fund_add(conn, chat_id, -200)
            await conn.execute("UPDATE territory SET police = MAX(0, police - 40), control = MIN(100, control + 8) WHERE chat_id = ? AND key = ?", (chat_id, key))
            await _kset(conn, f"porder:{chat_id}", max(0, await order_base(conn, chat_id) - 5))
            await P5.rep_add(conn, g["id"], 0, 2)
            if random.random() < 0.35:
                p["wanted"] = min(5, int(p.get("wanted", 0) or 0) + 1)
                p["wanted_ts"] = now()
                p["jail_until"] = now() + 8 * 60
                p["esc_used"] = 0
                await rp._save(conn, chat_id, uid, p)
                text = "🚔 Патруль оказался готов: тебя взяли. Розыск +1⭐ и 8 минут в тюрьме."
                good = False
            else:
                await rp._save(conn, chat_id, uid, p)
                text = "🥊 Патруль разогнан: давление полиции −40, контроль района +8, из фонда пропало 200 ₽."
                good = True
        else:
            if (g["treasury"] or 0) < SABOTAGE_QUIET_COST:
                raise GameError(f"На подкуп нужно {SABOTAGE_QUIET_COST} ₽ из общака")
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (SABOTAGE_QUIET_COST, g["id"]))
            await conn.execute("UPDATE territory SET police = MAX(0, police - 30), control = MIN(100, control + 5) WHERE chat_id = ? AND key = ?", (chat_id, key))
            await _kset(conn, f"porder:{chat_id}", max(0, await order_base(conn, chat_id) - 3))
            text = "💵 Патруль подкуплен: давление полиции −30, контроль района +5."
            good = True
        await _kset(conn, f"gsab:{g['id']}", now() + SABOTAGE_CD)
        t = gangwar.TERR[key]
        await gangwar.say(conn, chat_id, f"🕶 «{esc(g['name'])}» мешает полиции в районе {t[1]} <b>{t[2]}</b>: {'патруль разогнан' if force else 'патруль подкуплен'}.")
        return P3._res(text, good)
    return await P3._act(chat_id, uid, run)


async def big_case(chat_id: int, uid: int, name: str, gid) -> dict:
    async def run(conn):
        await _need_cop(conn, chat_id, uid, True)
        g = await _check_target(conn, chat_id, uid, gid)
        import power
        if await power.has_roof(conn, chat_id, g["id"]):
            raise GameError("Мэр прикрыл эту семью: суд и облава отменяются")
        case = await _case(conn, chat_id, g["id"])
        if case["pts"] < CASE_BIG:
            raise GameError(f"Дело не готово для суда: {int(case['pts'])} из {CASE_BIG}")
        if await _kget(conn, f"pcd:big:{chat_id}") > now():
            raise GameError("Громкое дело раз в 10 минут")
        await _kset(conn, f"pcd:big:{chat_id}", now() + BIG_CD)
        t = now()
        jailed = []
        for u in await gangwar._real_members(conn, g["id"]):
            p = await rp._load(conn, chat_id, u)
            if p["wanted"] >= 1 and p["jail_until"] <= t:
                p["jail_until"], p["esc_used"] = t + 25 * 60, 0
                p["money"] -= min(p["money"], 150)
                await rp._save(conn, chat_id, u, p)
                jailed.append(await P3._uname(conn, chat_id, u))
        conf = int((g["treasury"] or 0) * 0.30)
        await conn.execute("UPDATE gang SET treasury = treasury - ?, last_heist = ? WHERE id = ?", (conf, t + 12 * 3600, g["id"]))
        await fund_add(conn, chat_id, conf)
        cops = [r[0] for r in await (await conn.execute("SELECT user_id FROM police_force WHERE chat_id = ?", (chat_id,))).fetchall()]
        share = conf // max(1, len(cops) + 1)
        for cu in cops:
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (share, chat_id, cu))
        await _kset(conn, f"porder:{chat_id}", max(0, min(100, await order_base(conn, chat_id) + 10)))
        await conn.execute("UPDATE police_case SET pts = 30, wiretap_until = 0 WHERE chat_id = ? AND gang_id = ?", (chat_id, g["id"]))
        await P5.rep_add(conn, g["id"], -5, 0)
        if g.get("is_ai"):
            await conn.execute("UPDATE territory SET police = MIN(100, police + 10), control = MAX(0, control - 10) WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        await gangwar.say(conn, chat_id, f"⚖️ <b>Громкое дело против «{esc(g['name'])}»!</b> Арестованы: {', '.join(esc(x) for x in jailed) or 'никого не застали'}. Из общака конфисковано {conf} ₽, налёты запрещены на 12 часов.")
        return P3._res(f"⚖️ Громкое дело закрыто: арестов {len(jailed)}, конфисковано {conf} ₽ в фонд, премии офицерам по {share} ₽.")
    return await P3._act(chat_id, uid, run)


# ═════════════════════ взятки ═════════════════════
async def bribe_offer(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount or 0)

    async def run(conn):
        g, members = await G._mine(conn, chat_id, uid)
        if not g or g.get("is_ai") or next(m["role"] for m in members if m["user_id"] == uid) != "boss":
            raise GameError("Взятку предлагает босс банды")
        if not 300 <= amount <= 2000 or (g["treasury"] or 0) < amount:
            raise GameError("Взятка от 300 до 2000 ₽ из общака")
        if not await chief_of(conn, chat_id):
            raise GameError("Капитана полиции нет в этом чате")
        cur = await conn.execute("SELECT 1 FROM police_bribe WHERE chat_id = ? AND gang_id = ? AND status = 'open'", (chat_id, g["id"]))
        if await cur.fetchone():
            raise GameError("Предложение уже ждёт ответа")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (amount, g["id"]))
        await conn.execute("INSERT INTO police_bribe (chat_id, gang_id, amount, status, created, expires) VALUES (?, ?, ?, 'open', ?, ?)", (chat_id, g["id"], amount, now(), now() + 12 * 3600))
        return P3._res(f"💵 Конверт на {amount} ₽ отправлен капитану полиции. Ответ в течение 12 часов; откажет, дело ухудшится.")
    return await P3._act(chat_id, uid, run)


async def bribe_answer(chat_id: int, uid: int, name: str, bid, accept=True) -> dict:
    async def run(conn):
        await _need_cop(conn, chat_id, uid, True)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM police_bribe WHERE id = ? AND chat_id = ? AND status = 'open'", (int(bid), chat_id))
        r = await cur.fetchone()
        if not r or r["expires"] < now():
            raise GameError("Предложение неактуально")
        b = dict(r)
        if accept and b["gang_id"] == await _own_gang(conn, chat_id, uid):
            raise GameError("Свою банду нельзя покрывать открыто")
        if accept:
            p = await rp._load(conn, chat_id, uid)
            p["money"] += b["amount"]
            await rp._save(conn, chat_id, uid, p)
            await conn.execute("UPDATE police_case SET pts = MAX(0, pts - ?) WHERE chat_id = ? AND gang_id = ?", (min(60, b["amount"] // 25), chat_id, b["gang_id"]))
            await conn.execute("UPDATE police_bribe SET status = 'taken' WHERE id = ?", (b["id"],))
            try:
                import justice
                cur2 = await conn.execute("SELECT boss FROM gang WHERE id = ?", (b["gang_id"],))
                row2 = await cur2.fetchone()
                if row2:
                    await justice.report(conn, chat_id, int(row2[0]), "gang", "взятка полиции", 3)
                await justice.report(conn, chat_id, uid, "police", "получил взятку", 3)
            except Exception:
                pass
            return P3._res(f"💵 Конверт принят: +{b['amount']} ₽ лично тебе, дело против банды ослаблено. Но детектив Кротов уже что-то записал…", True, b["amount"])
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (b["amount"], b["gang_id"]))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (b["amount"] // 2, b["gang_id"]))
        await case_add(conn, chat_id, b["gang_id"], 6)
        await conn.execute("UPDATE police_bribe SET status = 'refused' WHERE id = ?", (b["id"],))
        g = await gangwar._gang_row(conn, b["gang_id"])
        await gangwar.say(conn, chat_id, f"🚔 Капитан полиции отказался от взятки «{esc(g['name'] if g else '?')}»: половина конверта вернулась, но дело +6.")
        return P3._res("🚔 Взятка отвергнута и добавлена в дело как улика.", False)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ интерфейс и фон ═════════════════════
def _level(pts: float) -> str:
    return "none" if pts < 25 else "low" if pts < 60 else "high" if pts < CASE_BIG else "critical"


async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    t = now()
    await ensure_captain(conn, chat_id)
    chief = await chief_of(conn, chat_id)
    role = await _role(conn, chat_id, uid)
    cur = await conn.execute("SELECT user_id FROM police_force WHERE chat_id = ?", (chat_id,))
    officers = [{"id": r[0], "name": await P3._uname(conn, chat_id, r[0])} for r in await cur.fetchall()]
    myp = await rp._load(conn, chat_id, uid)
    myrank = int(myp.get("cop_rep", 0) or 0)
    pol = {"chief": {"id": chief, "name": await P3._uname(conn, chat_id, chief)} if chief else None, "fund": await fund(conn, chat_id), "officers": officers, "role": role,
           "shift_pay": 80 + min(120, (myrank // 10) * 20), "shift_wait": max(0, await _kget(conn, f"pcd:shift:{chat_id}:{uid}") - t), "cop_rep": myrank,
           "can_join": not role and not (g and not g.get("is_ai")) and len(officers) < MAX_OFFICERS, "patrols": int((await patrol_penalty(conn, chat_id)) / 0.03 + 0.5),
           "patrol_wait": max(0, await _kget(conn, f"pcd:patrol:{chat_id}:{uid}") - t), "arrest_wait": max(0, await _kget(conn, f"pcd:arrest:{chat_id}:{uid}") - t),
           "costs": {"raid": RAID_COST, "wiretap": WIRETAP_COST, "post": POST_COST}, "case_raid": CASE_RAID, "case_big": CASE_BIG}
    cur = await conn.execute("SELECT DISTINCT chat_id FROM territory WHERE chat_id = ?", (chat_id,))
    own_map = await P5._owners(conn, chat_id)
    own_gid = await _own_gang(conn, chat_id, uid)
    cur = await conn.execute("SELECT id, name, color1, color2, emblem, is_ai FROM gang WHERE chat_id = ?", (chat_id,))
    pol["gangs"] = [{"id": r[0], "name": r[1], "color1": r[2] or "", "color2": r[3] or "", "emblem": r[4] or "", "ai": bool(r[5])} for r in await cur.fetchall()]
    cur = await conn.execute("SELECT key, owner, control, police FROM territory WHERE chat_id = ?", (chat_id,))
    gnames = {x["id"]: x["name"] for x in pol["gangs"]}
    pmap = []
    for key, owner, control, police in await cur.fetchall():
        pmap.append({"key": key, "icon": gangwar.TERR[key][1], "name": gangwar.TERR[key][2],
                     "owner": owner, "owner_name": gnames.get(owner, ""), "control": int(control or 0), "police": int(police or 0),
                     "neigh": [gangwar.TERR[n][1] for n in sorted(P5.NEIGH[key])], "neigh_keys": sorted(P5.NEIGH[key]),
                     "perk": P5.TERR_PERK.get(key, ("", ""))[1], "res": P5.TERR_RES.get(key, ""), "can_patrol": bool(owner and owner != own_gid), "block": await P3.kget(conn, f"tblock:{chat_id}:{key}") > t})
    pol["map"] = pmap
    pol["patrol_district_cost"] = PATROL_DISTRICT_COST
    pol["patrol_district_wait"] = max(0, await _kget(conn, f"pcd:pdist:{chat_id}") - t)
    pol["order"] = await order_of(conn, chat_id)
    pol["captain_id"] = chief or 0
    if role == "chief":
        cur = await conn.execute("SELECT id, name, is_ai FROM gang WHERE chat_id = ? AND is_ai IN (0, 1)", (chat_id,))
        own = await _own_gang(conn, chat_id, uid)
        cases = []
        for gid, gname, gai in await cur.fetchall():
            c = await _case(conn, chat_id, gid)
            cases.append({"gang_id": gid, "name": gname, "ai": bool(gai), "pts": int(c["pts"]), "wiretap": max(0, c["wiretap_until"] - t), "own": gid == own})
        pol["cases"] = sorted(cases, key=lambda x: -x["pts"])
        pol["waits"] = {"raid": max(0, await _kget(conn, f"pcd:raid:{chat_id}") - t), "wire": max(0, await _kget(conn, f"pcd:wire:{chat_id}") - t),
                        "post": max(0, await _kget(conn, f"pcd:post:{chat_id}") - t), "big": max(0, await _kget(conn, f"pcd:big:{chat_id}") - t)}
        cur = await conn.execute("SELECT * FROM police_bribe WHERE chat_id = ? AND status = 'open' AND expires > ?", (chat_id, t))
        gname = {c["gang_id"]: c["name"] for c in cases}
        pol["bribes"] = [{"id": r[0], "gang": gname.get(r[2], "?"), "amount": r[3], "left": r[6] - t} for r in await cur.fetchall()]
        pol["territories"] = [{"key": k, "name": gangwar.TERR[k][2]} for k, o in (await P5._owners(conn, chat_id)).items() if o]
    out["pol"] = pol
    m = out.get("mine")
    if g and m and not g.get("is_ai"):
        c = await _case(conn, chat_id, g["id"])
        cur = await conn.execute("SELECT amount, expires FROM police_bribe WHERE gang_id = ? AND status = 'open' AND expires > ?", (g["id"], t))
        b = await cur.fetchone()
        m["police"] = {"level": _level(c["pts"]), "bribe": {"amount": b[0], "left": b[1] - t} if b else None, "chief": pol["chief"]["name"] if pol["chief"] else ""}


async def tick(conn) -> None:
    try:
        t = now()
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
        for (chat_id,) in await cur.fetchall():
            await ensure_captain(conn, chat_id)
            day = time.strftime("%Y-%m-%d")
            if await gangwar.kget(conn, f"pday:{chat_id}") != day:
                await gangwar.kset(conn, f"pday:{chat_id}", day)
                import gangplus10
                import liveevents
                mods = await liveevents.mods(conn, chat_id)
                inc = int(FUND_DAILY * await gangplus10.police_mult(conn, chat_id) * mods.get("police_fund_mult", 1.0))
                await fund_add(conn, chat_id, inc)
                if await fund(conn, chat_id) > 12000:
                    over = await fund(conn, chat_id) - 12000
                    import balance
                    await balance.welfare_add(conn, chat_id, over // 2)
                    await fund_add(conn, chat_id, -(over // 2))
                cur2 = await conn.execute("SELECT user_id FROM police_force WHERE chat_id = ?", (chat_id,))
                for (u,) in await cur2.fetchall():
                    if await fund(conn, chat_id) >= SALARY:
                        await fund_add(conn, chat_id, -SALARY)
                        await rp._load(conn, chat_id, u)
                        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (SALARY, chat_id, u))
            # бандиты в полиции не служат
            await conn.execute("DELETE FROM police_force WHERE chat_id = ? AND user_id != ? AND user_id IN (SELECT user_id FROM gang_member WHERE chat_id = ?)", (chat_id, CAPTAIN_ID, chat_id))
            # дело: растёт от розыска участников, спадает без активности
            if await P3._roll(conn, f"p11case:{chat_id}", 3600):
                cur2 = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
                for (gid,) in await cur2.fetchall():
                    cur3 = await conn.execute("SELECT COUNT(*) FROM gang_member m JOIN rp_player r ON r.chat_id = m.chat_id AND r.user_id = m.user_id WHERE m.gang_id = ? AND r.wanted >= 3", (gid,))
                    hot = (await cur3.fetchone())[0]
                    await case_add(conn, chat_id, gid, 0.5 * hot - 0.5)
        # взятки без ответа: деньги возвращаются, дело +5
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM police_bribe WHERE status = 'open' AND expires <= ?", (t,))
        for b in [dict(r) for r in await cur.fetchall()]:
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (b["amount"], b["gang_id"]))
            await conn.execute("UPDATE police_bribe SET status = 'expired' WHERE id = ?", (b["id"],))
            await case_add(conn, b["chat_id"], b["gang_id"], 5)
    except Exception:
        log.exception("Сбой фоновых задач полиции")
