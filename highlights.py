"""Лента подвигов: короткие записи о заметных событиях чата (показываются в мини-аппе в «Что происходит»)."""
import time
from typing import List

import aiosqlite

from config import DB_PATH


async def add(chat_id: int, icon: str, text: str) -> None:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("INSERT INTO highlight (chat_id, ts, icon, text) VALUES (?, ?, ?, ?)", (chat_id, now, icon, text[:200]))
        await conn.execute("DELETE FROM highlight WHERE ts < ?", (now - 14 * 86400,))
        await conn.commit()


async def recent(chat_id: int, days: int = 3) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT ts, icon, text FROM highlight WHERE chat_id = ? AND ts >= ? ORDER BY ts DESC LIMIT 30",
            (chat_id, int(time.time()) - days * 86400),
        )
        return [dict(r) for r in await cur.fetchall()]
