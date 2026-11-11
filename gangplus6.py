"""ОПГ-плюс 6: давление ИИ-банд. Они расширяются, воюют между собой, объявляют войны игрокам,
копят ресурсы и торгуют; чем дольше живёт город, тем сильнее давление."""
import logging
import random
import time

import aiosqlite

import gangplus as GP
import gangplus3 as P3
import gangplus5 as P5
import gangwar
import rp
from utils import esc, now_local

log = logging.getLogger(__name__)
AI_MAX_TERR = 5
AI_WAR_MEAN, AI_EXPAND_MEAN, AI_SKIRMISH_MEAN = 20 * 3600, 6 * 3600, 10 * 3600


def now() -> int:
    return int(time.time())


async def pressure(conn, chat_id: int) -> float:
    """Множитель давления 1.0–1.8: растёт с числом районов ИИ-банд и возрастом города."""
    cur = await conn.execute("SELECT COUNT(*) FROM territory t JOIN gang g ON g.id = t.owner WHERE t.chat_id = ? AND g.is_ai = 1", (chat_id,))
    terr = (await cur.fetchone())[0]
    cur = await conn.execute("SELECT MIN(created) FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    first = (await cur.fetchone())[0] or now()
    days = (now() - first) / 86400
    return 1.0 + min(0.4, 0.08 * terr) + min(0.4, 0.04 * days)


async def _ais(conn, chat_id: int) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    return [dict(r) for r in await cur.fetchall()]


async def _expand(conn, chat_id: int) -> None:
    own = await P5._owners(conn, chat_id)
    for ai in random.sample(await _ais(conn, chat_id), k=len(await _ais(conn, chat_id))):
        mine = [k for k, o in own.items() if o == ai["id"]]
        if len(mine) >= AI_MAX_TERR:
            continue
        free = [k for k, o in own.items() if o == 0 and (not mine or any(n in mine for n in P5.NEIGH[k]))]
        if not free:
            continue
        key = random.choice(free)
        await conn.execute("UPDATE territory SET owner = ?, last_pay = ? WHERE chat_id = ? AND key = ?", (ai["id"], now(), chat_id, key))
        t = gangwar.TERR[key]
        await rp._news(conn, chat_id, f"🏴 «{ai['name']}» заняла район {t[1]} {t[2]}")
        await gangwar.say(conn, chat_id, f"🏴 «{esc(ai['name'])}» заняла свободный район {t[1]} <b>{t[2]}</b>. Город делится, пока вы спите.")
        return


async def _skirmish(conn, chat_id: int) -> None:
    ais = await _ais(conn, chat_id)
    if len(ais) < 2:
        return
    own = await P5._owners(conn, chat_id)
    random.shuffle(ais)
    for a in ais:
        for b in ais:
            if a["id"] == b["id"]:
                continue
            ta = [k for k, o in own.items() if o == a["id"]]
            tb = [k for k, o in own.items() if o == b["id"]]
            border = [k for k in tb if any(n in ta for n in P5.NEIGH[k])]
            if border and len(tb) >= 2 and len(ta) >= len(tb) - 1:
                key = random.choice(border)
                await conn.execute("UPDATE territory SET owner = ?, last_pay = ? WHERE chat_id = ? AND key = ?", (a["id"], now(), chat_id, key))
                t = gangwar.TERR[key]
                await rp._news(conn, chat_id, f"⚔️ «{a['name']}» отбила у «{b['name']}» район {t[1]} {t[2]}")
                await gangwar.say(conn, chat_id, f"⚔️ Банды делят город: «{esc(a['name'])}» отбила у «{esc(b['name'])}» район {t[1]} <b>{t[2]}</b>.")
                return


async def _declare(conn, chat_id: int) -> None:
    t = now()
    ais = [a for a in await _ais(conn, chat_id) if not await gangwar._active_war(conn, a["id"]) and await P3.kget(conn, f"gwcd:{a['id']}") <= t]
    if not ais:
        return
    cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
    ai = random.choice(ais)
    cands = []
    for gid, gname in await cur.fetchall():
        if len(await gangwar._real_members(conn, gid)) < 2 or await gangwar._active_war(conn, gid) or await P3.kget(conn, f"gwcd:{gid}") > t:
            continue
        if await P3.pact_between(conn, ai["id"], gid, ("active",)) or await P5.truce_between(conn, ai["id"], gid):
            continue
        if await P5._borders(conn, chat_id, ai["id"], gid) is False:
            continue
        cands.append((gid, gname))
    if not cands:
        return
    gid, gname = random.choice(cands)
    start = int(8 * await pressure(conn, chat_id))
    await conn.execute("INSERT INTO gang_war (chat_id, a, b, started, ends, score_a) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, ai["id"], gid, t, t + gangwar.WAR_HOURS * 3600, start))
    await rp._news(conn, chat_id, f"⚔️ «{ai['name']}» объявила войну «{gname}»")
    await gangwar.say(conn, chat_id, f"⚔️ <b>«{esc(ai['name'])}» объявила войну «{esc(gname)}»!</b> Сутки на то, чтобы доказать, кто в районе хозяин. Счёт уже {start}:0: нападающие пришли подготовленными. Очки идут за дела, налёты, рейды и разведку; можно откупиться миром (Район → ОПГ → Карта).")
    import gangplus3
    await gangplus3.on_war(conn, chat_id, ai["id"], gid)


async def _economy(conn) -> None:
    """Раз в сутки ИИ-банды распродают склад и растят общак; сила идёт в давление."""
    day = now_local().strftime("%Y-%m-%d")
    cur = await conn.execute("SELECT id, chat_id FROM gang WHERE is_ai = 1")
    for gid, chat_id in await cur.fetchall():
        key = f"p6eco:{gid}"
        if await gangwar.kget(conn, key) == day:
            continue
        await gangwar.kset(conn, key, day)
        total = 0
        for res, qty in (await P5._stock(conn, gid)).items():
            sell = int(qty * 0.7)
            if sell:
                total += int(sell * P5.ITEMS[res][2] * P5.price_index(gid, res) * 0.6)
                await P5._stock_add(conn, gid, res, -sell)
        if total:
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (total, gid))


async def tick(conn) -> None:
    try:
        await _economy(conn)
        cur = await conn.execute("SELECT DISTINCT chat_id FROM gang WHERE is_ai = 1")
        for (chat_id,) in await cur.fetchall():
            if await P3._roll(conn, f"p6exp:{chat_id}", AI_EXPAND_MEAN):
                await _expand(conn, chat_id)
            if await P3._roll(conn, f"p6skir:{chat_id}", AI_SKIRMISH_MEAN):
                await _skirmish(conn, chat_id)
            if 9 <= now_local().hour < 23 and await P3._roll(conn, f"p6war:{chat_id}", AI_WAR_MEAN):
                await _declare(conn, chat_id)
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 6")
