"""ОПГ-плюс 2: обязательные взносы, заговоры против босса, налёт с ролями, допрос, амнистия и раскаяние,
тайные сходки, недельный итог и двойной агент полиции."""
import json
import logging
import random
import time
from typing import Optional

import aiosqlite

import gang as G
import gangplus as GP
import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import now_local

log = logging.getLogger(__name__)

# ── взносы ──
DUES = {"boss": 200, "deputy": 150, "grunt": 100}
DUES_PERIOD, DUES_WINDOW, DUES_GRACE = 24 * 3600, 12 * 3600, 6 * 3600
DUES_MERIT_MISS, DUES_BEAT_CHANCE, BEAT_MINUTES = 15, 0.5, 20

# ── заговоры ──
PLOT_FINE, PLOT_CD = 1000, 86400
PLOTS = {
    "dirt": ("🗂 Компромат", "тихий путь: слежка, улики, обращение к банде"),
    "bribe": ("💵 Подкуп", "купить голоса замов и холопов, потом голосование"),
    "force": ("👊 Силой", "собрать бойцов, засада на босса, захват власти"),
}

# ── налёт с ролями ──
RH_ENERGY, RH_WINDOW, RH_CD, RH_MULT = 25, 20 * 60, 90 * 60, 1.6
RH_SLOTS = {"boss": "planner", "deputy": "cracker", "grunt": "lookout"}
RH_NAMES = {"planner": "🧠 Планировщик (босс)", "cracker": "🔓 Взломщик (зам)", "lookout": "👀 Шухер (холоп)"}

# ── допрос ──
INT_ENERGY, INT_MAX, INT_GAP = 15, 3, 30 * 60

# ── амнистия, раскаяние, сходки ──
AMNESTY_COST, AMNESTY_CD = 1000, 3 * 86400
REPENT_COST, PROBATION = 3000, 48 * 3600
MEET_LEN, MEET_CD, MEET_MERIT, MEET_ENERGY, MEET_BANQUET, MEET_MIN = 2 * 3600, 3 * 86400, 10, 30, 100, 3

# ── двойной агент ──
AGENT_GAP, AGENT_PAY, AGENT_EXPOSE = 90 * 60, (300, 600), 0.12
COP_RANKS = rp.COP_RANKS


def now() -> int:
    return int(time.time())


cop_rank = rp.cop_rank


async def _row(conn, sql: str, *args):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(sql, args)
    r = await cur.fetchone()
    return dict(r) if r else None


async def log_event(conn, chat_id: int, uid: int, kind: str, amount: int = 0) -> None:
    """Запись для недельного итога банды."""
    cur = await conn.execute("SELECT gang_id FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    row = await cur.fetchone()
    if row:
        await conn.execute("INSERT INTO gang_log (chat_id, gang_id, uid, kind, amount, ts) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, row[0], uid, kind, amount, now()))
        import police, police2
        await police.event(conn, chat_id, uid, kind)
        await police2.alert(conn, chat_id, row[0], kind)
        if kind == "jail":
            import balance
            await balance.on_jail(conn, chat_id, row[0])


def _member(members: list, uid: int) -> Optional[dict]:
    return next((m for m in members if m["user_id"] == uid), None)


async def _need_gang(conn, chat_id: int, uid: int):
    g, members = await G._mine(conn, chat_id, uid)
    if not g or g.get("is_ai"):
        raise GameError("Ты не состоишь в ОПГ")
    return g, members, _member(members, uid)


async def _beat(conn, chat_id: int, uid: int, minutes: int) -> None:
    cur = await conn.execute("SELECT value FROM kv WHERE key = ?", (f"p4cover:{chat_id}:{uid}",))
    row = await cur.fetchone()
    if row and int(row[0] or 0) > now():  # «крыша» бойца защищает от побоев
        return
    await rp._load(conn, chat_id, uid)
    await conn.execute("UPDATE rp_player SET beaten_until = ? WHERE chat_id = ? AND user_id = ?", (now() + minutes * 60, chat_id, uid))


async def _fine(conn, chat_id: int, uid: int, amount: int) -> int:
    p = await rp._load(conn, chat_id, uid)
    fine = min(p["money"], amount)
    await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, chat_id, uid))
    return fine


async def _merit(conn, chat_id: int, uid: int, delta: int) -> None:
    await conn.execute("UPDATE gang_member SET merit = MAX(0, merit + ?) WHERE chat_id = ? AND user_id = ?", (delta, chat_id, uid))


async def _result(chat_id: int, uid: int, text: str, good: bool = True, delta: int = 0) -> dict:
    return {"text": text, "delta": delta, "good": good, "state": await rp.state(chat_id, uid)}


def _plain(text: str, good: bool = True, delta: int = 0) -> dict:
    """Результат без состояния: для функций, где ответ готовится внутри блокировки (rp.state берёт её сам)."""
    return {"text": text, "delta": delta, "good": good}


# ═════════════════════ обязательные взносы ═════════════════════

async def pay_dues(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        due = me.get("dues_due") or 0
        if due == 0:
            due = now() + DUES_PERIOD
        if due - now() > DUES_WINDOW:
            raise GameError(f"Взнос принимают за 12 часов до срока: рано, осталось {GP_dur(due - now())}")
        amount = DUES[me["role"]]
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < amount:
            raise GameError(f"Взнос {amount} ₽, у тебя {p['money']}")
        p["money"] -= amount
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (amount, g["id"]))
        await conn.execute("UPDATE gang_member SET dues_due = ? WHERE chat_id = ? AND user_id = ?", (max(due, now()) + DUES_PERIOD, chat_id, uid))
        await _merit(conn, chat_id, uid, 2)
        await log_event(conn, chat_id, uid, "dues", amount)
        await conn.commit()
    return await _result(chat_id, uid, f"💰 Взнос {amount} ₽ принят в общак. Следующий через сутки, +2 авторитета.", True, -amount)


def GP_dur(s: int) -> str:
    return f"{s // 3600} ч {s % 3600 // 60} мин"


async def _dues_tick(conn) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute("UPDATE gang_member SET dues_due = ? WHERE dues_due = 0 AND user_id > 0", (now() + DUES_PERIOD,))
    cur = await conn.execute(
        """SELECT m.chat_id, m.user_id, m.gang_id, g.name AS gname, u.username FROM gang_member m JOIN gang g ON g.id = m.gang_id
           LEFT JOIN users u ON u.user_id = m.user_id AND u.chat_id = m.chat_id
           WHERE m.user_id > 0 AND g.is_ai = 0 AND m.dues_due > 0 AND m.dues_due + ? < ?""", (DUES_GRACE, now()))
    for r in [dict(x) for x in await cur.fetchall()]:
        import gangplus3
        beaten = random.random() < await gangplus3.beat_chance(conn, r["gang_id"])
        await _merit(conn, r["chat_id"], r["user_id"], -DUES_MERIT_MISS)
        await conn.execute("UPDATE gang_member SET dues_due = ? WHERE chat_id = ? AND user_id = ?", (now() + DUES_PERIOD, r["chat_id"], r["user_id"]))
        if beaten:
            await _beat(conn, r["chat_id"], r["user_id"], BEAT_MINUTES)
        await log_event(conn, r["chat_id"], r["user_id"], "dues_miss", 1)
        who = r["username"] or "участник"
        await gangwar.say(conn, r["chat_id"], f"👊 <b>{who}</b> не внёс взнос в ОПГ «{r['gname']}»: авторитет −{DUES_MERIT_MISS}" + (f", а ещё его побили: он лежит {BEAT_MINUTES} минут." if beaten else "."))


# ═════════════════════ заговор против босса ═════════════════════

async def _active_plot(conn, gid: int):
    return await _row(conn, "SELECT * FROM gang_plot WHERE gang_id = ? AND status IN ('active', 'vote') ORDER BY id DESC LIMIT 1", gid)


def _plot_steps(method: str, members: list, plotter: int) -> list:
    deps = [m for m in members if m["role"] == "deputy" and m["user_id"] > 0 and m["user_id"] != plotter]
    grs = [m for m in members if m["role"] == "grunt" and m["user_id"] > 0 and m["user_id"] != plotter]
    if method == "dirt":
        return [("Слежка за боссом", "⚡ 20, ждать 2 ч, риск 10%"), ("Добыть улики", "500 ₽, ждать 3 ч, риск 20%"), ("Предъявить банде", "голосование без залога")]
    if method == "bribe":
        return [("Подкупить замов", f"{700 * len(deps)} ₽ ({len(deps)} зам.), ждать 1 ч, каждый 20% откажется"),
                ("Подкупить холопов", f"{300 * len(grs)} ₽ ({len(grs)} чел.), ждать 2 ч, каждый 25% откажется"),
                ("Голосование", "купленные голоса уже «за»")]
    return [("Собрать бойцов", "800 ₽, ждать 1 ч, риск 15%"), ("Засада на босса", "⚡ 35, ждать 2 ч, шанс зависит от состава банды"), ("Занять трон", "захват власти без голосования")]


async def plot_start(chat_id: int, uid: int, name: str, method: str) -> dict:
    if method not in PLOTS:
        raise GameError("Неизвестный способ")
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        if me["role"] == "boss":
            raise GameError("Ты и так босс")
        if (me.get("probation_until") or 0) > now():
            raise GameError("На испытательном сроке заговоры запрещены")
        if await _active_plot(conn, g["id"]):
            raise GameError("В банде уже что-то затевается: подожди, пока это закончится")
        if await GP._coup_row(conn, g["id"]):
            raise GameError("Сейчас идут разборки")
        if now() - await GP._kget(conn, f"gplotcd:{uid}") < PLOT_CD:
            raise GameError("После провала заговорщику нужно лежать на дне 3 дня")
        if len([m for m in members if m["user_id"] > 0 and m["role"] != "boss" and m["user_id"] != uid]) < 2:
            raise GameError("Для заговора нужны хотя бы двое других, кроме босса")
        await conn.execute("INSERT INTO gang_plot (gang_id, chat_id, plotter, method, step, started, next_ts, data, status) VALUES (?, ?, ?, ?, 0, ?, 0, '{}', 'active')",
                           (g["id"], chat_id, uid, method, now()))
        await conn.commit()
    return await _result(chat_id, uid, f"🕶 Заговор «{PLOTS[method][0]}» запущен. Действуй по шагам и не попадись.")


async def _expose(conn, chat_id: int, g: dict, plot: dict, plotter_name: str, why: str) -> str:
    await conn.execute("UPDATE gang_plot SET status = 'exposed' WHERE id = ?", (plot["id"],))
    fine = await _fine(conn, chat_id, plot["plotter"], PLOT_FINE)
    await _beat(conn, chat_id, plot["plotter"], 30)
    await _merit(conn, chat_id, plot["plotter"], -30)
    await GP._kset(conn, f"gplotcd:{plot['plotter']}", now())
    await gangwar.say(conn, chat_id, f"🕵 <b>Заговор раскрыт!</b> В ОПГ «{g['name']}» {plotter_name} замышлял против босса ({why}). Штраф {fine} ₽, его побили, авторитет −30.", voice=True)
    return f"🚨 Тебя раскрыли ({why}): штраф {fine} ₽, тебя побили на 30 минут, авторитет −30."


async def plot_step(chat_id: int, uid: int, name: str) -> dict:
    res = await _plot_step(chat_id, uid, name)
    res["state"] = await rp.state(chat_id, uid)
    return res


async def _plot_step(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        plot = await _active_plot(conn, g["id"])
        if not plot or plot["plotter"] != uid:
            raise GameError("У тебя нет активного заговора")
        if plot["status"] != "active":
            raise GameError("Дело дошло до голосования: ждём решения банды")
        if plot["next_ts"] > now():
            raise GameError(f"Следующий шаг через {GP_dur(plot['next_ts'] - now())}")
        method, step = plot["method"], plot["step"]
        p = await rp._load(conn, chat_id, uid)
        rat_cover = bool(await GP._case(conn, g["id"])) and (await GP._case(conn, g["id"]))["uid"] == uid
        cover = 0.10 if rat_cover else 0.0
        data = json.loads(plot["data"] or "{}")
        deps = [m for m in members if m["role"] == "deputy" and m["user_id"] > 0 and m["user_id"] != uid]
        grs = [m for m in members if m["role"] == "grunt" and m["user_id"] > 0 and m["user_id"] != uid]
        boss = next((m for m in members if m["role"] == "boss"), None)

        def detect(chance: float) -> bool:
            return random.random() < max(0.0, chance - cover)

        text = ""
        if method == "dirt":
            if step == 0:
                rp._need(p, 20); p["energy"] -= 20
                if detect(0.10):
                    await rp._save(conn, chat_id, uid, p); msg = await _expose(conn, chat_id, g, plot, name, "заметили слежку"); await conn.commit(); return _plain(msg, False)
                await _plot_advance(conn, plot, 7200); text = "🔎 Слежка прошла незаметно. Следующий шаг через 2 часа."
            elif step == 1:
                if p["money"] < 500:
                    raise GameError("Нужно 500 ₽ на свидетелей")
                p["money"] -= 500
                if detect(0.20):
                    await rp._save(conn, chat_id, uid, p); msg = await _expose(conn, chat_id, g, plot, name, "свидетель проболтался"); await conn.commit(); return _plain(msg, False)
                await _plot_advance(conn, plot, 10800); text = "🗂 Улики на босса добыты. Следующий шаг через 3 часа."
            else:
                await _start_plot_vote(conn, chat_id, g, plot, name, {})
                text = "📢 Улики предъявлены банде: голосование открыто на 12 часов."
        elif method == "bribe":
            if step == 0:
                cost = 700 * len(deps)
                if p["money"] < cost:
                    raise GameError(f"Подкуп замов стоит {cost} ₽")
                p["money"] -= cost
                refused = [m for m in deps if random.random() < 0.20 - cover / 2]
                if refused:
                    await rp._save(conn, chat_id, uid, p); msg = await _expose(conn, chat_id, g, plot, name, "зам отказался от взятки и доложил боссу"); await conn.commit(); return _plain(msg, False)
                data["bribed"] = [m["user_id"] for m in deps]
                await conn.execute("UPDATE gang_plot SET data = ? WHERE id = ?", (json.dumps(data), plot["id"]))
                await _plot_advance(conn, plot, 3600); text = f"💵 Замы куплены ({len(deps)}). Следующий шаг через час."
            elif step == 1:
                cost = 300 * len(grs)
                if p["money"] < cost:
                    raise GameError(f"Подкуп холопов стоит {cost} ₽")
                p["money"] -= cost
                refused = [m for m in grs if random.random() < 0.25 - cover / 2]
                if refused:
                    await rp._save(conn, chat_id, uid, p); msg = await _expose(conn, chat_id, g, plot, name, "холоп отказался от взятки и доложил боссу"); await conn.commit(); return _plain(msg, False)
                data["bribed"] = data.get("bribed", []) + [m["user_id"] for m in grs]
                await conn.execute("UPDATE gang_plot SET data = ? WHERE id = ?", (json.dumps(data), plot["id"]))
                await _plot_advance(conn, plot, 7200); text = f"💵 Холопы куплены ({len(grs)}). Следующий шаг через 2 часа."
            else:
                await _start_plot_vote(conn, chat_id, g, plot, name, {str(u): "for" for u in data.get("bribed", [])})
                text = "📢 Голосование открыто: купленные голоса уже «за»."
        else:  # force
            if step == 0:
                if p["money"] < 800:
                    raise GameError("Нужно 800 ₽ на бойцов")
                p["money"] -= 800
                if detect(0.15):
                    await rp._save(conn, chat_id, uid, p); msg = await _expose(conn, chat_id, g, plot, name, "кто-то услышал про бойцов"); await conn.commit(); return _plain(msg, False)
                await _plot_advance(conn, plot, 3600); text = "👊 Бойцы собраны. Засада через час."
            elif step == 1:
                rp._need(p, 35); p["energy"] -= 35
                chance = min(0.65, max(0.15, 0.35 + 0.04 * len(grs) - 0.05 * len(deps)))
                if random.random() < chance and boss:
                    bp = await rp._load(conn, chat_id, boss["user_id"])
                    loss = bp["money"] // 5
                    await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (loss, chat_id, boss["user_id"]))
                    p["money"] += loss // 2
                    await _beat(conn, chat_id, boss["user_id"], 30)
                    await _plot_advance(conn, plot, 0)
                    await gangwar.say(conn, chat_id, f"🥊 На босса ОПГ «{g['name']}» напали из засады! Он побит и лежит 30 минут. Кто это был, пока неизвестно.")
                    text = f"🥊 Засада удалась! Босс побит, ты забрал {loss // 2} ₽. Теперь займи трон."
                else:
                    await conn.execute("UPDATE gang_plot SET status = 'failed' WHERE id = ?", (plot["id"],))
                    fine = min(p["money"], PLOT_FINE); p["money"] -= fine
                    p["jail_until"] = now() + 30 * 60; p["esc_used"] = 0
                    await _merit(conn, chat_id, uid, -20)
                    await GP._kset(conn, f"gplotcd:{uid}", now())
                    await gangwar.say(conn, chat_id, f"🛡 Нападение на босса ОПГ «{g['name']}» отбито. Нападавшего взяли: штраф {fine} ₽, 30 минут в тюрьме.")
                    await rp._save(conn, chat_id, uid, p); await conn.commit()
                    return _plain(f"💥 Засада провалилась: штраф {fine} ₽, 30 минут в тюрьме, авторитет −20.", False, -fine)
            else:
                await _take_throne(conn, chat_id, g, members, uid, name, shaky=True)
                await conn.execute("UPDATE gang_plot SET status = 'won' WHERE id = ?", (plot["id"],))
                text = "👑 Ты занял трон силой! Но власть шаткая: первые сутки банда не платит боссу долю."
        await rp._save(conn, chat_id, uid, p)
        await conn.commit()
    return _plain(text)


async def _plot_advance(conn, plot: dict, wait: int) -> None:
    await conn.execute("UPDATE gang_plot SET step = step + 1, next_ts = ? WHERE id = ?", (now() + wait, plot["id"]))


async def _start_plot_vote(conn, chat_id: int, g: dict, plot: dict, name: str, votes: dict) -> None:
    await conn.execute("UPDATE gang_plot SET status = 'vote', step = step + 1 WHERE id = ?", (plot["id"],))
    await conn.execute("INSERT INTO gang_coup (gang_id, chat_id, challenger, deposit, started, ends, votes, status, kind) VALUES (?, ?, ?, 0, ?, ?, ?, 'open', 'plot')",
                       (g["id"], chat_id, plot["plotter"], now(), now() + GP.COUP_HOURS * 3600, json.dumps(votes)))
    await gangwar.say(conn, chat_id, f"⚖️ <b>Сход в ОПГ «{g['name']}»!</b> {name} предъявил боссу претензии и просит банду сменить власть. Голосуют замы и холопы, у босса голоса нет.")


async def _take_throne(conn, chat_id: int, g: dict, members: list, uid: int, name: str, shaky: bool) -> None:
    old = next((m for m in members if m["role"] == "boss"), None)
    if old:
        await conn.execute("UPDATE gang_member SET role = 'deputy' WHERE chat_id = ? AND user_id = ?", (chat_id, old["user_id"]))
    await conn.execute("UPDATE gang_member SET role = 'boss', merit = MAX(merit, 20) WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    await conn.execute("UPDATE gang SET boss = ? WHERE id = ?", (uid, g["id"]))
    cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND role = 'deputy' AND user_id > 0 ORDER BY merit ASC", (g["id"],))
    deps = [r[0] for r in await cur.fetchall()]
    while len(deps) > G.MAX_DEPUTIES:
        await conn.execute("UPDATE gang_member SET role = 'grunt' WHERE chat_id = ? AND user_id = ?", (chat_id, deps.pop(0)))
    await GP._kset(conn, f"gcoupg:{g['id']}", now())
    if shaky:
        await GP._kset(conn, f"gshaky:{g['id']}", now() + 24 * 3600)
    await gangwar.say(conn, chat_id, f"👑 <b>Переворот!</b> В ОПГ «{g['name']}» новый главный босс — <b>{name}</b>. Прежний босс теперь зам.", voice=True)
    await rp._news(conn, chat_id, f"👑 {name} захватил власть в ОПГ «{g['name']}»")


async def plot_abort(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        plot = await _active_plot(conn, g["id"])
        if not plot or plot["plotter"] != uid or plot["status"] != "active":
            raise GameError("Нечего отменять")
        await conn.execute("UPDATE gang_plot SET status = 'failed' WHERE id = ?", (plot["id"],))
        await conn.commit()
    return await _result(chat_id, uid, "Ты свернул заговор. Деньги и время потрачены зря, зато никто не в курсе.", True)


# ═════════════════════ налёт с ролями ═════════════════════

async def rheist_plan(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        if me["role"] not in ("boss", "deputy"):
            raise GameError("Планировать налёт с ролями могут босс и замы")
        real = [m for m in members if m["user_id"] > 0]
        if not (any(m["role"] == "boss" for m in real) and any(m["role"] == "deputy" for m in real) and any(m["role"] == "grunt" for m in real)):
            raise GameError("Нужны все три роли: босс, зам и холоп")
        if await _row(conn, "SELECT * FROM gang_rheist WHERE gang_id = ? AND status = 'plan' AND ends > ?", g["id"], now()):
            raise GameError("План уже составлен: займите роли")
        wait = await GP._kget(conn, f"grh:{g['id']}") + RH_CD - now()
        if wait > 0:
            raise GameError(f"Банда отдыхает после крупного дела: ещё {GP_dur(wait)}")
        await conn.execute("INSERT INTO gang_rheist (gang_id, chat_id, created, ends, planner, cracker, lookout, status) VALUES (?, ?, ?, ?, 0, 0, 0, 'plan')", (g["id"], chat_id, now(), now() + RH_WINDOW))
        await gangwar.say(conn, chat_id, f"🕴 ОПГ «{g['name']}» готовит крупное дело с ролями. Планировщик, взломщик и шухер нужны в течение {RH_WINDOW // 60} минут.")
        await conn.commit()
    return await _result(chat_id, uid, f"🧠 План составлен. Боссу, заму и холопу нужно занять свои роли за {RH_WINDOW // 60} минут.")


async def rheist_join(chat_id: int, uid: int, name: str) -> dict:
    result = None
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        plan = await _row(conn, "SELECT * FROM gang_rheist WHERE gang_id = ? AND status = 'plan' AND ends > ?", g["id"], now())
        if not plan:
            raise GameError("Плана нет или время вышло")
        slot = RH_SLOTS[me["role"]]
        if plan[slot]:
            raise GameError("Эта роль уже занята")
        if uid in (plan["planner"], plan["cracker"], plan["lookout"]):
            raise GameError("Ты уже в деле")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, RH_ENERGY)
        p["energy"] -= RH_ENERGY
        await rp._save(conn, chat_id, uid, p)
        await conn.execute(f"UPDATE gang_rheist SET {slot} = ? WHERE id = ?", (uid, plan["id"]))
        plan[slot] = uid
        if plan["planner"] and plan["cracker"] and plan["lookout"]:
            result = await _rheist_resolve(conn, chat_id, g, members, plan)
        await conn.commit()
    if result:
        for u in (plan["planner"], plan["cracker"], plan["lookout"]):
            await gangwar.war_points(chat_id, u, "heist")
        return await _result(chat_id, uid, result["text"], result["good"], result.get("delta", 0))
    return await _result(chat_id, uid, f"{RH_NAMES[slot]}: роль занята. Ждём остальных.")


async def _rheist_resolve(conn, chat_id: int, g: dict, members: list, plan: dict) -> dict:
    team = [plan["planner"], plan["cracker"], plan["lookout"]]
    wanted = 0
    for u in team:
        wanted = max(wanted, (await rp._load(conn, chat_id, u))["wanted"])
    chance = max(0.15, 0.55 - 0.03 * wanted)
    await conn.execute("UPDATE gang SET last_heist = ? WHERE id = ?", (now(), g["id"]))
    await GP._kset(conn, f"grh:{g['id']}", now())
    n = len([m for m in members if m["user_id"] > 0])
    if random.random() < chance:
        loot = int(random.randint(*G.HEIST_LOOT) * RH_MULT * (1 + 0.1 * (n - 1)))
        await GP.split_loot(conn, chat_id, g, members, loot, -1, {"money": 0})
        for u in team:
            await _merit(conn, chat_id, u, 20)
            p = await rp._load(conn, chat_id, u)
            await conn.execute("UPDATE rp_player SET wanted = ?, wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (min(rp.MAX_WANTED, p["wanted"] + 3), now(), chat_id, u))
            await log_event(conn, chat_id, u, "rheist", loot // max(1, n))
        await conn.execute("UPDATE gang_rheist SET status = 'done' WHERE id = ?", (plan["id"],))
        await rp._news(conn, chat_id, f"🕴 ОПГ «{g['name']}» провернула дело с ролями: {loot} ₽")
        await gangwar.say(conn, chat_id, f"🕴 <b>Дело с ролями удалось!</b> ОПГ «{g['name']}» унесла {loot} ₽: босс спланировал, зам вскрыл, холоп стоял на шухере.", voice=True)
        return {"text": f"🕴 Дело с ролями удалось! Добыча {loot} ₽ поделена по ролям, авторитет +20 каждому из троих.", "good": True}
    for u in team:
        p = await rp._load(conn, chat_id, u)
        await conn.execute("UPDATE rp_player SET wanted = ?, wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (min(rp.MAX_WANTED, p["wanted"] + 2), now(), chat_id, u))
    fine = await _fine(conn, chat_id, plan["cracker"], 300)
    await conn.execute("UPDATE rp_player SET jail_until = ?, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (now() + 15 * 60, chat_id, plan["cracker"]))
    await log_event(conn, chat_id, plan["cracker"], "jail", 1)
    await conn.execute("UPDATE gang_rheist SET status = 'fail' WHERE id = ?", (plan["id"],))
    await gangwar.say(conn, chat_id, f"🚔 Дело с ролями у ОПГ «{g['name']}» сорвалось: взломщика взяли на месте, у всей тройки розыск +2.")
    return {"text": f"🚔 Дело сорвалось: у тройки розыск +2, взломщик заплатил {fine} ₽ и сидит 15 минут.", "good": False, "delta": 0}


# ═════════════════════ допрос ═════════════════════

def _claim(attr: str, d: dict, value: str) -> str:
    T = {
        ("tenure", "new"): "я в банде совсем недавно", ("tenure", "old"): "я в банде больше недели",
        ("role", "deputy"): "я зам", ("role", "grunt"): "я простой холоп",
        ("car", "car"): "у меня своя тачка", ("car", "foot"): "я хожу пешком",
        ("bar", "yes"): "я недавно был в баре", ("bar", "no"): "я трезвенник, в баре не был",
        ("wealth", "rich"): "денег у меня больше, чем у остальных", ("wealth", "poor"): "денег у меня меньше, чем у остальных",
        ("wanted", "hot"): "у меня розыск три звезды и выше", ("wanted", "clean"): "у меня розыск меньше трёх звёзд",
    }
    return T.get((attr, value), "я тут ни при чём")


async def interrogate(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        if me["role"] != "boss":
            raise GameError("Допрашивает только главный босс")
        import gangplus9
        if await gangplus9.wrecked(conn, g["id"], "basement"):
            raise GameError("Подвал разгромлен: допросы невозможны, пока не отремонтируете")
        t = _member(members, target)
        if not t or target == uid or target < 0:
            raise GameError("Выбери кого-то из своей банды")
        case = await GP._case(conn, g["id"])
        if not case:
            raise GameError("Нет открытого дела: допрашивать не о чем")
        if case["interrogations"] >= INT_MAX:
            raise GameError(f"По делу уже было {INT_MAX} допроса")
        wait = case["last_interrogation"] + INT_GAP - now()
        if wait > 0:
            raise GameError(f"Следующий допрос через {GP_dur(wait)}")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, INT_ENERGY); p["energy"] -= INT_ENERGY
        await rp._save(conn, chat_id, uid, p)
        dos = await GP._dossiers(conn, chat_id, members)
        d = dos[target]
        tname = t["username"] or "игрок"
        is_rat = case["uid"] == target
        attr = random.choice([a for a in GP.ATTRS])
        truth = GP._val(d, attr)
        slip = False
        lie = False
        if is_rat:
            r = random.random()
            if r < 0.25:
                slip = True
            elif r < 0.60:
                lie = True
        if lie:
            options = {"tenure": ["new", "old"], "role": ["deputy", "grunt"], "car": ["car", "foot"], "bar": ["yes", "no"], "wealth": ["rich", "poor"], "wanted": ["hot", "clean"]}[attr]
            value = next(v for v in options if v != truth)
        else:
            value = truth
        text = f"{tname}: «Клянусь, {_claim(attr, d, value)}»"
        extra = ""
        if slip:
            for c in await GP._clues(conn, case["id"]):
                if not c["fake"] and not GP._unlocked(c):
                    await conn.execute("UPDATE rat_clue SET unlock_ts = ? WHERE id = ?", (now(), c["id"]))
                    extra = " Он замялся и проговорился: у тебя появилась новая улика."
                    break
        await conn.execute("INSERT INTO rat_answer (case_id, ts, target, text) VALUES (?, ?, ?, ?)", (case["id"], now(), target, text))
        await conn.execute("UPDATE gang_betray SET interrogations = interrogations + 1, last_interrogation = ? WHERE id = ?", (now(), case["id"]))
        await gangwar.say(conn, chat_id, f"🕵 Босс ОПГ «{g['name']}» вызвал {tname} на допрос.")
        await conn.commit()
    return await _result(chat_id, uid, f"🕵 Допрос: {text}.{extra} Сверь ответ с досье: ложь выдаёт крысу.")


# ═════════════════════ амнистия и раскаяние ═════════════════════

async def amnesty(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        if me["role"] != "boss":
            raise GameError("Амнистию объявляет главный босс")
        wait = await GP._kget(conn, f"gam:{g['id']}") + AMNESTY_CD - now()
        if wait > 0:
            raise GameError(f"Амнистия раз в 3 дня: ещё {GP_dur(wait)}")
        if g["treasury"] < AMNESTY_COST:
            raise GameError(f"В общаке меньше {AMNESTY_COST} ₽")
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (AMNESTY_COST, g["id"]))
        ids = [m["user_id"] for m in members if m["user_id"] > 0]
        for u in ids:
            await conn.execute("UPDATE rp_player SET jail_until = 0, beaten_until = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, u))
            await conn.execute("UPDATE gang_member SET dues_due = ?, probation_until = 0 WHERE chat_id = ? AND user_id = ?", (now() + DUES_PERIOD, chat_id, u))
        await GP._kset(conn, f"gam:{g['id']}", now())
        await gangwar.say(conn, chat_id, f"🕊 <b>Амнистия в ОПГ «{g['name']}»!</b> Босс простил долги, выпустил своих из тюрьмы и снял испытательные сроки.")
        await conn.commit()
    return await _result(chat_id, uid, f"🕊 Амнистия объявлена (−{AMNESTY_COST} ₽ из общака): все свободны, сроки и долги обнулены.")


async def repent(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, _ = await G._mine(conn, chat_id, uid)
        if g:
            raise GameError("Ты уже в банде")
        case = await _row(conn, "SELECT * FROM gang_betray WHERE gang_id = ? AND uid = ? AND resolved = 1 AND ts > ? ORDER BY id DESC LIMIT 1", gid, uid, now() - 7 * 86400)
        if not case:
            raise GameError("Каяться можно только в банду, из которой тебя изгнали за последнюю неделю")
        if await _row(conn, "SELECT 1 FROM gang_repent WHERE gang_id = ? AND uid = ? AND status = 'open'", gid, uid):
            raise GameError("Прошение уже подано: жди решения босса")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < REPENT_COST:
            raise GameError(f"Раскаяние стоит {REPENT_COST} ₽ (двойной штраф), у тебя {p['money']}")
        p["money"] -= REPENT_COST
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("INSERT INTO gang_repent (gang_id, chat_id, uid, name, ts, paid, status) VALUES (?, ?, ?, ?, ?, ?, 'open')", (gid, chat_id, uid, name, now(), REPENT_COST))
        await conn.commit()
    return await _result(chat_id, uid, f"🙏 Прошение подано ({REPENT_COST} ₽ внесены). Босс решит, принять тебя или нет.", True, -REPENT_COST)


async def repent_decide(chat_id: int, uid: int, name: str, target, accept) -> dict:
    target = int(target)
    accept = bool(accept) and str(accept).lower() not in ("0", "false", "no")
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        if me["role"] != "boss":
            raise GameError("Решает главный босс")
        req = await _row(conn, "SELECT * FROM gang_repent WHERE gang_id = ? AND uid = ? AND status = 'open'", g["id"], target)
        if not req:
            raise GameError("Прошения нет")
        if accept:
            if sum(1 for m in members if m["user_id"] > 0) >= G.MAX_MEMBERS:
                raise GameError("В банде нет мест для игроков")
            await conn.execute("INSERT OR REPLACE INTO gang_member (chat_id, user_id, gang_id, joined, role, merit, dues_due, probation_until) VALUES (?, ?, ?, ?, 'grunt', 0, ?, ?)",
                               (chat_id, target, g["id"], now(), now() + DUES_PERIOD, now() + PROBATION))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (req["paid"], g["id"]))
            await conn.execute("UPDATE gang_repent SET status = 'accepted' WHERE id = ?", (req["id"],))
            await gangwar.say(conn, chat_id, f"🙏 Босс ОПГ «{g['name']}» принял раскаявшегося {req['name']} обратно. Испытательный срок: 48 часов.")
            text = f"🙏 {req['name']} принят на испытательный срок (48 часов без полной доли), {req['paid']} ₽ ушли в общак."
        else:
            half = req["paid"] // 2
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (half, chat_id, target))
            await conn.execute("UPDATE gang SET treasury = treasury + ? WHERE id = ?", (req["paid"] - half, g["id"]))
            await conn.execute("UPDATE gang_repent SET status = 'rejected' WHERE id = ?", (req["id"],))
            text = f"Прошение {req['name']} отклонено: ему вернули {half} ₽, остальное ушло в общак."
        await conn.commit()
    return await _result(chat_id, uid, text)


# ═════════════════════ тайные сходки ═════════════════════

async def meet_call(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        if me["role"] != "boss":
            raise GameError("Сходку созывает главный босс")
        if len([m for m in members if m["user_id"] > 0]) < MEET_MIN:
            raise GameError(f"Для сходки нужно хотя бы {MEET_MIN} человека в банде")
        wait = await GP._kget(conn, f"gmeet:{g['id']}") + MEET_CD - now()
        if wait > 0:
            raise GameError(f"Сходка раз в 3 дня: ещё {GP_dur(wait)}")
        await GP._kset(conn, f"gmeet:{g['id']}", now())
        await conn.execute("INSERT INTO gang_meet (gang_id, chat_id, created, ends, attendees, status) VALUES (?, ?, ?, ?, '[]', 'open')", (g["id"], chat_id, now(), now() + MEET_LEN))
        await gangwar.say(conn, chat_id, f"🕯 В ОПГ «{g['name']}» началась тайная сходка. Адрес знают только свои, сходка идёт {MEET_LEN // 3600} часа.")
        await conn.commit()
    return await _result(chat_id, uid, "🕯 Сходка объявлена. Свои могут прийти в течение 2 часов, банкет за счёт общака.")


async def meet_attend(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        meet = await _row(conn, "SELECT * FROM gang_meet WHERE gang_id = ? AND status = 'open' AND ends > ?", g["id"], now())
        if not meet:
            raise GameError("Сходки сейчас нет")
        att = json.loads(meet["attendees"] or "[]")
        if uid in att:
            raise GameError("Ты уже на сходке")
        att.append(uid)
        await conn.execute("UPDATE gang_meet SET attendees = ? WHERE id = ?", (json.dumps(att), meet["id"]))
        p = await rp._load(conn, chat_id, uid)
        p["energy"] = min(rp.max_energy(p["home"]), p["energy"] + MEET_ENERGY)
        got = min(MEET_BANQUET, g["treasury"])
        if got:
            p["money"] += got
            await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (got, g["id"]))
        await rp._save(conn, chat_id, uid, p)
        feast = int(await gangwar.kget(conn, f"p4feast:{g['id']}") or 0) > now()
        await _merit(conn, chat_id, uid, MEET_MERIT + (5 if feast else 0))
        await log_event(conn, chat_id, uid, "meet", 1)
        await conn.commit()
    return await _result(chat_id, uid, f"🕯 Ты на сходке: +{MEET_ENERGY} энергии, +{MEET_MERIT} авторитета" + (f", банкет {got} ₽." if got else ", в общаке на банкет пусто."))


async def _meet_tick(conn) -> None:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT m.*, g.name AS gname FROM gang_meet m JOIN gang g ON g.id = m.gang_id WHERE m.status = 'open' AND m.ends <= ?", (now(),))
    for m in [dict(r) for r in await cur.fetchall()]:
        await conn.execute("UPDATE gang_meet SET status = 'done' WHERE id = ?", (m["id"],))
        att = json.loads(m["attendees"] or "[]")
        cur2 = await conn.execute("SELECT COUNT(*) FROM gang_member WHERE gang_id = ? AND user_id > 0", (m["gang_id"],))
        total = (await cur2.fetchone())[0]
        if att:
            boss = await _row(conn, "SELECT boss FROM gang WHERE id = ?", m["gang_id"])
            if boss:
                await _merit(conn, m["chat_id"], boss["boss"], 5 * len(att))
        await gangwar.say(conn, m["chat_id"], f"🕯 Сходка ОПГ «{m['gname']}» закончилась: пришло {len(att)} из {total}. Кто именно, знает только босс.")


# ═════════════════════ недельный итог ═════════════════════

async def _weekly_tick(conn) -> None:
    n = now_local()
    if n.weekday() != 6 or (n.hour, n.minute) < (20, 30):
        return
    week = f"{n.isocalendar()[0]}-{n.isocalendar()[1]}"
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT id, chat_id, name, treasury FROM gang WHERE is_ai = 0")
    for g in [dict(r) for r in await cur.fetchall()]:
        key = f"gweek:{g['id']}:{week}"
        if await GP._kget(conn, key):
            continue
        await GP._kset(conn, key, 1)
        text = await _weekly_text(conn, g)
        if text:
            await gangwar.say(conn, g["chat_id"], text)


async def _weekly_text(conn, g: dict) -> Optional[str]:
    since = now() - 7 * 86400
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT m.user_id, m.role, m.merit, u.username FROM gang_member m LEFT JOIN users u ON u.user_id = m.user_id AND u.chat_id = m.chat_id WHERE m.gang_id = ? AND m.user_id > 0", (g["id"],))
    members = [dict(r) for r in await cur.fetchall()]
    if len(members) < 2:
        return None
    names = {m["user_id"]: m["username"] or "участник" for m in members}
    cur = await conn.execute("SELECT uid, kind, SUM(amount) AS s, COUNT(*) AS c FROM gang_log WHERE gang_id = ? AND ts >= ? GROUP BY uid, kind", (g["id"], since))
    rows = [dict(r) for r in await cur.fetchall()]
    def top(kinds, field):
        agg = {}
        for r in rows:
            if r["kind"] in kinds and r["uid"] in names:
                agg[r["uid"]] = agg.get(r["uid"], 0) + r[field]
        return max(agg.items(), key=lambda x: x[1]) if agg else None
    earn = sum(r["s"] for r in rows if r["kind"] in ("heist", "crime", "rheist"))
    best = top(("heist", "crime", "rheist"), "s")
    jail = top(("jail",), "c")
    dues = sum(r["s"] for r in rows if r["kind"] == "dues")
    missed = sum(r["c"] for r in rows if r["kind"] == "dues_miss")
    donors = top(("donate",), "s")
    meets = top(("meet",), "c")
    top_merit = max(members, key=lambda m: m["merit"])
    lines = [f"📅 <b>Итоги недели ОПГ «{g['name']}»</b>", f"💼 Заработали на делах: {earn} ₽"]
    if best:
        lines.append(f"🥇 Больше всех принёс: {names[best[0]]} ({best[1]} ₽)")
    if jail:
        lines.append(f"🪑 Чаще всех сидел: {names[jail[0]]} ({jail[1]} раз)")
    lines.append(f"💰 Взносы: собрано {dues} ₽, просрочек {missed}. В общаке сейчас {g['treasury']} ₽")
    if donors:
        lines.append(f"🎁 Щедрее всех в общак: {names[donors[0]]} ({donors[1]} ₽)")
    if meets:
        lines.append(f"🕯 Самый верный (сходки): {names[meets[0]]}")
    lines.append(f"👑 Авторитет №1: {names[top_merit['user_id']]} ({top_merit['merit']})")
    return "\n".join(lines)


# ═════════════════════ двойной агент полиции ═════════════════════

async def agent_enlist(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        p = await rp._load(conn, chat_id, uid)
        if p["agent"]:
            raise GameError("Ты уже работаешь на полицию")
        if p["wanted"] > 3:
            raise GameError("С таким розыском полиция не вербует: снизь до 3 звёзд")
        await conn.execute("UPDATE rp_player SET agent = 1 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        await conn.commit()
    return await _result(chat_id, uid, "🕶 Тебя завербовали как информатора. Теперь можно делать донесения, но банда ничего не знает.")


async def agent_quit(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        p = await rp._load(conn, chat_id, uid)
        if not p["agent"]:
            raise GameError("Ты не агент")
        await conn.execute("UPDATE rp_player SET agent = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        await conn.commit()
    return await _result(chat_id, uid, "Ты вышел из программы информаторов. Звание и очки остаются при тебе.")


async def agent_report(chat_id: int, uid: int, name: str) -> dict:
    res = await _agent_report(chat_id, uid, name)
    res["state"] = await rp.state(chat_id, uid)
    return res


async def _agent_report(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members, me = await _need_gang(conn, chat_id, uid)
        if me["role"] == "boss":
            raise GameError("Босс не стучит на собственную банду. Передай власть и уходи")
        p = await rp._load(conn, chat_id, uid)
        if not p["agent"]:
            raise GameError("Сначала завербуйся в информаторы")
        wait = p["agent_ts"] + AGENT_GAP - now()
        if wait > 0:
            raise GameError(f"Следующее донесение через {GP_dur(wait)}")
        case = await GP._case(conn, g["id"])
        if case and case["uid"] != uid:
            raise GameError("Банду уже трясёт другое расследование: не лезь")
        pay = random.randint(*AGENT_PAY)
        p["money"] += pay
        p["cop_rep"] += 2
        p["agent_ts"] = now()
        if random.random() < AGENT_EXPOSE:
            p["cop_rep"] = max(0, p["cop_rep"] - 7)
            p["agent"] = 0
            fine = min(p["money"], 400); p["money"] -= fine
            p["jail_until"] = now() + 20 * 60; p["esc_used"] = 0
            await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            await rp._save(conn, chat_id, uid, p)
            await gangwar.say(conn, chat_id, f"🕶 <b>Двойной агент вскрыт!</b> {name} стучал на ОПГ «{g['name']}». Изгнан, штраф {fine} ₽. Полиция от него отреклась.", voice=True)
            await rp._news(conn, chat_id, f"🕶 {name} оказался агентом полиции и изгнан из «{g['name']}»")
            await conn.commit()
            return _plain(f"🕶 Тебя вычислили: изгнан, штраф {fine} ₽, 20 минут в тюрьме, полиция тебя не знает (очки −5).", False, -fine)
        for m in members:
            if m["user_id"] > 0 and m["user_id"] != uid:
                op = await rp._load(conn, chat_id, m["user_id"])
                await conn.execute("UPDATE rp_player SET wanted = ?, wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (min(rp.MAX_WANTED, op["wanted"] + 1), now(), chat_id, m["user_id"]))
        await conn.execute("UPDATE gang SET last_heist = ? WHERE id = ?", (now(), g["id"]))
        if not case:
            cur = await conn.execute("INSERT INTO gang_betray (gang_id, chat_id, uid, ts, reward) VALUES (?, ?, ?, ?, ?)", (g["id"], chat_id, uid, now(), pay))
            await GP._make_clues(conn, cur.lastrowid, uid, await GP._dossiers(conn, chat_id, members))
            await gangwar.say(conn, chat_id, f"🚔 <b>Информатор в банде!</b> В ОПГ «{g['name']}» завёлся крот: у участников розыск +1, налёты на 30 минут отложены. Босс открыл дело.")
        else:
            await conn.execute("UPDATE gang_betray SET leaks = leaks + 1, reward = reward + ? WHERE id = ?", (pay, case["id"]))
        await rp._save(conn, chat_id, uid, p)
        await _merit(conn, chat_id, uid, 0)
        await log_event(conn, chat_id, uid, "agent", pay)
        await conn.commit()
    return _plain(f"🕶 Донесение принято: полиция заплатила {pay} ₽, твои очки полицейского +2.", True, pay)


# ═════════════════════ данные для интерфейса ═════════════════════

async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    m = out.get("mine")
    p = await rp._load(conn, chat_id, uid) if g or True else None
    out["cop"] = {"rep": p["cop_rep"], "rank": cop_rank(p["cop_rep"]), "agent": bool(p["agent"]),
                  "report_wait": max(0, p["agent_ts"] + AGENT_GAP - now()), "next": next((n for n, _ in COP_RANKS if n > p["cop_rep"]), 0)}
    if not g or not m:
        # изгнанные крысы могут покаяться
        cur = await conn.execute(
            """SELECT b.gang_id, g.name FROM gang_betray b JOIN gang g ON g.id = b.gang_id
               WHERE b.chat_id = ? AND b.uid = ? AND b.resolved = 1 AND b.ts > ? ORDER BY b.id DESC LIMIT 3""", (chat_id, uid, now() - 7 * 86400))
        out["repent"] = {"options": [{"id": r[0], "name": r[1]} for r in await cur.fetchall()], "cost": REPENT_COST}
        return
    _, members = await G._mine(conn, chat_id, uid)
    me = _member(members, uid)
    # взносы
    due = me.get("dues_due") or 0
    amount = DUES[me["role"]]
    left = max(0, due - now()) if due else DUES_PERIOD
    m["dues"] = {"amount": amount, "left": left, "can_pay": bool(due) and left <= DUES_WINDOW, "overdue": bool(due) and due < now(),
                 "window": DUES_WINDOW}
    m["beaten_left"] = max(0, p["beaten_until"] - now())
    if my_role == "boss":
        for x in m["members"]:
            mm = _member(members, x["id"])
            x["dues_late"] = bool(mm and (mm.get("dues_due") or 0) and mm["dues_due"] < now())
            x["probation"] = bool(mm and (mm.get("probation_until") or 0) > now())
    # заговор
    plot = await _active_plot(conn, g["id"])
    m["plot"] = None
    m["plot_methods"] = [{"key": k, "title": t, "desc": d} for k, (t, d) in PLOTS.items()] if my_role != "boss" and not plot and (me.get("probation_until") or 0) <= now() else []
    if plot and plot["plotter"] == uid:
        steps = _plot_steps(plot["method"], members, uid)
        m["plot"] = {"method": PLOTS[plot["method"]][0], "status": plot["status"], "step": plot["step"],
                     "wait": max(0, plot["next_ts"] - now()),
                     "steps": [{"title": t, "desc": d, "done": i < plot["step"]} for i, (t, d) in enumerate(steps)]}
    m["plot_hint"] = bool(plot and my_role == "boss" and plot["step"] >= 1)
    m["plot_cd"] = max(0, await GP._kget(conn, f"gplotcd:{uid}") + PLOT_CD - now())
    # налёт с ролями
    rh = await _row(conn, "SELECT * FROM gang_rheist WHERE gang_id = ? AND status = 'plan' AND ends > ?", g["id"], now())
    cd = max(0, await GP._kget(conn, f"grh:{g['id']}") + RH_CD - now())
    slot = RH_SLOTS[my_role]
    names = {x["user_id"]: x["username"] or "игрок" for x in members}
    m["rheist"] = {"plan": bool(rh), "left": max(0, rh["ends"] - now()) if rh else 0, "cd": cd,
                   "can_plan": my_role in ("boss", "deputy") and not rh and cd == 0,
                   "can_join": bool(rh) and not rh[slot] and uid not in (rh["planner"], rh["cracker"], rh["lookout"]),
                   "slots": [{"key": k, "title": RH_NAMES[k], "who": names.get(rh[k], "") if rh and rh[k] else ""} for k in ("planner", "cracker", "lookout")],
                   "energy": RH_ENERGY, "mult": RH_MULT}
    # допрос и дело
    if my_role == "boss" and m.get("rat"):
        case = await GP._case(conn, g["id"])
        if case:
            cur = await conn.execute("SELECT text FROM rat_answer WHERE case_id = ? ORDER BY id", (case["id"],))
            m["rat"]["answers"] = [r[0] for r in await cur.fetchall()]
            iw = case["last_interrogation"] + INT_GAP - now()
            m["rat"]["can_interrogate"] = case["interrogations"] < INT_MAX and iw <= 0
            m["rat"]["int_left"] = INT_MAX - case["interrogations"]
            m["rat"]["int_wait"] = max(0, iw)
            m["rat"]["int_energy"] = INT_ENERGY
    # амнистия и раскаяние
    aw = max(0, await GP._kget(conn, f"gam:{g['id']}") + AMNESTY_CD - now())
    m["amnesty"] = {"can": my_role == "boss" and aw == 0, "wait": aw, "cost": AMNESTY_COST}
    if my_role == "boss":
        cur = await conn.execute("SELECT uid, name, paid FROM gang_repent WHERE gang_id = ? AND status = 'open'", (g["id"],))
        m["repents"] = [{"id": r[0], "name": r[1], "paid": r[2]} for r in await cur.fetchall()]
    m["probation_left"] = max(0, (me.get("probation_until") or 0) - now())
    # сходка
    meet = await _row(conn, "SELECT * FROM gang_meet WHERE gang_id = ? AND status = 'open' AND ends > ?", g["id"], now())
    mw = max(0, await GP._kget(conn, f"gmeet:{g['id']}") + MEET_CD - now())
    att = json.loads(meet["attendees"] or "[]") if meet else []
    m["meet"] = {"open": bool(meet), "left": max(0, meet["ends"] - now()) if meet else 0, "came": uid in att, "count": len(att),
                 "can_call": my_role == "boss" and not meet and mw == 0, "wait": mw if not meet else 0,
                 "attendees": [names.get(a, "") for a in att] if my_role == "boss" else []}


# ═════════════════════ фоновые задачи ═════════════════════

async def tick(conn) -> None:
    try:
        await _dues_tick(conn)
        await _meet_tick(conn)
        await _weekly_tick(conn)
        cur = await conn.execute("SELECT * FROM gang_rheist WHERE status = 'plan' AND ends <= ?", (now(),))
        conn.row_factory = aiosqlite.Row
        for plan in [dict(r) for r in await cur.fetchall()]:
            await conn.execute("UPDATE gang_rheist SET status = 'cancel' WHERE id = ?", (plan["id"],))
            for u in (plan["planner"], plan["cracker"], plan["lookout"]):
                if u:
                    await rp._load(conn, plan["chat_id"], u)
                    await conn.execute("UPDATE rp_player SET energy = MIN(energy + ?, 150) WHERE chat_id = ? AND user_id = ?", (RH_ENERGY, plan["chat_id"], u))
    except Exception:
        log.exception("Сбой фоновых задач ОПГ-плюс 2")
