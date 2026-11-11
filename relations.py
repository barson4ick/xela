"""Отношения между игроками: очки растут от РП-действий и «+» в ответ на сообщение."""
import time
from typing import List, Optional

import aiosqlite

from config import DB_PATH
from utils import day_str

# (порог очков, название, иконка)
STAGES = [(0, "Знакомые", "🤝"), (5, "Друзья", "😊"), (15, "Хорошие друзья", "🤗"), (35, "Лучшие друзья", "💞")]
PAIR_GAP = 600      # секунд между начислениями очков одной паре
DAILY_CAP = 8       # очков в сутки на пару


def stage_idx(pts: int) -> int:
    idx = 0
    for i, (threshold, _, _) in enumerate(STAGES):
        if pts >= threshold:
            idx = i
    return idx


def stage_info(pts: int) -> dict:
    i = stage_idx(pts)
    nxt = STAGES[i + 1] if i + 1 < len(STAGES) else None
    return {"pts": pts, "stage": STAGES[i][1], "icon": STAGES[i][2], "next": nxt[0] - pts if nxt else 0, "next_name": nxt[1] if nxt else ""}


def _pair(a: int, b: int) -> tuple:
    return (a, b) if a < b else (b, a)


async def get(chat_id: int, a: int, b: int) -> dict:
    x, y = _pair(a, b)
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT pts FROM rel WHERE chat_id = ? AND a = ? AND b = ?", (chat_id, x, y))
        row = await cur.fetchone()
    return stage_info(row[0] if row else 0)


async def add_points(chat_id: int, a: int, b: int, pts: int = 1) -> dict:
    """Начисляет очки паре. Возвращает {gained, info, up}: up — перешли ли на новую стадию."""
    x, y = _pair(a, b)
    now, today = int(time.time()), day_str()
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT pts, last_ts, day, day_pts FROM rel WHERE chat_id = ? AND a = ? AND b = ?", (chat_id, x, y))
        row = await cur.fetchone()
        total, last_ts, day, day_pts = row if row else (0, 0, "", 0)
        if day != today:
            day, day_pts = today, 0
        gained = 0
        if now - last_ts >= PAIR_GAP and day_pts < DAILY_CAP:
            gained = min(pts, DAILY_CAP - day_pts)
        before = stage_idx(total)
        total += gained
        if gained:
            await conn.execute(
                """INSERT INTO rel (chat_id, a, b, pts, last_ts, day, day_pts) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, a, b) DO UPDATE SET pts = excluded.pts, last_ts = excluded.last_ts, day = excluded.day, day_pts = excluded.day_pts""",
                (chat_id, x, y, total, now, day, day_pts + gained),
            )
            await conn.commit()
    return {"gained": gained, "info": stage_info(total), "up": stage_idx(total) > before}


async def top(chat_id: int, uid: int, limit: int = 8) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """SELECT r.a, r.b, r.pts, u.username, u.user_id AS other FROM rel r
               JOIN users u ON u.chat_id = r.chat_id AND u.user_id = CASE WHEN r.a = ? THEN r.b ELSE r.a END
               WHERE r.chat_id = ? AND (r.a = ? OR r.b = ?) ORDER BY r.pts DESC LIMIT ?""",
            (uid, chat_id, uid, uid, limit),
        )
        rows = await cur.fetchall()
    return [{"id": r["other"], "name": r["username"], **stage_info(r["pts"])} for r in rows]
