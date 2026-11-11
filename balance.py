"""Баланс экономики: налог и содержание общака, охрана точек и бунты, цена тюрьмы для банды,
санаторий (трата энергии на деньги), откуп вольных граждан от налога банд, счётчики потоков и снимки денежной массы."""
import logging
import random
import time

import aiosqlite

import gangplus3 as P3
import gangwar
import rp
from rooms import GameError
from utils import esc, now_local
from world import _i, _is, _j, _js, _say

log = logging.getLogger("balance")

HQ_PER_MEMBER = 40
TREAS_FREE, TREAS_FROM2 = 10000, 20000
TREAS_R1, TREAS_R2 = 0.025, 0.05
GUARD_PER_GREED = 10
REBEL_MEAN = {3: 36 * 3600, 2: 120 * 3600}
SPA_COST, SPA_GAIN, SPA_GAP, SPA_DAY = 350, 50, 3 * 3600, 2
BUYOUT_COST, BUYOUT_DAYS = 400, 7


def now() -> int:
    return int(time.time())


def day_key() -> str:
    return now_local().strftime("%Y-%m-%d")


async def flow(conn, chat_id: int, key: str, amount: int) -> None:
    """Счётчик денежных потоков за сутки (для сводки баланса)."""
    if not amount:
        return
    k = f"flow:{chat_id}:{day_key()}"
    d = await _j(conn, k)
    d[key] = d.get(key, 0) + int(amount)
    await _js(conn, k, d)


# ═════════════════════ общак: содержание штаба и налог на богатство ═════════════════════
def treasury_tax(treasury: int) -> int:
    t = max(0, int(treasury))
    a = max(0, min(t, TREAS_FROM2) - TREAS_FREE) * TREAS_R1
    b = max(0, t - TREAS_FROM2) * TREAS_R2
    return int(a + b)


async def _rackets_of(conn, chat_id: int, gid: int) -> list:
    cur = await conn.execute("SELECT key, greed FROM racket WHERE chat_id = ? AND owner = ?", (chat_id, gid))
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def estimate(conn, chat_id: int, gid: int) -> dict:
    g = await gangwar._gang_row(conn, gid)
    n = len(await gangwar._real_members(conn, gid))
    guard = sum(GUARD_PER_GREED * gr for _k, gr in await _rackets_of(conn, chat_id, gid))
    return {"hq": HQ_PER_MEMBER * n, "tax": 0 if (not g or await __import__("charter").chartered(conn, gid)) else treasury_tax(g["treasury"] or 0), "guard": guard}


async def _daily_gangs(conn, chat_id: int) -> None:
    import liveevents
    import police
    mult = (await liveevents.mods(conn, chat_id)).get("tax_mult", 1.0)
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
    for g in [dict(r) for r in await cur.fetchall()]:
        est = await estimate(conn, chat_id, g["id"])
        hq, tax = est["hq"], int(est["tax"] * mult)
        treasury = g["treasury"] or 0
        pay_hq = min(treasury, hq)
        pay_tax = min(treasury - pay_hq, tax)
        total = pay_hq + pay_tax
        guard_lines = []
        left = treasury - total
        for key, greed in await _rackets_of(conn, chat_id, g["id"]):
            cost = GUARD_PER_GREED * greed
            if left >= cost:
                left -= cost
                total += cost
            else:
                await conn.execute("UPDATE racket SET rebel_until = ?, pending = pending / 2 WHERE chat_id = ? AND key = ?", (now() + 12 * 3600, chat_id, key))
                guard_lines.append(key)
        if total:
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (total, g["id"]))
            await police.fund_add(conn, chat_id, total // 4)
            await flow(conn, chat_id, "gang_upkeep", total)
            parts = [f"штаб {pay_hq} ₽"]
            if pay_tax:
                parts.append(f"налог на общак {pay_tax} ₽")
            guard = total - pay_hq - pay_tax
            if guard:
                parts.append(f"охрана точек {guard} ₽")
            await _say(conn, chat_id, f"🏛 Расходы банды «{esc(g['name'])}» за сутки: {', '.join(parts)}.")
        if guard_lines:
            await _say(conn, chat_id, f"🔥 «{esc(g['name'])}» не потянула охрану: точки ({', '.join(guard_lines)}) взбунтовались на 12 часов.")


async def _snapshot(conn, chat_id: int) -> None:
    cur = await conn.execute("SELECT COUNT(*), COALESCE(SUM(money), 0) FROM rp_player WHERE chat_id = ? AND user_id > 0", (chat_id,))
    n, pm = await cur.fetchone()
    cur = await conn.execute("SELECT is_ai, COALESCE(SUM(treasury), 0) FROM gang WHERE chat_id = ? GROUP BY is_ai", (chat_id,))
    tr = {r[0]: r[1] for r in await cur.fetchall()}
    cur = await conn.execute("SELECT COALESCE(SUM(amount), 0) FROM stash WHERE chat_id = ?", (chat_id,)) if await _has_stash_chat(conn) else None
    stash = (await cur.fetchone())[0] if cur else 0
    await _js(conn, f"snap:{chat_id}:{day_key()}", {"players": n, "money": int(pm), "gangs": int(tr.get(0, 0)), "ai_gangs": int(tr.get(1, 0)),
                                                    "pfund": await _i(conn, f"pfund:{chat_id}"), "stash": int(stash)})


async def _has_stash_chat(conn) -> bool:
    cur = await conn.execute("PRAGMA table_info(stash)")
    cols = [r[1] for r in await cur.fetchall()]
    return "chat_id" in cols and "amount" in cols


# ═════════════════════ бунты точек от жадности ═════════════════════
async def _rebels(conn, chat_id: int) -> None:
    import police
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        "SELECT r.key, r.greed, r.owner, g.name FROM racket r JOIN gang g ON g.id = r.owner WHERE r.chat_id = ? AND r.owner != 0 AND g.is_ai = 0 AND r.rebel_until <= ? AND r.greed >= 2",
        (chat_id, now()))
    for r in [dict(x) for x in await cur.fetchall()]:
        mean = REBEL_MEAN.get(r["greed"])
        if mean and await P3._roll(conn, f"brb:{chat_id}:{r['key']}", mean):
            await conn.execute("UPDATE racket SET rebel_until = ?, pending = pending / 2 WHERE chat_id = ? AND key = ?", (now() + 6 * 3600, chat_id, r["key"]))
            await police.case_add(conn, chat_id, r["owner"], 2)
            await flow(conn, chat_id, "rebels", 1)
            import gangplus8 as P8
            await _say(conn, chat_id, f"🔥 Хозяева точки {P8.POINTS[r['key']][0]} {P8.POINTS[r['key']][1]} взбунтовались против поборов «{esc(r['name'])}»: 6 часов без дани и налога, полиция в курсе.")


# ═════════════════════ цена тюрьмы для банды ═════════════════════
async def on_jail(conn, chat_id: int, gid: int) -> None:
    g = await gangwar._gang_row(conn, gid)
    if not g or g["is_ai"]:
        return
    cost = min(400, max(50, int((g["treasury"] or 0) * 0.02)))
    cost = min(cost, g["treasury"] or 0)
    if cost:
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, gid))
        await flow(conn, chat_id, "jail_costs", cost)
    import gangplus5 as P5
    await P5.rep_add(conn, gid, -1, 0)


# ═════════════════════ санаторий и откуп от налога банд ═════════════════════
async def spa(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        t = now()
        if await _i(conn, f"spagap:{chat_id}:{uid}") > t:
            raise GameError(f"Санаторий снова через {gangwar_dur(await _i(conn, f'spagap:{chat_id}:{uid}') - t)}")
        d = await _j(conn, f"spaday:{chat_id}:{uid}")
        day = day_key()
        used = d.get("n", 0) if d.get("day") == day else 0
        if used >= SPA_DAY:
            raise GameError(f"В санатории лимит: {SPA_DAY} процедуры в сутки")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < SPA_COST:
            raise GameError(f"Нужно {SPA_COST} ₽")
        cap = rp.max_energy(p["home"])
        if p["energy"] >= cap - 5:
            raise GameError("Ты и так бодр: санаторий не нужен")
        gain = min(SPA_GAIN, int(cap - p["energy"]))
        p["money"] -= SPA_COST
        p["energy"] = min(cap, p["energy"] + gain)
        await rp._save(conn, chat_id, uid, p)
        await _is(conn, f"spagap:{chat_id}:{uid}", t + SPA_GAP)
        await _js(conn, f"spaday:{chat_id}:{uid}", {"day": day, "n": used + 1})
        import police
        await police.fund_add(conn, chat_id, SPA_COST // 4)
        await flow(conn, chat_id, "spa", SPA_COST)
        return P3._res(f"🛁 Санаторий: ⚡+{gain} за {SPA_COST} ₽. Процедур сегодня: {used + 1} из {SPA_DAY}.", True, -SPA_COST)
    return await P3._act(chat_id, uid, run)


def gangwar_dur(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600} ч {sec % 3600 // 60} мин" if sec >= 3600 else f"{max(1, sec // 60)} мин"


async def buyout(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        t = now()
        g, _ = await _my_gang_simple(conn, chat_id, uid)
        if g:
            raise GameError("Ты в банде: налог платят только вольные граждане")
        until = await _i(conn, f"taxfree:{chat_id}:{uid}")
        if until > t:
            raise GameError(f"Откуп уже оплачен ещё на {gangwar_dur(until - t)}")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < BUYOUT_COST:
            raise GameError(f"Нужно {BUYOUT_COST} ₽")
        p["money"] -= BUYOUT_COST
        await rp._save(conn, chat_id, uid, p)
        await _is(conn, f"taxfree:{chat_id}:{uid}", t + BUYOUT_DAYS * 86400)
        import police
        await police.fund_add(conn, chat_id, BUYOUT_COST // 2)
        await flow(conn, chat_id, "buyout", BUYOUT_COST)
        return P3._res(f"🧾 Откуп {BUYOUT_COST} ₽: банды не берут с тебя налог {BUYOUT_DAYS} дней.", True, -BUYOUT_COST)
    return await P3._act(chat_id, uid, run)


async def _my_gang_simple(conn, chat_id: int, uid: int):
    cur = await conn.execute("SELECT gang_id FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return (r[0] if r else None), ""


async def tax_factor(conn, chat_id: int, uid: int, gid: int) -> float:
    """Множитель налога банды с работяги: 0 — откуп, 0.5 — адвокат или защита этой банды."""
    t = now()
    if await _i(conn, f"taxfree:{chat_id}:{uid}") > t:
        return 0.0
    f = 1.0
    cur = await conn.execute("SELECT lawyer_until FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    if r and (r[0] or 0) > t:
        f = 0.5
    cur = await conn.execute("SELECT 1 FROM protection WHERE chat_id = ? AND user_id = ? AND gang_id = ? AND status = 'active' AND until > ?", (chat_id, uid, gid, t))
    if await cur.fetchone():
        f = min(f, 0.5)
    return f


# ═════════════════════ состояние и фон ═════════════════════
async def state(conn, chat_id: int, uid: int, gid: int) -> dict:
    t = now()
    d = await _j(conn, f"spaday:{chat_id}:{uid}")
    used = d.get("n", 0) if d.get("day") == day_key() else 0
    out = {"spa": {"cost": SPA_COST, "gain": SPA_GAIN, "wait": max(0, await _i(conn, f"spagap:{chat_id}:{uid}") - t), "used": used, "limit": SPA_DAY},
           "buyout": {"cost": BUYOUT_COST, "left": max(0, await _i(conn, f"taxfree:{chat_id}:{uid}") - t), "days": BUYOUT_DAYS}}
    if gid:
        out["upkeep"] = await estimate(conn, chat_id, gid)
    out.update(await charity_state(conn, chat_id, uid))
    return out


async def tick(conn) -> None:
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
        for (chat_id,) in await cur.fetchall():
            try:
                await _rebels(conn, chat_id)
                await _stall_check(conn, chat_id)
                await _daily_report(conn, chat_id)
                day = day_key()
                if await gangwar.kget(conn, f"baldaily:{chat_id}") != day:
                    await gangwar.kset(conn, f"baldaily:{chat_id}", day)
                    await _snapshot(conn, chat_id)
                    await _daily_gangs(conn, chat_id)
            except Exception:
                log.exception("Сбой баланса в чате %s", chat_id)
    except Exception:
        log.exception("Сбой баланса")


# ═════════════════════ престиж: благотворительность и пособие ═════════════════════
CHARITY = (1000, 5000, 20000)
CHARITY_TO_WELFARE = 0.30
TITLES = [(150000, "Благодетель", "👑"), (50000, "Почётный житель", "🎖"), (10000, "Меценат", "🏵"), (1000, "Спонсор района", "🎗")]
WELFARE_AMOUNT, WELFARE_POOR, WELFARE_POOR_TOTAL = 300, 1500, 3000


def title_of(total: int):
    for lim, name, icon in TITLES:
        if total >= lim:
            return icon, name
    return None


async def welfare_fund(conn, chat_id: int) -> int:
    return await _i(conn, f"welfare:{chat_id}")


async def welfare_add(conn, chat_id: int, amount: int) -> None:
    await _is(conn, f"welfare:{chat_id}", max(0, await welfare_fund(conn, chat_id) + int(amount)))


async def charity(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount)

    async def run(conn):
        if amount not in CHARITY:
            raise GameError("Взнос: 1 000, 5 000 или 20 000 ₽")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < amount:
            raise GameError(f"Нужно {amount} ₽")
        p["money"] -= amount
        await rp._save(conn, chat_id, uid, p)
        key = f"charity:{chat_id}:{uid}"
        before = await _i(conn, key)
        total = before + amount
        await _is(conn, key, total)
        await welfare_add(conn, chat_id, int(amount * CHARITY_TO_WELFARE))
        await flow(conn, chat_id, "charity", amount)
        old, new = title_of(before), title_of(total)
        text = f"🎗 Спасибо! Ты пожертвовал {amount} ₽ району (всего {total} ₽). Часть пойдёт на пособия для бедных."
        if new and new != old:
            await rp._news(conn, chat_id, f"{new[0]} {name} получил титул «{new[1]}» за благотворительность")
            await _say(conn, chat_id, f"{new[0]} <b>{esc(name)}</b> получил титул «{new[1]}» за пожертвования району (всего {total} ₽).")
            text += f" Новый титул: {new[0]} {new[1]}."
        return P3._res(text, True, -amount)
    return await P3._act(chat_id, uid, run)


async def welfare_claim(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        import gangplus11 as G11
        t = now()
        if await _i(conn, f"welfgap:{chat_id}:{uid}") > t:
            raise GameError("Пособие выдают раз в сутки")
        p = await rp._load(conn, chat_id, uid)
        stash = await G11.stash_get(conn, chat_id, uid)
        if p["money"] >= WELFARE_POOR or p["money"] + stash >= WELFARE_POOR_TOTAL:
            raise GameError(f"Пособие только тем, у кого меньше {WELFARE_POOR} ₽ на руках (и меньше {WELFARE_POOR_TOTAL} ₽ с тайником)")
        fund = await welfare_fund(conn, chat_id)
        if fund < WELFARE_AMOUNT:
            raise GameError("Фонд пособий пуст: его пополняют благотворители и налоги")
        await welfare_add(conn, chat_id, -WELFARE_AMOUNT)
        p["money"] += WELFARE_AMOUNT
        await rp._save(conn, chat_id, uid, p)
        await _is(conn, f"welfgap:{chat_id}:{uid}", t + 86400)
        await flow(conn, chat_id, "welfare", WELFARE_AMOUNT)
        return P3._res(f"🤝 Пособие из городского фонда: +{WELFARE_AMOUNT} ₽.", True, WELFARE_AMOUNT)
    return await P3._act(chat_id, uid, run)


async def _charity_board(conn, chat_id: int) -> list:
    cur = await conn.execute("SELECT key, value FROM kv WHERE key LIKE ?", (f"charity:{chat_id}:%",))
    rows = sorted(((int(k.rsplit(":", 1)[1]), int(v)) for k, v in await cur.fetchall()), key=lambda x: -x[1])[:5]
    out = []
    for u, tot in rows:
        ti = title_of(tot)
        out.append({"name": await P3._uname(conn, chat_id, u), "total": tot, "title": f"{ti[0]} {ti[1]}" if ti else ""})
    return out


async def charity_state(conn, chat_id: int, uid: int) -> dict:
    import gangplus11 as G11
    t = now()
    total = await _i(conn, f"charity:{chat_id}:{uid}")
    ti = title_of(total)
    nxt = next(((lim, name) for lim, name, _i2 in reversed(TITLES) if total < lim), None)
    p = await rp._load(conn, chat_id, uid)
    stash = await G11.stash_get(conn, chat_id, uid)
    poor = p["money"] < WELFARE_POOR and p["money"] + stash < WELFARE_POOR_TOTAL
    return {"charity": {"total": total, "title": f"{ti[0]} {ti[1]}" if ti else "", "next": list(nxt) if nxt else None, "amounts": list(CHARITY), "board": await _charity_board(conn, chat_id)},
            "welfare": {"fund": await welfare_fund(conn, chat_id), "amount": WELFARE_AMOUNT, "poor": poor, "wait": max(0, await _i(conn, f"welfgap:{chat_id}:{uid}") - t),
                        "limit": WELFARE_POOR}}


# ═════════════════════ санитарная проверка ларьков (ИИ-полиция) ═════════════════════
async def _stall_check(conn, chat_id: int) -> None:
    if not 10 <= now_local().hour < 22:
        return
    mode = (await gangwar.kget(conn, f"pai:{chat_id}")) or "normal"
    if mode == "off":
        return
    if not await P3._roll(conn, f"wstall:{chat_id}", 18 * 3600):
        return
    cur = await conn.execute(
        """SELECT user_id, stall, money, lawyer_until FROM rp_player WHERE chat_id = ? AND user_id > 0 AND stall >= 3
           AND user_id NOT IN (SELECT user_id FROM police_force WHERE chat_id = ?)""", (chat_id, chat_id))
    rows = await cur.fetchall()
    if not rows:
        return
    import police
    p = 0.75 if mode == "strict" else 0.5
    hit = []
    for uid, stall, money, lawyer in rows:
        if random.random() > p:
            continue
        fine = rp.STALL_INCOME[stall] * 5
        if (lawyer or 0) > now():
            fine //= 2
        fine = min(fine, money)
        if fine <= 0:
            continue
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, chat_id, uid))
        await police.fund_add(conn, chat_id, fine)
        await flow(conn, chat_id, "stall_fines", fine)
        hit.append(f"{esc(await P3._uname(conn, chat_id, uid))} ({fine} ₽)")
    cop = random.choice(["Сержант Петров", "Лейтенант Громова", "Капитан Сидоренко"])
    if hit:
        await _say(conn, chat_id, f"🚓 {cop} провёл санитарную проверку ларьков: оштрафованы {', '.join(hit)}. Штрафы в фонд полиции. Адвокат режет штраф вдвое.")
    else:
        await _say(conn, chat_id, f"🚓 {cop} проверил ларьки района и никого не нашёл. Повезло.")


# ═════════════════════ ежедневный отчёт для начальника полиции ═════════════════════
async def report_text(conn, chat_id: int) -> str:
    cur = await conn.execute("SELECT key, value FROM kv WHERE key LIKE ? ORDER BY key DESC LIMIT 2", (f"snap:{chat_id}:%",))
    snaps = await cur.fetchall()
    if len(snaps) < 2:
        return ""
    import json
    cur_s, prev_s = json.loads(snaps[0][1]), json.loads(snaps[1][1])
    yday = snaps[1][0].rsplit(":", 1)[1]
    tot = lambda s: s["money"] + s["gangs"] + s.get("stash", 0)
    a, b = tot(cur_s), tot(prev_s)
    delta = a - b
    pct = 100 * delta / max(1, b)
    cur = await conn.execute("SELECT money FROM rp_player WHERE chat_id = ? AND user_id > 0 ORDER BY money DESC", (chat_id,))
    vals = [r[0] for r in await cur.fetchall()]
    share = 100 * vals[0] // max(1, sum(vals)) if vals else 0
    flows = await _j(conn, f"flow:{chat_id}:{yday}")
    names = {"work": "зарплата с работы", "work_tax": "налог банд с работы", "upkeep_wealth": "содержание и налог на богатство", "gang_upkeep": "расходы банд", "jail_costs": "цена тюрьмы",
             "spa": "санаторий", "buyout": "откупы", "charity": "благотворительность", "welfare": "пособия", "stall_fines": "штрафы ларькам", "rebels": "бунтов точек"}
    lines = [f"📊 <b>Экономика за {yday}</b>",
             f"Денежная масса: {a} ₽ ({delta:+d}, {pct:+.1f}%): игроки {cur_s['money']}, общаки {cur_s['gangs']}, тайники {cur_s.get('stash', 0)}",
             f"Богатейший держит {share}% наличных, медиана {vals[len(vals) // 2] if vals else 0} ₽. Фонд полиции {cur_s['pfund']} ₽, фонд пособий {await welfare_fund(conn, chat_id)} ₽."]
    if flows:
        lines.append("Потоки: " + "; ".join(f"{names.get(k, k)} {v}" for k, v in sorted(flows.items())))
    warns = []
    if pct > 8:
        warns.append("масса растёт быстрее 8% в сутки: денег слишком легко, пора поднимать расходы")
    if pct < -5:
        warns.append("масса падает: стоки слишком жёсткие")
    if share > 50:
        warns.append("богатейший держит больше половины наличных")
    if warns:
        lines.append("⚠️ " + "; ".join(warns) + ".")
    return "\n".join(lines)


async def _daily_report(conn, chat_id: int) -> None:
    if now_local().hour < 9 or await gangwar.kget(conn, f"balrep:{chat_id}") == day_key():
        return
    text = await report_text(conn, chat_id)
    if not text:
        return
    await gangwar.kset(conn, f"balrep:{chat_id}", day_key())
    import police
    await conn.execute("INSERT INTO outbox (chat_id, text, voice, ts) VALUES (?, ?, 0, ?)", (police.OWNER_ID, text, now()))
