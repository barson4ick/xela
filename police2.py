"""Полиция-2: оперативная сводка по ОПГ, вызов на допрос, ордер на обыск, проверка точек, подставной адвокат, ИИ-офицеры."""
import json
import logging
import random
import time
from typing import Optional

import aiosqlite

import gangplus3 as P3
import gangplus5 as P5
import gangwar
import police as PO
import rp
from config import DB_PATH
from rooms import GameError
from utils import esc

log = logging.getLogger(__name__)
SUMMON_MIN, WARRANT_COST, INSPECT_COST, OP_CD = 20, 300, 300, 10 * 60
AI_NAMES = {-9101: "Сержант Петров", -9102: "Лейтенант Громова", -9103: "Капитан Сидоренко"}
AI_MODES = {"off": "выключены", "normal": "обычный режим", "strict": "строгий режим"}


def now() -> int:
    return int(time.time())


# ═════════════════════ оперативная сводка ═════════════════════
ALERT_TEXT = {"heist": "провернула налёт", "crime": "участник вышел на дело", "bigheist": "готовит/провернула большое дело", "rheist": "налёт по ролям",
              "jail": "участник попал за решётку", "meet": "собирает тайную сходку", "trade": "торгует товаром", "project": "строит общий проект",
              "pot": "собирает общий котёл", "racket": "собрала дань с точек", "order": "подозрительная активность: возможен заказ на избиение"}


async def alert(conn, chat_id: int, gid: int, kind: str, text: str = "") -> None:
    if kind not in ALERT_TEXT and not text:
        return
    await conn.execute("INSERT INTO police_alert (chat_id, gang_id, kind, text, ts) VALUES (?, ?, ?, ?, ?)", (chat_id, gid, kind, text or ALERT_TEXT[kind], now()))
    await conn.execute("DELETE FROM police_alert WHERE chat_id = ? AND id NOT IN (SELECT id FROM police_alert WHERE chat_id = ? ORDER BY id DESC LIMIT 60)", (chat_id, chat_id))


DIGEST_KINDS = {"tip": ("🕵", "доносы"), "crime": ("🥷", "выходы на дело"), "heist": ("💰", "налёты"), "bigheist": ("🏦", "крупные дела"), "rheist": ("💰", "налёты с ролями"),
                "jail": ("🚔", "аресты"), "order": ("🥊", "заказы"), "pot": ("🍲", "сборы в котёл"), "project": ("🏗", "стройки"), "racket": ("🏪", "сбор дани"),
                "trade": ("📦", "сделки"), "warcops": ("⚔️", "стрелки"), "meet": ("🗣", "сходки")}


async def digest(conn, chat_id: int, hours: int = 24) -> list:
    """Сводка за сутки: по каждой банде — что и сколько раз, размер дела и на что его уже хватает."""
    t = now()
    cur = await conn.execute("SELECT a.gang_id, a.kind, COUNT(*), MAX(a.ts) FROM police_alert a WHERE a.chat_id = ? AND a.ts > ? GROUP BY a.gang_id, a.kind", (chat_id, t - hours * 3600))
    by = {}
    for gid, kind, n, last in await cur.fetchall():
        ic, label = DIGEST_KINDS.get(kind, ("📡", kind))
        by.setdefault(gid, []).append({"icon": ic, "label": label, "n": n, "last": last})
    cur = await conn.execute("SELECT g.id, g.name, g.is_ai, COALESCE(c.pts, 0), COALESCE(c.wiretap_until, 0) FROM gang g LEFT JOIN police_case c ON c.gang_id = g.id AND c.chat_id = g.chat_id "
                             "WHERE g.chat_id = ? AND g.is_ai < 2", (chat_id,))
    out = []
    for gid, gname, gai, pts, wire in await cur.fetchall():
        pts = int(pts or 0)
        items = sorted(by.get(gid, []), key=lambda x: -x["n"])
        if not items and pts < PO.CASE_RAID:
            continue
        hint = "хватает на громкое дело" if pts >= PO.CASE_BIG else "хватает на облаву" if pts >= PO.CASE_RAID else f"до ордера на облаву ещё {PO.CASE_RAID - pts}"
        out.append({"gang": gname, "ai": bool(gai), "pts": pts, "big": PO.CASE_BIG, "hint": hint, "wire": max(0, int(wire or 0) - t),
                    "items": items[:5], "last": max((x["last"] for x in items), default=0)})
    out.sort(key=lambda x: -x["pts"])
    return out


# ═════════════════════ вызов на допрос ═════════════════════
async def summon(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        await PO._need_cop(conn, chat_id, uid)
        if await PO._kget(conn, f"pcd:summon:{chat_id}:{uid}") > now():
            raise GameError("Вызывать на допрос можно раз в 10 минут")
        if target == uid or target == PO.CHIEF_ID or await PO.is_officer(conn, chat_id, target):
            raise GameError("Своих не допрашивают")
        cur = await conn.execute("SELECT 1 FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        if not await cur.fetchone():
            raise GameError("Такого жителя нет")
        cur = await conn.execute("SELECT 1 FROM police_summon WHERE chat_id = ? AND target = ? AND status = 'open'", (chat_id, target))
        if await cur.fetchone():
            raise GameError("Его уже вызвали")
        await PO._kset(conn, f"pcd:summon:{chat_id}:{uid}", now() + OP_CD)
        await conn.execute("INSERT INTO police_summon (chat_id, target, by_uid, created, expires, status) VALUES (?, ?, ?, ?, ?, 'open')", (chat_id, target, uid, now(), now() + SUMMON_MIN * 60))
        tn = await P3._uname(conn, chat_id, target)
        await gangwar.say(conn, chat_id, f"⚖️ Полиция вызвала на допрос {esc(tn)}. На явку {SUMMON_MIN} минут (мини-апп → Район).")
        return P3._res(f"⚖️ {tn} вызван на допрос на {SUMMON_MIN} минут. Не явится: розыск +1⭐ и 5 минут в камере.")
    return await P3._act(chat_id, uid, run)


async def summon_answer(chat_id: int, uid: int, name: str, sid, choice: str) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM police_summon WHERE id = ? AND chat_id = ? AND target = ? AND status = 'open'", (int(sid), chat_id, uid))
        r = await cur.fetchone()
        if not r or r["expires"] < now():
            raise GameError("Вызов уже неактуален")
        sm = dict(r)
        p = await rp._load(conn, chat_id, uid)
        gid = await PO._own_gang(conn, chat_id, uid)
        await conn.execute("UPDATE police_summon SET status = 'done' WHERE id = ?", (sm["id"],))
        if choice == "testify":
            reward = min(300, await PO.fund(conn, chat_id))
            await PO.fund_add(conn, chat_id, -reward)
            p["money"] += reward
            p["wanted"] = max(0, p["wanted"] - 1)
            await rp._save(conn, chat_id, uid, p)
            text = f"🗣 Ты дал показания: +{reward} ₽ и розыск −1."
            if gid:
                await PO.case_add(conn, chat_id, gid, 8)
                if random.random() < 0.30:
                    from gangplus2 import _merit
                    await _merit(conn, chat_id, uid, -15)
                    await rp._news(conn, chat_id, f"🕵 В банде шепчутся, что {name} разговаривал с полицией")
                    text += " Но банда что-то заподозрила: авторитет −15."
            return P3._res(text, True, reward)
        if choice == "lawyer":
            if (p.get("lawyer_until") or 0) <= now():
                await conn.execute("UPDATE police_summon SET status = 'open' WHERE id = ?", (sm["id"],))
                raise GameError("Адвоката нет: нанять можно в «Имуществе»")
            return P3._res("⚖️ Пришёл с адвокатом: допрос закончился ничем.")
        if choice == "silent":
            if random.random() < 0.4:
                fine = min(p["money"], 100)
                p["money"] -= fine
                p["jail_until"], p["esc_used"] = now() + 10 * 60, 0
                await PO.fund_add(conn, chat_id, fine)
                await rp._save(conn, chat_id, uid, p)
                return P3._res(f"🤐 Ты молчал, но следователь не поверил: 10 минут в камере и штраф {fine} ₽.", False, -fine)
            return P3._res("🤐 Ты молчал, как рыба: отпустили.")
        raise GameError("Выбери: показания, молчать или адвокат")
    return await P3._act(chat_id, uid, run)


# ═════════════════════ ордер на обыск ═════════════════════
async def search(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        await PO._need_cop(conn, chat_id, uid, True)
        if await PO._kget(conn, f"pcd:search:{chat_id}") > now():
            raise GameError("Ордера выписываются раз в 10 минут")
        if target == PO.CHIEF_ID or await PO.is_officer(conn, chat_id, target):
            raise GameError("Своих не обыскивают")
        tp = await rp._load(conn, chat_id, target)
        gid = await PO._own_gang(conn, chat_id, target)
        if gid and gid == await PO._own_gang(conn, chat_id, uid):
            raise GameError("Конфликт интересов")
        case = await PO._case(conn, chat_id, gid) if gid else None
        if tp["wanted"] < 3 and not (case and case["pts"] >= 40):
            raise GameError("Основания нет: розыск от 3⭐ или дело на банду от 40")
        if await PO.fund(conn, chat_id) < WARRANT_COST:
            raise GameError(f"В фонде нет {WARRANT_COST} ₽ на ордер")
        await PO.fund_add(conn, chat_id, -WARRANT_COST)
        await PO._kset(conn, f"pcd:search:{chat_id}", now() + OP_CD)
        tn = await P3._uname(conn, chat_id, target)
        if (tp.get("lawyer_until") or 0) > now() and random.random() < 0.5:
            return P3._res(f"⚖️ Адвокат {tn} опротестовал ордер: обыск сорван, деньги на ордер потрачены.", False)
        import flat
        items = await flat._items(conn, chat_id, target)
        found = []
        if "slot" in items.values():
            slot = next(s for s, i in items.items() if i == "slot")
            await conn.execute("DELETE FROM flat_item WHERE chat_id = ? AND user_id = ? AND slot = ?", (chat_id, target, slot))
            fine = min(tp["money"], 500)
            await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (fine, chat_id, target))
            await PO.fund_add(conn, chat_id, fine)
            found.append(f"🎰 игровой автомат (штраф {fine} ₽)")
            items.pop(slot)
        elif items and random.random() < 0.35:
            slot = random.choice(list(items))
            emoji, title = flat.ITEMS[items[slot]][0], flat.ITEMS[items[slot]][1]
            await conn.execute("DELETE FROM flat_item WHERE chat_id = ? AND user_id = ? AND slot = ?", (chat_id, target, slot))
            found.append(f"{emoji} {title} изъят «как улика»")
        cur = await conn.execute("SELECT cigs FROM jail_cig WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        cur2 = await conn.execute("SELECT amount FROM stash WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        st = await cur2.fetchone()
        if st and st[0] > 0 and random.random() < 0.5:
            take = st[0] // 2
            await conn.execute("UPDATE stash SET amount = amount - ? WHERE chat_id = ? AND user_id = ?", (take, chat_id, target))
            await PO.fund_add(conn, chat_id, take)
            found.append(f"тайник: изъято {take} ₽")
        if gid:
            await PO.case_add(conn, chat_id, gid, 6 if found else 2)
        text = "; ".join(found) if found else "ничего не нашли"
        await conn.execute("INSERT INTO flat_note (chat_id, owner, author, text, ts) VALUES (?, ?, 0, ?, ?)", (chat_id, target, f"🚔 обыск по ордеру: {text}", now()))
        await gangwar.say(conn, chat_id, f"🚔 Полиция провела обыск у {esc(tn)} по ордеру: {esc(text)}.")
        return P3._res(f"🚔 Обыск у {tn}: {text}.", bool(found))
    return await P3._act(chat_id, uid, run)


async def inspect_point(chat_id: int, uid: int, name: str, key: str) -> dict:
    async def run(conn):
        await PO._need_cop(conn, chat_id, uid, True)
        import gangplus8 as P8
        pts = {r["key"]: r for r in await P8._rackets(conn, chat_id)}
        if key not in pts or not pts[key]["owner"]:
            raise GameError("Проверять нечего: точка без крыши")
        g = await gangwar._gang_row(conn, pts[key]["owner"])
        if not g:
            raise GameError("У точки нет владельца")
        import power
        if await power.has_roof(conn, chat_id, g["id"]):
            raise GameError("Мэр прикрыл эту точку: проверка отменяется")
        if g["id"] == await PO._own_gang(conn, chat_id, uid):
            raise GameError("Конфликт интересов: это точка твоей банды")
        if await PO._kget(conn, f"pcd:inspect:{chat_id}") > now():
            raise GameError("Проверки раз в 10 минут")
        await PO._kset(conn, f"pcd:inspect:{chat_id}", now() + OP_CD)
        fine = min(400, g["treasury"] or 0)
        await conn.execute("UPDATE gang SET treasury = treasury - ? WHERE id = ?", (fine, g["id"]))
        await PO.fund_add(conn, chat_id, fine)
        await conn.execute("UPDATE racket SET rebel_until = ?, pending = pending / 2 WHERE chat_id = ? AND key = ?", (now() + 12 * 3600, chat_id, key))
        await PO.case_add(conn, chat_id, g["id"], 5)
        ic, title, _t = P8.POINTS[key]
        await gangwar.say(conn, chat_id, f"{ic} Полиция проверила «{title}» под крышей «{esc(g['name'])}»: штраф {fine} ₽, точка закрыта на 12 часов, накопленная дань уполовинена.")
        return P3._res(f"{ic} Проверка «{title}»: штраф {fine} ₽ в фонд, точка остановлена на 12 часов.")
    return await P3._act(chat_id, uid, run)


# ═════════════════════ подставной адвокат ═════════════════════
async def lawyer_offer(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)

    async def run(conn):
        await PO._need_cop(conn, chat_id, uid)
        tp = await rp._load(conn, chat_id, target)
        if tp["jail_until"] <= now():
            raise GameError("Он на свободе")
        gid = await PO._own_gang(conn, chat_id, target)
        if not gid:
            raise GameError("Подставной адвокат нужен для членов банд")
        cur = await conn.execute("SELECT 1 FROM police_lawyer WHERE chat_id = ? AND target = ? AND status = 'open'", (chat_id, target))
        if await cur.fetchone():
            raise GameError("Адвокат уже предложен")
        await conn.execute("INSERT INTO police_lawyer (chat_id, target, by_uid, created, expires, status) VALUES (?, ?, ?, ?, ?, 'open')", (chat_id, target, uid, now(), now() + 30 * 60))
        tn = await P3._uname(conn, chat_id, target)
        return P3._res(f"🎭 {tn} получил предложение «бесплатного адвоката» от полиции на 30 минут. Согласится: выйдет на свободу, но станет вашим источником.")
    return await P3._act(chat_id, uid, run)


async def lawyer_answer(chat_id: int, uid: int, name: str, oid, accept=True) -> dict:
    async def run(conn):
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM police_lawyer WHERE id = ? AND chat_id = ? AND target = ? AND status = 'open'", (int(oid), chat_id, uid))
        r = await cur.fetchone()
        if not r or r["expires"] < now():
            raise GameError("Предложение неактуально")
        if not accept:
            await conn.execute("UPDATE police_lawyer SET status = 'declined' WHERE id = ?", (r["id"],))
            return P3._res("Ты отказался от «адвоката от полиции»: сидишь дальше.", False)
        await conn.execute("UPDATE police_lawyer SET status = 'done' WHERE id = ?", (r["id"],))
        await conn.execute("UPDATE rp_player SET jail_until = 0, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        gid = await PO._own_gang(conn, chat_id, uid)
        text = "🎭 «Адвокат» добился освобождения: ты на свободе. Но в обмен на разговоры с полицией: все, что услышал, ушло в дело."
        if gid:
            await PO.case_add(conn, chat_id, gid, 12)
            if random.random() < 0.45:
                from gangplus2 import _merit
                await _merit(conn, chat_id, uid, -12)
                await rp._news(conn, chat_id, f"🕵 Поговаривают, что адвокат {name} был подставным")
                text += " Банда заметила странности: авторитет −12."
        return P3._res(text)
    return await P3._act(chat_id, uid, run)


# ═════════════════════ ИИ-офицеры ═════════════════════
async def ai_mode(chat_id: int, uid: int, name: str, mode: str) -> dict:
    async def run(conn):
        await PO._need_cop(conn, chat_id, uid, True)
        if mode not in AI_MODES:
            raise GameError("Режимы: off, normal, strict")
        await gangwar.kset(conn, f"pai:{chat_id}", mode)
        return P3._res(f"🤖 ИИ-офицеры: {AI_MODES[mode]}.")
    return await P3._act(chat_id, uid, run)


async def _ai_tick(conn, chat_id: int) -> None:
    mode = await gangwar.kget(conn, f"pai:{chat_id}") or "normal"
    t = now()
    sold0 = json.loads(await gangwar.kget(conn, f"ai_sold:{chat_id}") or "{}")
    if sold0 and sold0.get("until", 0) <= t and mode == "off":
        prev = await gangwar.kget(conn, f"ai_sold_prev:{chat_id}") or "normal"
        await gangwar.kset(conn, f"pai:{chat_id}", prev)
        await gangwar.kset(conn, f"ai_sold:{chat_id}", "")
        await gangwar.kset(conn, f"ai_sold_prev:{chat_id}", "")
        mode = prev
    if mode == "off":
        return
    sold = json.loads(await gangwar.kget(conn, f"ai_sold:{chat_id}") or "{}")
    for uid, nm in AI_NAMES.items():
        if sold.get("uid") == uid and sold.get("until", 0) > t:
            continue
        if await P3._roll(conn, f"p12patrol:{chat_id}:{uid}", 4800 if mode == "normal" else 3000):
            await PO._kset(conn, f"ppatrol:{chat_id}:{uid}", t)
            cur = await conn.execute(
                """SELECT user_id, wanted, money FROM rp_player WHERE chat_id = ? AND wanted >= ? AND jail_until < ? AND user_id > 0
                   AND user_id != ? AND user_id NOT IN (SELECT user_id FROM police_force WHERE chat_id = ?)""", (chat_id, 2 if mode == "normal" else 1, t, PO.CHIEF_ID, chat_id))
            rows = await cur.fetchall()
            if rows and random.random() < 0.4:
                tid, wanted, money = random.choice(rows)
                fine = min(money, 80 * wanted)
                await conn.execute("UPDATE rp_player SET money = money - ?, jail_until = ?, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (fine, t + 6 * 60 + 120 * wanted, chat_id, tid))
                await PO.fund_add(conn, chat_id, fine)
                await PO.event_gang_case(conn, chat_id, tid, 2)
                tn = await P3._uname(conn, chat_id, tid)
                await gangwar.say_ai(conn, chat_id, f"🚓 {nm} задержал {esc(tn)} (розыск {wanted}⭐): штраф {fine} ₽ в фонд полиции.")
    # автономные операции по делам
    cur = await conn.execute("SELECT id, name FROM gang WHERE chat_id = ? AND is_ai = 0", (chat_id,))
    chief_gang = await PO._own_gang(conn, chat_id, PO.CHIEF_ID)
    thr_raid, thr_wire, thr_big = (60, 40, 110) if mode == "normal" else (40, 25, 100)
    for gid, gname in await cur.fetchall():
        if gid == chief_gang:
            continue
        c = await PO._case(conn, chat_id, gid)
        if c["pts"] >= thr_big and await PO._kget(conn, f"pai:big:{chat_id}") < t - 12 * 3600 and await PO._kget(conn, f"pcd:big:{chat_id}") <= t:
            await PO._kset(conn, f"pai:big:{chat_id}", t)
            try:
                await _ai_big(conn, chat_id, gid, gname)
            except Exception:
                log.exception("ИИ: громкое дело")
        elif c["pts"] >= thr_raid and await PO._kget(conn, f"pai:raid:{chat_id}") < t - 8 * 3600 and await PO.fund(conn, chat_id) >= PO.RAID_COST and not await P3._open_visit(conn, chat_id, 0, gid, "omon_gang"):
            await PO._kset(conn, f"pai:raid:{chat_id}", t)
            await PO.fund_add(conn, chat_id, -PO.RAID_COST)
            await conn.execute("INSERT INTO rp_visit (chat_id, user_id, gang_id, kind, amount, created, expires, status) VALUES (?, 0, ?, 'omon_gang', 0, ?, ?, 'open')", (chat_id, gid, t, t + P3.OMON_WINDOW))
            await gangwar.say_ai(conn, chat_id, f"🚨 {random.choice(list(AI_NAMES.values()))} организовал облаву: ОМОН выехал к «{esc(gname)}»!")
        elif c["pts"] >= thr_wire and c["wiretap_until"] <= t and await PO.fund(conn, chat_id) >= PO.WIRETAP_COST and await PO._kget(conn, f"pai:wire:{chat_id}") < t - 6 * 3600:
            await PO._kset(conn, f"pai:wire:{chat_id}", t)
            await PO.fund_add(conn, chat_id, -PO.WIRETAP_COST)
            await conn.execute("UPDATE police_case SET wiretap_until = ? WHERE chat_id = ? AND gang_id = ?", (t + 12 * 3600, chat_id, gid))


async def _ai_big(conn, chat_id: int, gid: int, gname: str) -> None:
    g = await gangwar._gang_row(conn, gid)
    t = now()
    jailed = []
    for u in await gangwar._real_members(conn, gid):
        p = await rp._load(conn, chat_id, u)
        if p["wanted"] >= 1 and p["jail_until"] <= t:
            p["jail_until"], p["esc_used"] = t + 20 * 60, 0
            await rp._save(conn, chat_id, u, p)
            jailed.append(await P3._uname(conn, chat_id, u))
    conf = int((g["treasury"] or 0) * 0.20)
    await conn.execute("UPDATE gang SET treasury = treasury - ?, last_heist = ? WHERE id = ?", (conf, t + 8 * 3600, gid))
    await PO.fund_add(conn, chat_id, conf)
    await conn.execute("UPDATE police_case SET pts = 40, wiretap_until = 0 WHERE chat_id = ? AND gang_id = ?", (chat_id, gid))
    await gangwar.say_ai(conn, chat_id, f"⚖️ <b>ИИ-офицеры закрыли дело против «{esc(gname)}».</b> Арестованы: {', '.join(esc(x) for x in jailed) or 'никого не застали'}. Конфискация {conf} ₽ в фонд.")


# ═════════════════════ интерфейс и фон ═════════════════════
async def extras(conn, chat_id: int, uid: int, g: Optional[dict], my_role: str, out: dict) -> None:
    t = now()
    pol = out.get("pol")
    if pol is None:
        return
    pc = out.setdefault("pc", {})
    cur = await conn.execute("SELECT id, expires FROM police_summon WHERE chat_id = ? AND target = ? AND status = 'open' AND expires > ?", (chat_id, uid, t))
    r = await cur.fetchone()
    pc["summon"] = {"id": r[0], "left": r[1] - t} if r else None
    cur = await conn.execute("SELECT id, expires FROM police_lawyer WHERE chat_id = ? AND target = ? AND status = 'open' AND expires > ?", (chat_id, uid, t))
    r = await cur.fetchone()
    pc["pol_lawyer"] = {"id": r[0], "left": r[1] - t} if r else None
    sold = json.loads(await gangwar.kget(conn, f"ai_sold:{chat_id}") or "{}")
    if sold and sold.get("until", 0) <= t and (await gangwar.kget(conn, f"pai:{chat_id}")) == "off":
        prev = await gangwar.kget(conn, f"ai_sold_prev:{chat_id}") or "normal"
        await gangwar.kset(conn, f"pai:{chat_id}", prev)
        await gangwar.kset(conn, f"ai_sold:{chat_id}", "")
        await gangwar.kset(conn, f"ai_sold_prev:{chat_id}", "")
        sold = {}
    ai_names = [(k, v) for k, v in AI_NAMES.items() if not (sold.get("uid") == k and sold.get("until", 0) > t)]
    pol["ai"] = {"mode": await gangwar.kget(conn, f"pai:{chat_id}") or "normal", "names": [v for _k, v in ai_names], "sold": sold if sold.get("until", 0) > t else None}
    pol["officers"] = [{"id": k, "name": v + " (ИИ)", "ai": True} for k, v in ai_names] + pol["officers"] if pol["ai"]["mode"] != "off" else pol["officers"]
    if pol.get("role"):
        cur = await conn.execute("SELECT a.kind, a.text, a.ts, g.name FROM police_alert a LEFT JOIN gang g ON g.id = a.gang_id WHERE a.chat_id = ? ORDER BY a.id DESC LIMIT 15", (chat_id,))
        pol["alerts"] = [{"gang": r[3] or "?", "text": r[1], "ts": r[2], "kind": r[0]} for r in await cur.fetchall()]
        pol["digest"] = await digest(conn, chat_id)
        pol["waits"] = {**pol.get("waits", {}), "search": max(0, await PO._kget(conn, f"pcd:search:{chat_id}") - t), "inspect": max(0, await PO._kget(conn, f"pcd:inspect:{chat_id}") - t),
                        "summon": max(0, await PO._kget(conn, f"pcd:summon:{chat_id}:{uid}") - t)}
        cur = await conn.execute("SELECT r.user_id, u.username, r.wanted, r.jail_until FROM rp_player r JOIN users u ON u.user_id = r.user_id AND u.chat_id = r.chat_id WHERE r.chat_id = ? AND r.user_id > 0", (chat_id,))
        pol["people"] = []
        for u2, n2, w, ju in await cur.fetchall():
            if u2 == uid or u2 == PO.CHIEF_ID or await PO.is_officer(conn, chat_id, u2):
                continue
            gid2 = await PO._own_gang(conn, chat_id, u2)
            pol["people"].append({"id": u2, "name": n2, "wanted": w, "jailed": ju > t, "gang": bool(gid2)})
        from gangplus8 import POINTS, _rackets
        own_gid = await PO._own_gang(conn, chat_id, uid)
        wait = max(0, await PO._kget(conn, f"pcd:inspect:{chat_id}") - t)
        pts_out = []
        for r in await _rackets(conn, chat_id):
            if not r["owner"]:
                continue
            if wait:
                can, why = False, f"кулдаун {wait // 60 + 1} мин"
            elif r["owner"] == own_gid:
                can, why = False, "точка твоей банды"
            else:
                can, why = True, ""
            pts_out.append({"key": r["key"], "name": POINTS[r["key"]][1], "icon": POINTS[r["key"]][0], "can": can, "why": why})
        pol["points"] = pts_out


async def tick(conn) -> None:
    try:
        t = now()
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM police_summon WHERE status = 'open' AND expires <= ?", (t,))
        for s in [dict(r) for r in await cur.fetchall()]:
            await conn.execute("UPDATE police_summon SET status = 'ignored' WHERE id = ?", (s["id"],))
            await rp._load(conn, s["chat_id"], s["target"])
            await conn.execute("UPDATE rp_player SET wanted = MIN(?, wanted + 1), wanted_ts = ?, jail_until = ?, esc_used = 0 WHERE chat_id = ? AND user_id = ?", (rp.MAX_WANTED, t, t + 5 * 60, s["chat_id"], s["target"]))
            tn = await P3._uname(conn, s["chat_id"], s["target"])
            await gangwar.say(conn, s["chat_id"], f"🚔 {esc(tn)} проигнорировал вызов на допрос: розыск +1⭐ и 5 минут в камере.")
        await conn.execute("UPDATE police_lawyer SET status = 'expired' WHERE status = 'open' AND expires <= ?", (t,))
        cur = await conn.execute("SELECT DISTINCT chat_id FROM rp_player")
        for (chat_id,) in await cur.fetchall():
            await _ai_tick(conn, chat_id)
    except Exception:
        log.exception("Сбой фоновых задач полиции-2")
