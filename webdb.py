"""Запросы к базе для мини-аппа (бот и мини-апп используют одну SQLite-базу)."""
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from config import DB_PATH

EMOJIS = ["😎", "🔥", "👑", "🦄", "🐉", "🤡", "👽", "🥷", "🦊", "🐸", "💎", "🍀", "🎯", "🚀", "🧠", "🍕"]


async def _rows(query: str, params: tuple = ()) -> List[dict]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        return [dict(r) for r in await cursor.fetchall()]


async def _one(query: str, params: tuple = ()) -> Optional[dict]:
    rows = await _rows(query, params)
    return rows[0] if rows else None


async def user_chats(user_id: int) -> List[dict]:
    rows = await _rows(
        """
        SELECT u.chat_id, u.size, k.value AS title
        FROM users u LEFT JOIN kv k ON k.key = 'chat_title:' || u.chat_id
        WHERE u.user_id = ? AND u.chat_id < 0
        ORDER BY u.chat_id
        """,
        (user_id,),
    )
    for r in rows:
        r["title"] = r["title"] or f"Чат {abs(r['chat_id'])}"
    return rows


async def player(chat_id: int, user_id: int) -> Optional[dict]:
    return await _one(
        """
        SELECT user_id, username, size, rep, streak, last_daily, last_drip, emoji,
               CASE WHEN title_until > ? THEN title ELSE '' END AS title,
               CASE WHEN title_until > ? THEN title_until ELSE 0 END AS title_until
        FROM users WHERE chat_id = ? AND user_id = ?
        """,
        (int(time.time()), int(time.time()), chat_id, user_id),
    )


async def players(chat_id: int) -> List[dict]:
    return await _rows(
        """
        SELECT user_id, username, size, rep, streak, emoji,
               CASE WHEN title_until > ? THEN title ELSE '' END AS title
        FROM users WHERE chat_id = ? ORDER BY size DESC
        """,
        (int(time.time()), chat_id),
    )


async def activity(chat_id: int, since_day: str) -> List[dict]:
    return await _rows(
        "SELECT day, SUM(cnt) AS cnt FROM msg_stats WHERE chat_id = ? AND day >= ? GROUP BY day ORDER BY day",
        (chat_id, since_day),
    )


async def recent_heroes(chat_id: int, limit: int = 10) -> List[dict]:
    return await _rows(
        """
        SELECT h.day, h.title, u.username
        FROM heroes h JOIN users u ON u.user_id = h.user_id AND u.chat_id = h.chat_id
        WHERE h.chat_id = ? ORDER BY h.day DESC LIMIT ?
        """,
        (chat_id, limit),
    )


async def recent_quotes(chat_id: int, limit: int = 10) -> List[dict]:
    return await _rows(
        "SELECT text, author_name, created_at FROM quotes WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    )


async def size_history(chat_id: int, user_id: int, limit: int = 40) -> List[dict]:
    rows = await _rows(
        "SELECT ts, delta, reason, size_after FROM size_log WHERE chat_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, user_id, limit),
    )
    return list(reversed(rows))


async def stats(chat_id: int, user_id: int) -> Dict[str, Any]:
    """Сводка для достижений и профиля."""
    by_reason = {
        r["reason"]: r
        for r in await _rows(
            """
            SELECT reason,
                   SUM(CASE WHEN delta > 0 THEN 1 ELSE 0 END) AS plus,
                   SUM(CASE WHEN delta < 0 THEN 1 ELSE 0 END) AS minus,
                   COUNT(*) AS n
            FROM size_log WHERE chat_id = ? AND user_id = ? GROUP BY reason
            """,
            (chat_id, user_id),
        )
    }

    def plus(reason: str) -> int:
        return by_reason.get(reason, {}).get("plus") or 0

    def total(*reasons: str) -> int:
        return sum(by_reason.get(r, {}).get("n") or 0 for r in reasons)

    msgs = await _one(
        "SELECT COALESCE(SUM(cnt), 0) AS c FROM msg_stats WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    )
    heroes = await _one(
        "SELECT COUNT(*) AS c FROM heroes WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
    )
    return {
        "messages": msgs["c"] if msgs else 0,
        "heroes": heroes["c"] if heroes else 0,
        "duel_wins": plus("duel"),
        "duel_losses": by_reason.get("duel", {}).get("minus") or 0,
        "robs": plus("rob"),
        "gambles": total("casino", "wheel", "slots"),
        "trivia": plus("trivia"),
        "arena_wins": plus("arena"),
        "dick_rolls": total("dick"),
    }


GAMBLE_REASONS = ("casino", "wheel", "slots")
GAMBLE_DAILY_CAP = 60  # максимум чистого выигрыша в азартных играх за скользящие 24 часа


def win_factor(size: int) -> float:
    """Чем больше у игрока см, тем меньше выигрыш (и наоборот): множитель к чистой прибыли."""
    return max(0.3, min(1.4, 1.4 / (1 + max(0, size) / 120) ** 0.5))


async def gamble_room(user_id: int, chat_id: int) -> int:
    """Сколько см чистой прибыли игрок ещё может выиграть в казино/колесе за сутки."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM size_log WHERE chat_id = ? AND user_id = ? AND ts >= ? "
            "AND reason IN ('casino','wheel','slots')",
            (chat_id, user_id, int(time.time()) - 86400),
        )
        net = (await cur.fetchone())[0]
    return max(0, GAMBLE_DAILY_CAP - net)


async def apply_bet(user_id: int, chat_id: int, bet: int, payout: int, reason: str) -> Optional[int]:
    """
    Атомарно проводит ставку: списывает bet, начисляет payout.
    Возвращает новый размер, либо None, если у игрока меньше bet.
    """
    if reason in GAMBLE_REASONS and payout > bet:
        async with aiosqlite.connect(DB_PATH, timeout=10) as conn0:
            cur0 = await conn0.execute("SELECT size FROM users WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
            row0 = await cur0.fetchone()
        profit = max(1, round((payout - bet) * win_factor(row0[0] if row0 else 0)))
        payout = min(bet + profit, bet + await gamble_room(user_id, chat_id))
    delta = payout - bet
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "UPDATE users SET size = size + ? WHERE user_id = ? AND chat_id = ? AND size >= ?",
            (delta, user_id, chat_id, bet),
        )
        if cursor.rowcount == 0:
            await db.rollback()
            return None
        await db.execute(
            """
            INSERT INTO size_log (chat_id, user_id, ts, delta, reason, size_after)
            SELECT chat_id, user_id, ?, ?, ?, size FROM users WHERE chat_id = ? AND user_id = ?
            """,
            (int(time.time()), delta, reason, chat_id, user_id),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT size FROM users WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
        )
        return (await cursor.fetchone())[0]


async def set_emoji(user_id: int, chat_id: int, emoji: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "UPDATE users SET emoji = ? WHERE user_id = ? AND chat_id = ?", (emoji, user_id, chat_id)
        )
        await db.commit()
