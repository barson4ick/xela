"""«Район»: лёгкая шуточная РП-игра в мини-аппе (работа, дела, розыск, тюрьма, имущество)."""
import asyncio
import random
import time
from typing import Dict, List

import aiosqlite

import bar
import chat_events
import gangwar
import database as db
from config import DB_PATH
from rooms import GameError
from utils import day_str

MAX_ENERGY = 100
START_MONEY = 500
CAR_BONUS = 0.15          # прибавка к заработку за уровень машины
WANTED_DECAY = 1800       # раз в 30 минут розыск падает на 1
MAX_WANTED = 5
EXCHANGE_RATE = 900       # ₽ за 1 см
EXCHANGE_CM_DAY = 8
STALL_MAX_HOURS = 8

# (ключ, иконка, название, энергия, (мин, макс), реплики)
JOBS = [
    ("courier", "🌯", "Курьер шаурмы", 10, (30, 55), [
        "Доставил шаурму в гору, и она даже осталась тёплой.",
        "Клиент ждал 40 минут и оставил чаевые из жалости.",
        "Перепутал адреса, но шаурма всё равно ушла.",
        "Собака погналась за тобой до самого подъезда. Заказ жив.",
    ]),
    ("taxi", "🚕", "Таксист", 20, (60, 115), [
        "Пассажир оказался твоим бывшим. Ехали молча, чаевые огромные.",
        "Дядя всю дорогу рассказывал про инфляцию, зато заплатил.",
        "Довёз без пробок. Это чудо, отмечай.",
        "Студент расплатился мелочью и благодарностью.",
    ]),
    ("loader", "📦", "Грузчик", 25, (90, 150), [
        "Таскал диван на пятый этаж без лифта. Поясница в слезах.",
        "Двадцать коробок и один рояль. Платят щедро.",
        "Бригадир сказал «молодец» — это почти как премия.",
    ]),
    ("streamer", "🎮", "Стример", 15, (0, 190), [
        "Пришёл один зритель, но задонатил за троих.",
        "Стрим взорвал чат: рейды, донаты, мемы.",
        "Пять часов эфира, и все смотрели. Но молча.",
        "Забыл включить микрофон. Донаты всё равно были.",
    ]),
    ("hacker", "💻", "Хакер-самоучка", 30, (40, 300), [
        "Взломал домашний вай-фай соседа. Он этого не заметил.",
        "Нашёл баг на сайте шаурмы и получил купон на всю жизнь.",
        "Продал «секретную базу данных» школьникам. Пароль у неё: 123456.",
    ]),
]
JOBS += [
    ("firefighter", "🚒", "Пожарный", 30, (130, 230), [
        "Потушил горящую шаурмичную. Угощали бесплатно, но вкус гари остался.",
        "Снял кота с крыши, кот сказал «мяу» вместо спасибо.",
        "Вызов оказался учебным, зато премия настоящая.",
    ]),
    ("excavator", "🚜", "Экскаваторщик", 25, (110, 190), [
        "Выкопал котлован под торговый центр, который так и не построили.",
        "Случайно откопал клад, но он оказался чужим кабелем.",
        "Весь день ковырял землю и чувствовал себя ребёнком в песочнице.",
    ]),
    ("doctor", "🩺", "Врач скорой", 35, (180, 300), [
        "Лечил больных на скорости 120 км/ч. Все живы, сирена оглушила.",
        "Поставил диагноз «просто устал». Угадал на этот раз.",
        "Принимал роды в лифте. Теперь у ребёнка в паспорте будет этаж.",
    ]),
    ("chef", "👨‍🍳", "Повар", 15, (50, 85), [
        "Перегрел борщ, но гости решили, что так задумано.",
        "Пожарил котлеты на весь район, остался только запах.",
        "Шеф разрешил придумать блюдо. Получилась шаурма с ананасом.",
    ]),
    ("pilot", "✈️", "Пилот-стажёр", 40, (240, 380), [
        "Посадил самолёт мягко, пассажиры аплодировали.",
        "Перепутал рейс, но довёз всех туда, куда хотели.",
        "Автопилот всё сделал сам, а тебя назвали героем.",
    ]),
]
JOB_BY_KEY = {j[0]: j for j in JOBS}

# (ключ, иконка, название, энергия, шанс успеха, (мин, макс), +розыск)
CRIMES = [
    ("kiosk", "🏪", "Ограбить ларёк", 20, 0.6, (120, 240), 1),
    ("car", "🚗", "Угнать тачку", 25, 0.4, (300, 600), 2),
    ("steal", "🥷", "Обокрасть жителя района", 20, 0.5, (0, 0), 1),
    ("atm", "🏧", "Вскрыть банкомат", 30, 0.25, (800, 1600), 3),
]
CRIMES += [
    ("jewel", "💎", "Обчистить ювелирку", 30, 0.30, (1000, 1800), 2),
    ("bank", "🏦", "Ограбить банк", 40, 0.18, (2500, 4500), 4),
]
CRIME_BY_KEY = {c[0]: c for c in CRIMES}
CRIME_CD = {"bank": 6 * 3600, "jewel": 3 * 3600}  # кулдаун на игрока: громкие дела не конвейер
CRIME_WIN = {
    "kiosk": ["Ларёчник отвернулся на секунду, и ты исчез с кассой.", "Взял чипсы, кассу и ушёл как ни в чём не бывало."],
    "car": ["Угнал тачку за 40 секунд и перепродал за углом.", "Тачка завелась с полуоборота. Видимо, судьба."],
    "steal": ["Вытащил кошелёк, пока жертва любовалась голубями.", "Незаметно «одолжил» наличку."],
    "atm": ["Банкомат выдал всё и ещё извинился.", "Сигнализация молчала: сегодня твой день."],
}
CRIME_WIN["jewel"] = ["Витрина лопнула тихо, как мыльный пузырь. Ты вышел с полными карманами.", "Продавец заснул на дежурстве. Бриллианты сами просились в карман."]
CRIME_WIN["bank"] = ["Хранилище открылось, как консервная банка. Сигнализация почему-то молчала.", "Ты вынес мешки, а охранник пожелал хорошего дня. Операция века!"]
CRIME_FAIL = {
    "kiosk": "Продавщица оказалась бывшим борцом. Тебя сдали патрулю.",
    "car": "Тачка оказалась служебной. Участковый уже ждал у выезда.",
    "steal": "Жертва резко обернулась. «Ты что творишь?!» Приехал наряд.",
    "atm": "Банкомат завыл на весь квартал. Тебя взяли с поличным.",
}
CRIME_FAIL["jewel"] = "Сигнализация сработала раньше, чем ты дошёл до витрины. Приехал наряд."
CRIME_FAIL["bank"] = "В банке оказалась вторая сигнализация, о которой не писали в чатах. Спецназ уже у дверей."

# Чёрные дела для одиночек: деньги + уличный авторитет + розыск.
# (ключ, иконка, название, энергия, (мин, макс), шанс, авторитет, розыск, удачная реплика)
DIRTY = [
    ("errand", "📦", "Сгонять за посылкой", 10, (80, 150), 0.88, 1, 0,
     "Забрал посылку, отдал кому надо и не задавал вопросов."),
    ("debt", "🥊", "Выбить долг", 20, (160, 280), 0.72, 2, 1,
     "Должник всё понял и отдал деньги. Даже поблагодарил."),
    ("info", "📱", "Слить информацию", 10, (120, 220), 0.82, 2, 1,
     "Информация ушла, конверт остался. Никто ничего не видел."),
    ("car", "🚗", "Помочь угнать тачку", 25, (240, 400), 0.58, 3, 2,
     "Тачка ушла на разбор за пятнадцать минут. Ты — при деле."),
]
DIRTY_BY_KEY = {d[0]: d for d in DIRTY}
STREET_CRED_JOIN = 10

# уровни имущества: (название, цена)
CARS = [("Пешком", 0), ("Жигули ВАЗ-2107", 1500), ("Лада Приора", 5000), ("Бумер BMW", 15000), ("Гелик", 50000)]
HOMES = [("Подъезд", 0), ("Коммуналка", 2000), ("Хрущёвка", 8000), ("Студия в центре", 25000), ("Пентхаус", 80000)]
STALLS = [("Нет ларька", 0), ("Палатка с шаурмой", 800), ("Ларёк «У Ашота»", 2500), ("Кафе «Шаурма-Хаус»", 7000), ("Сеть «Три Шаурмы»", 20000), ("Шаурма-империя", 60000)]
STALL_INCOME = [0, 12, 30, 70, 150, 300]  # ₽ в час
ASSETS = {"car": CARS, "home": HOMES, "stall": STALLS}

_lock = asyncio.Lock()


def max_energy(home: int) -> int:
    return MAX_ENERGY + 10 * home


def regen_rate(home: int) -> float:
    return 0.6 + 0.15 * home  # энергии в минуту


async def _doom_regen(conn, chat_id: int) -> float:
    try:
        import doom
        return await doom.regen_mult(conn, chat_id)
    except Exception:
        return 1.0


async def _load(conn, chat_id: int, uid: int) -> dict:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    row = await cur.fetchone()
    now = int(time.time())
    if not row:
        await conn.execute(
            "INSERT INTO rp_player (chat_id, user_id, money, energy, energy_ts, wanted_ts, collect_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, uid, START_MONEY, MAX_ENERGY, now, now, now),
        )
        cur = await conn.execute("SELECT * FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        row = await cur.fetchone()
    p = dict(row)
    cur = await conn.execute("SELECT level, hang_until, out_until FROM bar_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    b = await cur.fetchone()
    conn.row_factory = aiosqlite.Row
    p["_lvl"], p["_hang"], p["_out"] = (b[0], b[1] > now, b[2] > now) if b else (0, False, False)
    # ленивое восстановление энергии (на похмелье вдвое медленнее) и спад розыска
    cur_f = await conn.execute("SELECT item FROM flat_item WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    import flat as _flat
    flat_bonus = _flat.regen_bonus([r[0] for r in await cur_f.fetchall()])
    p["_flat_bonus"] = flat_bonus
    rate = regen_rate(p["home"]) * (0.5 if p["_hang"] else 1.0) * (1 + flat_bonus) * await _doom_regen(conn, chat_id)
    p["energy"] = min(max_energy(p["home"]), p["energy"] + (now - p["energy_ts"]) / 60 * rate)
    p["energy_ts"] = now
    if p["wanted"] > 0:
        decay = WANTED_DECAY // 2 if p.get("cop_rep", 0) >= 10 else WANTED_DECAY  # у агентов розыск спадает вдвое быстрее
        steps = (now - p["wanted_ts"]) // decay
        if steps:
            p["wanted"] = max(0, p["wanted"] - int(steps))
            p["wanted_ts"] += int(steps) * decay
    else:
        p["wanted_ts"] = now
    return p


async def _save(conn, chat_id: int, uid: int, p: dict) -> None:
    await conn.execute(
        """UPDATE rp_player SET money = ?, energy = ?, energy_ts = ?, wanted = ?, wanted_ts = ?, jail_until = ?,
           car = ?, home = ?, stall = ?, collect_ts = ?, exch_day = ?, exch_cm = ?, esc_used = ?, job_key = ?, job_rep = ?, job_ts = ?, work_day = ?, work_sum = ?,
           beaten_until = ?, cop_rep = ?, agent = ?, agent_ts = ?, street_cred = ? WHERE chat_id = ? AND user_id = ?""",
        (p["money"], p["energy"], p["energy_ts"], p["wanted"], p["wanted_ts"], p["jail_until"], p["car"], p["home"],
         p["stall"], p["collect_ts"], p["exch_day"], p["exch_cm"], p["esc_used"], p["job_key"], p["job_rep"], p["job_ts"], p["work_day"], p["work_sum"],
         p["beaten_until"], p["cop_rep"], p["agent"], p["agent_ts"], p.get("street_cred", 0), chat_id, uid),
    )


async def _news(conn, chat_id: int, text: str) -> None:
    await conn.execute("INSERT INTO rp_news (chat_id, ts, text) VALUES (?, ?, ?)", (chat_id, int(time.time()), text))


def _invested(levels: list, level: int) -> int:
    """Сколько всего потрачено на имущество: цены всех купленных уровней."""
    return sum(price for _, price in levels[: level + 1])


def _net(p: dict) -> int:
    return p["money"] + _invested(CARS, p["car"]) + _invested(HOMES, p["home"]) + _invested(STALLS, p["stall"])


COP_RANKS = [(0, "Стукач-новичок"), (10, "Агент"), (30, "Опер"), (60, "Майор")]


def cop_rank(rep: int) -> str:
    name = COP_RANKS[0][1]
    for need, title in COP_RANKS:
        if rep >= need:
            name = title
    return name


def _public(p: dict) -> dict:
    now = int(time.time())
    cap = max_energy(p["home"])
    stall_ready = 0
    if p["stall"]:
        hours = min(STALL_MAX_HOURS, (now - p["collect_ts"]) / 3600)
        stall_ready = int(hours * STALL_INCOME[p["stall"]])
    return {
        "money": p["money"], "energy": int(p["energy"]), "max_energy": cap,
        "wanted": p["wanted"], "jail_left": max(0, p["jail_until"] - now), "esc_used": bool(p["esc_used"]),
        "car": p["car"], "home": p["home"], "stall": p["stall"], "stall_ready": stall_ready,
        "exch_left": EXCHANGE_CM_DAY - (p["exch_cm"] if p["exch_day"] == day_str() else 0),
        "net": _net(p),
        "beaten_left": max(0, p.get("beaten_until", 0) - now),
        "cop": {"rep": p.get("cop_rep", 0), "rank": cop_rank(p.get("cop_rep", 0)), "agent": bool(p.get("agent", 0))},
        "street_cred": p.get("street_cred", 0),
        "char": {"icon": p.get("char_icon", ""), "hat": p.get("char_hat", ""), "acc": p.get("char_acc", ""), "bio": p.get("char_bio", ""), "text": char_text(p)},
    }


CHAR_ICONS = ["🧑", "👩", "👨", "🧔", "👱‍♂️", "👱‍♀️", "🧑‍🎤", "🥷", "🕵️", "🤴", "👸", "🧙", "🦸", "🧛", "🧟", "🤖", "👽", "🐯", "🦁", "🐻"]
CHAR_HATS = ["", "🎩", "🧢", "👑", "🎓", "⛑️", "👒", "🎅", "🪖"]
CHAR_ACCS = ["", "🕶️", "🧣", "💼", "🎸", "🎧", "🔫", "🌹", "🧳", "⚽"]


def char_text(p: dict) -> str:
    icon = p.get("char_icon") or "🧑"
    return f"{p.get('char_hat') or ''}{icon}{p.get('char_acc') or ''}"


async def set_char(chat_id: int, uid: int, name: str, icon: str = "", hat: str = "", acc: str = "", bio: str = "") -> dict:
    icon, hat, acc, bio = icon or "", hat or "", acc or "", bio or ""
    if icon and icon not in CHAR_ICONS or hat not in CHAR_HATS or acc not in CHAR_ACCS:
        raise GameError("Такого варианта нет")
    bio = " ".join(bio.split())[:40]
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        await _load(conn, chat_id, uid)
        await conn.execute("UPDATE rp_player SET char_icon = ?, char_hat = ?, char_acc = ?, char_bio = ? WHERE chat_id = ? AND user_id = ?",
                           (icon, hat, acc, bio, chat_id, uid))
        await conn.commit()
    return {"text": "Внешность персонажа обновлена", "delta": 0, "good": True, "state": await state(chat_id, uid)}


def config() -> dict:
    return {
        "stall_hours": STALL_MAX_HOURS,
        "char": {"icons": CHAR_ICONS, "hats": CHAR_HATS, "accs": CHAR_ACCS},
        "jobs": [{"key": k, "icon": i, "title": t, "energy": e, "pay": list(pay)} for k, i, t, e, pay, _ in JOBS],
        "crimes": [{"key": k, "icon": i, "title": t, "energy": e, "chance": int(ch * 100), "pay": list(pay), "wanted": w} for k, i, t, e, ch, pay, w in CRIMES],
        "dirty": [{"key": k, "icon": i, "title": t, "energy": e, "pay": list(pay), "chance": int(ch * 100), "cred": cr, "wanted": w} for k, i, t, e, pay, ch, cr, w, _ in DIRTY],
        "cars": [{"title": t, "price": p} for t, p in CARS],
        "homes": [{"title": t, "price": p} for t, p in HOMES],
        "stalls": [{"title": t, "price": p, "income": STALL_INCOME[i]} for i, (t, p) in enumerate(STALLS)],
        "rate": EXCHANGE_RATE, "cm_day": EXCHANGE_CM_DAY, "car_bonus": int(CAR_BONUS * 100),
    }


async def state(chat_id: int, uid: int) -> dict:
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        await _save(conn, chat_id, uid, p)
        await conn.commit()
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT ts, text FROM rp_news WHERE chat_id = ? ORDER BY id DESC LIMIT 40", (chat_id,))
        news = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            """SELECT r.user_id, u.username, r.money, r.car, r.home, r.stall, r.jail_until, r.char_icon, r.char_hat, r.char_acc FROM rp_player r
               JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id WHERE r.chat_id = ?""",
            (chat_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        import gang
        gang_view = await gang.view(chat_id, uid, conn)
        import aiciv, patrons, power, justice
        ai = await aiciv.view(conn, chat_id)
        patron_state = await patrons.state(conn, chat_id, uid)
        power_state = await power.state(conn, chat_id, uid)
        justice_state = await justice.state(conn, chat_id, uid)
        import onboarding
        onboard = await onboarding.state(conn, chat_id, uid, p, gang_view)
        import events
        boss = await events.boss_state(chat_id, uid)
        try:
            import world
            patron_state["world"] = await world.state(conn, chat_id, uid)
            ai = await world.enrich(conn, chat_id, ai)
        except Exception:
            import logging as _lg
            _lg.getLogger("world").exception("world.state")
        import gangwar as _gw
        dm_off = bool(await _gw.kget(conn, f"dmoff:{chat_id}:{uid}"))
        res_state = None
        try:
            import residents
            res_state = await residents.state(conn, chat_id, uid)
        except Exception:
            import logging as _lg
            _lg.getLogger("residents").exception("residents.state")
        doom_state = None
        try:
            import doom
            doom_state = await doom.state(conn, chat_id, uid)
        except Exception:
            import logging as _lg
            _lg.getLogger("doom").exception("doom.state")
        await conn.commit()
    now = int(time.time())
    board = sorted(
        ({"user_id": r["user_id"], "name": r["username"], "net": _net(r), "jail": r["jail_until"] > now, "car": CARS[r["car"]][0], "char": char_text(r)} for r in rows),
        key=lambda x: -x["net"],
    )[:8]
    out = {"me": _public(p), "news": news, "board": board, "gang": gang_view, "config": config(),
           "ai": ai, "patrons": patron_state, "power": power_state, "justice": justice_state, "onboard": onboard, "boss": boss,
           "todo": [], "notify": {"on": not dm_off}, "res": res_state, "doom": doom_state}
    try:
        import guide
        out["todo"] = guide.build(out)
    except Exception:
        import logging as _lg
        _lg.getLogger("guide").exception("guide.build")
    return out


def _jail_text(p: dict) -> str:
    return f"Ты в тюрьме ещё {max(1, (p['jail_until'] - int(time.time())) // 60 + 1)} мин. Взятка или побег — в разделе «Тюрьма»."


def _need(p: dict, cost: int) -> None:
    if p["_out"]:
        raise GameError("Ты спишь лицом в салате 💤 Дела подождут")
    if p["jail_until"] > time.time():
        raise GameError(_jail_text(p))
    if p.get("beaten_until", 0) > time.time():
        raise GameError(f"Тебя побили за долги в банде. Лежи ещё {int((p['beaten_until'] - time.time()) // 60) + 1} мин.")
    if p["energy"] < cost:
        mins = int((cost - p["energy"]) / regen_rate(p["home"])) + 1
        raise GameError(f"Нет сил: нужно {cost} энергии, подожди около {mins} мин.")


async def work(chat_id: int, uid: int, name: str, key: str) -> dict:
    if key not in JOB_BY_KEY:
        raise GameError("Такой работы нет")
    _, icon, title, cost, (lo, hi), lines = JOB_BY_KEY[key]
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        _need(p, cost)
        import police
        if await police._role(conn, chat_id, uid):
            raise GameError("Ты на службе: обычная работа не для тебя. Выходи на смену в разделе «Полиция».")
        cur = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        if await cur.fetchone():
            raise GameError("Ты в ОПГ: обычная работа не для тебя. Бери контракты и дела банды.")
        pay = random.randint(lo, hi)
        text = random.choice(lines)
        if key == "streamer" and random.random() < 0.25:
            pay, text = random.randint(0, 20), "Стрим не зашёл: чат молчал, друзья делали вид, что заняты."
        if key == "hacker" and random.random() < 0.12:
            p["wanted"] = min(MAX_WANTED, p["wanted"] + 1)
            text += " Но кто-то это заметил, и розыск вырос."
        pay = round(pay * (1 + CAR_BONUS * p["car"]))
        import liveevents
        pay = int(pay * (await liveevents.mods(conn, chat_id))["work_mult"])
        if await chat_events.active(chat_id, "double_pay"):
            pay *= 2
        if key == "police":
            p["cop_rep"] += 1
            if p["cop_rep"] >= 30:
                pay = int(pay * 1.2)
                text += " (Опер: надбавка 20%.)"
        if p["work_day"] != day_str():
            p["work_day"], p["work_sum"] = day_str(), 0
        wmult = 1.0 if p["work_sum"] < 1500 else 0.6 if p["work_sum"] < 3000 else 0.35
        if wmult < 1.0:
            text += " (Смена затянулась: за переработку платят меньше.)"
        pay = int(pay * wmult)
        p["work_sum"] += pay
        import world
        tax, tax_gang = await world.work_tax(conn, chat_id, key, pay, uid)
        pay -= tax
        import balance
        await balance.flow(conn, chat_id, "work", pay)
        await balance.flow(conn, chat_id, "work_tax", tax)
        if tax:
            text += f" Налог банде «{tax_gang}»: −{tax} ₽."
        p["money"] += pay
        p["energy"] -= cost
        await _save(conn, chat_id, uid, p)
        await conn.commit()
    await chat_events.bump(chat_id, uid, "rp")
    return {"text": f"{icon} {title}: {text} +{pay} ₽", "delta": pay, "good": pay > 0, "state": await state(chat_id, uid)}


async def crime(chat_id: int, uid: int, name: str, key: str) -> dict:
    if key not in CRIME_BY_KEY:
        raise GameError("Такого дела нет")
    _, icon, title, cost, chance, (lo, hi), wanted = CRIME_BY_KEY[key]
    victim = None
    stall_extra = 0
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        _need(p, cost)
        import liveevents, police
        if await police._role(conn, chat_id, uid):
            raise GameError("Ты в полиции: бандитские дела не для тебя.")
        cur_gang = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        if not await cur_gang.fetchone():
            raise GameError(f"Одиночкам доступна только «чернуха»: набери {STREET_CRED_JOIN} уличного авторитета и вступай в ОПГ. Открой «Работа → Чёрные дела».")
        md = await liveevents.mods(conn, chat_id)
        from utils import now_local
        if md["night_block"] and (now_local().hour >= 23 or now_local().hour < 7):
            raise GameError("Комендантский час: дела запрещены с 23:00 до 7:00")
        if key == "steal":
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """SELECT r.user_id, u.username, r.money FROM rp_player r JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id
                   WHERE r.chat_id = ? AND r.user_id != ? AND r.money >= 100
                     AND r.user_id NOT IN (SELECT user_id FROM gang_member WHERE gang_id = (SELECT gang_id FROM gang_member WHERE chat_id = ? AND user_id = ?))""",
                (chat_id, uid, chat_id, uid),
            )
            victims = [dict(r) for r in await cur.fetchall()]
            if not victims:
                raise GameError("Грабить некого: у остальных в районе пока пусто в карманах")
            victim = random.choice(victims)
        if key in CRIME_CD:
            import gangwar as _gw
            _until = int(await _gw.kget(conn, f"crcd:{chat_id}:{uid}:{key}") or 0)
            if _until > int(time.time()):
                _left = _until - int(time.time())
                raise GameError(f"«{title}» можно не чаще раза в {CRIME_CD[key] // 3600} ч: ещё {_left // 3600} ч {_left % 3600 // 60} мин")
            await _gw.kset(conn, f"crcd:{chat_id}:{uid}:{key}", str(int(time.time()) + CRIME_CD[key]))
        p["energy"] -= cost
        drunk_pen = 0.1 if p["_lvl"] >= 40 else 0.0  # под градусом дела идут хуже
        cur = await conn.execute(
            """SELECT COUNT(*), (SELECT role FROM gang_member WHERE chat_id = ? AND user_id = ?) FROM gang_member
               WHERE gang_id = (SELECT gang_id FROM gang_member WHERE chat_id = ? AND user_id = ?)""", (chat_id, uid, chat_id, uid))
        gang_cnt, gang_role = await cur.fetchone()
        gang_bonus = min(0.12, 0.03 * max(0, gang_cnt - 1)) + {"boss": 0.05, "deputy": 0.02}.get(gang_role, 0)  # банда и ранг повышают шансы
        chance = min(0.9, max(0.1, chance - 0.04 * p["wanted"] + 0.03 * p["car"] - drunk_pen + gang_bonus - md["crime_penalty"] - await police.patrol_penalty(conn, chat_id)))
        if random.random() < chance:
            if key == "steal":
                loot = max(20, min(400, int(victim["money"] * random.uniform(0.1, 0.2))))
                v = await _load(conn, chat_id, victim["user_id"])
                loot = min(loot, v["money"])
                v["money"] -= loot
                if v["stall"]:
                    _nw = int(time.time())
                    _h = min(STALL_MAX_HOURS, (_nw - v["collect_ts"]) / 3600)
                    stall_extra = min(800, int(_h * 0.25 * STALL_INCOME[v["stall"]]))
                    if stall_extra > 0:
                        v["collect_ts"] = int(_nw - _h * 0.75 * 3600)
                        loot += stall_extra
                await _save(conn, chat_id, victim["user_id"], v)
            else:
                loot = max(1, int(random.randint(lo, hi) * md["crime_pay_mult"]))
            p["money"] += loot
            p["wanted"] = min(MAX_WANTED, p["wanted"] + wanted)
            p["wanted_ts"] = int(time.time())
            text = random.choice(CRIME_WIN[key])
            if key == "steal":
                await _news(conn, chat_id, f"🥷 {name} обчистил {victim['username']} на {loot} ₽")
            else:
                await _news(conn, chat_id, f"{icon} {name}: «{title}» удалось, добыча {loot} ₽, розыск {p['wanted']}⭐")
            if stall_extra > 0:
                text += f" Заодно выгреб часть выручки с ларька (+{stall_extra} ₽)."
            result = {"text": f"{icon} {text} +{loot} ₽, розыск {p['wanted']}⭐", "delta": loot, "good": True}
            import gangplus2
            await gangplus2.log_event(conn, chat_id, uid, "crime", loot)
        else:
            p["wanted"] = min(MAX_WANTED, p["wanted"] + 1)
            p["wanted_ts"] = int(time.time())
            minutes = min(30, 5 + 5 * p["wanted"])
            if p.get("cop_rep", 0) >= 30:  # связи в полиции сокращают срок
                minutes = max(3, int(minutes * 0.7))
            if (p.get("lawyer_until") or 0) > time.time():  # адвокат на связи
                minutes = max(2, int(minutes * 0.6))
            minutes = max(2, int(minutes * md["jail_mult"]))
            fine = min(p["money"], 30 * p["wanted"] + 20)
            if gang_role:  # банда «крышует»: штраф меньше на 30%
                fine = int(fine * 0.7)
            p["money"] -= fine
            p["jail_until"] = int(time.time()) + minutes * 60
            p["esc_used"] = 0
            await _news(conn, chat_id, f"🚔 {name} сел на {minutes} мин: «{title}» провалилось")
            result = {"text": f"🚔 {CRIME_FAIL[key]} Штраф {fine} ₽ и {minutes} мин в тюрьме.", "delta": -fine, "good": False}
            import gangplus2
            await gangplus2.log_event(conn, chat_id, uid, "jail", 1)
        await _save(conn, chat_id, uid, p)
        await conn.commit()
    await chat_events.bump(chat_id, uid, "rp")
    if result.get("good"):
        import gangwar
        await gangwar.war_points(chat_id, uid, "crime")
    result["state"] = await state(chat_id, uid)
    return result


async def dirty(chat_id: int, uid: int, name: str, key: str) -> dict:
    """Чёрное дело одиночки: деньги, уличный авторитет и розыск."""
    if key not in DIRTY_BY_KEY:
        raise GameError("Такого дела нет")
    _, icon, title, cost, (lo, hi), chance, cred, wanted, good_line = DIRTY_BY_KEY[key]
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        _need(p, cost)
        import police
        if await police._role(conn, chat_id, uid):
            raise GameError("Ты в полиции: чёрные дела не для тебя.")
        cur = await conn.execute("SELECT 1 FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        if await cur.fetchone():
            raise GameError("Ты уже в ОПГ: тебе доступны дела банды, а не уличная чернуха.")
        import gangwar
        cd = int(await gangwar.kget(conn, f"dirty:{chat_id}:{uid}") or 0)
        if cd > int(time.time()):
            raise GameError(f"После прошлого дела нужно отлежаться: ещё {max(1, (cd - int(time.time())) // 60)} мин.")
        p["energy"] -= cost
        if random.random() < chance:
            loot = random.randint(lo, hi)
            p["money"] += loot
            p["street_cred"] = int(p.get("street_cred") or 0) + cred
            p["wanted"] = min(MAX_WANTED, p["wanted"] + wanted)
            p["wanted_ts"] = int(time.time())
            text = f"{icon} {good_line} +{loot} ₽, +{cred} уличного авторитета."
            good = True
            delta = loot
        else:
            p["wanted"] = min(MAX_WANTED, p["wanted"] + 1)
            p["wanted_ts"] = int(time.time())
            fine = min(p["money"], 40 + 30 * p["wanted"])
            p["money"] -= fine
            p["jail_until"] = int(time.time()) + (5 + 3 * p["wanted"]) * 60
            p["esc_used"] = 0
            text = f"🚔 Дело сорвалось: штраф {fine} ₽, розыск {p['wanted']}⭐ и {5 + 3 * p['wanted']} мин в тюрьме."
            good = False
            delta = -fine
        await gangwar.kset(conn, f"dirty:{chat_id}:{uid}", str(int(time.time()) + 5 * 60))
        await _save(conn, chat_id, uid, p)
        await _news(conn, chat_id, f"{icon} {name}: «{title}»" + (" — удачно." if good else " — провал."))
        await conn.commit()
    await chat_events.bump(chat_id, uid, "rp")
    return {"text": text, "delta": delta, "good": good, "state": await state(chat_id, uid)}


async def collect(chat_id: int, uid: int, name: str) -> dict:
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        if not p["stall"]:
            raise GameError("Сначала купи ларёк в магазине")
        now = int(time.time())
        hours = min(STALL_MAX_HOURS, (now - p["collect_ts"]) / 3600)
        pay = int(hours * STALL_INCOME[p["stall"]])
        import liveevents
        wm = (await liveevents.mods(conn, chat_id))["work_mult"]
        if wm < 1:
            pay = int(pay * wm)
        if pay < 1:
            raise GameError("Пока копить нечего, загляни позже")
        p["money"] += pay
        p["collect_ts"] = now
        await _save(conn, chat_id, uid, p)
        await conn.commit()
    return {"text": f"🌯 Выручка с ларька: +{pay} ₽", "delta": pay, "good": True, "state": await state(chat_id, uid)}


async def buy(chat_id: int, uid: int, name: str, kind: str) -> dict:
    if kind not in ASSETS:
        raise GameError("Нечего покупать")
    levels = ASSETS[kind]
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        if p[kind] + 1 >= len(levels):
            raise GameError("Уже максимальный уровень, дальше только слава")
        title, price = levels[p[kind] + 1]
        if p["money"] < price:
            raise GameError(f"Не хватает денег: {title} стоит {price} ₽")
        if kind == "stall" and p["stall"] > 0:
            hours = min(STALL_MAX_HOURS, (time.time() - p["collect_ts"]) / 3600)
            p["money"] += int(hours * STALL_INCOME[p["stall"]])  # не теряем накопленную выручку
        p["money"] -= price
        p[kind] += 1
        if kind == "stall":
            p["collect_ts"] = int(time.time())
        if p[kind] >= 3:
            await _news(conn, chat_id, f"💎 {name} прокачался: {title}")
        await _save(conn, chat_id, uid, p)
        await conn.commit()
    return {"text": f"🛒 Куплено: {title} за {price} ₽", "delta": -price, "good": True, "state": await state(chat_id, uid)}


async def exchange(chat_id: int, uid: int, name: str, cm: int) -> dict:
    if cm < 1:
        raise GameError("Укажи, сколько см хочешь купить")
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        used = p["exch_cm"] if p["exch_day"] == day_str() else 0
        if used + cm > EXCHANGE_CM_DAY:
            raise GameError(f"Лимит обмена: ещё {EXCHANGE_CM_DAY - used} см сегодня")
        cost = cm * EXCHANGE_RATE
        if p["money"] < cost:
            raise GameError(f"Нужно {cost} ₽, у тебя {p['money']}")
        p["money"] -= cost
        p["exch_day"], p["exch_cm"] = day_str(), used + cm
        await _save(conn, chat_id, uid, p)
        await conn.commit()
    size = await db.add_size(uid, chat_id, name, cm, "rp")
    return {"text": f"🔁 Обмен: −{cost} ₽, +{cm} см", "delta": -cost, "good": True, "size": size, "state": await state(chat_id, uid)}


async def bribe(chat_id: int, uid: int, name: str) -> dict:
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        if int(await gangwar.kget(conn, f"courtlock:{chat_id}:{uid}") or 0) > time.time():
            raise GameError("Приговор суда: этот срок нельзя закрыть взяткой. Жди или подкупай судью до приговора.")
        if p["jail_until"] <= time.time():
            raise GameError("Ты и так на свободе")
        cost = 150 + 100 * p["wanted"]
        if p.get("cop_rep", 0) >= 60:
            cost //= 2  # майор берёт взятки по-свойски: скидка 50%
        if p["money"] < cost:
            raise GameError(f"Взятка стоит {cost} ₽, у тебя {p['money']}")
        p["money"] -= cost
        p["jail_until"] = 0
        p["wanted"] = max(0, p["wanted"] - 1)
        await _news(conn, chat_id, f"💸 {name} откупился от тюрьмы")
        await _save(conn, chat_id, uid, p)
        await conn.commit()
    return {"text": f"💸 Дежурный закрыл глаза: −{cost} ₽, ты на свободе", "delta": -cost, "good": True, "state": await state(chat_id, uid)}


async def escape(chat_id: int, uid: int, name: str) -> dict:
    async with _lock, aiosqlite.connect(DB_PATH) as conn:
        p = await _load(conn, chat_id, uid)
        if int(await gangwar.kget(conn, f"courtlock:{chat_id}:{uid}") or 0) > time.time():
            raise GameError("Приговор суда: побег невозможен до конца срока.")
        if p["jail_until"] <= time.time():
            raise GameError("Ты и так на свободе")
        if p["esc_used"]:
            raise GameError("Ты уже пробовал бежать в этот срок — охрана начеку. Отсиди или дай взятку.")
        p["esc_used"] = 1
        if random.random() < 0.25:
            p["jail_until"] = 0
            p["wanted"] = min(MAX_WANTED, p["wanted"] + 1)
            await _news(conn, chat_id, f"🏃 {name} сбежал из тюрьмы")
            text, good = "🏃 Побег удался: перелез через забор и растворился в ночи. Розыск вырос.", True
        else:
            p["jail_until"] += 10 * 60
            text, good = "🚔 Побег не удался: поймали у забора. +10 минут к сроку, вторая попытка недоступна.", False
        await _save(conn, chat_id, uid, p)
        await conn.commit()
    return {"text": text, "delta": 0, "good": good, "state": await state(chat_id, uid)}


async def news_facts(chat_id: int, hours: int = 24) -> str:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT text FROM rp_news WHERE chat_id = ? AND ts >= ? ORDER BY id DESC LIMIT 15", (chat_id, int(time.time()) - hours * 3600)
        )
        return "\n".join(f"- {r[0]}" for r in await cur.fetchall())


async def active_chats() -> List[int]:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT chat_id FROM rp_player GROUP BY chat_id HAVING COUNT(*) >= 2")
        return [r[0] for r in await cur.fetchall()]


async def is_jailed(chat_id: int, uid: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT jail_until FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        r = await cur.fetchone()
        return bool(r and r[0] > time.time())
