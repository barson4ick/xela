"""Мир района: ИИ-патроны и город живут своей жизнью.
Война «семьи» Крёстного (если кинуть с данью), ИИ-полиция на стрелках и штурмах, ИИ-граждане (работы, семьи, бизнесы, доносы, герой дня),
ИИ-перебежчики, импичмент мэра Архипа, дуэль Архип против Крёстного (игроки выбирают сторону), городские события (облава, забастовка, передел района).
"""
import json
import logging
import math
import random
import time

import aiosqlite

import gangplus as GP
import gangplus3 as P3
import gangwar
import rp
from rooms import GameError
from utils import esc, now_local

log = logging.getLogger("world")

FEUD_HOURS = 24
IMP_HOURS = 24
DUEL_HOURS = 36
DUEL_MEAN = 120 * 3600
CITY_MEAN = 18 * 3600
CITIZEN_GAP = 60 * 60
SHAKE_ENERGY = 8
LURE_COST = 800
HUSH_COST = 300
HIDE_COST = 150
DEFEND_COST = 500

AI_JOBS = [("курьер", "🛵", 40, 90), ("таксист", "🚕", 60, 120), ("грузчик", "📦", 70, 130), ("повар", "👨‍🍳", 40, 80),
           ("врач", "🩺", 100, 160), ("стример", "🎮", 0, 150)]
AI_BIZ = [("киоск «Союзпечать»", "📰"), ("шаурмичная «У Гоши»", "🌯"), ("автомойка", "🚿"), ("барбершоп", "💈"), ("ломбард", "💍"), ("шиномонтаж", "🛞")]
HERO_STORIES = ["вынес из пожара соседскую кошку", "вернул хозяину потерянный кошелёк", "починил лавочку во дворе, которую все ломали",
                "в четвёртый раз за неделю перевёл бабушку через дорогу", "поймал воришку у шаурмичной", "накормил весь подъезд борщом"]


def now() -> int:
    return int(time.time())


async def _j(conn, key: str) -> dict:
    raw = await gangwar.kget(conn, key)
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


async def _js(conn, key: str, data) -> None:
    await gangwar.kset(conn, key, json.dumps(data, ensure_ascii=False))


async def _i(conn, key: str) -> int:
    try:
        return int(await gangwar.kget(conn, key) or 0)
    except Exception:
        return 0


async def _is(conn, key: str, val) -> None:
    await gangwar.kset(conn, key, str(int(val)))


async def _say(conn, chat_id: int, text: str) -> None:
    await gangwar.say(conn, chat_id, text)


async def _name(conn, chat_id: int, uid: int) -> str:
    if uid < 0:
        cur = await conn.execute("SELECT name FROM ai_citizen WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        r = await cur.fetchone()
        return r[0] if r else "Гражданин"
    return await P3._uname(conn, chat_id, uid)


async def _players(conn, chat_id: int) -> list:
    cur = await conn.execute("SELECT user_id FROM rp_player WHERE chat_id = ? AND user_id > 0", (chat_id,))
    return [r[0] for r in await cur.fetchall()]


async def _my_gang(conn, chat_id: int, uid: int):
    """(банда, роль) игрока или (None, '')."""
    import gang as G
    g, members = await G._mine(conn, chat_id, uid)
    if not g or g.get("is_ai"):
        return None, ""
    role = next((m["role"] for m in members if m["user_id"] == uid), "grunt")
    return g, role


async def _need_dep(conn, chat_id: int, uid: int):
    g, role = await _my_gang(conn, chat_id, uid)
    if not g:
        raise GameError("Ты не в банде игроков")
    if role == "grunt":
        raise GameError("Это дело босса и замов")
    return g, role


async def _mode(conn, chat_id: int) -> str:
    return (await gangwar.kget(conn, f"pai:{chat_id}")) or "normal"


async def _beat(conn, chat_id: int, uid: int, minutes: int) -> None:
    import gangplus2 as G2
    await G2._beat(conn, chat_id, uid, minutes)


async def _jail(conn, chat_id: int, uid: int, minutes: int) -> None:
    await rp._load(conn, chat_id, uid)
    await conn.execute("UPDATE rp_player SET jail_until = ?, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (now() + minutes * 60, chat_id, uid))


async def _fund_add(conn, chat_id: int, amount: int) -> None:
    await gangwar.kset(conn, f"pfund:{chat_id}", str(max(0, await _i(conn, f"pfund:{chat_id}") + int(amount))))


# ═════════════════════ А. Война «семьи» Крёстного ═════════════════════
async def family(conn, chat_id: int):
    conn.row_factory = aiosqlite.Row
    raw = await gangwar.kget(conn, f"fam:{chat_id}")
    if raw and str(raw).isdigit():
        g = await gangwar._gang_row(conn, int(raw))
        if g and g["is_ai"] == 1:
            return g
    cur = await conn.execute(
        """SELECT g.id FROM gang g LEFT JOIN territory t ON t.owner = g.id WHERE g.chat_id = ? AND g.is_ai = 1
           GROUP BY g.id ORDER BY (g.name = 'Шиномонтажная мафия') DESC, COUNT(t.key) DESC, g.id LIMIT 1""", (chat_id,))
    r = await cur.fetchone()
    if not r:
        return None
    await gangwar.kset(conn, f"fam:{chat_id}", str(r[0]))
    return await gangwar._gang_row(conn, r[0])


async def strike(conn, chat_id: int, gid: int, why: str) -> bool:
    """Крёстный присылает боевиков на банду и (если получится) его семья объявляет ей войну."""
    g = await gangwar._gang_row(conn, gid)
    if not g or g["is_ai"]:
        return False
    feud = await _j(conn, f"feud:{chat_id}")
    if feud.get("until", 0) > now() and feud.get("gid") == gid:
        return False
    fam = await family(conn, chat_id)
    if not fam:
        return False
    members = await gangwar._real_members(conn, gid)
    hit = []
    for u in random.sample(members, k=min(2, len(members))):
        p = await rp._load(conn, chat_id, u)
        loss = min(600, int(p["money"] * 0.05))
        if loss:
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (loss, chat_id, u))
        await _beat(conn, chat_id, u, 30)
        hit.append(esc(await _name(conn, chat_id, u)))
    t = now()
    war_line = ""
    pact = await P3.pact_between(conn, fam["id"], gid, ("active", "proposed"))
    if pact:
        await conn.execute("UPDATE gang_pact SET status = 'broken' WHERE id = ?", (pact["id"],))
        war_line = " Союз с семьёй разорван."
    if not await gangwar._active_war(conn, gid) and not await gangwar._active_war(conn, fam["id"]):
        import gangplus6 as GP6
        start = int(10 * await GP6.pressure(conn, chat_id))
        await conn.execute("INSERT INTO gang_war (chat_id, a, b, started, ends, score_a) VALUES (?, ?, ?, ?, ?, ?)",
                           (chat_id, fam["id"], gid, t, t + gangwar.WAR_HOURS * 3600, start))
        await P3.on_war(conn, chat_id, fam["id"], gid)
        war_line += f" «{esc(fam['name'])}» объявила войну, счёт уже {start}:0."
    atone = min(6000, max(2500, int((g["treasury"] or 0) * 0.25)))
    await _js(conn, f"feud:{chat_id}", {"gid": gid, "fam": fam["id"], "until": t + FEUD_HOURS * 3600, "atone": atone, "why": why})
    await rp._news(conn, chat_id, f"👑 Крёстный отец мстит банде «{g['name']}»")
    people = (" Досталось: " + ", ".join(hit) + " (лежат 30 минут, наличных меньше).") if hit else ""
    await _say(conn, chat_id, f"👑 <b>Крёстный отец не прощает: «{esc(g['name'])}» {esc(why)}.</b> Он прислал боевиков.{people}{war_line} Откупиться можно в мини-аппе: Район → Город → Патроны ({atone} ₽ из общака).")
    return True


async def atone(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        import patrons
        g = await patrons._need_boss(conn, chat_id, uid)
        feud = await _j(conn, f"feud:{chat_id}")
        if feud.get("until", 0) <= now() or feud.get("gid") != g["id"]:
            raise GameError("Крёстный отец на вас не злится")
        cost = int(feud["atone"])
        if (g["treasury"] or 0) < cost:
            raise GameError(f"В общаке {g['treasury'] or 0} ₽, а откуп стоит {cost} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, g["id"]))
        war = await gangwar._active_war(conn, g["id"])
        if war and feud["fam"] in (war["a"], war["b"]):
            await conn.execute("UPDATE gang_war SET status = 'done' WHERE id = ?", (war["id"],))
            for x in (war["a"], war["b"]):
                await gangwar.kset(conn, f"gwcd:{x}", str(now() + gangwar.WAR_COOLDOWN))
        await _js(conn, f"feud:{chat_id}", {})
        await _say(conn, chat_id, f"👑 «{esc(g['name'])}» откупилась от Крёстного отца: {cost} ₽ — и вражда закончена.")
        return P3._res(f"👑 Откуп {cost} ₽ принят. Война с семьёй закончена.", True, -cost)
    return await P3._act(chat_id, uid, run)


async def on_default(conn, chat_id: int, gid: int) -> None:
    """Банда не вернула долг: второй раз за неделю — Крёстный объявляет вражду."""
    key = f"gdef:{chat_id}:{gid}"
    last = await _i(conn, key)
    await _is(conn, key, now())
    if last and now() - last < 7 * 86400:
        await strike(conn, chat_id, gid, "второй раз не вернула долг")


# ═════════════════════ Б. ИИ-полиция на стрелках ═════════════════════
async def police_react(conn, chat_id: int, gids: list, why: str, chance: float = 1.0) -> bool:
    mode = await _mode(conn, chat_id)
    if mode == "off" or random.random() > chance:
        return False
    import police
    import police2
    mult = 1.5 if mode == "strict" else 1.0
    names, caught = [], []
    for gid in gids:
        g = await gangwar._gang_row(conn, gid)
        if not g:
            continue
        names.append(esc(g["name"]))
        if g["is_ai"]:
            continue
        await police.case_add(conn, chat_id, gid, int(8 * mult))
        fine = min(int((g["treasury"] or 0) * 0.04), 500)
        if fine:
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (fine, gid))
            await _fund_add(conn, chat_id, fine)
        await police2.alert(conn, chat_id, gid, "warcops", f"🚓 ИИ-патруль выехал на {why} с участием банды: дело +{int(8 * mult)}, штраф {fine} ₽.")
        cur = await conn.execute(
            """SELECT m.user_id FROM gang_member m JOIN rp_player r ON r.chat_id = m.chat_id AND r.user_id = m.user_id
               WHERE m.gang_id = ? AND m.user_id > 0 AND r.wanted >= 1 AND r.jail_until <= ? ORDER BY RANDOM() LIMIT 1""", (gid, now()))
        row = await cur.fetchone()
        if row and random.random() < 0.35 * mult:
            await _jail(conn, chat_id, row[0], 8)
            caught.append(esc(await _name(conn, chat_id, row[0])))
    if not names:
        return False
    cop = random.choice(["Сержант Петров", "Лейтенант Громова", "Капитан Сидоренко"])
    extra = f" Задержаны на 8 минут: {', '.join(caught)}." if caught else ""
    await _say(conn, chat_id, f"🚓 {cop} с нарядом приехал на {why} ({' и '.join(names)}). Разогнали, оштрафовали, дела пополнились.{extra}")
    return True


async def _wars_tick(conn, chat_id: int) -> None:
    cur = await conn.execute("SELECT id, a, b FROM gang_war WHERE chat_id = ? AND status = 'active' AND ends > ?", (chat_id, now()))
    for wid, a, b in await cur.fetchall():
        mean = 7 * 3600 / (1.6 if await _mode(conn, chat_id) == "strict" else 1.0)
        if await P3._roll(conn, f"wpol:{wid}", mean):
            await police_react(conn, chat_id, [a, b], "стрелку")


# ═════════════════════ В. ИИ-граждане ═════════════════════
async def _aiw(conn, chat_id: int) -> dict:
    return await _j(conn, f"aiw:{chat_id}")


async def _cit_rows(conn, chat_id: int) -> list:
    cur = await conn.execute("SELECT user_id, name, icon, kind, money, cred FROM ai_citizen WHERE chat_id = ? AND kind = 'citizen' ORDER BY user_id", (chat_id,))
    return [tuple(r) for r in await cur.fetchall()]


async def _gangs_real(conn, chat_id: int) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT id, name, respect, treasury FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
    return [dict(r) for r in await cur.fetchall()]


async def _snitch(conn, chat_id: int, name: str, icon: str, shaken: int = 0) -> None:
    import police
    import police2
    gangs = await _gangs_real(conn, chat_id)
    if not gangs:
        return
    cur = await conn.execute("SELECT gang_id, pts FROM police_case WHERE chat_id = ?", (chat_id,))
    pts = {r[0]: r[1] for r in await cur.fetchall()}
    g = random.choices(gangs, weights=[1 + pts.get(x["id"], 0) / 20 for x in gangs])[0]
    await police.case_add(conn, chat_id, g["id"], 4)
    await police2.alert(conn, chat_id, g["id"], "tip", f"📞 Донос: {icon} {name} сообщил, что люди «{g['name']}» занимаются подозрительными делами. Дело +4.")
    if random.random() < 0.4:
        await _say(conn, chat_id, "📞 В полицию поступил донос на одну из банд района. Кто стучит — неизвестно.")


async def _citizen_life(conn, chat_id: int) -> None:
    import aiciv
    aiw = await _aiw(conn, chat_id)
    rows = await _cit_rows(conn, chat_id)
    by_id = {r[0]: r for r in rows}
    # работы
    for uid, name, icon, kind, money, cred in rows:
        w = aiw.setdefault(str(uid), {})
        if not w.get("job"):
            j = random.choice(AI_JOBS)
            w["job"] = [j[0], j[1], j[2], j[3]]
        pay = random.randint(w["job"][2], w["job"][3]) // 3
        if w.get("spouse"):
            pay = int(pay * 1.1)
        kids = len(w.get("kids", []))
        net = max(0, pay - 10 * kids)
        await conn.execute("UPDATE ai_citizen SET money = money + ? WHERE chat_id = ? AND user_id = ?", (net, chat_id, uid))
        # бизнес
        biz = w.get("biz")
        if biz:
            biz["cash"] = min(900, biz.get("cash", 0) + random.randint(50, 120))
            await conn.execute("UPDATE ai_citizen SET money = money + ? WHERE chat_id = ? AND user_id = ?", (random.randint(30, 70), chat_id, uid))
        elif money >= 1500 and random.random() < 0.25:
            b = random.choice(AI_BIZ)
            w["biz"] = {"name": b[0], "icon": b[1], "cash": 0, "shake_at": 0}
            await conn.execute("UPDATE ai_citizen SET money = MAX(0, money - 1000) WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            await _say(conn, chat_id, f"🏪 {icon} {esc(name)} открыл бизнес: {b[1]} {esc(b[0])}. Теперь это лакомый кусок для бригад района.")
    # семьи
    singles = [r for r in rows if not aiw.get(str(r[0]), {}).get("spouse")]
    if len(singles) >= 2 and random.random() < 0.10:
        a, b = random.sample(singles, 2)
        aiw[str(a[0])]["spouse"], aiw[str(b[0])]["spouse"] = b[0], a[0]
        await _say(conn, chat_id, f"💍 {a[2]} {esc(a[1])} и {b[2]} {esc(b[1])} сыграли свадьбу. Весь район гулял, шаурма была за счёт молодожёнов.")
    for uid, name, icon, kind, money, cred in rows:
        w = aiw.get(str(uid), {})
        sp = w.get("spouse")
        if sp and uid < sp and len(w.get("kids", [])) < 3 and random.random() < 0.04:
            kid = random.choice(["Ваня", "Маша", "Кирилл", "Соня", "Артём", "Лиза"])
            w.setdefault("kids", []).append(kid)
            if str(sp) in aiw:
                aiw[str(sp)]["kids"] = list(w["kids"])
            sn = by_id.get(sp, (0, "?", "👤"))[1]
            await _say(conn, chat_id, f"👶 У {icon} {esc(name)} и {esc(sn)} родился ребёнок: {kid}. Район поздравляет.")
    # доносы и перебежчики
    for uid, name, icon, kind, money, cred in rows:
        state, gid, gname = await aiciv._membership(conn, chat_id, uid)
        if state == "citizen" and random.random() < 0.035:
            await _snitch(conn, chat_id, esc(name), icon)
        elif state == "gang":
            await _maybe_defect(conn, chat_id, uid, name, icon, gid, gname)
    await _js(conn, f"aiw:{chat_id}", aiw)


async def _maybe_defect(conn, chat_id: int, uid: int, name: str, icon: str, gid: int, gname: str) -> None:
    """ИИ-гражданин уходит к конкурентам: не крыса, а честный перебежчик со своими деньгами."""
    cur = await conn.execute("SELECT respect, treasury FROM gang WHERE id = ?", (gid,))
    row = await cur.fetchone()
    if not row:
        return
    p = 0.035 if (row[0] or 0) < 40 or (row[1] or 0) < 300 else 0.012
    if random.random() >= p:
        return
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        """SELECT g.id, g.name, g.respect FROM gang g LEFT JOIN gang_member m ON m.gang_id = g.id
           WHERE g.chat_id = ? AND g.is_ai = 0 AND g.id != ? GROUP BY g.id HAVING COUNT(m.user_id) < 6""", (chat_id, gid))
    rivals = [dict(r) for r in await cur.fetchall()]
    cur = await conn.execute("SELECT id, name, respect FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    ais = [dict(r) for r in await cur.fetchall()]
    pool = rivals + ais
    if not pool:
        return
    new = random.choices(pool, weights=[1 + (x["respect"] or 0) / 20 for x in pool])[0]
    to_ai = any(new["id"] == a["id"] for a in ais)
    cur = await conn.execute("SELECT money FROM ai_citizen WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    mr = await cur.fetchone()
    bring = min(int(mr[0] if mr else 0), random.randint(100, 300))
    await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    if not to_ai:
        await conn.execute("INSERT INTO gang_member (chat_id, user_id, gang_id, joined, role, merit) VALUES (?, ?, ?, ?, 'grunt', 0)", (chat_id, uid, new["id"], now()))
    if bring:
        await conn.execute("UPDATE ai_citizen SET money = money - ? WHERE chat_id = ? AND user_id = ?", (bring, chat_id, uid))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (bring, new["id"]))
    import gangplus5 as P5
    await P5.rep_add(conn, gid, -2, 0)
    await _say(conn, chat_id, f"🔀 {icon} {esc(name)} ушёл от «{esc(gname)}» к конкурентам — в «{esc(new['name'])}»" + (f" и принёс {bring} ₽ в общак" if bring else "") + ". Банде не хватило уважения, чтобы его удержать.")


async def _hero_tick(conn, chat_id: int) -> None:
    day = rp.day_str()
    h = await _j(conn, f"hero:{chat_id}")
    if h.get("day") == day or now_local().hour < 21:
        return
    cur = await conn.execute("SELECT user_id, work_sum FROM rp_player WHERE chat_id = ? AND user_id > 0 AND work_day = ? AND work_sum >= 400 ORDER BY work_sum DESC LIMIT 1", (chat_id, day))
    row = await cur.fetchone()
    if row:
        uid = row[0]
        await rp._load(conn, chat_id, uid)
        await conn.execute("UPDATE rp_player SET money = money + 300 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        nm = await _name(conn, chat_id, uid)
        h = {"day": day, "uid": uid, "name": nm, "ai": False, "why": f"отпахал смену на {row[1]} ₽ и не упал"}
        await _say(conn, chat_id, f"🏅 <b>Герой района сегодня — {esc(nm)}</b>: {h['why']}. Мэрия выписала премию 300 ₽.")
    else:
        rows = await _cit_rows(conn, chat_id)
        if not rows:
            return
        uid, nm, icon, _k, _m, _c = random.choice(rows)
        story = random.choice(HERO_STORIES)
        await conn.execute("UPDATE ai_citizen SET cred = cred + 3, money = money + 300 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        h = {"day": day, "uid": uid, "name": nm, "icon": icon, "ai": True, "why": story}
        await _say(conn, chat_id, f"🏅 <b>Герой района сегодня — {icon} {esc(nm)}</b>: {story}. Авторитет +3.")
    await _js(conn, f"hero:{chat_id}", h)


async def shake(chat_id: int, uid: int, name: str, cid) -> dict:
    cid = int(cid)

    async def run(conn):
        g, role = await _my_gang(conn, chat_id, uid)
        if not g:
            raise GameError("Бизнесы облагает только банда")
        aiw = await _aiw(conn, chat_id)
        w = aiw.get(str(cid)) or {}
        biz = w.get("biz")
        if not biz:
            raise GameError("У него нет бизнеса")
        if biz.get("shake_at", 0) > now() - 6 * 3600:
            raise GameError("Этого уже недавно трясли: выручка не накопилась")
        if await _i(conn, f"wshk:{g['id']}") > now():
            raise GameError("Банда уже ходила собирать: подожди 20 минут")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, SHAKE_ENERGY)
        p["energy"] -= SHAKE_ENERGY
        await rp._save(conn, chat_id, uid, p)
        await _is(conn, f"wshk:{g['id']}", now() + 20 * 60)
        biz["shake_at"] = now()
        cname = await _name(conn, chat_id, cid)
        if random.random() < 0.25:
            await _beat(conn, chat_id, uid, 15)
            await _js(conn, f"aiw:{chat_id}", aiw)
            return P3._res(f"🥊 {cname} вызвал своих: тебя вежливо отправили лечиться на 15 минут, выручки нет.", False, 0)
        amount = min(biz.get("cash", 0), random.randint(250, 700))
        biz["cash"] = max(0, biz.get("cash", 0) - amount)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (amount, g["id"]))
        import gangplus2 as G2
        await G2.log_event(conn, chat_id, uid, "trade", amount)
        txt = f"🏪 Собрал с «{biz['name']}» {amount} ₽ в общак."
        if random.random() < 0.35:
            cur = await conn.execute("SELECT icon FROM ai_citizen WHERE chat_id = ? AND user_id = ?", (chat_id, cid))
            ic = (await cur.fetchone() or ["👤"])[0]
            await _snitch(conn, chat_id, esc(cname), ic)
            txt += " Но хозяин побежал в полицию."
        await _js(conn, f"aiw:{chat_id}", aiw)
        return P3._res(txt, True, amount)
    return await P3._act(chat_id, uid, run)


async def hush(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, role = await _need_dep(conn, chat_id, uid)
        if await _i(conn, f"whush:{g['id']}") > now():
            raise GameError("Информаторов можно успокаивать раз в 2 часа")
        if (g["treasury"] or 0) < HUSH_COST:
            raise GameError(f"В общаке нужно {HUSH_COST} ₽")
        import police
        c = await police._case(conn, chat_id, g["id"])
        if (c["pts"] or 0) <= 0:
            raise GameError("На банду нет дела: затыкать некого")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (HUSH_COST, g["id"]))
        await police.case_add(conn, chat_id, g["id"], -8)
        await _is(conn, f"whush:{g['id']}", now() + 2 * 3600)
        return P3._res(f"🤫 Информаторы получили конверты: дело −8. Потрачено {HUSH_COST} ₽.", True, -HUSH_COST)
    return await P3._act(chat_id, uid, run)


async def lure(chat_id: int, uid: int, name: str, cid) -> dict:
    cid = int(cid)

    async def run(conn):
        import patrons
        import aiciv
        g = await patrons._need_boss(conn, chat_id, uid)
        if await _i(conn, f"wlure:{g['id']}") > now():
            raise GameError("Переманивать можно раз в час")
        cur = await conn.execute("SELECT name, icon, kind FROM ai_citizen WHERE chat_id = ? AND user_id = ?", (chat_id, cid))
        row = await cur.fetchone()
        if not row or row[2] != "citizen":
            raise GameError("Такого жителя нет")
        state, ogid, ogname = await aiciv._membership(conn, chat_id, cid)
        if ogid == g["id"]:
            raise GameError("Он уже в твоей банде")
        if state == "police":
            raise GameError("Из полиции не переманить")
        cur = await conn.execute("SELECT COUNT(*) FROM gang_member WHERE gang_id = ?", (g["id"],))
        if (await cur.fetchone())[0] >= 6:
            raise GameError("В банде нет мест")
        if (g["treasury"] or 0) < LURE_COST:
            raise GameError(f"Нужно {LURE_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (LURE_COST, g["id"]))
        await _is(conn, f"wlure:{g['id']}", now() + 3600)
        if random.random() > (0.45 if state == "gang" else 0.6):
            return P3._res(f"🤷 {row[0]} взял деньги и всё равно отказался. {LURE_COST} ₽ потеряны.", False, -LURE_COST)
        await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, cid))
        await conn.execute("INSERT INTO gang_member (chat_id, user_id, gang_id, joined, role, merit) VALUES (?, ?, ?, ?, 'grunt', 0)", (chat_id, cid, g["id"], now()))
        src = f" из «{esc(ogname)}»" if state == "gang" else ""
        await _say(conn, chat_id, f"🔀 «{esc(g['name'])}» переманила {row[1]} {esc(row[0])}{src}.")
        return P3._res(f"🔀 {row[0]} теперь в твоей банде.", True, -LURE_COST)
    return await P3._act(chat_id, uid, run)


async def enrich(conn, chat_id: int, ai: list) -> list:
    aiw = await _aiw(conn, chat_id)
    names = {x["id"]: x["name"] for x in ai}
    for x in ai:
        w = aiw.get(str(x["id"]))
        if not w:
            continue
        if w.get("job"):
            x["job"] = f"{w['job'][1]} {w['job'][0]}"
        if w.get("biz"):
            x["biz"] = f"{w['biz']['icon']} {w['biz']['name']}"
        if w.get("spouse"):
            x["spouse"] = names.get(w["spouse"], "")
        if w.get("kids"):
            x["kids"] = len(w["kids"])
    return ai


# ═════════════════════ Д. Импичмент Архипа ═════════════════════
async def _need_votes(conn, chat_id: int) -> int:
    n = len(await _players(conn, chat_id))
    return max(2, min(6, math.ceil(n * 0.5)))


async def _ai_mayor_active(conn, chat_id: int) -> bool:
    import gangplus10 as GP10
    return not await GP10._mayor(conn, chat_id) and await _i(conn, f"aimsus:{chat_id}") <= now()


async def impeach_vote(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        import gangplus10 as GP10
        import patrons
        if await GP10._mayor(conn, chat_id):
            raise GameError("Сейчас мэр — босс банды: импичмент объявляют боссы")
        if await _i(conn, f"aimsus:{chat_id}") > now():
            raise GameError("Архип уже в отставке")
        if await _i(conn, f"impcd:{chat_id}") > now():
            raise GameError("Архипа смещали недавно: новая попытка позже")
        imp = await _j(conn, f"imp:{chat_id}")
        if imp.get("start", 0) < now() - IMP_HOURS * 3600:
            imp = {"start": now(), "votes": []}
        if uid in imp["votes"]:
            raise GameError("Ты уже голосовал за отставку")
        imp["votes"].append(uid)
        need = await _need_votes(conn, chat_id)
        if len(imp["votes"]) < need:
            await _js(conn, f"imp:{chat_id}", imp)
            return P3._res(f"🗳 Голос за отставку Архипа учтён: {len(imp['votes'])} из {need}.", True, 0)
        t = now()
        await _js(conn, f"imp:{chat_id}", {})
        await _is(conn, f"impcd:{chat_id}", t + 3 * 86400)
        await _is(conn, f"aimsus:{chat_id}", t + (GP10.ELECT_HOURS + 6) * 3600)
        await patrons._set(conn, f"aimayor:{chat_id}", {"tax": 1.0, "police": 1.0, "curfew": False, "until": 0})
        raw = await gangwar.kget(conn, f"council:{chat_id}")
        el = json.loads(raw) if raw else None
        tail = ""
        if not el or el.get("closes", 0) <= t:
            await gangwar.kset(conn, f"council:{chat_id}", json.dumps({"week": GP10._week(), "closes": t + GP10.ELECT_HOURS * 3600}))
            tail = f" Совет боссов собран: {GP10.ELECT_HOURS} часов на голосование за нового мэра."
        await rp._news(conn, chat_id, "🗳 Мэр Архип отправлен в отставку жителями")
        await _say(conn, chat_id, f"🗳 <b>Жители сместили мэра Архипа!</b> Голосов: {len(imp['votes'])}. Налог и полицейский произвол отменены до новых выборов.{tail}")
        return P3._res("🗳 Архип смещён: налог 1.0, мэра выберет совет боссов.", True, 0)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ Е. Архип против Крёстного ═════════════════════
SIDES = {"m": ("🎩", "Мэр Архип"), "g": ("👑", "Крёстный отец")}


async def _duel_active(conn, chat_id: int) -> dict:
    d = await _j(conn, f"duel:{chat_id}")
    return d if d.get("ends", 0) > now() and not d.get("done") else {}


async def _duel_start(conn, chat_id: int) -> None:
    if not await _ai_mayor_active(conn, chat_id) or await _duel_active(conn, chat_id):
        return
    if await _i(conn, f"duelcd:{chat_id}") > now():
        return
    t = now()
    await _js(conn, f"duel:{chat_id}", {"start": t, "ends": t + DUEL_HOURS * 3600, "pts": {"m": 5, "g": 5}, "sides": {}, "don": {}})
    await _say(conn, chat_id, f"⚔️ <b>🎩 Мэр Архип и 👑 Крёстный отец сошлись в открытой борьбе за район!</b> {DUEL_HOURS} часов. Выберите сторону и поддержите её деньгами (мини-апп → Район → Город). Проигравший теряет влияние на сутки, а ставки победителей возвращаются с прибавкой.")


async def duel_side(chat_id: int, uid: int, name: str, side: str) -> dict:
    async def run(conn):
        d = await _duel_active(conn, chat_id)
        if not d:
            raise GameError("Сейчас борьбы за район нет")
        if side not in SIDES:
            raise GameError("Выбери Архипа или Крёстного отца")
        if str(uid) in d["sides"]:
            raise GameError("Сторону уже выбрали: назад дороги нет")
        d["sides"][str(uid)] = side
        await _js(conn, f"duel:{chat_id}", d)
        return P3._res(f"{SIDES[side][0]} Ты на стороне: {SIDES[side][1]}.", True, 0)
    return await P3._act(chat_id, uid, run)


async def duel_fund(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount)

    async def run(conn):
        d = await _duel_active(conn, chat_id)
        if not d:
            raise GameError("Сейчас борьбы за район нет")
        side = d["sides"].get(str(uid))
        if not side:
            raise GameError("Сначала выбери сторону")
        if amount not in (100, 500, 1000):
            raise GameError("Взнос: 100, 500 или 1000 ₽")
        don = d["don"].get(str(uid), {"s": side, "a": 0})
        if don["a"] + amount > 3000:
            raise GameError("Больше 3000 ₽ на борьбу не вложить")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < amount:
            raise GameError(f"Нужно {amount} ₽")
        p["money"] -= amount
        await rp._save(conn, chat_id, uid, p)
        don["a"] += amount
        d["don"][str(uid)] = don
        d["pts"][side] += amount // 50
        await _js(conn, f"duel:{chat_id}", d)
        return P3._res(f"{SIDES[side][0]} Взнос {amount} ₽: очки стороны +{amount // 50}.", True, -amount)
    return await P3._act(chat_id, uid, run)


async def _duel_tick(conn, chat_id: int, accrue: bool) -> None:
    d = await _j(conn, f"duel:{chat_id}")
    if not d or d.get("done"):
        return
    t = now()
    if accrue and d.get("ends", 0) > t:
        sup = {"m": 0, "g": 0}
        for s in d["sides"].values():
            sup[s] += 1
        cur = await conn.execute("SELECT COUNT(*) FROM territory t JOIN gang g ON g.id = t.owner WHERE t.chat_id = ? AND g.is_ai = 0", (chat_id,))
        terr = (await cur.fetchone())[0]
        d["pts"]["g"] += sup["g"] + terr // 2 + random.randint(0, 2)
        d["pts"]["m"] += sup["m"] + min(3, await _i(conn, f"pfund:{chat_id}") // 1500) + random.randint(0, 2)
        await _js(conn, f"duel:{chat_id}", d)
    if d.get("ends", 0) > t:
        return
    win = "m" if d["pts"]["m"] > d["pts"]["g"] else "g" if d["pts"]["g"] > d["pts"]["m"] else random.choice(["m", "g"])
    don = d["don"]
    pot = sum(v["a"] for v in don.values())
    pool = int(pot * 0.8)
    wtot = sum(v["a"] for v in don.values() if v["s"] == win)
    paid = 0
    for u, v in don.items():
        if v["s"] == win and wtot:
            share = int(pool * v["a"] / wtot)
            await rp._load(conn, chat_id, int(u))
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (share, chat_id, int(u)))
            paid += share
    for u, s in d["sides"].items():
        if s == win and u not in don:
            await rp._load(conn, chat_id, int(u))
            await conn.execute("UPDATE rp_player SET money = money + 150 WHERE chat_id = ? AND user_id = ?", (chat_id, int(u)))
    if win == "m":
        await conn.execute("UPDATE territory SET police = MIN(100, police + 8), control = MAX(0, control - 3) WHERE chat_id = ? AND owner != 0", (chat_id,))
        await _fund_add(conn, chat_id, 300)
        effect = "Архип ужесточил порядок: налог ×1.5, полиция ×1.5 на сутки, контроль банд −3."
    else:
        cnt = {}
        for u, s in d["sides"].items():
            if s == "g":
                g, _ = await _my_gang(conn, chat_id, int(u))
                if g:
                    cnt[g["id"]] = cnt.get(g["id"], 0) + 1
        for gid, n in cnt.items():
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (min(1000, 250 * n), gid))
        effect = "Крёстный отец ослабил мэра: налог ×0.5, полиция ×0.5 на сутки, банды его сторонников получили премию в общак."
    await _js(conn, f"dueleff:{chat_id}", {"winner": win, "until": t + 24 * 3600})
    await _is(conn, f"duelcd:{chat_id}", t + 60 * 3600)
    d["done"] = True
    await _js(conn, f"duel:{chat_id}", d)
    await rp._news(conn, chat_id, f"⚔️ Борьба за район: победил {SIDES[win][1]}")
    ret = f" Ставки победителей вернулись с бонусом: выплачено {paid} ₽." if paid else ""
    await _say(conn, chat_id, f"⚔️ <b>Борьба за район закончилась: победил {SIDES[win][0]} {SIDES[win][1]}</b> ({d['pts'][win]} очков против {d['pts']['m' if win == 'g' else 'g']}). {effect}{ret}")


async def mayor_override(conn, chat_id: int):
    """Что изменили импичмент и борьба за район. None — мэр работает как обычно."""
    if await _i(conn, f"aimsus:{chat_id}") > now():
        return {"tax": 1.0, "curfew": False, "police": 1.0}
    eff = await _j(conn, f"dueleff:{chat_id}")
    if eff.get("until", 0) > now():
        return {"tax": 1.5, "curfew": False, "police": 1.5} if eff["winner"] == "m" else {"tax": 0.5, "curfew": False, "police": 0.5}
    return None


# ═════════════════════ Ж. Городские события ═════════════════════
async def mods(conn, chat_id: int) -> dict:
    m = await _j(conn, f"citymods:{chat_id}")
    if m.get("until", 0) > now():
        return {"work_mult": m.get("work_mult", 1.0), "sell_mult": m.get("sell_mult", 1.0)}
    return {}


async def _city_start(conn, chat_id: int) -> None:
    n = len(await _players(conn, chat_id))
    kind = random.choices(["raid", "strike", "market"], [3, 3, 2])[0]
    t = now()
    if kind == "market":
        cur = await conn.execute("SELECT t.key, t.owner, g.is_ai, t.control FROM territory t JOIN gang g ON g.id = t.owner WHERE t.chat_id = ? AND t.owner != 0", (chat_id,))
        owned = await cur.fetchall()
        cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
        ais = await cur.fetchall()
        players_t = sorted([r for r in owned if not r[2]], key=lambda r: r[3])
        pool = players_t[:2] if players_t and random.random() < 0.7 else list(owned)
        if not pool or not ais:
            kind = "strike"
        else:
            key, owner = random.choice(pool)[:2]
            cands = [a for a in ais if a[0] != owner]
            if not cands:
                kind = "strike"
            else:
                to = random.choice(cands)
                city = {"kind": "market", "phase": "warn", "at": t + 15 * 60, "target": key, "to": to[0], "to_name": to[1], "from": owner, "defended": False}
                tn = gangwar.TERR[key]
                await _js(conn, f"city:{chat_id}", city)
                await _say(conn, chat_id, f"🛒 <b>Передел рынка:</b> «{esc(to[1])}» нацелилась на район {tn[1]} <b>{tn[2]}</b>. Через 15 минут она заберёт его, если хозяин не заплатит за защиту (босс или зам, мини-апп → Город).")
                return
    if kind == "raid":
        await _js(conn, f"city:{chat_id}", {"kind": "raid", "phase": "warn", "at": t + 10 * 60, "hidden": []})
        await _say(conn, chat_id, f"🚔 <b>Слухи о городской облаве:</b> через 10 минут полиция прочешет район. Тем, кто в розыске (2⭐ и больше), лучше залечь на дно ({HIDE_COST} ₽, мини-апп → Город).")
        return
    need = max(2, math.ceil(n * 0.4))
    await _js(conn, f"city:{chat_id}", {"kind": "strike", "phase": "active", "until": t + 3 * 3600, "support": [], "scabs": [], "need": need})
    await _js(conn, f"citymods:{chat_id}", {"work_mult": 0.6, "until": t + 3 * 3600})
    await _say(conn, chat_id, f"✊ <b>В районе забастовка!</b> На работе платят на 40% меньше. Нужно {need} голосов поддержки профсоюза (мини-апп → Город), чтобы выбить прибавку. Штрейкбрехеры получают 200 ₽, но забастовку это ослабляет.")


async def _do_raid(conn, chat_id: int, city: dict) -> None:
    mode = await _mode(conn, chat_id)
    hidden = set(city.get("hidden", []))
    cur = await conn.execute(
        """SELECT r.user_id FROM rp_player r WHERE r.chat_id = ? AND r.user_id > 0 AND r.wanted >= 2 AND r.jail_until <= ?
           AND r.user_id NOT IN (SELECT user_id FROM police_force WHERE chat_id = ?)""", (chat_id, now(), chat_id))
    caught = []
    import police
    for (u,) in await cur.fetchall():
        if u in hidden or random.random() > (0.7 if mode == "strict" else 0.55):
            continue
        p = await rp._load(conn, chat_id, u)
        fine = min(300, p["money"])
        await conn.execute("UPDATE rp_player SET money = money - ?, wanted = MAX(0, wanted - 1) WHERE chat_id = ? AND user_id = ?", (fine, chat_id, u))
        await _jail(conn, chat_id, u, 8)
        await _fund_add(conn, chat_id, fine + 100)
        g, _ = await _my_gang(conn, chat_id, u)
        if g:
            await police.case_add(conn, chat_id, g["id"], 6)
        caught.append(esc(await _name(conn, chat_id, u)))
    if caught:
        await _say(conn, chat_id, f"🚔 <b>Облава!</b> Задержаны на 8 минут: {', '.join(caught)}. Штрафы 300 ₽ ушли в фонд полиции.")
    else:
        await _say(conn, chat_id, "🚔 Облава прошла впустую: все, кого искали, вовремя залегли на дно.")


async def _do_market(conn, chat_id: int, city: dict) -> None:
    key, to = city["target"], city["to"]
    if city.get("defended"):
        await _say(conn, chat_id, "🛒 Передел рынка сорвался: хозяин района заплатил за защиту.")
        return
    cur = await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
    row = await cur.fetchone()
    if not row or row[0] == to:
        return
    await conn.execute("UPDATE territory SET owner = ?, last_pay = ?, control = 35 WHERE chat_id = ? AND key = ?", (to, now(), chat_id, key))
    mult = random.choice([0.8, 1.25])
    await _js(conn, f"citymods:{chat_id}", {"sell_mult": mult, "until": now() + 6 * 3600})
    tn = gangwar.TERR[key]
    await rp._news(conn, chat_id, f"🛒 Передел рынка: «{city['to_name']}» забрала район {tn[1]} {tn[2]}")
    await _say(conn, chat_id, f"🛒 <b>Передел рынка!</b> «{esc(city['to_name'])}» забрала район {tn[1]} <b>{tn[2]}</b>. Цены на товары {'упали' if mult < 1 else 'выросли'}: продажа ×{mult} на 6 часов.")


async def _city_tick(conn, chat_id: int) -> None:
    city = await _j(conn, f"city:{chat_id}")
    if not city:
        return
    t = now()
    if city["kind"] in ("raid", "market") and city.get("at", 0) <= t:
        await (_do_raid if city["kind"] == "raid" else _do_market)(conn, chat_id, city)
        await _js(conn, f"city:{chat_id}", {})
        await _is(conn, f"citycd:{chat_id}", t + 6 * 3600)
    elif city["kind"] == "strike" and city.get("until", 0) <= t:
        await _js(conn, f"city:{chat_id}", {})
        await _js(conn, f"citymods:{chat_id}", {})
        await _is(conn, f"citycd:{chat_id}", t + 6 * 3600)
        await _say(conn, chat_id, "✊ Забастовка выдохлась: профсоюз не набрал голосов, всё вернулось как было.")


async def _need_city(conn, chat_id: int, kind: str, phase: str) -> dict:
    city = await _j(conn, f"city:{chat_id}")
    if city.get("kind") != kind or city.get("phase") != phase:
        raise GameError("Сейчас этого события нет")
    return city


async def city_hide(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        city = await _need_city(conn, chat_id, "raid", "warn")
        p = await rp._load(conn, chat_id, uid)
        if p["wanted"] < 1:
            raise GameError("Ты не в розыске: прятаться незачем")
        if uid in city["hidden"]:
            raise GameError("Ты уже залёг на дно")
        if p["money"] < HIDE_COST:
            raise GameError(f"Нужно {HIDE_COST} ₽")
        p["money"] -= HIDE_COST
        await rp._save(conn, chat_id, uid, p)
        city["hidden"].append(uid)
        await _js(conn, f"city:{chat_id}", city)
        await _fund_add(conn, chat_id, HIDE_COST // 2)
        return P3._res(f"🕳 Ты залёг на дно: облава тебя не найдёт. Заплатил {HIDE_COST} ₽.", True, -HIDE_COST)
    return await P3._act(chat_id, uid, run)


async def city_support(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        city = await _need_city(conn, chat_id, "strike", "active")
        if uid in city["support"] or uid in city["scabs"]:
            raise GameError("Ты уже определился")
        city["support"].append(uid)
        if len(city["support"]) >= city["need"]:
            t = now()
            await _js(conn, f"city:{chat_id}", {})
            await _js(conn, f"citymods:{chat_id}", {"work_mult": 1.15, "until": t + 6 * 3600})
            await _is(conn, f"citycd:{chat_id}", t + 6 * 3600)
            await _say(conn, chat_id, "✊ <b>Забастовка победила!</b> Профсоюз выбил прибавку: зарплаты +15% на 6 часов.")
            return P3._res("✊ Профсоюз победил: зарплаты +15% на 6 часов.", True, 0)
        await _js(conn, f"city:{chat_id}", city)
        return P3._res(f"✊ Ты поддержал профсоюз: {len(city['support'])} из {city['need']}.", True, 0)
    return await P3._act(chat_id, uid, run)


async def city_scab(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        city = await _need_city(conn, chat_id, "strike", "active")
        if uid in city["support"] or uid in city["scabs"]:
            raise GameError("Ты уже определился")
        city["scabs"].append(uid)
        city["need"] = min(city["need"] + 1, 8)
        await _js(conn, f"city:{chat_id}", city)
        await rp._load(conn, chat_id, uid)
        await conn.execute("UPDATE rp_player SET money = money + 200 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        return P3._res(f"🧱 Штрейкбрехер: +200 ₽, но профсоюзу теперь нужно {city['need']} голосов.", True, 200)
    return await P3._act(chat_id, uid, run)


async def city_defend(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        city = await _need_city(conn, chat_id, "market", "warn")
        g, role = await _need_dep(conn, chat_id, uid)
        if g["id"] != city["from"]:
            raise GameError("Это не ваш район")
        if city.get("defended"):
            raise GameError("Защита уже оплачена")
        if (g["treasury"] or 0) < DEFEND_COST:
            raise GameError(f"В общаке нужно {DEFEND_COST} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (DEFEND_COST, g["id"]))
        await conn.execute("UPDATE territory SET control = MIN(100, control + 10) WHERE chat_id = ? AND key = ?", (chat_id, city["target"]))
        city["defended"] = True
        await _js(conn, f"city:{chat_id}", city)
        return P3._res(f"🛡 Защита оплачена ({DEFEND_COST} ₽): район удержим, контроль +10.", True, -DEFEND_COST)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ состояние для мини-аппа ═════════════════════
async def state(conn, chat_id: int, uid: int) -> dict:
    import aiciv
    import police
    import gangplus10 as GP10
    t = now()
    g, role = await _my_gang(conn, chat_id, uid)
    gid = g["id"] if g else 0
    dep = role in ("boss", "deputy")
    out = {"role": role}
    feud = await _j(conn, f"feud:{chat_id}")
    if feud.get("until", 0) > t:
        fg = await gangwar._gang_row(conn, feud["gid"])
        fam = await gangwar._gang_row(conn, feud["fam"])
        out["feud"] = {"gang": fg["name"] if fg else "?", "family": fam["name"] if fam else "семья", "why": feud.get("why", ""), "left": feud["until"] - t,
                       "cost": feud["atone"], "mine": feud["gid"] == gid and role == "boss"}
    # импичмент
    mayor_gang = await GP10._mayor(conn, chat_id)
    imp = await _j(conn, f"imp:{chat_id}")
    if imp.get("start", 0) < t - IMP_HOURS * 3600:
        imp = {"votes": []}
    aim = await _j(conn, f"aimayor:{chat_id}")
    out["impeach"] = {"active": not mayor_gang and await _i(conn, f"aimsus:{chat_id}") <= t, "votes": len(imp.get("votes", [])), "need": await _need_votes(conn, chat_id),
                      "mine": uid in imp.get("votes", []), "tax": aim.get("tax", 1.0) if aim.get("until", 0) > t else 1.0,
                      "suspended": max(0, await _i(conn, f"aimsus:{chat_id}") - t), "cd": max(0, await _i(conn, f"impcd:{chat_id}") - t),
                      "mayor_gang": mayor_gang["gname"] if mayor_gang else ""}
    # борьба за район
    d = await _duel_active(conn, chat_id)
    eff = await _j(conn, f"dueleff:{chat_id}")
    duel = {"active": bool(d), "effect": None}
    if eff.get("until", 0) > t:
        duel["effect"] = {"winner": eff["winner"], "left": eff["until"] - t}
    if d:
        sup = {"m": 0, "g": 0}
        for s in d["sides"].values():
            sup[s] += 1
        mine = d["sides"].get(str(uid), "")
        duel.update({"left": d["ends"] - t, "pts": d["pts"], "sup": sup, "mine": mine, "my_don": d["don"].get(str(uid), {}).get("a", 0),
                     "pot": sum(v["a"] for v in d["don"].values())})
    out["duel"] = duel
    # город
    city = await _j(conn, f"city:{chat_id}")
    cs = None
    if city:
        cs = {"kind": city["kind"], "phase": city["phase"], "left": max(0, (city.get("until") or city.get("at") or 0) - t)}
        if city["kind"] == "raid":
            cs["hidden"] = uid in city["hidden"]
        elif city["kind"] == "strike":
            cs.update({"support": len(city["support"]), "need": city["need"], "mine": "yes" if uid in city["support"] else "scab" if uid in city["scabs"] else ""})
        else:
            tn = gangwar.TERR[city["target"]]
            cs.update({"target": f"{tn[1]} {tn[2]}", "to": city["to_name"], "defended": city.get("defended", False), "can_defend": dep and city["from"] == gid})
    out["city"] = cs
    cm = await _j(conn, f"citymods:{chat_id}")
    out["citymods"] = {"work": cm.get("work_mult", 1.0), "sell": cm.get("sell_mult", 1.0), "left": cm.get("until", 0) - t} if cm.get("until", 0) > t else None
    out["hero"] = await _j(conn, f"hero:{chat_id}")
    # жители: бизнесы, лови, доносы
    aiw = await _aiw(conn, chat_id)
    rows = await _cit_rows(conn, chat_id)
    names = {r[0]: r[1] for r in rows}
    cits = []
    for cid, name, icon, kind, money, cred in rows:
        w = aiw.get(str(cid), {})
        state_, ogid, ogname = await aiciv._membership(conn, chat_id, cid)
        biz = w.get("biz")
        cits.append({"id": cid, "name": name, "icon": icon, "job": f"{w['job'][1]} {w['job'][0]}" if w.get("job") else "", "state": state_, "group": ogname, "mine": ogid == gid and gid != 0,
                     "biz": f"{biz['icon']} {biz['name']}" if biz else "", "biz_wait": max(0, biz.get("shake_at", 0) + 6 * 3600 - t) if biz else 0,
                     "spouse": names.get(w.get("spouse"), ""), "kids": len(w.get("kids", []))})
    out["citizens"] = cits
    if g:
        c = await police._case(conn, chat_id, gid)
        out["my"] = {"case": int(c["pts"] or 0), "hush_wait": max(0, await _i(conn, f"whush:{gid}") - t), "lure_wait": max(0, await _i(conn, f"wlure:{gid}") - t),
                     "shake_wait": max(0, await _i(conn, f"wshk:{gid}") - t), "hush_cost": HUSH_COST, "lure_cost": LURE_COST}
    out["costs"] = {"hide": HIDE_COST, "defend": DEFEND_COST}
    out["jobtax"] = await job_taxes(conn, chat_id)
    import assign
    out["tasks"] = await assign.state(conn, chat_id, uid)
    import conflict
    out["ops"] = await conflict.state(conn, chat_id, uid)
    out["crcd"] = {k: max(0, await _i(conn, f"crcd:{chat_id}:{uid}:{k}") - t) for k in ("bank", "jewel")}
    import balance
    out["bal"] = await balance.state(conn, chat_id, uid, gid)
    if g:
        out.setdefault("my", {})["rtax"] = await _i(conn, f"rtax:{chat_id}:{gid}")
    return out


# ═════════════════════ фоновые задачи ═════════════════════
async def _tick_chat(conn, chat_id: int) -> None:
    t = now()
    feud = await _j(conn, f"feud:{chat_id}")
    if feud and feud.get("until", 0) <= t:
        await _js(conn, f"feud:{chat_id}", {})
    god = await _j(conn, f"god:{chat_id}")
    trib = god.get("tribute")
    if trib and trib.get("expires", 0) < t:
        god.pop("tribute", None)
        await _js(conn, f"god:{chat_id}", god)
        if trib.get("gid"):
            await strike(conn, chat_id, int(trib["gid"]), "не заплатила дань в срок")
    await _wars_tick(conn, chat_id)
    await _city_tick(conn, chat_id)
    gap_ok = t - await _i(conn, f"wtick:{chat_id}") >= CITIZEN_GAP
    if gap_ok:
        await _is(conn, f"wtick:{chat_id}", t)
        await _citizen_life(conn, chat_id)
    await _duel_tick(conn, chat_id, gap_ok)
    await _hero_tick(conn, chat_id)
    cur = await conn.execute("SELECT COUNT(*) FROM rp_player WHERE chat_id = ? AND user_id > 0", (chat_id,))
    if (await cur.fetchone())[0] < 2:
        return
    if 10 <= now_local().hour < 23:
        if not await _city_busy(conn, chat_id) and await _i(conn, f"citycd:{chat_id}") <= t and await P3._roll(conn, f"wcity:{chat_id}", CITY_MEAN):
            await _city_start(conn, chat_id)
        if await P3._roll(conn, f"wduel:{chat_id}", DUEL_MEAN):
            await _duel_start(conn, chat_id)


async def _city_busy(conn, chat_id: int) -> bool:
    return bool(await _j(conn, f"city:{chat_id}"))


async def tick(conn) -> None:
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM ai_citizen")
        for (chat_id,) in await cur.fetchall():
            try:
                await _tick_chat(conn, chat_id)
            except Exception:
                log.exception("Сбой мира района в чате %s", chat_id)
    except Exception:
        log.exception("Сбой мира района")


# ═════════════════════ налог банды с обычной работы ═════════════════════
# Какая «точка под крышей» стоит за обычной работой. Госслужба (пожарный, врач, экскаваторщик, пилот) не облагается.
JOB_POINT = {"courier": "shawarma", "chef": "shawarma", "taxi": "taxi", "streamer": "bar", "loader": "pawn", "hacker": "casino"}
WORK_TAX_BASE = 0.12


async def _point_tax(conn, chat_id: int, job_key: str):
    """(банда-хозяин, процент) точки, под которой идёт эта работа, или None."""
    key = JOB_POINT.get(job_key)
    if not key:
        return None
    cur = await conn.execute("SELECT owner, greed, rebel_until FROM racket WHERE chat_id = ? AND key = ? AND owner != 0", (chat_id, key))
    r = await cur.fetchone()
    if not r or (r[2] or 0) > now():
        return None
    import gangplus8 as P8
    pct = WORK_TAX_BASE * P8.GREED.get(r[1], 1.0)
    return r[0], pct


async def work_tax(conn, chat_id: int, job_key: str, pay: int, uid: int = 0):
    """Берёт налог с заработка обычной работы в общак банды, которая держит точку. Возвращает (налог, имя банды)."""
    if pay < 20:
        return 0, ""
    info = await _point_tax(conn, chat_id, job_key)
    if not info:
        return 0, ""
    gid, pct = info
    g = await gangwar._gang_row(conn, gid)
    if not g:
        return 0, ""
    if uid:
        import balance
        pct *= await balance.tax_factor(conn, chat_id, uid, gid)
        if pct <= 0:
            return 0, ""
    tax = max(1, int(pay * pct))
    await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (tax, gid))
    await _is(conn, f"rtax:{chat_id}:{gid}", await _i(conn, f"rtax:{chat_id}:{gid}") + tax)
    return tax, g["name"]


async def job_taxes(conn, chat_id: int) -> dict:
    """Для интерфейса: какие работы сейчас облагаются и в чью пользу."""
    out = {}
    for job in JOB_POINT:
        info = await _point_tax(conn, chat_id, job)
        if info:
            g = await gangwar._gang_row(conn, info[0])
            if g:
                out[job] = {"gang": g["name"], "pct": round(info[1] * 100), "ai": bool(g["is_ai"])}
    return out
