"""Задания дня, босс чата и сезоны: общая логика для бота и мини-аппа (одна SQLite-база)."""
import asyncio
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiosqlite

import database as db
from config import DB_PATH, TZ
from utils import day_str, now_local

# ───────────────────────────── задания дня ─────────────────────────────

# id, иконка, текст, причины в size_log (None — особый источник), нужно раз, награда, только прирост
QUESTS = [
    ("dick", "📏", "Сделай замер", ("dick",), 1, 1, False),
    ("daily", "🎁", "Забери ежедневный бонус", ("daily",), 1, 1, False),
    ("gamble", "🎰", "Сыграй 3 раза в казино", ("casino", "wheel", "slots"), 3, 2, False),
    ("arena", "🏟", "Выиграй партию на арене", ("arena",), 1, 3, True),
    ("duel", "⚔️", "Выиграй дуэль в чате", ("duel",), 1, 3, True),
    ("trivia", "🧠", "Правильно ответь в викторине", ("trivia",), 1, 2, True),
    ("rob", "🥷", "Удачно ограбь кого-нибудь", ("rob",), 1, 3, True),
    ("mafia", "🕵️", "Выиграй партию в мафию", ("mafia",), 1, 3, True),
    ("chat", "💬", "Напиши 15 сообщений в чат", None, 15, 2, False),
    ("boss", "👹", "Ударь босса чата", None, 1, 2, False),
]
QUEST_BY_ID = {q[0]: q for q in QUESTS}
QUESTS_PER_DAY = 3
QUESTS_BONUS = 3


def _midnight_ts() -> int:
    n = now_local()
    return int(n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def quest_ids(chat_id: int, user_id: int, day: str) -> List[str]:
    rng = random.Random(f"{chat_id}:{user_id}:{day}")
    return [q[0] for q in rng.sample(QUESTS, QUESTS_PER_DAY)]


async def _scalar(query: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(query, params)
        row = await cur.fetchone()
        return row[0] or 0 if row else 0


async def quest_progress(chat_id: int, user_id: int, qid: str, day: str) -> int:
    _, _, _, reasons, _, _, gain_only = QUEST_BY_ID[qid]
    if qid == "chat":
        return await _scalar(
            "SELECT SUM(cnt) FROM msg_stats WHERE chat_id = ? AND user_id = ? AND day = ?",
            (chat_id, user_id, day),
        )
    if qid == "boss":
        return await _scalar(
            """SELECT SUM(d.hits) FROM boss_dmg d JOIN boss b ON b.id = d.boss_id
               WHERE b.chat_id = ? AND d.user_id = ? AND d.day = ?""",
            (chat_id, user_id, day),
        )
    marks = ",".join("?" * len(reasons))
    return await _scalar(
        f"SELECT COUNT(*) FROM size_log WHERE chat_id = ? AND user_id = ? AND ts >= ? AND reason IN ({marks})"
        + (" AND delta > 0" if gain_only else ""),
        (chat_id, user_id, _midnight_ts(), *reasons),
    )


async def _claimed(chat_id: int, user_id: int, day: str) -> set:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT qid FROM quest_claims WHERE chat_id = ? AND user_id = ? AND day = ?",
            (chat_id, user_id, day),
        )
        return {r[0] for r in await cur.fetchall()}


async def quests_view(chat_id: int, user_id: int) -> dict:
    day = day_str()
    claimed = await _claimed(chat_id, user_id, day)
    items = []
    for qid in quest_ids(chat_id, user_id, day):
        _, icon, title, _, need, reward, _ = QUEST_BY_ID[qid]
        have = min(await quest_progress(chat_id, user_id, qid, day), need)
        items.append({
            "id": qid, "icon": icon, "title": title, "need": need, "have": have,
            "reward": reward, "claimed": qid in claimed, "ready": have >= need and qid not in claimed,
        })
    all_claimed = all(i["claimed"] for i in items)
    return {
        "items": items,
        "bonus": QUESTS_BONUS,
        "bonus_claimed": "bonus" in claimed,
        "bonus_ready": all_claimed and "bonus" not in claimed,
    }


async def _claim_row(chat_id: int, user_id: int, day: str, qid: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO quest_claims (chat_id, user_id, day, qid) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, day, qid),
        )
        await conn.commit()
        return cur.rowcount == 1


async def claim_quest(chat_id: int, user_id: int, name: str, qid: str) -> int:
    """Выдаёт награду за задание (или бонус за все три). Возвращает награду; 0 — ещё нельзя."""
    day = day_str()
    view = await quests_view(chat_id, user_id)
    if qid == "bonus":
        if not view["bonus_ready"]:
            return 0
        reward = QUESTS_BONUS
    else:
        item = next((i for i in view["items"] if i["id"] == qid), None)
        if not item or not item["ready"]:
            return 0
        reward = item["reward"]
    if not await _claim_row(chat_id, user_id, day, qid):
        return 0
    await db.add_size(user_id, chat_id, name, reward, "quest")
    return reward


# ───────────────────────────── босс чата ─────────────────────────────

BOSSES = [
    ("Дедлайн-Дракон", "🐉"), ("Понедельник", "😈"), ("Босс Бесконечных Созвонов", "📞"),
    ("Кринж-Голем", "🗿"), ("Вечный Чат-Тролль", "🧌"), ("Сессия", "📚"),
    ("Король Спойлеров", "🤴"), ("Уведомление в 3 ночи", "🔔"), ("Мега-Душнила", "🥱"),
]
BOSS_LIFETIME = 48 * 3600
BOSS_REST = 2 * 3600
HITS_PER_DAY = 3
_boss_lock = asyncio.Lock()


async def _active_players(chat_id: int) -> int:
    return await _scalar(
        "SELECT COUNT(DISTINCT user_id) FROM msg_stats WHERE chat_id = ? AND day >= ?",
        (chat_id, day_str(-7)),
    )


async def _latest_boss(chat_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM boss WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def ensure_boss(chat_id: int) -> tuple[Optional[dict], Optional[str]]:
    """
    Возвращает (текущий босс или None, событие для чата).
    Событие: 'spawn' — босс только что появился, 'fled' — прошлый босс сбежал.
    """
    async with _boss_lock:
        now = int(time.time())
        boss = await _latest_boss(chat_id)
        event = None
        if boss and boss["status"] == "alive" and boss["ends"] < now:
            async with aiosqlite.connect(DB_PATH) as conn:
                await conn.execute("UPDATE boss SET status = 'fled' WHERE id = ?", (boss["id"],))
                await conn.commit()
            boss["status"] = "fled"
            event = "fled"
        if boss and boss["status"] == "alive":
            return boss, event
        if boss and now - boss["ends"] < BOSS_REST and boss["status"] == "dead":
            return boss, event
        n = max(await _active_players(chat_id), 2)
        hp = 25 * n + 30
        name, emoji = random.choice([b for b in BOSSES if not boss or b[0] != boss["name"]] or BOSSES)
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute(
                "INSERT INTO boss (chat_id, name, emoji, max_hp, hp, spawned, ends) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chat_id, name, emoji, hp, hp, now, now + BOSS_LIFETIME),
            )
            await conn.commit()
        return await _latest_boss(chat_id), "spawn" if event is None else "fled+spawn"


async def boss_state(chat_id: int, user_id: int) -> dict:
    boss, _ = await ensure_boss(chat_id)
    return await _boss_view(boss, user_id)


async def _boss_view(boss: Optional[dict], user_id: int) -> dict:
    if not boss:
        return {"boss": None}
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """SELECT d.user_id, u.username, SUM(d.dmg) AS dmg FROM boss_dmg d
               LEFT JOIN users u ON u.user_id = d.user_id AND u.chat_id = ?
               WHERE d.boss_id = ? GROUP BY d.user_id ORDER BY dmg DESC LIMIT 5""",
            (boss["chat_id"], boss["id"]),
        )
        top = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT COALESCE(SUM(hits), 0) FROM boss_dmg WHERE boss_id = ? AND user_id = ? AND day = ?",
            (boss["id"], user_id, day_str()),
        )
        used = (await cur.fetchone())[0]
    return {
        "boss": {
            "name": boss["name"], "emoji": boss["emoji"], "hp": boss["hp"], "max_hp": boss["max_hp"],
            "status": boss["status"], "ends_in": max(0, boss["ends"] - int(time.time())),
            "rest_left": max(0, boss["ends"] + BOSS_REST - int(time.time())) if boss["status"] != "alive" else 0,
        },
        "top": top,
        "hits_left": max(0, HITS_PER_DAY - used) if boss["status"] == "alive" else 0,
        "hits_per_day": HITS_PER_DAY,
    }


async def boss_hit(chat_id: int, user_id: int, name: str) -> dict:
    """Удар по боссу. Возвращает {dmg, crit, killed, rewards?, boss: …} или бросает ValueError с текстом."""
    boss, _ = await ensure_boss(chat_id)
    if not boss or boss["status"] != "alive":
        raise ValueError("Босс сейчас отдыхает, скоро появится новый")
    day = day_str()
    async with _boss_lock:
        boss = await _latest_boss(chat_id)
        if not boss or boss["status"] != "alive" or boss["ends"] < time.time():
            raise ValueError("Босс уже побеждён или сбежал")
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute(
                "SELECT COALESCE(SUM(hits), 0) FROM boss_dmg WHERE boss_id = ? AND user_id = ? AND day = ?",
                (boss["id"], user_id, day),
            )
            if (await cur.fetchone())[0] >= HITS_PER_DAY:
                raise ValueError(f"Удары на сегодня закончились ({HITS_PER_DAY} в день). Завтра снова!")
            dmg = random.randint(4, 12)
            crit = random.random() < 0.15
            if crit:
                dmg *= 2
            dmg = min(dmg, boss["hp"])
            await conn.execute(
                """INSERT INTO boss_dmg (boss_id, user_id, day, hits, dmg) VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(boss_id, user_id, day) DO UPDATE SET hits = hits + 1, dmg = dmg + excluded.dmg""",
                (boss["id"], user_id, day, dmg),
            )
            hp = boss["hp"] - dmg
            killed = hp <= 0
            await conn.execute(
                "UPDATE boss SET hp = ?, status = ?, killer = ?, ends = ? WHERE id = ?",
                (hp, "dead" if killed else "alive", user_id if killed else None, int(time.time()) if killed else boss["ends"], boss["id"]),
            )
            await conn.commit()
    result = {"dmg": dmg, "crit": crit, "killed": killed}
    if killed:
        result["rewards"] = await _boss_rewards(boss, user_id)
    boss = await _latest_boss(chat_id)
    result.update(await _boss_view(boss, user_id))
    return result


async def _boss_rewards(boss: dict, killer: int) -> List[dict]:
    """Награда всем, кто бил: 2 см + до 8 см по доле урона, убийце +3."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """SELECT d.user_id, u.username, SUM(d.dmg) AS dmg FROM boss_dmg d
               LEFT JOIN users u ON u.user_id = d.user_id AND u.chat_id = ?
               WHERE d.boss_id = ? GROUP BY d.user_id ORDER BY dmg DESC""",
            (boss["chat_id"], boss["id"]),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    total = sum(r["dmg"] for r in rows) or 1
    out = []
    for r in rows:
        reward = 2 + round(8 * r["dmg"] / total) + (3 if r["user_id"] == killer else 0)
        await db.add_size(r["user_id"], boss["chat_id"], r["username"] or "?", reward, "boss")
        out.append({"user_id": r["user_id"], "name": r["username"] or "?", "dmg": r["dmg"], "reward": reward})
    return out


# ───────────────────────────── сезон ─────────────────────────────

SEASON_PRIZES = [10, 6, 3]
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def season_bounds(year: int, month: int) -> tuple[int, int]:
    start = datetime(year, month, 1, tzinfo=TZ)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=TZ)
    return int(start.timestamp()), int(end.timestamp())


async def season_board(chat_id: int, year: int, month: int, limit: int = 10) -> List[dict]:
    start, end = season_bounds(year, month)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """SELECT l.user_id, u.username, u.emoji, SUM(l.delta) AS score FROM size_log l
               JOIN users u ON u.user_id = l.user_id AND u.chat_id = l.chat_id
               WHERE l.chat_id = ? AND l.ts >= ? AND l.ts < ? AND l.reason NOT IN ('quest', 'boss', 'season', 'admin', 'give_in', 'give_out', 'poker_in', 'poker_out', 'event', 'cquest', 'dispute_in', 'dispute_out', 'predict_in', 'predict_out', 'cosmetic', 'game_in', 'game_out')
               GROUP BY l.user_id HAVING score != 0 ORDER BY score DESC LIMIT ?""",
            (chat_id, start, end, limit),
        )
        return [dict(r) for r in await cur.fetchall()]


async def season_view(chat_id: int) -> dict:
    n = now_local()
    _, end = season_bounds(n.year, n.month)
    return {
        "name": f"{MONTHS[n.month - 1].capitalize()} {n.year}",
        "days_left": max(0, (end - int(n.timestamp())) // 86400),
        "board": await season_board(chat_id, n.year, n.month),
        "prizes": SEASON_PRIZES,
    }


async def finish_season(chat_id: int, year: int, month: int) -> Optional[List[dict]]:
    """Награждает топ-3 прошлого месяца (один раз на чат и месяц). None — награждать некого или уже было."""
    key = f"season_done:{chat_id}:{year}-{month:02d}"
    if await db.kv_get(key):
        return None
    await db.kv_set(key, "1")
    board = await season_board(chat_id, year, month, limit=3)
    if len(board) < 2:
        return None
    title = f"Чемпион сезона 🏆 {MONTHS[month - 1]}"
    until = int(time.time()) + 30 * 86400
    for i, row in enumerate(board):
        await db.add_size(row["user_id"], chat_id, row["username"], SEASON_PRIZES[i], "season")
        if i == 0:
            async with aiosqlite.connect(DB_PATH) as conn:
                await conn.execute(
                    "UPDATE users SET title = ?, title_until = ? WHERE user_id = ? AND chat_id = ?",
                    (title, until, row["user_id"], chat_id),
                )
                await conn.commit()
    return board


def spawn_text(boss: dict) -> str:
    return (
        f"{boss['emoji']} <b>В чате появился босс: {boss['name']}</b> ({boss['max_hp']} HP)!\n"
        f"Бейте его вместе в мини-аппе → «Задания» (по {HITS_PER_DAY} удара в день у каждого). "
        f"Добьёте за 48 часов — награда всем, кто бил."
    )


# ───────────────────────────── лента событий и сводка ─────────────────────────────

FEED_TEXT = {
    "wheel": ("🎡", "{n} сорвал куш на колесе: +{d} см"),
    "slots": ("🎰", "{n} поймал удачу в слотах: +{d} см"),
    "casino": ("🎰", "{n} выиграл в казино: +{d} см"),
    "arena": ("🏟", "{n} победил на арене: +{d} см"),
    "duel": ("⚔️", "{n} выиграл дуэль: +{d} см"),
    "rob": ("🥷", "{n} успешно ограбил на {d} см"),
    "mafia": ("🕵️", "{n} победил в мафии: +{d} см"),
    "boss": ("👹", "{n} получил награду за босса: +{d} см"),
    "lottery": ("🎟", "{n} выиграл лотерею: +{d} см"),
    "season": ("🏆", "{n} — призёр сезона: +{d} см"),
    "bet_win": ("🎯", "{n} угадал исход партии: +{d} см"),
}
FEED_MIN = {"wheel": 5, "slots": 5, "casino": 5}


async def feed(chat_id: int, limit: int = 12) -> List[dict]:
    reasons = ",".join("?" * len(FEED_TEXT))
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            f"""SELECT l.ts, l.delta, l.reason, u.username FROM size_log l
                JOIN users u ON u.user_id = l.user_id AND u.chat_id = l.chat_id
                WHERE l.chat_id = ? AND l.ts >= ? AND l.delta > 0 AND l.reason IN ({reasons})
                ORDER BY l.ts DESC LIMIT 60""",
            (chat_id, int(time.time()) - 3 * 86400, *FEED_TEXT),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT name, emoji, ends FROM boss WHERE chat_id = ? AND status = 'dead' AND ends >= ?",
            (chat_id, int(time.time()) - 3 * 86400),
        )
        bosses = [dict(r) for r in await cur.fetchall()]
    out = []
    for r in rows:
        if r["delta"] < FEED_MIN.get(r["reason"], 1):
            continue
        icon, text = FEED_TEXT[r["reason"]]
        out.append({"ts": r["ts"], "icon": icon, "text": text.format(n=r["username"], d=r["delta"])})
    for b in bosses:
        out.append({"ts": b["ends"], "icon": b["emoji"], "text": f"Босс «{b['name']}» повержен всем чатом"})
    import highlights
    for h in await highlights.recent(chat_id):
        out.append({"ts": h["ts"], "icon": h["icon"], "text": h["text"]})
    out.sort(key=lambda x: -x["ts"])
    return out[:limit]


async def today_summary(chat_id: int, user_id: int) -> dict:
    boss, _ = await ensure_boss(chat_id)
    quests = await quests_view(chat_id, user_id)
    mine, total, players = await db.lottery_state(chat_id, day_str(), user_id)
    b = None
    if boss and boss["status"] == "alive":
        b = {"name": boss["name"], "emoji": boss["emoji"], "pct": round(100 * boss["hp"] / boss["max_hp"]), "left": max(0, boss["ends"] - int(time.time()))}
    return {
        "boss": b,
        "quests_done": sum(1 for i in quests["items"] if i["claimed"]),
        "quests_total": len(quests["items"]),
        "quests_ready": sum(1 for i in quests["items"] if i["ready"]) + (1 if quests["bonus_ready"] else 0),
        "lottery": {"mine": mine, "total": total, "players": players},
    }


async def week_facts(chat_id: int) -> str:
    """Факты недели для ИИ-пересказа: победители, куши, боссы, цитата."""
    since = int(time.time()) - 7 * 86400
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        facts = []
        for reason, label in (("arena", "побед на арене"), ("duel", "побед в дуэлях"), ("mafia", "побед в мафии"), ("rob", "удачных ограблений")):
            cur = await conn.execute(
                """SELECT u.username, COUNT(*) AS c FROM size_log l JOIN users u ON u.user_id = l.user_id AND u.chat_id = l.chat_id
                   WHERE l.chat_id = ? AND l.ts >= ? AND l.reason = ? AND l.delta > 0 GROUP BY l.user_id ORDER BY c DESC LIMIT 1""",
                (chat_id, since, reason),
            )
            row = await cur.fetchone()
            if row:
                facts.append(f"Больше всего {label}: {row['username']} ({row['c']})")
        cur = await conn.execute(
            """SELECT u.username, l.delta, l.reason FROM size_log l JOIN users u ON u.user_id = l.user_id AND u.chat_id = l.chat_id
               WHERE l.chat_id = ? AND l.ts >= ? AND l.reason IN ('wheel','slots','casino') ORDER BY l.delta DESC LIMIT 1""",
            (chat_id, since),
        )
        row = await cur.fetchone()
        if row and row["delta"] > 0:
            facts.append(f"Самый крупный выигрыш в казино: {row['username']} +{row['delta']} см")
        cur = await conn.execute(
            """SELECT u.username, l.delta FROM size_log l JOIN users u ON u.user_id = l.user_id AND u.chat_id = l.chat_id
               WHERE l.chat_id = ? AND l.ts >= ? AND l.reason IN ('wheel','slots','casino') ORDER BY l.delta ASC LIMIT 1""",
            (chat_id, since),
        )
        row = await cur.fetchone()
        if row and row["delta"] < 0:
            facts.append(f"Самый болезненный проигрыш в казино: {row['username']} {row['delta']} см")
        cur = await conn.execute("SELECT name FROM boss WHERE chat_id = ? AND status = 'dead' AND ends >= ?", (chat_id, since))
        for r in await cur.fetchall():
            facts.append(f"Чат победил босса «{r['name']}»")
        cur = await conn.execute(
            "SELECT text, author_name FROM quotes WHERE chat_id = ? AND created_at >= ? ORDER BY RANDOM() LIMIT 1", (chat_id, since)
        )
        row = await cur.fetchone()
        if row:
            facts.append(f"Цитата недели от {row['author_name']}: «{row['text'][:160]}»")
    return "\n".join(f"- {f}" for f in facts)
