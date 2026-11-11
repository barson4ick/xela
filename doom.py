"""Судный день: сутки хард-режима для всего района.

kv `doom:{chat}`      — активное событие (JSON): start, until, snap {uid: деньги на старте}, wave_ts, waves, phase
kv `doomdead:{chat}`  — реестр погибших {uid: {name, ts, until, how}}; until = 0 — навсегда, иначе до конца события
kv `doomlast:{chat}`  — итог последнего события (для баннера)
Множители подмешиваются в liveevents.mods (работа, налоги, тюрьма, ночь, чёрные дела), rp._load (энергия),
gangplus10.police_mult (полиция), gangwar._pay_territories (доход районов), gangplus5.control_tick (дрейф контроля).
"""
import json
import logging
import random
import time

import aiosqlite

import gangwar
from config import DB_PATH

log = logging.getLogger(__name__)

HOUR = 3600
FIRST_WAVE = 20 * 60
WAVE_GAP = 90 * 60
LAST_SHOWN = 12 * HOUR
REWARDS = (1000, 600, 300)
HERO_MIN = 500  # герой суток — тот, кто за сутки хард-режима прибавил хотя бы столько

NEUTRAL = {"work_mult": 1.0, "sell_mult": 1.0, "tax_mult": 1.0, "jail_mult": 1.0, "police_mult": 1.0, "regen_mult": 1.0, "terr_mult": 1.0,
           "drift": 0, "night_block": False, "crime_pay_mult": 1.0, "crime_penalty": 0.0}

PHASES = [
    {"key": "sirens", "until": 6, "icon": "🚨", "title": "Сирены",
     "desc": "Город закрыт на карантин: работы меньше, полиция злее, налоги выросли.",
     "mods": {"work_mult": 0.8, "sell_mult": 0.9, "tax_mult": 1.1, "jail_mult": 1.5, "police_mult": 1.25, "regen_mult": 0.8, "terr_mult": 0.8,
              "drift": 1, "night_block": False, "crime_pay_mult": 1.25, "crime_penalty": 0.05}},
    {"key": "dark", "until": 18, "icon": "🌑", "title": "Тьма",
     "desc": "Самая тяжёлая часть суток: работы почти нет, энергия еле возвращается, районы рассыпаются, ночью дела закрыты.",
     "mods": {"work_mult": 0.6, "sell_mult": 0.7, "tax_mult": 1.25, "jail_mult": 2.0, "police_mult": 1.5, "regen_mult": 0.5, "terr_mult": 0.5,
              "drift": 2, "night_block": True, "crime_pay_mult": 1.5, "crime_penalty": 0.10}},
    {"key": "dawn", "until": 24, "icon": "🌅", "title": "Рассвет",
     "desc": "Последние часы: давление спадает, но волны ещё приходят. Продержись до утра.",
     "mods": {"work_mult": 0.8, "sell_mult": 0.9, "tax_mult": 1.1, "jail_mult": 1.5, "police_mult": 1.25, "regen_mult": 0.8, "terr_mult": 0.8,
              "drift": 1, "night_block": False, "crime_pay_mult": 1.25, "crime_penalty": 0.05}},
]


def now() -> int:
    return int(time.time())


async def _get(conn, key: str):
    raw = await gangwar.kget(conn, key)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


async def _set(conn, key: str, data) -> None:
    await gangwar.kset(conn, key, json.dumps(data, ensure_ascii=False))


async def _active(conn, chat_id: int) -> dict:
    d = await _get(conn, f"doom:{chat_id}")
    return d if d and int(d.get("until", 0)) > now() else {}


def _phase(d: dict, t: int) -> dict:
    hours = (t - int(d["start"])) / HOUR
    for p in PHASES:
        if hours < p["until"]:
            return p
    return PHASES[-1]


async def mods(conn, chat_id: int) -> dict:
    d = await _active(conn, chat_id)
    return {**NEUTRAL, **_phase(d, now())["mods"]} if d else dict(NEUTRAL)


async def regen_mult(conn, chat_id: int) -> float:
    return (await mods(conn, chat_id))["regen_mult"]


async def police_mult(conn, chat_id: int) -> float:
    return (await mods(conn, chat_id))["police_mult"]


async def terr_mult(conn, chat_id: int) -> float:
    return (await mods(conn, chat_id))["terr_mult"]


async def drift(conn, chat_id: int) -> int:
    return int((await mods(conn, chat_id))["drift"])


# ═════════════════════ погибшие ═════════════════════
async def _reg(conn, chat_id: int) -> dict:
    return await _get(conn, f"doomdead:{chat_id}")


async def dead_info(conn, chat_id: int, uid: int):
    r = (await _reg(conn, chat_id)).get(str(uid))
    if not r:
        return None
    return r if int(r.get("until", 0)) == 0 or int(r["until"]) > now() else None


async def is_dead(chat_id: int, uid: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        return bool(await dead_info(conn, chat_id, uid))


# ═════════════════════ запуск ═════════════════════
async def start(conn, chat_id: int, victims: list, hours: int = 24) -> dict:
    """victims: [{"uid", "name", "perm": bool, "how": str}]. Первые жертвы погибают в начале; perm — навсегда."""
    import rp
    t = now()
    if await _active(conn, chat_id):
        raise RuntimeError("Судный день уже идёт")
    reg = await _reg(conn, chat_id)
    until = t + hours * HOUR
    for v in victims:
        reg[str(v["uid"])] = {"name": v["name"], "ts": t, "until": 0 if v.get("perm") else until, "how": v.get("how", "")}
    dead_ids = {int(u) for u in reg}
    cur = await conn.execute("SELECT user_id, money FROM rp_player WHERE chat_id = ? AND user_id > 0", (chat_id,))
    snap = {str(u): int(m or 0) for u, m in await cur.fetchall() if int(u) not in dead_ids}
    d = {"start": t, "until": until, "snap": snap, "wave_ts": t + FIRST_WAVE - WAVE_GAP, "waves": 0, "phase": PHASES[0]["key"]}
    await _set(conn, f"doom:{chat_id}", d)
    await _set(conn, f"doomdead:{chat_id}", reg)
    names = ", ".join(v["name"] for v in victims)
    await rp._news(conn, chat_id, "☠ 🚨 СУДНЫЙ ДЕНЬ. Небо над районом вспыхнуло, сирены воют по всем кварталам. Сутки хард-режима: работы почти нет, "
                                  "полиция злая, районы рассыпаются, а энергия еле возвращается.")
    if victims:
        await rp._news(conn, chat_id, f"☠ 🕯 Первые жертвы: {names} не пережили первую ночь.")
    await rp._news(conn, chat_id, "☠ 📦 Совет: деньги в тайнике уцелеют при пожарах, а гуманитарная помощь приходит бедным. До рассвета ещё сутки.")
    return d


# ═════════════════════ волны ═════════════════════
def _t(key: str) -> str:
    t = gangwar.TERR[key]
    return f"{t[1]} {t[2]}"


async def _living(conn, chat_id: int, min_money: int = 0, max_money: int = 10 ** 9) -> list:
    reg = await _reg(conn, chat_id)
    cur = await conn.execute("SELECT r.user_id, r.money, COALESCE(u.username, 'житель') FROM rp_player r LEFT JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id "
                             "WHERE r.chat_id = ? AND r.user_id > 0 AND r.money >= ? AND r.money < ?", (chat_id, min_money, max_money))
    return [(int(u), int(m), n) for u, m, n in await cur.fetchall() if not (reg.get(str(u)) and (int(reg[str(u)].get("until", 0)) == 0 or int(reg[str(u)]["until"]) > now()))]


async def _owned(conn, chat_id: int, player_only: bool = False) -> list:
    q = "SELECT t.key, t.owner, g.name, g.treasury FROM territory t JOIN gang g ON g.id = t.owner WHERE t.chat_id = ? AND t.owner != 0 AND g.is_ai < 2"
    if player_only:
        q += " AND g.is_ai = 0"
    return list(await (await conn.execute(q, (chat_id,))).fetchall())


async def _w_marauders(conn, chat_id: int):
    rows = await _owned(conn, chat_id)
    if not rows:
        return None
    key, owner, gname, tr = random.choice(rows)
    loss = min(800, int(int(tr or 0) * 0.05))
    await conn.execute("UPDATE territory SET control = MAX(0, control - 12) WHERE chat_id = ? AND key = ?", (chat_id, key))
    if loss:
        await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (loss, owner))
    return f"🧟 Мародёры разграбили район {_t(key)}: «{gname}» теряет контроль −12" + (f" и {loss} ₽ из общака." if loss else ".")


async def _w_fire(conn, chat_id: int):
    people = await _living(conn, chat_id, 300)
    if not people:
        return None
    hit = random.sample(people, min(3, len(people)))
    for uid, money, _n in hit:
        loss = min(600, int(money * 0.06))
        await conn.execute("UPDATE rp_player SET money = MAX(0, money - ?) WHERE chat_id = ? AND user_id = ?", (loss, chat_id, uid))
    return f"🔥 Пожар на складах: припасы потеряли {', '.join(n for _u, _m, n in hit)} (до 600 ₽ у каждого). Деньги в тайнике уцелели."


async def _w_raid(conn, chat_id: int):
    import police
    rows = await _owned(conn, chat_id)
    if not rows:
        return None
    pick = random.sample(rows, min(2, len(rows)))
    for key, _o, _g, _t_ in pick:
        await conn.execute("UPDATE territory SET police = MIN(100, police + 10) WHERE chat_id = ? AND key = ?", (chat_id, key))
    gid, gname = random.choice(rows)[1:3]
    await police.case_add(conn, chat_id, gid, 6)
    return f"🚁 Зачистка: войска прошли по районам {', '.join(_t(k) for k, *_ in pick)} (давление +10), дело на «{gname}» +6."


async def _w_ration(conn, chat_id: int):
    poor = await _living(conn, chat_id, 0, 1500)
    if not poor:
        return None
    poor = random.sample(poor, min(10, len(poor)))
    for uid, _m, _n in poor:
        await conn.execute("UPDATE rp_player SET money = money + 150 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    return f"📦 Гуманитарная помощь: {len(poor)} жителей с пустыми карманами получили по 150 ₽."


async def _w_plague(conn, chat_id: int):
    people = await _living(conn, chat_id)
    if not people:
        return None
    uid, _m, name = random.choice(people)
    await conn.execute("UPDATE rp_player SET energy = MAX(0, energy - 30) WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    return f"☢ Заражённый воздух: у {name} лихорадка (⚡−30)."


async def _w_riot(conn, chat_id: int):
    rows = await _owned(conn, chat_id, True)
    if not rows:
        return None
    key, owner, gname, _tr = random.choice(rows)
    await conn.execute("UPDATE territory SET control = MAX(0, control - 10) WHERE chat_id = ? AND key = ?", (chat_id, key))
    return f"🏴 ИИ-банды штурмуют район {_t(key)}: «{gname}» теряет контроль −10."


WAVES = [(_w_marauders, 3), (_w_fire, 2), (_w_raid, 2), (_w_ration, 2), (_w_plague, 2), (_w_riot, 2)]


async def run_wave(conn, chat_id: int, only=None):
    """Одна случайная волна (или конкретная для теста). Возвращает текст или None."""
    import rp
    pool = [w for w, _ in WAVES if only is None or w is only]
    weights = [wt for w, wt in WAVES if only is None or w is only]
    order = []
    while pool:
        i = random.choices(range(len(pool)), weights)[0]
        order.append(pool.pop(i)); weights.pop(i)
    for w in order:
        text = await w(conn, chat_id)
        if text:
            await rp._news(conn, chat_id, "☠ " + text)
            return text
    return None


# ═════════════════════ фон ═════════════════════
async def _finish(conn, chat_id: int, d: dict) -> None:
    import rp
    reg = await _reg(conn, chat_id)
    t = now()
    snap = d.get("snap") or {}
    cur = await conn.execute("SELECT r.user_id, r.money, COALESCE(u.username, 'житель') FROM rp_player r LEFT JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id "
                             "WHERE r.chat_id = ? AND r.user_id > 0", (chat_id,))
    gains = []
    for uid, money, name in await cur.fetchall():
        if str(uid) in snap and not (reg.get(str(uid)) and int(reg[str(uid)].get("ts", 0)) >= int(d["start"])):
            gains.append((int(money) - int(snap[str(uid)]), int(uid), name))
    gains.sort(reverse=True)
    heroes = [g for g in gains if g[0] >= HERO_MIN][:len(REWARDS)]
    for (delta, uid, _n), reward in zip(heroes, REWARDS):
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (reward, chat_id, uid))
    back = [r["name"] for r in reg.values() if int(r.get("until", 0)) == int(d["until"])]
    gone = [r["name"] for r in reg.values() if int(r.get("until", 0)) == 0 and int(r.get("ts", 0)) >= int(d["start"])]
    last = {"ts": t, "heroes": [{"name": n, "delta": dl, "reward": rw} for (dl, _u, n), rw in zip(heroes, REWARDS)], "back": back, "gone": gone, "survivors": len(gains)}
    await _set(conn, f"doomlast:{chat_id}", last)
    await gangwar.kset(conn, f"doom:{chat_id}", "")
    text = f"☠ 🌅 Судный день окончен: выжили {len(gains)}."
    if heroes:
        text += " Герои: " + ", ".join(f"{n} (+{dl} ₽, награда {rw} ₽)" for (dl, _u, n), rw in zip(heroes, REWARDS)) + "."
    if gone:
        text += " Навсегда остались в тёмных днях: " + ", ".join(gone) + "."
    if back:
        text += " С рассветом вернулись: " + ", ".join(back) + "."
    await rp._news(conn, chat_id, text)


async def tick(conn) -> None:
    import rp
    try:
        rows = await (await conn.execute("SELECT key, value FROM kv WHERE key LIKE 'doom:%' AND value != ''")).fetchall()
    except Exception:
        log.exception("doom.tick: чтение")
        return
    for key, value in rows:
        try:
            chat_id = int(key.split(":")[1])
            d = json.loads(value)
            t = now()
            if int(d.get("until", 0)) <= t:
                await _finish(conn, chat_id, d)
                continue
            p = _phase(d, t)
            if d.get("phase") != p["key"]:
                d["phase"] = p["key"]
                await rp._news(conn, chat_id, f"☠ {p['icon']} Фаза «{p['title']}»: {p['desc']}")
            if t - int(d.get("wave_ts", 0)) >= WAVE_GAP:
                d["wave_ts"] = t + random.randint(0, 20 * 60)
                if await run_wave(conn, chat_id):
                    d["waves"] = int(d.get("waves", 0)) + 1
            await _set(conn, f"doom:{chat_id}", d)
        except Exception:
            log.exception("doom.tick: сбой в чате %s", key)


# ═════════════════════ состояние для интерфейса ═════════════════════
def _effects(m: dict) -> list:
    out = [f"Работа ×{m['work_mult']}", f"Продажи ×{m['sell_mult']}", f"Налоги ×{m['tax_mult']}", f"Срок тюрьмы ×{m['jail_mult']}", f"Полиция ×{m['police_mult']}",
           f"Энергия восстанавливается ×{m['regen_mult']}", f"Доход районов ×{m['terr_mult']}", f"Контроль районов −{m['drift']} каждые 30 мин (сверх обычного роста)"]
    if m["night_block"]:
        out.append("Ночью (23–7) чёрные дела закрыты")
    out.append(f"Чёрные дела: добыча ×{m['crime_pay_mult']}, шанс −{int(m['crime_penalty'] * 100)} п.п.")
    return out


async def state(conn, chat_id: int, uid: int) -> dict:
    t = now()
    d = await _active(conn, chat_id)
    reg = await _reg(conn, chat_id)
    me = await dead_info(conn, chat_id, uid)
    dead = [{"name": r["name"], "perm": int(r.get("until", 0)) == 0, "left": max(0, int(r.get("until", 0)) - t) if int(r.get("until", 0)) else 0}
            for r in reg.values() if int(r.get("until", 0)) == 0 or int(r["until"]) > t]
    out = {"active": bool(d), "dead": dead, "dead_me": bool(me), "perm_me": bool(me and int(me.get("until", 0)) == 0),
           "dead_left": max(0, int(me.get("until", 0)) - t) if me and int(me.get("until", 0)) else 0}
    if d:
        p = _phase(d, t)
        idx = PHASES.index(p)
        out.update({"left": int(d["until"]) - t, "phase": {"key": p["key"], "icon": p["icon"], "title": p["title"], "desc": p["desc"], "n": idx + 1, "of": len(PHASES)},
                    "phase_left": max(0, int(d["start"]) + p["until"] * HOUR - t), "effects": _effects({**NEUTRAL, **p["mods"]}),
                    "waves": int(d.get("waves", 0)), "next_wave": max(0, int(d.get("wave_ts", 0)) + WAVE_GAP - t)})
    else:
        last = await _get(conn, f"doomlast:{chat_id}")
        if last and t - int(last.get("ts", 0)) < LAST_SHOWN:
            out["last"] = last
    return out
