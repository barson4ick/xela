"""ОПГ: банды игроков в «Районе». Вместе легче грабить, а налёт на всю банду приносит общую добычу."""
import random
import time

import aiosqlite

import rp
from config import DB_PATH
from rooms import GameError

CREATE_COST = 1500
MAX_MEMBERS = 6
MAX_AI_HELPERS = 3
HEIST_ENERGY = 35
JOIN_CRED = 10
HEIST_COOLDOWN = 30 * 60
HEIST_LOOT = (2300, 4600)  # +15%: компенсация доли босса и взноса в общак
NPC_BOSS = -9001
NPC_NAMES = {-9001: "Баба Валя", -9002: "Тётя Зина", -9003: "Дед Мазай", -9004: "Кузьмич", -9005: "Лидка с рынка", -9006: "Племянник Гена"}
NPC_ROLES = [(-9001, "boss"), (-9002, "deputy"), (-9003, "deputy"), (-9004, "grunt"), (-9005, "grunt"), (-9006, "grunt")]
MAX_DEPUTIES = 2
INVITE_TTL = 24 * 3600
ROLE_NAMES = {"boss": "Главный босс", "deputy": "Зам", "grunt": "Холоп"}
ROLE_ICONS = {"boss": "👑", "deputy": "🥈", "grunt": "🪓"}


def crime_bonus(members: int) -> float:
    return min(0.12, 0.03 * max(0, members - 1))


def heist_chance(members: int, wanted: int) -> float:
    return min(0.6, max(0.15, 0.30 + 0.06 * (members - 1) - 0.03 * wanted))


async def _mine(conn, chat_id: int, uid: int):
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        "SELECT g.* FROM gang g JOIN gang_member m ON m.gang_id = g.id WHERE m.chat_id = ? AND m.user_id = ?", (chat_id, uid))
    g = await cur.fetchone()
    if not g:
        return None, []
    g = dict(g)
    cur = await conn.execute(
        """SELECT m.user_id, m.role, m.merit, m.joined, m.dues_due, m.probation_until, u.username FROM gang_member m LEFT JOIN users u ON u.user_id = m.user_id AND u.chat_id = m.chat_id
           WHERE m.gang_id = ?
           ORDER BY CASE m.role WHEN 'boss' THEN 0 WHEN 'deputy' THEN 1 ELSE 2 END, m.joined""", (g["id"],))
    members = [dict(r) for r in await cur.fetchall()]
    for m in members:
        if not m.get("username"):
            m["username"] = NPC_NAMES.get(m["user_id"])
    return g, members


async def _mine_by_gang(conn, gid: int):
    """Банда и её участники по id банды (для фоновых задач)."""
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM gang WHERE id = ?", (gid,))
    g = await cur.fetchone()
    if not g:
        return None, []
    cur = await conn.execute(
        """SELECT m.user_id, m.role, m.merit, m.joined, m.dues_due, m.probation_until, u.username FROM gang_member m LEFT JOIN users u ON u.user_id = m.user_id AND u.chat_id = m.chat_id
           WHERE m.gang_id = ? ORDER BY CASE m.role WHEN 'boss' THEN 0 WHEN 'deputy' THEN 1 ELSE 2 END, m.joined""", (gid,))
    members = [dict(r) for r in await cur.fetchall()]
    for m in members:
        if not m.get("username"):
            m["username"] = NPC_NAMES.get(m["user_id"])
    return dict(g), members


def _can(my_role: str, target_role: str, what: str) -> bool:
    """Права: босс — всё; зам — приглашать и выгонять только холопов."""
    if what == "invite":
        return my_role in ("boss", "deputy")
    if what == "kick":
        return my_role == "boss" or (my_role == "deputy" and target_role == "grunt")
    if what == "rank":
        return my_role == "boss"
    return False


async def _purge_invites(conn, chat_id: int):
    await conn.execute("DELETE FROM gang_invite WHERE chat_id = ? AND ts < ?", (chat_id, int(time.time()) - INVITE_TTL))


async def _ai_kind(conn, gid: int, kind: str) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM ai_citizen a JOIN gang_member m ON m.user_id = a.user_id AND m.chat_id = a.chat_id WHERE m.gang_id = ? AND a.kind = ?", (gid, kind))
    return int((await cur.fetchone())[0] or 0)


async def view(chat_id: int, uid: int, conn) -> dict:
    await _purge_invites(conn, chat_id)
    g, members = await _mine(conn, chat_id, uid)
    now = int(time.time())
    if g:
        players = [m for m in members if m["user_id"] > 0]
        n = len(players)
        my_role = next((m["role"] for m in members if m["user_id"] == uid), "grunt")
        deputies = sum(1 for m in players if m["role"] == "deputy")
        out = {"mine": {
            "id": g["id"], "name": g["name"], "boss": g["boss"], "is_boss": my_role == "boss", "my_role": my_role,
            "members": [{"id": m["user_id"], "name": m["username"] or "игрок", "role": m["role"], "merit": m.get("merit") or 0,
                         "can_kick": m["user_id"] != uid and _can(my_role, m["role"], "kick"),
                         "can_rank": m["user_id"] != uid and _can(my_role, m["role"], "rank")} for m in members],
            "bonus": int(crime_bonus(n) * 100), "heist_left": max(0, g["last_heist"] + HEIST_COOLDOWN - now),
            "heist_chance": int(heist_chance(n, 0) * 100), "heist_energy": HEIST_ENERGY,
            "deputies": deputies, "max_deputies": MAX_DEPUTIES, "can_invite": _can(my_role, "", "invite") and n < MAX_MEMBERS,
            "player_count": n, "ai_count": max(0, len(members) - n), "max_ai": MAX_AI_HELPERS, "ai_helpers": await _ai_kind(conn, g["id"], "helper"), "ai_citizens": await _ai_kind(conn, g["id"], "citizen"),
            "candidates": [], "invited": [],
        }, "open": [], "invites": [], "cost": CREATE_COST, "max": MAX_MEMBERS, "max_ai": MAX_AI_HELPERS}
        if _can(my_role, "", "invite"):
            cur = await conn.execute(
                """SELECT u.user_id, u.username, COALESCE(r.street_cred, 0) AS cred FROM users u
                   LEFT JOIN rp_player r ON r.chat_id = u.chat_id AND r.user_id = u.user_id
                   WHERE u.chat_id = ? AND u.user_id > 0 AND u.user_id NOT IN (SELECT user_id FROM gang_member WHERE chat_id = ?)
                     AND u.user_id NOT IN (SELECT user_id FROM gang_invite WHERE gang_id = ?)
                   ORDER BY cred DESC, lower(u.username) LIMIT 80""", (chat_id, chat_id, g["id"]))
            out["mine"]["candidates"] = [{"id": r[0], "name": r[1] or "игрок", "cred": r[2] or 0} for r in await cur.fetchall()]
            out["mine"]["join_cred"] = JOIN_CRED
            cur = await conn.execute(
                """SELECT i.user_id, u.username FROM gang_invite i LEFT JOIN users u ON u.user_id = i.user_id AND u.chat_id = i.chat_id
                   WHERE i.gang_id = ?""", (g["id"],))
            out["mine"]["invited"] = [{"id": r[0], "name": r[1] or "игрок"} for r in await cur.fetchall()]
        await _extras(conn, chat_id, uid, g, my_role, out)
        return out
    cur = await conn.execute(
        """SELECT g.id, g.name, COUNT(m.user_id) AS n, u.username AS inviter FROM gang_invite i
           JOIN gang g ON g.id = i.gang_id JOIN gang_member m ON m.gang_id = g.id
           LEFT JOIN users u ON u.user_id = i.invited_by AND u.chat_id = i.chat_id
           WHERE i.chat_id = ? AND i.user_id = ? GROUP BY g.id""", (chat_id, uid))
    invites = [{"id": r[0], "name": r[1], "n": r[2], "from": r[3] or "игрок"} for r in await cur.fetchall()]
    out = {"mine": None, "open": [], "invites": invites, "cost": CREATE_COST, "max": MAX_MEMBERS}
    await _extras(conn, chat_id, uid, None, "", out)
    return out


async def _extras(conn, chat_id, uid, g, my_role, out) -> None:
    try:
        import gangwar
        out.update(await gangwar.extras(conn, chat_id, uid, g, my_role))
        import gangplus
        await gangplus.extras(conn, chat_id, uid, g, my_role, out)
        import gangplus3
        await gangplus3.extras(conn, chat_id, uid, g, my_role, out)
        import gangplus4
        await gangplus4.extras(conn, chat_id, uid, g, my_role, out)
        import gangplus5
        await gangplus5.extras(conn, chat_id, uid, g, my_role, out)
        import liveevents
        await liveevents.extras(conn, chat_id, uid, out)
        import gangplus8, gangplus9, gangplus10
        await gangplus8.extras(conn, chat_id, uid, g, my_role, out)
        await gangplus9.extras(conn, chat_id, uid, g, my_role, out)
        await gangplus10.extras(conn, chat_id, uid, g, my_role, out)
        import police
        await police.extras(conn, chat_id, uid, g, my_role, out)
        import police2, gangplus11
        await police2.extras(conn, chat_id, uid, g, my_role, out)
        await gangplus11.extras(conn, chat_id, uid, g, my_role, out)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Не удалось собрать данные войн ОПГ")


async def create(chat_id: int, uid: int, name: str, gname: str) -> dict:
    gname = " ".join(str(gname).split())
    if not 3 <= len(gname) <= 20:
        raise GameError("Название ОПГ — от 3 до 20 символов")
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        p = await rp._load(conn, chat_id, uid)
        g, _ = await _mine(conn, chat_id, uid)
        if g:
            raise GameError("Ты уже состоишь в ОПГ")
        if p["money"] < CREATE_COST:
            raise GameError(f"Основать ОПГ стоит {CREATE_COST} ₽, у тебя {p['money']}")
        cur = await conn.execute("SELECT 1 FROM gang WHERE chat_id = ? AND lower(name) = lower(?)", (chat_id, gname))
        if await cur.fetchone():
            raise GameError("Банда с таким названием уже есть")
        p["money"] -= CREATE_COST
        now = int(time.time())
        cur = await conn.execute("INSERT INTO gang (chat_id, name, boss, created) VALUES (?, ?, ?, ?)", (chat_id, gname, uid, now))
        await conn.execute("INSERT INTO gang_member (chat_id, user_id, gang_id, joined, role) VALUES (?, ?, ?, ?, 'boss')", (chat_id, uid, cur.lastrowid, now))
        await rp._news(conn, chat_id, f"🕴 {name} основал ОПГ «{gname}»")
        await rp._save(conn, chat_id, uid, p)
        await conn.commit()
    return {"text": f"🕴 ОПГ «{gname}» основана (−{CREATE_COST} ₽). Зови своих в банду!", "delta": -CREATE_COST, "good": True, "state": await rp.state(chat_id, uid)}


async def _display(conn, chat_id: int, uid: int) -> str:
    cur = await conn.execute("SELECT username FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return (r[0] if r and r[0] else "игрок")


async def invite(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)
    if target < 0:
        raise GameError("ИИ-граждане вступают сами: приглашать их не нужно")
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await _mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        my_role = next(m["role"] for m in members if m["user_id"] == uid)
        if not _can(my_role, "", "invite"):
            raise GameError("Приглашать может только босс или зам")
        if sum(1 for m in members if m["user_id"] > 0) >= MAX_MEMBERS:
            raise GameError("В банде нет мест для игроков")
        cur = await conn.execute("SELECT 1 FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        if not await cur.fetchone():
            raise GameError("Такого игрока нет в чате")
        other, _ = await _mine(conn, chat_id, target)
        if other:
            raise GameError("Этот игрок уже в какой-то ОПГ")
        tname = await _display(conn, chat_id, target)
        tp = await rp._load(conn, chat_id, target)
        if int(tp.get("street_cred") or 0) < JOIN_CRED:
            raise GameError(f"У {tname} только {int(tp.get('street_cred') or 0)} уличного авторитета. Для вступления нужно {JOIN_CRED}: пусть сначала сделает чёрные дела.")
        await conn.execute("INSERT OR REPLACE INTO gang_invite (gang_id, user_id, chat_id, invited_by, ts) VALUES (?, ?, ?, ?, ?)",
                           (g["id"], target, chat_id, uid, int(time.time())))
        await rp._news(conn, chat_id, f"🕴 {name} зовёт {tname} в ОПГ «{g['name']}»")
        await conn.commit()
    return {"text": f"📨 Приглашение для {tname} отправлено. Оно действует 24 часа.", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def accept(chat_id: int, uid: int, name: str, gid) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        p = await rp._load(conn, chat_id, uid)
        await _purge_invites(conn, chat_id)
        mine, _ = await _mine(conn, chat_id, uid)
        if mine:
            raise GameError("Ты уже состоишь в ОПГ")
        cur = await conn.execute(
            """SELECT g.id, g.name FROM gang_invite i JOIN gang g ON g.id = i.gang_id
               WHERE i.gang_id = ? AND i.user_id = ? AND i.chat_id = ?""", (int(gid), uid, chat_id))
        g = await cur.fetchone()
        if not g:
            raise GameError("Приглашения нет или оно истекло")
        if int(p.get("street_cred") or 0) < JOIN_CRED:
            raise GameError(f"Для вступления нужно {JOIN_CRED} уличного авторитета, у тебя {int(p.get('street_cred') or 0)}. Сначала сделай чёрные дела.")
        cur = await conn.execute("SELECT COUNT(*) FROM gang_member WHERE gang_id = ?", (g[0],))
        if (await cur.fetchone())[0] >= MAX_MEMBERS:
            raise GameError("В банде не осталось мест")
        await conn.execute("INSERT INTO gang_member (chat_id, user_id, gang_id, joined, role) VALUES (?, ?, ?, ?, 'grunt')",
                           (chat_id, uid, g[0], int(time.time())))
        await conn.execute("DELETE FROM gang_invite WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        await rp._news(conn, chat_id, f"🕴 {name} вступил в ОПГ «{g[1]}» холопом")
        await conn.commit()
    return {"text": f"🪓 Ты в банде «{g[1]}». Начинаешь холопом, выслуживайся.", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def decline(chat_id: int, uid: int, name: str, gid) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM gang_invite WHERE gang_id = ? AND user_id = ? AND chat_id = ?", (int(gid), uid, chat_id))
        await conn.commit()
    return {"text": "Приглашение отклонено", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def cancel_invite(chat_id: int, uid: int, name: str, target) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await _mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        my_role = next(m["role"] for m in members if m["user_id"] == uid)
        if not _can(my_role, "", "invite"):
            raise GameError("Только босс или зам")
        await conn.execute("DELETE FROM gang_invite WHERE gang_id = ? AND user_id = ?", (g["id"], int(target)))
        await conn.commit()
    return {"text": "Приглашение отозвано", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def kick(chat_id: int, uid: int, name: str, target) -> dict:
    target = int(target)
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await _mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        if target == uid:
            raise GameError("Себя не выгоняют, используй «Уйти из банды»")
        my_role = next(m["role"] for m in members if m["user_id"] == uid)
        t = next((m for m in members if m["user_id"] == target), None)
        if not t:
            raise GameError("Этого игрока нет в твоей банде")
        if not _can(my_role, t["role"], "kick"):
            raise GameError("Зам может выгонять только холопов, босса и замов трогает лишь босс" if my_role == "deputy" else "У холопа нет такого права")
        await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        tname = t["username"] or "игрок"
        await rp._news(conn, chat_id, f"🕴 {name} выгнал {tname} из ОПГ «{g['name']}»")
        await conn.commit()
    return {"text": f"👢 {tname} выгнан из банды", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def setrole(chat_id: int, uid: int, name: str, target, role: str) -> dict:
    target = int(target)
    if role not in ROLE_NAMES:
        raise GameError("Неизвестный ранг")
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await _mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        my_role = next(m["role"] for m in members if m["user_id"] == uid)
        if my_role != "boss":
            raise GameError("Менять ранги может только главный босс")
        if target == uid:
            raise GameError("Свой ранг менять нельзя, передай босса другому игроку")
        t = next((m for m in members if m["user_id"] == target), None)
        if not t:
            raise GameError("Этого игрока нет в твоей банде")
        if t["role"] == role:
            raise GameError(f"Он уже: {ROLE_NAMES[role]}")
        tname = t["username"] or "игрок"
        deputies = sum(1 for m in members if m["role"] == "deputy")
        if role == "boss":
            # передача власти: прежний босс становится замом
            if t["role"] != "deputy" and deputies >= MAX_DEPUTIES:
                raise GameError(f"Сначала освободи место зама (максимум {MAX_DEPUTIES}), прежний босс станет замом")
            await conn.execute("UPDATE gang_member SET role = 'deputy' WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
            await conn.execute("UPDATE gang_member SET role = 'boss' WHERE chat_id = ? AND user_id = ?", (chat_id, target))
            await conn.execute("UPDATE gang SET boss = ? WHERE id = ?", (target, g["id"]))
            await rp._news(conn, chat_id, f"👑 {name} передал ОПГ «{g['name']}» игроку {tname}")
            text = f"👑 {tname} теперь главный босс, ты — зам"
        else:
            if role == "deputy" and deputies >= MAX_DEPUTIES:
                raise GameError(f"Замов может быть только {MAX_DEPUTIES}")
            await conn.execute("UPDATE gang_member SET role = ? WHERE chat_id = ? AND user_id = ?", (role, chat_id, target))
            up = role == "deputy"
            await rp._news(conn, chat_id, f"{'🥈' if up else '🪓'} {tname} в ОПГ «{g['name']}»: теперь {ROLE_NAMES[role].lower()}")
            text = f"{ROLE_ICONS[role]} {tname}: {ROLE_NAMES[role]}"
        await conn.commit()
    return {"text": text, "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def leave(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        g, members = await _mine(conn, chat_id, uid)
        if not g:
            raise GameError("Ты не состоишь в ОПГ")
        await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        rest = [m for m in members if m["user_id"] != uid]
        if not rest:
            await conn.execute("DELETE FROM gang WHERE id = ?", (g["id"],))
        elif g["boss"] == uid:
            heir = next((m for m in rest if m["role"] == "deputy"), rest[0])  # сначала зам, потом старейший
            await conn.execute("UPDATE gang SET boss = ? WHERE id = ?", (heir["user_id"], g["id"]))
            await conn.execute("UPDATE gang_member SET role = 'boss' WHERE chat_id = ? AND user_id = ?", (chat_id, heir["user_id"]))
        await rp._news(conn, chat_id, f"🕴 {name} покинул ОПГ «{g['name']}»")
        await conn.commit()
    return {"text": "Ты ушёл из банды", "delta": 0, "good": True, "state": await rp.state(chat_id, uid)}


async def heist(chat_id: int, uid: int, name: str) -> dict:
    async with rp._lock, aiosqlite.connect(DB_PATH) as conn:
        p = await rp._load(conn, chat_id, uid)
        g, members = await _mine(conn, chat_id, uid)
        if not g:
            raise GameError("Налёт устраивает только ОПГ")
        if len(members) < 2:
            raise GameError("Для налёта нужно хотя бы двое в банде")
        now = int(time.time())
        wait = g["last_heist"] + HEIST_COOLDOWN - now
        if wait > 0:
            raise GameError(f"Банда отдыхает после дела ещё {wait // 60 + 1} мин.")
        rp._need(p, HEIST_ENERGY)
        p["energy"] -= HEIST_ENERGY
        await conn.execute("UPDATE gang SET last_heist = ? WHERE id = ?", (now, g["id"]))
        n = len(members)
        import gangplus4
        if random.random() < heist_chance(n, p["wanted"]) + await gangplus4.drive_bonus(conn, g["id"]):
            loot = int(random.randint(*HEIST_LOOT) * (1 + 0.1 * (n - 1)) * await __import__("charter").heist_mult(conn, g["id"]))
            import gangplus
            share, cut_note = await gangplus.split_loot(conn, chat_id, g, members, loot, uid, p)
            p["wanted"] = min(rp.MAX_WANTED, p["wanted"] + 3)
            p["wanted_ts"] = now
            await rp._news(conn, chat_id, f"🕴 ОПГ «{g['name']}» провернула налёт: {loot} ₽ на {n} человек")
            res = {"text": f"🕴 Налёт удался! Добыча {loot} ₽, твоя доля {share} ₽{cut_note}. Розыск вырос.", "delta": share, "good": True}
        else:
            p["wanted"] = min(rp.MAX_WANTED, p["wanted"] + 2)
            p["wanted_ts"] = now
            fine = min(p["money"], 150)
            p["money"] -= fine
            p["jail_until"] = now + 20 * 60
            p["esc_used"] = 0
            await rp._news(conn, chat_id, f"🚔 {name} из ОПГ «{g['name']}» попался на налёте")
            res = {"text": f"🚔 Налёт сорвался: штраф {fine} ₽ и 20 минут в тюрьме. Банда ждёт тебя.", "delta": -fine, "good": False}
        await rp._save(conn, chat_id, uid, p)
        await conn.commit()
    if res.get("good"):
        import gangwar
        await gangwar.war_points(chat_id, uid, "heist")
    res["state"] = await rp.state(chat_id, uid)
    return res
