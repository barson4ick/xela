"""ОПГ-плюс 8: доска контрактов, бригада NPC-шестёрок, крыша для бизнеса."""
import logging
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
from utils import esc, now_local

log = logging.getLogger(__name__)


def now() -> int:
    return int(time.time())


def today() -> str:
    return now_local().strftime("%Y-%m-%d")


async def _gx(conn, gid: int) -> Optional[dict]:
    return await gangwar._gang_row(conn, gid)


async def _need_dep(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g or g.get("is_ai"):
        raise GameError("Ты не состоишь в ОПГ")
    role = next(m["role"] for m in members if m["user_id"] == uid)
    if role == "grunt":
        raise GameError("Это дело босса и замов")
    return g, members, role


# ═════════════════════ доска контрактов ═════════════════════
CONTRACTS = {
    "stall": ("🌯", "Отжать ларёк", "Припугнуть хозяина ларька и забрать выручку", 15, 15, 0.50, 1),
    "witness": ("😨", "Напугать свидетеля", "Свидетель что-то видел. Пусть передумает", 10, 10, 0.55, 1),
    "car": ("🚗", "Перегнать тачку", "Чужая тачка должна оказаться в другом районе", 15, 20, 0.50, 1),
    "bribe": ("📨", "Занести в полицию", "Конверт для нужного человека (300 ₽ вперёд)", 5, 10, 0.80, 0),
    "leak": ("🔎", "Найти, кто сливает", "Тихо выяснить, откуда утекает информация", 10, 15, 0.60, 0),
}
CONTRACT_DAILY = 3
FLAT_GIFTS = ["candle", "plant", "teddy"]


async def _offers(conn, chat_id: int, uid: int) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM contract WHERE chat_id = ? AND user_id = ? AND day = ? ORDER BY id", (chat_id, uid, today()))
    rows = [dict(r) for r in await cur.fetchall()]
    if rows:
        return rows
    cur = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    in_gang = bool(await cur.fetchone())
    for kind in random.sample(list(CONTRACTS), CONTRACT_DAILY):
        if kind == "bribe":
            rkind, reward = "cop", 4
        else:
            rkind = random.choice(["money", "money", "merit", "item"] if in_gang else ["money", "money", "item"])
            reward = {"money": random.randint(250, 700), "merit": random.randint(5, 9), "item": 0}[rkind]
        await conn.execute("INSERT INTO contract (chat_id, user_id, day, kind, rkind, reward, status) VALUES (?, ?, ?, ?, ?, ?, 'offer')", (chat_id, uid, today(), kind, rkind, reward))
    cur = await conn.execute("SELECT * FROM contract WHERE chat_id = ? AND user_id = ? AND day = ? ORDER BY id", (chat_id, uid, today()))
    return [dict(r) for r in await cur.fetchall()]


async def contract_accept(chat_id: int, uid: int, name: str, cid, npc=0) -> dict:
    npc = int(npc or 0)

    async def run(conn):
        offers = await _offers(conn, chat_id, uid)
        c = next((x for x in offers if x["id"] == int(cid)), None)
        if not c or c["status"] != "offer":
            raise GameError("Это дело уже недоступно")
        if any(x["status"] == "active" for x in offers):
            raise GameError("Сначала закончи начатое дело")
        icon, title, _d, cost, minutes, chance, wanted = CONTRACTS[c["kind"]]
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, cost)
        if c["kind"] == "bribe":
            if p["money"] < 300:
                raise GameError("Конверт стоит 300 ₽")
            p["money"] -= 300
        if npc:
            g, _ = await G._mine(conn, chat_id, uid)
            cur = await conn.execute("SELECT id, status, busy_until FROM brigade WHERE id = ? AND gang_id = ?", (npc, g["id"] if g else -1))
            b = await cur.fetchone()
            if not b or b[1] != "free":
                raise GameError("Этот шнырь сейчас занят или недоступен")
            await conn.execute("UPDATE brigade SET status = 'busy', busy_until = ?, task = 'escort' WHERE id = ?", (now() + minutes * 60, npc))
        p["energy"] -= cost
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE contract SET status = 'active', ends = ?, npc = ? WHERE id = ?", (now() + minutes * 60, npc, c["id"]))
        return P3._res(f"{icon} Дело «{title}» начато: результат через {minutes} мин." + (" Ты взял с собой помощника: шанс выше." if npc else ""))
    return await P3._act(chat_id, uid, run)


async def contract_finish(chat_id: int, uid: int, name: str, cid) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM contract WHERE id = ? AND chat_id = ? AND user_id = ?", (int(cid), chat_id, uid))
        r = await cur.fetchone()
        c = dict(r) if r else None
        if not c or c["status"] != "active":
            raise GameError("Дела нет")
        if c["ends"] > now():
            left = c["ends"] - now()
            raise GameError(f"Ещё не закончилось: {left // 60 + 1} мин.")
        icon, title, _d, cost, minutes, chance, wanted = CONTRACTS[c["kind"]]
        p = await rp._load(conn, chat_id, uid)
        import liveevents
        md = await liveevents.mods(conn, chat_id)
        ch = chance + (0.15 if c["npc"] else 0) - 0.04 * p["wanted"] - md["crime_penalty"]
        ch = max(0.15, min(0.9, ch))
        t = now()
        good = random.random() < ch
        text, delta = "", 0
        if good:
            if c["kind"] == "bribe":
                p["cop_rep"] = p.get("cop_rep", 0) + c["reward"]
                p["wanted"] = max(0, p["wanted"] - 1)
                text = f"📨 Конверт дошёл: очки полицейского +{c['reward']}, розыск −1."
            else:
                if c["rkind"] == "money":
                    p["money"] += c["reward"]
                    delta = c["reward"]
                    text = f"{icon} Дело сделано: +{c['reward']} ₽."
                elif c["rkind"] == "merit":
                    cur2 = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
                    if await cur2.fetchone():
                        await G2._merit(conn, chat_id, uid, c["reward"])
                        text = f"{icon} Дело сделано: авторитет в банде +{c['reward']}."
                    else:
                        p["money"] += 350
                        delta = 350
                        text = f"{icon} Дело сделано: банды нет, так что +350 ₽."
                else:
                    import flat
                    items = await flat._items(conn, chat_id, uid)
                    item = random.choice(FLAT_GIFTS)
                    try:
                        slot = flat._free(items)
                        await conn.execute("INSERT INTO flat_item (chat_id, user_id, slot, item, ts) VALUES (?, ?, ?, ?, ?)", (chat_id, uid, slot, item, t))
                        text = f"{icon} Дело сделано: тебе подарили {flat.ITEMS[item][0]} {flat.ITEMS[item][1]} (уже стоит в квартире)."
                    except GameError:
                        p["money"] += 200
                        delta = 200
                        text = f"{icon} Дело сделано: в квартире нет места под подарок, взял 200 ₽."
                if c["kind"] == "leak":
                    g, _ = await G._mine(conn, chat_id, uid)
                    mole = await P5._mole(conn, g["id"], ("active",)) if g else None
                    if mole:
                        await conn.execute("UPDATE gang_mole SET status = 'found', found = ? WHERE id = ?", (t, mole["id"]))
                        text += " 🕵 И ты вычислил чужого агента в банде! Босс решит его судьбу."
                p["wanted"] = min(rp.MAX_WANTED, p["wanted"] + wanted)
                p["wanted_ts"] = t
        else:
            good = False
            p["wanted"] = min(rp.MAX_WANTED, p["wanted"] + 1)
            p["wanted_ts"] = t
            p["jail_until"], p["esc_used"] = t + 8 * 60, 0
            text = f"🚔 {icon} Дело сорвалось (шанс был ~{int(ch * 100)}%): розыск +1⭐, 8 минут в тюрьме."
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE contract SET status = ? WHERE id = ?", ("done" if good else "failed", c["id"]))
        if c["npc"]:
            await conn.execute("UPDATE brigade SET status = 'free', task = '', busy_until = 0 WHERE id = ? AND status = 'busy'", (c["npc"],))
            if random.random() < 0.10:
                await conn.execute("UPDATE brigade SET status = 'injured', injured_until = ? WHERE id = ?", (t + 6 * 3600, c["npc"]))
                text += " Шнырь вернулся с шишкой: 6 часов на лечении."
        return P3._res(text, good, delta)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ бригада ═════════════════════
ROLES = {"driver": ("🚗", "Водила"), "fighter": ("💪", "Боец"), "runner": ("🏃", "Шнырь")}
NAMES = ["Витёк", "Гоша", "Толян", "Серёга", "Макс", "Димон", "Борян", "Лёха", "Кирюха", "Валера"]
NICKS = {"driver": ["Колесо", "Газель", "Дрифт", "Педаль"], "fighter": ["Кувалда", "Бетон", "Кулак", "Танк"], "runner": ["Шустрый", "Тень", "Сквозняк", "Юла"]}
TRAITS = {"loyal": "верный", "greedy": "жадный", "coward": "трусливый", "hot": "горячий", "sly": "хитрый"}
HIRE_COST, TRAIN_COST, WAGE = 800, 500, 40
TASKS = {
    "cover": ("🌯", "Крышевать ларёк", 2, "driver_fighter", "доход в общак"),
    "guard": ("🛡", "Сторожить склад", 3, "fighter", "склад и общак под охраной: нападающим труднее"),
    "raid": ("🚗", "Везти налёт", 1, "driver", "следующий налёт банды +10% к шансу"),
    "clues": ("🔎", "Искать улики", 2, "runner", "открыть улику по делу о крысе"),
}
FIT = {"cover": ("driver", "fighter"), "guard": ("fighter",), "raid": ("driver",), "clues": ("runner",)}


async def _brig(conn, gid: int) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM brigade WHERE gang_id = ? AND status NOT IN ('gone', 'poached', 'fired') ORDER BY id", (gid,))
    return [dict(r) for r in await cur.fetchall()]


async def free_fighters(conn, gid: int) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM brigade WHERE gang_id = ? AND status = 'free' AND role = 'fighter'", (gid,))
    return (await cur.fetchone())[0]


async def brig_hire(chat_id: int, uid: int, name: str, role: str) -> dict:
    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        if role not in ROLES:
            raise GameError("Такой должности нет")
        if any(b["role"] == role for b in await _brig(conn, g["id"])):
            raise GameError("Эта должность уже занята")
        if (g["treasury"] or 0) < HIRE_COST:
            raise GameError(f"Нанять шестёрку стоит {HIRE_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (HIRE_COST, g["id"]))
        trait = random.choice(list(TRAITS))
        nm, nick = random.choice(NAMES), random.choice(NICKS[role])
        loyal = random.randint(50, 70) + (15 if trait == "loyal" else 0)
        await conn.execute("INSERT INTO brigade (chat_id, gang_id, role, name, nick, trait, level, loyalty, status, hired) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'free', ?)",
                           (chat_id, g["id"], role, nm, nick, trait, loyal, now()))
        return P3._res(f"{ROLES[role][0]} В бригаду нанят {nm} по кличке «{nick}» ({TRAITS[trait]}). −{HIRE_COST} ₽ из общака.")
    return await P3._act(chat_id, uid, run)


async def brig_train(chat_id: int, uid: int, name: str, bid) -> dict:
    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        b = next((x for x in await _brig(conn, g["id"]) if x["id"] == int(bid)), None)
        if not b:
            raise GameError("Такого шестёрки нет")
        if b["level"] >= 5:
            raise GameError("Выше уже некуда")
        cost = TRAIN_COST * b["level"]
        if (g["treasury"] or 0) < cost:
            raise GameError(f"Тренировка стоит {cost} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, g["id"]))
        await conn.execute("UPDATE brigade SET level = level + 1, loyalty = MIN(100, loyalty + 5) WHERE id = ?", (b["id"],))
        return P3._res(f"💪 «{b['nick']}» подтянулся до {b['level'] + 1} уровня (−{cost} ₽).")
    return await P3._act(chat_id, uid, run)


async def brig_fire(chat_id: int, uid: int, name: str, bid) -> dict:
    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        b = next((x for x in await _brig(conn, g["id"]) if x["id"] == int(bid)), None)
        if not b:
            raise GameError("Такого шестёрки нет")
        await conn.execute("UPDATE brigade SET status = 'fired' WHERE id = ?", (b["id"],))
        return P3._res(f"«{b['nick']}» уволен: слот свободен.", False)
    return await P3._act(chat_id, uid, run)


async def brig_task(chat_id: int, uid: int, name: str, bid, task: str) -> dict:
    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        b = next((x for x in await _brig(conn, g["id"]) if x["id"] == int(bid)), None)
        if not b:
            raise GameError("Такого шестёрки нет")
        if b["status"] != "free":
            raise GameError("Он сейчас занят или лечится")
        if task not in TASKS:
            raise GameError("Такого задания нет")
        icon, title, hours, _fit, _d = TASKS[task]
        await conn.execute("UPDATE brigade SET status = 'busy', task = ?, busy_until = ? WHERE id = ?", (task, now() + hours * 3600, b["id"]))
        return P3._res(f"{icon} «{b['nick']}»: {title.lower()}, вернётся через {hours} ч.")
    return await P3._act(chat_id, uid, run)


async def brig_guard_on(conn, gid: int) -> bool:
    return await P3.kget(conn, f"p8guard:{gid}") > now()


async def _brig_resolve(conn, b: dict) -> None:
    g = await _gx(conn, b["gang_id"])
    if not g:
        return
    t = now()
    task = b["task"]
    fit = b["role"] in FIT.get(task, ())
    eff = (1.3 if fit else 1.0) * (1 + 0.15 * (b["level"] - 1))
    text = ""
    if task == "cover":
        gain = int(150 * eff * TASKS["cover"][2] / 2 * b["level"] ** 0.5 + 100)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (gain, g["id"]))
        text = f"🌯 «{b['nick']}» крышевал ларёк: +{gain} ₽ в общак."
    elif task == "guard":
        await GP._kset(conn, f"p8guard:{g['id']}", t + 3 * 3600)
        text = f"🛡 «{b['nick']}» отстоял смену на складе: ещё 3 часа банду труднее обокрасть."
    elif task == "raid":
        await GP._kset(conn, f"p4drive:{g['id']}", t + 3 * 3600)
        text = f"🚗 «{b['nick']}» прогрел машину: ближайший налёт +10% к шансу."
    elif task == "clues":
        case = await GP._case(conn, g["id"])
        if case:
            locked = [c for c in await GP._clues(conn, case["id"]) if not c["fake"] and not GP._unlocked(c)]
            if locked and random.random() < min(0.9, 0.5 * eff):
                await conn.execute("UPDATE rat_clue SET unlock_ts = ? WHERE id = ?", (t, locked[0]["id"]))
                text = f"🔎 «{b['nick']}» нашёл зацепку: открыта новая улика!"
            else:
                text = f"🔎 «{b['nick']}» побегал, но ничего толкового не принёс."
        else:
            text = f"🔎 «{b['nick']}» вернулся: дела о крысе сейчас нет."
    status, loy = "free", b["loyalty"] + 3
    run_risk = 0.15 if b["loyalty"] < 30 else 0.0
    if random.random() < run_risk:
        stolen = min(300, int((g["treasury"] or 0) * 0.05))
        await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (stolen, g["id"]))
        await conn.execute("UPDATE brigade SET status = 'gone', task = '' WHERE id = ?", (b["id"],))
        await gangwar.say(conn, g["chat_id"], f"🏃 «{esc(b['nick'])}» сбежал из бригады «{esc(g['name'])}» с {stolen} ₽ из общака!")
        return
    if random.random() < 0.12 * (1.5 if b["trait"] == "coward" else 1.0):
        status = "injured"
        text += " Но вернулся с травмой (6 ч)."
    await conn.execute("UPDATE brigade SET status = ?, task = '', busy_until = 0, loyalty = MIN(100, ?), injured_until = ? WHERE id = ?",
                       (status, loy, t + 6 * 3600 if status == "injured" else 0, b["id"]))
    boss = await P3._boss_of(conn, g["id"])
    if text and boss:
        await rp._news(conn, g["chat_id"], text)


# ═════════════════════ крыша для бизнеса ═════════════════════
POINTS = {"shawarma": ("🌯", "Шаурмичная", 500), "bar": ("🍻", "Бар", 700), "taxi": ("🚕", "Такси", 550), "pawn": ("💍", "Ломбард", 630), "casino": ("🎰", "Подпольное казино", 980),
          "carwash": ("🚿", "Автомойка", 760), "karaoke": ("🎤", "Караоке-клуб", 880)}
GREED = {1: 0.7, 2: 1.0, 3: 1.4}
CLAIM_COST, CHALLENGE_COST, CHALLENGE_CD = 1500, 1000, 2 * 3600


async def _ensure_rackets(conn, chat_id: int) -> None:
    cur = await conn.execute("SELECT COUNT(*) FROM racket WHERE chat_id = ?", (chat_id,))
    if (await cur.fetchone())[0] >= len(POINTS):
        return
    cur = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    ais = [r[0] for r in await cur.fetchall()]
    for key in POINTS:
        owner = random.choice(ais) if ais and key in ("casino", "taxi") else 0
        await conn.execute("INSERT OR IGNORE INTO racket (chat_id, key, owner, greed, pending, last_ts) VALUES (?, ?, ?, 2, 0, ?)", (chat_id, key, owner, now()))


async def _rackets(conn, chat_id: int) -> list:
    await _ensure_rackets(conn, chat_id)
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM racket WHERE chat_id = ?", (chat_id,))
    return [dict(r) for r in await cur.fetchall()]


async def racket_claim(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        pts = {r["key"]: r for r in await _rackets(conn, chat_id)}
        if key not in pts:
            raise GameError("Такой точки нет")
        if pts[key]["owner"]:
            raise GameError("Точка уже под чьей-то крышей: можно наехать на владельца")
        if (g["treasury"] or 0) < CLAIM_COST:
            raise GameError(f"Нужно {CLAIM_COST} ₽ из общака на первый «подарок» владельцу")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (CLAIM_COST, g["id"]))
        await conn.execute("UPDATE racket SET owner = ?, greed = 2, pending = 0, last_ts = ? WHERE chat_id = ? AND key = ?", (g["id"], now(), chat_id, key))
        ic, title, _t = POINTS[key]
        await gangwar.say(conn, chat_id, f"{ic} «{esc(g['name'])}» взяла под крышу точку «{title}».")
        return P3._res(f"{ic} «{title}» теперь под вашей крышей: дань копится каждый час.")
    return await P3._act(chat_id, uid, run)


async def racket_greed(chat_id: int, uid: int, name: str, key: str, level) -> dict:
    level = int(level)

    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        pts = {r["key"]: r for r in await _rackets(conn, chat_id)}
        if key not in pts or pts[key]["owner"] != g["id"]:
            raise GameError("Это не ваша точка")
        if level not in GREED:
            raise GameError("Выбери 1, 2 или 3")
        await conn.execute("UPDATE racket SET greed = ? WHERE chat_id = ? AND key = ?", (level, chat_id, key))
        return P3._res(f"Аппетит на точке: {['', 'скромный (×0.7)', 'обычный (×1.0)', 'жадный (×1.4, но опасно)'][level]}.")
    return await P3._act(chat_id, uid, run)


async def racket_collect(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        pts = [r for r in await _rackets(conn, chat_id) if r["owner"] == g["id"]]
        total = sum(r["pending"] for r in pts)
        if total < 1:
            raise GameError("Дани пока нет")
        await conn.execute("UPDATE racket SET pending = 0 WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        await conn.execute("UPDATE gang SET treasury = treasury + ?, last_act = ? WHERE id = ?", (total, now(), g["id"]))
        await P5.rep_add(conn, g["id"], 0, 1)
        import police2
        await police2.alert(conn, chat_id, g["id"], "racket")
        return P3._res(f"💰 Дань собрана: +{total} ₽ в общак.", True)
    return await P3._act(chat_id, uid, run)


async def _power(conn, gid: int) -> float:
    g = await _gx(conn, gid)
    n = max(len(await gangwar._real_members(conn, gid)), 3 if g and g["is_ai"] else 0)
    cur = await conn.execute("SELECT COUNT(*) FROM territory WHERE owner = ?", (gid,))
    terr = (await cur.fetchone())[0]
    return n + 0.5 * terr + 0.02 * ((g.get("respect") or 0) + (g.get("fear") or 0)) + 0.5 * await free_fighters(conn, gid)


async def _takeover(conn, chat_id: int, key: str, att: dict, victim_id: int, auto: bool) -> tuple:
    ap, vp = await _power(conn, att["id"]), await _power(conn, victim_id)
    chance = max(0.2, min(0.75, 0.45 + 0.05 * (ap - vp)))
    ic, title, _t = POINTS[key]
    victim = await _gx(conn, victim_id)
    if random.random() < chance:
        await conn.execute("UPDATE racket SET owner = ?, pending = pending / 2, rebel_until = 0 WHERE chat_id = ? AND key = ?", (att["id"], chat_id, key))
        await P5.rep_add(conn, att["id"], 0, 3)
        await P5.rep_add(conn, victim_id, -2, 0)
        await gangwar.say(conn, chat_id, f"{ic} «{esc(att['name'])}» перекупила «{title}» у «{esc(victim['name'] if victim else '?')}»: теперь точка под их крышей.")
        return True, chance
    await P5.rep_add(conn, att["id"], -2, 0)
    await gangwar.say(conn, chat_id, f"{ic} «{esc(victim['name'] if victim else '?')}» отбила наезд «{esc(att['name'])}» на «{title}».")
    return False, chance


async def racket_challenge(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        g, _, _r = await _need_dep(conn, chat_id, uid)
        pts = {r["key"]: r for r in await _rackets(conn, chat_id)}
        if key not in pts or not pts[key]["owner"] or pts[key]["owner"] == g["id"]:
            raise GameError("Наехать можно на чужую точку")
        if await P3.pact_between(conn, g["id"], pts[key]["owner"], ("active",)):
            raise GameError("Нельзя наезжать на союзника")
        if await P3.kget(conn, f"p8chal:{g['id']}") > now():
            raise GameError("Банда ещё не отдышалась после прошлого наезда")
        if (g["treasury"] or 0) < CHALLENGE_COST:
            raise GameError(f"На наезд нужно {CHALLENGE_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (CHALLENGE_COST, g["id"]))
        await GP._kset(conn, f"p8chal:{g['id']}", now() + CHALLENGE_CD)
        won, chance = await _takeover(conn, chat_id, key, g, pts[key]["owner"], False)
        if won:
            return P3._res(f"{POINTS[key][0]} Наезд удался (шанс ~{int(chance * 100)}%): точка ваша!")
        extra = min(500, g["treasury"] or 0)
        await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (extra, g["id"]))
        return P3._res(f"💥 Наезд провалился (шанс был ~{int(chance * 100)}%): ещё −{extra} ₽, уважение −2.", False)
    return await P3._act(chat_id, uid, run)


async def bar_discount(chat_id: int, uid: int) -> float:
    """−10% в баре для участников банды, которая держит крышу над баром."""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute("SELECT r.owner FROM racket r JOIN gang_member m ON m.gang_id = r.owner WHERE r.chat_id = ? AND r.key = 'bar' AND m.user_id = ?", (chat_id, uid))
            return 0.10 if await cur.fetchone() else 0.0
    except Exception:
        return 0.0


async def _rackets_tick(conn) -> None:
    t = now()
    cur = await conn.execute("SELECT DISTINCT chat_id FROM gang WHERE is_ai = 0")
    for (chat_id,) in await cur.fetchall():
        for r in await _rackets(conn, chat_id):
            hours = min(24, (t - (r["last_ts"] or t)) // 3600)
            if hours < 1:
                continue
            await conn.execute("UPDATE racket SET last_ts = ? WHERE chat_id = ? AND key = ?", ((r["last_ts"] or t) + hours * 3600, chat_id, r["key"]))
            if not r["owner"]:
                continue
            g = await _gx(conn, r["owner"])
            if not g or g.get("is_ai"):
                continue
            if r["rebel_until"] > t:
                continue
            add = int(POINTS[r["key"]][2] / 168 * GREED.get(r["greed"], 1.0) * hours)
            await conn.execute("UPDATE racket SET pending = MIN(?, pending + ?) WHERE chat_id = ? AND key = ?", (POINTS[r["key"]][2] * 2, add, chat_id, r["key"]))
            if r["greed"] >= 2 and await P3._roll(conn, f"p8greed:{chat_id}:{r['key']}", 24 * 3600 / (0.04 if r["greed"] == 2 else 0.25)):
                ic, title, _t = POINTS[r["key"]]
                if random.random() < 0.5 and not await P3._open_visit(conn, chat_id, 0, g["id"], "omon_gang"):
                    await conn.execute("INSERT INTO rp_visit (chat_id, user_id, gang_id, kind, amount, created, expires, status) VALUES (?, 0, ?, 'omon_gang', 0, ?, ?, 'open')", (chat_id, g["id"], t, t + P3.OMON_WINDOW))
                    await gangwar.say(conn, chat_id, f"{ic} Хозяин «{title}» пожаловался полиции на жадную крышу «{esc(g['name'])}»: к ним едет ОМОН!")
                else:
                    await conn.execute("UPDATE racket SET rebel_until = ? WHERE chat_id = ? AND key = ?", (t + 24 * 3600, chat_id, r["key"]))
                    await P5.rep_add(conn, g["id"], -3, 0)
                    await gangwar.say(conn, chat_id, f"{ic} Хозяин «{title}» взбунтовался против «{esc(g['name'])}»: сутки дани не будет.")
        # ИИ-банды наезжают на точки игроков
        if await P3._roll(conn, f"p8aichal:{chat_id}", 14 * 3600):
            rs = [r for r in await _rackets(conn, chat_id) if r["owner"]]
            real = [r for r in rs if (await _gx(conn, r["owner"]) or {}).get("is_ai") == 0]
            cur2 = await conn.execute("SELECT id, name, is_ai FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
            ais = [dict(zip(("id", "name", "is_ai"), x)) for x in await cur2.fetchall()]
            if real and ais:
                r = random.choice(real)
                await _takeover(conn, chat_id, r["key"], random.choice(ais), r["owner"], True)


# ═════════════════════ интерфейс и фон ═════════════════════
async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    offers = await _offers(conn, chat_id, uid)
    brig_free = []
    if g and not g.get("is_ai"):
        brig_free = [{"id": b["id"], "nick": b["nick"]} for b in await _brig(conn, g["id"]) if b["status"] == "free"]
    t = now()
    out["pc"] = {"contracts": [{"id": c["id"], "kind": c["kind"], "icon": CONTRACTS[c["kind"]][0], "title": CONTRACTS[c["kind"]][1], "desc": CONTRACTS[c["kind"]][2],
                                "energy": CONTRACTS[c["kind"]][3], "minutes": CONTRACTS[c["kind"]][4], "status": c["status"], "left": max(0, c["ends"] - t), "ready": c["status"] == "active" and c["ends"] <= t,
                                "rkind": c["rkind"], "reward": c["reward"], "wanted": CONTRACTS[c["kind"]][6], "chance": int(CONTRACTS[c["kind"]][5] * 100)} for c in offers],
                 "npc": brig_free}
    m = out.get("mine")
    if not g or not m or g.get("is_ai"):
        return
    brig = []
    for b in await _brig(conn, g["id"]):
        brig.append({"id": b["id"], "role": b["role"], "icon": ROLES[b["role"]][0], "role_name": ROLES[b["role"]][1], "name": b["name"], "nick": b["nick"], "trait": TRAITS.get(b["trait"], ""),
                     "level": b["level"], "loyalty": b["loyalty"], "status": b["status"], "task": TASKS[b["task"]][1] if b["task"] in TASKS else ("сопровождает" if b["task"] == "escort" else ""),
                     "left": max(0, b["busy_until"] - t) if b["status"] == "busy" else max(0, b["injured_until"] - t) if b["status"] == "injured" else 0,
                     "train_cost": TRAIN_COST * b["level"] if b["level"] < 5 else 0})
    taken = {b["role"] for b in brig}
    rack = []
    names = {x["id"]: x["name"] for x in out.get("gangs", [])}
    for r in await _rackets(conn, chat_id):
        ic, title, tribute = POINTS[r["key"]]
        rack.append({"key": r["key"], "icon": ic, "title": title, "tribute": tribute, "owner": r["owner"], "owner_name": names.get(r["owner"], ""), "mine": r["owner"] == g["id"],
                     "greed": r["greed"], "pending": r["pending"], "rebel": max(0, r["rebel_until"] - t)})
    m.setdefault("p8", {}).update({"brigade": brig, "roles_free": [{"key": k, "icon": v[0], "title": v[1]} for k, v in ROLES.items() if k not in taken],
                                   "tasks": [{"key": k, "icon": v[0], "title": v[1], "hours": v[2], "desc": v[4]} for k, v in TASKS.items()],
                                   "hire_cost": HIRE_COST, "rackets": rack, "claim_cost": CLAIM_COST, "challenge_cost": CHALLENGE_COST,
                                   "challenge_wait": max(0, await P3.kget(conn, f"p8chal:{g['id']}") - t), "guard": await brig_guard_on(conn, g["id"])})


async def tick(conn) -> None:
    try:
        t = now()
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM brigade WHERE status = 'busy' AND task != 'escort' AND busy_until <= ?", (t,))
        for b in [dict(r) for r in await cur.fetchall()]:
            await _brig_resolve(conn, b)
        await conn.execute("UPDATE brigade SET status = 'free' WHERE status = 'injured' AND injured_until <= ?", (t,))
        # зарплата и переманивание раз в сутки
        cur = await conn.execute("SELECT DISTINCT gang_id FROM brigade WHERE status NOT IN ('gone', 'poached', 'fired')")
        for (gid,) in await cur.fetchall():
            key = f"p8wage:{gid}"
            if await gangwar.kget(conn, key) == today():
                continue
            await gangwar.kset(conn, key, today())
            g = await _gx(conn, gid)
            if not g:
                continue
            for b in await _brig(conn, gid):
                wage = int(WAGE * b["level"] * (1.5 if b["trait"] == "greedy" else 1.0))
                if (g["treasury"] or 0) >= wage:
                    await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (wage, gid))
                    g["treasury"] -= wage
                    await conn.execute("UPDATE brigade SET loyalty = MIN(100, loyalty + 1) WHERE id = ?", (b["id"],))
                else:
                    await conn.execute("UPDATE brigade SET loyalty = MAX(0, loyalty - 10) WHERE id = ?", (b["id"],))
                if b["loyalty"] < 40 and random.random() < 0.05:
                    await conn.execute("UPDATE brigade SET status = 'poached' WHERE id = ?", (b["id"],))
                    await gangwar.say(conn, g["chat_id"], f"🎣 «{esc(b['nick'])}» ушёл к конкурентам: бригаде «{esc(g['name'])}» мало платят.")
        # бойцы бригады добавляют очки в войне
        cur = await conn.execute("SELECT DISTINCT gang_id FROM brigade WHERE status = 'free' AND role = 'fighter'")
        for (gid,) in await cur.fetchall():
            if await P3.kget(conn, f"p8war:{gid}") > t - 1800:
                continue
            if await gangwar._active_war(conn, gid):
                await GP._kset(conn, f"p8war:{gid}", t)
                import gangplus4
                await gangplus4._war_add(conn, gid, 2 * max(1, await free_fighters(conn, gid)))
        await _rackets_tick(conn)
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 8")
