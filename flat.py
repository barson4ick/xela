"""Квартира: мебель и декор, гости, записки на стене, кража вещей (если хозяин разрешил)."""
import random
import time
from typing import Optional

import aiosqlite

import gangwar
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc, now_local

SLOTS = 12
ITEMS = {
    "candle": ("🕯", "Свечи", 400, 1), "plant": ("🪴", "Растение", 600, 1), "teddy": ("🧸", "Мишка", 800, 1),
    "picture": ("🖼", "Картина", 1500, 2), "sofa": ("🛋", "Диван", 2500, 2), "bed": ("🛏", "Кровать", 3500, 2),
    "cup": ("🏆", "Кубок", 3000, 2), "cart": ("🍾", "Бар-тележка", 3500, 2), "tv": ("📺", "Телевизор", 5000, 3),
    "aquarium": ("🐠", "Аквариум", 5500, 3), "disco": ("🪩", "Диско-шар", 8000, 4), "slot": ("🎰", "Игровой автомат", 9000, 4),
}
REGEN = {"candle": 2, "plant": 3, "teddy": 3, "picture": 4, "sofa": 8, "bed": 10, "cup": 5, "cart": 6, "tv": 8, "aquarium": 9, "disco": 7, "slot": 6}
REGEN_CAP = 60


def regen_bonus(items) -> float:
    """Доля ускорения восстановления энергии от вещей в квартире (до +60%)."""
    return min(REGEN_CAP, sum(REGEN.get(i, 0) for i in items)) / 100


THEMES = {"gray": ("Серые обои", "#8a8f98", 0), "warm": ("Тёплый беж", "#d9b99b", 500), "mint": ("Мятный", "#a8d5ba", 500),
          "night": ("Ночной синий", "#2b3a67", 800), "pink": ("Розовый", "#e8a0bf", 800)}
REST_GAP, STEAL_GAP, STEAL_CHANCE, NOTE_MAX, NOTES_DAY, REWARD_VISITS = 6 * 3600, 6 * 3600, 0.6, 80, 3, 5


def now() -> int:
    return int(time.time())


def today() -> str:
    return now_local().strftime("%Y-%m-%d")


async def _kget(conn, key: str) -> int:
    return int(await gangwar.kget(conn, key) or 0)


async def _kset(conn, key: str, val) -> None:
    await gangwar.kset(conn, key, str(val))


async def _name(conn, chat_id: int, uid: int) -> str:
    cur = await conn.execute("SELECT username FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return r[0] if r and r[0] else "Кто-то"


async def _items(conn, chat_id: int, uid: int) -> dict:
    cur = await conn.execute("SELECT slot, item FROM flat_item WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    return {r[0]: r[1] for r in await cur.fetchall()}


def _cozy(items: dict) -> int:
    return sum(ITEMS[i][3] for i in items.values() if i in ITEMS)


async def _flat(conn, chat_id: int, uid: int) -> dict:
    cur = await conn.execute("SELECT theme, allow_steal FROM flat WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return {"theme": r[0] if r else "gray", "allow_steal": bool(r[1]) if r else False}


async def view(chat_id: int, viewer: int, owner: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as conn:
        fl = await _flat(conn, chat_id, owner)
        items = await _items(conn, chat_id, owner)
        t = now()
        slots = [{"slot": i, "item": items.get(i, ""), "emoji": ITEMS[items[i]][0] if i in items and items[i] in ITEMS else "",
                  "name": ITEMS[items[i]][1] if i in items and items[i] in ITEMS else ""} for i in range(SLOTS)]
        cur = await conn.execute("SELECT id, author, text, ts FROM flat_note WHERE chat_id = ? AND owner = ? ORDER BY id DESC LIMIT 12", (chat_id, owner))
        notes = []
        for nid, author, text, ts in await cur.fetchall():
            notes.append({"id": nid, "author": author, "name": "📜 Журнал" if author == 0 else await _name(conn, chat_id, author), "text": text, "ts": ts,
                          "can_del": viewer in (owner, author)})
        cur = await conn.execute("SELECT COUNT(*) FROM flat_visit WHERE chat_id = ? AND owner = ? AND day = ?", (chat_id, owner, today()))
        visits_today = (await cur.fetchone())[0]
        out = {"owner": owner, "owner_name": await _name(conn, chat_id, owner), "me": viewer, "self": viewer == owner, "theme": fl["theme"],
               "theme_color": THEMES.get(fl["theme"], THEMES["gray"])[1], "allow_steal": False, "slots": slots, "cozy": _cozy(items),
               "notes": notes, "visits_today": visits_today, "slots_n": SLOTS}
        p = await rp._load(conn, chat_id, viewer)
        out["money"] = p["money"]
        if viewer == owner:
            out["shop"] = [{"key": k, "emoji": v[0], "name": v[1], "price": v[2], "cozy": v[3], "regen": REGEN[k]} for k, v in ITEMS.items()]
            out["regen_bonus"] = int(regen_bonus(items.values()) * 100)
            out["themes"] = [{"key": k, "name": v[0], "color": v[1], "price": v[2], "on": k == fl["theme"]} for k, v in THEMES.items()]
            out["rest_wait"] = max(0, await _kget(conn, f"flatrest:{chat_id}:{viewer}") - t)
            out["rest_energy"] = min(70, 15 + int(regen_bonus(items.values()) * 100))
        else:
            cur = await conn.execute("SELECT 1 FROM flat_visit WHERE chat_id = ? AND owner = ? AND visitor = ? AND day = ?", (chat_id, owner, viewer, today()))
            out["visited"] = bool(await cur.fetchone())
            out["steal_wait"] = max(0, await _kget(conn, f"flatsteal:{chat_id}:{viewer}") - t)
            out["can_steal"] = False
        cur = await conn.execute(
            """SELECT r.user_id, u.username FROM rp_player r JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id
               WHERE r.chat_id = ? AND r.user_id > 0""", (chat_id,))
        nb = []
        for uid, uname in await cur.fetchall():
            its = await _items(conn, chat_id, uid)
            f2 = await _flat(conn, chat_id, uid)
            nb.append({"id": uid, "name": uname, "items": len(its), "cozy": _cozy(its), "open": f2["allow_steal"]})
        out["neighbors"] = sorted(nb, key=lambda x: (-x["cozy"], x["name"]))
        return out


async def _run(chat_id: int, uid: int, fn, owner: Optional[int] = None) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        res = await fn(conn)
        await conn.commit()
    res["flat"] = await view(chat_id, uid, owner if owner is not None else uid)
    return res


def _res(text: str, good: bool = True, delta: int = 0) -> dict:
    return {"text": text, "good": good, "delta": delta}


def _free(items: dict, slot=None) -> int:
    if slot is not None:
        slot = int(slot)
        if not 0 <= slot < SLOTS or slot in items:
            raise GameError("Это место занято")
        return slot
    for i in range(SLOTS):
        if i not in items:
            return i
    raise GameError("В комнате нет места: убери что-нибудь")


async def buy(chat_id: int, uid: int, name: str, item: str, slot=None) -> dict:
    async def run(conn):
        if item not in ITEMS:
            raise GameError("Такой вещи нет")
        emoji, title, price, _c = ITEMS[item]
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < price:
            raise GameError(f"{title} стоит {price} ₽")
        s = _free(await _items(conn, chat_id, uid), slot)
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (price, chat_id, uid))
        await conn.execute("INSERT INTO flat_item (chat_id, user_id, slot, item, ts) VALUES (?, ?, ?, ?, ?)", (chat_id, uid, s, item, now()))
        return _res(f"{emoji} {title} теперь у тебя дома (−{price} ₽).", True, -price)
    return await _run(chat_id, uid, run)


async def sell(chat_id: int, uid: int, name: str, slot) -> dict:
    async def run(conn):
        items = await _items(conn, chat_id, uid)
        slot_i = int(slot)
        if slot_i not in items:
            raise GameError("Здесь ничего нет")
        emoji, title, price, _c = ITEMS[items[slot_i]]
        back = price // 2
        await conn.execute("DELETE FROM flat_item WHERE chat_id = ? AND user_id = ? AND slot = ?", (chat_id, uid, slot_i))
        await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (back, chat_id, uid))
        return _res(f"{emoji} {title} продан за {back} ₽.", True, back)
    return await _run(chat_id, uid, run)


async def theme(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        if key not in THEMES:
            raise GameError("Такой отделки нет")
        title, _c, price = THEMES[key]
        cur = await _flat(conn, chat_id, uid)
        if cur["theme"] == key:
            raise GameError("Эта отделка уже стоит")
        p = await rp._load(conn, chat_id, uid)
        if p["money"] < price:
            raise GameError(f"Ремонт стоит {price} ₽")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (price, chat_id, uid))
        await conn.execute("INSERT INTO flat (chat_id, user_id, theme) VALUES (?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET theme = excluded.theme", (chat_id, uid, key))
        return _res(f"🎨 В комнате теперь «{title}» (−{price} ₽).", True, -price)
    return await _run(chat_id, uid, run)


async def toggle(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        cur = await _flat(conn, chat_id, uid)
        new = 0 if cur["allow_steal"] else 1
        await conn.execute("INSERT INTO flat (chat_id, user_id, allow_steal) VALUES (?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET allow_steal = excluded.allow_steal", (chat_id, uid, new))
        return _res("🔓 Дверь открыта: гости могут пробовать вынести вещи (60% успеха). Не обижайтесь!" if new else "🔒 Дверь заперта: вещи в безопасности.")
    return await _run(chat_id, uid, run)


async def rest(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        left = await _kget(conn, f"flatrest:{chat_id}:{uid}") - now()
        if left > 0:
            raise GameError(f"Отдохнёшь снова через {left // 3600} ч {left % 3600 // 60} мин")
        items = await _items(conn, chat_id, uid)
        p = await rp._load(conn, chat_id, uid)
        gain = min(70, 15 + int(regen_bonus(items.values()) * 100))
        p["energy"] = min(rp.max_energy(p["home"]), p["energy"] + gain)
        await rp._save(conn, chat_id, uid, p)
        await _kset(conn, f"flatrest:{chat_id}:{uid}", now() + REST_GAP)
        return _res(f"🛋 Ты отдохнул дома: ⚡+{gain} (чем уютнее, тем лучше).")
    return await _run(chat_id, uid, run)


async def visit(chat_id: int, uid: int, name: str, owner) -> dict:
    owner = int(owner)

    async def run(conn):
        if owner == uid:
            raise GameError("Это твоя квартира")
        cur = await conn.execute("SELECT 1 FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, owner))
        if not await cur.fetchone():
            raise GameError("Такого жильца нет")
        cur = await conn.execute("SELECT 1 FROM flat_visit WHERE chat_id = ? AND owner = ? AND visitor = ? AND day = ?", (chat_id, owner, uid, today()))
        if await cur.fetchone():
            return _res("Ты уже заглядывал сюда сегодня. Можно оставить записку.")
        cur = await conn.execute("SELECT COUNT(*) FROM flat_visit WHERE chat_id = ? AND owner = ? AND day = ?", (chat_id, owner, today()))
        n = (await cur.fetchone())[0]
        await conn.execute("INSERT INTO flat_visit (chat_id, owner, visitor, day) VALUES (?, ?, ?, ?)", (chat_id, owner, uid, today()))
        p = await rp._load(conn, chat_id, uid)
        p["energy"] = min(rp.max_energy(p["home"]), p["energy"] + 5)
        await rp._save(conn, chat_id, uid, p)
        text = "🚪 Ты зашёл в гости: чай, разговоры, ⚡+5."
        if n < REWARD_VISITS:
            await rp._load(conn, chat_id, owner)
            await conn.execute("UPDATE rp_player SET money = money + 20 WHERE chat_id = ? AND user_id = ?", (chat_id, owner))
            text += " Хозяину прилетели 20 ₽ «за гостеприимство»."
        return _res(text)
    return await _run(chat_id, uid, run, owner)


async def note(chat_id: int, uid: int, name: str, owner, text: str) -> dict:
    owner = int(owner)

    async def run(conn):
        t = " ".join(str(text or "").split())[:NOTE_MAX]
        if not t:
            raise GameError("Напиши хоть что-нибудь")
        cur = await conn.execute("SELECT COUNT(*) FROM flat_note WHERE chat_id = ? AND owner = ? AND author = ? AND ts > ?", (chat_id, owner, uid, now() - 86400))
        if (await cur.fetchone())[0] >= NOTES_DAY and uid != owner:
            raise GameError(f"Не больше {NOTES_DAY} записок в сутки на одну стену")
        await conn.execute("INSERT INTO flat_note (chat_id, owner, author, text, ts) VALUES (?, ?, ?, ?, ?)", (chat_id, owner, uid, t, now()))
        await conn.execute("DELETE FROM flat_note WHERE chat_id = ? AND owner = ? AND id NOT IN (SELECT id FROM flat_note WHERE chat_id = ? AND owner = ? ORDER BY id DESC LIMIT 30)", (chat_id, owner, chat_id, owner))
        return _res("📌 Записка приколота к стене.")
    return await _run(chat_id, uid, run, owner)


async def note_del(chat_id: int, uid: int, name: str, nid, owner) -> dict:
    owner = int(owner)

    async def run(conn):
        cur = await conn.execute("SELECT author FROM flat_note WHERE id = ? AND chat_id = ? AND owner = ?", (int(nid), chat_id, owner))
        r = await cur.fetchone()
        if not r:
            raise GameError("Записки уже нет")
        if uid not in (owner, r[0]):
            raise GameError("Снять чужую записку может только хозяин")
        await conn.execute("DELETE FROM flat_note WHERE id = ?", (int(nid),))
        return _res("🗑 Записка снята.")
    return await _run(chat_id, uid, run, owner)


async def steal(chat_id: int, uid: int, name: str, owner) -> dict:
    owner = int(owner)

    async def run(conn):
        if owner == uid:
            raise GameError("У себя красть незачем")
        fl = await _flat(conn, chat_id, owner)
        if not fl["allow_steal"]:
            raise GameError("Хозяин не разрешил брать вещи: дверь заперта")
        left = await _kget(conn, f"flatsteal:{chat_id}:{uid}") - now()
        if left > 0:
            raise GameError(f"Рано: снова можно через {left // 3600} ч {left % 3600 // 60} мин")
        theirs = await _items(conn, chat_id, owner)
        if not theirs:
            raise GameError("Красть нечего: в квартире пусто")
        mine = await _items(conn, chat_id, uid)
        s = _free(mine)
        await _kset(conn, f"flatsteal:{chat_id}:{uid}", now() + STEAL_GAP)
        oname = await _name(conn, chat_id, owner)
        if random.random() < STEAL_CHANCE:
            slot = random.choice(list(theirs))
            item = theirs[slot]
            emoji, title, _p, _c = ITEMS[item]
            await conn.execute("DELETE FROM flat_item WHERE chat_id = ? AND user_id = ? AND slot = ?", (chat_id, owner, slot))
            await conn.execute("INSERT INTO flat_item (chat_id, user_id, slot, item, ts) VALUES (?, ?, ?, ?, ?)", (chat_id, uid, s, item, now()))
            await conn.execute("INSERT INTO flat_note (chat_id, owner, author, text, ts) VALUES (?, ?, 0, ?, ?)", (chat_id, owner, f"📦 {name} унёс {emoji} {title}", now()))
            await gangwar.say(conn, chat_id, f"🕵 {esc(name)} вынес из квартиры {esc(oname)}: {emoji} {title}! (хозяин разрешил, так что всё по-честному)")
            return _res(f"🕵 Удалось! Ты унёс {emoji} {title} и поставил у себя.")
        fine = 100
        p = await rp._load(conn, chat_id, uid)
        fine = min(p["money"], fine)
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, chat_id, uid))
        await conn.execute("INSERT INTO flat_note (chat_id, owner, author, text, ts) VALUES (?, ?, 0, ?, ?)", (chat_id, owner, f"🕵 {name} пытался что-то вынести, но хозяин проснулся", now()))
        return _res(f"🚨 Хозяин проснулся! Пришлось откупиться: −{fine} ₽.", False, -fine)
    return await _run(chat_id, uid, run, owner)


# ═════════════════════ редкий ИИ-грабитель ═════════════════════
async def tick(conn) -> None:
    """Раз в 2–4 дня ИИ-гражданин может залезть в чужую квартиру. Игроки вещи не берут."""
    import random, time
    t = int(time.time())
    cur = await conn.execute("SELECT DISTINCT chat_id FROM flat_item")
    for (chat_id,) in await cur.fetchall():
        key = f"flatburg:{chat_id}"
        raw = await _kget(conn, key)
        if raw and int(raw) > t:
            continue
        await _kset(conn, key, t + random.randint(2 * 86400, 4 * 86400))
        if random.random() > 0.45:
            continue
        cur = await conn.execute("SELECT user_id, slot, item FROM flat_item WHERE chat_id = ? ORDER BY RANDOM() LIMIT 1", (chat_id,))
        r = await cur.fetchone()
        cur = await conn.execute("SELECT user_id, name FROM ai_citizen WHERE chat_id = ? AND kind = 'citizen' ORDER BY RANDOM() LIMIT 1", (chat_id,))
        ai = await cur.fetchone()
        if not r or not ai:
            continue
        owner, slot, item = r
        ai_uid, ai_name = ai
        await conn.execute("DELETE FROM flat_item WHERE chat_id = ? AND user_id = ? AND slot = ?", (chat_id, owner, slot))
        ic, title, _p = ITEMS.get(item, ("📦", "вещь", 0))
        await conn.execute("INSERT INTO flat_note (chat_id, owner, author, text, ts) VALUES (?, ?, 0, ?, ?)",
                           (chat_id, owner, f"🕵 ИИ-гражданин {ai_name} залез в квартиру и унёс {ic} {title}", t))
        await gangwar.say_ai(conn, chat_id, f"🕵 <b>ИИ-грабитель!</b> {ai_name} залез в квартиру и унёс {ic} {title}. Говорит, что хочет в ОПГ — ищет, кому продать вещь.")
