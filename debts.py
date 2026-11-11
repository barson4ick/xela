"""Долговая книга: заём берёт сам должник, а не навязывает босс.

Таблица debt (общая с gangplus9). Статусы: offer (банда игроков предложила, ждёт согласия), open, late, paid, written, expired, declined.
У ИИ-банд заём берётся сразу; банда игроков делает предложение, должник соглашается или отказывается.
Срок выбирает должник: длиннее — дороже. Просрочка: коллекторы забирают наличные, потом тайник; безнадёжный долг закрывает кредит на неделю.
"""
import logging
import time

import aiosqlite

import gangwar
import rp
from rooms import GameError

log = logging.getLogger(__name__)

TERMS = {24: 0.15, 48: 0.25, 72: 0.40}
AMOUNT_MIN, AMOUNT_MAX = 200, 3000
RESERVE = 500                # банда не отдаёт последнее
OFFER_TTL = 12 * 3600
LATE_MULT = 1.15
SEIZE_GAP = 12 * 3600
WRITE_OFF = 3 * 86400
BLACKLIST = 7 * 86400


def now() -> int:
    return int(time.time())


async def _blocked(conn, chat_id: int, uid: int) -> int:
    return max(0, int(await gangwar.kget(conn, f"debtbl:{chat_id}:{uid}") or 0) - now())


async def _has_debt(conn, chat_id: int, uid: int) -> bool:
    cur = await conn.execute("SELECT 1 FROM debt WHERE chat_id = ? AND debtor = ? AND status IN ('open', 'late')", (chat_id, uid))
    return bool(await cur.fetchone())


def _check(amount: int, hours: int) -> None:
    if not AMOUNT_MIN <= amount <= AMOUNT_MAX:
        raise GameError(f"Заём от {AMOUNT_MIN} до {AMOUNT_MAX} ₽")
    if hours not in TERMS:
        raise GameError("Срок: 24, 48 или 72 часа")


async def _open(conn, chat_id: int, gid: int, uid: int, amount: int, hours: int, did: int = 0) -> int:
    owed = int(amount * (1 + TERMS[hours]))
    await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (amount, gid))
    await rp._load(conn, chat_id, uid)
    await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, uid))
    if did:
        await conn.execute("UPDATE debt SET status = 'open', owed = ?, due = ?, created = ?, fined = 0 WHERE id = ?", (owed, now() + hours * 3600, now(), did))
    else:
        await conn.execute("INSERT INTO debt (chat_id, gang_id, debtor, principal, owed, due, status, created) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                           (chat_id, gid, uid, amount, owed, now() + hours * 3600, now()))
    return owed


async def take(chat_id: int, uid: int, name: str, gid, amount, hours=48) -> dict:
    """Занять у ИИ-банды: деньги сразу, срок выбирает должник."""
    import gangplus3 as P3
    gid, amount, hours = int(gid or 0), int(amount or 0), int(hours or 48)

    async def run(conn):
        _check(amount, hours)
        left = await _blocked(conn, chat_id, uid)
        if left:
            raise GameError(f"Тебе не дают в долг после списанного займа: ещё {left // 3600 + 1} ч")
        if await _has_debt(conn, chat_id, uid):
            raise GameError("Сначала верни прошлый долг")
        g = await gangwar._gang_row(conn, gid)
        if not g or g["chat_id"] != chat_id or g["is_ai"] != 1:
            raise GameError("Сразу дают в долг только ИИ-банды. Банда игроков должна сама предложить заём")
        if int(g["treasury"] or 0) < amount + RESERVE:
            raise GameError(f"У «{g['name']}» нет столько свободных денег")
        owed = await _open(conn, chat_id, gid, uid, amount, hours)
        await rp._news(conn, chat_id, f"📒 {name} занял {amount} ₽ у «{g['name']}» на {hours} ч")
        return P3._res(f"📒 Заём {amount} ₽ у «{g['name']}». Вернуть {owed} ₽ за {hours} ч. Просрочишь — коллекторы заберут наличные и тайник.", True, amount)
    return await P3._act(chat_id, uid, run)


async def offer(chat_id: int, uid: int, name: str, target, amount, hours=48) -> dict:
    """Босс или зам предлагает заём из общака. Деньги уйдут, только когда должник согласится."""
    import gangplus3 as P3
    import gangplus8 as P8
    target, amount, hours = int(target or 0), int(amount or 0), int(hours or 48)

    async def run(conn):
        g, _m, _r = await P8._need_dep(conn, chat_id, uid)
        _check(amount, hours)
        if int(g["treasury"] or 0) < amount:
            raise GameError("В общаке нет столько")
        cur = await conn.execute("SELECT 1 FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        if not await cur.fetchone() or target == uid or target <= 0:
            raise GameError("Выбери другого игрока")
        if await _has_debt(conn, chat_id, target):
            raise GameError("У него уже есть долг")
        cur = await conn.execute("SELECT 1 FROM debt WHERE chat_id = ? AND debtor = ? AND gang_id = ? AND status = 'offer' AND created > ?", (chat_id, target, g["id"], now() - OFFER_TTL))
        if await cur.fetchone():
            raise GameError("Ты уже предложил ему заём: жди ответа")
        owed = int(amount * (1 + TERMS[hours]))
        # для предложения в поле due лежит срок в часах; настоящий срок ставится при согласии
        await conn.execute("INSERT INTO debt (chat_id, gang_id, debtor, principal, owed, due, status, created) VALUES (?, ?, ?, ?, ?, ?, 'offer', ?)",
                           (chat_id, g["id"], target, amount, owed, hours, now()))
        tn = await P3._uname(conn, chat_id, target)
        return P3._res(f"📒 Предложение отправлено: {tn} может взять {amount} ₽ и вернуть {owed} ₽ за {hours} ч. Деньги уйдут из общака, когда он согласится.")
    return await P3._act(chat_id, uid, run)


async def answer(chat_id: int, uid: int, name: str, did, accept=True) -> dict:
    import gangplus3 as P3
    accept = accept in (True, 1, "1", "true", "True")

    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM debt WHERE id = ? AND chat_id = ? AND debtor = ? AND status = 'offer'", (int(did or 0), chat_id, uid))
        r = await cur.fetchone()
        conn.row_factory = None
        if not r or r["created"] + OFFER_TTL < now():
            raise GameError("Предложение уже неактуально")
        d = dict(r)
        g = await gangwar._gang_row(conn, d["gang_id"])
        if not accept:
            await conn.execute("UPDATE debt SET status = 'declined' WHERE id = ?", (d["id"],))
            return P3._res("📒 Ты отказался от займа.", True, 0)
        if await _has_debt(conn, chat_id, uid):
            raise GameError("Сначала верни прошлый долг")
        if not g or int(g["treasury"] or 0) < d["principal"]:
            raise GameError("В общаке банды уже нет этих денег")
        hours = int(d["due"]) if int(d["due"]) in TERMS else 48
        owed = await _open(conn, chat_id, d["gang_id"], uid, d["principal"], hours, d["id"])
        await rp._news(conn, chat_id, f"📒 {name} занял {d['principal']} ₽ у «{g['name']}» на {hours} ч")
        return P3._res(f"📒 Заём {d['principal']} ₽ получен. Вернуть {owed} ₽ за {hours} ч.", True, d["principal"])
    return await P3._act(chat_id, uid, run)


async def repay(chat_id: int, uid: int, name: str, did, amount=0) -> dict:
    """Вернуть долг целиком или частью (сколько есть)."""
    import gangplus3 as P3
    import gangplus5 as P5
    amount = int(amount or 0)

    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM debt WHERE id = ? AND chat_id = ? AND debtor = ? AND status IN ('open', 'late')", (int(did or 0), chat_id, uid))
        r = await cur.fetchone()
        conn.row_factory = None
        if not r:
            raise GameError("Долга нет")
        d = dict(r)
        p = await rp._load(conn, chat_id, uid)
        pay = min(int(p["money"]), d["owed"], amount if amount > 0 else d["owed"])
        if pay <= 0:
            raise GameError("Платить нечем")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (pay, chat_id, uid))
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (pay, d["gang_id"]))
        rest = d["owed"] - pay
        if rest <= 0:
            await conn.execute("UPDATE debt SET status = 'paid', owed = 0 WHERE id = ?", (d["id"],))
            await P5.rep_add(conn, d["gang_id"], 1, 0)
            return P3._res(f"📒 Долг закрыт: −{pay} ₽. Книга чиста.", True, -pay)
        await conn.execute("UPDATE debt SET owed = ? WHERE id = ?", (rest, d["id"]))
        return P3._res(f"📒 Внесено {pay} ₽. Осталось вернуть {rest} ₽.", True, -pay)
    return await P3._act(chat_id, uid, run)


async def _seize(conn, d: dict, with_stash: bool) -> int:
    """Коллекторы забирают наличные (и тайник) в счёт долга. Возвращает остаток долга."""
    chat_id, uid, owed = d["chat_id"], d["debtor"], int(d["owed"])
    row = await (await conn.execute("SELECT money FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))).fetchone()
    cash = min(owed, max(0, int(row[0]) if row else 0))
    if cash:
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cash, chat_id, uid))
    owed -= cash
    took = cash
    if owed > 0 and with_stash:
        row = await (await conn.execute("SELECT amount FROM stash WHERE chat_id = ? AND user_id = ?", (chat_id, uid))).fetchone()
        st = min(owed, max(0, int(row[0]) if row else 0))
        if st:
            await conn.execute("UPDATE stash SET amount = amount - ? WHERE chat_id = ? AND user_id = ?", (st, chat_id, uid))
            owed -= st
            took += st
    if took:
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (took, d["gang_id"]))
    return owed


async def tick(conn) -> None:
    import gangplus2 as G2
    import gangplus3 as P3
    import gangplus5 as P5
    t = now()
    await conn.execute("UPDATE debt SET status = 'expired' WHERE status = 'offer' AND created < ?", (t - OFFER_TTL,))
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM debt WHERE status IN ('open', 'late') AND due <= ?", (t,))
    rows = [dict(r) for r in await cur.fetchall()]
    conn.row_factory = None
    for d in rows:
        g = await gangwar._gang_row(conn, d["gang_id"])
        gname = g["name"] if g else "банда"
        name = await P3._uname(conn, d["chat_id"], d["debtor"])
        if d["status"] == "open":
            rest = await _seize(conn, d, False)
            if rest <= 0:
                await conn.execute("UPDATE debt SET status = 'paid', owed = 0 WHERE id = ?", (d["id"],))
                await rp._news(conn, d["chat_id"], f"📒 Коллекторы «{gname}» забрали у {name} просроченный долг целиком")
                continue
            rest = int(rest * LATE_MULT)
            await conn.execute("UPDATE debt SET status = 'late', owed = ?, fined = ? WHERE id = ?", (rest, t, d["id"]))
            await G2._beat(conn, d["chat_id"], d["debtor"], 20)
            await G2._merit(conn, d["chat_id"], d["debtor"], -5)
            await rp._news(conn, d["chat_id"], f"🗣 К {name} пришли коллекторы «{gname}»: забрали наличные, долг вырос до {rest} ₽, 20 минут отлёживаться")
        else:
            rest = int(d["owed"])
            if t - int(d.get("fined") or 0) >= SEIZE_GAP:
                # коллекторы приходят снова: теперь забирают и тайник
                rest = await _seize(conn, d, True)
                if rest <= 0:
                    await conn.execute("UPDATE debt SET status = 'paid', owed = 0 WHERE id = ?", (d["id"],))
                    await rp._news(conn, d["chat_id"], f"📒 Коллекторы «{gname}» вытрясли из {name} весь долг, включая тайник")
                    continue
                await conn.execute("UPDATE debt SET owed = ?, fined = ? WHERE id = ?", (rest, t, d["id"]))
            if t - d["due"] > WRITE_OFF:
                await conn.execute("UPDATE debt SET status = 'written' WHERE id = ?", (d["id"],))
                if g:
                    await P5.rep_add(conn, d["gang_id"], -2, 0)
                await gangwar.kset(conn, f"debtbl:{d['chat_id']}:{d['debtor']}", str(t + BLACKLIST))
                await conn.execute("UPDATE rp_player SET street_cred = MAX(0, street_cred - 5) WHERE chat_id = ? AND user_id = ?", (d["chat_id"], d["debtor"]))
                await rp._news(conn, d["chat_id"], f"📒 «{gname}» списала безнадёжный долг {name} ({rest} ₽): неделю ему никто не даст в долг, уличный авторитет −5")


async def fill(conn, chat_id: int, uid: int, pc: dict) -> None:
    """Состояние для интерфейса: мои долги и предложения, публичный список должников, условия займа."""
    import gangplus3 as P3
    t = now()
    cur = await conn.execute("SELECT d.id, d.gang_id, d.debtor, d.principal, d.owed, d.due, d.status, d.created, g.name FROM debt d JOIN gang g ON g.id = d.gang_id "
                             "WHERE d.chat_id = ? AND d.status IN ('open', 'late', 'offer') ORDER BY d.due", (chat_id,))
    mine, offers, shame = [], [], []
    for did, _gid, debtor, principal, owed, due, status, created, gname in await cur.fetchall():
        if status == "offer":
            if debtor == uid and created + OFFER_TTL > t:
                hours = int(due) if int(due) in TERMS else 48
                offers.append({"id": did, "gang": gname, "amount": principal, "owed": owed, "hours": hours, "left": created + OFFER_TTL - t})
            continue
        item = {"id": did, "gang": gname, "debtor": await P3._uname(conn, chat_id, debtor), "owed": owed, "left": due - t, "late": status == "late"}
        if debtor == uid:
            mine.append(item)
        if status == "late":
            shame.append(item)
    cur = await conn.execute("SELECT id, name, treasury FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    lenders = [{"id": r[0], "name": r[1], "max": max(0, min(AMOUNT_MAX, int(r[2] or 0) - RESERVE))} for r in await cur.fetchall()]
    pc["my_debts"], pc["debt_offers"], pc["debts"] = mine, offers, shame
    pc["loan"] = {"lenders": [x for x in lenders if x["max"] >= AMOUNT_MIN], "terms": [{"hours": h, "pct": int(r * 100)} for h, r in TERMS.items()],
                  "min": AMOUNT_MIN, "max": AMOUNT_MAX, "blocked": await _blocked(conn, chat_id, uid), "busy": bool(mine)}
