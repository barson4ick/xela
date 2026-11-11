"""Пьяная дуэль: градус влияет на шанс попадания, после боя можно опохмелиться."""
import random
import sqlite3
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import bar
from config import DB_PATH

MISS = [0.0, 0.10, 0.25, 0.40]      # шанс промаха по градусу
BONUS = [0, 0, 2, 3]                # «пьяная удаль»: урон выше
LABEL = ["трезв", "навеселе", "пьян", "в стельку"]


def level_code(chat_id: int, uid: int) -> int:
    """0..3: насколько боец пьян (похмелье тоже мешает целиться)."""
    try:
        con = sqlite3.connect(DB_PATH, timeout=2)
        try:
            row = con.execute("SELECT level, ts, hang_until, out_until, tipsy_until FROM bar_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid)).fetchone()
        finally:
            con.close()
    except Exception:
        return 0
    if not row:
        return 0
    now = int(time.time())
    level = max(0.0, row[0] - (now - row[1]) / 60 * bar.DECAY)
    code = bar._lvl_code(level)
    if code == 0 and row[4] > now:
        code = 1
    if row[2] > now:
        code = max(code, 1)
    if row[3] > now:
        code = 3
    return code


def swing(chat_id: int, uid: int, dmg: int) -> tuple:
    """Возвращает (новый урон, пояснение или пустая строка)."""
    code = level_code(chat_id, uid)
    if not code or dmg <= 0:
        return dmg, ""
    if random.random() < MISS[code]:
        return 0, f" 🥴 Но боец {LABEL[code]}, и удар уходит мимо!"
    if BONUS[code]:
        return dmg + BONUS[code], f" 🥃 Пьяная удаль: +{BONUS[code]} HP."
    return dmg, ""


def cure_kb(chat_id: int, players: list):
    """Кнопки «опохмелиться» для тех, кто остался на градусе. players: [(uid, name)]."""
    rows = []
    for uid, name in players:
        if level_code(chat_id, uid):
            rows.append([InlineKeyboardButton(text=f"🌯 {name[:18]}: опохмелиться (3 см)", callback_data=f"dcure:{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
