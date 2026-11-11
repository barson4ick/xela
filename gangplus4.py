"""ОПГ-плюс 4: нападения ИИ-банд раз в 3 часа (6 видов), защита одиночек (оружие, охрана, адвокат),
специализации внутри банды с активностями, ночной дозор, общий котёл и турнир банды."""
import logging
import random
import time
from typing import Optional

import aiosqlite

import gang as G
import gangplus as GP
import gangplus2 as G2
import gangplus3 as P3
import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc, now_local

log = logging.getLogger(__name__)


def now() -> int:
    return int(time.time())


# ═════════════════════ защита одиночек ═════════════════════
WEAPON = [("Без оружия", 0), ("🔪 Нож", 1500), ("🔫 Травмат", 4000), ("💥 Ствол", 9000)]
GUARD = [("Без охраны", 0), ("🔒 Замок", 2000), ("🚨 Сигнализация", 5000), ("💂 Охранник", 12000)]


def law_level(p: dict) -> int:
    left = (p.get("lawyer_until") or 0) - now()
    return 0 if left <= 0 else 1 if left < 48 * 3600 else 2 if left < 96 * 3600 else 3


async def defense_buy(chat_id: int, uid: int, name: str, kind: str) -> dict:
    async def run(conn):
        if kind not in ("weapon", "guard"):
            raise GameError("Такой защиты нет")
        p = await rp._load(conn, chat_id, uid)
        lst = WEAPON if kind == "weapon" else GUARD
        lvl = p.get(kind) or 0
        if lvl >= 3:
            raise GameError("Уровень уже максимальный")
        title, cost = lst[lvl + 1]
        if kind == "weapon":
            import liveevents
            cost = int(cost * (1 - (await liveevents.mods(conn, chat_id))["weapon_discount"]))
        if p["money"] < cost:
            raise GameError(f"{title} стоит {cost} ₽")
        await conn.execute(f"UPDATE rp_player SET money = money - ?, {kind} = {kind} + 1 WHERE chat_id = ? AND user_id = ?", (cost, chat_id, uid))
        return P3._res(f"{title}: куплено за {cost} ₽. Теперь нападающим труднее.", True, -cost)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ нападения ═════════════════════
ATK_GAP, ATK_GAP_USER, ATK_WINDOW = 45 * 60, 60 * 60, 15 * 60
KINDS = {
    "loot": ("💰", "Набег на деньги"),
    "seize": ("🏴", "Захват"),
    "arson": ("🔥", "Поджог"),
    "ambush": ("🪤", "Засада"),
    "robbery": ("🔫", "Ограбление"),
    "blackmail": ("🎭", "Шантаж"),
}
TEXT_GANG = {
    "loot": "хочет вынести общак",
    "seize": "метит на ваш район",
    "arson": "собирается поджечь ваш район",
    "ambush": "устраивает засаду на ваших людей",
    "robbery": "хочет обчистить карманы вашей банды",
    "blackmail": "шантажирует босса компроматом",
}
TEXT_USER = {
    "loot": "хочет вытрясти деньги",
    "seize": "лезет на твой ларёк",
    "arson": "хочет поджечь твою тачку",
    "ambush": "поджидает тебя в подворотне",
    "robbery": "собирается тебя ограбить",
    "blackmail": "шантажирует тебя",
}
TROPHY_GANG, TROPHY_USER = (500, 1500), (300, 800)


async def _atk_open(conn, chat_id: int, tkind: str, target: int) -> Optional[dict]:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM rp_attack WHERE chat_id = ? AND tkind = ? AND target = ? AND status = 'open'", (chat_id, tkind, target))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _watchers(conn, gid: int) -> int:
    import gangplus9
    if await gangplus9.wrecked(conn, gid, "yard"):
        return 0
    cur = await conn.execute("SELECT COUNT(*) FROM gang_watch WHERE gang_id = ? AND until > ?", (gid, now()))
    return (await cur.fetchone())[0]


async def jam_hit(conn, gid: int) -> bool:
    """Глушилка взломщика: один раз блокирует нападение или ОМОН."""
    if await P3.kget(conn, f"p4jam:{gid}") > now():
        await GP._kset(conn, f"p4jam:{gid}", 0)
        return True
    return False


async def _gang_chance(conn, g: dict) -> float:
    n = min(5, len(await gangwar._real_members(conn, g["id"])))
    guard = await P3.proj_level(conn, g["id"], "guard")
    allies = min(2, len(await P3._pacts_of(conn, g["id"])))
    w = min(0.2, 0.05 * await _watchers(conn, g["id"]))
    import gangplus8, gangplus9
    fighters = min(0.12, 0.04 * await gangplus8.free_fighters(conn, g["id"]))
    guard_on = 0.08 if await gangplus8.brig_guard_on(conn, g["id"]) else 0.0
    arm = -0.10 if await gangplus9.wrecked(conn, g["id"], "armory") else 0.0
    return max(0.05, min(0.85, 0.15 + 0.05 * n + 0.07 * guard + 0.08 * allies + w + fighters + guard_on + arm))


def _user_chance(p: dict) -> float:
    return min(0.85, 0.20 + 0.12 * (p.get("weapon") or 0) + 0.05 * (p.get("guard") or 0) + 0.04 * law_level(p))


async def _atk_new(conn, chat_id: int, only: str = "") -> bool:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    ais = [dict(r) for r in await cur.fetchall()]
    ais = [a for a in ais if not await gangwar._active_war(conn, a["id"])]
    if not ais:
        return False
    atk = random.choice(ais)
    t = now()
    cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
    gangs = []
    for r in await cur.fetchall():
        gid = r[0]
        import gangplus5
        if (len(await gangwar._real_members(conn, gid)) < 2 or await _atk_open(conn, chat_id, "gang", gid) or await P3.pact_between(conn, atk["id"], gid, ("active",))
                or await gangplus5.truce_between(conn, atk["id"], gid) or await gangplus5.disinfo_active(conn, gid)):
            continue
        gangs.append({"id": gid, "name": r[1], "treasury": r[2]})
    cur = await conn.execute(
        """SELECT r.user_id, r.money FROM rp_player r JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id
           WHERE r.chat_id = ? AND r.user_id > 0 AND r.money >= 1500 AND r.jail_until < ?
             AND r.user_id NOT IN (SELECT user_id FROM gang_member WHERE chat_id = ?)""", (chat_id, t, chat_id))
    import gangplus11
    loners = [(r[0], r[1]) for r in await cur.fetchall() if not await _atk_open(conn, chat_id, "user", r[0]) and not await gangplus11.protected(conn, chat_id, r[0])]
    if not gangs and not loners:
        return False
    if only == "gang" and not gangs or only == "user" and not loners:
        return False
    use_gang = gangs and (only == "gang" or (only != "user" and (not loners or random.random() < 0.6)))
    if use_gang:
        g = random.choices(gangs, weights=[await gangplus5.attack_weight(conn, x["id"]) for x in gangs])[0]
        if await jam_hit(conn, g["id"]):
            await gangwar.say(conn, chat_id, f"🔓 Глушилка взломщика «{esc(g['name'])}» сорвала нападение «{esc(atk['name'])}»: те даже не нашли дорогу.")
            return True
        cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ?", (chat_id, g["id"]))
        terr = [x[0] for x in await cur.fetchall()]
        kinds = ["loot", "ambush", "robbery", "blackmail"] + (["seize", "arson"] if terr else [])
        kind = random.choice(kinds + (["seize", "seize"] if terr else []))
        key = random.choice(terr) if terr and kind in ("seize", "arson") else ""
        price = max(300, int(g["treasury"] * 0.12))
        await conn.execute("INSERT INTO rp_attack (chat_id, attacker, kind, tkind, target, terr, price, created, expires, status) VALUES (?, ?, ?, 'gang', ?, ?, ?, ?, ?, 'open')",
                           (chat_id, atk["id"], kind, g["id"], key, price, t, t + ATK_WINDOW))
        ic, title = KINDS[kind]
        await gangwar.say(conn, chat_id, f"{ic} <b>Нападение!</b> «{esc(atk['name'])}» {TEXT_GANG[kind]} у «{esc(g['name'])}»"
                                         + (f" ({gangwar.TERR[key][1]} {gangwar.TERR[key][2]})" if key else "")
                                         + f". Босс, у вас {ATK_WINDOW // 60} минут: откупиться или принять бой (мини-апп → Район).")
        return True
    uid, money = random.choice(loners)
    kind = random.choice(list(KINDS))
    price = max(150, int(money * 0.08))
    await conn.execute("INSERT INTO rp_attack (chat_id, attacker, kind, tkind, target, terr, price, created, expires, status) VALUES (?, ?, ?, 'user', ?, '', ?, ?, ?, 'open')",
                       (chat_id, atk["id"], kind, uid, price, t, t + ATK_WINDOW))
    ic, title = KINDS[kind]
    name = await P3._uname(conn, chat_id, uid)
    await gangwar.say(conn, chat_id, f"{ic} <b>{title}!</b> «{esc(atk['name'])}» {TEXT_USER[kind]}, {esc(name)}. "
                                     f"У тебя {ATK_WINDOW // 60} минут: откупиться или дать отпор (оружие, охрана и адвокат помогают).")
    return True


async def _apply_gang(conn, atk: dict, g: dict, mult: float) -> str:
    chat_id = atk["chat_id"]
    red = P3.GUARD_OMON[await P3.proj_level(conn, g["id"], "guard")]
    members = await gangwar._real_members(conn, g["id"])
    kind = atk["kind"]
    if kind in ("seize", "arson") and not atk["terr"]:
        kind = "loot"
    if kind == "loot":
        import gangplus8, gangplus9
        amt = int(min(4000, (g["treasury"] or 0) * 0.15) * mult * (1 - red) * (1.5 if await gangplus9.wrecked(conn, g["id"], "vault") else 1.0) * (0.7 if await gangplus8.brig_guard_on(conn, g["id"]) else 1.0))
        await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (amt, g["id"]))
        import gangplus5
        stolen = 0
        for res, qty in (await gangplus5._stock(conn, g["id"])).items():
            take = int(qty * 0.2 * mult * (1 - red))
            if take:
                await gangplus5._stock_add(conn, g["id"], res, -take)
                stolen += take
        return f"из общака «{g['name']}» унесли {amt} ₽" + (f" и {stolen} ед. товара со склада" if stolen else "")
    if kind == "seize":
        await conn.execute("UPDATE territory SET owner = ?, last_pay = ? WHERE chat_id = ? AND key = ?", (atk["attacker"], now(), chat_id, atk["terr"]))
        return f"{gangwar.TERR[atk['terr']][1]} {gangwar.TERR[atk['terr']][2]} захвачен, «{g['name']}» потеряла район"
    if kind == "arson":
        await GP._kset(conn, f"tfire:{chat_id}:{atk['terr']}", now() + 6 * 3600)
        return f"{gangwar.TERR[atk['terr']][1]} {gangwar.TERR[atk['terr']][2]} горит: доход вдвое меньше на 6 часов"
    if kind == "ambush":
        victim = random.choice(members)
        await G2._beat(conn, chat_id, victim, 20)
        await G2._merit(conn, chat_id, victim, -5)
        amt = int((g["treasury"] or 0) * 0.03 * (1 - red))
        await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (amt, g["id"]))
        return f"{await P3._uname(conn, chat_id, victim)} избит в засаде (20 мин), в общаке −{amt} ₽"
    if kind == "robbery":
        total = 0
        for u in members:
            p = await rp._load(conn, chat_id, u)
            loss = min(400, int(p["money"] * 0.04 * mult * (1 - red)))
            if loss:
                await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (loss, chat_id, u))
                total += loss
        return f"у участников «{g['name']}» вытащили из карманов {total} ₽"
    amt = int(min(2500, (g["treasury"] or 0) * 0.08) * mult * (1 - red))
    await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (amt, g["id"]))
    boss = await P3._boss_of(conn, g["id"])
    if boss:
        await G2._merit(conn, chat_id, boss, -10)
    return f"компромат слит: общак −{amt} ₽, авторитет босса −10"


async def _apply_user(conn, atk: dict, uid: int, mult: float) -> str:
    chat_id = atk["chat_id"]
    p = await rp._load(conn, chat_id, uid)
    red = max(0.1, (1 - 0.25 * (p.get("guard") or 0)) * (1 - 0.10 * law_level(p)))
    kind = atk["kind"]
    if kind == "seize" and not p["stall"]:
        kind = "loot"
    cash = p["money"]
    name = await P3._uname(conn, chat_id, uid)

    async def take(pct, cap):
        amt = min(cap, int(cash * pct * mult * red))
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (amt, chat_id, uid))
        return amt
    if kind == "loot":
        return f"у {name} вытрясли {await take(0.12, 2500)} ₽"
    if kind == "seize":
        stall = int(rp._public(p)["stall_ready"] * red)
        await conn.execute("UPDATE rp_player SET collect_ts = ? WHERE chat_id = ? AND user_id = ?", (now(), chat_id, uid))
        return f"ларёк {name} обчистили: накопленные {stall} ₽ пропали"
    if kind == "arson":
        return f"тачка {name} сгорела: ремонт стоит {await take(0.06, 1500)} ₽"
    if kind == "ambush":
        a = await take(0.05, 1200)
        await G2._beat(conn, chat_id, uid, 20)
        return f"{name} избит в подворотне (20 мин), потерял {a} ₽"
    if kind == "robbery":
        a = await take(0.10, 2000)
        await conn.execute("UPDATE rp_player SET energy = MAX(0, energy - 30) WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        return f"{name} ограбили на {a} ₽ и вымотали (⚡ −30)"
    a = await take(0.08, 1500)
    if law_level(p) < 2:
        await conn.execute("UPDATE rp_player SET wanted = MIN(?, wanted + 1), wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (rp.MAX_WANTED, now(), chat_id, uid))
        return f"шантажист получил {a} ₽ и слил слухи: у {name} розыск +1⭐"
    return f"шантажист получил {a} ₽, но адвокат {name} не дал раздуть дело"


async def _atk_resolve(conn, atk: dict, mode: str) -> dict:
    """mode: pay, fight, timeout. Возвращает {text, good}."""
    chat_id = atk["chat_id"]
    ai = await gangwar._gang_row(conn, atk["attacker"])
    aname = ai["name"] if ai else "Банда"
    ic, title = KINDS[atk["kind"]]
    await conn.execute("UPDATE rp_attack SET status = 'done' WHERE id = ?", (atk["id"],))
    if atk["tkind"] == "gang":
        g = await gangwar._gang_row(conn, atk["target"])
        if not g:
            return {"text": "Банды уже нет", "good": False}
        who = f"«{g['name']}»"
        chance = await _gang_chance(conn, g)
        can_pay = (g["treasury"] or 0) >= atk["price"]
    else:
        p = await rp._load(conn, chat_id, atk["target"])
        who = await P3._uname(conn, chat_id, atk["target"])
        import liveevents
        chance = _user_chance(p) + (await liveevents.mods(conn, chat_id))["repel_bonus"]
        can_pay = p["money"] >= atk["price"]
    if mode == "pay":
        if not can_pay:
            await conn.execute("UPDATE rp_attack SET status = 'open' WHERE id = ?", (atk["id"],))
            raise GameError(f"Откуп стоит {atk['price']} ₽, а их нет")
        if atk["tkind"] == "gang":
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (atk["price"], atk["target"]))
        else:
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (atk["price"], chat_id, atk["target"]))
        await rp._news(conn, chat_id, f"{ic} {who} откупился от «{aname}»")
        await gangwar.say(conn, chat_id, f"{ic} {esc(who)} откупился от «{esc(aname)}» ({atk['price']} ₽). Нападение отменено.")
        return {"text": f"💸 Откупился за {atk['price']} ₽: «{aname}» уехала.", "good": True, "delta": -atk["price"]}
    import gangplus6
    press = await gangplus6.pressure(conn, chat_id)
    chance = max(0.05, chance - min(0.15, 0.1 * (press - 1)))
    eff = chance * (0.5 if mode == "timeout" else 1.0)
    if random.random() < eff:
        if atk["tkind"] == "gang":
            for u in await gangwar._real_members(conn, g["id"]):
                await G2._merit(conn, chat_id, u, 6)
            prize = random.randint(*TROPHY_GANG)
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (prize, g["id"]))
        else:
            prize = random.randint(*TROPHY_USER)
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (prize, chat_id, atk["target"]))
        if atk["tkind"] == "gang":
            import gangplus5
            await gangplus5.rep_add(conn, g["id"], 5, 2)
        await rp._news(conn, chat_id, f"{ic} {who} отбился от «{aname}»")
        await gangwar.say(conn, chat_id, f"🛡 {esc(who)} отбился от «{esc(aname)}» ({title.lower()}). Трофеи: {prize} ₽.")
        return {"text": f"🛡 Отбился! Трофеи {prize} ₽." + (" Авторитет банды +6." if atk["tkind"] == "gang" else ""), "good": True, "delta": prize}
    mult = (1.2 if mode == "timeout" else 1.0) * press
    line = await (_apply_gang(conn, atk, g, mult) if atk["tkind"] == "gang" else _apply_user(conn, atk, atk["target"], mult))
    await rp._news(conn, chat_id, f"{ic} «{aname}»: {line}")
    await gangwar.say(conn, chat_id, f"{ic} <b>«{esc(aname)}»</b> добилась своего: {esc(line)}.")
    return {"text": f"💥 Не отбились (шанс был ~{int(chance * 100)}%): {line}.", "good": False}


async def attack_answer(chat_id: int, uid: int, name: str, aid, choice: str) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM rp_attack WHERE id = ? AND chat_id = ?", (int(aid), chat_id))
        r = await cur.fetchone()
        atk = dict(r) if r else None
        if not atk or atk["status"] != "open" or atk["expires"] < now():
            raise GameError("Нападение уже закончилось")
        if choice not in ("pay", "fight"):
            raise GameError("Неизвестное решение")
        if atk["tkind"] == "gang":
            g, _ = await P3._need_boss(conn, chat_id, uid)
            if g["id"] != atk["target"]:
                raise GameError("Это не твоя банда")
        elif atk["target"] != uid:
            raise GameError("Это нападение не на тебя")
        res = await _atk_resolve(conn, atk, choice)
        return P3._res(res["text"], res["good"], res.get("delta", 0))
    return await P3._act(chat_id, uid, run)


async def fire_mult(conn, chat_id: int, key: str) -> float:
    import gangplus5
    return (0.5 if await P3.kget(conn, f"tfire:{chat_id}:{key}") > now() else 1.0) * await gangplus5.terr_mult(conn, chat_id, key)


async def drive_bonus(conn, gid: int) -> float:
    import gangplus9
    import police
    pen = await police.heist_penalty(conn, gid)
    if await gangplus9.wrecked(conn, gid, "garage"):
        return -0.10 + pen
    if await P3.kget(conn, f"p4drive:{gid}") > now():
        await GP._kset(conn, f"p4drive:{gid}", 0)
        return 0.10 + pen
    return pen


# ═════════════════════ специализации ═════════════════════
SPECS = {
    "accountant": ("🧮", "Бухгалтер", {"launder": ("Отмыть деньги", "Общак +3% (до 1500 ₽), нужно от 1000 ₽ в общаке", 15, 6 * 3600, None),
                                       "audit": ("Проверка взносов", "Прощает долг самому большому должнику", 5, 12 * 3600, None)}),
    "hacker": ("🔓", "Взломщик", {"safe": ("Взлом сейфа", "Шанс ~50%: 1500–3500 ₽, иначе тюрьма", 25, 4 * 3600, None),
                                  "jam": ("Глушилка", "Следующее нападение или ОМОН на банду сорвётся", 10, 24 * 3600, None)}),
    "scout": ("👀", "Разведчик", {"recon": ("Разведка", "Узнать силы другой банды; в войне +5 очков", 10, 3 * 3600, "gang"),
                                 "scan": ("Проверить своих", "В деле о крысе шанс 45% открыть улику", 10, 6 * 3600, None)}),
    "driver": ("🚗", "Водитель", {"getaway": ("Подготовить побег", "Следующий налёт банды +10% к шансу (3 часа)", 15, 6 * 3600, None),
                                 "race": ("Гонка", "В войне +12 очков, иначе +4 авторитета и 200 ₽", 15, 4 * 3600, None)}),
    "fighter": ("💪", "Боец", {"brawl": ("Разборка", "В войне +10 очков, иначе +4 авторитета (риск побоев)", 20, 3 * 3600, None),
                              "cover": ("Крыша", "Товарищ на сутки защищён от побоев", 10, 12 * 3600, "member")}),
    "recruiter": ("📢", "Вербовщик", {"agitate": ("Агитация", "Зовёт случайного одиночку в банду", 10, 6 * 3600, None),
                                     "buzz": ("Раскрутка", "+5 авторитета, +200 ₽ в общак, банда «в деле»", 10, 12 * 3600, None)}),
    "cook": ("🍽", "Кок", {"banquet": ("Банкет", "Всем в банде +20 энергии за счёт общака (до 100 ₽ на человека)", 10, 8 * 3600, None),
                          "feast": ("Праздничный стол", "На сходке +5 авторитета каждому (3 часа)", 10, 24 * 3600, None)}),
    "informer": ("🕶", "Информатор", {"tipoff": ("Слить полиции", "+200–400 ₽, очки полицейского +2, риск быть вычисленным 8%", 5, 24 * 3600, None)}),
}
SPEC_CD = 12 * 3600


async def _need_member(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g or g.get("is_ai"):
        raise GameError("Ты не состоишь в ОПГ")
    me = next(m for m in members if m["user_id"] == uid)
    return g, members, me


async def spec_set(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        g, _, me = await _need_member(conn, chat_id, uid)
        if key not in SPECS and key != "":
            raise GameError("Такой специализации нет")
        if await P3.kget(conn, f"p4specset:{chat_id}:{uid}") > now():
            raise GameError("Специализацию можно менять раз в 12 часов")
        await conn.execute("UPDATE gang_member SET spec = ? WHERE chat_id = ? AND user_id = ?", (key, chat_id, uid))
        await GP._kset(conn, f"p4specset:{chat_id}:{uid}", now() + SPEC_CD)
        title = f"{SPECS[key][0]} {SPECS[key][1]}" if key else "без специализации"
        return P3._res(f"Теперь ты: {title}.")
    return await P3._act(chat_id, uid, run)


def _war_pts(conn, gid: int, pts: int):
    t = now()
    return (conn.execute("UPDATE gang_war SET score_a = score_a + ? WHERE status = 'active' AND a = ? AND ends > ?", (pts, gid, t)),
            conn.execute("UPDATE gang_war SET score_b = score_b + ? WHERE status = 'active' AND b = ? AND ends > ?", (pts, gid, t)))


async def _war_add(conn, gid: int, pts: int) -> bool:
    war = await gangwar._active_war(conn, gid)
    if not war:
        return False
    a, b = _war_pts(conn, gid, pts)
    await a
    await b
    return True


async def spec_act(chat_id: int, uid: int, name: str, act: str, arg=None) -> dict:
    async def run(conn):
        g, members, me = await _need_member(conn, chat_id, uid)
        cur = await conn.execute("SELECT spec FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        spec = (await cur.fetchone())[0]
        if not spec or act not in SPECS[spec][2]:
            raise GameError("Это умение другой специализации")
        title, _d, cost, cd, need = SPECS[spec][2][act]
        if act in ("banquet", "feast"):
            import gangplus9
            if await gangplus9.wrecked(conn, g["id"], "kitchen"):
                raise GameError("Кухня разгромлена: банкетов не будет, пока не отремонтируете")
        ckey = f"p4cd:{chat_id}:{uid}:{act}"
        left = await P3.kget(conn, ckey) - now()
        if left > 0:
            raise GameError(f"«{title}» снова через {left // 3600} ч {left % 3600 // 60} мин")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, cost)
        p["energy"] -= cost
        t = now()
        n_real = len(await gangwar._real_members(conn, g["id"]))
        good, delta, text = True, 0, ""
        if act == "launder":
            if (g["treasury"] or 0) < 1000:
                raise GameError("В общаке меньше 1000 ₽: отмывать нечего")
            amt = min(1500, int(g["treasury"] * 0.03))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (amt, g["id"]))
            await G2._merit(conn, chat_id, uid, 3)
            text = f"🧮 Деньги отмыты: общак +{amt} ₽, авторитет +3."
        elif act == "audit":
            cur = await conn.execute("SELECT user_id, dues_due FROM gang_member WHERE gang_id = ? AND user_id > 0 AND dues_due > 0 AND dues_due < ? ORDER BY dues_due LIMIT 1", (g["id"], t))
            row = await cur.fetchone()
            if not row:
                raise GameError("Должников нет: все платят вовремя")
            await conn.execute("UPDATE gang_member SET dues_due = ? WHERE chat_id = ? AND user_id = ?", (t + G2.DUES_PERIOD, chat_id, row[0]))
            text = f"🧮 Долг {await P3._uname(conn, chat_id, row[0])} прощён: срок взноса сдвинут на сутки."
        elif act == "safe":
            if random.random() < max(0.2, 0.5 - 0.03 * p["wanted"]):
                loot = random.randint(1500, 3500)
                share = int(loot * 0.85)
                p["money"] += share
                await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (loot - share, g["id"]))
                p["wanted"], p["wanted_ts"] = min(rp.MAX_WANTED, p["wanted"] + 1), t
                delta = share
                text = f"🔓 Сейф вскрыт! Тебе {share} ₽, в общак {loot - share} ₽. Розыск +1⭐."
            else:
                p["wanted"], p["wanted_ts"] = min(rp.MAX_WANTED, p["wanted"] + 1), t
                fine = min(p["money"], 150)
                p["money"] -= fine
                p["jail_until"], p["esc_used"] = t + 10 * 60, 0
                good, delta = False, -fine
                text = f"🚔 Сейф оказался с сигнализацией: штраф {fine} ₽ и 10 минут тюрьмы."
        elif act == "jam":
            await GP._kset(conn, f"p4jam:{g['id']}", t + 24 * 3600)
            text = "🔓 Глушилка включена на сутки: ближайшее нападение или ОМОН на банду сорвётся."
        elif act == "recon":
            gid = int(arg or 0)
            other = await gangwar._gang_row(conn, gid)
            if not other or other["chat_id"] != chat_id or gid == g["id"]:
                raise GameError("Выбери другую банду для разведки")
            cur = await conn.execute("SELECT COUNT(*) FROM gang_member WHERE gang_id = ?", (gid,))
            n = (await cur.fetchone())[0]
            tr = other["treasury"] or 0
            rich = "бедные" if tr < 2000 else "средний достаток" if tr < 6000 else "богатые"
            guard = await P3.proj_level(conn, gid, "guard")
            cur = await conn.execute("SELECT COUNT(*) FROM territory WHERE chat_id = ? AND owner = ?", (chat_id, gid))
            terr = (await cur.fetchone())[0]
            bonus = ""
            war = await gangwar._active_war(conn, g["id"])
            if war and gid in (war["a"], war["b"]):
                await _war_add(conn, g["id"], 5)
                bonus = " Война идёт: +5 очков."
            text = f"👀 «{other['name']}»: бойцов {n}, {rich}, охрана {guard}/3, районов {terr}.{bonus}"
        elif act == "scan":
            case = await GP._case(conn, g["id"])
            if not case:
                text = "👀 Всё тихо: дела о крысе сейчас нет."
            else:
                locked = [c for c in await GP._clues(conn, case["id"]) if not c["fake"] and not GP._unlocked(c)]
                if locked and random.random() < 0.45:
                    await conn.execute("UPDATE rat_clue SET unlock_ts = ? WHERE id = ?", (t, locked[0]["id"]))
                    text = "👀 Зацепка нашлась: открыта новая улика по делу о крысе!"
                else:
                    text = "👀 Проверил своих, но ничего определённого не нашёл."
        elif act == "getaway":
            await GP._kset(conn, f"p4drive:{g['id']}", t + 3 * 3600)
            text = "🚗 Машина прогрета: ближайший налёт банды получит +10% к шансу (3 часа)."
        elif act == "race":
            if await _war_add(conn, g["id"], 12):
                text = "🚗 Гонка выиграна: +12 очков в войне!"
            else:
                p["money"] += 200
                await G2._merit(conn, chat_id, uid, 4)
                delta = 200
                text = "🚗 Гонка без войны: +200 ₽ и +4 авторитета."
        elif act == "brawl":
            if await _war_add(conn, g["id"], 10):
                text = "💪 Разборка выиграна: +10 очков в войне!"
            else:
                await G2._merit(conn, chat_id, uid, 4)
                text = "💪 Показал, кто тут сильный: +4 авторитета."
                if random.random() < 0.10:
                    await G2._beat(conn, chat_id, uid, 10)
                    text += " Но и самому прилетело: 10 минут отлёживаешься."
                    good = False
        elif act == "cover":
            target = int(arg or 0)
            if target == uid or not any(m["user_id"] == target for m in members):
                raise GameError("Выбери товарища по банде")
            await GP._kset(conn, f"p4cover:{chat_id}:{target}", t + 24 * 3600)
            text = f"💪 {await P3._uname(conn, chat_id, target)} под твоей крышей на сутки: побоям не бывать."
        elif act == "agitate":
            cur = await conn.execute(
                """SELECT r.user_id, r.money FROM rp_player r JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id
                   WHERE r.chat_id = ? AND r.user_id > 0 AND r.user_id NOT IN (SELECT user_id FROM gang_member WHERE chat_id = ?)
                     AND r.user_id NOT IN (SELECT user_id FROM gang_invite WHERE gang_id = ?)""", (chat_id, chat_id, g["id"]))
            cands = await cur.fetchall()
            if cands and n_real < G.MAX_MEMBERS:
                cuid = random.choice(cands)[0]
                await conn.execute("INSERT OR REPLACE INTO gang_invite (gang_id, user_id, chat_id, invited_by, ts) VALUES (?, ?, ?, ?, ?)", (g["id"], cuid, chat_id, uid, t))
                cname = await P3._uname(conn, chat_id, cuid)
                await gangwar.say(conn, chat_id, f"📢 Вербовщик «{esc(g['name'])}» присмотрел {esc(cname)} и зовёт его в банду. Приглашение в мини-аппе: Район → ОПГ.")
                text = f"📢 Агитация сработала: приглашение отправлено {cname}."
            else:
                await G2._merit(conn, chat_id, uid, 3)
                text = "📢 Свободных кандидатов нет (или в банде нет мест), но слава о банде растёт: +3 авторитета."
        elif act == "buzz":
            await G2._merit(conn, chat_id, uid, 5)
            await conn.execute("UPDATE gang SET treasury = treasury + 200, last_act = ? WHERE id = ?", (t, g["id"]))
            await rp._news(conn, chat_id, f"📢 Об ОПГ «{g['name']}» заговорил весь район")
            text = "📢 Раскрутка удалась: +5 авторитета, +200 ₽ в общак, банда снова «в деле»."
        elif act == "banquet":
            tr = g["treasury"] or 0
            if tr < 100:
                raise GameError("Общак пуст: банкета не будет")
            per = max(1, min(100, tr // max(1, n_real)))
            spent = per * n_real
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (spent, g["id"]))
            for m in members:
                if m["user_id"] > 0:
                    await rp._load(conn, chat_id, m["user_id"])
                    await conn.execute("UPDATE rp_player SET energy = MIN(energy + 20, 150) WHERE chat_id = ? AND user_id = ?", (chat_id, m["user_id"]))
            p["energy"] = min(150, p["energy"] + 20)
            text = f"🍽 Банкет на весь район: всем +20 энергии, из общака ушло {spent} ₽."
        elif act == "feast":
            await GP._kset(conn, f"p4feast:{g['id']}", t + 3 * 3600)
            text = "🍽 Стол накрыт: на сходке в ближайшие 3 часа каждый получит +5 авторитета."
        elif act == "tipoff":
            pay = random.randint(200, 400)
            p["money"] += pay
            p["cop_rep"] = p.get("cop_rep", 0) + 2
            delta = pay
            if random.random() < 0.08:
                p["cop_rep"] = max(0, p["cop_rep"] - 5)
                fine = min(p["money"], 300)
                p["money"] -= fine
                p["jail_until"], p["esc_used"] = t + 15 * 60, 0
                await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
                await gangwar.say(conn, chat_id, f"🕶 <b>Информатор вскрыт!</b> {esc(name)} сливал полиции дела ОПГ «{esc(g['name'])}». Изгнан, штраф {fine} ₽.")
                await rp._save(conn, chat_id, uid, p)
                await GP._kset(conn, ckey, t + cd)
                return P3._res(f"🕶 Тебя вычислили: изгнан из банды, штраф {fine} ₽, 15 минут в тюрьме.", False, -fine)
            text = f"🕶 Информация ушла полиции: +{pay} ₽, очки полицейского +2."
        await GP._kset(conn, ckey, t + cd)
        await G2.log_event(conn, chat_id, uid, "spec_" + act, 1)
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE gang SET last_act = ? WHERE id = ?", (t, g["id"]))
        return P3._res(text, good, delta)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ командные активности ═════════════════════
WATCH_HOURS, WATCH_ENERGY, WATCH_CD = 4, 10, 4 * 3600
POT_PER_MEMBER, POT_MERIT, POT_PAY = 1500, 8, 300
TOURNEY_ENERGY, TOURNEY_CD = 10, 45 * 60


def _week() -> str:
    n = now_local().isocalendar()
    return f"{n[0]}-{n[1]}"


async def watch_start(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, _, me = await _need_member(conn, chat_id, uid)
        if await P3.kget(conn, f"p4watchcd:{chat_id}:{uid}") > now():
            raise GameError("Ты уже дежурил: следующее дежурство через несколько часов")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, WATCH_ENERGY)
        p["energy"] -= WATCH_ENERGY
        await conn.execute("INSERT OR REPLACE INTO gang_watch (gang_id, uid, until) VALUES (?, ?, ?)", (g["id"], uid, now() + WATCH_HOURS * 3600))
        await GP._kset(conn, f"p4watchcd:{chat_id}:{uid}", now() + WATCH_CD)
        await G2._merit(conn, chat_id, uid, 2)
        await rp._save(conn, chat_id, uid, p)
        return P3._res(f"🌙 Ты в ночном дозоре {WATCH_HOURS} ч: ОМОН заметят заранее (реже приезжает), отпор нападениям сильнее. Авторитет +2.")
    return await P3._act(chat_id, uid, run)


async def pot_add(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount or 0)

    async def run(conn):
        if amount < 50:
            raise GameError("В котёл — от 50 ₽")
        g, members, me = await _need_member(conn, chat_id, uid)
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < amount:
            raise GameError("Столько у тебя нет")
        week = _week()
        n_real = len(await gangwar._real_members(conn, g["id"]))
        goal = POT_PER_MEMBER * max(2, n_real)
        cur = await conn.execute("SELECT total, done FROM gang_pot WHERE gang_id = ? AND week = ?", (g["id"], week))
        row = await cur.fetchone()
        total, done = (row[0], row[1]) if row else (0, 0)
        if done:
            raise GameError("Котёл этой недели уже собран: следующий со следующей недели")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, uid))
        total += amount
        await conn.execute("INSERT INTO gang_pot (gang_id, week, total, done) VALUES (?, ?, ?, 0) ON CONFLICT(gang_id, week) DO UPDATE SET total = excluded.total", (g["id"], week, total))
        await G2.log_event(conn, chat_id, uid, "pot", amount)
        text = f"🍯 В общий котёл: +{amount} ₽ ({total}/{goal})."
        if total >= goal:
            await conn.execute("UPDATE gang_pot SET done = 1 WHERE gang_id = ? AND week = ?", (g["id"], week))
            await conn.execute("UPDATE gang SET treasury = treasury + ?, last_act = ? WHERE id = ?", (total, now(), g["id"]))
            for m in members:
                if m["user_id"] > 0:
                    await rp._load(conn, chat_id, m["user_id"])
                    await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (POT_PAY, chat_id, m["user_id"]))
                    await G2._merit(conn, chat_id, m["user_id"], POT_MERIT)
            await gangwar.say(conn, chat_id, f"🍯 <b>Общий котёл собран!</b> ОПГ «{esc(g['name'])}» скинулась на {total} ₽: сумма ушла в общак, каждому по {POT_PAY} ₽ и +{POT_MERIT} авторитета.")
            text += f" Цель достигнута! Всем по {POT_PAY} ₽ и +{POT_MERIT} авторитета, котёл ушёл в общак."
        return P3._res(text, True, -amount)
    return await P3._act(chat_id, uid, run)


async def tourney_fight(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, members, me = await _need_member(conn, chat_id, uid)
        real = [m for m in members if m["user_id"] > 0 and m["user_id"] != uid]
        if not real:
            raise GameError("Для турнира нужен хотя бы ещё один человек в банде")
        if await P3.kget(conn, f"p4tcd:{chat_id}:{uid}") > now():
            raise GameError("Ты ещё не отдышался после спарринга")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, TOURNEY_ENERGY)
        p["energy"] -= TOURNEY_ENERGY
        opp = random.choice(real)
        chance = min(0.7, max(0.3, 0.5 + ((me.get("merit") or 0) - (opp.get("merit") or 0)) / 400))
        win = random.random() < chance
        week = _week()
        await conn.execute("INSERT INTO gang_tourney (gang_id, week, uid, pts, fights) VALUES (?, ?, ?, ?, 1) ON CONFLICT(gang_id, week, uid) DO UPDATE SET pts = pts + ?, fights = fights + 1",
                           (g["id"], week, uid, 1 if win else 0, 1 if win else 0))
        await GP._kset(conn, f"p4tcd:{chat_id}:{uid}", now() + TOURNEY_CD)
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE gang SET last_act = ? WHERE id = ?", (now(), g["id"]))
        oname = await P3._uname(conn, chat_id, opp["user_id"])
        return P3._res(f"🥊 Спарринг с {oname} (шанс ~{int(chance * 100)}%): " + ("победа, +1 очко турнира!" if win else "проигрыш, очка нет."), win)
    return await P3._act(chat_id, uid, run)


async def _weekly_tourney(conn) -> None:
    n = now_local()
    if n.weekday() != 6 or (n.hour, n.minute) < (20, 45):
        return
    week = _week()
    cur = await conn.execute("SELECT id, chat_id, name, treasury FROM gang WHERE is_ai = 0")
    for gid, chat_id, gname, tr in await cur.fetchall():
        key = f"p4tw:{gid}:{week}"
        if await P3.kget(conn, key):
            continue
        await GP._kset(conn, key, now())
        cur2 = await conn.execute("SELECT uid, pts, fights FROM gang_tourney WHERE gang_id = ? AND week = ? ORDER BY pts DESC, fights ASC LIMIT 1", (gid, week))
        row = await cur2.fetchone()
        if not row or row[1] < 2:
            continue
        prize = min(500, tr)
        if prize:
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (prize, gid))
            await rp._load(conn, chat_id, row[0])
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (prize, chat_id, row[0]))
        await G2._merit(conn, chat_id, row[0], 15)
        name = await P3._uname(conn, chat_id, row[0])
        await gangwar.say(conn, chat_id, f"🏆 Лучший боец недели в «{esc(gname)}»: <b>{esc(name)}</b> ({row[1]} побед в {row[2]} спаррингах). Награда: {prize} ₽ и +15 авторитета.")


# ═════════════════════ интерфейс и фоновые задачи ═════════════════════
async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    t = now()
    p = await rp._load(conn, chat_id, uid)
    ll = law_level(p)
    p4 = {"defense": {"weapon": p.get("weapon") or 0, "guard": p.get("guard") or 0, "lawyer": ll,
                      "weapon_next": WEAPON[(p.get("weapon") or 0) + 1] if (p.get("weapon") or 0) < 3 else None,
                      "guard_next": GUARD[(p.get("guard") or 0) + 1] if (p.get("guard") or 0) < 3 else None,
                      "weapon_title": WEAPON[p.get("weapon") or 0][0], "guard_title": GUARD[p.get("guard") or 0][0],
                      "chance": int(_user_chance(p) * 100), "in_gang": bool(g)}}
    atk = None
    if g and my_role == "boss":
        atk = await _atk_open(conn, chat_id, "gang", g["id"])
    elif not g:
        atk = await _atk_open(conn, chat_id, "user", uid)
    if atk and atk["expires"] > t:
        ai = await gangwar._gang_row(conn, atk["attacker"])
        ic, title = KINDS[atk["kind"]]
        left = atk["expires"] - t
        import gangplus6
        press = await gangplus6.pressure(conn, chat_id)
        if atk["tkind"] == "gang":
            chance = int(max(0.05, await _gang_chance(conn, g) - min(0.15, 0.1 * (press - 1))) * 100)
            text = f"«{ai['name'] if ai else '?'}» {TEXT_GANG[atk['kind']]}."
        else:
            import liveevents
            chance = int(max(0.05, _user_chance(p) + (await liveevents.mods(conn, chat_id))["repel_bonus"] - min(0.15, 0.1 * (press - 1))) * 100)
            text = f"«{ai['name'] if ai else '?'}» {TEXT_USER[atk['kind']]}."
        p4["attack"] = {"id": atk["id"], "icon": ic, "title": title, "left": left, "text": text, "price": atk["price"], "chance": chance}
    elif g and my_role != "boss" and await _atk_open(conn, chat_id, "gang", g["id"]):
        p4["attack_warn"] = True
    out["p4"] = p4
    m = out.get("mine")
    if not g or not m or g.get("is_ai"):
        return
    cur = await conn.execute("SELECT user_id, spec FROM gang_member WHERE gang_id = ?", (g["id"],))
    specs = {r[0]: r[1] for r in await cur.fetchall()}
    mine = specs.get(uid) or ""
    for x in m["members"]:
        x["spec"] = specs.get(x["id"], "")
        if x["spec"] in SPECS:
            x["spec_title"] = f"{SPECS[x['spec']][0]} {SPECS[x['spec']][1]}"
    acts = []
    if mine in SPECS:
        for key, (title, desc, cost, cd, need) in SPECS[mine][2].items():
            left = max(0, await P3.kget(conn, f"p4cd:{chat_id}:{uid}:{key}") - t)
            acts.append({"key": key, "title": title, "desc": desc, "energy": cost, "left": left, "need": need})
    targets = {"gang": [{"id": x["id"], "name": x["name"]} for x in out.get("gangs", []) if not x.get("mine")],
               "member": [{"id": x["id"], "name": x["name"]} for x in m["members"] if x["id"] != uid and x["id"] > 0]}
    week = _week()
    cur = await conn.execute("SELECT total, done FROM gang_pot WHERE gang_id = ? AND week = ?", (g["id"], week))
    row = await cur.fetchone()
    n_real = len(await gangwar._real_members(conn, g["id"]))
    cur = await conn.execute("SELECT t.uid, t.pts, t.fights, u.username FROM gang_tourney t LEFT JOIN users u ON u.user_id = t.uid AND u.chat_id = ? WHERE t.gang_id = ? AND t.week = ? ORDER BY t.pts DESC, t.fights ASC LIMIT 3", (chat_id, g["id"], week))
    top = [{"name": r[3] or "?", "pts": r[1], "fights": r[2]} for r in await cur.fetchall()]
    m["p4"] = {"spec": mine, "specs": [{"key": k, "icon": v[0], "title": v[1]} for k, v in SPECS.items()],
               "spec_wait": max(0, await P3.kget(conn, f"p4specset:{chat_id}:{uid}") - t), "acts": acts, "targets": targets,
               "watch": {"n": await _watchers(conn, g["id"]), "wait": max(0, await P3.kget(conn, f"p4watchcd:{chat_id}:{uid}") - t), "energy": WATCH_ENERGY},
               "pot": {"total": row[0] if row else 0, "goal": POT_PER_MEMBER * max(2, n_real), "done": bool(row and row[1])},
               "tourney": {"top": top, "wait": max(0, await P3.kget(conn, f"p4tcd:{chat_id}:{uid}") - t), "energy": TOURNEY_ENERGY}}


async def tick(conn) -> None:
    try:
        t = now()
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM rp_attack WHERE status = 'open' AND expires <= ?", (t,))
        for atk in [dict(r) for r in await cur.fetchall()]:
            await _atk_resolve(conn, atk, "timeout")
        await conn.execute("DELETE FROM gang_watch WHERE until < ?", (t - 3600,))
        cur = await conn.execute("SELECT chat_id FROM rp_player GROUP BY chat_id HAVING COUNT(*) >= 2")
        for (chat_id,) in await cur.fetchall():
            import liveevents
            ugap = int(ATK_GAP_USER * (await liveevents.mods(conn, chat_id))["attack_user_gap_mult"])
            for only, gap in (("gang", ATK_GAP), ("user", ugap)):
                key = f"p4atk:{chat_id}:{only}"
                last = await P3.kget(conn, key)
                if not last:
                    await GP._kset(conn, key, t - gap + 1800)
                    continue
                if t - last >= gap and 9 <= now_local().hour < 23:
                    if await _atk_new(conn, chat_id, only):
                        await GP._kset(conn, key, t)
        await _weekly_tourney(conn)
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 4")
