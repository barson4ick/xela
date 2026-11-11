"""Банк вопросов викторины: ИИ дописывает новые, повторы в чате исключаются."""
import asyncio
import hashlib
import json
import logging
import random
import re
import time
from typing import List, Optional, Tuple

import aiosqlite

from config import DB_PATH, GEMINI_API_KEY
from database import kv_get, kv_set
from utils import day_str

log = logging.getLogger(__name__)

Question = Tuple[str, str, List[str]]  # вопрос, верный ответ, три неверных

BANK_CAP = 600
BATCH = 15
LOW_WATER = 8  # столько непросмотренных осталось в чате — пора дописывать
CATEGORIES = [
    "география", "наука и космос", "история", "кино и сериалы", "музыка", "спорт", "еда и напитки",
    "животные и природа", "интернет и мемы", "технологии и игры", "литература и мультфильмы",
    "человеческое тело", "русский язык и слова", "изобретения", "страны и флаги", "случайные факты",
]
_lock = asyncio.Lock()
_last_try = 0.0


def qhash(question: str) -> str:
    norm = re.sub(r"[^\w]+", " ", question.lower()).strip()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def _valid(q: str, right: str, wrong: List[str]) -> bool:
    opts = [right] + wrong
    return (
        10 <= len(q) <= 220
        and len(wrong) == 3
        and all(1 <= len(o) <= 48 for o in opts)
        and len({o.lower() for o in opts}) == 4
        and right.lower() not in q.lower()
    )


async def bank_size() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        return (await (await db.execute("SELECT COUNT(*) FROM trivia_bank")).fetchone())[0]


async def _bank_questions() -> List[Question]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT q, ans, wrong FROM trivia_bank")
        return [(q, r, json.loads(w)) for q, r, w in await cur.fetchall()]


async def pick(chat_id: int, static: List[Question]) -> Question:
    """Случайный вопрос, которого в этом чате ещё не было; когда всё пройдено — круг начинается заново."""
    pool = {qhash(q[0]): q for q in static}
    for q in await _bank_questions():
        pool.setdefault(qhash(q[0]), q)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT h FROM trivia_seen WHERE chat_id = ?", (chat_id,))
        seen = {r[0] for r in await cur.fetchall()}
        fresh = [h for h in pool if h not in seen]
        if not fresh:
            await db.execute("DELETE FROM trivia_seen WHERE chat_id = ?", (chat_id,))
            fresh = list(pool)
        h = random.choice(fresh)
        await db.execute(
            "INSERT OR REPLACE INTO trivia_seen (chat_id, h, ts) VALUES (?, ?, ?)", (chat_id, h, int(time.time()))
        )
        await db.commit()
    if len(fresh) - 1 < LOW_WATER:
        asyncio.create_task(generate())  # фоном: игрок не ждёт
    return pool[h]


async def generate(batches: int = 1) -> int:
    """Просит ИИ новые вопросы и кладёт в банк. Возвращает, сколько добавлено."""
    global _last_try
    if not GEMINI_API_KEY or _lock.locked() or time.time() - _last_try < 300:
        return 0
    from handlers.chatai import _complete_gemini  # поздний импорт: модули ИИ и игр не зависят друг от друга

    added = 0
    async with _lock:
        _last_try = time.time()
        for _ in range(batches):
            if await bank_size() >= BANK_CAP:
                break
            cats = ", ".join(random.sample(CATEGORIES, 4))
            prompt = (
                f"Придумай {BATCH} вопросов для викторины на русском языке из тем: {cats}. "
                "Нужны проверяемые факты с ОДНОЗНАЧНЫМ ответом, без спорных и устаревающих данных, без вопросов "
                "«какой самый-самый» там, где есть споры. Сложность разная: от лёгких до средних. Вопрос не должен "
                "содержать правильный ответ. У каждого вопроса: один верный ответ и три правдоподобных неверных, "
                "каждый вариант не длиннее 40 символов. Верни ТОЛЬКО JSON-массив без пояснений и markdown: "
                '[{"q": "...", "right": "...", "wrong": ["...", "...", "..."]}]'
            )
            try:
                text = await _complete_gemini("Ты автор точных и интересных вопросов для викторин.", prompt, max_tokens=4000)
            except Exception as exc:  # таймаут или сеть: пробуем позже, игру это не ломает
                log.warning("ИИ не сгенерировал вопросы: %r", exc)
                continue
            if not text:
                continue
            added += await _store(text)
    if added:
        log.info("ИИ дописал %s вопросов викторины", added)
    return added


async def _store(text: str) -> int:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return 0
    try:
        items = json.loads(m.group())
    except json.JSONDecodeError:
        return 0
    added = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for it in items:
            try:
                q, right = str(it["q"]).strip(), str(it["right"]).strip()
                wrong = [str(w).strip() for w in it["wrong"]]
            except (KeyError, TypeError):
                continue
            if not _valid(q, right, wrong):
                continue
            cur = await db.execute(
                "INSERT OR IGNORE INTO trivia_bank (h, q, ans, wrong, created) VALUES (?, ?, ?, ?, ?)",
                (qhash(q), q, right, json.dumps(wrong, ensure_ascii=False), int(time.time())),
            )
            added += cur.rowcount
        await db.commit()
    return added


async def top_up_daily() -> None:
    """Раз в сутки дописывает вопросы про запас (3 пачки)."""
    global _last_try
    key = f"trivia_topup:{day_str()}"
    if await kv_get(key):
        return
    await kv_set(key, "1")
    for _ in range(3):
        _last_try = 0
        await generate()


async def wins_today(chat_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT cnt FROM trivia_wins WHERE chat_id = ? AND user_id = ? AND day = ?", (chat_id, user_id, day_str())
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def add_win(chat_id: int, user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO trivia_wins (chat_id, user_id, day, cnt) VALUES (?, ?, ?, 1)
               ON CONFLICT(chat_id, user_id, day) DO UPDATE SET cnt = cnt + 1""",
            (chat_id, user_id, day_str()),
        )
        await db.commit()
