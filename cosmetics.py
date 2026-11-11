"""Смена внешности: рамки и фоны карточки профиля за см."""
import json
from typing import Dict, Optional

import aiosqlite

from config import DB_PATH
from rooms import GameError

# id: (название, цена, цвета). Для рамки один-два цвета, для фона два (градиент сверху вниз)
FRAMES: Dict[str, tuple] = {
    "gold": ("Золотая", 15, ("#f5a524", "#f5a524")),
    "neon": ("Неон", 20, ("#22d3ee", "#22d3ee")),
    "flame": ("Пламя", 25, ("#ff5a1f", "#ffb020")),
    "royal": ("Королевская", 30, ("#8b5cf6", "#ec4899")),
    "rainbow": ("Радуга", 40, ("#ef4444", "#3b82f6")),
}
BGS: Dict[str, tuple] = {
    "dusk": ("Закат", 10, ("#2b1055", "#d53369")),
    "forest": ("Лес", 10, ("#0f2e1d", "#2f8f5b")),
    "ocean": ("Океан", 15, ("#0b2545", "#13a5c9")),
    "lava": ("Лава", 20, ("#2a0a0a", "#e0481f")),
    "space": ("Космос", 25, ("#050816", "#3a2a7a")),
}
KINDS = {"frame": FRAMES, "bg": BGS}


async def _row(conn, chat_id: int, uid: int) -> dict:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM cosmetic WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    row = await cur.fetchone()
    if not row:
        return {"frame": "", "bg": "", "owned": []}
    d = dict(row)
    try:
        d["owned"] = json.loads(d["owned"] or "[]")
    except ValueError:
        d["owned"] = []
    return d


async def equipped(chat_id: int, uid: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        r = await _row(conn, chat_id, uid)
    return {"frame": r["frame"], "bg": r["bg"]}


def colors(kind: str, item_id: str) -> Optional[tuple]:
    item = KINDS[kind].get(item_id)
    return item[2] if item else None


def catalog_view(row: dict) -> dict:
    def listing(kind: str) -> list:
        return [
            {"id": k, "title": v[0], "price": v[1], "colors": list(v[2]), "owned": f"{kind}:{k}" in row["owned"]}
            for k, v in KINDS[kind].items()
        ]
    return {"frames": listing("frame"), "bgs": listing("bg"), "frame": row["frame"], "bg": row["bg"]}


async def state(chat_id: int, uid: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        return catalog_view(await _row(conn, chat_id, uid))


async def buy(chat_id: int, uid: int, kind: str, item_id: str) -> dict:
    from webdb import apply_bet
    if kind not in KINDS or item_id not in KINDS[kind]:
        raise GameError("Такого товара нет")
    title, price, _ = KINDS[kind][item_id]
    async with aiosqlite.connect(DB_PATH) as conn:
        row = await _row(conn, chat_id, uid)
    key = f"{kind}:{item_id}"
    if key in row["owned"]:
        raise GameError("Это уже у тебя есть")
    size = await apply_bet(uid, chat_id, price, 0, "cosmetic")
    if size is None:
        raise GameError(f"Не хватает см: {title} стоит {price}")
    owned = row["owned"] + [key]
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO cosmetic (chat_id, user_id, frame, bg, owned) VALUES (?, ?, '', '', ?)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET owned = excluded.owned""",
            (chat_id, uid, json.dumps(owned)),
        )
        await conn.execute(f"UPDATE cosmetic SET {kind} = ? WHERE chat_id = ? AND user_id = ?", (item_id, chat_id, uid))
        await conn.commit()
    return {"text": f"✨ Куплено и надето: {title} (−{price} см)", "size": size, "cosmetics": await state(chat_id, uid)}


async def equip(chat_id: int, uid: int, kind: str, item_id: str) -> dict:
    if kind not in KINDS:
        raise GameError("Нечего надевать")
    async with aiosqlite.connect(DB_PATH) as conn:
        row = await _row(conn, chat_id, uid)
        if item_id and f"{kind}:{item_id}" not in row["owned"]:
            raise GameError("Сначала купи это")
        await conn.execute(
            """INSERT INTO cosmetic (chat_id, user_id, frame, bg, owned) VALUES (?, ?, '', '', '[]')
               ON CONFLICT(chat_id, user_id) DO NOTHING""",
            (chat_id, uid),
        )
        await conn.execute(f"UPDATE cosmetic SET {kind} = ? WHERE chat_id = ? AND user_id = ?", (item_id, chat_id, uid))
        await conn.commit()
    return {"text": "Готово", "cosmetics": await state(chat_id, uid)}
