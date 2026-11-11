"""Контроль района: капитан по назначению мэра, новые операции полиции (комендантский час,
опорный пункт, КПП) и влияние Крёстного отца на другие ОПГ (дань, саботаж, подкуп, натравливание).
Все операции работают и против ИИ-банд."""
import json
import logging
import random
import time

import gangwar
import police
from rooms import GameError
from utils import esc

log = logging.getLogger(__name__)

# ── капитан ──
APPOINT_CD = 6 * 3600

# ── полиция ──
CURFEW_COST, CURFEW_HOURS, CURFEW_CD, CURFEW_MAX = 600, 2, 15 * 60, 2
OUTPOST_COST, OUTPOST_MAX, OUTPOST_UPKEEP = 1500, 3, 150
KPP_COST, KPP_HOURS, KPP_CD, KPP_MAX = 800, 3, 10 * 60, 2

# ── ИИ-капитан ──
AI_CAPTAIN = (-9104, "Капитан Волков")
AI_CAP_GAP = 30 * 60

# ── Крёстный ──
LEVY_CD, LEVY_CAP = 12 * 3600, 1500
SAB_COST, SAB_CD = 1500, 8 * 3600
BRIBE_COST, BRIBE_CD, BRIBE_CAP = 800, 6 * 3600, 2000
INCITE_COST, INCITE_CD = 2000, 12 * 3600


def now() -> int:
    return int(time.time())


async def _i(conn, key: str) -> int:
    try:
        return int(await gangwar.kget(conn, key) or 0)
    except Exception:
        return 0


async def _is(conn, key: str, val) -> None:
    await gangwar.kset(conn, key, str(val))


async def _name(conn, chat_id: int, uid: int) -> str:
    import gangplus3 as P3
    return await P3._uname(conn, chat_id, uid)


async def _act(chat_id: int, uid: int, fn):
    import gangplus3 as P3
    return await P3._act(chat_id, uid, fn)


def _res(text: str, good: bool = True, delta: int = 0) -> dict:
    import gangplus3 as P3
    return P3._res(text, good, delta)


async def _active(conn, chat_id: int, prefix: str) -> list:
    """Активные записи kv вида prefix:{chat}:{key} -> [(key, value)]."""
    cur = await conn.execute("SELECT key, value FROM kv WHERE key LIKE ?", (f"{prefix}:{chat_id}:%",))
    return [(k.rsplit(":", 1)[1], v) for k, v in await cur.fetchall() if v]


async def _drop(conn, key: str) -> None:
    await conn.execute("DELETE FROM kv WHERE key = ?", (key,))


# ═════════════════════ капитан ═════════════════════
async def appoint_captain(chat_id: int, uid: int, name: str, target=0) -> dict:
    target = int(target or 0)

    async def run(conn):
        import power
        m = await power._mayor(conn, chat_id)
        if not m or m.get("ai") or int(m.get("uid", 0)) != uid:
            raise GameError("Капитана полиции назначает мэр")
        cur_cap = await police.chief_of(conn, chat_id)
        if target == 0:
            if cur_cap == AI_CAPTAIN[0]:
                raise GameError("Обязанности и так исполняет ИИ-капитан: выбери офицера и назначь его")
            cname = await _name(conn, chat_id, cur_cap) if cur_cap else ""
            await ensure_ai_captain(conn, chat_id)
            await gangwar.kset(conn, f"captain:{chat_id}", str(AI_CAPTAIN[0]))
            await police.ensure_captain(conn, chat_id)
            await gangwar.say(conn, chat_id, f"🏛 Мэр {esc(name)} снял {esc(cname)} с должности капитана полиции. Обязанности принял {AI_CAPTAIN[1]}.")
            return _res(f"🏛 Капитан снят. Пост не пустует: обязанности исполняет {AI_CAPTAIN[1]}.", True, 0)
        if target == uid:
            raise GameError("Мэр не может назначить капитаном самого себя")
        if target == 0 or not await police.is_officer(conn, chat_id, target):
            raise GameError("Капитаном может стать только действующий офицер")
        if cur_cap == target:
            raise GameError("Он уже капитан")
        if await _i(conn, f"capcd:{chat_id}") > now():
            raise GameError("Менять капитана можно раз в 6 часов")
        tname = await _name(conn, chat_id, target)
        await gangwar.kset(conn, f"captain:{chat_id}", str(target))
        await police.ensure_captain(conn, chat_id)
        await _is(conn, f"capcd:{chat_id}", now() + APPOINT_CD)
        await gangwar.say(conn, chat_id, f"🏛 <b>Мэр {esc(name)} назначил капитаном полиции {esc(tname)}.</b> Теперь у полиции есть начальник; мэр может давать ему приказы.")
        return _res(f"🏛 {tname} назначен капитаном полиции.", True, 0)
    return await _act(chat_id, uid, run)


# ═════════════════════ полиция: районы ═════════════════════
async def _district(conn, chat_id: int, uid: int, key: str) -> tuple:
    """Район под операцию: есть владелец (в т.ч. ИИ-банда), не своя банда, не под крышей мэра."""
    import power
    if key not in gangwar.TERR:
        raise GameError("Такого района нет")
    cur = await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
    row = await cur.fetchone()
    owner = int(row[0]) if row and row[0] else 0
    if not owner:
        raise GameError("Район никому не принадлежит: давить некого")
    if owner == await police._own_gang(conn, chat_id, uid):
        raise GameError("Конфликт интересов: свой район трогать нельзя")
    if await power.has_roof(conn, chat_id, owner):
        raise GameError("Мэр прикрыл эту семью: полиция не трогает её район")
    return owner, gangwar.TERR[key]


async def curfew(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        await police._need_cop(conn, chat_id, uid, True)
        owner, t = await _district(conn, chat_id, uid, key)
        act = await _active(conn, chat_id, "curfew")
        if any(int(v) > now() for _k, v in act if _k == key):
            raise GameError("В этом районе уже действует комендантский час")
        if sum(1 for _k, v in act if int(v) > now()) >= CURFEW_MAX:
            raise GameError(f"Одновременно не больше {CURFEW_MAX} комендантских часов")
        if await _i(conn, f"ctlcd:curfew:{chat_id}") > now():
            raise GameError("Комендантский час вводят раз в 15 минут")
        if await police.fund(conn, chat_id) < CURFEW_COST:
            raise GameError(f"В фонде нет {CURFEW_COST} ₽")
        await police.fund_add(conn, chat_id, -CURFEW_COST)
        until = now() + CURFEW_HOURS * 3600
        await _is(conn, f"curfew:{chat_id}:{key}", until)
        await _is(conn, f"ctlcd:curfew:{chat_id}", now() + CURFEW_CD)
        if await _i(conn, f"tblock:{chat_id}:{key}") < until:
            await _is(conn, f"tblock:{chat_id}:{key}", until)
        await police.case_add(conn, chat_id, owner, 2)
        await gangwar.say(conn, chat_id, f"🌙 <b>Комендантский час в районе {t[1]} {esc(t[2])}</b> на {CURFEW_HOURS} ч: давление полиции растёт, контроль банды падает, доход и поставки вдвое меньше.")
        return _res(f"🌙 Комендантский час в районе {t[2]} на {CURFEW_HOURS} ч (−{CURFEW_COST} ₽ из фонда).", True, 0)
    return await _act(chat_id, uid, run)


async def outpost(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        await police._need_cop(conn, chat_id, uid, True)
        owner, t = await _district(conn, chat_id, uid, key)
        act = await _active(conn, chat_id, "outpost")
        if any(k == key for k, _v in act):
            raise GameError("Опорный пункт здесь уже стоит")
        if len(act) >= OUTPOST_MAX:
            raise GameError(f"Не больше {OUTPOST_MAX} опорных пунктов")
        if await police.fund(conn, chat_id) < OUTPOST_COST:
            raise GameError(f"В фонде нет {OUTPOST_COST} ₽")
        await police.fund_add(conn, chat_id, -OUTPOST_COST)
        await _is(conn, f"outpost:{chat_id}:{key}", json.dumps({"since": now(), "by": uid}))
        await conn.execute("UPDATE territory SET police = MIN(100, police + 10) WHERE chat_id = ? AND key = ?", (chat_id, key))
        await gangwar.say(conn, chat_id, f"🏢 <b>Полиция открыла опорный пункт в районе {t[1]} {esc(t[2])}.</b> Постоянное давление на банду, содержание {OUTPOST_UPKEEP} ₽ в сутки из фонда.")
        return _res(f"🏢 Опорный пункт в районе {t[2]} открыт (−{OUTPOST_COST} ₽, содержание {OUTPOST_UPKEEP} ₽/сутки).", True, 0)
    return await _act(chat_id, uid, run)


async def outpost_close(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        await police._need_cop(conn, chat_id, uid, True)
        if not any(k == key for k, _v in await _active(conn, chat_id, "outpost")):
            raise GameError("Здесь нет опорного пункта")
        await _drop(conn, f"outpost:{chat_id}:{key}")
        return _res("🏢 Опорный пункт закрыт.", False, 0)
    return await _act(chat_id, uid, run)


async def kpp(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        await police._need_cop(conn, chat_id, uid)
        owner, t = await _district(conn, chat_id, uid, key)
        act = await _active(conn, chat_id, "kpp")
        if any(int(v) > now() for _k, v in act if _k == key):
            raise GameError("КПП на этом районе уже работает")
        if sum(1 for _k, v in act if int(v) > now()) >= KPP_MAX:
            raise GameError(f"Одновременно не больше {KPP_MAX} КПП")
        if await _i(conn, f"ctlcd:kpp:{chat_id}") > now():
            raise GameError("КПП выставляют раз в 10 минут")
        if await police.fund(conn, chat_id) < KPP_COST:
            raise GameError(f"В фонде нет {KPP_COST} ₽")
        await police.fund_add(conn, chat_id, -KPP_COST)
        until = now() + KPP_HOURS * 3600
        await _is(conn, f"kpp:{chat_id}:{key}", until)
        await _is(conn, f"ctlcd:kpp:{chat_id}", now() + KPP_CD)
        if await _i(conn, f"tblock:{chat_id}:{key}") < until:
            await _is(conn, f"tblock:{chat_id}:{key}", until)
        await gangwar.say(conn, chat_id, f"🚧 <b>КПП у района {t[1]} {esc(t[2])}</b> на {KPP_HOURS} ч: полиция проверяет грузы и изымает контрабанду в фонд.")
        return _res(f"🚧 КПП у района {t[2]} на {KPP_HOURS} ч (−{KPP_COST} ₽): изъятое пойдёт в фонд.", True, 0)
    return await _act(chat_id, uid, run)


# ═════════════════════ Крёстный: влияние на другие банды ═════════════════════
async def _godfather(conn, chat_id: int, uid: int) -> dict:
    import power
    d = await power._god(conn, chat_id)
    if not d or int(d.get("uid", 0)) != uid:
        raise GameError("Это может только Крёстный отец-игрок")
    return d


async def _target(conn, chat_id: int, d: dict, gid) -> dict:
    g = await gangwar._gang_row(conn, int(gid))
    if not g or g["chat_id"] != chat_id or int(g.get("is_ai") or 0) >= 2:
        raise GameError("Такой банды нет")
    if int(g["id"]) == int(d.get("gid", 0)):
        raise GameError("Свою семью трогать нельзя")
    return g


async def _report(conn, chat_id: int, uid: int, charge: str, sev: int) -> None:
    try:
        import justice
        await justice.report(conn, chat_id, uid, "godfather", charge, sev)
    except Exception:
        log.debug("justice.report не сработал", exc_info=True)


async def _save_god(conn, chat_id: int, d: dict) -> None:
    import power
    await power._set(conn, f"don:{chat_id}", d)


LEVY_MODES = {"soft": (0.06, "мягкая"), "normal": (0.12, "обычная"), "hard": (0.20, "жёсткая")}


async def levy(chat_id: int, uid: int, name: str, gid, mode="normal") -> dict:
    """Дань с чужой банды. Мягкая — мало, но без шума; жёсткая — много, но банда может взбунтоваться."""
    mode = mode if mode in LEVY_MODES else "normal"

    async def run(conn):
        import power
        d = await _godfather(conn, chat_id, uid)
        g = await _target(conn, chat_id, d, gid)
        levies = d.get("levy") or {}
        if now() - int(levies.get(str(g["id"]), 0)) < LEVY_CD:
            raise GameError("С этой банды дань уже брали: подожди")
        tr = int(g["treasury"] or 0)
        if tr < 300:
            raise GameError("В общаке у банды пусто: брать нечего")
        rate = LEVY_MODES[mode][0]
        cap = int(LEVY_CAP * (1.5 if mode == "hard" else 0.6 if mode == "soft" else 1))
        # Влияние Крёстного решает, сколько банда отдаст: 0 → ×0.6, 50 → ×1.0, 100 → ×1.4.
        cut = min(tr, max(100, min(cap, int(tr * rate * (0.6 + 0.8 * int(d.get("influence", 0)) / 100)))))
        levies[str(g["id"])] = now()
        d["levy"] = levies
        if mode == "hard" and random.random() < 0.3:
            d["influence"] = max(0, int(d.get("influence", 0)) - 6)
            await _save_god(conn, chat_id, d)
            await conn.execute("UPDATE gang SET respect = MIN(100, COALESCE(respect, 0) + 2) WHERE id = ?", (g["id"],))
            await gangwar.say(conn, chat_id, f"👑 «{esc(g['name'])}» отказалась платить жёсткую дань Крёстному и выставила его людей за дверь.")
            await _report(conn, chat_id, uid, "вымогательство дани", 3)
            return _res(f"👑 «{g['name']}» взбунтовалась: денег нет, влияние −6, детектив в курсе.", False, 0)
        await conn.execute("UPDATE gang SET treasury = treasury - ?, respect = MAX(0, COALESCE(respect, 0) - ?) WHERE id = ?", (cut, {"soft": 0, "normal": 1, "hard": 3}[mode], g["id"]))
        d["treasury"] = int(d.get("treasury", 0)) + cut
        d["influence"] = min(100, int(d.get("influence", 0)) + {"soft": 1, "normal": 2, "hard": 3}[mode])
        await _save_god(conn, chat_id, d)
        await gangwar.say(conn, chat_id, f"👑 Крёстный отец обложил «{esc(g['name'])}» данью ({LEVY_MODES[mode][1]}): {cut} ₽ ушли в тёмную кассу.")
        if mode != "soft":
            await _report(conn, chat_id, uid, "дань с чужой банды", 2 if mode == "normal" else 3)
        await power.mg_score(conn, chat_id, "god", 2)
        return _res(f"👑 С «{g['name']}» собрано {cut} ₽ дани ({LEVY_MODES[mode][1]})." + ("" if mode != "soft" else " Без шума: детектив не узнал."), True, cut)
    return await _act(chat_id, uid, run)


async def sabotage(chat_id: int, uid: int, name: str, gid) -> dict:
    async def run(conn):
        d = await _godfather(conn, chat_id, uid)
        g = await _target(conn, chat_id, d, gid)
        if now() - int(d.get("sab_ts", 0)) < SAB_CD:
            raise GameError("Саботаж раз в 8 часов")
        if int(d.get("treasury", 0)) < SAB_COST:
            raise GameError(f"Нужно {SAB_COST} ₽ из тёмной кассы")
        cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ? ORDER BY RANDOM() LIMIT 2", (chat_id, g["id"]))
        keys = [r[0] for r in await cur.fetchall()]
        if not keys:
            raise GameError("У этой банды нет районов: ломать нечего")
        d["treasury"] = int(d.get("treasury", 0)) - SAB_COST
        d["sab_ts"] = now()
        if random.random() < 0.2:
            d["influence"] = max(0, int(d.get("influence", 0)) - 8)
            await _save_god(conn, chat_id, d)
            await gangwar.say(conn, chat_id, f"🕵 Диверсантов Крёстного поймали на районе «{esc(g['name'])}». Заказчика все знают.")
            await _report(conn, chat_id, uid, "провалённый саботаж", 3)
            return _res(f"🕵 Диверсантов поймали: {SAB_COST} ₽ потеряны, влияние −8.", False, -SAB_COST)
        until = now() + 2 * 3600
        names = []
        for k in keys:
            await conn.execute("UPDATE territory SET control = MAX(0, control - 15) WHERE chat_id = ? AND key = ?", (chat_id, k))
            if await _i(conn, f"tblock:{chat_id}:{k}") < until:
                await _is(conn, f"tblock:{chat_id}:{k}", until)
            names.append(f"{gangwar.TERR[k][1]} {gangwar.TERR[k][2]}")
        await conn.execute("UPDATE racket SET pending = pending / 2 WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        await _save_god(conn, chat_id, d)
        await gangwar.say(conn, chat_id, f"💣 <b>Саботаж против «{esc(g['name'])}»:</b> {esc(', '.join(names))} — контроль −15, доход вдвое меньше на 2 ч, дань уполовинена.")
        await _report(conn, chat_id, uid, "саботаж против банды", 3)
        return _res(f"💣 Саботаж удался: {', '.join(names)}.", True, -SAB_COST)
    return await _act(chat_id, uid, run)


async def bribe_member(chat_id: int, uid: int, name: str, gid) -> dict:
    async def run(conn):
        d = await _godfather(conn, chat_id, uid)
        g = await _target(conn, chat_id, d, gid)
        if now() - int(d.get("brb_ts", 0)) < BRIBE_CD:
            raise GameError("Подкуп раз в 6 часов")
        if int(d.get("treasury", 0)) < BRIBE_COST:
            raise GameError(f"Нужно {BRIBE_COST} ₽ из тёмной кассы")
        tr = int(g["treasury"] or 0)
        if tr < 400:
            raise GameError("В общаке слишком мало: подкупать нет смысла")
        d["treasury"] = int(d.get("treasury", 0)) - BRIBE_COST
        d["brb_ts"] = now()
        if random.random() < 0.35:
            d["influence"] = max(0, int(d.get("influence", 0)) - 5)
            await _save_god(conn, chat_id, d)
            await gangwar.say(conn, chat_id, f"🕵 Человек Крёстного пытался подкупить участника «{esc(g['name'])}», но его сдали боссу.")
            await _report(conn, chat_id, uid, "попытка подкупа", 3)
            return _res(f"🕵 Участник не продался: {BRIBE_COST} ₽ потеряны, влияние −5.", False, -BRIBE_COST)
        steal = min(tr, max(200, min(BRIBE_CAP, int(tr * 0.10))))
        await conn.execute("UPDATE gang SET treasury = treasury - ?, respect = MAX(0, COALESCE(respect, 0) - 2) WHERE id = ?", (steal, g["id"]))
        d["treasury"] = int(d.get("treasury", 0)) + steal
        await _save_god(conn, chat_id, d)
        await gangwar.say(conn, chat_id, f"💼 Кто-то из «{esc(g['name'])}» слил часть общака людям Крёстного: пропало {steal} ₽.")
        await _report(conn, chat_id, uid, "подкуп участника банды", 3)
        return _res(f"💼 Подкуп сработал: из общака «{g['name']}» ушло {steal} ₽ (расход {BRIBE_COST} ₽).", True, steal - BRIBE_COST)
    return await _act(chat_id, uid, run)


async def incite(chat_id: int, uid: int, name: str, a, b) -> dict:
    async def run(conn):
        import gangplus3 as P3
        import gangplus5 as P5
        d = await _godfather(conn, chat_id, uid)
        ga = await _target(conn, chat_id, d, a)
        gb = await _target(conn, chat_id, d, b)
        if ga["id"] == gb["id"]:
            raise GameError("Выбери две разные банды")
        if now() - int(d.get("inc_ts", 0)) < INCITE_CD:
            raise GameError("Натравливать банды можно раз в 12 часов")
        if int(d.get("treasury", 0)) < INCITE_COST:
            raise GameError(f"Нужно {INCITE_COST} ₽ из тёмной кассы")
        if await gangwar._active_war(conn, ga["id"]) or await gangwar._active_war(conn, gb["id"]):
            raise GameError("Одна из банд уже воюет")
        if await P3.pact_between(conn, ga["id"], gb["id"], ("active",)) or await P5.truce_between(conn, ga["id"], gb["id"]):
            raise GameError("Между бандами действует союз или перемирие")
        d["treasury"] = int(d.get("treasury", 0)) - INCITE_COST
        d["inc_ts"] = now()
        t = now()
        await conn.execute("INSERT INTO gang_war (chat_id, a, b, started, ends, score_a) VALUES (?, ?, ?, ?, ?, 0)", (chat_id, ga["id"], gb["id"], t, t + gangwar.WAR_HOURS * 3600))
        await P3.on_war(conn, chat_id, ga["id"], gb["id"])
        await _save_god(conn, chat_id, d)
        await gangwar.say(conn, chat_id, f"🎭 <b>Крёстный отец стравил «{esc(ga['name'])}» и «{esc(gb['name'])}»!</b> Слух про предательство сработал: началась война.")
        await _report(conn, chat_id, uid, "натравил банды друг на друга", 4)
        return _res(f"🎭 «{ga['name']}» и «{gb['name']}» воюют (−{INCITE_COST} ₽ из тёмной кассы).", True, -INCITE_COST)
    return await _act(chat_id, uid, run)


# ═════════════════════ ИИ-капитан ═════════════════════
async def ensure_ai_captain(conn, chat_id: int) -> None:
    """ИИ-офицер «Капитан Волков» всегда числится в полиции (его можно назначить капитаном)."""
    uid, nm = AI_CAPTAIN
    await conn.execute("INSERT OR IGNORE INTO users (user_id, chat_id, username, emoji) VALUES (?, ?, ?, ?)", (uid, chat_id, nm, "🚓"))
    await conn.execute("INSERT OR IGNORE INTO police_force (chat_id, user_id, rank, joined) VALUES (?, ?, 'officer', ?)", (chat_id, uid, now()))


async def _district_ok(conn, chat_id: int, key: str) -> int:
    import power
    cur = await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
    row = await cur.fetchone()
    owner = int(row[0]) if row and row[0] else 0
    if not owner or await power.has_roof(conn, chat_id, owner):
        return 0
    return owner


PROP_TTL = 45 * 60
PROP_BONUS = 1.25
OP_NAMES = {"raid": "облаву", "big": "громкое дело"}


async def _cap_exec(conn, chat_id: int, nm: str, op: str, g: dict, bonus: float = 1.0, note: str = "") -> str:
    """Облава или громкое дело капитана против банды. bonus > 1 — мэр дал добро, изъятие больше."""
    import rp
    t = now()
    row = await gangwar._gang_row(conn, int(g["id"]))
    if not row or op not in OP_NAMES:
        return ""
    tr = int(row["treasury"] or 0)
    if op == "big":
        await _is(conn, f"ctlcap:big:{chat_id}", t)
        conf = min(tr, int(tr * 0.30 * bonus))
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (conf, g["id"]))
        await police.fund_add(conn, chat_id, conf)
        await conn.execute("UPDATE territory SET police = MIN(100, police + 10), control = MAX(0, control - 10) WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        await conn.execute("UPDATE police_case SET pts = 30, wiretap_until = 0 WHERE chat_id = ? AND gang_id = ?", (chat_id, g["id"]))
        text = f"⚖️ {nm} закрыл громкое дело против «{row['name']}» ({note}конфисковано {conf} ₽ в фонд, давление на районы +10)"
    else:
        await _is(conn, f"ctlcap:raid:{chat_id}", t)
        conf = min(tr, int(min(2500, tr * 0.15) * bonus))
        await police.fund_add(conn, chat_id, -police.RAID_COST)
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (conf, g["id"]))
        await police.fund_add(conn, chat_id, conf)
        await conn.execute("UPDATE territory SET police = MIN(100, police + 8), control = MAX(0, control - 6) WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        await conn.execute("UPDATE police_case SET pts = pts * 0.5 WHERE chat_id = ? AND gang_id = ?", (chat_id, g["id"]))
        text = f"🚨 {nm} провёл облаву на «{row['name']}» ({note}изъято {conf} ₽ в фонд, контроль −6)"
    await rp._news(conn, chat_id, text)
    return text


async def decide(chat_id: int, uid: int, name: str, accept=True) -> dict:
    """Мэр отвечает на предложение капитана: добро (изъятие ×1.25) или вето (операции не будет, детектив запомнит)."""
    accept = accept in (True, 1, "1", "true", "True")

    async def run(conn):
        import power
        import rp
        m = await power._mayor(conn, chat_id)
        if not m or m.get("ai") or int(m.get("uid", 0)) != uid:
            raise GameError("Решение по операциям капитана принимает мэр")
        prop = await power._get(conn, f"capprop:{chat_id}")
        if not prop or int(prop.get("expires", 0)) <= now():
            raise GameError("Предложений от капитана сейчас нет")
        await gangwar.kset(conn, f"capprop:{chat_id}", "")
        op, gid = prop.get("op", ""), int(prop.get("gid", 0))
        if accept:
            text = await _cap_exec(conn, chat_id, prop.get("by", "Капитан"), op, {"id": gid}, PROP_BONUS, "с санкции мэра, ")
            if not text:
                raise GameError("Банды уже нет: операция отменена")
            return _res(f"✅ Добро дано. {text}", True, 0)
        await _is(conn, f"ctlcap:{op}:{chat_id}", now())
        try:
            import justice
            await justice.report(conn, chat_id, uid, "mayor", "вето на операцию полиции против банды", 2)
        except Exception:
            log.debug("justice.report не сработал", exc_info=True)
        await rp._news(conn, chat_id, f"🛑 Мэр {name} запретил капитану {OP_NAMES.get(op, 'операцию')} против «{prop.get('gname', '')}»")
        return _res(f"🛑 Вето: капитан не тронет «{prop.get('gname', '')}» ближайшие часы. Фонд цел, но детектив Кротов взял это на карандаш.", True, 0)
    return await _act(chat_id, uid, run)


async def _ai_captain(conn, chat_id: int) -> None:
    """Если капитан — ИИ, он сам ведёт дела: прослушки, облавы, громкие дела и давление на районы банд."""
    import rp
    cap = await police.chief_of(conn, chat_id)
    if not cap or cap > 0:
        return
    t = now()
    if t - await _i(conn, f"ctlcap:{chat_id}") < AI_CAP_GAP:
        return
    await _is(conn, f"ctlcap:{chat_id}", t)
    if (await gangwar.kget(conn, f"pai:{chat_id}") or "normal") == "off":
        return
    nm = AI_CAPTAIN[1] if cap == AI_CAPTAIN[0] else await _name(conn, chat_id, cap)
    cur = await conn.execute("SELECT id, name, is_ai, treasury FROM gang WHERE chat_id = ? AND is_ai IN (0, 1)", (chat_id,))
    gangs = []
    for gid, gname, gai, tr in await cur.fetchall():
        c = await police._case(conn, chat_id, gid)
        gangs.append({"id": gid, "name": gname, "ai": bool(gai), "treasury": int(tr or 0), "pts": float(c["pts"] or 0), "wire": int(c["wiretap_until"] or 0)})
    if not gangs:
        return
    gangs.sort(key=lambda x: -x["pts"])
    import power
    # 1. ИИ-банды: прослушка → облава → громкое дело (по своим интервалам)
    mayor = await power._mayor(conn, chat_id)
    ask = bool(mayor and not mayor.get("ai") and int(mayor.get("uid", 0)) > 0)
    prop = await power._get(conn, f"capprop:{chat_id}")
    if prop and int(prop.get("expires", 0)) <= t:
        # Мэр промолчал: капитан действует сам, без бонуса.
        await gangwar.kset(conn, f"capprop:{chat_id}", "")
        g0 = next((x for x in gangs if x["id"] == int(prop.get("gid", 0))), None)
        if g0:
            await _cap_exec(conn, chat_id, nm, prop.get("op", ""), g0, 1.0, "мэр не ответил, ")
        prop = {}
    for g in gangs:
        if not g["ai"] or await power.has_roof(conn, chat_id, g["id"]):
            continue
        fund = await police.fund(conn, chat_id)
        op = ""
        if g["pts"] >= police.CASE_BIG and fund >= 500 and await _i(conn, f"ctlcap:big:{chat_id}") < t - 12 * 3600:
            op = "big"
        elif g["pts"] >= 60 and fund >= police.RAID_COST + 500 and await _i(conn, f"ctlcap:raid:{chat_id}") < t - 4 * 3600:
            op = "raid"
        if op:
            if prop:
                break  # ждём ответа мэра по прошлому предложению
            if ask:
                await power._set(conn, f"capprop:{chat_id}", {"id": t, "op": op, "gid": g["id"], "gname": g["name"], "by": nm, "expires": t + PROP_TTL})
                await rp._news(conn, chat_id, f"📋 {nm} просит у мэра добро на {OP_NAMES[op]} против «{g['name']}»")
            else:
                await _cap_exec(conn, chat_id, nm, op, g, 1.0)
            break
        if g["pts"] >= 30 and g["wire"] <= t and fund >= police.WIRETAP_COST + 500 and await _i(conn, f"ctlcap:wire:{chat_id}") < t - 3 * 3600:
            await _is(conn, f"ctlcap:wire:{chat_id}", t)
            await police.fund_add(conn, chat_id, -police.WIRETAP_COST)
            await conn.execute("UPDATE police_case SET wiretap_until = ? WHERE chat_id = ? AND gang_id = ?", (t + 12 * 3600, chat_id, g["id"]))
            await police.case_add(conn, chat_id, g["id"], 3)
            await rp._news(conn, chat_id, f"🎧 {nm} поставил прослушку у «{g['name']}» на 12 часов")
            break
    # 2. давление на районы самой «подозрительной» банды (игровые — только при деле от 60)
    target = next((g for g in gangs if g["ai"] or g["pts"] >= 60), None)
    if not target or await power.has_roof(conn, chat_id, target["id"]):
        return
    cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ? ORDER BY control DESC", (chat_id, target["id"]))
    keys = [r[0] for r in await cur.fetchall()]
    if not keys:
        return
    fund = await police.fund(conn, chat_id)
    curfews = [k for k, v in await _active(conn, chat_id, "curfew") if int(v) > t]
    kpps = [k for k, v in await _active(conn, chat_id, "kpp") if int(v) > t]
    posts = [k for k, _v in await _active(conn, chat_id, "outpost")]
    key = keys[0]
    tn = gangwar.TERR[key][2]
    if len(posts) < OUTPOST_MAX and key not in posts and fund >= OUTPOST_COST + 1500 and random.random() < 0.3:
        await police.fund_add(conn, chat_id, -OUTPOST_COST)
        await _is(conn, f"outpost:{chat_id}:{key}", json.dumps({"since": t, "by": cap}))
        await rp._news(conn, chat_id, f"🏢 {nm} открыл опорный пункт в районе {tn}")
    elif len(curfews) < CURFEW_MAX and key not in curfews and fund >= CURFEW_COST + 800 and random.random() < 0.5:
        await police.fund_add(conn, chat_id, -CURFEW_COST)
        until = t + CURFEW_HOURS * 3600
        await _is(conn, f"curfew:{chat_id}:{key}", until)
        if await _i(conn, f"tblock:{chat_id}:{key}") < until:
            await _is(conn, f"tblock:{chat_id}:{key}", until)
        await rp._news(conn, chat_id, f"🌙 {nm} ввёл комендантский час в районе {tn} на {CURFEW_HOURS} ч")
    elif len(kpps) < KPP_MAX and key not in kpps and fund >= KPP_COST + 500:
        await police.fund_add(conn, chat_id, -KPP_COST)
        until = t + KPP_HOURS * 3600
        await _is(conn, f"kpp:{chat_id}:{key}", until)
        if await _i(conn, f"tblock:{chat_id}:{key}") < until:
            await _is(conn, f"tblock:{chat_id}:{key}", until)
        await rp._news(conn, chat_id, f"🚧 {nm} выставил КПП у района {tn} на {KPP_HOURS} ч")


# ═════════════════════ фон ═════════════════════
async def _tick_chat(conn, chat_id: int) -> None:
    import power
    t = now()
    await ensure_ai_captain(conn, chat_id)
    if not await police.chief_of(conn, chat_id):
        # Пост капитана не пустует: если игрока-капитана нет, обязанности берёт ИИ.
        await gangwar.kset(conn, f"captain:{chat_id}", str(AI_CAPTAIN[0]))
        await police.ensure_captain(conn, chat_id)
        import rp
        await rp._news(conn, chat_id, f"🚓 Пост капитана полиции пустовал: обязанности принял {AI_CAPTAIN[1]}")
    # комендантский час: каждые 5 минут
    if t - await _i(conn, f"ctl5:{chat_id}") >= 300:
        await _is(conn, f"ctl5:{chat_id}", t)
        for key, v in await _active(conn, chat_id, "curfew"):
            if int(v) <= t:
                await _drop(conn, f"curfew:{chat_id}:{key}")
                continue
            cur = await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
            row = await cur.fetchone()
            owner = int(row[0]) if row and row[0] else 0
            if owner and not await power.has_roof(conn, chat_id, owner):
                await conn.execute("UPDATE territory SET police = MIN(100, police + 3), control = MAX(0, control - 2) WHERE chat_id = ? AND key = ?", (chat_id, key))
                await police.case_add(conn, chat_id, owner, 0.5)
    # опорные пункты и КПП: каждые 10 минут
    if t - await _i(conn, f"ctl10:{chat_id}") >= 600:
        await _is(conn, f"ctl10:{chat_id}", t)
        import rp
        for key, _v in await _active(conn, chat_id, "outpost"):
            cur = await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
            row = await cur.fetchone()
            owner = int(row[0]) if row and row[0] else 0
            if owner and not await power.has_roof(conn, chat_id, owner):
                await conn.execute("UPDATE territory SET police = MIN(100, police + 3), control = MAX(0, control - 2) WHERE chat_id = ? AND key = ?", (chat_id, key))
                await police.case_add(conn, chat_id, owner, 0.3)
        for key, v in await _active(conn, chat_id, "kpp"):
            if int(v) <= t:
                await _drop(conn, f"kpp:{chat_id}:{key}")
                continue
            cur = await conn.execute("SELECT owner FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
            row = await cur.fetchone()
            owner = int(row[0]) if row and row[0] else 0
            if not owner or await power.has_roof(conn, chat_id, owner):
                continue
            g = await gangwar._gang_row(conn, owner)
            cur = await conn.execute("SELECT res, qty FROM gang_stock WHERE gang_id = ? AND qty > 2 ORDER BY RANDOM() LIMIT 1", (owner,))
            st = await cur.fetchone()
            tname = gangwar.TERR[key][2]
            if st:
                take = min(int(st[1]), random.randint(3, 8))
                await conn.execute("UPDATE gang_stock SET qty = MAX(0, qty - ?) WHERE gang_id = ? AND res = ?", (take, owner, st[0]))
                await police.fund_add(conn, chat_id, take * 50)
                await police.case_add(conn, chat_id, owner, 1)
                await rp._news(conn, chat_id, f"🚧 КПП у района {tname} изъял {take} ед. груза «{g['name'] if g else '?'}»: фонд +{take * 50} ₽")
            elif g and int(g["treasury"] or 0) > 800 and random.random() < 0.4:
                conf = min(300, int(g["treasury"] * 0.03))
                await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (conf, owner))
                await police.fund_add(conn, chat_id, conf)
                await rp._news(conn, chat_id, f"🚧 КПП у района {tname} конфисковал {conf} ₽ у «{g['name']}» в фонд полиции")
    # содержание опорных пунктов: раз в сутки
    day = time.strftime("%Y-%m-%d")
    if await gangwar.kget(conn, f"ctlday:{chat_id}") != day:
        await gangwar.kset(conn, f"ctlday:{chat_id}", day)
        for key, _v in await _active(conn, chat_id, "outpost"):
            if await police.fund(conn, chat_id) >= OUTPOST_UPKEEP:
                await police.fund_add(conn, chat_id, -OUTPOST_UPKEEP)
            else:
                await _drop(conn, f"outpost:{chat_id}:{key}")
                import rp
                await rp._news(conn, chat_id, f"🏢 Опорный пункт в районе {gangwar.TERR[key][2]} закрыт: в фонде нет денег на содержание")
    # дело на ИИ-банды растёт само (чтобы по ним тоже были основания для облавы)
    if t - await _i(conn, f"ctlai:{chat_id}") >= 1800:
        await _is(conn, f"ctlai:{chat_id}", t)
        cur = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
        for (gid,) in await cur.fetchall():
            terr = (await (await conn.execute("SELECT COUNT(*) FROM territory WHERE owner = ?", (gid,))).fetchone())[0]
            await police.case_add(conn, chat_id, gid, 1.0 + 0.4 * terr)
    await _ai_captain(conn, chat_id)


async def tick(conn) -> None:
    try:
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
        for (chat_id,) in await cur.fetchall():
            await _tick_chat(conn, chat_id)
    except Exception:
        log.exception("Сбой фоновых задач контроля")


# ═════════════════════ состояние для мини-аппа ═════════════════════
async def state(conn, chat_id: int, uid: int) -> dict:
    import power
    t = now()
    m = await power._mayor(conn, chat_id)
    d = await power._god(conn, chat_id)
    role = await police._role(conn, chat_id, uid)
    chief = await police.chief_of(conn, chat_id)
    is_mayor = bool(m and not m.get("ai") and int(m.get("uid", 0)) == uid)
    cap = {"id": chief or 0, "name": await _name(conn, chat_id, chief) if chief else "", "is_mayor": is_mayor,
           "wait": max(0, await _i(conn, f"capcd:{chat_id}") - t), "candidates": []}
    if is_mayor:
        cur = await conn.execute("SELECT user_id FROM police_force WHERE chat_id = ? AND user_id != ?", (chat_id, uid))
        cap["candidates"] = [{"id": r[0], "name": await _name(conn, chat_id, r[0]), "ai": r[0] < 0} for r in await cur.fetchall()]
    if is_mayor:
        prop = await power._get(conn, f"capprop:{chat_id}")
        if prop and int(prop.get("expires", 0)) > t:
            cap["proposal"] = {"op": prop.get("op"), "title": OP_NAMES.get(prop.get("op"), "операцию"), "gname": prop.get("gname", ""), "by": prop.get("by", ""),
                               "left": int(prop["expires"]) - t, "bonus": int((PROP_BONUS - 1) * 100)}
    out = {"captain": cap}
    if role:
        def tn(k):
            return f"{gangwar.TERR[k][1]} {gangwar.TERR[k][2]}" if k in gangwar.TERR else k
        out["police"] = {
            "role": role,
            "curfews": [{"key": k, "name": tn(k), "left": int(v) - t} for k, v in await _active(conn, chat_id, "curfew") if int(v) > t],
            "outposts": [{"key": k, "name": tn(k)} for k, _v in await _active(conn, chat_id, "outpost")],
            "kpps": [{"key": k, "name": tn(k), "left": int(v) - t} for k, v in await _active(conn, chat_id, "kpp") if int(v) > t],
            "waits": {"curfew": max(0, await _i(conn, f"ctlcd:curfew:{chat_id}") - t), "kpp": max(0, await _i(conn, f"ctlcd:kpp:{chat_id}") - t)},
            "costs": {"curfew": CURFEW_COST, "outpost": OUTPOST_COST, "kpp": KPP_COST, "upkeep": OUTPOST_UPKEEP},
            "max": {"curfew": CURFEW_MAX, "outpost": OUTPOST_MAX, "kpp": KPP_MAX},
        }
    if d and int(d.get("uid", 0)) == uid:
        cur = await conn.execute(
            "SELECT g.id, g.name, g.is_ai, g.treasury, (SELECT COUNT(*) FROM territory x WHERE x.owner = g.id) FROM gang g "
            "WHERE g.chat_id = ? AND g.is_ai IN (0, 1) AND g.id != ?", (chat_id, int(d.get("gid", 0))))
        levies = d.get("levy") or {}
        out["god"] = {
            "targets": [{"id": r[0], "name": r[1], "ai": bool(r[2]), "treasury": int(r[3] or 0), "terr": r[4],
                         "levy_wait": max(0, int(levies.get(str(r[0]), 0)) + LEVY_CD - t)} for r in await cur.fetchall()],
            "waits": {"sab": max(0, int(d.get("sab_ts", 0)) + SAB_CD - t), "bribe": max(0, int(d.get("brb_ts", 0)) + BRIBE_CD - t),
                      "incite": max(0, int(d.get("inc_ts", 0)) + INCITE_CD - t)},
            "costs": {"sab": SAB_COST, "bribe": BRIBE_COST, "incite": INCITE_COST},
            "treasury": int(d.get("treasury", 0)),
        }
    return out
