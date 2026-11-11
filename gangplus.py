"""ОПГ-плюс: доля босса и общак, авторитет и разборки, дело о крысе.
Босс получает больше (но только пока банда живёт делами), холоп может стать боссом через разборки,
а крыса действует чаще, чем босс может её вычислить."""
import json
import logging
import random
import time
from typing import Optional

import aiosqlite

import gang as G
import gangwar
import rp
from config import DB_PATH
from rooms import GameError

log = logging.getLogger(__name__)

# ── доли и общак ──
BOSS_CUT, DEPUTY_CUT, TREASURY_CUT = 0.10, 0.04, 0.08   # от добычи налёта
IDLE_HOURS = 48                                          # без дел столько часов: привилегии босса отключаются
TERR_MULT = {"boss": 1.5, "deputy": 1.2}                 # множитель дохода с районов
TERR_TREASURY = 0.10

# ── разборки ──
MERIT_NEED, TENURE_DAYS, DEPOSIT = 80, 3, 1000
COUP_HOURS, COUP_CD, GANG_COUP_CD, MIN_VOTERS = 6, 3 * 86400, 86400, 2

# ── крыса и расследование ──
BETRAY_GAP = 3 * 3600          # крыса может сдавать банду чаще (было 24 ч)...
BETRAY_GANG_GAP = 3600     # ...а банду можно сдавать раз в 2 часа (было 6)
BETRAY_MIN_AGE = 1800
LEAK_GAP, LEAK_PAY, LEAK_EXPOSE = 3600, (150, 300), 0.25
FRAME_COST, CLUE_COST, CLUE_MAX = 300, 300, 5
CLUE_DELAYS = [300, 4 * 3600, 8 * 3600, 16 * 3600, 0]   # 0 — только за покупку или утечку
ACCUSE_NEED, ACCUSE_GAP, ACCUSE_MAX = 2, 90 * 60, 2     # босс ошибается дорого и редко
CASE_TTL = 48 * 3600
ESCAPE_PAY, FELL_BONUS = 2000, 700
ACCUSE_FINE, WRONG_COMP = 1500, 800
CLUE_BUY_GAP = 3600

ATTRS = ["tenure", "role", "car", "bar", "wealth", "wanted"]


def now() -> int:
    return int(time.time())


async def _kget(conn, key: str) -> int:
    return int(await gangwar.kget(conn, key) or 0)


async def _kset(conn, key: str, val) -> None:
    await gangwar.kset(conn, key, str(val))


def _active(g: dict) -> bool:
    """Банда «живая»: недавно были дела. Без этого босс не получает привилегий."""
    last = g.get("last_act") or g.get("last_heist") or g.get("created") or 0
    return now() - last < IDLE_HOURS * 3600


# ═════════════════════ доля босса, общак ═════════════════════

async def split_loot(conn, chat_id: int, g: dict, members: list, loot: int, actor: int, p: dict) -> tuple:
    """Делит добычу налёта. Возвращает (доля актёра, пояснение для текста)."""
    import gangplus3
    loot = int(loot * (1 + await gangplus3.proj_effect(conn, g["id"], "depot")))
    real = [m for m in members if m["user_id"] > 0]
    n = max(1, len(real))
    active = _active(g) and await _kget(conn, f"gshaky:{g['id']}") <= now()
    deputies = [m for m in real if m["role"] == "deputy"][:G.MAX_DEPUTIES]
    boss_cut = int(loot * BOSS_CUT) if active else 0
    dep_cut = int(loot * DEPUTY_CUT) if active else 0
    treasury = int(loot * TREASURY_CUT)
    rest = loot - boss_cut - dep_cut * len(deputies) - treasury
    share = rest // n
    mine = share
    from gangplus2 import log_event
    for m in real:
        amount = share + (boss_cut if m["role"] == "boss" else 0) + (dep_cut if m in deputies else 0)
        if (m.get("probation_until") or 0) > now():  # на испытательном сроке половина доли уходит в общак
            treasury += amount // 2
            amount -= amount // 2
        await log_event(conn, chat_id, m["user_id"], "heist", amount)
        if m["user_id"] == actor:
            mine = amount
            p["money"] += amount
        else:
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, m["user_id"]))
    await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (treasury, g["id"]))
    note = f" (общак +{treasury} ₽" + (f", босс получил {boss_cut + share} ₽" if boss_cut else ", босс без доли: банда простаивала") + ")"
    return mine, note


async def pay_territory(conn, chat_id: int, gid: int, members: list, base: int) -> None:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang WHERE id = ?", (gid,))
    g = await cur.fetchone()
    g = dict(g) if g else {}
    active = bool(g) and _active(g)
    cur = await conn.execute("SELECT user_id, role FROM gang_member WHERE gang_id = ? AND user_id > 0", (gid,))
    roles = {r[0]: r[1] for r in await cur.fetchall()}
    import gangplus3
    bonus = await gangplus3.bonus_mult(conn, gid)
    for u in members:
        mult = (TERR_MULT.get(roles.get(u, "grunt"), 1.0) if active else 1.0) * bonus
        await rp._load(conn, chat_id, u)
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (int(base * mult), chat_id, u))
    if g:
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (int(base * len(members) * TERR_TREASURY), gid))


async def donate(chat_id: int, uid: int, name: str, amount) -> dict:
    amount = int(amount or 0)
    if amount < 50:
        raise GameError("Взнос в общак — от 50 ₽")
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, _ = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < amount:
            raise GameError(f"Нужно {amount} ₽, у тебя {p['money']}")
        p["money"] -= amount
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (amount, g["id"]))
        await conn.execute("UPDATE gang_member SET merit = merit + ? WHERE chat_id = ? AND user_id = ?", (amount // 100, chat_id, uid))
        from gangplus2 import log_event
        await log_event(conn, chat_id, uid, "donate", amount)
        await rp._news(conn, chat_id, f"💰 {name} пополнил общак ОПГ «{g['name']}» на {amount} ₽")
        await conn.commit()
    return {"text": f"💰 В общак ушло {amount} ₽. Авторитет вырос.", "delta": -amount, "good": True, "state": await rp.state(chat_id, uid)}


async def bail(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)
    cost = 500
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        my_role = next(m["role"] for m in members if m["user_id"] == uid)
        if my_role not in ("boss", "deputy"):
            raise GameError("Вытаскивать из тюрьмы могут босс и замы")
        t = next((m for m in members if m["user_id"] == target and m["user_id"] > 0), None)
        if not t:
            raise GameError("Этого человека нет в банде")
        tp = await rp._load(conn, chat_id, target)
        if tp["jail_until"] <= now():
            raise GameError("Он и так на свободе")
        cur = await conn.execute("SELECT treasury FROM gang WHERE id = ?", (g["id"],))
        if (await cur.fetchone())[0] < cost:
            raise GameError(f"В общаке меньше {cost} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (cost, g["id"]))
        await conn.execute("UPDATE rp_player SET jail_until = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        await rp._news(conn, chat_id, f"🔓 ОПГ «{g['name']}» вытащила {t['username'] or 'своего'} из тюрьмы за счёт общака")
        await conn.commit()
    return {"text": f"🔓 {t['username'] or 'Игрок'} на свободе (−{cost} ₽ из общака)", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


# ═════════════════════ разборки (смена босса) ═════════════════════

async def _coup_row(conn, gid: int):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_coup WHERE gang_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1", (gid,))
    r = await cur.fetchone()
    return dict(r) if r else None


def _voters(members: list, challenger: int) -> list:
    return [m for m in members if m["user_id"] > 0 and m["role"] != "boss" and m["user_id"] != challenger]


async def coup_start(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        me = next(m for m in members if m["user_id"] == uid)
        if me["role"] == "boss":
            raise GameError("Ты и так босс")
        if await _coup_row(conn, g["id"]):
            raise GameError("В банде уже идут разборки")
        if (me.get("probation_until") or 0) > now():
            raise GameError("На испытательном сроке бросать вызов нельзя")
        if (me.get("merit") or 0) < MERIT_NEED:
            raise GameError(f"Мало авторитета: нужно {MERIT_NEED}, у тебя {me.get('merit') or 0}")
        if now() - (me.get("joined") or now()) < TENURE_DAYS * 86400:
            raise GameError(f"В банде нужно побыть хотя бы {TENURE_DAYS} дня")
        if now() - await _kget(conn, f"gcoupu:{uid}") < COUP_CD:
            raise GameError("Ты уже бросал вызов на этой неделе")
        if now() - await _kget(conn, f"gcoupg:{g['id']}") < GANG_COUP_CD:
            raise GameError("Новый босс ещё не освоился: банде нужно 3 дня тишины после разборок")
        if len(_voters(members, uid)) < MIN_VOTERS:
            raise GameError(f"Для разборок нужно минимум {MIN_VOTERS} голосующих кроме тебя и босса")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < DEPOSIT:
            raise GameError(f"Залог {DEPOSIT} ₽, у тебя {p['money']}")
        p["money"] -= DEPOSIT
        await rp._save(conn, chat_id, uid, p)
        await _kset(conn, f"gcoupu:{uid}", now())
        await conn.execute("INSERT INTO gang_coup (gang_id, chat_id, challenger, deposit, started, ends, votes, status) VALUES (?, ?, ?, ?, ?, ?, '{}', 'open')",
                           (g["id"], chat_id, uid, DEPOSIT, now(), now() + COUP_HOURS * 3600))
        await rp._news(conn, chat_id, f"⚔️ {name} бросил вызов боссу ОПГ «{g['name']}»")
        await gangwar.say(conn, chat_id, f"⚔️ <b>Разборки в ОПГ «{g['name']}»!</b> {name} бросил вызов боссу. Банда голосует {COUP_HOURS} часов. "
                                          "Босс голоса не имеет, решают замы и холопы.")
        await conn.commit()
    return {"text": f"⚔️ Вызов брошен (залог {DEPOSIT} ₽). Банда решит за {COUP_HOURS} часов.", "delta": -DEPOSIT, "good": True, "state": await rp.state(chat_id, uid)}


async def kset_plot_cd(conn, uid: int) -> None:
    await _kset(conn, f"gplotcd:{uid}", now())


async def _coup_finish(conn, g: dict, members: list, coup: dict, success: bool) -> None:
    await conn.execute("UPDATE gang_coup SET status = ? WHERE id = ?", ("won" if success else "lost", coup["id"]))
    chat_id, ch = coup["chat_id"], coup["challenger"]
    ch_name = next((m["username"] for m in members if m["user_id"] == ch), None) or "претендент"
    is_plot = coup.get("kind") == "plot"
    old = next((m for m in members if m["role"] == "boss"), None)
    if success:
        if coup["deposit"]:
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (coup["deposit"], chat_id, ch))
        if is_plot:
            await conn.execute("UPDATE gang_plot SET status = 'won' WHERE gang_id = ? AND status = 'vote'", (g["id"],))
        if old:
            await conn.execute("UPDATE gang_member SET role = 'deputy' WHERE chat_id = ? AND user_id = ?", (chat_id, old["user_id"]))
        await conn.execute("UPDATE gang_member SET role = 'boss', merit = merit + 20 WHERE chat_id = ? AND user_id = ?", (chat_id, ch))
        await conn.execute("UPDATE gang SET boss = ? WHERE id = ?", (ch, g["id"]))
        cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND role = 'deputy' AND user_id > 0 ORDER BY merit ASC", (g["id"],))
        deps = [r[0] for r in await cur.fetchall()]
        while len(deps) > G.MAX_DEPUTIES:  # лишнего зама с наименьшим авторитетом разжалуем
            await conn.execute("UPDATE gang_member SET role = 'grunt' WHERE chat_id = ? AND user_id = ?", (chat_id, deps.pop(0)))
        await _kset(conn, f"gcoupg:{g['id']}", now())
        await rp._news(conn, chat_id, f"👑 Переворот в ОПГ «{g['name']}»: {ch_name} стал боссом")
        await gangwar.say(conn, chat_id, f"👑 <b>Переворот!</b> В ОПГ «{g['name']}» новый главный босс — <b>{ch_name}</b>. Прежний босс теперь зам. Залог возвращён.", voice=True)
    else:
        if is_plot:
            await conn.execute("UPDATE gang_plot SET status = 'failed' WHERE gang_id = ? AND status = 'vote'", (g["id"],))
            cur = await conn.execute("SELECT money FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, ch))
            row = await cur.fetchone()
            fine = min(row[0] if row else 0, 800)
            await conn.execute("UPDATE rp_player SET money = money - ?, beaten_until = ? WHERE chat_id = ? AND user_id = ?", (fine, now() + 20 * 60, chat_id, ch))
            await kset_plot_cd(conn, ch)
        elif old and old["user_id"] > 0:
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (coup["deposit"], chat_id, old["user_id"]))
        await conn.execute("UPDATE gang_member SET merit = MAX(0, merit - 20) WHERE chat_id = ? AND user_id = ?", (chat_id, ch))
        await rp._news(conn, chat_id, f"🛡 Босс ОПГ «{g['name']}» отбил бунт {ch_name}")
        await gangwar.say(conn, chat_id, f"🛡 <b>Бунт подавлен.</b> Банда «{g['name']}» оставила прежнего босса. Залог {ch_name} ушёл боссу.")


async def _coup_check(conn, coup: dict, force: bool = False) -> bool:
    """Проверяет исход голосования. Возвращает True, если разборки закончились."""
    gid = coup["gang_id"]
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang WHERE id = ?", (gid,))
    g = await cur.fetchone()
    if not g:
        await conn.execute("UPDATE gang_coup SET status = 'lost' WHERE id = ?", (coup["id"],))
        return True
    g = dict(g)
    _, members = await G._mine_by_gang(conn, gid)
    voters = {m["user_id"] for m in _voters(members, coup["challenger"])}
    votes = {int(k): v for k, v in json.loads(coup["votes"] or "{}").items() if int(k) in voters}
    yes = sum(1 for v in votes.values() if v == "for")
    no = sum(1 for v in votes.values() if v == "against")
    need = len(voters) // 2 + 1
    if yes >= need:
        await _coup_finish(conn, g, members, coup, True)
        return True
    if no >= need or (len(voters) - no < need) or (force and yes <= no) or (force and yes < need):
        await _coup_finish(conn, g, members, coup, False)
        return True
    return False


async def coup_vote(chat_id: int, uid: int, name: str, side) -> dict:
    if side not in ("for", "against"):
        raise GameError("Голос: за или против")
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        coup = await _coup_row(conn, g["id"])
        if not coup:
            raise GameError("Разборок сейчас нет")
        if uid not in {m["user_id"] for m in _voters(members, coup["challenger"])}:
            raise GameError("Босс и претендент не голосуют")
        votes = json.loads(coup["votes"] or "{}")
        votes[str(uid)] = side
        coup["votes"] = json.dumps(votes)
        await conn.execute("UPDATE gang_coup SET votes = ? WHERE id = ?", (coup["votes"], coup["id"]))
        done = await _coup_check(conn, coup)
        await conn.commit()
    return {"text": "Голос принят" + (": разборки закончились, смотри итог" if done else ""), "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


# ═════════════════════ дело о крысе ═════════════════════

async def _dossiers(conn, chat_id: int, members: list) -> dict:
    real = [m for m in members if m["user_id"] > 0]
    if not real:
        return {}
    ids = [m["user_id"] for m in real]
    q = ",".join("?" * len(ids))
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(f"SELECT user_id, money, wanted, car FROM rp_player WHERE chat_id = ? AND user_id IN ({q})", (chat_id, *ids))
    rps = {r["user_id"]: dict(r) for r in await cur.fetchall()}
    cur = await conn.execute(f"SELECT user_id, level, tipsy_until FROM bar_player WHERE chat_id = ? AND user_id IN ({q})", (chat_id, *ids))
    bars = {r["user_id"]: dict(r) for r in await cur.fetchall()}
    avg = sum(rps.get(u, {}).get("money", 0) for u in ids) / len(ids)
    out = {}
    for m in real:
        u = m["user_id"]
        bar = bars.get(u)
        nm = (m["username"] or "?").lstrip("@")
        out[u] = {
            "id": u, "name": m["username"] or "игрок", "role": m["role"],
            "days": max(0, (now() - (m.get("joined") or now())) // 86400),
            "car": rps.get(u, {}).get("car", 0),
            "drunk": bool(bar and (bar["level"] >= 15 or bar["tipsy_until"] > now())),
            "rich": rps.get(u, {}).get("money", 0) > avg,
            "hot": rps.get(u, {}).get("wanted", 0) >= 3,
            "letter": (nm[:1] or "?").upper(),
        }
    return out


def _val(d: dict, attr: str) -> str:
    return {"tenure": "new" if d["days"] < 7 else "old", "role": d["role"], "car": "car" if d["car"] > 0 else "foot",
            "bar": "yes" if d["drunk"] else "no", "wealth": "rich" if d["rich"] else "poor",
            "wanted": "hot" if d["hot"] else "clean", "letter": d["letter"]}[attr]


def _riddle(attr: str, value: str, d: dict) -> str:
    T = {
        ("tenure", "new"): "Лавочка шепчет: этот человек в банде без году неделя.",
        ("tenure", "old"): "Лавочка шепчет: тут старожил, больше недели в банде.",
        ("role", "deputy"): "Тётя Зина слышала: донос принёс тот, у кого на плечах зам-погоны.",
        ("role", "grunt"): "Дед Мазай видел: сдал тот, кто носит топор простого холопа.",
        ("bar", "yes"): "Кузьмич клянётся: от него несло баром не дальше чем сутки назад.",
        ("bar", "no"): "Кузьмич клянётся: последние сутки он был трезв как стёклышко.",
        ("wealth", "rich"): "Лидка с рынка шепнула: кошелёк у него толще, чем у остальных в среднем.",
        ("wealth", "poor"): "Лидка с рынка шепнула: кошелёк у него тоньше, чем у остальных в среднем.",
        ("wanted", "hot"): "Участковый вздыхает: на него уже косо смотрят, три звезды розыска и выше.",
        ("wanted", "clean"): "Участковый вздыхает: на него пока ничего нет, меньше трёх звёзд.",
    }
    if attr == "car":
        return ("Соседка заметила: он уже ездит на собственной тачке." if value == "car"
                else "Соседка заметила: он до сих пор ходит пешком.")
    if attr == "letter":
        return f"Баба Валя щурится: имя начинается на «{value}»."
    return T[(attr, value)]


async def _case(conn, gid: int):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_betray WHERE gang_id = ? AND resolved = 0 AND ts > ? ORDER BY id DESC LIMIT 1", (gid, now() - CASE_TTL))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _make_clues(conn, case_id: int, rat: int, dossiers: dict) -> None:
    d = dossiers.get(rat)
    if not d:
        return
    suspects = [x for u, x in dossiers.items()]
    scored = []
    for attr in ATTRS:
        same = sum(1 for x in suspects if _val(x, attr) == _val(d, attr))
        scored.append((same / len(suspects) + random.random() * 0.15, attr))
    order = [a for _, a in sorted(scored)][:4] + ["letter"]
    for i, attr in enumerate(order):
        unlock = now() + CLUE_DELAYS[i] if CLUE_DELAYS[i] else 0
        await conn.execute(
            "INSERT INTO rat_clue (case_id, idx, attr, value, text, fake, subject, unlock_ts) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (case_id, i, attr, _val(d, attr), _riddle(attr, _val(d, attr), d), rat, unlock))


async def _clues(conn, case_id: int) -> list:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM rat_clue WHERE case_id = ? ORDER BY idx", (case_id,))
    return [dict(r) for r in await cur.fetchall()]


def _unlocked(c: dict) -> bool:
    return c["unlock_ts"] > 0 and c["unlock_ts"] <= now()


async def betray(chat_id: int, uid: int, name: str) -> dict:
    # состояние считаем вне блокировки: rp.state сам берёт её (asyncio.Lock не повторный)
    res = await _betray(chat_id, uid, name)
    res["state"] = await rp.state(chat_id, uid)
    return res


async def _betray(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        me = next(m for m in members if m["user_id"] == uid)
        if me["role"] == "boss":
            raise GameError("Босс не может сдать сам себя. Если надоело, передай власть и уходи")
        if now() - (me.get("joined") or 0) < BETRAY_MIN_AGE:
            raise GameError("Сначала пробейся в доверие хотя бы на полчаса: свежих предателей полиция не уважает")
        if now() - await _kget(conn, f"gbt:{uid}") < BETRAY_GAP:
            raise GameError("Ты недавно уже сдавал банду, подожди немного")
        if now() - await _kget(conn, f"gbtg:{g['id']}") < BETRAY_GANG_GAP:
            raise GameError("Полиция уже недавно навещала эту банду, наводку не примут")
        if await _case(conn, g["id"]):
            raise GameError("Расследование уже идёт. Заметай следы: «Слив» и «Подстава» помогают спрятаться")
        p = await rp._load(conn, chat_id, uid)
        await _kset(conn, f"gbt:{uid}", now())
        await _kset(conn, f"gbtg:{g['id']}", now())
        for m in members:
            if m["user_id"] > 0 and m["user_id"] != uid:
                op = await rp._load(conn, chat_id, m["user_id"])
                await conn.execute("UPDATE rp_player SET wanted = ?, wanted_ts = ? WHERE chat_id = ? AND user_id = ?",
                                   (min(rp.MAX_WANTED, op["wanted"] + 2), now(), chat_id, m["user_id"]))
        await conn.execute("UPDATE gang SET last_heist = ? WHERE id = ?", (now(), g["id"]))
        if random.random() < 0.2:
            fine = min(p["money"], 400)
            p["money"] -= fine
            p["jail_until"] = now() + 20 * 60
            p["esc_used"] = 0
            await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            await rp._save(conn, chat_id, uid, p)
            await rp._news(conn, chat_id, f"🐀 {name} попытался сдать ОПГ «{g['name']}», но его вычислили на месте. Изгнан, штраф {fine} ₽")
            await gangwar.say(conn, chat_id, f"🐀 <b>Крыса найдена!</b> {name} пытался сдать «{g['name']}» полиции, но попался с поличным. Изгнан.")
            await conn.commit()
            return {"text": f"🐀 Провал: тебя вычислили сразу. Изгнан, штраф {fine} ₽, 20 минут в тюрьме.", "delta": -fine, "good": False}
        reward = random.randint(800, 1500)
        p["money"] += reward
        p["cop_rep"] += 3
        await rp._save(conn, chat_id, uid, p)
        cur = await conn.execute("INSERT INTO gang_betray (gang_id, chat_id, uid, ts, reward) VALUES (?, ?, ?, ?, ?)", (g["id"], chat_id, uid, now(), reward))
        await _make_clues(conn, cur.lastrowid, uid, await _dossiers(conn, chat_id, members))
        await rp._news(conn, chat_id, f"🚔 Полиция получила анонимную наводку на ОПГ «{g['name']}»: у участников розыск +2, налёты на час отложены")
        await gangwar.say(conn, chat_id, f"🚔 <b>Анонимная наводка!</b> На ОПГ «{g['name']}» поступил донос. У всех участников розыск +2, налёт отложен на час. "
                                          "Босс открыл дело: улики-загадки приходят в мини-апп. Кто это был, не знает никто... пока.")
        await conn.commit()
    return {"text": f"🐀 Наводка принята: {reward} ₽ твои. Дело открыто на 48 часов. Сливай по мелочи и подставляй других, но осторожно: каждая утечка может стать уликой.",
            "delta": reward, "good": True}


async def _my_case(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g:
        raise GameError("Ты не состоишь в ОПГ")
    case = await _case(conn, g["id"])
    if not case or case["uid"] != uid:
        raise GameError("У тебя нет открытого дела: сначала «Сдать банду»")
    return g, members, case


async def leak(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, case = await _my_case(conn, chat_id, uid)
        wait = case["last_leak"] + LEAK_GAP - now()
        if wait > 0:
            raise GameError(f"Слить можно через {wait // 60 + 1} мин.")
        pay = random.randint(*LEAK_PAY)
        p = await rp._load(conn, chat_id, uid)
        p["money"] += pay
        await rp._save(conn, chat_id, uid, p)
        exposed = False
        if random.random() < LEAK_EXPOSE:
            for c in await _clues(conn, case["id"]):
                if not c["fake"] and not _unlocked(c):
                    await conn.execute("UPDATE rat_clue SET unlock_ts = ? WHERE id = ?", (now(), c["id"]))
                    exposed = True
                    break
        await conn.execute("UPDATE gang_betray SET leaks = leaks + 1, last_leak = ?, reward = reward + ? WHERE id = ?", (now(), pay, case["id"]))
        if exposed:
            await gangwar.say(conn, chat_id, f"🕵 В ОПГ «{g['name']}» опять кто-то мелькнул: у босса появилась новая улика.")
        await conn.commit()
    return {"text": f"🐀 Слив прошёл тихо: +{pay} ₽." + (" Но кто-то что-то заметил: у босса новая улика." if exposed else ""),
            "delta": pay, "good": not exposed, "state": await rp.state(chat_id, uid)}


async def frame(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, case = await _my_case(conn, chat_id, uid)
        if case["framed"]:
            raise GameError("Ты уже подставлял кого-то в этом деле")
        t = next((m for m in members if m["user_id"] == target and m["user_id"] > 0 and m["role"] != "boss"), None)
        if not t or target == uid:
            raise GameError("Подставить можно только другого участника, кроме босса")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < FRAME_COST:
            raise GameError(f"Подстава стоит {FRAME_COST} ₽ на взятки свидетелям")
        dos = await _dossiers(conn, chat_id, members)
        me_d, t_d = dos[uid], dos[target]
        diffs = [a for a in ATTRS if _val(me_d, a) != _val(t_d, a)]
        if not diffs:
            raise GameError("Вы слишком похожи: на него нечего повесить")
        attr = random.choice(diffs)
        p["money"] -= FRAME_COST
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("INSERT INTO rat_clue (case_id, idx, attr, value, text, fake, subject, unlock_ts) VALUES (?, 100, ?, ?, ?, 1, ?, ?)",
                           (case["id"], attr, _val(t_d, attr), _riddle(attr, _val(t_d, attr), t_d), target, now() + 900))
        await conn.execute("UPDATE gang_betray SET framed = ? WHERE id = ?", (target, case["id"]))
        await conn.commit()
    return {"text": f"🎭 Подстава готова (−{FRAME_COST} ₽): через 15 минут у босса появится улика, указывающая на {t['username'] or 'него'}.",
            "delta": -FRAME_COST, "good": True, "state": await rp.state(chat_id, uid)}


async def buy_clue(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g or next(m["role"] for m in members if m["user_id"] == uid) != "boss":
            raise GameError("Улики покупает главный босс")
        case = await _case(conn, g["id"])
        if not case:
            raise GameError("Нет открытого дела")
        wait = await _kget(conn, f"gclue:{g['id']}") + CLUE_BUY_GAP - now()
        if wait > 0:
            raise GameError(f"Следующую улику можно купить через {wait // 60 + 1} мин.")
        locked = [c for c in await _clues(conn, case["id"]) if not c["fake"] and not _unlocked(c)]
        if not locked:
            raise GameError("Все улики уже открыты")
        cur = await conn.execute("SELECT treasury FROM gang WHERE id = ?", (g["id"],))
        tr = (await cur.fetchone())[0]
        paid_from = "общака"
        if tr >= CLUE_COST:
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (CLUE_COST, g["id"]))
        else:
            p = await rp._load(conn, chat_id, uid)
            if p["money"] < CLUE_COST:
                raise GameError(f"Улика стоит {CLUE_COST} ₽: ни в общаке, ни у тебя столько нет")
            p["money"] -= CLUE_COST
            await rp._save(conn, chat_id, uid, p)
            paid_from = "твоего кармана"
        await conn.execute("UPDATE rat_clue SET unlock_ts = ? WHERE id = ?", (now(), locked[0]["id"]))
        await _kset(conn, f"gclue:{g['id']}", now())
        await conn.commit()
    return {"text": f"🔎 Куплена новая улика ({CLUE_COST} ₽ из {paid_from}).", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def accuse(chat_id: int, uid: int, name: str, target) -> dict:
    # состояние считаем вне блокировки: rp.state сам берёт её (asyncio.Lock не повторный)
    res = await _accuse(chat_id, uid, name, target)
    res["state"] = await rp.state(chat_id, uid)
    return res


async def _accuse(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        if next(m["role"] for m in members if m["user_id"] == uid) != "boss":
            raise GameError("Расследование ведёт только главный босс")
        t = next((m for m in members if m["user_id"] == target and m["user_id"] > 0), None)
        if not t or target == uid:
            raise GameError("Выбери кого-то из своей банды")
        case = await _case(conn, g["id"])
        if not case:
            raise GameError("Нет нераскрытых дел: расследовать нечего")
        if case["attempts"] >= ACCUSE_MAX:
            raise GameError("Попытки обвинить закончились: дело уходит в архив")
        opened = sum(1 for c in await _clues(conn, case["id"]) if _unlocked(c))
        if opened < ACCUSE_NEED:
            raise GameError(f"Улик мало: нужно открыть хотя бы {ACCUSE_NEED} (сейчас {opened})")
        wait = await _kget(conn, f"gacc2:{g['id']}") + ACCUSE_GAP - now()
        if wait > 0:
            raise GameError(f"После ошибки нужно остыть: ещё {wait // 60 + 1} мин.")
        tname = t["username"] or "игрок"
        if case["uid"] == target:
            await conn.execute("UPDATE gang_betray SET resolved = 1 WHERE id = ?", (case["id"],))
            tp = await rp._load(conn, chat_id, target)
            fine = min(tp["money"], ACCUSE_FINE)
            tp["money"] -= fine
            tp["jail_until"] = now() + 30 * 60
            tp["esc_used"] = 0
            await rp._save(conn, chat_id, target, tp)
            await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, target))
            await conn.execute("UPDATE rp_player SET cop_rep = MAX(0, cop_rep - 5) WHERE chat_id = ? AND user_id = ?", (chat_id, target))
            bonus = case["reward"] // 2
            bp = await rp._load(conn, chat_id, uid)
            bp["money"] += bonus
            await rp._save(conn, chat_id, uid, bp)
            await conn.execute("UPDATE gang_member SET merit = merit + 30 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            await rp._news(conn, chat_id, f"🐀 Босс «{g['name']}» вычислил крысу: {tname}. Изгнан, штраф {fine} ₽")
            await gangwar.say(conn, chat_id, f"🐀 <b>Крыса найдена!</b> Босс «{g['name']}» собрал улики: донос написал <b>{tname}</b>. Изгнан, штраф {fine} ₽, 30 минут в тюрьме.", voice=True)
            await conn.commit()
            return {"text": f"🐀 В точку! Это был {tname}. Изгнан, штраф {fine} ₽. Тебе премия {bonus} ₽ и +30 авторитета.", "delta": bonus, "good": True}
        await _kset(conn, f"gacc2:{g['id']}", now())
        await conn.execute("UPDATE gang_betray SET attempts = attempts + 1 WHERE id = ?", (case["id"],))
        p = await rp._load(conn, chat_id, uid)
        comp = min(p["money"], WRONG_COMP)
        p["money"] -= comp
        await rp._save(conn, chat_id, uid, p)
        await rp._load(conn, chat_id, target)
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (comp, chat_id, target))
        await conn.execute("UPDATE gang_member SET merit = MAX(0, merit - 15) WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        if case["framed"] and case["framed"] == target:
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (FELL_BONUS, chat_id, case["uid"]))
            await conn.execute("UPDATE gang_betray SET frame_fell = 1 WHERE id = ?", (case["id"],))
        await rp._news(conn, chat_id, f"😤 Босс «{g['name']}» обвинил {tname} в доносе, но ошибся и заплатил компенсацию {comp} ₽")
        await conn.commit()
        left = ACCUSE_MAX - case["attempts"] - 1
    return {"text": f"😤 Мимо! {tname} невиновен, ты платишь ему {comp} ₽ и теряешь авторитет. Попыток осталось: {left}.",
            "delta": -comp, "good": False}


# ═════════════════════ данные для интерфейса ═════════════════════

async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    await _extras1(conn, chat_id, uid, g, my_role, out)
    import gangplus2
    await gangplus2.extras(conn, chat_id, uid, g, my_role, out)


async def _extras1(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    m = out.get("mine")
    if not g or not m:
        return
    _, members = await G._mine(conn, chat_id, uid)
    me = next((x for x in members if x["user_id"] == uid), {})
    m["treasury"] = g.get("treasury", 0)
    m["my_merit"] = me.get("merit") or 0
    m["cuts"] = {"boss": int(BOSS_CUT * 100), "deputy": int(DEPUTY_CUT * 100), "treasury": int(TREASURY_CUT * 100),
                 "active": _active(g), "idle_hours": IDLE_HOURS}
    m["can_bail"] = my_role in ("boss", "deputy")
    cur = await conn.execute("SELECT user_id, jail_until FROM rp_player WHERE chat_id = ?", (chat_id,))
    jail = {r[0]: r[1] > now() for r in await cur.fetchall()}
    for x in m.get("members", []):
        x["jailed"] = bool(jail.get(x["id"]))
    # разборки
    coup = await _coup_row(conn, g["id"])
    rules = {"merit": MERIT_NEED, "days": TENURE_DAYS, "deposit": DEPOSIT, "hours": COUP_HOURS}
    days = max(0, (now() - (me.get("joined") or now())) // 86400)
    if coup:
        votes = json.loads(coup["votes"] or "{}")
        voters = _voters(members, coup["challenger"])
        m["coup"] = {
            "challenger": coup["challenger"], "challenger_name": next((x["username"] for x in members if x["user_id"] == coup["challenger"]), "?"),
            "left": max(0, coup["ends"] - now()), "deposit": coup["deposit"],
            "yes": sum(1 for v in votes.values() if v == "for"), "no": sum(1 for v in votes.values() if v == "against"),
            "voters": len(voters), "need": len(voters) // 2 + 1,
            "can_vote": uid in {x["user_id"] for x in voters}, "my_vote": votes.get(str(uid), ""),
        }
    else:
        m["coup"] = None
        reason = ""
        if my_role == "boss":
            reason = "boss"
        elif m["my_merit"] < MERIT_NEED:
            reason = "merit"
        elif days < TENURE_DAYS:
            reason = "days"
        elif now() - await _kget(conn, f"gcoupu:{uid}") < COUP_CD:
            reason = "week"
        elif now() - await _kget(conn, f"gcoupg:{g['id']}") < GANG_COUP_CD:
            reason = "fresh"
        elif len(_voters(members, uid)) < MIN_VOTERS:
            reason = "voters"
        m["coup_can"] = not reason
        m["coup_block"] = reason
    m["coup_rules"] = rules
    m["my_days"] = days
    # дело о крысе
    case = await _case(conn, g["id"])
    m["rat"] = None
    m["my_rat"] = None
    if case:
        clues = await _clues(conn, case["id"])
        shown = [c for c in clues if _unlocked(c)]
        if my_role == "boss":
            dos = await _dossiers(conn, chat_id, members)
            gw = await _kget(conn, f"gacc2:{g['id']}") + ACCUSE_GAP - now()
            bw = await _kget(conn, f"gclue:{g['id']}") + CLUE_BUY_GAP - now()
            m["rat"] = {
                "left": max(0, case["ts"] + CASE_TTL - now()), "clues": [{"n": i + 1, "text": c["text"]} for i, c in enumerate(shown)],
                "attempts_left": max(0, ACCUSE_MAX - case["attempts"]), "need": ACCUSE_NEED,
                "can_accuse": len(shown) >= ACCUSE_NEED and case["attempts"] < ACCUSE_MAX and gw <= 0,
                "accuse_wait": max(0, gw), "buy_cost": CLUE_COST, "buy_wait": max(0, bw),
                "can_buy": any(not c["fake"] and not _unlocked(c) for c in clues) and bw <= 0,
                "dossier": [{"id": d["id"], "name": d["name"], "role": d["role"], "days": d["days"], "car": d["car"] > 0, "drunk": d["drunk"],
                             "rich": d["rich"], "hot": d["hot"], "letter": d["letter"]} for u, d in dos.items() if u != uid],
            }
        elif case["uid"] == uid:
            lw = case["last_leak"] + LEAK_GAP - now()
            m["my_rat"] = {"left": max(0, case["ts"] + CASE_TTL - now()), "leaks": case["leaks"], "leak_wait": max(0, lw),
                           "leak_pay": list(LEAK_PAY), "can_frame": not case["framed"], "frame_cost": FRAME_COST,
                           "escape_pay": ESCAPE_PAY, "clues_out": len(shown),
                           "targets": [{"id": x["user_id"], "name": x["username"] or "игрок"} for x in members if x["user_id"] > 0 and x["user_id"] != uid and x["role"] != "boss"]}


# ═════════════════════ фоновые задачи ═════════════════════

async def _tick2(conn) -> None:
    import gangplus2
    await gangplus2.tick(conn)


async def tick(conn) -> None:
    await _tick2(conn)
    conn.row_factory = aiosqlite.Row
    # разборки по таймеру
    cur = await conn.execute("SELECT * FROM gang_coup WHERE status = 'open' AND ends <= ?", (now(),))
    for coup in [dict(r) for r in await cur.fetchall()]:
        await _coup_check(conn, coup, force=True)
    # дела, которые никто не раскрыл за 48 часов: крыса уходит чистой
    cur = await conn.execute("SELECT b.*, g.name AS gname FROM gang_betray b JOIN gang g ON g.id = b.gang_id WHERE b.resolved = 0 AND b.ts <= ?", (now() - CASE_TTL,))
    for case in [dict(r) for r in await cur.fetchall()]:
        await conn.execute("UPDATE gang_betray SET resolved = 2 WHERE id = ?", (case["id"],))
        cur2 = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (case["chat_id"], case["uid"]))
        if await cur2.fetchone():
            pay = ESCAPE_PAY + 200 * case["leaks"]
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (pay, case["chat_id"], case["uid"]))
        await gangwar.say(conn, case["chat_id"], f"🕵 Дело о доносе на ОПГ «{case['gname']}» закрыто за сроком давности. Крысу так и не нашли. Она ушла чистой.")
