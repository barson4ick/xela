"""ОПГ-плюс 3: союзы и предательство между бандами, новые ИИ-банды и районы, общие проекты банды,
неплановые визиты (налоговая, ОМОН), адвокат и редкие гости (Саша Белый, Пабло Эскобар)."""
import logging
import math
import random
import time
from typing import Optional

import aiosqlite

import gang as G
import gangplus as GP
import gangplus2 as G2
import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc, now_local

log = logging.getLogger(__name__)


def now() -> int:
    return int(time.time())


# ═════════════════════ новые ИИ-банды ═════════════════════
AI_GANGS = [
    {"name": "Шиномонтажная мафия", "icon": "🛞", "terr": ("tire", "garages"),
     "members": [(-9011, "Дядя Миша", "boss"), (-9012, "Вася-Домкрат", "deputy"), (-9013, "Колян", "grunt"), (-9014, "Серый", "grunt")],
     "lines": ["🛞 Дядя Миша из «Шиномонтажной мафии» разул чью-то машину: «Балансировка за твой счёт, брат»",
               "🛞 Вася-Домкрат поднял цену на подкачку колёс вдвое. Район пыхтит, но качает",
               "🛞 Колян из «Шиномонтажной мафии» забыл затянуть гайки у чужого джипа. Это случайность. Наверное",
               "🛞 «Шиномонтажная мафия» взяла дань с трёх такси: «Зимняя резина — это не просьба»"]},
    {"name": "Рыбнадзор", "icon": "🎣", "terr": ("embank", "bath"),
     "members": [(-9021, "Капитан Сом", "boss"), (-9022, "Тётя Рыба", "deputy"), (-9023, "Лёха-Удочка", "grunt"), (-9024, "Толян", "grunt")],
     "lines": ["🎣 Капитан Сом из «Рыбнадзора» оштрафовал деда за карася без документов",
               "🎣 Тётя Рыба поймала на набережной двоих рыбаков и взяла «налог на леску»",
               "🎣 Лёха-Удочка из «Рыбнадзора» утверждает, что щука — это государственная тайна",
               "🎣 «Рыбнадзор» открыл в бане закрытую парилку: вход только по рекомендации Сома"]},
]
AI_ICON = {x["name"]: x["icon"] for x in AI_GANGS}
AI_ICON[gangwar.AI_NAME] = "🧓"
for _x in AI_GANGS:
    for _u, _n, _r in _x["members"]:
        G.NPC_NAMES[_u] = _n


async def ensure_ai_gangs(conn, chat_id: int) -> None:
    t = now()
    for key, *_ in gangwar.TERRITORIES:
        await conn.execute("INSERT OR IGNORE INTO territory (chat_id, key, owner, last_pay) VALUES (?, ?, 0, ?)", (chat_id, key, t))
    for spec in AI_GANGS:
        cur = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND name = ? AND is_ai IN (1, 2)", (chat_id, spec["name"]))
        if await cur.fetchone():
            continue
        boss = spec["members"][0][0]
        cur = await conn.execute("INSERT INTO gang (chat_id, name, boss, created, is_ai, last_act) VALUES (?, ?, ?, ?, 1, ?)", (chat_id, spec["name"], boss, t, t))
        gid = cur.lastrowid
        for uid, _n, role in spec["members"]:
            await conn.execute("INSERT OR IGNORE INTO gang_member (chat_id, user_id, gang_id, joined, role) VALUES (?, ?, ?, ?, ?)", (chat_id, uid, gid, t, role))
        held = []
        for key in spec["terr"]:
            cur = await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
            row = await cur.fetchone()
            if row and row[0] == 0:
                await conn.execute("UPDATE territory SET owner = ?, last_pay = ? WHERE chat_id = ? AND key = ?", (gid, t, chat_id, key))
                held.append(f"{gangwar.TERR[key][1]} {gangwar.TERR[key][2]}")
        boss_name = spec["members"][0][1]
        await rp._news(conn, chat_id, f"{spec['icon']} В городе появилась ОПГ «{spec['name']}» ({boss_name}). Держит: {', '.join(held) or 'пока ничего'}")
        await gangwar.say(conn, chat_id, f"{spec['icon']} <b>Новая банда в районе!</b> ОПГ <b>«{spec['name']}»</b>, главный — <b>{boss_name}</b>. "
                                         f"Держат: {', '.join(held) or 'пока ничего'}. С ними можно воевать или заключить союз (мини-апп → Район → ОПГ).")


async def ai_line(conn, ai_id: int) -> str:
    cur = await conn.execute("SELECT name FROM gang WHERE id = ?", (ai_id,))
    row = await cur.fetchone()
    for spec in AI_GANGS:
        if row and spec["name"] == row[0]:
            return random.choice(spec["lines"])
    return random.choice(gangwar.AI_LINES)


# ═════════════════════ союзы и предательство ═════════════════════
PACT_HOURS, MAX_PACTS = 72, 2
STAB_MIN_AGE, STAB_POINTS, STAB_LOOT_PCT, STAB_LOOT_CAP = 6 * 3600, 20, 0.10, 1500
TRAITOR_DAYS, PACT_BREAK_CD = 3, 6 * 3600
AID_COST, AID_POINTS, AID_GAP, ALLY_WAR_POINTS = 500, 15, 3 * 3600, 8
PACT_BONUS = 0.05  # к доходу районов за каждый действующий союз
AI_ACCEPT = 0.7
AI_STAB_MEAN = 36 * 3600


async def _act(chat_id: int, uid: int, fn) -> dict:
    """Общая обёртка: действие под блокировкой, состояние собираем уже снаружи (rp.state сам берёт блокировку)."""
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        res = await fn(conn)
        await conn.commit()
    res["state"] = await rp.state(chat_id, uid)
    return res


def _res(text: str, good: bool = True, delta: int = 0) -> dict:
    return {"text": text, "delta": delta, "good": good}


async def _gang(conn, gid: int) -> Optional[dict]:
    return await gangwar._gang_row(conn, gid)


async def _pact_row(conn, pid: int) -> Optional[dict]:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_pact WHERE id = ?", (int(pid),))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _pacts_of(conn, gid: int, statuses=("active",)) -> list:
    conn.row_factory = aiosqlite.Row
    q = ",".join("?" * len(statuses))
    cur = await conn.execute(f"SELECT * FROM gang_pact WHERE (a = ? OR b = ?) AND status IN ({q})", (gid, gid, *statuses))
    return [dict(r) for r in await cur.fetchall()]


def _other(p: dict, gid: int) -> int:
    return p["b"] if p["a"] == gid else p["a"]


async def pact_between(conn, a: int, b: int, statuses=("active", "proposed")) -> Optional[dict]:
    q = ",".join("?" * len(statuses))
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(f"SELECT * FROM gang_pact WHERE status IN ({q}) AND ((a = ? AND b = ?) OR (a = ? AND b = ?))", (*statuses, a, b, b, a))
    r = await cur.fetchone()
    return dict(r) if r else None


async def check_war(conn, a: int, b: int) -> None:
    """Вызывается при объявлении войны: с союзником воевать нельзя, пока не разорвёшь договор."""
    if await pact_between(conn, a, b, ("active",)):
        raise GameError("Вы в союзе. Сначала разорви договор, а если хватит наглости, нанеси удар в спину")
    import gangplus5
    gx = await _gang(conn, a)
    await gangplus5.check_war_extra(conn, gx["chat_id"], a, b)


async def bonus_mult(conn, gid: int) -> float:
    """Множитель дохода районов: союзы и штаб."""
    n = min(MAX_PACTS, len(await _pacts_of(conn, gid)))
    return 1.0 + PACT_BONUS * n + await proj_effect(conn, gid, "hq")


async def _boss_of(conn, gid: int) -> Optional[int]:
    cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND role = 'boss' LIMIT 1", (gid,))
    r = await cur.fetchone()
    return r[0] if r else None


async def _need_boss(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g:
        raise GameError("Ты не состоишь в ОПГ")
    if next(m["role"] for m in members if m["user_id"] == uid) != "boss":
        raise GameError("Это решает только босс")
    return g, members


async def pact_propose(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)

    async def run(conn):
        g, members = await _need_boss(conn, chat_id, uid)
        other = await _gang(conn, gid)
        if not other or other["chat_id"] != chat_id or gid == g["id"]:
            raise GameError("Такой банды нет")
        if await kget(conn, f"gtraitor:{g['id']}") > now():
            raise GameError("Банду считают предателями: ещё несколько дней с вами никто не заключит союз")
        if await kget(conn, f"gpactcd:{g['id']}") > now():
            raise GameError("После разрыва договора нужно переждать пару часов")
        if await pact_between(conn, g["id"], gid):
            raise GameError("Договор с этой бандой уже есть или ждёт ответа")
        w0 = await gangwar._active_war(conn, g["id"])
        if w0 and gid in (w0["a"], w0["b"]):
            raise GameError("Нельзя заключить союз с тем, с кем воюешь")
        if len(await _pacts_of(conn, g["id"], ("active", "proposed"))) >= MAX_PACTS or len(await _pacts_of(conn, gid, ("active", "proposed"))) >= MAX_PACTS:
            raise GameError(f"У банды может быть не больше {MAX_PACTS} союзов")
        t = now()
        if other["is_ai"]:
            if await kget(conn, f"gpai:{g['id']}:{gid}") > t:
                raise GameError(f"«{other['name']}» пока не хочет разговаривать. Попробуй позже")
            import gangplus5
            if random.random() > await gangplus5.accept_prob(conn, g["id"]):
                await GP._kset(conn, f"gpai:{g['id']}:{gid}", t + 3 * 3600)
                await rp._news(conn, chat_id, f"🤝 «{other['name']}» отказалась от союза с «{g['name']}»")
                return _res(f"«{other['name']}» отказалась: «Мы подумаем». Попробуй снова через 3 часа.", False)
            await conn.execute("INSERT INTO gang_pact (chat_id, a, b, status, created, ends) VALUES (?, ?, ?, 'active', ?, ?)", (chat_id, g["id"], gid, t, t + PACT_HOURS * 3600))
            await rp._news(conn, chat_id, f"🤝 «{g['name']}» заключила союз с «{other['name']}»")
            await gangwar.say(conn, chat_id, f"🤝 <b>Союз!</b> «{esc(g['name'])}» и «{esc(other['name'])}» договорились не воевать и помогать друг другу на {PACT_HOURS} ч. "
                                             "Доход районов у обоих +5%, в войне союзник даёт очки. Но договор — дело хрупкое: предать можно в любой момент.")
            return _res(f"🤝 «{other['name']}» согласилась. Союз на {PACT_HOURS} ч: доход районов +5%, помощь в войне.")
        await conn.execute("INSERT INTO gang_pact (chat_id, a, b, status, created, ends) VALUES (?, ?, ?, 'proposed', ?, ?)", (chat_id, g["id"], gid, t, t + 24 * 3600))
        boss2 = await _boss_of(conn, gid)
        await gangwar.say(conn, chat_id, f"🤝 «{esc(g['name'])}» предлагает союз банде «{esc(other['name'])}». Решает её босс (мини-апп → Район → ОПГ).")
        return _res(f"Предложение отправлено «{other['name']}». Ждём ответа босса до 24 часов.")
    return await _act(chat_id, uid, run)


async def pact_answer(chat_id: int, uid: int, name: str, pid, accept=True) -> dict:
    async def run(conn):
        g, _ = await _need_boss(conn, chat_id, uid)
        p = await _pact_row(conn, pid)
        if not p or p["status"] != "proposed" or p["b"] != g["id"]:
            raise GameError("Это предложение уже неактуально")
        asker = await _gang(conn, p["a"])
        t = now()
        if not accept or p["ends"] < t:
            await conn.execute("UPDATE gang_pact SET status = 'declined' WHERE id = ?", (p["id"],))
            await rp._news(conn, chat_id, f"🤝 «{g['name']}» отклонила союз с «{asker['name'] if asker else '?'}»")
            return _res("Предложение отклонено.", False)
        if len(await _pacts_of(conn, g["id"], ("active",))) >= MAX_PACTS or (asker and len(await _pacts_of(conn, asker["id"], ("active",))) >= MAX_PACTS):
            raise GameError(f"У банды уже {MAX_PACTS} союза")
        await conn.execute("UPDATE gang_pact SET status = 'active', created = ?, ends = ? WHERE id = ?", (t, t + PACT_HOURS * 3600, p["id"]))
        await rp._news(conn, chat_id, f"🤝 «{asker['name']}» и «{g['name']}» заключили союз")
        await gangwar.say(conn, chat_id, f"🤝 <b>Союз!</b> «{esc(asker['name'])}» и «{esc(g['name'])}» теперь вместе на {PACT_HOURS} ч. Доход районов +5%, в войне союзник даёт очки.")
        return _res("🤝 Союз заключён!")
    return await _act(chat_id, uid, run)


async def pact_break(chat_id: int, uid: int, name: str, pid) -> dict:
    async def run(conn):
        g, _ = await _need_boss(conn, chat_id, uid)
        p = await _pact_row(conn, pid)
        if not p or p["status"] not in ("active", "proposed") or g["id"] not in (p["a"], p["b"]):
            raise GameError("Такого договора нет")
        other = await _gang(conn, _other(p, g["id"]))
        await conn.execute("UPDATE gang_pact SET status = 'broken' WHERE id = ?", (p["id"],))
        await GP._kset(conn, f"gpactcd:{g['id']}", now() + PACT_BREAK_CD)
        await rp._news(conn, chat_id, f"🤝 «{g['name']}» мирно разорвала договор с «{other['name'] if other else '?'}»")
        return _res("Договор расторгнут. Новые союзы можно будет заключать через 12 часов.", False)
    return await _act(chat_id, uid, run)


async def pact_aid(chat_id: int, uid: int, name: str, pid) -> dict:
    async def run(conn):
        g, _ = await _need_boss(conn, chat_id, uid)
        p = await _pact_row(conn, pid)
        if not p or p["status"] != "active" or g["id"] not in (p["a"], p["b"]):
            raise GameError("Такого союза нет")
        ally = await _gang(conn, _other(p, g["id"]))
        war = await gangwar._active_war(conn, ally["id"]) if ally else None
        if not war:
            raise GameError("Союзнику сейчас не нужна помощь: он не воюет")
        if await kget(conn, f"gaid:{g['id']}") > now():
            raise GameError("Помогать союзнику можно раз в 6 часов")
        if g["treasury"] < AID_COST:
            raise GameError(f"В общаке нет {AID_COST} ₽ на «подарочные наборы»")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (AID_COST, g["id"]))
        col = "score_a" if war["a"] == ally["id"] else "score_b"
        await conn.execute(f"UPDATE gang_war SET {col} = {col} + ? WHERE id = ?", (AID_POINTS, war["id"]))
        await GP._kset(conn, f"gaid:{g['id']}", now() + AID_GAP)
        await G2.log_event(conn, chat_id, uid, "aid", AID_COST)
        await gangwar.say(conn, chat_id, f"🤝 «{esc(g['name'])}» помогла союзнику «{esc(ally['name'])}» в войне: +{AID_POINTS} очков.")
        return _res(f"🤝 Союзнику отправлена помощь: +{AID_POINTS} очков в его войне (−{AID_COST} ₽ из общака).")
    return await _act(chat_id, uid, run)


async def _stab(conn, chat_id: int, attacker: dict, victim: dict, pact: dict, by_ai: bool) -> None:
    """Удар в спину: договор рвётся, начинается война, у нападающего стартовый перевес."""
    t = now()
    await conn.execute("UPDATE gang_pact SET status = 'broken' WHERE id = ?", (pact["id"],))
    await conn.execute("INSERT INTO gang_war (chat_id, a, b, started, ends, score_a) VALUES (?, ?, ?, ?, ?, ?)",
                       (chat_id, attacker["id"], victim["id"], t, t + gangwar.WAR_HOURS * 3600, STAB_POINTS))
    loot = min(STAB_LOOT_CAP, int((victim.get("treasury") or 0) * STAB_LOOT_PCT))
    if loot:
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (loot, victim["id"]))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (loot, attacker["id"]))
    import gangplus5
    await gangplus5.rep_add(conn, attacker["id"], -25, 10)
    await GP._kset(conn, f"gtraitor:{attacker['id']}", t + TRAITOR_DAYS * 86400)
    for u in await gangwar._real_members(conn, attacker["id"]):
        await G2._merit(conn, chat_id, u, -5)
    await rp._news(conn, chat_id, f"🗡 «{attacker['name']}» нанесла удар в спину союзнику «{victim['name']}»")
    await gangwar.say(conn, chat_id, f"🗡 <b>ПРЕДАТЕЛЬСТВО!</b> «{esc(attacker['name'])}» нарушила союз и напала на «{esc(victim['name'])}». "
                                      f"Старт войны {STAB_POINTS}:0" + (f", из общака жертвы утащили {loot} ₽" if loot else "") +
                                      f". Предателям {TRAITOR_DAYS} дня никто не доверяет, а их авторитет просел.")
    if by_ai and attacker["name"] == gangwar.AI_NAME:
        await gangwar.say(conn, chat_id, "🧓 <i>Ой, внучки, а я вас так любила… Но дружба дружбой, а лавочка врозь!</i>")


async def pact_stab(chat_id: int, uid: int, name: str, pid) -> dict:
    async def run(conn):
        g, _ = await _need_boss(conn, chat_id, uid)
        p = await _pact_row(conn, pid)
        if not p or p["status"] != "active" or g["id"] not in (p["a"], p["b"]):
            raise GameError("Такого союза нет")
        if now() - p["created"] < STAB_MIN_AGE:
            raise GameError("Союз слишком свежий: подожди хотя бы 6 часов, чтобы расслабить союзника")
        victim = await _gang(conn, _other(p, g["id"]))
        if not victim:
            raise GameError("Союзника уже нет")
        if await gangwar._active_war(conn, g["id"]) or await gangwar._active_war(conn, victim["id"]):
            raise GameError("Кто-то из вас уже воюет: ударить в спину можно только в мирное время")
        await _stab(conn, chat_id, g, victim, p, False)
        return _res(f"🗡 Договор нарушен! Война началась со счётом {STAB_POINTS}:0. Предателям {TRAITOR_DAYS} дня никто не верит.", True)
    return await _act(chat_id, uid, run)


async def on_war(conn, chat_id: int, a: int, b: int) -> None:
    """Союзники воюющих банд добавляют очки своей стороне."""
    for side, col in ((a, "score_a"), (b, "score_b")):
        for p in await _pacts_of(conn, side):
            ally = _other(p, side)
            if ally in (a, b):
                continue
            ag = await _gang(conn, ally)
            sg = await _gang(conn, side)
            await conn.execute(f"UPDATE gang_war SET {col} = {col} + ? WHERE status = 'active' AND a = ? AND b = ?", (ALLY_WAR_POINTS, a, b))
            if ag and sg:
                await gangwar.say(conn, chat_id, f"🤝 Союзник «{esc(ag['name'])}» встал на сторону «{esc(sg['name'])}»: +{ALLY_WAR_POINTS} очков.")


async def kget(conn, key: str) -> int:
    return int(await gangwar.kget(conn, key) or 0)


async def _pacts_tick(conn) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_pact WHERE status IN ('active', 'proposed') AND ends <= ?", (t,))
    for p in [dict(r) for r in await cur.fetchall()]:
        await conn.execute("UPDATE gang_pact SET status = ? WHERE id = ?", ("expired" if p["status"] == "active" else "declined", p["id"]))
        if p["status"] == "active":
            ga, gb = await _gang(conn, p["a"]), await _gang(conn, p["b"])
            if ga and gb:
                await rp._news(conn, p["chat_id"], f"🤝 Союз «{ga['name']}» и «{gb['name']}» истёк")
    # ИИ-банды: сами зовут в союз и иногда предают
    cur = await conn.execute("SELECT chat_id, id, name FROM gang WHERE is_ai = 1")
    ais = [dict(r) for r in await cur.fetchall()]
    for ai in ais:
        pacts = await _pacts_of(conn, ai["id"])
        for p in pacts:
            if t - p["created"] < 24 * 3600 or not await _roll(conn, f"p3stab:{p['id']}", AI_STAB_MEAN):
                continue
            victim = await _gang(conn, _other(p, ai["id"]))
            attacker = await _gang(conn, ai["id"])
            if not victim or victim["is_ai"] or await gangwar._active_war(conn, ai["id"]) or await gangwar._active_war(conn, victim["id"]):
                continue
            await _stab(conn, ai["chat_id"], attacker, victim, p, True)
        if len(pacts) < MAX_PACTS and await _roll(conn, f"p3aiask:{ai['id']}", 30 * 3600):
            cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 0", (ai["chat_id"],))
            cand = []
            for gid, gname in await cur.fetchall():
                if await pact_between(conn, ai["id"], gid) or len(await _pacts_of(conn, gid, ("active", "proposed"))) >= MAX_PACTS:
                    continue
                if await kget(conn, f"gtraitor:{gid}") > t or len(await gangwar._real_members(conn, gid)) < 2:
                    continue
                cand.append((gid, gname))
            if cand:
                gid, gname = random.choice(cand)
                await conn.execute("INSERT INTO gang_pact (chat_id, a, b, status, created, ends) VALUES (?, ?, ?, 'proposed', ?, ?)", (ai["chat_id"], ai["id"], gid, t, t + 24 * 3600))
                await gangwar.say(conn, ai["chat_id"], f"🤝 «{esc(ai['name'])}» предлагает союз банде «{esc(gname)}». Босс решает в мини-аппе (Район → ОПГ). Но у них есть привычка предавать…")


# ═════════════════════ общие проекты банды ═════════════════════
PROJECTS = {
    "bar": ("🍸", "Подпольный бар", "каждый час приносит деньги в общак", (3000, 8000, 16000), ("+60 ₽/ч", "+130 ₽/ч", "+220 ₽/ч")),
    "depot": ("📦", "Склад", "добыча налётов банды больше", (3500, 9000, 18000), ("+5%", "+10%", "+15%")),
    "guard": ("🛡", "Охрана", "меньше побоев за долги, ОМОН забирает меньше, легче отбиться", (3000, 7500, 15000), ("побои 35%, ОМОН −25%", "побои 22%, ОМОН −50%", "побои 10%, ОМОН −75%")),
    "hq": ("🏛", "Штаб", "районы приносят больше", (4000, 10000, 20000), ("+10%", "+20%", "+30%")),
}
BAR_INCOME = (0, 60, 130, 220)
DEPOT_BONUS = (0, 0.05, 0.10, 0.15)
HQ_BONUS = (0, 0.10, 0.20, 0.30)
GUARD_BEAT = (0.5, 0.35, 0.22, 0.10)
GUARD_OMON = (0, 0.25, 0.50, 0.75)


async def proj_level(conn, gid: int, key: str) -> int:
    cur = await conn.execute("SELECT level FROM gang_project WHERE gang_id = ? AND key = ?", (gid, key))
    r = await cur.fetchone()
    return r[0] if r else 0


async def proj_effect(conn, gid: int, key: str) -> float:
    lvl = await proj_level(conn, gid, key)
    return {"depot": DEPOT_BONUS, "hq": HQ_BONUS}.get(key, (0, 0, 0, 0))[lvl]


async def beat_chance(conn, gid: int) -> float:
    return GUARD_BEAT[await proj_level(conn, gid, "guard")]


async def proj_build(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        g, _ = await _need_boss(conn, chat_id, uid)
        if key not in PROJECTS:
            raise GameError("Такого проекта нет")
        icon, title, _d, costs, _e = PROJECTS[key]
        lvl = await proj_level(conn, g["id"], key)
        if lvl >= 3:
            raise GameError("Проект уже на максимальном уровне")
        cost = costs[lvl]
        if g["treasury"] < cost:
            raise GameError(f"В общаке {g['treasury']} ₽, а нужно {cost} ₽. Пусть банда сдаёт взносы и ходит на налёты")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, g["id"]))
        await conn.execute("INSERT INTO gang_project (gang_id, key, level) VALUES (?, ?, 1) ON CONFLICT(gang_id, key) DO UPDATE SET level = level + 1", (g["id"], key))
        await GP._kset(conn, f"gprojpay:{g['id']}", now())
        await G2.log_event(conn, chat_id, uid, "project", cost)
        await rp._news(conn, chat_id, f"{icon} «{g['name']}» улучшила проект «{title}» до {lvl + 1} ур.")
        await gangwar.say(conn, chat_id, f"{icon} «{esc(g['name'])}»: проект «{title}» теперь {lvl + 1} уровня! ({PROJECTS[key][4][lvl]})")
        return _res(f"{icon} «{title}» улучшен до {lvl + 1} ур. за {cost} ₽ из общака: {PROJECTS[key][4][lvl]}.")
    return await _act(chat_id, uid, run)


async def _projects_tick(conn) -> None:
    cur = await conn.execute("SELECT gang_id, level FROM gang_project WHERE key = 'bar' AND level > 0")
    for gid, lvl in await cur.fetchall():
        last = await kget(conn, f"gprojpay:{gid}") or now()
        hours = min(12, (now() - last) // 3600)
        if hours >= 1:
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (BAR_INCOME[lvl] * hours, gid))
            await GP._kset(conn, f"gprojpay:{gid}", last + hours * 3600)


# ═════════════════════ адвокат ═════════════════════
LAW_PRICE, LAW_HOURS = 1500, 24
LAW_RELEASE, LAW_RELEASE_GAP = 600, 6 * 3600
LAW_CLEAR, LAW_CLEAR_GAP = 700, 3 * 3600


async def lawyer_active(conn, chat_id: int, uid: int) -> bool:
    cur = await conn.execute("SELECT lawyer_until FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return bool(r and r[0] > now())


async def lawyer_hire(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < LAW_PRICE:
            raise GameError(f"Адвокат берёт {LAW_PRICE} ₽ за сутки")
        start = max(now(), p.get("lawyer_until") or 0)
        await conn.execute("UPDATE rp_player SET money = money - ?, lawyer_until = ? WHERE chat_id = ? AND user_id = ?", (LAW_PRICE, start + LAW_HOURS * 3600, chat_id, uid))
        return _res(f"⚖️ Адвокат нанят на {LAW_HOURS} ч. Налоги −50%, срок за дела −40%, ОМОН не берёт врасплох, плюс услуги ниже.", True, -LAW_PRICE)
    return await _act(chat_id, uid, run)


async def lawyer_release(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        p = await rp._load(conn, chat_id, uid)
        if (p.get("lawyer_until") or 0) <= now():
            raise GameError("Сначала найми адвоката")
        if p["jail_until"] <= now():
            raise GameError("Ты не в тюрьме")
        if await kget(conn, f"lawrel:{chat_id}:{uid}") > now():
            raise GameError("Адвокат уже вытаскивал тебя недавно: следующий раз через несколько часов")
        if p["money"] < LAW_RELEASE:
            raise GameError(f"Услуга стоит {LAW_RELEASE} ₽")
        await conn.execute("UPDATE rp_player SET money = money - ?, jail_until = 0, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (LAW_RELEASE, chat_id, uid))
        await GP._kset(conn, f"lawrel:{chat_id}:{uid}", now() + LAW_RELEASE_GAP)
        await rp._news(conn, chat_id, f"⚖️ {name} вышел из тюрьмы по ходатайству адвоката")
        return _res(f"⚖️ Адвокат добился освобождения. Ты на свободе (−{LAW_RELEASE} ₽).", True, -LAW_RELEASE)
    return await _act(chat_id, uid, run)


async def lawyer_clear(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        p = await rp._load(conn, chat_id, uid)
        if (p.get("lawyer_until") or 0) <= now():
            raise GameError("Сначала найми адвоката")
        if p["wanted"] < 1:
            raise GameError("Ты и так вне розыска")
        if await kget(conn, f"lawclr:{chat_id}:{uid}") > now():
            raise GameError("Адвокат уже снимал тебе розыск недавно")
        if p["money"] < LAW_CLEAR:
            raise GameError(f"Услуга стоит {LAW_CLEAR} ₽")
        await conn.execute("UPDATE rp_player SET money = money - ?, wanted = MAX(0, wanted - 2), wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (LAW_CLEAR, now(), chat_id, uid))
        await GP._kset(conn, f"lawclr:{chat_id}:{uid}", now() + LAW_CLEAR_GAP)
        return _res(f"⚖️ Адвокат «потерял» пару дел: розыск −2⭐ (−{LAW_CLEAR} ₽).", True, -LAW_CLEAR)
    return await _act(chat_id, uid, run)


# ═════════════════════ неплановые визиты ═════════════════════
TAX_MEAN, TAX_MIN_MONEY, TAX_PCT, TAX_CAP, TAX_WINDOW = 14 * 3600, 2500, 0.10, 7000, 20 * 60
OMON_MEAN, OMON_WINDOW, OMON_CONF, OMON_JAIL_MIN = 30 * 3600, 15 * 60, 0.25, 12
OMON_MAN_MEAN, OMON_MAN_WANTED = 8 * 3600, 4


async def _roll(conn, key: str, mean: float) -> bool:
    t = now()
    last = await kget(conn, key)
    await GP._kset(conn, key, t)
    if not last:
        return False
    dt = min(t - last, 600)
    return random.random() < 1 - math.exp(-dt / mean)


async def _uname(conn, chat_id: int, uid: int) -> str:
    cur = await conn.execute("SELECT username FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return (r[0] if r and r[0] else "Кто-то")


async def _open_visit(conn, chat_id: int, uid: int, gid: int = 0, kind: str = "") -> Optional[dict]:
    conn.row_factory = aiosqlite.Row
    if kind == "tax" or (not gid and uid):
        cur = await conn.execute("SELECT * FROM rp_visit WHERE chat_id = ? AND user_id = ? AND kind = 'tax' AND status = 'open' ORDER BY id DESC LIMIT 1", (chat_id, uid))
    else:
        cur = await conn.execute("SELECT * FROM rp_visit WHERE chat_id = ? AND gang_id = ? AND kind = 'omon_gang' AND status = 'open' ORDER BY id DESC LIMIT 1", (chat_id, gid))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _tax_new(conn, chat_id: int) -> None:
    t = now()
    cur = await conn.execute(
        """SELECT r.user_id, r.money FROM rp_player r JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id
           WHERE r.chat_id = ? AND r.money >= ? AND r.jail_until < ? AND r.user_id > 0""", (chat_id, TAX_MIN_MONEY, t))
    rows = await cur.fetchall()
    rows = [r for r in rows if await kget(conn, f"p3taxlast:{chat_id}:{r[0]}") < t - 8 * 3600]
    if not rows:
        return
    uid, money = random.choice(rows)
    import liveevents
    amount = int(max(300, min(TAX_CAP, int(money * TAX_PCT))) * (await liveevents.mods(conn, chat_id))["tax_mult"])
    await conn.execute("INSERT INTO rp_visit (chat_id, user_id, gang_id, kind, amount, created, expires, status) VALUES (?, ?, 0, 'tax', ?, ?, ?, 'open')", (chat_id, uid, amount, t, t + TAX_WINDOW))
    await GP._kset(conn, f"p3taxlast:{chat_id}:{uid}", t)
    name = await _uname(conn, chat_id, uid)
    await gangwar.say(conn, chat_id, f"🧾 <b>Налоговая нагрянула без предупреждения!</b> К {esc(name)} пришла проверка: доначислено {amount} ₽. "
                                     f"{TAX_WINDOW // 60} минут, чтобы решить вопрос: заплатить, дать на лапу или вызвать адвоката (мини-апп → Район).")


async def _tax_close(conn, v: dict, how: str) -> None:
    await conn.execute("UPDATE rp_visit SET status = 'done', result = ? WHERE id = ?", (how, v["id"]))


async def _omon_pick(conn, chat_id: int) -> Optional[dict]:
    cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
    best = []
    for gid, gname, tr in await cur.fetchall():
        real = await gangwar._real_members(conn, gid)
        if len(real) < 2 or await _open_visit(conn, chat_id, 0, gid, "omon_gang"):
            continue
        import gangplus4
        if await gangplus4.jam_hit(conn, gid):
            await gangwar.say(conn, chat_id, f"🔓 Глушилка взломщика «{esc(gname)}» сбила ОМОН со следа: облава отменена.")
            continue
        if random.random() < min(0.75, 0.25 * await gangplus4._watchers(conn, gid)):
            continue  # ночной дозор заметил ОМОН заранее
        q = ",".join("?" * len(real))
        cur2 = await conn.execute(f"SELECT COALESCE(SUM(wanted), 0) FROM rp_player WHERE chat_id = ? AND user_id IN ({q})", (chat_id, *real))
        w = (await cur2.fetchone())[0]
        if w < 1 and tr < 300:
            continue
        best.append(({"id": gid, "name": gname}, 1 + w))
    if not best:
        return None
    return random.choices([b[0] for b in best], weights=[b[1] for b in best])[0]


async def _omon_new(conn, chat_id: int) -> None:
    g = await _omon_pick(conn, chat_id)
    if not g:
        return
    t = now()
    await conn.execute("INSERT INTO rp_visit (chat_id, user_id, gang_id, kind, amount, created, expires, status) VALUES (?, 0, ?, 'omon_gang', 0, ?, ?, 'open')", (chat_id, g["id"], t, t + OMON_WINDOW))
    await gangwar.say(conn, chat_id, f"🚨 <b>ОМОН у дверей «{esc(g['name'])}»!</b> Босс, у вас {OMON_WINDOW // 60} минут: откупиться из общака, принять бой или позвать адвоката (мини-апп → Район → ОПГ). "
                                     "Иначе общак опустошат, а засветившихся уведут в тюрьму.")


async def _omon_hit(conn, v: dict, mult: float, jail: bool, extra_wanted: bool = False) -> str:
    chat_id = v["chat_id"]
    g = await _gang(conn, v["gang_id"])
    if not g:
        return "банды уже нет"
    guard = GUARD_OMON[await proj_level(conn, g["id"], "guard")]
    conf = int((g["treasury"] or 0) * OMON_CONF * mult * (1 - guard))
    if conf:
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (conf, g["id"]))
        import police
        await police.fund_add(conn, chat_id, conf)
    await conn.execute("UPDATE police_case SET pts = MAX(0, pts - 30) WHERE chat_id = ? AND gang_id = ?", (chat_id, g["id"]))
    jailed = []
    for u in await gangwar._real_members(conn, g["id"]):
        p = await rp._load(conn, chat_id, u)
        changed = False
        if jail and p["wanted"] >= 1 and p["jail_until"] <= now():
            p["jail_until"] = now() + OMON_JAIL_MIN * 60
            p["esc_used"] = 0
            p["money"] -= min(p["money"], 100)
            jailed.append(await _uname(conn, chat_id, u))
            changed = True
        if extra_wanted:
            p["wanted"] = min(rp.MAX_WANTED, p["wanted"] + 1)
            p["wanted_ts"] = now()
            changed = True
        if changed:
            await rp._save(conn, chat_id, u, p)
    text = f"🚨 ОМОН закончил с «{esc(g['name'])}»: из общака изъято {conf} ₽" + (f", задержаны: {', '.join(esc(x) for x in jailed)}" if jailed else ", арестов нет") + "."
    await rp._news(conn, chat_id, f"🚨 ОМОН навестил «{g['name']}»: изъято {conf} ₽, задержано {len(jailed)}")
    await gangwar.say(conn, chat_id, text)
    return text


async def visit_answer(chat_id: int, uid: int, name: str, vid, choice: str) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM rp_visit WHERE id = ? AND chat_id = ?", (int(vid), chat_id))
        r = await cur.fetchone()
        v = dict(r) if r else None
        if not v or v["status"] != "open" or v["expires"] < now():
            raise GameError("Визит уже закончился")
        p = await rp._load(conn, chat_id, uid)
        law = (p.get("lawyer_until") or 0) > now()
        if v["kind"] == "tax":
            if v["user_id"] != uid:
                raise GameError("Это не твоя проверка")
            amt = v["amount"]
            if choice == "pay":
                cost = amt // 2 if law else amt
                if p["money"] < cost:
                    raise GameError(f"Нужно {cost} ₽")
                await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cost, chat_id, uid))
                await _tax_close(conn, v, "pay")
                await rp._news(conn, chat_id, f"🧾 {name} закрыл вопрос с налоговой за {cost} ₽")
                return _res(f"🧾 Налоги уплачены: {cost} ₽" + (" (адвокат сбил вдвое)." if law else "."), True, -cost)
            if choice == "bribe":
                cost = int(amt * 0.6)
                if p["money"] < cost:
                    raise GameError(f"На лапу нужно {cost} ₽")
                await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cost, chat_id, uid))
                await _tax_close(conn, v, "bribe")
                if random.random() < 0.65:
                    await rp._news(conn, chat_id, f"🧾 {name} решил вопрос с налоговой «по-свойски»")
                    return _res(f"🧾 Инспектор улыбнулся и ушёл. Это обошлось в {cost} ₽ вместо {amt} ₽.", True, -cost)
                fine = min(max(0, p["money"] - cost), int(amt * 1.5))
                await conn.execute("UPDATE rp_player SET money = money - ?, wanted = MIN(?, wanted + 1), wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (fine, rp.MAX_WANTED, now(), chat_id, uid))
                await rp._news(conn, chat_id, f"🧾 {name} дал взятку налоговой, но инспектор оказался принципиальным")
                return _res(f"🧾 Инспектор принципиальный! Взятка не принята: штраф {fine} ₽, розыск +1⭐.", False, -(cost + fine))
            if choice == "lawyer":
                if not law:
                    raise GameError("Адвоката нет. Нанять можно в «Имуществе» (1500 ₽ в сутки)")
                await _tax_close(conn, v, "lawyer")
                if random.random() < 0.7:
                    await rp._news(conn, chat_id, f"🧾 Адвокат {name} опроверг претензии налоговой")
                    return _res("⚖️ Адвокат разнёс акт проверки в пух и прах. Платить ничего не нужно!", True)
                cost = min(p["money"], amt // 2)
                await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cost, chat_id, uid))
                return _res(f"⚖️ Адвокат отбил половину: доплатил {cost} ₽.", False, -cost)
            raise GameError("Неизвестное решение")
        # ОМОН у банды
        g, _ = await _need_boss(conn, chat_id, uid)
        if g["id"] != v["gang_id"]:
            raise GameError("Это не твоя банда")
        await conn.execute("UPDATE rp_visit SET status = 'done', result = ? WHERE id = ?", (choice, v["id"]))
        if choice == "bribe":
            cost = max(300, int((g["treasury"] or 0) * 0.20)) // (2 if law else 1)
            if g["treasury"] < cost:
                await conn.execute("UPDATE rp_visit SET status = 'open', result = '' WHERE id = ?", (v["id"],))
                raise GameError(f"В общаке нет {cost} ₽ на откуп")
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, g["id"]))
            if random.random() < 0.85:
                await rp._news(conn, chat_id, f"🚨 «{g['name']}» откупилась от ОМОНа")
                await gangwar.say(conn, chat_id, f"🚨 «{esc(g['name'])}» договорилась с ОМОНом: все остались на свободе.")
                return _res(f"🚨 Командир взял конверт ({cost} ₽ из общака) и увёл людей.", True)
            t = await _omon_hit(conn, v, 0.5, True)
            return _res("🚨 Командир взял конверт и всё равно устроил зачистку. " + t, False)
        if choice == "fight":
            guard = await proj_level(conn, g["id"], "guard")
            n = min(5, len(await gangwar._real_members(conn, g["id"])))
            chance = 0.30 + 0.06 * n + 0.07 * guard
            if random.random() < chance:
                for u in await gangwar._real_members(conn, g["id"]):
                    await G2._merit(conn, chat_id, u, 8)
                await rp._news(conn, chat_id, f"🚨 «{g['name']}» отбилась от ОМОНа")
                await gangwar.say(conn, chat_id, f"🚨 «{esc(g['name'])}» отбилась от ОМОНа! Авторитет всех участников +8.")
                return _res("🔥 Отбились! Авторитет банды +8, ОМОН ушёл ни с чем.", True)
            t = await _omon_hit(conn, v, 1.0, True, extra_wanted=True)
            return _res(f"💥 Не отбились (шанс был ~{int(chance * 100)}%). " + t, False)
        if choice == "lawyer":
            boss_law = law
            if not boss_law:
                await conn.execute("UPDATE rp_visit SET status = 'open', result = '' WHERE id = ?", (v["id"],))
                raise GameError("Нужен адвокат у босса (1500 ₽ в сутки, «Имущество»)")
            t = await _omon_hit(conn, v, 0.4, False)
            return _res("⚖️ Адвокат показал ордер с ошибкой в печати: ареста нет, изъят лишь небольшой «залог». " + t, True)
        await conn.execute("UPDATE rp_visit SET status = 'open', result = '' WHERE id = ?", (v["id"],))
        raise GameError("Неизвестное решение")
    return await _act(chat_id, uid, run)


async def _visits_tick(conn) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM rp_visit WHERE status = 'open' AND expires <= ?", (t,))
    for v in [dict(r) for r in await cur.fetchall()]:
        if v["kind"] == "tax":
            p = await rp._load(conn, v["chat_id"], v["user_id"])
            cost = min(p["money"], int(v["amount"] * (0.65 if (p.get("lawyer_until") or 0) > t else 1.3)))
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cost, v["chat_id"], v["user_id"]))
            await _tax_close(conn, v, "expired")
            name = await _uname(conn, v["chat_id"], v["user_id"])
            await gangwar.say(conn, v["chat_id"], f"🧾 {esc(name)} проигнорировал налоговую, и инспекция списала {cost} ₽ с надбавкой за хамство.")
        else:
            await conn.execute("UPDATE rp_visit SET status = 'done', result = 'expired' WHERE id = ?", (v["id"],))
            await _omon_hit(conn, v, 1.0, True)
    cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player GROUP BY chat_id HAVING COUNT(*) >= 2")
    for (chat_id,) in await cur.fetchall():
        h = now_local().hour
        if not 9 <= h < 23:
            continue
        if await _roll(conn, f"p3tax:{chat_id}", TAX_MEAN):
            await _tax_new(conn, chat_id)
        import gangplus10
        if await _roll(conn, f"p3omon:{chat_id}", OMON_MEAN / await gangplus10.police_mult(conn, chat_id)):
            await _omon_new(conn, chat_id)
        if await _roll(conn, f"p3omonman:{chat_id}", OMON_MAN_MEAN):
            await _omon_man(conn, chat_id)


async def _omon_man(conn, chat_id: int) -> None:
    t = now()
    cur = await conn.execute("SELECT user_id, money, lawyer_until FROM rp_player WHERE chat_id = ? AND wanted >= ? AND jail_until < ? AND user_id > 0", (chat_id, OMON_MAN_WANTED, t))
    rows = await cur.fetchall()
    if not rows:
        return
    uid, money, law = random.choice(rows)
    name = await _uname(conn, chat_id, uid)
    if law and law > t:
        await rp._news(conn, chat_id, f"🚨 ОМОН пришёл за {name}, но адвокат показал бумаги и всех развернул")
        await gangwar.say(conn, chat_id, f"🚨 ОМОН пришёл за {esc(name)}, но у того есть адвокат. Бумаги в порядке, спецназ уехал ни с чем.")
        return
    fine = min(800, int(money * 0.08))
    await conn.execute("UPDATE rp_player SET jail_until = ?, esc_used = 0, money = money - ? WHERE chat_id = ? AND user_id = ?", (t + 10 * 60, fine, chat_id, uid))
    await rp._news(conn, chat_id, f"🚨 ОМОН задержал {name} (в розыске): 10 мин, штраф {fine} ₽")
    await gangwar.say(conn, chat_id, f"🚨 ОМОН задержал {esc(name)}: слишком много звёзд в розыске. 10 минут в камере и штраф {fine} ₽. Адвокат бы помог…")


# ═════════════════════ редкие гости ═════════════════════
GUESTS = {
    "sasha": {
        "name": "Саша Белый", "icon": "🕴", "minutes": 45, "mean": 40 * 3600,
        "intro": "Зашёл на огонёк в Район. Щедрый, но недолго: уедет через {m} мин.",
        "offers": {
            "sit": ("🍽 Сесть за стол", "Бесплатно: энергия до максимума и 300 ₽ на чай", 0),
            "share": ("🤝 Войти в долю", "Вложи 1500 ₽: 75% шанс получить 4500 ₽, 25% Саша «забыл»", 1500),
            "tip": ("📝 Взять наводку", "Розыск обнуляется, авторитет +15 в банде (без банды: +1500 ₽)", 0),
        },
        "arrive": "🕴 <b>Саша Белый в районе!</b> Заехал на чёрном «Мерседесе», раздаёт приглашения за стол. Он здесь всего {m} минут: мини-апп → Район, там карточка гостя. Каждому по одному предложению.",
        "leave": "🕴 Саша Белый уехал так же внезапно, как и приехал. Говорят, оставил за столом нехилые чаевые.",
    },
    "pablo": {
        "name": "Пабло Эскобар", "icon": "🌴", "minutes": 40, "mean": 70 * 3600,
        "intro": "Опасный гость из Колумбии. Предложения щедрые, но у них есть цена: уедет через {m} мин.",
        "offers": {
            "plata": ("💰 Плата", "+5000 ₽ наличными, но розыск +2⭐ и 35% риск быть побитым (30 мин)", 0),
            "plomo": ("🔫 Свинец", "Отказаться красиво: авторитет +20 в банде (без банды: +1000 ₽), очки полицейского +3", 0),
            "cargo": ("📦 Принять груз", "Вложи 2500 ₽: 45% шанс получить 8000 ₽, иначе груз «ушёл», розыск +1⭐", 2500),
        },
        "arrive": "🌴 <b>Пабло Эскобар прибыл в район!</b> Предлагает «плату или свинец». Он здесь {m} минут: мини-апп → Район, карточка гостя. По одному предложению на человека. Отказаться тоже можно, но красиво.",
        "leave": "🌴 Пабло Эскобар улетел на частном самолёте. На прощание сказал: «Район у вас смешной, буду скучать».",
    },
}


async def spawn_guest(conn, chat_id: int, key: str, minutes: Optional[int] = None) -> int:
    spec = GUESTS[key]
    t = now()
    mins = minutes or spec["minutes"]
    cur = await conn.execute("INSERT INTO rp_guest (chat_id, key, started, ends, status) VALUES (?, ?, ?, ?, 'open')", (chat_id, key, t, t + mins * 60))
    await rp._news(conn, chat_id, f"{spec['icon']} В районе гость: {spec['name']}!")
    await gangwar.say(conn, chat_id, spec["arrive"].format(m=mins))
    return cur.lastrowid


async def _active_guest(conn, chat_id: int) -> Optional[dict]:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM rp_guest WHERE chat_id = ? AND status = 'open' AND ends > ? ORDER BY id DESC LIMIT 1", (chat_id, now()))
    r = await cur.fetchone()
    return dict(r) if r else None


async def guest_take(chat_id: int, uid: int, name: str, offer: str) -> dict:
    async def run(conn):
        gst = await _active_guest(conn, chat_id)
        if not gst:
            raise GameError("Гость уже уехал")
        spec = GUESTS[gst["key"]]
        if offer not in spec["offers"]:
            raise GameError("Такого предложения нет")
        cur = await conn.execute("SELECT 1 FROM rp_guest_claim WHERE guest_id = ? AND user_id = ?", (gst["id"], uid))
        if await cur.fetchone():
            raise GameError("Ты уже воспользовался предложением гостя")
        title, _d, cost = spec["offers"][offer]
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < cost:
            raise GameError(f"Нужно {cost} ₽")
        cur = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        in_gang = bool(await cur.fetchone())
        await conn.execute("INSERT INTO rp_guest_claim (guest_id, user_id, offer, ts) VALUES (?, ?, ?, ?)", (gst["id"], uid, offer, now()))
        p["money"] -= cost
        good, delta, text = True, 0, ""
        after_beat = 0
        if offer == "sit":
            p["energy"] = rp.max_energy(p["home"])
            p["money"] += 300
            text, delta = "🍽 Саша налил горячего: энергия полная, плюс 300 ₽ «на чай».", 300
        elif offer == "share":
            if random.random() < 0.75:
                p["money"] += 4500
                text, delta = "🤝 Дело выгорело! Саша вернул вложение с процентами: +4500 ₽ (чистыми +3000).", 3000
            else:
                text, delta, good = "🤝 Саша «забыл» про тебя и уехал. Вложение 1500 ₽ пропало.", -1500, False
        elif offer == "tip":
            p["wanted"], p["wanted_ts"] = 0, now()
            if in_gang:
                await G2._merit(conn, chat_id, uid, 15)
                text = "📝 Наводка пригодилась: розыск обнулён, авторитет в банде +15."
            else:
                p["money"] += 1500
                text, delta = "📝 Розыск обнулён, и без банды Саша сунул 1500 ₽ «за смекалку».", 1500
        elif offer == "plata":
            p["money"] += 5000
            p["wanted"], p["wanted_ts"] = min(rp.MAX_WANTED, p["wanted"] + 2), now()
            text, delta = "💰 Деньги взяты: +5000 ₽. Но розыск +2⭐: такие деньги не прощают.", 5000
            if random.random() < 0.35:
                after_beat = 30
                text += " Помощники Пабло «объяснили» правила: тебя побили на 30 минут."
                good = False
        elif offer == "plomo":
            p["cop_rep"] = p.get("cop_rep", 0) + 3
            if in_gang:
                await G2._merit(conn, chat_id, uid, 20)
                text = "🔫 Красивый отказ. Пабло уважительно кивнул: авторитет в банде +20, очки полицейского +3."
            else:
                p["money"] += 1000
                text, delta = "🔫 Красивый отказ. Пабло уважительно кивнул: +1000 ₽ за смелость и очки полицейского +3.", 1000
        elif offer == "cargo":
            if random.random() < 0.45:
                p["money"] += 8000
                text, delta = "📦 Груз дошёл! Пабло расплатился: +8000 ₽ (чистыми +5500).", 5500
            else:
                p["wanted"], p["wanted_ts"] = min(rp.MAX_WANTED, p["wanted"] + 1), now()
                text, delta, good = "📦 Груз «ушёл» по дороге. Вложение 2500 ₽ потеряно, розыск +1⭐.", -2500, False
        await rp._save(conn, chat_id, uid, p)
        if after_beat:
            await G2._beat(conn, chat_id, uid, after_beat)
        await rp._news(conn, chat_id, f"{spec['icon']} {name} принял предложение гостя: {title}")
        return _res(text, good, delta)
    return await _act(chat_id, uid, run)


async def _guests_tick(conn) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM rp_guest WHERE status = 'open' AND ends <= ?", (t,))
    for gst in [dict(r) for r in await cur.fetchall()]:
        await conn.execute("UPDATE rp_guest SET status = 'done' WHERE id = ?", (gst["id"],))
        await gangwar.say(conn, gst["chat_id"], GUESTS[gst["key"]]["leave"])
    cur = await conn.execute("SELECT chat_id FROM rp_player GROUP BY chat_id HAVING COUNT(*) >= 2")
    for (chat_id,) in await cur.fetchall():
        if not 11 <= now_local().hour < 23 or await _active_guest(conn, chat_id):
            continue
        for key, spec in GUESTS.items():
            if await _roll(conn, f"p3guest:{key}:{chat_id}", spec["mean"]):
                await spawn_guest(conn, chat_id, key)
                break


# ═════════════════════ данные для интерфейса ═════════════════════
async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    t = now()
    for x in out.get("gangs", []):
        x["icon"] = AI_ICON.get(x["name"], "🕴" if not x.get("is_ai") else "🧓")
    p = await rp._load(conn, chat_id, uid)
    law_left = max(0, (p.get("lawyer_until") or 0) - t)
    p3 = {"lawyer": {"active": law_left > 0, "left": law_left, "price": LAW_PRICE, "hours": LAW_HOURS,
                     "release_cost": LAW_RELEASE, "release_wait": max(0, await kget(conn, f"lawrel:{chat_id}:{uid}") - t),
                     "clear_cost": LAW_CLEAR, "clear_wait": max(0, await kget(conn, f"lawclr:{chat_id}:{uid}") - t)}}
    visit = await _open_visit(conn, chat_id, uid, 0, "tax")
    if visit and visit["expires"] < t:
        visit = None
    kind = None
    if not visit and g and my_role == "boss":
        visit = await _open_visit(conn, chat_id, 0, g["id"], "omon_gang")
        if visit and visit["expires"] < t:
            visit = None
    if visit:
        left = visit["expires"] - t
        if visit["kind"] == "tax":
            amt = visit["amount"]
            half = amt // 2
            p3["visit"] = {"id": visit["id"], "kind": "tax", "left": left, "title": "🧾 Налоговая проверка",
                           "text": f"Инспектор доначислил {amt} ₽. Решай быстро.",
                           "options": [{"key": "pay", "label": f"Заплатить {half if law_left else amt} ₽" + (" (со скидкой адвоката)" if law_left else "")},
                                       {"key": "bribe", "label": f"Дать на лапу {int(amt * 0.6)} ₽ (65%)"},
                                       {"key": "lawyer", "label": "Вызвать адвоката (70%)", "disabled": not law_left}]}
        else:
            tr = g["treasury"] or 0
            n = min(5, len(await gangwar._real_members(conn, g["id"])))
            guard = await proj_level(conn, g["id"], "guard")
            cost = max(300, int(tr * 0.20)) // (2 if law_left else 1)
            p3["visit"] = {"id": visit["id"], "kind": "omon", "left": left, "title": "🚨 ОМОН у дверей банды",
                           "text": "Иначе изымут часть общака и задержат тех, кто в розыске.",
                           "options": [{"key": "bribe", "label": f"Откупиться {cost} ₽ из общака (85%)"},
                                       {"key": "fight", "label": f"Принять бой (~{int((0.30 + 0.06 * n + 0.07 * guard) * 100)}%)"},
                                       {"key": "lawyer", "label": "Адвокат босса: ареста нет", "disabled": not law_left}]}
    elif g and my_role != "boss" and await _open_visit(conn, chat_id, 0, g["id"], "omon_gang"):
        p3["omon_warn"] = True
    gst = await _active_guest(conn, chat_id)
    if gst:
        spec = GUESTS[gst["key"]]
        cur = await conn.execute("SELECT 1 FROM rp_guest_claim WHERE guest_id = ? AND user_id = ?", (gst["id"], uid))
        p3["guest"] = {"id": gst["id"], "key": gst["key"], "name": spec["name"], "icon": spec["icon"], "left": gst["ends"] - t,
                       "intro": spec["intro"].format(m=max(1, (gst["ends"] - t) // 60)), "taken": bool(await cur.fetchone()),
                       "offers": [{"key": k, "title": v[0], "desc": v[1], "cost": v[2]} for k, v in spec["offers"].items()]}
    out["p3"] = p3
    m = out.get("mine")
    if not g or not m:
        return
    # союзы
    gangs = {x["id"]: x for x in out.get("gangs", [])}
    pacts = []
    for pc in await _pacts_of(conn, g["id"], ("active", "proposed")):
        oid = _other(pc, g["id"])
        o = gangs.get(oid)
        if not o:
            continue
        war = await gangwar._active_war(conn, oid) if pc["status"] == "active" else None
        pacts.append({"id": pc["id"], "other_id": oid, "other": o["name"], "icon": o.get("icon", "🕴"), "status": pc["status"],
                      "left": max(0, pc["ends"] - t), "mine": pc["a"] == g["id"], "age": t - pc["created"],
                      "can_stab": pc["status"] == "active" and t - pc["created"] >= STAB_MIN_AGE, "ally_war": bool(war)})
    m["pacts"] = pacts
    busy = {x["other_id"] for x in pacts}
    traitor = max(0, await kget(conn, f"gtraitor:{g['id']}") - t)
    m["pact_info"] = {"hours": PACT_HOURS, "max": MAX_PACTS, "bonus": int(PACT_BONUS * 100), "traitor_left": traitor, "aid_cost": AID_COST,
                      "aid_points": AID_POINTS, "stab_points": STAB_POINTS, "aid_wait": max(0, await kget(conn, f"gaid:{g['id']}") - t),
                      "cd": max(0, await kget(conn, f"gpactcd:{g['id']}") - t), "busy": sorted(busy)}
    projs = []
    for key, (icon, title, desc, costs, effs) in PROJECTS.items():
        lvl = await proj_level(conn, g["id"], key)
        projs.append({"key": key, "icon": icon, "title": title, "desc": desc, "level": lvl, "cost": costs[lvl] if lvl < 3 else 0,
                      "now": effs[lvl - 1] if lvl else "", "next": effs[lvl] if lvl < 3 else ""})
    m["projects"] = projs


async def tick(conn) -> None:
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player GROUP BY chat_id HAVING COUNT(*) >= 2")
        for (chat_id,) in await cur.fetchall():
            await ensure_ai_gangs(conn, chat_id)
        await _pacts_tick(conn)
        await _projects_tick(conn)
        await _visits_tick(conn)
        await _guests_tick(conn)
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 3")
