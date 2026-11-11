"""ИИ-детектив и суд: ловит коррупцию ОПГ, полиции, мэра и Крёстного.
Суд длится 5 минут, ИИ-судья читает материалы, выносит штраф/тюрьму/снятие должности.
Судью можно подкупить: дешевле штрафа, но есть риск.
"""
import json
import logging
import random
import time

import aiosqlite

import gangwar
from config import DB_PATH
from rooms import GameError

log = logging.getLogger("justice")
COURT_SECONDS = 10 * 60
WITNESS_MAX = 3
DETECTIVE_ID = -9901
JUDGE_ID = -9902


def now() -> int:
    return int(time.time())


async def _get(conn, key: str) -> dict:
    raw = await gangwar.kget(conn, key)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


async def _set(conn, key: str, data: dict) -> None:
    await gangwar.kset(conn, key, json.dumps(data, ensure_ascii=False))


async def report(conn, chat_id: int, uid: int, role: str, charge: str, severity: int = 2) -> None:
    """Детектив получает наводку. После порога — суд."""
    if uid <= 0:
        return
    key = f"corrupt:{chat_id}:{uid}"
    data = await _get(conn, key)
    data["score"] = int(data.get("score", 0)) + int(severity)
    data["role"] = role
    charges = data.get("charges", [])
    if charge not in charges:
        charges.append(charge)
    data["charges"] = charges[-6:]
    data["last"] = now()
    await _set(conn, key, data)
    if data["score"] >= 8:
        await start_trial(conn, chat_id, uid, role, data)


async def start_trial(conn, chat_id: int, uid: int, role: str, data: dict) -> None:
    raw = await gangwar.kget(conn, f"trial:{chat_id}")
    if raw:
        return
    score = int(data.get("score", 8))
    prior = int(await gangwar.kget(conn, f"convict:{chat_id}:{uid}") or 0)
    # Рецидив: каждая прошлая судимость +25% к штрафу, с третьего раза должность снимают всегда.
    fine = min(8000, int((600 + score * 120) * (1 + 0.25 * prior)))
    jail = min(1800, 300 + score * 60)
    lose = score >= 18 or prior >= 2
    bribe = max(300, fine // 2)
    t = {"uid": uid, "role": role, "charges": data.get("charges", ["коррупция"]), "ends": now() + COURT_SECONDS,
         "fine": fine, "jail": jail, "lose": lose, "bribe": bribe, "bribed": False, "prior": prior, "wit": {}}
    await _set(conn, f"trial:{chat_id}", t)
    name = await _name(conn, chat_id, uid)
    await gangwar.say(conn, chat_id, f"🕵️ <b>Детектив Кротов раскрыл дело!</b> {name} подозревается в коррупции ({role}): " + ", ".join(t["charges"]) + f". Суд через {COURT_SECONDS // 60} минут. Материалы у ИИ-судьи.")


async def _name(conn, chat_id: int, uid: int) -> str:
    cur = await conn.execute("SELECT username FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    r = await cur.fetchone()
    return r[0] if r and r[0] else f"игрок {uid}"


async def _snitch_target(conn, chat_id: int, t: dict) -> dict:
    """Кого можно сдать: для Крёстного — своего в ОПГ, для мэра — офицера."""
    role, uid = t.get("role"), int(t.get("uid", 0))
    if role == "godfather":
        raw = await gangwar.kget(conn, f"don:{chat_id}")
        try:
            d = json.loads(raw) if raw else {}
        except Exception:
            d = {}
        gid = int(d.get("gid") or 0)
        if not gid:
            return {}
        cur = await conn.execute(
            """SELECT m.user_id, COALESCE(u.username, 'боец') FROM gang_member m
               LEFT JOIN users u ON u.user_id = m.user_id AND u.chat_id = m.chat_id
               WHERE m.gang_id = ? AND m.user_id != ? ORDER BY (m.user_id > 0), m.role""", (gid, uid))
        r = await cur.fetchone()
        if r:
            return {"uid": int(r[0]), "name": r[1], "kind": "gang", "ai": int(r[0]) < 0}
    if role == "mayor":
        try:
            import police2
            for aid, aname in police2.AI_NAMES.items():
                return {"uid": int(aid), "name": aname, "kind": "police_ai", "ai": True}
        except Exception:
            pass
        cur = await conn.execute("SELECT user_id FROM police_force WHERE chat_id = ? AND user_id != ? AND user_id > 0 LIMIT 1", (chat_id, uid))
        r = await cur.fetchone()
        if r:
            return {"uid": int(r[0]), "name": await _name(conn, chat_id, int(r[0])), "kind": "police", "ai": False}
    return {}


async def snitch(chat_id: int, uid: int, name: str) -> dict:
    async def run(conn):
        import gangplus3 as P3
        t = await _get(conn, f"trial:{chat_id}")
        if not t or t["uid"] != uid or t["ends"] <= now():
            raise GameError("Тебя сейчас не судят")
        if t.get("bribed") or t.get("snitched"):
            raise GameError("Сделку уже заключили")
        target = await _snitch_target(conn, chat_id, t)
        if not target:
            raise GameError("Некого сдавать: у тебя нет своего в структуре")
        t["lose"] = False
        t["jail"] = 0
        t["fine"] = max(200, int(t["fine"]) // 2)
        t["snitched"] = target["name"]
        t["snitch_ai"] = bool(target["ai"])
        await _set(conn, f"trial:{chat_id}", t)
        if target["kind"] == "gang":
            await conn.execute("DELETE FROM gang_member WHERE chat_id = ? AND user_id = ?", (chat_id, target["uid"]))
            if target["ai"]:
                await gangwar.kset(conn, f"recruitlock:{chat_id}:gang", str(now() + 48 * 3600))
        elif target["kind"] == "police_ai":
            prev = await gangwar.kget(conn, f"pai:{chat_id}") or "normal"
            if prev != "off":
                await gangwar.kset(conn, f"ai_sold_prev:{chat_id}", prev)
            await gangwar.kset(conn, f"ai_sold:{chat_id}", json.dumps({"uid": target["uid"], "name": target["name"], "until": now() + 48 * 3600}, ensure_ascii=False))
            await gangwar.kset(conn, f"pai:{chat_id}", "off")
        elif target["kind"] == "police":
            await conn.execute("DELETE FROM police_force WHERE chat_id = ? AND user_id = ?", (chat_id, target["uid"]))
        await gangwar.say(conn, chat_id, f"⚖️ {name} сдал своего: {target['name']}. Суд смягчён, должность сохранена." + (" Нового ИИ нельзя набрать 48 часов." if target["ai"] else ""))
        return P3._res(f"⚖️ Сдал {target['name']}: штраф снижен, тюрьмы нет, должность сохранена." + (" Новый ИИ недоступен 48 ч." if target["ai"] else ""), True, 0)
    import gangplus3 as P3
    return await P3._act(chat_id, uid, run)


async def state(conn, chat_id: int, uid: int) -> dict:
    t = await _get(conn, f"trial:{chat_id}")
    if not t or t.get("ends", 0) <= now():
        return {}
    target = await _snitch_target(conn, chat_id, t) if t.get("role") in ("mayor", "godfather") and t["uid"] == uid and not t.get("bribed") and not t.get("snitched") else {}
    is_me = t["uid"] == uid
    wit = t.get("wit") or {}
    law = False
    if is_me:
        row = await (await conn.execute("SELECT lawyer_until FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))).fetchone()
        law = bool(row and (row[0] or 0) > now())
    sides = list(wit.values())
    return {"trial": {"left": max(0, t["ends"] - now()), "charges": t["charges"], "role": t["role"], "fine": t["fine"], "jail": t["jail"], "lose": t["lose"], "bribe": t["bribe"],
                      "is_me": is_me, "name": await _name(conn, chat_id, int(t["uid"])), "bribed": t.get("bribed", False), "snitched": t.get("snitched", ""),
                      "can_snitch": bool(target), "snitch_name": target.get("name", ""), "snitch_ai": bool(target.get("ai")),
                      "prior": int(t.get("prior", 0)), "plea": bool(t.get("plea")), "defended": bool(t.get("defended")),
                      "can_defend": bool(is_me and law and not t.get("defended")), "has_lawyer": law,
                      "can_plea": bool(is_me and not t.get("plea") and not t.get("bribed")),
                      "can_bribe": bool(is_me and not t.get("bribed") and not t.get("plea")),
                      "wit_for": sides.count("for"), "wit_against": sides.count("against"), "wit_max": WITNESS_MAX,
                      "my_wit": wit.get(str(uid), ""), "can_witness": bool(not is_me and uid > 0 and str(uid) not in wit)}}


async def _my_trial(conn, chat_id: int, uid: int) -> dict:
    t = await _get(conn, f"trial:{chat_id}")
    if not t or t["uid"] != uid or t["ends"] <= now():
        raise GameError("Тебя сейчас не судят")
    return t


async def court_defend(chat_id: int, uid: int, name: str) -> dict:
    """Защита адвоката: штраф −30%, срок −40%, шанс 50% отстоять должность. Контракт с адвокатом на этом сгорает."""
    async def run(conn):
        import gangplus3 as P3
        t = await _my_trial(conn, chat_id, uid)
        if t.get("defended"):
            raise GameError("Адвокат уже выступил")
        row = await (await conn.execute("SELECT lawyer_until FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))).fetchone()
        if not row or (row[0] or 0) <= now():
            raise GameError("У тебя нет адвоката: наймёшь в «Дом → Услуги»")
        t["defended"] = True
        t["fine"] = max(150, int(int(t["fine"]) * 0.7))
        t["jail"] = int(int(t["jail"]) * 0.6)
        kept = False
        if t.get("lose") and random.random() < 0.5:
            t["lose"] = False
            kept = True
        await _set(conn, f"trial:{chat_id}", t)
        await conn.execute("UPDATE rp_player SET lawyer_until = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        await gangwar.say(conn, chat_id, f"⚖️ Адвокат {name} выступил в суде: штраф и срок снижены" + (", должность удалось отстоять." if kept else "."))
        return P3._res(f"⚖️ Адвокат отработал: штраф {t['fine']} ₽, срок {t['jail'] // 60} мин" + (", должность сохранена" if kept else "") + ". Контракт с ним закончен.", True, 0)
    import gangplus3 as P3
    return await P3._act(chat_id, uid, run)


async def court_plea(chat_id: int, uid: int, name: str) -> dict:
    """Признать вину: приговор мягче сейчас, но детектив оставляет дело открытым и конверт судье уже не занести."""
    async def run(conn):
        import gangplus3 as P3
        t = await _my_trial(conn, chat_id, uid)
        if t.get("plea"):
            raise GameError("Ты уже признал вину")
        if t.get("bribed"):
            raise GameError("Конверт уже у судьи: признание ничего не изменит")
        t["plea"] = True
        t["fine"] = max(150, int(int(t["fine"]) * 0.75))
        t["jail"] = int(t["jail"]) // 2
        await _set(conn, f"trial:{chat_id}", t)
        await gangwar.say(conn, chat_id, f"⚖️ {name} признал вину: суд учтёт это в приговоре.")
        return P3._res(f"⚖️ Вина признана: штраф {t['fine']} ₽, срок {t['jail'] // 60} мин. Детектив оставит дело открытым, а конверт судье больше не занести.", True, 0)
    import gangplus3 as P3
    return await P3._act(chat_id, uid, run)


async def court_witness(chat_id: int, uid: int, name: str, side: str = "against") -> dict:
    """Свидетель: показания против (награда из фонда, приговор тяжелее) или алиби (приговор мягче, риск разоблачения)."""
    async def run(conn):
        import gangplus3 as P3
        import police
        t = await _get(conn, f"trial:{chat_id}")
        if not t or t.get("ends", 0) <= now():
            raise GameError("Суда сейчас нет")
        if t["uid"] == uid:
            raise GameError("Подсудимый не свидетель")
        if side not in ("for", "against"):
            raise GameError("Показания: за или против")
        wit = t.get("wit") or {}
        if str(uid) in wit:
            raise GameError("Ты уже дал показания")
        if list(wit.values()).count(side) >= WITNESS_MAX:
            raise GameError("Суд уже выслушал достаточно таких свидетелей")
        acc = await _name(conn, chat_id, int(t["uid"]))
        wit[str(uid)] = side
        t["wit"] = wit
        if side == "against":
            t["fine"] = min(9000, int(int(t["fine"]) * 1.1))
            pay = 150 if await police.fund(conn, chat_id) >= 150 else 0
            if pay:
                await police.fund_add(conn, chat_id, -pay)
                await conn.execute("UPDATE rp_player SET money = money + ? WHERE chat_id = ? AND user_id = ?", (pay, chat_id, uid))
            await _set(conn, f"trial:{chat_id}", t)
            await gangwar.say(conn, chat_id, f"⚖️ {name} дал показания против {acc}: приговор будет тяжелее.")
            return P3._res(f"⚖️ Показания приняты: штраф {acc} +10%." + (f" Награда свидетелю: +{pay} ₽." if pay else ""), True, pay)
        if random.random() < 0.3:
            await conn.execute("UPDATE rp_player SET wanted = MIN(5, wanted + 1), wanted_ts = ? WHERE chat_id = ? AND user_id = ?", (now(), chat_id, uid))
            await _set(conn, f"trial:{chat_id}", t)
            await gangwar.say(conn, chat_id, f"⚖️ {name} пытался дать алиби {acc}, но суд поймал его на лжи.")
            return P3._res("⚖️ Алиби развалилось: судья не поверил, тебе +1⭐ розыска за ложные показания.", False, 0)
        t["fine"] = max(150, int(int(t["fine"]) * 0.9))
        t["jail"] = int(int(t["jail"]) * 0.9)
        await _set(conn, f"trial:{chat_id}", t)
        await gangwar.say(conn, chat_id, f"⚖️ {name} дал алиби {acc}: приговор будет мягче.")
        return P3._res(f"⚖️ Алиби принято: штраф и срок {acc} −10%.", True, 0)
    import gangplus3 as P3
    return await P3._act(chat_id, uid, run)


async def bribe(chat_id: int, uid: int, name: str, amount=None) -> dict:
    async def run(conn):
        import gangplus3 as P3
        t = await _get(conn, f"trial:{chat_id}")
        if not t or t["uid"] != uid or t["ends"] <= now():
            raise GameError("Тебя сейчас не судят")
        if t.get("bribed"):
            raise GameError("Судья уже взял конверт")
        if t.get("plea"):
            raise GameError("Ты признал вину: конверт судья уже не возьмёт")
        cost = int(t["bribe"])
        cur = await conn.execute("SELECT money FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        row = await cur.fetchone()
        if not row or row[0] < cost:
            raise GameError(f"Конверт судье стоит {cost} ₽")
        await conn.execute("UPDATE rp_player SET money = money - ? WHERE chat_id = ? AND user_id = ?", (cost, chat_id, uid))
        t["bribed"] = True
        if random.random() < 0.75:
            t["fine"] = max(100, t["fine"] // 3)
            t["jail"] = 0
            t["lose"] = False
            t["bribe_ok"] = True
            await _set(conn, f"trial:{chat_id}", t)
            await gangwar.say(conn, chat_id, f"⚖️ Судья принял конверт {cost} ₽: штраф снижен, тюрьма отменена, должность сохранена.")
            return P3._res(f"⚖️ Судья в доле: штраф снижен до {t['fine']} ₽, тюрьмы нет, должность сохранена.", True, -cost)
        t["jail"] = min(1800, int(t["jail"]) + 600)
        t["fine"] = int(t["fine"]) + 500
        t["lose"] = True
        await _set(conn, f"trial:{chat_id}", t)
        await gangwar.say(conn, chat_id, f"⚖️ Судья отказался от конверта: +10 минут и +500 ₽ к приговору.")
        return P3._res("⚖️ Судья отказался: приговор жёстче.", False, -cost)
    import gangplus3 as P3
    return await P3._act(chat_id, uid, run)


async def _verdict(conn, chat_id: int, t: dict) -> None:
    import police
    uid = int(t["uid"])
    fine, jail, lose = int(t["fine"]), int(t["jail"]), bool(t["lose"])
    cur = await conn.execute("SELECT money FROM rp_player WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
    row = await cur.fetchone()
    if row:
        pay = min(int(row[0]), fine)
        await conn.execute("UPDATE rp_player SET money = money - ?, jail_until = MAX(jail_until, ?), esc_used = 1 WHERE chat_id = ? AND user_id = ?",
                           (pay, now() + jail, chat_id, uid))
        await gangwar.kset(conn, f"courtlock:{chat_id}:{uid}", str(now() + jail))
        if pay > 0:
            await police.fund_add(conn, chat_id, pay)  # штраф уходит в фонд полиции
    name = await _name(conn, chat_id, uid)
    role = t.get("role", "")
    if lose:
        if role == "police" and uid != police.CAPTAIN_ID:
            await conn.execute("DELETE FROM police_force WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
        elif role == "mayor":
            await gangwar.kset(conn, f"mayor:{chat_id}", "")
        elif role == "godfather":
            await gangwar.kset(conn, f"don:{chat_id}", "")
        elif role == "gang":
            await conn.execute("UPDATE gang_member SET role = 'grunt' WHERE chat_id = ? AND user_id = ? AND role IN ('boss','deputy')", (chat_id, uid))
    # Последствия выбора: купленный приговор и признание оставляют хвост у детектива, а судимость копится.
    residual = 4 if t.get("bribe_ok") else 3 if t.get("plea") else 0
    if residual:
        await _set(conn, f"corrupt:{chat_id}:{uid}", {"score": residual, "role": role, "charges": ["прошлое дело"], "last": now()})
    else:
        await conn.execute("DELETE FROM kv WHERE key = ?", (f"corrupt:{chat_id}:{uid}",))
    if not t.get("bribe_ok"):
        await gangwar.kset(conn, f"convict:{chat_id}:{uid}", str(int(t.get("prior", 0)) + 1))
    await gangwar.kset(conn, f"trial:{chat_id}", "")
    await gangwar.say(conn, chat_id, f"⚖️ <b>Приговор ИИ-судьи:</b> {name} — штраф {fine} ₽, тюрьма {jail // 60} мин" + (", должность потеряна." if lose else ", должность сохранена.") + " Материалы: " + ", ".join(t.get("charges", [])) + ".")


async def tick(conn) -> None:
    try:
        for chat_id, value in await (await conn.execute("SELECT key, value FROM kv WHERE key LIKE 'trial:%'")).fetchall():
            try:
                t = json.loads(value)
            except Exception:
                continue
            if t.get("ends", 0) <= now():
                await _verdict(conn, int(chat_id.split(":")[1]), t)
    except Exception:
        log.exception("Сбой суда")
