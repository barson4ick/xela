"""Запуск «Судного дня» (сутки хард-режима).

Пример: CHAT_ID=-100123 python -P tools/doom_start.py 111:@first:perm 222:@second
Аргументы — первые жертвы в виде uid:имя[:perm]; perm означает гибель навсегда, без него — до конца события.
"""
import asyncio
import os
import sys

import aiosqlite

import doom
from config import DB_PATH


async def main():
    chat_id = int(os.environ["CHAT_ID"])
    victims = []
    for arg in sys.argv[1:]:
        uid, name, *rest = arg.split(":")
        victims.append({"uid": int(uid), "name": name, "perm": "perm" in rest, "how": "первая ночь"})
    async with aiosqlite.connect(DB_PATH) as conn:
        d = await doom.start(conn, chat_id, victims)
        await conn.commit()
        print("started; until", d["until"], "snap", len(d["snap"]))


asyncio.run(main())
