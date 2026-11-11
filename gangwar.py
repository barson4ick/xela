"""Войны ОПГ, ИИ-банда «Бабушкин синдикат», районы города и предательство."""
import logging
import random
import time

import aiosqlite

import gang as G
import rp
from config import DB_PATH
from rooms import GameError

log = logging.getLogger(__name__)

AI_NAME = "Бабушкин синдикат"
WAR_HOURS = 12
WAR_COOLDOWN = 6 * 3600
TRIBUTE_PCT = 0.08
TRIBUTE_CAP = 700
AI_REWARD = 3000
RAID_ENERGY = 20
RAID_GAP = 10 * 60
BETRAY_GAP = 8 * 3600
BETRAY_GANG_GAP = 3 * 3600
BETRAY_MIN_AGE = 3600
ACCUSE_WINDOW = 48 * 3600
AI_TICK = 1800
POINTS = {"crime": 3, "heist": 25, "duel": 5, "raid": 10}

# (ключ, иконка, название, ₽ в час каждому участнику банды-владельца)
TERRITORIES = [
    ("market", "🛒", "Рынок", 40), ("port", "⚓", "Порт", 60), ("station", "🚉", "Вокзал", 50),
    ("garages", "🔧", "Гаражи", 35), ("embank", "🌉", "Набережная", 45), ("elev", "🏭", "Элеватор", 55),
    ("bath", "🧖", "Баня", 45), ("tire", "🛞", "Шиномонтаж", 40), ("club", "🪩", "Ночной клуб", 65), ("dacha", "🏡", "Дачи", 30),
]
TERR = {t[0]: t for t in TERRITORIES}
AI_START_TERR = ("market", "port")

AI_LINES = [
    "🧓 Баба Валя заняла лавочку у подъезда и теперь знает всё про всех",
    "🧓 Бабушкин синдикат собрал дань с тёти Нины: три банки огурцов и компот",
    "🧓 Тётя Зина из «Бабушкиного синдиката» пустила слух, что у соседа новая машина. Сосед прячется",
    "🧓 Дед Мазай из синдиката перегородил дорогу на пять минут: «Тут теперь платная проходная»",
    "🧓 Племянник Гена из «Бабушкиного синдиката» снова не вернул банку. Баба Валя недовольна",
    "🧓 Лидка с рынка из синдиката подняла цену на семечки вдвое. Район стонет, но платит",
]
AI_WAR_DECLARED = [
    "Ой, внучки, войну мне объявили? Ну-ну. Сидите, не вставайте, бабушка сама придёт и всех накормит, и всех накажет!",
    "Милые мои, вы на кого руку подняли? Я эту лавочку тридцать лет держу. Готовьте валерьянку!",
]
AI_WIN = [
    "Вот так-то, внучки. Бабушка сказала, бабушка сделала. Дань на стол, и без обид!",
    "Я же говорила: молодёжь, не лезьте. Несите деньги и пирожки, и все свободны.",
]
AI_LOSE = [
    "Ох, батюшки, обидели старушку! Ладно, ваша взяла. Но я всё запомнила, ироды.",
    "Ну, победили. Давление у меня скачет. Только это ещё не конец, внучки!",
]


def now() -> int:
    return int(time.time())


async def kget(conn, key: str):
    cur = await conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
    r = await cur.fetchone()
    return r[0] if r else None


async def kset(conn, key: str, val: str) -> None:
    await conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, val))


async def say(conn, chat_id: int, text: str, voice: bool = False) -> None:
    """Кладёт сообщение в очередь: бот отправит его в чат (так пишут и мини-апп, и бот)."""
    await conn.execute("INSERT INTO outbox (chat_id, text, voice, ts) VALUES (?, ?, ?, ?)", (chat_id, text, 0, now()))


async def say_ai(conn, chat_id: int, text: str) -> None:
    """Служебные сообщения ИИ: по умолчанию в чат не идут, чтобы не спамить."""
    import config
    if getattr(config, "AI_CHAT", False):
        await say(conn, chat_id, text)


# ──────────── ИИ-банда и районы ────────────

async def ensure_ai(conn, chat_id: int) -> int:
    cur = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
    row = await cur.fetchone()
    if row:
        return row[0]
    t = now()
    cur = await conn.execute("INSERT INTO gang (chat_id, name, boss, created, is_ai) VALUES (?, ?, ?, ?, 1)", (chat_id, AI_NAME, G.NPC_BOSS, t))
    gid = cur.lastrowid
    for uid, role in G.NPC_ROLES:
        await conn.execute("INSERT OR IGNORE INTO gang_member (chat_id, user_id, gang_id, joined, role) VALUES (?, ?, ?, ?, ?)", (chat_id, uid, gid, t, role))
    for key, *_ in TERRITORIES:
        await conn.execute("INSERT OR IGNORE INTO territory (chat_id, key, owner, last_pay) VALUES (?, ?, 0, ?)", (chat_id, key, t))
    for key in AI_START_TERR:
        await conn.execute("UPDATE territory SET owner = ? WHERE chat_id = ? AND key = ?", (gid, chat_id, key))
    await rp._news(conn, chat_id, f"🧓 В городе появилась ОПГ «{AI_NAME}» во главе с Бабой Валей. Захвачены Рынок и Порт")
    await say(conn, chat_id, f"🧓 <b>Внимание, район!</b> На лавочке у подъезда обосновалась ОПГ <b>«{AI_NAME}»</b>. Главная — <b>Баба Валя</b>, "
                              "с ней зам Тётя Зина и Дед Мазай, плюс холопы. Контролируют Рынок и Порт. Хотите отбить? Основывайте свои банды и объявляйте войну!")
    return gid


async def _active_war(conn, gid: int):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang_war WHERE status = 'active' AND (a = ? OR b = ?)", (gid, gid))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _gang_row(conn, gid: int):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang WHERE id = ?", (gid,))
    r = await cur.fetchone()
    return dict(r) if r else None


async def _real_members(conn, gid: int) -> list:
    cur = await conn.execute("SELECT user_id FROM gang_member WHERE gang_id = ? AND user_id > 0", (gid,))
    return [r[0] for r in await cur.fetchall()]


async def _pay_all(conn, chat_id: int, uids: list, amount: int) -> None:
    for u in uids:
        await rp._load(conn, chat_id, u)
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (amount, chat_id, u))


# ──────────── для интерфейса ────────────

async def extras(conn, chat_id: int, uid: int, g, my_role: str) -> dict:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        """SELECT g.id, g.name, g.is_ai, g.wins, g.losses, g.color1, g.color2, g.emblem, (SELECT COUNT(*) FROM gang_member m WHERE m.gang_id = g.id) AS n
           FROM gang g WHERE g.chat_id = ? ORDER BY g.is_ai DESC, n DESC""", (chat_id,))
    gangs = [dict(r) for r in await cur.fetchall()]
    cur = await conn.execute("SELECT key, owner FROM territory WHERE chat_id = ?", (chat_id,))
    owners = {r[0]: r[1] for r in await cur.fetchall()}
    names = {x["id"]: x["name"] for x in gangs}
    terr = [{"key": k, "icon": i, "name": n, "income": inc, "owner": owners.get(k, 0), "owner_name": names.get(owners.get(k, 0), "")} for k, i, n, inc in TERRITORIES]
    for x in gangs:
        x["terr"] = "".join(t["icon"] for t in terr if t["owner"] == x["id"])
        x["mine"] = bool(g and g["id"] == x["id"])
    out = {"gangs": gangs, "terr": terr, "war": None, "pending_betrayal": False}
    if not g:
        return out
    war = await _active_war(conn, g["id"])
    t = now()
    if war:
        mine_a = war["a"] == g["id"]
        enemy = war["b"] if mine_a else war["a"]
        raid_ts = int(await kget(conn, f"gwraid:{uid}") or 0)
        out["war"] = {"id": war["id"], "enemy": names.get(enemy, "?"), "enemy_id": enemy,
                      "mine": war["score_a"] if mine_a else war["score_b"], "theirs": war["score_b"] if mine_a else war["score_a"],
                      "left": max(0, war["ends"] - t), "raid_left": max(0, raid_ts + RAID_GAP - t), "energy": RAID_ENERGY}
    cd = int(await kget(conn, f"gwcd:{g['id']}") or 0)
    out["war_cd"] = max(0, cd - t)
    if my_role == "boss":
        cur = await conn.execute("SELECT 1 FROM gang_betray WHERE gang_id = ? AND resolved = 0 AND ts > ?", (g["id"], t - ACCUSE_WINDOW))
        out["pending_betrayal"] = bool(await cur.fetchone())
    return out


# ──────────── очки войны ────────────

async def war_points(chat_id: int, uid: int, kind: str, pts: int = 0) -> None:
    pts = pts or POINTS.get(kind, 0)
    if not pts:
        return
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute("SELECT gang_id FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            r = await cur.fetchone()
            if not r:
                return
            t = now()
            await conn.execute("UPDATE gang_member SET merit = merit + ? WHERE chat_id = ? AND user_id = ?", (pts, chat_id, uid))
            await conn.execute("UPDATE gang SET last_act = ? WHERE id = ?", (t, r[0]))
            await conn.execute("UPDATE gang_war SET score_a = score_a + ? WHERE chat_id = ? AND status = 'active' AND a = ? AND ends > ?", (pts, chat_id, r[0], t))
            await conn.execute("UPDATE gang_war SET score_b = score_b + ? WHERE chat_id = ? AND status = 'active' AND b = ? AND ends > ?", (pts, chat_id, r[0], t))
            await conn.commit()
    except Exception:
        log.exception("Не удалось записать очки войны")


# ──────────── действия игроков ────────────

async def declare(chat_id: int, uid: int, name: str, gid) -> dict:
    gid = int(gid)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        if next(m["role"] for m in members if m["user_id"] == uid) != "boss":
            raise GameError("Объявлять войну может только главный босс")
        if gid == g["id"]:
            raise GameError("Воевать с самими собой? Это уже семейные разборки")
        enemy = await _gang_row(conn, gid)
        if not enemy or enemy["chat_id"] != chat_id:
            raise GameError("Такой банды нет")
        if len(members) < 2:
            raise GameError("Для войны нужно хотя бы двое в банде")
        if not enemy["is_ai"] and len(await _real_members(conn, gid)) < 2:
            raise GameError("В банде врага меньше двух человек: это не война, а травля")
        if await _active_war(conn, g["id"]):
            raise GameError("Твоя банда уже воюет")
        if await _active_war(conn, gid):
            raise GameError("Эта банда уже в войне")
        left = int(await kget(conn, f"gwcd:{g['id']}") or 0) - now()
        if left > 0:
            raise GameError(f"Банде нужно отдышаться после прошлой войны: ещё {left // 3600 + 1} ч.")
        t = now()
        import gangplus3
        await gangplus3.check_war(conn, g["id"], gid)
        await conn.execute("INSERT INTO gang_war (chat_id, a, b, started, ends) VALUES (?, ?, ?, ?, ?)", (chat_id, g["id"], gid, t, t + WAR_HOURS * 3600))
        await rp._news(conn, chat_id, f"⚔️ ОПГ «{g['name']}» объявила войну ОПГ «{enemy['name']}»")
        await gangplus3.on_war(conn, chat_id, g["id"], gid)
        if enemy["is_ai"] and enemy["name"] == AI_NAME:
            await say(conn, chat_id, f"⚔️ <b>Война!</b> «{g['name']}» бросила вызов «{AI_NAME}». Сутки на то, чтобы доказать, кто в районе хозяин.\n\n"
                                      f"🧓 <i>{random.choice(AI_WAR_DECLARED)}</i>", voice=False)
        elif enemy["is_ai"]:
            await say(conn, chat_id, f"⚔️ <b>Война!</b> «{g['name']}» бросила вызов «{enemy['name']}». Сутки на то, чтобы доказать, кто в районе хозяин.")
        else:
            await say(conn, chat_id, f"⚔️ <b>Война ОПГ!</b> «{g['name']}» объявила войну «{enemy['name']}». Сутки на то, чтобы доказать, кто в районе хозяин. "
                                      "Очки идут за дела, налёты, дуэли и рейды на врага. Победитель получает дань и район.")
        await conn.commit()
    return {"text": f"⚔️ Война против «{enemy['name']}» объявлена! Длится {WAR_HOURS} ч. Очки: дела +3, налёт банды +25, дуэль +5, рейд на врага +10.",
            "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def raid(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        war = await _active_war(conn, g["id"])
        if not war or war["ends"] <= now():
            raise GameError("Война не идёт, рейдить некого")
        gap = int(await kget(conn, f"gwraid:{uid}") or 0) + RAID_GAP - now()
        if gap > 0:
            raise GameError(f"Рейд можно раз в {RAID_GAP // 60} мин, подожди ещё {gap // 60 + 1} мин.")
        p = await rp._load(conn, chat_id, uid)
        rp._need(p, RAID_ENERGY)
        p["energy"] -= RAID_ENERGY
        mine_a = war["a"] == g["id"]
        enemy_id = war["b"] if mine_a else war["a"]
        enemy = await _gang_row(conn, enemy_id)
        chance = min(0.8, 0.5 + 0.04 * len(members) - 0.03 * p["wanted"])
        col_me, col_en = ("score_a", "score_b") if mine_a else ("score_b", "score_a")
        await kset(conn, f"gwraid:{uid}", str(now()))
        if random.random() < chance:
            loot = 0
            if not enemy["is_ai"]:
                victims = await _real_members(conn, enemy_id)
                random.shuffle(victims)
                for v in victims:
                    vp = await rp._load(conn, chat_id, v)
                    if vp["money"] >= 50:
                        loot = min(150, int(vp["money"] * 0.1))
                        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (loot, chat_id, v))
                        break
            else:
                loot = random.randint(60, 140)
            p["money"] += loot
            p["wanted"] = min(rp.MAX_WANTED, p["wanted"] + 1)
            p["wanted_ts"] = now()
            await conn.execute(f"UPDATE gang_war SET {col_me} = {col_me} + ? WHERE id = ?", (POINTS["raid"], war["id"]))
            await rp._news(conn, chat_id, f"⚔️ {name} из «{g['name']}» устроил рейд на «{enemy['name']}»")
            res = {"text": f"⚔️ Рейд удался! +{POINTS['raid']} очков банде, добыча {loot} ₽.", "delta": loot, "good": True}
        else:
            fine = min(p["money"], 50)
            p["money"] -= fine
            p["jail_until"] = now() + 10 * 60
            p["esc_used"] = 0
            await conn.execute(f"UPDATE gang_war SET {col_en} = {col_en} + 4 WHERE id = ?", (war["id"],))
            await rp._news(conn, chat_id, f"🚔 {name} из «{g['name']}» попался на рейде против «{enemy['name']}»")
            res = {"text": f"🚔 Рейд провален: штраф {fine} ₽, 10 минут в тюрьме, врагу +4 очка.", "delta": -fine, "good": False}
        await rp._save(conn, chat_id, uid, p)
        await conn.commit()
    res["state"] = await rp.state(chat_id, uid)
    return res


async def betray(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        me = next(m for m in members if m["user_id"] == uid)
        if me["role"] == "boss":
            raise GameError("Босс не может сдать сам себя. Если надоело, передай власть и уходи")
        cur = await conn.execute("SELECT joined FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        if now() - (await cur.fetchone())[0] < BETRAY_MIN_AGE:
            raise GameError("Сначала пробейся в доверие хотя бы на час: свежих предателей полиция не уважает")
        if now() - int(await kget(conn, f"gbt:{uid}") or 0) < BETRAY_GAP:
            raise GameError("Ты уже сдавал банду сегодня, не наглей")
        if now() - int(await kget(conn, f"gbtg:{g['id']}") or 0) < BETRAY_GANG_GAP:
            raise GameError("Полиция уже недавно навещала эту банду, наводку не примут")
        p = await rp._load(conn, chat_id, uid)
        await kset(conn, f"gbt:{uid}", str(now()))
        await kset(conn, f"gbtg:{g['id']}", str(now()))
        others = [m["user_id"] for m in members if m["user_id"] != uid and m["user_id"] > 0]
        for u in others:
            op = await rp._load(conn, chat_id, u)
            await conn.execute("UPDATE rp_player SET wanted = ?, wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (min(rp.MAX_WANTED, op["wanted"] + 2), now(), chat_id, u))
        await conn.execute("UPDATE gang SET last_heist = ? WHERE id = ?", (now(), g["id"]))
        if random.random() < 0.2:
            # провал: предателя вычислили сразу
            fine = min(p["money"], 400)
            p["money"] -= fine
            p["jail_until"] = now() + 20 * 60
            p["esc_used"] = 0
            await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            await rp._save(conn, chat_id, uid, p)
            await rp._news(conn, chat_id, f"🐀 {name} попытался сдать ОПГ «{g['name']}», но его вычислили на месте. Изгнан, штраф {fine} ₽")
            await say(conn, chat_id, f"🐀 <b>Крыса найдена!</b> {name} пытался сдать «{g['name']}» полиции, но попался с поличным. Изгнан из банды, штраф {fine} ₽, 20 минут в тюрьме.")
            await conn.commit()
            return {"text": f"🐀 Провал: тебя вычислили сразу. Изгнан, штраф {fine} ₽, 20 минут в тюрьме.", "delta": -fine, "good": False, "state": await rp.state(chat_id, uid)}
        reward = random.randint(800, 1500)
        p["money"] += reward
        await rp._save(conn, chat_id, uid, p)
        await conn.execute("INSERT INTO gang_betray (gang_id, chat_id, uid, ts) VALUES (?, ?, ?, ?)", (g["id"], chat_id, uid, now()))
        await rp._news(conn, chat_id, f"🚔 Полиция получила анонимную наводку на ОПГ «{g['name']}»: у участников розыск +2, налёт отложен")
        await say(conn, chat_id, f"🚔 <b>Анонимная наводка!</b> На ОПГ «{g['name']}» поступил донос. У всех участников розыск +2, налёт заблокирован на час. "
                                  "Босс может провести расследование и найти крысу. Кто это был, не знает никто... пока.")
        await conn.commit()
    return {"text": f"🐀 Наводка принята. Полиция заплатила тебе {reward} ₽. Тебя никто не видел. Пока.", "delta": reward, "good": True, "state": await rp.state(chat_id, uid)}


async def accuse(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await G._mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        if next(m["role"] for m in members if m["user_id"] == uid) != "boss":
            raise GameError("Расследование ведёт только главный босс")
        t = next((m for m in members if m["user_id"] == target), None)
        if not t or target == uid:
            raise GameError("Выбери кого-то из своей банды")
        cur = await conn.execute("SELECT id, uid FROM gang_betray WHERE gang_id = ? AND resolved = 0 AND ts > ? ORDER BY id DESC LIMIT 1", (g["id"], now() - ACCUSE_WINDOW))
        bt = await cur.fetchone()
        if not bt:
            raise GameError("Нет нераскрытых доносов: расследовать нечего")
        wait = int(await kget(conn, f"gacc:{g['id']}") or 0) + 1800 - now()
        if wait > 0:
            raise GameError(f"После ошибки нужно остыть: ещё {wait // 60 + 1} мин.")
        tname = t["username"] or "игрок"
        if bt[1] == target:
            await conn.execute("UPDATE gang_betray SET resolved = 1 WHERE id = ?", (bt[0],))
            tp = await rp._load(conn, chat_id, target)
            fine = min(tp["money"], 1500)
            tp["money"] -= fine
            tp["jail_until"] = now() + 30 * 60
            tp["esc_used"] = 0
            await rp._save(conn, chat_id, target, tp)
            await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, target))
            await rp._news(conn, chat_id, f"🐀 Босс «{g['name']}» вычислил крысу: {tname}. Изгнан, штраф {fine} ₽")
            await say(conn, chat_id, f"🐀 <b>Крыса найдена!</b> Босс «{g['name']}» провёл расследование: донос написал <b>{tname}</b>. Изгнан из банды, штраф {fine} ₽, 30 минут в тюрьме.")
            await conn.commit()
            return {"text": f"🐀 В точку! Это был {tname}. Изгнан, штраф {fine} ₽.", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}
        await kset(conn, f"gacc:{g['id']}", str(now()))
        p = await rp._load(conn, chat_id, uid)
        comp = min(p["money"], 500)
        p["money"] -= comp
        await rp._save(conn, chat_id, uid, p)
        if t["user_id"] > 0:
            await rp._load(conn, chat_id, target)
            await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (comp, chat_id, target))
        await rp._news(conn, chat_id, f"😤 Босс «{g['name']}» обвинил {tname} в доносе, но ошибся и заплатил компенсацию {comp} ₽")
        await conn.commit()
    return {"text": f"😤 Мимо! {tname} невиновен, ты платишь ему {comp} ₽ за оскорбление. Новое расследование через 30 минут.", "delta": -comp, "good": False, "state": await rp.state(chat_id, uid)}


# ──────────── фоновые задачи ────────────

async def _resolve(conn, war: dict) -> None:
    chat_id = war["chat_id"]
    ga, gb = await _gang_row(conn, war["a"]), await _gang_row(conn, war["b"])
    await conn.execute("UPDATE gang_war SET status = 'done' WHERE id = ?", (war["id"],))
    if not ga or not gb:
        return
    for gx in (ga, gb):
        await kset(conn, f"gwcd:{gx['id']}", str(now() + WAR_COOLDOWN))
    sa, sb = war["score_a"], war["score_b"]
    head = f"🏁 <b>Война окончена:</b> «{ga['name']}» {sa} : {sb} «{gb['name']}»\n"
    if sa == sb:
        await rp._news(conn, chat_id, f"🏁 Война «{ga['name']}» и «{gb['name']}» закончилась вничью {sa}:{sb}")
        await say(conn, chat_id, head + "Ничья! Все устали, все расходятся.")
        return
    win, lose = (ga, gb) if sa > sb else (gb, ga)
    await conn.execute("UPDATE gang_war SET winner = ? WHERE id = ?", (win["id"], war["id"]))
    await conn.execute("UPDATE gang SET wins = wins + 1 WHERE id = ?", (win["id"],))
    await conn.execute("UPDATE gang SET losses = losses + 1 WHERE id = ?", (lose["id"],))
    import gangplus5
    await gangplus5.on_war_end(conn, win, lose)
    wm, lm = await _real_members(conn, win["id"]), await _real_members(conn, lose["id"])
    pool = 0
    for u in lm:
        lp = await rp._load(conn, chat_id, u)
        tr = min(TRIBUTE_CAP, int(lp["money"] * TRIBUTE_PCT))
        if tr > 0:
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (tr, chat_id, u))
            pool += tr
    if win["is_ai"]:
        prize = f"Дань {pool} ₽ ушла в общак «{win['name']}»."
    elif lose["is_ai"]:
        pool = AI_REWARD
        prize = f"«{lose['name']}» раскошелились: {pool} ₽ на всех победителей."
    else:
        prize = f"Дань {pool} ₽ делится между победителями."
    if wm and pool and not win["is_ai"]:
        await _pay_all(conn, chat_id, wm, pool // len(wm))
    # район
    cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = ?", (chat_id, lose["id"]))
    theirs = [r[0] for r in await cur.fetchall()]
    cur = await conn.execute("SELECT key FROM territory WHERE chat_id = ? AND owner = 0", (chat_id,))
    free = [r[0] for r in await cur.fetchall()]
    import gangplus5
    take = await gangplus5.pick_take(conn, chat_id, win["id"], theirs, free)
    terr_line = ""
    if take:
        await conn.execute("UPDATE territory SET owner = ?, last_pay = ? WHERE chat_id = ? AND key = ?", (win["id"], now(), chat_id, take))
        terr_line = f"\n{TERR[take][1]} Район <b>{TERR[take][2]}</b> переходит под контроль «{win['name']}» (+{TERR[take][3]} ₽/час каждому)."
    await rp._news(conn, chat_id, f"🏁 «{win['name']}» победила в войне против «{lose['name']}» со счётом {max(sa, sb)}:{min(sa, sb)}")
    await say(conn, chat_id, head + f"🏆 Победитель: <b>«{win['name']}»</b>. {prize}{terr_line}")
    if win["is_ai"] and win["name"] == AI_NAME:
        await say(conn, chat_id, "🧓 <i>" + random.choice(AI_WIN) + "</i>")
    elif lose["is_ai"] and lose["name"] == AI_NAME:
        await say(conn, chat_id, "🧓 <i>" + random.choice(AI_LOSE) + "</i>")


async def _pay_territories(conn, chat_id: int) -> None:
    cur = await conn.execute("SELECT key, owner, last_pay FROM territory WHERE chat_id = ? AND owner != 0", (chat_id,))
    for key, owner, last in await cur.fetchall():
        hours = min(12, (now() - last) // 3600)
        if hours < 1 or key not in TERR:
            continue
        members = await _real_members(conn, owner)
        if members:
            import gangplus
            import gangplus4
            await gangplus.pay_territory(conn, chat_id, owner, members, int(hours * TERR[key][3] * await gangplus4.fire_mult(conn, chat_id, key) * (1.5 if key == "market" else 1) * await __import__("charter").income_mult(conn, owner) * await __import__("doom").terr_mult(conn, chat_id)))
        await conn.execute("UPDATE territory SET last_pay = ? WHERE chat_id = ? AND key = ?", (last + hours * 3600, chat_id, key))


async def _ai_life(conn, chat_id: int, ai_id: int) -> None:
    t = now()
    # у ИИ-банды свои дела: сама обновляет район и пишет новости
    if t - int(await kget(conn, f"gwailife:{ai_id}") or 0) > 5 * 3600 and random.random() < 0.3:
        await kset(conn, f"gwailife:{ai_id}", str(t))
        import gangplus3
        await rp._news(conn, chat_id, await gangplus3.ai_line(conn, ai_id))
    war = await _active_war(conn, ai_id)
    if war and war["ends"] > t and t - int(await kget(conn, f"gwai:{war['id']}") or 0) >= AI_TICK:
        await kset(conn, f"gwai:{war['id']}", str(t))
        ai_is_a = war["a"] == ai_id
        mine, theirs = (war["score_a"], war["score_b"]) if ai_is_a else (war["score_b"], war["score_a"])
        import gangplus6
        gain = int(random.randint(3, 10) * await gangplus6.pressure(conn, chat_id)) + (4 if theirs > mine * 1.3 else 0)
        col = "score_a" if ai_is_a else "score_b"
        await conn.execute(f"UPDATE gang_war SET {col} = {col} + ? WHERE id = ?", (gain, war["id"]))
        if random.random() < 0.35:
            import gangplus3
            await rp._news(conn, chat_id, await gangplus3.ai_line(conn, ai_id))


async def tick(chats=None) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute("SELECT chat_id FROM rp_player GROUP BY chat_id HAVING COUNT(*) >= 2")
            for (chat_id,) in await cur.fetchall():
                ai_id = await ensure_ai(conn, chat_id)
                import gangplus3
                await gangplus3.ensure_ai_gangs(conn, chat_id)
                cur2 = await conn.execute("SELECT id FROM gang WHERE chat_id = ? AND is_ai = 1", (chat_id,))
                for (aid,) in await cur2.fetchall():
                    await _ai_life(conn, chat_id, aid)
                await _pay_territories(conn, chat_id)
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM gang_war WHERE status = 'active' AND ends <= ?", (now(),))
            for war in [dict(r) for r in await cur.fetchall()]:
                await _resolve(conn, war)
            import gangplus
            await gangplus.tick(conn)
            import gangplus3, gangplus4
            await gangplus3.tick(conn)
            await gangplus4.tick(conn)
            import gangplus5
            await gangplus5.tick(conn)
            import gangplus6
            await gangplus6.tick(conn)
            import gangplus8, gangplus9, gangplus10
            await gangplus8.tick(conn)
            await gangplus9.tick(conn)
            await gangplus10.tick(conn)
            import police
            await police.tick(conn)
            import police2, gangplus11, aiciv, patrons
            await police2.tick(conn)
            await gangplus11.tick(conn)
            await aiciv.tick(conn)
            await patrons.tick(conn)
            import world
            await world.tick(conn)
            import balance
            await balance.tick(conn)
            import assign
            await assign.tick(conn)
            import conflict
            await conflict.tick(conn)
            import justice
            await justice.tick(conn)
            import flat
            await flat.tick(conn)
            import power
            await power.tick(conn)
            import control
            await control.tick(conn)
            import residents
            await residents.tick(conn)
            import doom
            await doom.tick(conn)
            await conn.commit()
    except Exception:
        log.exception("Сбой фоновых задач ОПГ")


async def overview_text(chat_id: int) -> str:
    from utils import esc
    async with aiosqlite.connect(DB_PATH) as conn:
        data = await extras(conn, chat_id, 0, None, "")
    if not data["gangs"]:
        return "🕴 В районе пока нет банд. Основать ОПГ можно в мини-аппе: Район → ОПГ."
    lines = ["🕴 <b>Банды района</b>"]
    for x in data["gangs"]:
        mark = "🧓" if x["is_ai"] else "🕴"
        lines.append(f"{mark} <b>{esc(x['name'])}</b> · {x['n']} чел. · побед {x['wins']}/{x['wins'] + x['losses']} {x['terr']}")
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT w.*, a.name AS an, b.name AS bn FROM gang_war w JOIN gang a ON a.id = w.a JOIN gang b ON b.id = w.b WHERE w.chat_id = ? AND w.status = 'active'", (chat_id,))
        wars = [dict(r) for r in await cur.fetchall()]
    for w in wars:
        left = max(0, w["ends"] - now())
        lines.append(f"\n⚔️ <b>{esc(w['an'])}</b> {w['score_a']} : {w['score_b']} <b>{esc(w['bn'])}</b> · осталось {left // 3600} ч {left % 3600 // 60} мин")
    free = [t for t in data["terr"] if not t["owner"]]
    if free:
        lines.append("\n🏳 Свободные районы: " + ", ".join(f"{t['icon']} {t['name']}" for t in free))
    lines.append("\nВойны, рейды и роли: мини-апп → Район → ОПГ.")
    return "\n".join(lines)
