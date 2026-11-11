"""ОПГ-плюс 5: ресурсы и производство, торговля и договоры между бандами, карта района с маршрутами,
уважение и страх, многоэтапные дела, контрразведка (кроты конкурентов, подстава)."""
import json
import logging
import random
import time
from typing import Optional

import aiosqlite

import gang as G
import gangplus as GP
import gangplus2 as G2
import gangplus3 as P3
import charter
import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc, now_local

log = logging.getLogger(__name__)


def now() -> int:
    return int(time.time())


async def _gx(conn, gid: int) -> Optional[dict]:
    return await gangwar._gang_row(conn, gid)


# ═════════════════════ карта района ═════════════════════
EDGES = [("market", "port"), ("port", "embank"), ("embank", "bath"), ("bath", "club"), ("club", "dacha"), ("dacha", "elev"),
         ("elev", "tire"), ("tire", "garages"), ("garages", "station"), ("station", "market"), ("station", "club"), ("port", "elev")]
NEIGH = {}
for _a, _b in EDGES:
    NEIGH.setdefault(_a, set()).add(_b)
    NEIGH.setdefault(_b, set()).add(_a)
CLAIM_COST, CLAIM_CD, BLOCK_COST, BLOCK_CD, BLOCK_HOURS = 1500, 2 * 3600, 800, 2 * 3600, 4
FORTIFY_COST, FORTIFY_CD, FORTIFY_GAIN = 700, 2 * 3600, 18
CONTROL_TICK_GAP, CONTROL_GROW, CONTROL_BLOCK_LOSS, POLICE_DECAY = 30 * 60, 1, 5, 2
ROUTE_BONUS = 1.2


async def _owners(conn, chat_id: int) -> dict:
    cur = await conn.execute("SELECT key, owner FROM territory WHERE chat_id = ?", (chat_id,))
    return {r[0]: r[1] for r in await cur.fetchall()}


async def terr_mult(conn, chat_id: int, key: str) -> float:
    """Множитель дохода района: контроль, полиция, связи и блокада."""
    own = await _owners(conn, chat_id)
    owner = own.get(key, 0)
    mult = 1.0
    if owner and any(own.get(n) == owner for n in NEIGH.get(key, ())):
        mult *= ROUTE_BONUS
    cur = await conn.execute("SELECT control, police FROM territory WHERE chat_id = ? AND key = ?", (chat_id, key))
    row = await cur.fetchone()
    if owner and row:
        control, police = int(row[0] or 0), int(row[1] or 0)
        mult *= 0.4 + 0.6 * max(0, min(100, control)) / 100
        mult *= max(0.55, 1 - max(0, min(100, police)) / 200)
    if await P3.kget(conn, f"tblock:{chat_id}:{key}") > now():
        mult *= 0.5
    return mult


async def _borders(conn, chat_id: int, a: int, b: int) -> Optional[bool]:
    """None, если у кого-то нет районов (граница не нужна); иначе есть ли общая граница."""
    own = await _owners(conn, chat_id)
    ta = [k for k, o in own.items() if o == a]
    tb = [k for k, o in own.items() if o == b]
    if not ta or not tb:
        return None
    return any(n in tb for k in ta for n in NEIGH.get(k, ()))


async def check_war_extra(conn, chat_id: int, a: int, b: int) -> None:
    if await truce_between(conn, a, b):
        raise GameError("Между вашими бандами действует мирный договор: сначала дождись его конца")
    if await _borders(conn, chat_id, a, b) is False:
        raise GameError("У вас нет общей границы: сначала займи район рядом с врагом («Карта»)")


async def claim(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        own = await _owners(conn, chat_id)
        if own.get(key, 0) != 0:
            raise GameError("Район уже занят")
        mine = [k for k, o in own.items() if o == g["id"]]
        if mine and not any(n in mine for n in NEIGH[key]):
            raise GameError("Занять можно только район рядом с твоим")
        if await P3.kget(conn, f"p5claim:{g['id']}") > now():
            raise GameError("Банда ещё осваивает прошлый район")
        ccost = await charter.cost(conn, g["id"], CLAIM_COST)
        if (g["treasury"] or 0) < ccost:
            raise GameError(f"Нужно {ccost} ₽ в общаке на «взятку участковому»")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (ccost, g["id"]))
        await conn.execute("UPDATE territory SET owner = ?, last_pay = ?, control = 50, police = 0 WHERE chat_id = ? AND key = ?", (g["id"], now(), chat_id, key))
        await GP._kset(conn, f"p5claim:{g['id']}", now() + CLAIM_CD)
        await rep_add(conn, g["id"], 2, 1)
        t = gangwar.TERR[key]
        await rp._news(conn, chat_id, f"🏴 «{g['name']}» заняла район {t[1]} {t[2]}")
        await gangwar.say(conn, chat_id, f"🏴 «{esc(g['name'])}» заняла свободный район {t[1]} <b>{t[2]}</b>.")
        return P3._res(f"🏴 {t[1]} {t[2]} теперь ваш: +{t[3]} ₽/ч каждому, плюс сырьё для производства.")
    return await P3._act(chat_id, uid, run)


async def fortify(chat_id: int, uid: int, name: str, key: str) -> dict:
    """Укрепить свой район: +контроль, дороже и с КД."""
    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        if key not in gangwar.TERR:
            raise GameError("Такого района нет")
        own = await _owners(conn, chat_id)
        if own.get(key, 0) != g["id"]:
            raise GameError("Укреплять можно только свой район")
        gain = int(FORTIFY_GAIN * 1.5) if own.get("garages") == g["id"] else FORTIFY_GAIN
        if await P3.kget(conn, f"p5fort:{g['id']}") > now():
            raise GameError("Банда ещё укрепляет прошлый район")
        fcost = await charter.cost(conn, g["id"], FORTIFY_COST)
        if (g["treasury"] or 0) < fcost:
            raise GameError(f"Нужно {fcost} ₽ в общаке на укрепление")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (fcost, g["id"]))
        await conn.execute("UPDATE territory SET control = MIN(100, control + ?), last_pay = ? WHERE chat_id = ? AND key = ?", (gain, now(), chat_id, key))
        await GP._kset(conn, f"p5fort:{g['id']}", now() + FORTIFY_CD)
        await rep_add(conn, g["id"], 1, 2)
        t = gangwar.TERR[key]
        await gangwar.say(conn, chat_id, f"🧱 «{esc(g['name'])}» укрепила район {t[1]} <b>{t[2]}</b>: контроль +{gain}.")
        return P3._res(f"🧱 {t[1]} {t[2]}: контроль +{gain} (−{fcost} ₽ из общака).")
    return await P3._act(chat_id, uid, run)


async def control_tick(conn) -> None:
    """Раз в 30 минут: контроль растёт у связанных районов, полиция остывает, блокады душат."""
    t = now()
    cur = await conn.execute("SELECT DISTINCT chat_id FROM territory")
    for (chat_id,) in await cur.fetchall():
        last = await P3.kget(conn, f"p5ctick:{chat_id}")
        if last and t - last < CONTROL_TICK_GAP:
            continue
        await GP._kset(conn, f"p5ctick:{chat_id}", t)
        own = await _owners(conn, chat_id)
        import doom
        doom_drift = await doom.drift(conn, chat_id)
        cur2 = await conn.execute("SELECT key, owner, control, police FROM territory WHERE chat_id = ?", (chat_id,))
        rows = await cur2.fetchall()
        if own.get("club"):
            await conn.execute("UPDATE police_case SET pts = MAX(0, pts - 1) WHERE chat_id = ? AND gang_id = ?", (chat_id, own["club"]))
        for key, owner, control, police in rows:
            control, police = int(control or 0), int(police or 0)
            if owner:
                route = any(own.get(n) == owner for n in NEIGH.get(key, ()))
                gain = CONTROL_GROW + (1 if route else 0) + (1 if own.get("dacha") == owner else 0) + (1 if await charter.chartered(conn, owner) else 0)
                if await P3.kget(conn, f"tblock:{chat_id}:{key}") > t:
                    gain -= CONTROL_BLOCK_LOSS
                gain -= doom_drift
                control = max(0, min(100, control + gain))
                police = max(0, police - POLICE_DECAY * (2 if own.get("bath") == owner else 1) - (1 if await charter.chartered(conn, owner) else 0))
                if control <= 0:
                    await conn.execute("UPDATE territory SET owner = 0, control = 0, police = 0 WHERE chat_id = ? AND key = ?", (chat_id, key))
                    await gangwar.say(conn, chat_id, f"🏳 Район {gangwar.TERR[key][1]} <b>{gangwar.TERR[key][2]}</b> вышел из-под контроля: банда его теряет.")
                    continue
            else:
                police = max(0, police - POLICE_DECAY)
            await conn.execute("UPDATE territory SET control = ?, police = ? WHERE chat_id = ? AND key = ?", (control, police, chat_id, key))


async def blockade(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        own = await _owners(conn, chat_id)
        victim = own.get(key, 0)
        if not victim or victim == g["id"]:
            raise GameError("Блокировать можно только район другой банды")
        cost = await charter.cost(conn, g["id"], BLOCK_COST // 2 if own.get("tire") == g["id"] else BLOCK_COST)
        mine = [k for k, o in own.items() if o == g["id"]]
        if not any(n in mine for n in NEIGH.get(key, ())):
            raise GameError("Перекрыть маршрут можно только у района рядом с твоим")
        if await P3.kget(conn, f"p5blockcd:{g['id']}") > now():
            raise GameError("Люди ещё не отдохнули после прошлой блокады")
        if (g["treasury"] or 0) < cost:
            raise GameError(f"На блокаду нужно {cost} ₽ из общака")
        if await P3.pact_between(conn, g["id"], victim, ("active",)):
            raise GameError("Нельзя блокировать союзника")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, g["id"]))
        await GP._kset(conn, f"tblock:{chat_id}:{key}", now() + BLOCK_HOURS * 3600)
        await GP._kset(conn, f"p5blockcd:{g['id']}", now() + BLOCK_CD)
        await rep_add(conn, g["id"], 0, 3)
        vg = await _gx(conn, victim)
        t = gangwar.TERR[key]
        await gangwar.say(conn, chat_id, f"🚧 «{esc(g['name'])}» перекрыла дороги к району {t[1]} <b>{t[2]}</b> («{esc(vg['name']) if vg else '?'}»): доход и поставки вдвое меньше на {BLOCK_HOURS} ч.")
        return P3._res(f"🚧 Блокада {t[2]} на {BLOCK_HOURS} ч (−{cost} ₽). Страх банды +3.")
    return await P3._act(chat_id, uid, run)


# ═════════════════════ уважение и страх ═════════════════════
async def rep_add(conn, gid: int, respect: int = 0, fear: int = 0) -> None:
    g = await _gx(conn, gid)
    if not g or g.get("is_ai"):
        return
    if (g.get("fear") or 0) >= 70 and respect > 0:
        respect = max(1, respect // 2)  # перед страшными не выслуживаются
    await conn.execute("UPDATE gang SET respect = MAX(0, MIN(100, respect + ?)), fear = MAX(0, MIN(100, fear + ?)) WHERE id = ?", (respect, fear, gid))


async def accept_prob(conn, gid: int) -> float:
    g = await _gx(conn, gid)
    r, f = (g.get("respect") or 0), (g.get("fear") or 0)
    return max(0.3, min(0.95, 0.7 + 0.002 * r - 0.003 * max(0, f - 40)))


async def attack_weight(conn, gid: int) -> float:
    g = await _gx(conn, gid)
    f = (g.get("fear") or 0)
    return 0.4 if f >= 60 else 0.7 if f >= 30 else 1.0


async def on_war_end(conn, win: dict, lose: dict) -> None:
    await rep_add(conn, win["id"], 15, 10)
    await rep_add(conn, lose["id"], -5, -5)


async def pick_take(conn, chat_id: int, win_id: int, theirs: list, free: list):
    """Победитель берёт район, граничащий с его владениями (если есть такой)."""
    own = await _owners(conn, chat_id)
    def adj(keys):
        return [k for k in keys if any(own.get(n) == win_id for n in NEIGH.get(k, ()))]
    pool = adj(theirs) or theirs or adj(free) or free
    return random.choice(pool) if pool else None


async def _daily(conn) -> None:
    day = now_local().strftime("%Y-%m-%d")
    cur = await conn.execute("SELECT id, chat_id, respect, fear, treasury FROM gang WHERE is_ai = 0")
    for gid, chat_id, r, f, tr in await cur.fetchall():
        key = f"p5day:{gid}"
        if await gangwar.kget(conn, key) == day:
            continue
        await gangwar.kset(conn, key, day)
        tribute = min(300, f * 3) if f >= 30 else 0
        await conn.execute("UPDATE gang SET respect = MAX(0, respect - 1), fear = MAX(0, fear - 1), treasury = treasury + ? WHERE id = ?", (tribute, gid))
        if tribute:
            await gangwar.say(conn, chat_id, f"😨 Перед «{esc((await _gx(conn, gid))['name'])}» трясутся: данники принесли в общак {tribute} ₽.")


# ═════════════════════ ресурсы и производство ═════════════════════
# Три ресурса вместо пяти: товары, оружие, информация.
RES = {"goods": ("🛍", "Товары", 40), "arms": ("🔫", "Оружие", 70), "intel": ("🧠", "Информация", 55)}
PRODUCTS = {"contraband": ("💎", "Контрабанда", 900), "premium": ("⚙️", "Тюнинг-детали", 700), "lux": ("🍾", "Люкс-товар", 1000)}
RECIPES = {"contraband": {"goods": 4, "intel": 2}, "premium": {"arms": 4, "goods": 2}, "lux": {"goods": 3, "arms": 2, "intel": 1}}
TERR_RES = {"market": "goods", "port": "goods", "station": "intel", "garages": "arms", "embank": "goods", "elev": "goods",
            "bath": "intel", "tire": "arms", "club": "intel", "dacha": "arms"}
# У каждого района своя особенность: работает на банду-хозяина (код, описание).
TERR_PERK = {
    "market": ("pay", "доход района участникам ×1.5"),
    "port": ("prod", "сырьё здесь копится вдвое быстрее"),
    "station": ("recruit", "ИИ-помощники приносят в общак в 1.5 раза больше"),
    "garages": ("fort", "укрепление районов даёт +27 вместо +18"),
    "embank": ("cash", "+60 ₽ в час в общак с туристов"),
    "elev": ("depot", "склад банды вмещает на 100 больше"),
    "bath": ("cool", "давление полиции спадает вдвое быстрее на всех районах банды"),
    "tire": ("block", "блокада чужих районов стоит вдвое дешевле"),
    "club": ("case", "дело на банду тает на 1 очко каждые 30 минут"),
    "dacha": ("grow", "контроль растёт на +1 быстрее на всех районах банды"),
}
RES_RATE, STOCK_CAP = 3, 150
ITEMS = {**{k: (v[0], v[1], v[2]) for k, v in RES.items()}, **{k: (v[0], v[1], v[2]) for k, v in PRODUCTS.items()}}


async def _stock(conn, gid: int) -> dict:
    cur = await conn.execute("SELECT res, qty FROM gang_stock WHERE gang_id = ?", (gid,))
    return {r[0]: r[1] for r in await cur.fetchall()}


async def _stock_add(conn, gid: int, res: str, qty: int) -> None:
    await conn.execute("INSERT INTO gang_stock (gang_id, res, qty) VALUES (?, ?, ?) ON CONFLICT(gang_id, res) DO UPDATE SET qty = MAX(0, qty + ?)", (gid, res, max(0, qty), qty))


def price_index(gid: int, item: str) -> float:
    """Цена зависит от покупателя и дня: у каждой ИИ-банды свой спрос."""
    day = now_local().strftime("%Y-%m-%d")
    r = random.Random(f"{day}:{gid}:{item}")
    return r.uniform(0.85, 1.15) if gid == 0 else r.uniform(0.8, 1.4)


async def _stock_tick(conn) -> None:
    cur = await conn.execute("SELECT DISTINCT chat_id FROM territory")
    for (chat_id,) in await cur.fetchall():
        last = await P3.kget(conn, f"p5prod:{chat_id}")
        hours = min(12, (now() - last) // 3600) if last else 0
        if not last:
            await GP._kset(conn, f"p5prod:{chat_id}", now())
            continue
        if hours < 1:
            continue
        await GP._kset(conn, f"p5prod:{chat_id}", last + hours * 3600)
        own = await _owners(conn, chat_id)
        for key, owner in own.items():
            if not owner or key not in TERR_RES:
                continue
            g = await _gx(conn, owner)
            if not g:
                continue
            units = int(RES_RATE * hours * await terr_mult(conn, chat_id, key) * (1 + await P3.proj_effect(conn, owner, "hq")) * (2 if key == "port" else 1))
            cap = STOCK_CAP + 50 * await P3.proj_level(conn, owner, "depot") + (100 if own.get("elev") == owner else 0)
            if key == "embank":
                await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (60 * hours, owner))
            have = (await _stock(conn, owner)).get(TERR_RES[key], 0)
            await _stock_add(conn, owner, TERR_RES[key], max(0, min(units, cap - have)))


async def _need_trader(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g or g.get("is_ai"):
        raise GameError("Ты не состоишь в ОПГ")
    if next(m["role"] for m in members if m["user_id"] == uid) == "grunt":
        raise GameError("Склад и торговля: дело босса и замов")
    return g, members


async def sell_all(chat_id: int, uid: int, name: str) -> dict:
    """Один клик: сбыть весь склад на рынок по текущим ценам."""
    async def run(conn):
        g, _ = await _need_trader(conn, chat_id, uid)
        st = await _stock(conn, g["id"])
        have = {k: v for k, v in st.items() if v > 0 and k in ITEMS}
        if not have:
            raise GameError("Склад пуст: сначала накопите или произведите товар")
        gx = await _gx(conn, g["id"])
        bonus = 1.05 if (gx.get("respect") or 0) >= 30 else 1.0
        import liveevents
        mult = (await liveevents.mods(conn, chat_id))["sell_mult"]
        total = 0
        for item, qty in have.items():
            total += int(ITEMS[item][2] * price_index(0, item) * qty * bonus * mult)
            await _stock_add(conn, g["id"], item, -qty)
        await conn.execute("UPDATE gang SET treasury = treasury + ?, last_act = ? WHERE id = ?", (total, now(), g["id"]))
        await rep_add(conn, g["id"], 1, 0)
        await G2.log_event(conn, chat_id, uid, "trade", total)
        return P3._res(f"💱 Склад сбыт целиком: +{total} ₽ в общак.")
    return await P3._act(chat_id, uid, run)


async def craft(chat_id: int, uid: int, name: str, recipe: str, qty=1) -> dict:
    qty = max(1, min(10, int(qty or 1)))

    async def run(conn):
        g, _ = await _need_trader(conn, chat_id, uid)
        if recipe not in RECIPES:
            raise GameError("Такого рецепта нет")
        st = await _stock(conn, g["id"])
        for res, n in RECIPES[recipe].items():
            if st.get(res, 0) < n * qty:
                raise GameError(f"Не хватает: {RES[res][1]} (нужно {n * qty}, есть {st.get(res, 0)})")
        for res, n in RECIPES[recipe].items():
            await _stock_add(conn, g["id"], res, -n * qty)
        await _stock_add(conn, g["id"], recipe, qty)
        await conn.execute("UPDATE gang SET last_act = ? WHERE id = ?", (now(), g["id"]))
        ic, title, _p = ITEMS[recipe]
        return P3._res(f"{ic} Произведено: {title} ×{qty}.")
    return await P3._act(chat_id, uid, run)


async def sell(chat_id: int, uid: int, name: str, item: str, qty, buyer=0) -> dict:
    qty = int(qty or 0)
    buyer = int(buyer or 0)

    async def run(conn):
        g, _ = await _need_trader(conn, chat_id, uid)
        if item not in ITEMS or qty < 1:
            raise GameError("Выбери товар и количество")
        st = await _stock(conn, g["id"])
        if st.get(item, 0) < qty:
            raise GameError(f"На складе только {st.get(item, 0)}")
        if buyer:
            b = await _gx(conn, buyer)
            if not b or b["chat_id"] != chat_id or not b["is_ai"]:
                raise GameError("Покупатель не найден")
        base = ITEMS[item][2]
        gx = await _gx(conn, g["id"])
        bonus = 1.05 if (gx.get("respect") or 0) >= 30 else 1.0
        import liveevents
        total = int(base * price_index(buyer, item) * qty * bonus * (await liveevents.mods(conn, chat_id))["sell_mult"])
        await _stock_add(conn, g["id"], item, -qty)
        await conn.execute("UPDATE gang SET treasury = treasury + ?, last_act = ? WHERE id = ?", (total, now(), g["id"]))
        await rep_add(conn, g["id"], 1, 0)
        await G2.log_event(conn, chat_id, uid, "trade", total)
        who = (await _gx(conn, buyer))["name"] if buyer else "рынку"
        return P3._res(f"{ITEMS[item][0]} Продано {ITEMS[item][1]} ×{qty}: {total} ₽ в общак ({who}).", True, 0)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ договоры между бандами ═════════════════════
TRUCE_AI_BASE, TRUCE_HOURS_AI, TRUCE_HOURS_PL, DEAL_WINDOW = 1500, 24, 48, 24 * 3600


async def truce_between(conn, a: int, b: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM gang_deal WHERE kind = 'truce' AND status = 'active' AND ends > ? AND ((a = ? AND b = ?) OR (a = ? AND b = ?))", (now(), a, b, b, a))
    return bool(await cur.fetchone())


async def truce_buy(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)

    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        ai = await _gx(conn, gid)
        if not ai or ai["chat_id"] != chat_id or not ai["is_ai"]:
            raise GameError("Мир можно купить только у ИИ-банд")
        if await truce_between(conn, g["id"], gid):
            raise GameError("Договор уже действует")
        gx = await _gx(conn, g["id"])
        disc = 0.7 if (gx.get("respect") or 0) >= 60 else 1.0
        price = int((TRUCE_AI_BASE + (g["treasury"] or 0) * 0.10) * disc)
        if (g["treasury"] or 0) < price:
            raise GameError(f"Мир стоит {price} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (price, g["id"]))
        t = now()
        await conn.execute("INSERT INTO gang_deal (chat_id, a, b, kind, item, qty, price, created, expires, ends, status) VALUES (?, ?, ?, 'truce', '', 0, ?, ?, ?, ?, 'active')",
                           (chat_id, g["id"], gid, price, t, t + TRUCE_HOURS_AI * 3600, t + TRUCE_HOURS_AI * 3600))
        await rp._news(conn, chat_id, f"🕊 «{g['name']}» купила мир у «{ai['name']}»")
        await gangwar.say(conn, chat_id, f"🕊 «{esc(g['name'])}» откупилась от «{esc(ai['name'])}»: {TRUCE_HOURS_AI} часа никаких нападений и войн.")
        return P3._res(f"🕊 Мир с «{ai['name']}» на {TRUCE_HOURS_AI} ч за {price} ₽ из общака.")
    return await P3._act(chat_id, uid, run)


async def deal_offer(chat_id: int, uid: int, name: str, gid, kind: str, item="", qty=0, price=0) -> dict:
    gid, qty, price = int(gid), int(qty or 0), int(price or 0)

    async def run(conn):
        nonlocal item, qty
        g, _ = await P3._need_boss(conn, chat_id, uid)
        other = await _gx(conn, gid)
        if not other or other["chat_id"] != chat_id or other["is_ai"] or gid == g["id"]:
            raise GameError("Договариваться можно с другими бандами игроков")
        if price < 100:
            raise GameError("Сумма от 100 ₽")
        cur = await conn.execute("SELECT 1 FROM gang_deal WHERE status = 'open' AND a = ? AND b = ?", (g["id"], gid))
        if await cur.fetchone():
            raise GameError("Предложение этой банде уже отправлено")
        if kind == "trade":
            if item not in ITEMS or qty < 1:
                raise GameError("Выбери товар и количество")
            if (await _stock(conn, g["id"])).get(item, 0) < qty:
                raise GameError("На складе нет столько")
            text = f"продаёт {ITEMS[item][1]} ×{qty} за {price} ₽"
        elif kind == "truce":
            if (g["treasury"] or 0) < price:
                raise GameError("В общаке нет такой суммы")
            item, qty = "", 0
            text = f"предлагает мир на {TRUCE_HOURS_PL} ч за {price} ₽"
        else:
            raise GameError("Неизвестный договор")
        t = now()
        await conn.execute("INSERT INTO gang_deal (chat_id, a, b, kind, item, qty, price, created, expires, ends, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'open')",
                           (chat_id, g["id"], gid, kind, item, qty, price, t, t + DEAL_WINDOW))
        await gangwar.say(conn, chat_id, f"📜 «{esc(g['name'])}» {text} банде «{esc(other['name'])}». Босс решает в мини-аппе (Район → ОПГ).")
        return P3._res(f"📜 Предложение отправлено «{other['name']}» на 24 часа.")
    return await P3._act(chat_id, uid, run)


async def deal_answer(chat_id: int, uid: int, name: str, did, accept=True) -> dict:
    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM gang_deal WHERE id = ?", (int(did),))
        r = await cur.fetchone()
        d = dict(r) if r else None
        if not d or d["status"] != "open" or d["b"] != g["id"] or d["expires"] < now():
            raise GameError("Предложение уже неактуально")
        a = await _gx(conn, d["a"])
        if not a:
            raise GameError("Той банды уже нет")
        if not accept:
            await conn.execute("UPDATE gang_deal SET status = 'declined' WHERE id = ?", (d["id"],))
            return P3._res("Предложение отклонено.", False)
        if d["kind"] == "trade":
            if (g["treasury"] or 0) < d["price"]:
                raise GameError(f"В общаке нет {d['price']} ₽")
            if (await _stock(conn, d["a"])).get(d["item"], 0) < d["qty"]:
                await conn.execute("UPDATE gang_deal SET status = 'void' WHERE id = ?", (d["id"],))
                raise GameError("У продавца уже нет такого товара")
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (d["price"], g["id"]))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (d["price"], d["a"]))
            await _stock_add(conn, d["a"], d["item"], -d["qty"])
            await _stock_add(conn, g["id"], d["item"], d["qty"])
            await conn.execute("UPDATE gang_deal SET status = 'done' WHERE id = ?", (d["id"],))
            await rep_add(conn, d["a"], 2, 0)
            await rep_add(conn, g["id"], 2, 0)
            await gangwar.say(conn, chat_id, f"📜 Сделка: «{esc(a['name'])}» продала «{esc(g['name'])}» {ITEMS[d['item']][1]} ×{d['qty']} за {d['price']} ₽.")
            return P3._res(f"📜 Куплено: {ITEMS[d['item']][1]} ×{d['qty']} за {d['price']} ₽.")
        # мирный договор: платит предложивший
        if (a["treasury"] or 0) < d["price"]:
            await conn.execute("UPDATE gang_deal SET status = 'void' WHERE id = ?", (d["id"],))
            raise GameError("У предложившей банды уже нет таких денег")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (d["price"], d["a"]))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (d["price"], g["id"]))
        await conn.execute("UPDATE gang_deal SET status = 'active', ends = ?, expires = ? WHERE id = ?", (now() + TRUCE_HOURS_PL * 3600, now() + TRUCE_HOURS_PL * 3600, d["id"]))
        await rep_add(conn, d["a"], 1, 0)
        await rep_add(conn, g["id"], 1, 0)
        await gangwar.say(conn, chat_id, f"🕊 Мир: «{esc(a['name'])}» и «{esc(g['name'])}» договорились не воевать {TRUCE_HOURS_PL} ч ({d['price']} ₽ в общак «{esc(g['name'])}»).")
        return P3._res(f"🕊 Мир на {TRUCE_HOURS_PL} ч заключён, +{d['price']} ₽ в общак.")
    return await P3._act(chat_id, uid, run)


# ═════════════════════ многоэтапные дела ═════════════════════
OPS = {"warehouse": ("📦", "Склад", (4500, 7000), 2), "bank": ("🏦", "Банк", (7500, 11000), 3), "casino": ("🎰", "Казино", (6500, 9500), 3)}
STAGES = [("recon", "🔎 Разведка", "scout", 15), ("prep", "🧰 Подготовка", "driver", 15), ("crack", "🔓 Взлом", "hacker", 25), ("escape", "🏃 Отход", "fighter", 20)]
OP_COST, OP_HOURS, OP_CD, OP_FAIL_CD = 1000, 6, 4 * 3600, 2 * 3600


async def _op_active(conn, gid: int) -> Optional[dict]:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_op WHERE gang_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (gid,))
    r = await cur.fetchone()
    return dict(r) if r else None


async def op_start(chat_id: int, uid: int, name: str, kind: str) -> dict:
    async def run(conn):
        g, members = await P3._need_boss(conn, chat_id, uid)
        if kind not in OPS:
            raise GameError("Такой цели нет")
        icon, title, rng, need = OPS[kind]
        import gangplus9
        if await gangplus9.wrecked(conn, g["id"], "office"):
            raise GameError("Кабинет разгромлен: большие дела не планируются, пока не отремонтируете")
        if len(await gangwar._real_members(conn, g["id"])) < need:
            raise GameError(f"Для «{title}» нужно минимум {need} человека в банде")
        if await _op_active(conn, g["id"]):
            raise GameError("Одно большое дело уже идёт")
        if await P3.kget(conn, f"p5opcd:{g['id']}") > now():
            raise GameError("Банда залегла на дно после прошлого дела")
        if (g["treasury"] or 0) < OP_COST:
            raise GameError(f"На подготовку нужно {OP_COST} ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (OP_COST, g["id"]))
        t = now()
        await conn.execute("INSERT INTO gang_op (gang_id, chat_id, kind, stage, quality, doers, started, ends, status) VALUES (?, ?, ?, 0, 0, '[]', ?, ?, 'active')",
                           (g["id"], chat_id, kind, t, t + OP_HOURS * 3600))
        await gangwar.say(conn, chat_id, f"{icon} «{esc(g['name'])}» замышляет большое дело: <b>{title}</b>. Четыре этапа, {OP_HOURS} часов: разведка, подготовка, взлом, отход.")
        return P3._res(f"{icon} Дело «{title}» начато (−{OP_COST} ₽). Этапы выполняют участники, лучше всего их профильные специалисты.")
    return await P3._act(chat_id, uid, run)


async def op_stage(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, members = await G._mine(conn, chat_id, uid)
        if not g or g.get("is_ai"):
            raise GameError("Ты не состоишь в ОПГ")
        op = await _op_active(conn, g["id"])
        if not op:
            raise GameError("Большого дела сейчас нет")
        if op["ends"] < now():
            raise GameError("Время дела вышло")
        doers = json.loads(op["doers"] or "[]")
        if doers.count(uid) >= 2:
            raise GameError("Ты уже сделал свои два этапа: пусть работают другие")
        key, label, spec, cost = STAGES[op["stage"]]
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, cost)
        p["energy"] -= cost
        cur = await conn.execute("SELECT spec FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        my_spec = (await cur.fetchone())[0]
        pro = my_spec == spec
        chance = max(0.2, (0.85 if pro else 0.55) - 0.03 * p["wanted"])
        icon, title, rng, need = OPS[op["kind"]]
        t = now()
        if random.random() < chance:
            q = op["quality"] + (1.0 if pro else 0.5)
            doers.append(uid)
            stage = op["stage"] + 1
            await rp._save(conn, chat_id, uid, p)
            if stage < 4:
                await conn.execute("UPDATE gang_op SET stage = ?, quality = ?, doers = ? WHERE id = ?", (stage, q, json.dumps(doers), op["id"]))
                nxt = STAGES[stage]
                return P3._res(f"{label}: готово{' (профи!)' if pro else ''}. Дальше: {nxt[1]} (лучше всего подойдёт {SPECS_TITLE[nxt[2]]}).")
            loot = int(random.randint(*rng) * (0.8 + 0.12 * q))
            await conn.execute("UPDATE gang_op SET stage = 4, quality = ?, doers = ?, status = 'done' WHERE id = ?", (q, json.dumps(doers), op["id"]))
            real = [m["user_id"] for m in members if m["user_id"] > 0]
            tre = int(loot * 0.25)
            bonus = 200
            share = (loot - tre - bonus * len(set(doers))) // max(1, len(real))
            await conn.execute("UPDATE gang SET treasury = treasury + ?, last_act = ? WHERE id = ?", (tre, t, g["id"]))
            for u in real:
                await rp._load(conn, chat_id, u)
                pay = share + (bonus if u in doers else 0)
                await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (pay, chat_id, u))
                await G2._merit(conn, chat_id, u, 6 if u in doers else 3)
                if op["kind"] == "bank" and u in doers:
                    await conn.execute("UPDATE rp_player SET wanted = MIN(?, wanted + 2), wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (rp.MAX_WANTED, t, chat_id, u))
            await GP._kset(conn, f"p5opcd:{g['id']}", t + OP_CD)
            await rep_add(conn, g["id"], 4, 2)
            await G2.log_event(conn, chat_id, uid, "bigheist", loot)
            await _war_pts(conn, g["id"], 25)
            await rp._news(conn, chat_id, f"{icon} ОПГ «{g['name']}» провернула дело «{title}»: {loot} ₽")
            await gangwar.say(conn, chat_id, f"{icon} <b>«{esc(g['name'])}» провернула дело «{title}»!</b> Добыча {loot} ₽ (качество {q:.1f}): 25% в общак, остальное поровну, исполнителям премия.")
            return P3._res(f"{icon} Дело закончено! Добыча {loot} ₽, твоя доля {share + bonus} ₽.", True, share + bonus)
        # провал
        q = op["quality"] - 1.0
        p["wanted"], p["wanted_ts"] = min(rp.MAX_WANTED, p["wanted"] + 1), t
        await rp._save(conn, chat_id, uid, p)
        if random.random() < 0.25:
            await conn.execute("UPDATE gang_op SET status = 'failed' WHERE id = ?", (op["id"],))
            await GP._kset(conn, f"p5opcd:{g['id']}", t + OP_FAIL_CD)
            await rep_add(conn, g["id"], -3, 0)
            await gangwar.say(conn, chat_id, f"🚔 Дело «{title}» ОПГ «{esc(g['name'])}» сорвалось на этапе «{label}»: бригада рассыпалась.")
            return P3._res(f"💥 {label} провален, и всё дело сорвалось. Розыск +1⭐. Банде нужно {OP_FAIL_CD // 3600} ч, чтобы прийти в себя.", False)
        await conn.execute("UPDATE gang_op SET quality = ? WHERE id = ?", (q, op["id"]))
        return P3._res(f"⚠️ {label} не удался (шанс был ~{int(chance * 100)}%): розыск +1⭐, качество дела −1. Этап можно повторить.", False)
    return await P3._act(chat_id, uid, run)


SPECS_TITLE = {"scout": "разведчик", "driver": "водитель", "hacker": "взломщик", "fighter": "боец"}


async def _war_pts(conn, gid: int, pts: int) -> None:
    import gangplus4
    await gangplus4._war_add(conn, gid, pts)


# ═════════════════════ контрагентура ═════════════════════
SCAN_COST, SCAN_ENERGY, MOLE_LEAK_GAP, MOLE_LIFE, MOLE_DECIDE = 300, 20, 3 * 3600, 36 * 3600, 12 * 3600
FRAME_COST, FRAME_CD, FRAME_FAIL = 1500, 8 * 3600, 0.35
DISINFO_HOURS = 24


async def _mole(conn, gid: int, statuses=("active", "found")) -> Optional[dict]:
    q = ",".join("?" * len(statuses))
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(f"SELECT * FROM gang_mole WHERE gang_id = ? AND status IN ({q}) ORDER BY id DESC LIMIT 1", (gid, *statuses))
    r = await cur.fetchone()
    return dict(r) if r else None


async def counter_scan(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        if await P3.kget(conn, f"p5scancd:{g['id']}") > now():
            raise GameError("Контрразведка отдыхает: следующая проверка позже")
        if (g["treasury"] or 0) < SCAN_COST:
            raise GameError(f"Проверка стоит {SCAN_COST} ₽ из общака")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, SCAN_ENERGY)
        p["energy"] -= SCAN_ENERGY
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (SCAN_COST, g["id"]))
        await GP._kset(conn, f"p5scancd:{g['id']}", now() + 3600)
        cur = await conn.execute("SELECT COUNT(*) FROM gang_member WHERE gang_id = ? AND spec = 'scout'", (g["id"],))
        scouts = (await cur.fetchone())[0]
        chance = min(0.85, 0.35 + 0.10 * scouts)
        mole = await _mole(conn, g["id"], ("active",))
        if not mole:
            return P3._res("🕵 Проверка закончена: чужих агентов в банде нет (по крайней мере, пока).")
        if random.random() < chance:
            await conn.execute("UPDATE gang_mole SET status = 'found', found = ? WHERE id = ?", (now(), mole["id"]))
            ai = await _gx(conn, mole["ai_id"])
            return P3._res(f"🕵 Вскрыт чужой агент от «{ai['name'] if ai else '?'}»! В течение {MOLE_DECIDE // 3600} ч реши его судьбу: перевербовать или убрать.", True)
        return P3._res(f"🕵 Проверка ничего не нашла (шанс был ~{int(chance * 100)}%). Разведчики в банде повышают шанс.", False)
    return await P3._act(chat_id, uid, run)


async def mole_decide(chat_id: int, uid: int, name: str, mode: str) -> dict:
    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        mole = await _mole(conn, g["id"], ("found",))
        if not mole:
            raise GameError("Вскрытых агентов нет")
        ai = await _gx(conn, mole["ai_id"])
        if mode == "turn":
            await conn.execute("UPDATE gang_mole SET status = 'turned' WHERE id = ?", (mole["id"],))
            await GP._kset(conn, f"p5disinfo:{g['id']}", now() + DISINFO_HOURS * 3600)
            await rep_add(conn, g["id"], 3, 0)
            return P3._res(f"🎭 Агент перевербован: он кормит «{ai['name'] if ai else '?'}» дезинформацией. {DISINFO_HOURS} ч нападений от ИИ-банд на вас не будет.")
        if mode == "kill":
            await conn.execute("UPDATE gang_mole SET status = 'killed' WHERE id = ?", (mole["id"],))
            await rep_add(conn, g["id"], -3, 8)
            await gangwar.say(conn, chat_id, f"💀 В «{esc(g['name'])}» нашли и «убрали» чужого агента. Страх перед ними вырос.")
            return P3._res("💀 Агент убран. Страх банды +8, уважение −3.", True)
        raise GameError("Выбери: перевербовать или убрать")
    return await P3._act(chat_id, uid, run)


async def frame(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)

    async def run(conn):
        g, _ = await P3._need_boss(conn, chat_id, uid)
        victim = await _gx(conn, gid)
        if not victim or victim["chat_id"] != chat_id or victim["is_ai"] or gid == g["id"]:
            raise GameError("Подставить можно только другую банду игроков")
        if await P3.kget(conn, f"p5framecd:{g['id']}") > now():
            raise GameError("Подставу можно устраивать раз в 8 часов")
        if (g["treasury"] or 0) < FRAME_COST:
            raise GameError(f"Липовые улики стоят {FRAME_COST} ₽ из общака")
        if await P3._open_visit(conn, chat_id, 0, gid, "omon_gang") or await P3._open_visit(conn, chat_id, 0, g["id"], "omon_gang"):
            raise GameError("У одной из банд уже висит ОМОН: подожди")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (FRAME_COST, g["id"]))
        await GP._kset(conn, f"p5framecd:{g['id']}", now() + FRAME_CD)
        t = now()
        if random.random() < FRAME_FAIL:
            target, who = g, victim
            await rep_add(conn, g["id"], -10, 0)
            msg = f"🚨 Подстава раскрыта: ОМОН вместо жертвы поехал к «{esc(g['name'])}», а уважение к ним упало."
            res = P3._res("💥 Подстава раскрылась: ОМОН едет к вам, уважение −10.", False)
        else:
            target = victim
            await rep_add(conn, g["id"], 0, 3)
            msg = f"🚨 Анонимный донос: ОМОН выехал к «{esc(victim['name'])}». Кто стучал, неизвестно."
            res = P3._res(f"🚨 Донос сработал: ОМОН едет к «{victim['name']}». Страх +3. Ваше имя не раскрыто.")
        await conn.execute("INSERT INTO rp_visit (chat_id, user_id, gang_id, kind, amount, created, expires, status) VALUES (?, 0, ?, 'omon_gang', 0, ?, ?, 'open')",
                           (chat_id, target["id"], t, t + P3.OMON_WINDOW))
        await gangwar.say(conn, chat_id, msg)
        return res
    return await P3._act(chat_id, uid, run)


async def disinfo_active(conn, gid: int) -> bool:
    return await P3.kget(conn, f"p5disinfo:{gid}") > now()


async def _moles_tick(conn) -> None:
    t = now()
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_mole WHERE status IN ('active', 'found')")
    for m in [dict(r) for r in await cur.fetchall()]:
        g = await _gx(conn, m["gang_id"])
        if not g:
            continue
        if m["status"] == "found" and t - (m["found"] or 0) > MOLE_DECIDE:
            await conn.execute("UPDATE gang_mole SET status = 'gone' WHERE id = ?", (m["id"],))
            continue
        if m["status"] != "active":
            continue
        if t - m["planted"] > MOLE_LIFE:
            amt = min(1000, int((g["treasury"] or 0) * 0.05))
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (amt, g["id"]))
            await conn.execute("UPDATE gang_mole SET status = 'gone' WHERE id = ?", (m["id"],))
            await gangwar.say(conn, m["chat_id"], f"🕵 Из «{esc(g['name'])}» пропал кто-то, кого никто не замечал, вместе с {amt} ₽ из общака. Похоже, это был чужой агент.")
            continue
        if t - (m["last_leak"] or m["planted"]) >= MOLE_LEAK_GAP:
            amt = min(300, int((g["treasury"] or 0) * 0.02))
            await conn.execute("UPDATE gang SET treasury = MAX(0, treasury - ?) WHERE id = ?", (amt, g["id"]))
            await conn.execute("UPDATE gang_mole SET last_leak = ?, leaks = leaks + 1 WHERE id = ?", (t, m["id"]))
    cur = await conn.execute("SELECT DISTINCT chat_id FROM gang WHERE is_ai = 0")
    for (chat_id,) in await cur.fetchall():
        if not await P3._roll(conn, f"p5mole:{chat_id}", 20 * 3600):
            continue
        cur2 = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
        ais = [r[0] for r in await cur2.fetchall()]
        cur2 = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
        gangs = [r[0] for r in await cur2.fetchall() if not await _mole(conn, r[0]) and len(await gangwar._real_members(conn, r[0])) >= 2]
        if ais and gangs:
            await conn.execute("INSERT INTO gang_mole (gang_id, chat_id, ai_id, planted, status) VALUES (?, ?, ?, ?, 'active')", (random.choice(gangs), chat_id, random.choice(ais), t))


# ═════════════════════ интерфейс и фон ═════════════════════
async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    m = out.get("mine")
    if not g or not m or g.get("is_ai"):
        return
    t = now()
    m["charter"] = await charter.info(conn, g["id"])
    gx = await _gx(conn, g["id"])
    own = await _owners(conn, chat_id)
    cur_state = await conn.execute("SELECT key, control, police FROM territory WHERE chat_id = ?", (chat_id,))
    state = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in await cur_state.fetchall()}
    names = {x["id"]: x["name"] for x in out.get("gangs", [])}
    cur_look = await conn.execute("SELECT id, color1, emblem FROM gang WHERE chat_id = ?", (chat_id,))
    looks = {r[0]: (r[1] or "", r[2] or "") for r in await cur_look.fetchall()}
    mine_keys = [k for k, o in own.items() if o == g["id"]]
    tmap = []
    for key, tinfo in gangwar.TERR.items():
        owner = own.get(key, 0)
        fire = await P3.kget(conn, f"tfire:{chat_id}:{key}") > t
        block = await P3.kget(conn, f"tblock:{chat_id}:{key}") > t
        near_mine = any(n in mine_keys for n in NEIGH[key])
        ctrl, pol = state.get(key, (0, 0))
        tmap.append({"key": key, "icon": tinfo[1], "name": tinfo[2], "owner": owner, "owner_name": names.get(owner, ""), "mine": owner == g["id"],
                     "res": TERR_RES.get(key, ""), "perk": TERR_PERK.get(key, ("", ""))[1], "neigh": [gangwar.TERR[n][1] for n in sorted(NEIGH[key])], "neigh_keys": sorted(NEIGH[key]),
                     "fire": fire, "block": block, "owner_color": looks.get(owner, ("", ""))[0], "owner_icon": looks.get(owner, ("", ""))[1],
                     "control": ctrl, "police": pol, "route": bool(owner and any(own.get(n) == owner for n in NEIGH[key])),
                     "can_claim": owner == 0 and (not mine_keys or near_mine), "can_block": bool(owner and owner != g["id"] and near_mine),
                     "can_fortify": owner == g["id"]})
    st = await _stock(conn, g["id"])
    ais = [{"id": x["id"], "name": x["name"], "icon": x.get("icon", "🧓")} for x in out.get("gangs", []) if x.get("is_ai")]
    items = [{"key": k, "icon": v[0], "title": v[1], "qty": st.get(k, 0), "base": v[2], "product": k in PRODUCTS} for k, v in ITEMS.items()]
    prices = {str(b): {k: int(v[2] * price_index(b, k)) for k, v in ITEMS.items()} for b in [0] + [a["id"] for a in ais]}
    recipes = [{"key": k, "icon": PRODUCTS[k][0], "title": PRODUCTS[k][1], "need": [{"icon": RES[r][0], "title": RES[r][1], "n": n, "have": st.get(r, 0)} for r, n in need.items()],
                "can": all(st.get(r, 0) >= n for r, n in need.items())} for k, need in RECIPES.items()]
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_deal WHERE chat_id = ? AND status IN ('open', 'active') AND expires > ? AND (a = ? OR b = ?) ORDER BY id DESC LIMIT 8", (chat_id, t, g["id"], g["id"]))
    deals = []
    for d in [dict(r) for r in await cur.fetchall()]:
        if d["status"] == "active" and d["ends"] <= t:
            continue
        other = d["b"] if d["a"] == g["id"] else d["a"]
        deals.append({"id": d["id"], "kind": d["kind"], "status": d["status"], "other": names.get(other, "?"), "mine": d["a"] == g["id"], "price": d["price"],
                      "item": ITEMS[d["item"]][1] if d["item"] else "", "qty": d["qty"], "left": max(0, (d["ends"] if d["status"] == "active" else d["expires"]) - t)})
    truces = []
    for a in ais:
        if await truce_between(conn, g["id"], a["id"]):
            truces.append(a["id"])
    op = await _op_active(conn, g["id"])
    ops = {"list": [{"key": k, "icon": v[0], "title": v[1], "loot": list(v[2]), "need": v[3]} for k, v in OPS.items()], "cost": OP_COST,
           "cd": max(0, await P3.kget(conn, f"p5opcd:{g['id']}") - t), "active": None}
    if op and op["ends"] > t:
        icon, title, rng, need = OPS[op["kind"]]
        doers = json.loads(op["doers"] or "[]")
        ops["active"] = {"icon": icon, "title": title, "stage": op["stage"], "quality": op["quality"], "left": op["ends"] - t, "my_done": doers.count(uid),
                         "stages": [{"title": s[1], "spec": SPECS_TITLE[s[2]], "energy": s[3]} for s in STAGES]}
    mole = await _mole(conn, g["id"])
    leaks = mole["leaks"] if mole and mole.get("leaks") else 0
    ci = {"scan_wait": max(0, await P3.kget(conn, f"p5scancd:{g['id']}") - t), "scan_cost": SCAN_COST, "frame_cost": FRAME_COST,
          "frame_wait": max(0, await P3.kget(conn, f"p5framecd:{g['id']}") - t), "found": bool(mole and mole["status"] == "found"),
          "hint": bool(mole and mole["status"] == "active" and leaks >= 1), "disinfo": max(0, await P3.kget(conn, f"p5disinfo:{g['id']}") - t),
          "targets": [{"id": x["id"], "name": x["name"]} for x in out.get("gangs", []) if not x.get("is_ai") and not x.get("mine")]}
    import gangplus6
    m["p5"] = {"pressure": await gangplus6.pressure(conn, chat_id), "respect": gx.get("respect") or 0, "fear": gx.get("fear") or 0, "map": tmap, "items": items, "recipes": recipes, "prices": prices,
               "buyers": [{"id": 0, "name": "Рынок", "icon": "🏪"}] + ais, "cap": STOCK_CAP + 50 * await P3.proj_level(conn, g["id"], "depot"),
               "deals": deals, "truces": truces, "truce_price": int(TRUCE_AI_BASE + (g["treasury"] or 0) * 0.10), "ops": ops, "ci": ci,
               "claim_cost": await charter.cost(conn, g["id"], CLAIM_COST), "block_cost": await charter.cost(conn, g["id"], BLOCK_COST // 2 if own.get("tire") == g["id"] else BLOCK_COST), "fort_gain": int(FORTIFY_GAIN * 1.5) if own.get("garages") == g["id"] else FORTIFY_GAIN, "claim_wait": max(0, await P3.kget(conn, f"p5claim:{g['id']}") - t),
               "block_wait": max(0, await P3.kget(conn, f"p5blockcd:{g['id']}") - t),
               "fort_cost": await charter.cost(conn, g["id"], FORTIFY_COST), "fort_wait": max(0, await P3.kget(conn, f"p5fort:{g['id']}") - t),
               "others": [{"id": x["id"], "name": x["name"]} for x in out.get("gangs", []) if not x.get("is_ai") and not x.get("mine")]}


async def tick(conn) -> None:
    try:
        t = now()
        await control_tick(conn)
        await _daily(conn)
        await _stock_tick(conn)
        await _moles_tick(conn)
        cur = await conn.execute("SELECT id, gang_id, chat_id FROM gang_op WHERE status = 'active' AND ends <= ?", (t,))
        for oid, gid, chat_id in await cur.fetchall():
            await conn.execute("UPDATE gang_op SET status = 'failed' WHERE id = ?", (oid,))
            await GP._kset(conn, f"p5opcd:{gid}", t + OP_FAIL_CD)
            g = await _gx(conn, gid)
            if g:
                await gangwar.say(conn, chat_id, f"⏳ Большое дело ОПГ «{esc(g['name'])}» сорвалось: не успели в срок.")
        await conn.execute("UPDATE gang_deal SET status = 'expired' WHERE status = 'open' AND expires <= ?", (t,))
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 5")
