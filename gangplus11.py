"""ОПГ-плюс 11: заказ избиения (нанять другую банду), тайник, премии и штрафы в банде, крыша для одиночки, экономика (расходы и налог на богатство)."""
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
import gangplus8 as P8
import gangwar
import police as PO
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc, now_local

log = logging.getLogger(__name__)


def now() -> int:
    return int(time.time())


# ═════════════════════ заказ избиения ═════════════════════
ORDER_MIN, ORDER_MAX, ORDER_CD = 1500, 4000, 30 * 60
ORDER_BEAT, ORDER_CASH_PCT, ORDER_CASH_CAP = 30, 0.05, 800


ORDER_KINDS = {
    "beat": ("🥊", "Избить", 0.65, "30 минут лежит и теряет 5% наличных (до 800 ₽)"),
    "rob": ("💰", "Обчистить", 0.55, "уносят 10% наличных (до 2000 ₽), половина — тебе; тайник не достанут"),
    "dirt": ("🗂", "Компромат", 0.70, "жертве +2⭐ розыска, её банде дело +6, а чиновнику или полицейскому — материал детективу"),
}


def order_odds(price: int) -> tuple:
    """Цена заказа решает: щедрая оплата повышает шанс и снижает риск, что полиция выйдет на заказчика."""
    price = max(ORDER_MIN, min(ORDER_MAX, int(price or ORDER_MIN)))
    bonus = (price - 2500) / 1000 * 0.1
    notice = 0.35 - (price - ORDER_MIN) / (ORDER_MAX - ORDER_MIN) * 0.2
    return bonus, notice


async def _perform(conn, o: dict, hired: dict) -> str:
    """Исполнение заказа: вид задаёт последствия, цена и охрана цели — шанс. Провал может выдать заказчика."""
    chat_id = o["chat_id"]
    kind = o.get("kind") if o.get("kind") in ORDER_KINDS else "beat"
    tp = await rp._load(conn, chat_id, o["target"])
    tn = await P3._uname(conn, chat_id, o["target"])
    pn = await P3._uname(conn, chat_id, o["payer"])
    guard_gang = 0.0
    tg = await PO._own_gang(conn, chat_id, o["target"])
    if tg:
        guard_gang = 0.07 * await P3.proj_level(conn, tg, "guard")
    bonus, notice = order_odds(o.get("price"))
    chance = max(0.2, min(0.9, ORDER_KINDS[kind][2] + bonus - 0.06 * (tp.get("weapon") or 0) - 0.04 * (tp.get("guard") or 0) - guard_gang))
    if random.random() < chance:
        if kind == "beat":
            loss = min(ORDER_CASH_CAP, int(tp["money"] * ORDER_CASH_PCT))
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (loss, chat_id, o["target"]))
            await G2._beat(conn, chat_id, o["target"], ORDER_BEAT)
            await rp._news(conn, chat_id, f"🥊 {tn} избит неизвестными: {ORDER_BEAT} минут отлёживается, пропало {loss} ₽")
            res = f"🥊 Заказ выполнен: {tn} избит (−{loss} ₽ у жертвы, {ORDER_BEAT} мин)."
        elif kind == "rob":
            loss = min(2000, int(max(0, tp["money"]) * 0.10))
            half = loss // 2
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (loss, chat_id, o["target"]))
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (half, chat_id, o["payer"]))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (loss - half, hired["id"]))
            await rp._news(conn, chat_id, f"💰 {tn} обчистили неизвестные: пропало {loss} ₽")
            res = f"💰 Заказ выполнен: у {tn} забрали {loss} ₽, твоя доля {half} ₽."
        else:
            await conn.execute("UPDATE rp_player SET wanted = MIN(?, wanted + 2), wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (rp.MAX_WANTED, now(), chat_id, o["target"]))
            extra = ""
            if tg:
                await PO.case_add(conn, chat_id, tg, 6)
                extra = ", дело на его банду +6"
            try:
                import justice
                import power
                m, d = await power._mayor(conn, chat_id), await power._god(conn, chat_id)
                role = "mayor" if m and int(m.get("uid", 0)) == o["target"] else "godfather" if d and int(d.get("uid", 0)) == o["target"] else "police" if await PO._role(conn, chat_id, o["target"]) else ""
                if role:
                    await justice.report(conn, chat_id, o["target"], role, "анонимный компромат", 3)
                    extra += ", материал ушёл детективу"
            except Exception:
                log.exception("компромат: детектив")
            await rp._news(conn, chat_id, f"🗂 На {tn} всплыл компромат: розыск +2⭐")
            res = f"🗂 Заказ выполнен: {tn} получил +2⭐ розыска{extra}."
        await P5.rep_add(conn, hired["id"], 0, 2)
        if random.random() < notice:  # полиция вышла на заказчика
            from police2 import alert
            payer_g = o["payer_gang"]
            await alert(conn, chat_id, payer_g or hired["id"], "order", f"Заказ «{ORDER_KINDS[kind][1].lower()}» на жителя: заказчик ищется")
            if payer_g:
                await PO.case_add(conn, chat_id, payer_g, 6)
            else:
                await conn.execute("UPDATE rp_player SET wanted = MIN(?, wanted + 1), wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (rp.MAX_WANTED, now(), chat_id, o["payer"]))
            res += " Полиция что-то заподозрила: " + ("дело на банду +6." if payer_g else "тебе +1⭐ розыска.")
        return res
    if random.random() < 0.3:  # исполнитель проболтался
        await rp._news(conn, chat_id, f"🛡 {tn} отбился от нападавших, а один из них проболтался: заказ оплатил {pn}")
        return f"🛡 {tn} отбился, и исполнитель назвал твоё имя: теперь он знает, кто заказчик. Деньги не вернут."
    await rp._news(conn, chat_id, f"🛡 {tn} отбился от неизвестных нападавших")
    return f"🛡 {tn} отбился: заказ провален, деньги исполнителям уже ушли."


async def _order_schema(conn) -> None:
    try:
        await conn.execute("ALTER TABLE hit_order ADD COLUMN kind TEXT NOT NULL DEFAULT 'beat'")
    except Exception:
        pass


async def order_create(chat_id: int, uid: int, name: str, target, hired, price, kind="beat") -> dict:
    target, hired, price = int(target), int(hired), int(price or 0)
    kind = kind if kind in ORDER_KINDS else "beat"

    async def run(conn):
        if not ORDER_MIN <= price <= ORDER_MAX:
            raise GameError(f"Цена заказа от {ORDER_MIN} до {ORDER_MAX} ₽")
        if target == uid:
            raise GameError("Себя заказывать не нужно")
        tp = await rp._load(conn, chat_id, target)
        if kind == "beat" and tp.get("beaten_until", 0) > now():
            raise GameError("Он и так лежит побитый")
        if kind == "rob" and int(tp.get("money") or 0) < 500:
            raise GameError("У него в карманах пусто: обчищать нечего (деньги в тайнике не достать)")
        await _order_schema(conn)
        hg = await gangwar._gang_row(conn, hired)
        if not hg or hg["chat_id"] != chat_id:
            raise GameError("Такой банды нет")
        my, members = await G._mine(conn, chat_id, uid)
        payer_gang = 0
        if my and not my.get("is_ai"):
            payer_gang = my["id"]
            if my["id"] == hired:
                raise GameError("Свою банду нанимать нельзя")
            if next(m["role"] for m in members if m["user_id"] == uid) == "grunt":
                raise GameError("Заказы делает босс или зам (из общака)")
            if (my["treasury"] or 0) < price:
                raise GameError("В общаке нет такой суммы")
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (price, my["id"]))
        else:
            p = await rp._load(conn, chat_id, uid)
            if p["money"] < price:
                raise GameError("У тебя нет такой суммы")
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (price, chat_id, uid))
        if await P3.kget(conn, f"p13order:{chat_id}:{uid}") > now():
            raise GameError("Заказывать можно раз в час")
        if await PO._own_gang(conn, chat_id, target) == hired:
            raise GameError("Банда не побьёт своего")
        await GP._kset(conn, f"p13order:{chat_id}:{uid}", now() + ORDER_CD)
        o = {"chat_id": chat_id, "payer": uid, "payer_gang": payer_gang, "target": target, "price": price, "kind": kind}
        if hg["is_ai"]:
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (price, hired))
            text = await _perform(conn, o, hg)
            return P3._res(text, True)
        await conn.execute("INSERT INTO hit_order (chat_id, payer, payer_gang, target, hired_gang, price, status, created, expires, kind) VALUES (?, ?, ?, ?, ?, ?, 'offer', ?, ?, ?)",
                           (chat_id, uid, payer_gang, target, hired, price, now(), now() + 6 * 3600, kind))
        return P3._res(f"🥊 Заказ отправлен боссу «{hg['name']}» ({price} ₽ заморожены). Он согласится или откажется в течение 6 часов.")
    return await P3._act(chat_id, uid, run)


async def order_answer(chat_id: int, uid: int, name: str, oid, accept=True) -> dict:
    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Отвечает босс")
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM hit_order WHERE id = ? AND hired_gang = ? AND status = 'offer'", (int(oid), g["id"]))
        r = await cur.fetchone()
        if not r or r["expires"] < now():
            raise GameError("Заказ неактуален")
        o = dict(r)
        await _refund_or_pay(conn, o, bool(accept), g)
        if not accept:
            await conn.execute("UPDATE hit_order SET status = 'declined' WHERE id = ?", (o["id"],))
            return P3._res("Заказ отклонён, деньги вернулись заказчику.", False)
        await conn.execute("UPDATE hit_order SET status = 'done' WHERE id = ?", (o["id"],))
        text = await _perform(conn, o, g)
        return P3._res(f"Заказ принят: +{o['price']} ₽ в общак. {text}", True, 0)
    return await P3._act(chat_id, uid, run)


async def _refund_or_pay(conn, o: dict, accepted: bool, g: dict) -> None:
    if accepted:
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (o["price"], g["id"]))
    elif o["payer_gang"]:
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (o["price"], o["payer_gang"]))
    else:
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (o["price"], o["chat_id"], o["payer"]))


# ═════════════════════ тайник ═════════════════════
STASH_CAP = 50000


async def stash_get(conn, chat_id: int, uid: int) -> int:
    cur = await conn.execute("SELECT amount FROM stash WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return r[0] if r else 0


async def stash_put(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount or 0)

    async def run(conn):
        p = await rp._load(conn, chat_id, uid)
        cur_amt = await stash_get(conn, chat_id, uid)
        amount_c = min(amount, STASH_CAP - cur_amt)
        if amount_c < 1 or p["money"] < amount_c:
            raise GameError("Тайник полон или у тебя нет таких денег")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (amount_c, chat_id, uid))
        await conn.execute("INSERT INTO stash (chat_id, user_id, amount) VALUES (?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET amount = amount + ?", (chat_id, uid, amount_c, amount_c))
        return P3._res(f"🗄 В тайник: {amount_c} ₽. Налётчики и шантажисты их не достанут (кроме обыска по ордеру).", True, -amount_c)
    return await P3._act(chat_id, uid, run)


async def stash_take(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount or 0)

    async def run(conn):
        have = await stash_get(conn, chat_id, uid)
        amt = min(amount, have)
        if amt < 1:
            raise GameError("В тайнике пусто")
        await conn.execute("UPDATE stash SET amount = amount - ? WHERE chat_id = ? AND user_id = ?", (amt, chat_id, uid))
        await rp._load(conn, chat_id, uid)
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (amt, chat_id, uid))
        return P3._res(f"🗄 Из тайника: +{amt} ₽.", True, amt)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ премии и штрафы ═════════════════════
async def bonus(chat_id: int, uid: int, name: str, target, amount) -> dict:
    target, amount = int(target), int(amount or 0)

    async def run(conn):
        g, members, role = await P8._need_dep(conn, chat_id, uid)
        if not any(m["user_id"] == target for m in members) or target == uid:
            raise GameError("Премию получает другой участник банды")
        if not 50 <= amount <= 1000 or (g["treasury"] or 0) < amount:
            raise GameError("Премия от 50 до 1000 ₽ из общака")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (amount, g["id"]))
        await rp._load(conn, chat_id, target)
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, target))
        await G2._merit(conn, chat_id, target, 2)
        return P3._res(f"🎁 {await P3._uname(conn, chat_id, target)} получил премию {amount} ₽ (+2 авторитета).")
    return await P3._act(chat_id, uid, run)


async def fine_member(chat_id: int, uid: int, name: str, target, amount) -> dict:
    target, amount = int(target), int(amount or 0)

    async def run(conn):
        g, members, role = await P8._need_dep(conn, chat_id, uid)
        t = next((m for m in members if m["user_id"] == target), None)
        if not t or target == uid or t["role"] == "boss":
            raise GameError("Штрафовать можно только рядовых участников")
        if not 50 <= amount <= 500:
            raise GameError("Штраф от 50 до 500 ₽")
        if await P3.kget(conn, f"p13fine:{target}") > now():
            raise GameError("Этого участника уже штрафовали сегодня")
        p = await rp._load(conn, chat_id, target)
        fine = min(amount, p["money"])
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, chat_id, target))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (fine, g["id"]))
        await G2._merit(conn, chat_id, target, -3)
        await GP._kset(conn, f"p13fine:{target}", now() + 86400)
        return P3._res(f"💢 Штраф {fine} ₽ в общак (участнику −3 авторитета).")
    return await P3._act(chat_id, uid, run)


# ═════════════════════ крыша для одиночки ═════════════════════
PROT_DAYS, PROT_MIN, PROT_MAX = 7, 300, 1500


async def protect_offer(chat_id: int, uid: int, name: str, target, price) -> dict:
    target, price = int(target), int(price or 0)

    async def run(conn):
        g, _, role = await P8._need_dep(conn, chat_id, uid)
        if role != "boss":
            raise GameError("Покровительство предлагает босс")
        if not PROT_MIN <= price <= PROT_MAX:
            raise GameError(f"Цена от {PROT_MIN} до {PROT_MAX} ₽ за неделю")
        if await PO._own_gang(conn, chat_id, target):
            raise GameError("Нужен одиночка без банды")
        cur = await conn.execute("SELECT 1 FROM protection WHERE chat_id = ? AND user_id = ? AND status = 'open'", (chat_id, target))
        if await cur.fetchone():
            raise GameError("Ему уже сделали предложение")
        await conn.execute("INSERT INTO protection (chat_id, user_id, gang_id, price, status, until, expires) VALUES (?, ?, ?, ?, 'open', 0, ?)", (chat_id, target, g["id"], price, now() + 24 * 3600))
        return P3._res(f"🤝 Предложение покровительства отправлено: {price} ₽ в неделю.")
    return await P3._act(chat_id, uid, run)


async def protect_answer(chat_id: int, uid: int, name: str, pid, accept=True) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM protection WHERE id = ? AND chat_id = ? AND user_id = ? AND status = 'open'", (int(pid), chat_id, uid))
        r = await cur.fetchone()
        if not r or r["expires"] < now():
            raise GameError("Предложение неактуально")
        pr = dict(r)
        if not accept:
            await conn.execute("UPDATE protection SET status = 'declined' WHERE id = ?", (pr["id"],))
            return P3._res("Ты отказался от покровительства.", False)
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < pr["price"]:
            raise GameError(f"Нужно {pr['price']} ₽")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (pr["price"], chat_id, uid))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (pr["price"], pr["gang_id"]))
        await conn.execute("UPDATE protection SET status = 'active', until = ? WHERE id = ?", (now() + PROT_DAYS * 86400, pr["id"]))
        return P3._res(f"🤝 Банда взяла тебя под крышу на {PROT_DAYS} дней: ИИ-банды тебя не трогают.", True, -pr["price"])
    return await P3._act(chat_id, uid, run)


async def protected(conn, chat_id: int, uid: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM protection WHERE chat_id = ? AND user_id = ? AND status = 'active' AND until > ?", (chat_id, uid, now()))
    return bool(await cur.fetchone())


# ═════════════════════ экономика: расходы и налог на богатство ═════════════════════
UPKEEP_HOME = [0, 25, 70, 160, 350]
UPKEEP_CAR = [0, 15, 35, 90, 220]
UPKEEP_STALL = [0, 20, 50, 110, 240, 480]
WEALTH_FROM, WEALTH_PCT = 20000, 0.015


def upkeep(p: dict) -> int:
    return UPKEEP_HOME[min(p["home"], 4)] + UPKEEP_CAR[min(p["car"], 4)] + UPKEEP_STALL[min(p["stall"], 5)]


WEALTH_BRACKETS = [(20000, 60000, 0.015), (60000, 100000, 0.04), (100000, None, 0.06)]


def wealth_tax(money: int) -> int:
    tax = 0.0
    for lo, hi, pct in WEALTH_BRACKETS:
        top = money if hi is None else min(money, hi)
        tax += max(0, top - lo) * pct
    return int(tax)


async def _econ_tick(conn) -> None:
    day = now_local().strftime("%Y-%m-%d")
    cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
    for (chat_id,) in await cur.fetchall():
        if await gangwar.kget(conn, f"p13econ:{chat_id}") == day:
            continue
        await gangwar.kset(conn, f"p13econ:{chat_id}", day)
        conn.row_factory = aiosqlite.Row
        cur2 = await conn.execute("SELECT * FROM rp_player WHERE chat_id = ? AND user_id > 0", (chat_id,))
        total = 0
        for row in [dict(r) for r in await cur2.fetchall()]:
            cost = upkeep(row) + wealth_tax(row["money"])
            cost = min(cost, row["money"])
            if cost > 0:
                await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cost, chat_id, row["user_id"]))
                total += cost
        if total:
            import balance
            await balance.flow(conn, chat_id, "upkeep_wealth", total)
            await balance.welfare_add(conn, chat_id, total // 10)
            await PO.fund_add(conn, chat_id, total // 4)
            await rp._news(conn, chat_id, f"🧾 Район заплатил за жильё, машины, ларьки и налог на богатство: {total} ₽ (четверть ушла в фонд полиции)")


async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    t = now()
    pc = out.setdefault("pc", {})
    p = await rp._load(conn, chat_id, uid)
    pc["econ"] = {"upkeep": upkeep(p), "wealth": wealth_tax(p["money"]), "wealth_from": WEALTH_FROM, "stash": await stash_get(conn, chat_id, uid), "stash_cap": STASH_CAP,
                  "protected": await protected(conn, chat_id, uid)}
    cur = await conn.execute("SELECT id, gang_id, price, expires FROM protection WHERE chat_id = ? AND user_id = ? AND status = 'open' AND expires > ?", (chat_id, uid, t))
    r = await cur.fetchone()
    pc["prot_offer"] = None
    if r:
        gg = await gangwar._gang_row(conn, r[1])
        pc["prot_offer"] = {"id": r[0], "gang": gg["name"] if gg else "?", "price": r[2], "left": r[3] - t}
    cur = await conn.execute("SELECT id, payer_gang, hired_gang, target, price, expires, kind FROM hit_order WHERE chat_id = ? AND status = 'offer' AND expires > ?", (chat_id, t))
    gids = await conn.execute("SELECT id, name, is_ai FROM gang WHERE chat_id = ? AND is_ai < 2", (chat_id,))
    gname = {x[0]: (x[1], x[2]) for x in await gids.fetchall()}
    orders = []
    for oid, pg, hg, tgt, price, ex, okind in await cur.fetchall():
        if g and hg == g["id"] and my_role == "boss":
            orders.append({"id": oid, "kind": ORDER_KINDS.get(okind, ORDER_KINDS["beat"])[1].lower(), "price": price, "target": await P3._uname(conn, chat_id, tgt), "left": ex - t})
    pc["orders_in"] = orders
    cur = await conn.execute("SELECT user_id, username FROM users WHERE chat_id = ? AND user_id > 0 AND user_id IN (SELECT user_id FROM rp_player WHERE chat_id = ?)", (chat_id, chat_id))
    pc["order_targets"] = [{"id": r[0], "name": r[1]} for r in await cur.fetchall() if r[0] != uid]
    pc["order_gangs"] = [{"id": k, "name": v[0], "ai": bool(v[1])} for k, v in gname.items() if not (g and k == g["id"])]
    pc["order_range"] = [ORDER_MIN, ORDER_MAX]
    pc["order_kinds"] = [{"key": k, "icon": v[0], "title": v[1], "chance": int(v[2] * 100), "desc": v[3]} for k, v in ORDER_KINDS.items()]
    pc["order_tiers"] = [{"price": pr, "title": tt, "bonus": int(round(order_odds(pr)[0] * 100)), "notice": int(round(order_odds(pr)[1] * 100))}
                         for pr, tt in ((ORDER_MIN, "Дёшево"), (2500, "Нормально"), (ORDER_MAX, "Щедро"))]
    pc["order_wait"] = max(0, await P3.kget(conn, f"p13order:{chat_id}:{uid}") - t)
    m = out.get("mine")
    if g and m and not g.get("is_ai"):
        cur = await conn.execute("SELECT user_id, username FROM users WHERE chat_id = ? AND user_id IN (SELECT user_id FROM rp_player WHERE chat_id = ?) AND user_id NOT IN (SELECT user_id FROM gang_member WHERE chat_id = ?)", (chat_id, chat_id, chat_id))
        m.setdefault("p13", {})["loners"] = [{"id": r[0], "name": r[1]} for r in await cur.fetchall()]


async def tick(conn) -> None:
    try:
        await _econ_tick(conn)
        t = now()
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM hit_order WHERE status = 'offer' AND expires <= ?", (t,))
        for o in [dict(r) for r in await cur.fetchall()]:
            g = await gangwar._gang_row(conn, o["hired_gang"])
            await _refund_or_pay(conn, o, False, g or {"id": 0})
            await conn.execute("UPDATE hit_order SET status = 'expired' WHERE id = ?", (o["id"],))
        await conn.execute("UPDATE protection SET status = 'expired' WHERE status = 'open' AND expires <= ?", (t,))
        await conn.execute("UPDATE protection SET status = 'done' WHERE status = 'active' AND until <= ?", (t,))
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 11")
