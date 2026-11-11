"""Бар: напитки и закуски за см, статусы (навеселе, пьяный, в стельку, отключился, похмелье, опохмеляется)
и «заплетающийся язык» в чате."""
import asyncio
import random
import re
import time
from typing import Dict, List, Optional, Tuple

import aiosqlite

from config import DB_PATH
from rooms import GameError

DECAY = 1.0              # градусов в минуту уходит само
STATUS_MINUTES = 10      # каждый статус держится около 10 минут
OUT_MINUTES = STATUS_MINUTES   # сколько спишь после «перебора»
OPO_MINUTES = STATUS_MINUTES   # статус «опохмеляется»
HANG_MINUTES = STATUS_MINUTES  # похмелье

# id, иконка, название, цена в см, градусы
DRINKS = [
    ("beer", "🍺", "Пиво", 1, 8),
    ("wine", "🍷", "Вино", 2, 12),
    ("cocktail", "🍹", "Коктейль", 3, 12),
    ("vodka", "🥃", "Водка", 3, 20),
    ("whisky", "🥃", "Виски", 4, 22),
    ("champagne", "🍾", "Шампанское", 5, 15),
    ("moonshine", "🫗", "Самогон", 2, 28),
]
# id, иконка, название, цена, сколько градусов убирает
SNACKS = [
    ("pickle", "🥒", "Солёный огурчик", 1, 6),
    ("chips", "🥔", "Сухарики", 1, 5),
    ("shawarma", "🌯", "Шаурма", 3, 15),
    ("pizza", "🍕", "Пицца", 3, 15),
    ("kebab", "🍖", "Шашлык", 4, 20),
]
# id, иконка, название, цена: снимают похмелье
CURES = [
    ("brine", "🥤", "Рассол", 1),
    ("aspirin", "💊", "Аспирин", 2),
]
ITEMS: Dict[str, tuple] = {i[0]: ("drink",) + i for i in DRINKS}
ITEMS.update({i[0]: ("snack",) + i for i in SNACKS})
ITEMS.update({i[0]: ("cure",) + i for i in CURES})

DRINK_LINES = {
    "beer": ["Холодненькое зашло как надо.", "Пена до потолка, настроение тоже."],
    "wine": ["Ты сразу почувствовал себя эстетом.", "Бокал красного, и жизнь налаживается."],
    "cocktail": ["С зонтиком, значит, всё серьёзно.", "Бармен сказал, что это «авторский». Ты поверил."],
    "vodka": ["Холодная, как отношение бывшей. Пошла.", "Рюмка пошла хорошо, без закуски — зря."],
    "whisky": ["Со льдом и с видом на глубокие мысли.", "Дымно, дорого, умно."],
    "champagne": ["Хлопок на всё заведение! Праздник удался.", "Пузырьки щекочут нос, а мысли не слушаются."],
    "moonshine": ["Бабушкин рецепт. Предупреждать надо было.", "Глаза слезятся, но уважение появилось."],
}
SNACK_LINES = {
    "pickle": "Хрустнул огурчиком, и мир стал чуть яснее.",
    "chips": "Сухарики хрустят, мысли возвращаются.",
    "shawarma": "Шаурма творит чудеса: голова как новая.",
    "pizza": "Горячая пицца вернула тебя к жизни.",
    "kebab": "Шашлык закрыл вопрос почти полностью.",
}


def _lvl_code(level: float) -> int:
    return 0 if level < 15 else 1 if level < 40 else 2 if level < 70 else 3


STATUS_NAMES = {
    "sober": ("🙂", "Трезвый"), "tipsy": ("😄", "Навеселе"), "drunk": ("🥴", "Пьяный"),
    "wasted": ("🤪", "В стельку"), "out": ("💤", "Отключился"), "hang": ("🤕", "Похмелье"), "opo": ("🍻", "Опохмеляется"),
}


def status_key(p: dict, now: Optional[int] = None) -> str:
    now = now or int(time.time())
    if p["out_until"] > now:
        return "out"
    if p["hang_until"] > now:
        return "hang"
    if p["opo_until"] > now:
        return "opo"
    code = _lvl_code(p["level"])
    if code == 0 and p["tipsy_until"] > now:
        code = 1  # любая выпивка держит статус минимум 10 минут
    return ("sober", "tipsy", "drunk", "wasted")[code]


def effect_level(p: dict) -> int:
    """0 — нет эффекта, 1..3 — искажение речи, 4 — спит."""
    key = status_key(p)
    if key == "out":
        return 4
    if key == "hang":
        return 0
    return 1 if (_lvl_code(p["level"]) == 0 and p["tipsy_until"] > time.time()) else _lvl_code(p["level"])


async def _load(conn, chat_id: int, uid: int) -> dict:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM bar_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    row = await cur.fetchone()
    now = int(time.time())
    if not row:
        return {"chat_id": chat_id, "user_id": uid, "level": 0.0, "ts": now, "peak": 0.0,
                "hang_until": 0, "out_until": 0, "opo_until": 0, "tipsy_until": 0, "drinks": 0}
    p = dict(row)
    prev = p["level"]
    p["level"] = max(0.0, prev - (now - p["ts"]) / 60 * DECAY)
    # протрезвел после серьёзной попойки: дальше похмелье
    if p["peak"] >= 40 and p["level"] <= 10:
        crossed = p["ts"] + max(0.0, prev - 10) / DECAY * 60
        p["hang_until"] = max(p["hang_until"], int(crossed) + HANG_MINUTES * 60)
        p["peak"] = 0.0
    p["ts"] = now
    return p


async def _save(conn, p: dict) -> None:
    await conn.execute(
        """INSERT INTO bar_player (chat_id, user_id, level, ts, peak, hang_until, out_until, opo_until, tipsy_until, drinks)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(chat_id, user_id) DO UPDATE SET level = excluded.level, ts = excluded.ts, peak = excluded.peak,
           hang_until = excluded.hang_until, out_until = excluded.out_until, opo_until = excluded.opo_until,
           tipsy_until = excluded.tipsy_until, drinks = excluded.drinks""",
        (p["chat_id"], p["user_id"], p["level"], p["ts"], p["peak"], p["hang_until"], p["out_until"], p["opo_until"], p["tipsy_until"], p["drinks"]),
    )


async def get(chat_id: int, uid: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        if p["drinks"] or p["level"] or p["hang_until"]:
            await _save(conn, p)
            await conn.commit()
        return p


def public(p: dict) -> dict:
    now = int(time.time())
    key = status_key(p, now)
    icon, name = STATUS_NAMES[key]
    return {
        "key": key, "icon": icon, "name": name, "level": int(p["level"]),
        "left": (
            max(p["out_until"], p["hang_until"], p["opo_until"]) - now if key in ("out", "hang", "opo")
            else max(0, p["tipsy_until"] - now) if key == "tipsy" and p["level"] < 15 else 0
        ),
        "drinks": p["drinks"],
    }


def config() -> dict:
    return {
        "drinks": [{"id": i, "icon": ic, "title": t, "price": pr, "proof": d} for i, ic, t, pr, d in DRINKS],
        "snacks": [{"id": i, "icon": ic, "title": t, "price": pr, "cut": c} for i, ic, t, pr, c in SNACKS],
        "cures": [{"id": i, "icon": ic, "title": t, "price": pr} for i, ic, t, pr in CURES],
    }


async def buy(chat_id: int, uid: int, name: str, item_id: str) -> dict:
    """Покупка напитка, закуски или средства от похмелья за см."""
    from webdb import apply_bet  # списание см атомарно
    item = ITEMS.get(item_id)
    if not item:
        raise GameError("Такого в меню нет")
    kind, _, icon, title, price = item[:5]
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        if p["out_until"] > now:
            raise GameError("Ты в отключке, до утра обслуживать не будут 💤")
        if kind == "cure" and p["hang_until"] <= now:
            raise GameError("Похмелья нет, лечить нечего")
        if kind == "snack" and p["level"] < 1:
            raise GameError("Ты и так трезв, закусывать нечем")
    import chat_events
    if await chat_events.active(chat_id, "free_bar"):
        price = 0  # счастливый час: бесплатно
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute("SELECT size FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            row = await cur.fetchone()
        size = row[0] if row else 0
    else:
        import liveevents
        disc = (await liveevents.get_mods(chat_id))["bar_discount"]
        import gangplus8
        disc = min(0.6, disc + await gangplus8.bar_discount(chat_id, uid))
        if disc:
            price = max(1, int(round(price * (1 - disc))))
        size = await apply_bet(uid, chat_id, price, 0, "bar")
    if size is None:
        raise GameError(f"Не хватает см: {title} стоит {price}")
    async with aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        text = ""
        if kind == "drink":
            units = item[5]
            was_hang = p["hang_until"] > now
            p["level"] = min(100.0, p["level"] + units)
            p["peak"] = max(p["peak"], p["level"])
            p["drinks"] += 1
            p["tipsy_until"] = max(p["tipsy_until"], now + STATUS_MINUTES * 60)
            if was_hang:
                p["hang_until"] = 0
                p["opo_until"] = now + OPO_MINUTES * 60
                text = f"{icon} {title}: клин клином вышибают, ты опохмеляешься. "
            text += random.choice(DRINK_LINES[item_id])
            if p["level"] >= 100:
                p["out_until"] = now + OUT_MINUTES * 60
                p["level"] = 70.0
                text += " Перебор! Ты уснул лицом в салате 💤"
        elif kind == "snack":
            p["level"] = max(0.0, p["level"] - item[5])
            text = SNACK_LINES[item_id]
        else:
            p["hang_until"] = 0
            p["opo_until"] = 0
            text = "Полегчало: голова почти не гудит 🙌"
        p["ts"] = now
        await _save(conn, p)
        await conn.commit()
    return {"text": f"{icon} {title} −{price} см. {text}", "size": size, "good": True, "bar": public(p)}


# ───────────────────────── заплетающийся язык ─────────────────────────

SLUR_WORDS = {
    "привет": "прывееет", "да": "дааа", "нет": "неее", "я": "я-я", "ты": "ты-ы", "друг": "брат",
    "друзья": "братва", "что": "чё", "почему": "пачему", "хорошо": "афигенно", "пока": "пака",
    "ладно": "лана", "сейчас": "щас", "спасибо": "спасииибо", "люблю": "люблююю", "уважаю": "уваааажаю",
    "давай": "давааай", "конечно": "канешна", "всех": "всеххх", "привет,": "прывет,",
}
INSERTS = ["*ик*", "я тебя уважаю", "ты мне как брат", "а чё такое?", "щас спою", "мы тут все свои", "*ик*", "ну типа"]
VOWELS = "аеёиоуыэюяaeiou"


def _word(w: str, level: int) -> str:
    low = w.lower()
    if level >= 2 and low in SLUR_WORDS and random.random() < 0.7:
        return SLUR_WORDS[low]
    p = {1: 0.25, 2: 0.45, 3: 0.7}[level]
    if len(w) < 3 or random.random() > p:
        return w
    op = random.choice(["swap", "drop", "stretch", "case"] + (["scramble", "stretch"] if level == 3 else []))
    chars = list(w)
    if op == "swap" and len(chars) >= 4:
        i = random.randrange(1, len(chars) - 2)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
    elif op == "drop":
        del chars[random.randrange(1, len(chars) - 1)]
    elif op == "stretch":
        idx = [i for i, c in enumerate(chars) if c.lower() in VOWELS]
        if idx:
            i = random.choice(idx)
            chars[i] = chars[i] * random.randint(2, 4)
    elif op == "case":
        i = random.randrange(len(chars))
        chars[i] = chars[i].upper() if chars[i].islower() else chars[i].lower()
    elif op == "scramble" and len(chars) >= 5:
        mid = chars[1:-1]
        random.shuffle(mid)
        chars = [chars[0]] + mid + [chars[-1]]
    return "".join(chars)


def drunkify(text: str, level: int) -> str:
    """Искажает текст «пьяной» речью (level 1..3)."""
    parts = re.findall(r"\w+|\W+", text)
    out = [(_word(t, level) if t[0].isalpha() else t) for t in parts]
    result = "".join(out)
    if level >= 2 and random.random() < 0.6:
        words = result.split(" ")
        words.insert(random.randrange(len(words) + 1), random.choice(INSERTS))
        result = " ".join(words)
    elif level == 1 and random.random() < 0.3:
        result += " *ик*"
    if level == 3 and random.random() < 0.5:
        result += " " + random.choice(["...", "ыыы", "*ик*"])
    if result == text:  # гарантируем, что пьяная речь заметна
        result = f"{text} *ик*" if level == 1 else _word(text.split(" ")[0] + "ооо", 3) + text[len(text.split(" ")[0]):]
    return result


async def ai_drunkify(text: str) -> Optional[str]:
    """Для «в стельку»: ИИ переписывает фразу заплетающимся языком (если не вышло, будет обычное искажение)."""
    from handlers.chatai import _complete_gemini, ai_enabled
    if not ai_enabled():
        return None
    try:
        res = await asyncio.wait_for(
            _complete_gemini(
                "Ты переписываешь сообщения так, будто их печатает очень пьяный человек в чате.",
                "Перепиши сообщение пьяным языком: ошибки, потеря и перестановка букв, растянутые гласные, пара слов "
                "заменена на пьяные выражения («я тебя уважаю», «ты мне как брат»). Смысл должен угадываться. "
                "Без мата и оскорблений, без пояснений, одной строкой, не длиннее исходного больше чем вдвое.\n\n"
                f"Сообщение: {text[:300]}",
                max_tokens=200,
            ),
            timeout=8,
        )
    except Exception:
        return None
    return res.strip().strip('"«»')[:400] if res else None
