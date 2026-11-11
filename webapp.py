"""Мини-апп Telegram: HTTP API + статика. Запускается отдельным сервисом за nginx."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qsl

import aiosqlite
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiohttp import web

import database as db
import bar
import card
import chat_events
import cosmetics
import events
import gang
import gangplus
import gangplus2
import gangplus3
import gangplus4
import gangplus5
import liveevents
import flat
import gangplus8
import gangplus9
import gangplus10
import police
import police2
import gangplus11
import world
import balance
import assign
import conflict
import patrons
import control
import power
import justice
import gangwar
import highlights
import themes
import live
import poker
import rp
import webdb
from config import BOT_TOKEN
from keyboards import app_link_button
from mafia import MafiaRoom, REWARD as MAFIA_REWARD
from rooms import BOT_ID, GAMES, GameError, Room
from utils import day_str, esc

log = logging.getLogger("webapp")
STATIC = Path(__file__).resolve().parent / "webapp" / "static"
HOST, PORT = "127.0.0.1", int(os.environ.get("WEBAPP_PORT", "8092"))
AUTH_MAX_AGE = 86400

TITLE_DAYS = 7
SHOP = [
    ("Барон Сантиметров 👑", 15),
    ("Тень чата 🥷", 10),
    ("Королева драмы 🎭", 10),
    ("Безработный миллионер 💸", 8),
    ("Легенда рулетки 🔫", 12),
    ("Душа компании 🪩", 6),
]
MAX_BET = 10
ARENA_BET_MAX = 5
GIVE_COOLDOWN = db.GIVE_COOLDOWN

# Колесо удачи: 12 секторов, множитель ставки. Матожидание ≈ 0.92 — казино немного в плюсе.
WHEEL = [0, 2, 0, 1, 0, 5, 0, 2, 0, 1, 0, 0]
SLOT_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "⭐", "7️⃣"]

rooms: Dict[str, Room] = {}
user_room: Dict[int, str] = {}  # игрок → его активная комната
_last_gamble: Dict[int, float] = {}
mafias: Dict[str, MafiaRoom] = {}
user_mafia: Dict[int, str] = {}
tables: Dict[str, poker.PokerTable] = {}
user_table: Dict[int, str] = {}
MAX_TABLES_PER_CHAT = 3
bot: Optional[Bot] = None


# ───────────────────────────── авторизация ─────────────────────────────

def validate_init_data(init_data: str) -> Optional[dict]:
    """Проверка подписи Telegram WebApp initData (HMAC-SHA256 от токена бота)."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got_hash = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, got_hash):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > AUTH_MAX_AGE:
            return None
        user = json.loads(pairs["user"])
        return user if isinstance(user, dict) and "id" in user else None
    except Exception:
        return None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/api/"):
        user = validate_init_data(request.headers.get("X-Init-Data", ""))
        if not user:
            return web.json_response({"error": "Откройте мини-апп из Telegram"}, status=401)
        request["user"] = user
    return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except GameError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("Ошибка API %s", request.path)
        return web.json_response({"error": "Внутренняя ошибка"}, status=500)


async def member(request: web.Request) -> dict:
    """Игрок обязан состоять в чате (иметь запись в базе бота)."""
    chat_id = int(request.match_info["chat_id"])
    me = await webdb.player(chat_id, request["user"]["id"])
    if not me:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Тебя нет в этом чате. Напиши что-нибудь в нём боту."}),
            content_type="application/json",
        )
    return me


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def ok(**data) -> web.Response:
    return web.json_response(data)


def throttle(user_id: int, gap: float = 1.2) -> None:
    now = time.monotonic()
    if now - _last_gamble.get(user_id, 0) < gap:
        raise GameError("Не так быстро!")
    _last_gamble[user_id] = now


async def notify(chat_id: int, text: str) -> None:
    if bot:
        try:
            await bot.send_message(chat_id, text)
        except Exception as exc:
            log.warning("Не удалось написать в чат %s: %s", chat_id, exc)


# ───────────────────────────── достижения ─────────────────────────────

def achievements(me: dict, st: dict) -> list:
    spec = [
        ("📏", "Первые сантиметры", "Размер от 10 см", me["size"], 10),
        ("🏔", "Титан", "Размер от 50 см", me["size"], 50),
        ("🔥", "Огонь", "Серия бонуса 7 дней", me["streak"], 7),
        ("🗣", "Болтун", "500 сообщений в чате", st["messages"], 500),
        ("⭐", "Любимчик", "Репутация от 5", me["rep"], 5),
        ("🌟", "Звезда дня", "Герой дня 3 раза", st["heroes"], 3),
        ("⚔️", "Дуэлянт", "5 побед в дуэлях", st["duel_wins"], 5),
        ("🥷", "Вор в законе", "5 удачных ограблений", st["robs"], 5),
        ("🎰", "Азартный", "20 игр в казино", st["gambles"], 20),
        ("🧠", "Умник", "3 победы в викторине", st["trivia"], 3),
        ("🏟", "Гладиатор", "5 побед на арене", st["arena_wins"], 5),
    ]
    return [
        {"icon": i, "title": t, "desc": d, "value": min(v, goal), "goal": goal, "done": v >= goal}
        for i, t, d, v, goal in spec
    ]


# ───────────────────────────── обработчики ─────────────────────────────

async def h_me(request: web.Request) -> web.Response:
    user = request["user"]
    return ok(
        id=user["id"],
        name=user.get("first_name", ""),
        chats=await webdb.user_chats(user["id"]),
        games=GAMES,
        emojis=webdb.EMOJIS,
        wheel=WHEEL,
        slots=SLOT_SYMBOLS,
        max_bet=MAX_BET,
        title_days=TITLE_DAYS,
        theme=themes.current(),
    )


async def _digest(chat_id: int):
    text = await db.kv_get(f"digest_text:{chat_id}")
    if not text:
        return None
    return {"text": text, "ts": int(await db.kv_get(f"digest_ts:{chat_id}") or 0)}


async def h_board(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    return ok(
        me=me,
        players=await webdb.players(chat_id),
        activity=await webdb.activity(chat_id, day_str(-13)),
        heroes=await webdb.recent_heroes(chat_id),
        quotes=await webdb.recent_quotes(chat_id),
        feed=await events.feed(chat_id),
        today=await events.today_summary(chat_id, me["user_id"]),
        digest=await _digest(chat_id),
        life=await chat_events.view(chat_id, me["user_id"]),
        give_in=max(0, me["last_drip"] + GIVE_COOLDOWN - int(time.time())),
    )


async def _look(chat_id: int, uid: int) -> dict:
    eq = await cosmetics.equipped(chat_id, uid)
    return {"frame": cosmetics.colors("frame", eq["frame"]) if eq["frame"] else None,
            "bg": cosmetics.colors("bg", eq["bg"]) if eq["bg"] else None}


async def h_cosmetics(request: web.Request) -> web.Response:
    me = await member(request)
    return ok(cosmetics=await cosmetics.state(int(request.match_info["chat_id"]), me["user_id"]), size=me["size"])


def _cosmetic_action(fn):
    async def handler(request: web.Request) -> web.Response:
        me = await member(request)
        throttle(me["user_id"], 0.7)
        data = await body(request)
        res = await fn(int(request.match_info["chat_id"]), me["user_id"], str(data.get("kind", "")), str(data.get("id", "")))
        return ok(**res)
    return handler


async def h_profile(request: web.Request) -> web.Response:
    await member(request)
    chat_id = int(request.match_info["chat_id"])
    uid = int(request.match_info["uid"])
    target = await webdb.player(chat_id, uid)
    if not target:
        raise web.HTTPNotFound(text=json.dumps({"error": "Игрок не найден"}), content_type="application/json")
    st = await webdb.stats(chat_id, uid)
    return ok(
        player=target,
        stats=st,
        history=await webdb.size_history(chat_id, uid),
        achievements=achievements(target, st),
        look=await _look(chat_id, uid),
    )


async def h_daily(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    got, streak, bonus, size = await db.claim_daily(
        me["user_id"], chat_id, me["username"], day_str(), day_str(-1)
    )
    return ok(claimed=got, streak=streak, bonus=bonus, size=size)


async def h_give(request: web.Request) -> web.Response:
    """Передать часть своих см другому игроку чата: любая сумма, но не больше, чем есть; раз в 2 часа."""
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    data = await body(request)
    try:
        to, amount = int(data.get("to")), int(data.get("amount"))
    except (TypeError, ValueError):
        raise GameError("Укажи, кому и сколько")
    if to == me["user_id"]:
        raise GameError("Себе передавать незачем")
    if amount < 1:
        raise GameError("Сумма должна быть от 1 см")
    target = await webdb.player(chat_id, to)
    if not target:
        raise GameError("Такого игрока нет в этом чате")
    status, size, wait = await db.give_size(chat_id, me["user_id"], to, amount)
    if status == "cooldown":
        raise GameError(f"Передавать можно раз в 2 часа, подожди ещё {(wait + 59) // 60} мин.")
    if status == "funds":
        raise GameError(f"У тебя только {size} см")
    if status != "ok":
        raise GameError("Не получилось передать")
    await notify(chat_id, f"🎁 <b>{esc(me['username'])}</b> подарил <b>{esc(target['username'])}</b> {amount} см!")
    return ok(size=size, give_in=GIVE_COOLDOWN)


async def _read_bet(request: web.Request) -> int:
    try:
        bet = int((await body(request)).get("bet"))
    except (TypeError, ValueError):
        raise GameError("Некорректная ставка")
    if not 1 <= bet <= MAX_BET:
        raise GameError(f"Ставка от 1 до {MAX_BET} см")
    return bet


async def _check_room(me: dict, chat_id: int) -> None:
    if await webdb.gamble_room(me["user_id"], chat_id) <= 0:
        raise GameError(f"Дневной лимит выигрыша ({webdb.GAMBLE_DAILY_CAP} см) исчерпан — казино закрыто для тебя, приходи позже")


async def h_wheel(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    throttle(me["user_id"])
    bet = await _read_bet(request)
    await _check_room(me, chat_id)
    index = random.randrange(len(WHEEL))
    mult = WHEEL[index]
    size = await webdb.apply_bet(me["user_id"], chat_id, bet, bet * mult, "wheel")
    if size is None:
        raise GameError(f"Не хватает сантиметров (у тебя {me['size']})")
    if mult >= 5:
        await notify(chat_id, f"🎡 <b>{esc(me['username'])}</b> срывает куш на колесе удачи: <b>x{mult}</b> (+{bet * (mult - 1)} см)!")
    return ok(index=index, mult=mult, delta=size - me["size"], size=size)


async def h_slots(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    throttle(me["user_id"])
    bet = await _read_bet(request)
    await _check_room(me, chat_id)
    reels = [random.randrange(len(SLOT_SYMBOLS)) for _ in range(3)]
    if reels[0] == reels[1] == reels[2]:
        mult = 30 if reels[0] == len(SLOT_SYMBOLS) - 1 else 15
    elif len(set(reels)) == 2:
        mult = 1
    else:
        mult = 0
    size = await webdb.apply_bet(me["user_id"], chat_id, bet, bet * mult, "slots")
    if size is None:
        raise GameError(f"Не хватает сантиметров (у тебя {me['size']})")
    if mult >= 15:
        await notify(chat_id, f"🎰 <b>{esc(me['username'])}</b> ловит три в ряд в слотах: <b>x{mult}</b> (+{bet * (mult - 1)} см)!")
    return ok(reels=reels, mult=mult, delta=size - me["size"], size=size)


async def h_emoji(request: web.Request) -> web.Response:
    me = await member(request)
    emoji = (await body(request)).get("emoji", "")
    if emoji and emoji not in webdb.EMOJIS:
        raise GameError("Такого значка нет")
    await webdb.set_emoji(me["user_id"], int(request.match_info["chat_id"]), emoji)
    return ok(emoji=emoji)


# ── арена ──

def _free_user(uid: int) -> None:
    """Игрок может войти в новую игру, если прошлая закончилась."""
    current = rooms.get(user_room.get(uid, ""))
    if current and current.status != "done":
        raise GameError("Ты уже в игре — сначала закончи или выйди")
    user_room.pop(uid, None)


async def _cleanup_rooms() -> None:
    for rid in [r for r, room in rooms.items() if room.expired() or (room.status == "done" and time.time() - room.updated > 300)]:
        room = rooms.pop(rid)
        await _settle_bets(room, refund=True)
        for p in room.players:
            if user_room.get(p) == rid:
                user_room.pop(p, None)


async def _settle_bets(room: Room, refund: bool = False) -> None:
    """Ставки зрителей: выигравшие делят банк пропорционально ставкам; ничья, пустая сторона или сбой — возврат."""
    if not room.bets or room.bets_done:
        return
    room.bets_done = True
    total = sum(a for _, a, _ in room.bets.values())
    win_pool = sum(a for on, a, _ in room.bets.values() if on == room.winner)
    if refund or room.winner is None or win_pool == 0:
        for uid, (_, amount, name) in room.bets.items():
            await db.add_size(uid, room.chat_id, name, amount, "bet_refund")
        if not refund:
            await notify(room.chat_id, "🎯 Ставки на партию возвращены: ничья или никто не угадал исход.")
        return
    lines = []
    for uid, (on, amount, name) in room.bets.items():
        if on == room.winner:
            payout = amount * total // win_pool
            await db.add_size(uid, room.chat_id, name, payout, "bet_win")
            if payout > amount:
                lines.append(f"{esc(name)} +{payout - amount} см")
    if lines:
        await notify(room.chat_id, f"🎯 <b>Ставки на партию сыграли:</b> {', '.join(lines)}")


async def _settle(room: Room) -> None:
    """Выплата за законченную партию (один раз) и сообщение в чат."""
    if room.status != "done" or room.settled:
        return
    room.settled = True
    await _settle_bets(room)
    if room.vs_bot or room.stake == 0:
        return
    if room.winner is None:
        await notify(room.chat_id, f"🤝 На арене ({GAMES[room.game]['title']}) ничья: <b>{esc(room.name(room.players[0]))}</b> и <b>{esc(room.name(room.players[1]))}</b>.")
        return
    loser = room.other(room.winner)
    await db.transfer_size(room.winner, loser, room.chat_id, room.stake, -room.stake, reason="arena")
    await notify(
        room.chat_id,
        f"🏟 <b>Арена:</b> {GAMES[room.game]['icon']} {GAMES[room.game]['title']}\n"
        f"🏆 <b>{esc(room.name(room.winner))}</b> побеждает <b>{esc(room.name(loser))}</b> и забирает <b>{room.stake} см</b>!",
    )


def _get_room(request: web.Request) -> Room:
    room = rooms.get(request.match_info["rid"])
    if not room or room.expired():
        raise GameError("Комната закрыта")
    if request["user"]["id"] not in room.players:
        raise GameError("Ты не участвуешь в этой игре")
    return room


async def h_rooms(request: web.Request) -> web.Response:
    await member(request)
    await _cleanup_rooms()
    chat_id = int(request.match_info["chat_id"])
    uid = request["user"]["id"]
    mine = rooms.get(user_room.get(uid, ""))
    await _mafia_cleanup()
    mmine = mafias.get(user_mafia.get(uid, ""))
    if mmine:
        await _mafia_sync(mmine)
    return ok(
        open=[r.summary() for r in rooms.values() if r.chat_id == chat_id and r.status == "open" and not r.vs_bot and uid not in r.players],
        mine=mine.view(uid) if mine and mine.chat_id == chat_id else None,
        live=[
            {
                "rid": r.rid, "game": r.game,
                "players": [{"id": p, "name": r.names[p]} for p in r.players],
                "pool": {str(p): sum(a for on, a, _ in r.bets.values() if on == p) for p in r.players},
                "mine": ({"on": r.bets[uid][0], "amount": r.bets[uid][1]} if uid in r.bets else None),
            }
            for r in rooms.values()
            if r.chat_id == chat_id and r.status == "playing" and not r.vs_bot and uid not in r.players
        ],
        bet_max=ARENA_BET_MAX,
        mafia_open=[m.summary() for m in mafias.values() if m.chat_id == chat_id and m.status == "open" and uid not in m.players],
        mafia_mine=mmine.view(uid) if mmine and mmine.chat_id == chat_id else None,
    )


async def h_room_create(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    uid = me["user_id"]
    data = await body(request)
    game = data.get("game")
    if game not in GAMES:
        raise GameError("Неизвестная игра")
    await _cleanup_rooms()
    _free_user(uid)
    room = Room(chat_id, game, uid, me["username"], bool(data.get("vs_bot")))
    rooms[room.rid] = room
    user_room[uid] = room.rid
    if not room.vs_bot:
        g = GAMES[game]
        await notify(chat_id, f"🏟 <b>{esc(me['username'])}</b> вызывает на арену: {g['icon']} {g['title']} (ставка {g['stake']} см)!\nОткрой мини-апп → «Арена», чтобы принять бой.")
    return ok(room=room.view(uid))


async def h_room_join(request: web.Request) -> web.Response:
    me = await member(request)
    uid = me["user_id"]
    room = rooms.get(request.match_info["rid"])
    if not room or room.chat_id != int(request.match_info["chat_id"]) or room.expired():
        raise GameError("Комната закрыта")
    _free_user(uid)
    room.join(uid, me["username"])
    user_room[uid] = room.rid
    return ok(room=room.view(uid))


async def h_room_get(request: web.Request) -> web.Response:
    room = _get_room(request)
    return ok(room=room.view(request["user"]["id"]))


async def h_room_act(request: web.Request) -> web.Response:
    room = _get_room(request)
    uid = request["user"]["id"]
    room.act(uid, str((await body(request)).get("action", "")))
    await _settle(room)
    return ok(room=room.view(uid))


async def h_room_leave(request: web.Request) -> web.Response:
    uid = request["user"]["id"]
    room = rooms.get(request.match_info["rid"])
    if room and uid in room.players:
        if room.status == "playing" and not room.vs_bot:
            room.finish(room.other(uid), f"{room.name(uid)} сдался")
            await _settle(room)
        elif room.status != "done" or room.vs_bot:
            rooms.pop(room.rid, None)
            for p in room.players:
                if user_room.get(p) == room.rid:
                    user_room.pop(p, None)
        # завершённую партию с живым соперником оставляем ему до конца просмотра результата
    user_room.pop(uid, None)
    return ok()


async def h_room_bet(request: web.Request) -> web.Response:
    me = await member(request)
    uid = me["user_id"]
    room = rooms.get(request.match_info["rid"])
    if not room or room.chat_id != int(request.match_info["chat_id"]) or room.expired():
        raise GameError("Партия уже закрыта")
    data = await body(request)
    try:
        on, amount = int(data.get("on")), int(data.get("amount"))
    except (TypeError, ValueError):
        raise GameError("Некорректная ставка")
    if room.status != "playing" or room.vs_bot:
        raise GameError("На эту партию сейчас нельзя ставить")
    if uid in room.players:
        raise GameError("Участникам ставить нельзя")
    if on not in room.players or not 1 <= amount <= ARENA_BET_MAX:
        raise GameError(f"Ставка от 1 до {ARENA_BET_MAX} см")
    if uid in room.bets:
        raise GameError("Ты уже сделал ставку на эту партию")
    throttle(uid)
    size = await webdb.apply_bet(uid, room.chat_id, amount, 0, "bet")
    if size is None:
        raise GameError(f"Не хватает сантиметров (у тебя {me['size']})")
    room.bets[uid] = (on, amount, me["username"])
    return ok(size=size)


# ── мафия ──

async def _mafia_sync(room: MafiaRoom) -> None:
    """Таймер фаз, выплата победителям и отправка накопленных сообщений в чат."""
    room.tick()
    if room.status == "done" and not room.settled:
        room.settled = True
        for uid in room.winners():
            await db.add_size(uid, room.chat_id, room.players[uid], MAFIA_REWARD, "mafia")
    while room.outbox:
        await notify(room.chat_id, room.outbox.pop(0))


async def _mafia_cleanup() -> None:
    for rid in [r for r, m in mafias.items() if m.expired() or (m.status == "done" and time.time() - m.updated > 300)]:
        room = mafias.pop(rid)
        for p in room.players:
            if user_mafia.get(p) == rid:
                user_mafia.pop(p, None)


def _mafia_room(request: web.Request) -> MafiaRoom:
    room = mafias.get(request.match_info["rid"])
    if not room or room.expired():
        raise GameError("Игра закрыта")
    return room


async def h_mafia_create(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    uid = me["user_id"]
    await _mafia_cleanup()
    current = mafias.get(user_mafia.get(uid, ""))
    if current and current.status != "done":
        raise GameError("Ты уже в мафии — сначала выйди")
    if any(m.chat_id == chat_id and m.status == "open" for m in mafias.values()):
        raise GameError("В этом чате уже собирают мафию — присоединяйся")
    room = MafiaRoom(chat_id, uid, me["username"])
    mafias[room.rid] = room
    user_mafia[uid] = room.rid
    await notify(chat_id, f"🕵️ <b>{esc(me['username'])}</b> собирает мафию (нужно {room.summary()['max']} игроков максимум, от 4)!\nОткрой мини-апп → «Арена» → «Мафия».")
    return ok(mafia=room.view(uid))


async def h_mafia_join(request: web.Request) -> web.Response:
    me = await member(request)
    uid = me["user_id"]
    room = _mafia_room(request)
    if room.chat_id != int(request.match_info["chat_id"]):
        raise GameError("Игра закрыта")
    current = mafias.get(user_mafia.get(uid, ""))
    if current and current.status != "done" and current is not room:
        raise GameError("Ты уже в другой мафии")
    room.join(uid, me["username"])
    user_mafia[uid] = room.rid
    return ok(mafia=room.view(uid))


async def h_mafia_start(request: web.Request) -> web.Response:
    room = _mafia_room(request)
    room.start(request["user"]["id"])
    await _mafia_sync(room)
    return ok(mafia=room.view(request["user"]["id"]))


async def h_mafia_act(request: web.Request) -> web.Response:
    room = _mafia_room(request)
    uid = request["user"]["id"]
    if uid not in room.players:
        raise GameError("Ты не участвуешь в этой игре")
    await _mafia_sync(room)
    room.act(uid, str((await body(request)).get("action", "")))
    await _mafia_sync(room)
    return ok(mafia=room.view(uid))


async def h_mafia_leave(request: web.Request) -> web.Response:
    uid = request["user"]["id"]
    room = mafias.get(request.match_info["rid"])
    if room and uid in room.players:
        room.leave(uid)
        await _mafia_sync(room)
        if room.status == "open" and not room.players:
            mafias.pop(room.rid, None)
    user_mafia.pop(uid, None)
    return ok()


# ── покер ──

async def _poker_persist(t: poker.PokerTable) -> None:
    """Депозиты за столом хранятся в базе: после перезапуска сервиса их вернут игрокам."""
    in_play = t.phase in ("pre", "flop", "turn", "river")
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("DELETE FROM poker_escrow WHERE tid = ?", (t.tid,))
        for s in t.seats:
            if s.leaving:
                continue
            chips = s.chips + (s.paid if in_play else 0)
            if chips > 0:
                await conn.execute("INSERT OR REPLACE INTO poker_escrow (tid, chat_id, user_id, name, chips) VALUES (?, ?, ?, ?, ?)",
                                   (t.tid, t.chat_id, s.uid, s.name, chips))
        await conn.commit()


async def _poker_sync(t: poker.PokerTable) -> None:
    t.tick()
    while t.cashouts:
        uid, name, chips = t.cashouts.pop(0)
        await db.add_size(uid, t.chat_id, name, chips, "poker_out")
        if user_table.get(uid) == t.tid and not t.seat_of(uid):
            user_table.pop(uid, None)
    while t.outbox:
        await notify(t.chat_id, t.outbox.pop(0))
    while t.finished:
        fin = t.finished.pop(0)
        for uid in fin["uids"]:
            await chat_events.bump(t.chat_id, uid, "poker")
        top = fin["top"]
        if top and top["amount"] >= t.buyin:
            await highlights.add(t.chat_id, "🃏", f"{top['name']} сорвал банк в покере: +{top['amount']} см" + (f" ({top['hand']})" if top["hand"] else ""))
    for uid in [u for u, tid in user_table.items() if tid == t.tid and not t.seat_of(u)]:
        user_table.pop(uid, None)
    await _poker_persist(t)


async def _poker_cleanup() -> None:
    for tid in list(tables):
        t = tables[tid]
        if t.expired() or not t.seats:
            t.close_all()
            await _poker_sync(t)
            tables.pop(tid, None)
            async with aiosqlite.connect(db.DB_PATH) as conn:
                await conn.execute("DELETE FROM poker_escrow WHERE tid = ?", (tid,))
                await conn.commit()


async def _poker_refund_all() -> None:
    """При старте сервиса возвращаем всё, что осталось на столах до перезапуска."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        cur = await conn.execute("SELECT chat_id, user_id, name, chips FROM poker_escrow")
        rows = await cur.fetchall()
        await conn.execute("DELETE FROM poker_escrow")
        await conn.commit()
    for chat_id, uid, name, chips in rows:
        await db.add_size(uid, chat_id, name, chips, "poker_out")
    if rows:
        log.info("Покер: возвращено депозитов после перезапуска: %s", len(rows))


async def _poker_loop() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            for t in list(tables.values()):
                await _poker_sync(t)
            await _poker_cleanup()
        except Exception:
            log.exception("Ошибка цикла покера")


def _table(request: web.Request) -> poker.PokerTable:
    t = tables.get(request.match_info["tid"])
    if not t:
        raise GameError("Стол закрыт")
    return t


async def h_poker_state(request: web.Request) -> web.Response:
    await member(request)
    chat_id = int(request.match_info["chat_id"])
    uid = request["user"]["id"]
    mine = tables.get(user_table.get(uid, ""))
    if mine:
        await _poker_sync(mine)
    return ok(
        mine=mine.view(uid) if mine and mine.chat_id == chat_id and mine.seat_of(uid) else None,
        open=[t.summary() for t in tables.values() if t.chat_id == chat_id and len(t.seats) < poker.MAX_SEATS and not t.seat_of(uid)],
        levels=[{"key": k, **v} for k, v in poker.LEVELS.items()],
    )


async def _poker_sit(t: poker.PokerTable, me: dict) -> None:
    uid = me["user_id"]
    if t.seat_of(uid):
        raise GameError("Ты уже за этим столом")
    if len(t.seats) >= poker.MAX_SEATS:
        raise GameError("За столом нет мест")
    if await webdb.apply_bet(uid, t.chat_id, t.buyin, 0, "poker_in") is None:
        raise GameError(f"Для входа нужно {t.buyin} см, у тебя {me['size']}")
    t.add(uid, me["username"])
    user_table[uid] = t.tid


async def h_poker_create(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    uid = me["user_id"]
    throttle(uid)
    level = str((await body(request)).get("level", "small"))
    if level not in poker.LEVELS:
        raise GameError("Нет такого стола")
    await _poker_cleanup()
    if user_table.get(uid) in tables:
        raise GameError("Ты уже играешь за столом — сначала встань из-за него")
    if sum(1 for t in tables.values() if t.chat_id == chat_id) >= MAX_TABLES_PER_CHAT:
        raise GameError("В этом чате уже открыто много столов — присоединяйся к любому")
    cfg = poker.LEVELS[level]
    if await webdb.apply_bet(uid, chat_id, cfg["buyin"], 0, "poker_in") is None:
        raise GameError(f"Для входа нужно {cfg['buyin']} см, у тебя {me['size']}")
    t = poker.PokerTable(chat_id, uid, me["username"], level)
    tables[t.tid] = t
    user_table[uid] = t.tid
    await _poker_persist(t)
    await notify(chat_id, f"🃏 <b>{esc(me['username'])}</b> открыл покерный стол «{cfg['title']}» (вход {cfg['buyin']} см, блайнды {cfg['sb']}/{cfg['bb']}).\nМини-апп → «Покер», садись!")
    return ok(table=t.view(uid))


async def h_poker_join(request: web.Request) -> web.Response:
    me = await member(request)
    uid = me["user_id"]
    throttle(uid)
    t = _table(request)
    if t.chat_id != int(request.match_info["chat_id"]):
        raise GameError("Стол закрыт")
    if user_table.get(uid) in tables and user_table.get(uid) != t.tid:
        raise GameError("Ты уже играешь за другим столом")
    await _poker_sit(t, me)
    await _poker_sync(t)
    return ok(table=t.view(uid))


async def h_poker_act(request: web.Request) -> web.Response:
    t = _table(request)
    uid = request["user"]["id"]
    data = await body(request)
    try:
        amount = int(data.get("amount") or 0)
    except (TypeError, ValueError):
        raise GameError("Некорректная сумма")
    if not t.seat_of(uid):
        raise GameError("Ты не за этим столом")
    t.act(uid, str(data.get("action", "")), amount)
    await _poker_sync(t)
    return ok(table=t.view(uid))


async def h_poker_leave(request: web.Request) -> web.Response:
    uid = request["user"]["id"]
    t = tables.get(request.match_info["tid"])
    if t and t.seat_of(uid):
        t.leave(uid)
        await _poker_sync(t)
    user_table.pop(uid, None)
    return ok()


# ── район (РП) ──

async def h_rp_state(request: web.Request) -> web.Response:
    me = await member(request)
    return ok(**await rp.state(int(request.match_info["chat_id"]), me["user_id"]))


async def h_flat(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    owner = int(request.query.get("owner") or me["user_id"])
    return ok(**await flat.view(chat_id, me["user_id"], owner))


JAIL_OK = {"bribe", "escape", "prison_work", "prison_smuggle", "prison_buy_time", "lawyer_hire", "lawyer_release", "lawyer_clear", "summon_answer", "lawyer_answer", "court_defend", "court_plea", "snitch", "repay"}


def _rp_action(fn, *keys):
    async def handler(request: web.Request) -> web.Response:
        me = await member(request)
        throttle(me["user_id"], 0.7)
        if await __import__("doom").is_dead(int(request.match_info["chat_id"]), me["user_id"]):
            raise GameError("☠ Твой персонаж погиб в Судный день: действия недоступны")
        if fn.__name__ not in JAIL_OK and await rp.is_jailed(int(request.match_info["chat_id"]), me["user_id"]):
            raise GameError("Ты за решёткой: пока не выйдешь, делать нельзя. Тебя может выкупить банда (побег или адвокат из общака) или нанять адвоката сам (Район → Имущество).")
        data = await body(request)
        args = []
        for k in keys:
            v = data.get(k)
            args.append(int(v) if k == "cm" and v is not None else v)
        try:
            res = await fn(int(request.match_info["chat_id"]), me["user_id"], me["username"], *args)
        except (TypeError, ValueError):
            raise GameError("Некорректный запрос")
        if "size" not in res:
            res["size"] = (await webdb.player(int(request.match_info["chat_id"]), me["user_id"]))["size"]
        return ok(**res)
    return handler


# ── тамагочи ──

async def h_relations(request: web.Request) -> web.Response:
    me = await member(request)
    import relations
    return ok(relations=await relations.top(int(request.match_info["chat_id"]), me["user_id"]))


# ── задания, босс, сезон ──

async def h_card(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    throttle(me["user_id"], 30)
    players = await webdb.players(chat_id)
    rank = next((i + 1 for i, p in enumerate(players) if p["user_id"] == me["user_id"]), 0)
    st = await webdb.stats(chat_id, me["user_id"])
    ach = achievements(me, st)
    n = events.now_local()
    season = await events.season_board(chat_id, n.year, n.month, limit=50)
    srank = next((i + 1 for i, r in enumerate(season) if r["user_id"] == me["user_id"]), 0)
    look = await _look(chat_id, me["user_id"])
    png = card.render(me["username"], me.get("title", ""), me["size"], rank, len(players), me["streak"], me["rep"],
                      srank, sum(1 for a in ach if a["done"]), len(ach), [], frame=look["frame"], bg=look["bg"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[app_link_button("🏙 Район", "rp", chat_id)]])
    await bot.send_photo(chat_id, BufferedInputFile(png, "card.png"), caption=f"🪪 Карточка игрока {esc(me['username'])}", reply_markup=kb)
    return ok()


async def h_tasks(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    boss, event = await events.ensure_boss(chat_id)
    if event and "spawn" in event:
        await _announce_boss(chat_id, boss)
    return ok(
        quests=await events.quests_view(chat_id, me["user_id"]),
        boss=await events._boss_view(boss, me["user_id"]),
        season=await events.season_view(chat_id),
    )


async def _announce_boss(chat_id: int, boss: dict) -> None:
    await notify(chat_id, events.spawn_text({**boss, "name": esc(boss["name"])}))


async def h_quest_claim(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    qid = str((await body(request)).get("id", ""))
    reward = await events.claim_quest(chat_id, me["user_id"], me["username"], qid)
    if not reward:
        raise GameError("Награду пока нельзя забрать")
    fresh = await webdb.player(chat_id, me["user_id"])
    return ok(reward=reward, size=fresh["size"], quests=await events.quests_view(chat_id, me["user_id"]))


async def h_boss_hit(request: web.Request) -> web.Response:
    me = await member(request)
    chat_id = int(request.match_info["chat_id"])
    throttle(me["user_id"], 0.6)
    try:
        res = await events.boss_hit(chat_id, me["user_id"], me["username"])
    except ValueError as exc:
        raise GameError(str(exc))
    if res["killed"]:
        top = res["rewards"][:3]
        lines = "\n".join(f"{['🥇','🥈','🥉'][i]} {esc(r['name'])} — {r['dmg']} урона (+{r['reward']} см)" for i, r in enumerate(top))
        await notify(chat_id, f"🏆 <b>Босс повержен!</b> Последний удар нанёс <b>{esc(me['username'])}</b>.\n{lines}\nНаграда выдана всем, кто участвовал ({len(res['rewards'])} чел.).")
    fresh = await webdb.player(chat_id, me["user_id"])
    res["size"] = fresh["size"]
    return ok(**res)


# ───────────────────────────── запуск ─────────────────────────────

async def index(request: web.Request) -> web.Response:
    # версия в адресе скриптов и стилей: Telegram не должен держать старый кэш после обновлений
    version = int(max(f.stat().st_mtime for f in STATIC.iterdir()))
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("/static/style.css", f"/static/style.css?v={version}")
    html = html.replace("/static/app.js", f"/static/app.js?v={version}")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def on_startup(app: web.Application) -> None:
    global bot
    await db.init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await _poker_refund_all()
    app["poker_task"] = asyncio.create_task(_poker_loop())


async def on_cleanup(app: web.Application) -> None:
    task = app.get("poker_task")
    if task:
        task.cancel()
    for t in list(tables.values()):  # вернуть депозиты при штатной остановке
        t.close_all()
        try:
            await _poker_sync(t)
        except Exception:
            log.exception("Не удалось вернуть депозиты стола %s", t.tid)
    if bot:
        await bot.session.close()


def make_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware])
    c = "/api/chat/{chat_id}"
    app.add_routes([
        web.get("/", index),
        web.get("/api/me", h_me),
        web.get(c + "/board", h_board),
        web.get(c + "/player/{uid}", h_profile),
        web.post(c + "/daily", h_daily),
        web.post(c + "/wheel", h_wheel),
        web.post(c + "/slots", h_slots),
        web.post(c + "/emoji", h_emoji),
        web.get(c + "/rooms", h_rooms),
        web.post(c + "/rooms", h_room_create),
        web.post(c + "/rooms/{rid}/join", h_room_join),
        web.get("/api/room/{rid}", h_room_get),
        web.post("/api/room/{rid}/act", h_room_act),
        web.post("/api/room/{rid}/leave", h_room_leave),
        web.post(c + "/give", h_give),
        web.get(c + "/rp", h_rp_state),
        web.get(c + "/flat", h_flat),
        web.post(c + "/rp/flat/buy", _rp_action(flat.buy, "item", "slot")),
        web.post(c + "/rp/flat/sell", _rp_action(flat.sell, "slot")),
        web.post(c + "/rp/flat/theme", _rp_action(flat.theme, "key")),
        web.post(c + "/rp/flat/rest", _rp_action(flat.rest)),
        web.post(c + "/rp/flat/visit", _rp_action(flat.visit, "owner")),
        web.post(c + "/rp/flat/note", _rp_action(flat.note, "owner", "text")),
        web.post(c + "/rp/flat/notedel", _rp_action(flat.note_del, "nid", "owner")),
        web.post(c + "/rp/char", _rp_action(rp.set_char, "icon", "hat", "acc", "bio")),
        web.post(c + "/rp/gang/pact", _rp_action(gangplus3.pact_propose, "gid")),
        web.post(c + "/rp/gang/pact/answer", _rp_action(gangplus3.pact_answer, "pid", "accept")),
        web.post(c + "/rp/gang/pact/break", _rp_action(gangplus3.pact_break, "pid")),
        web.post(c + "/rp/gang/pact/stab", _rp_action(gangplus3.pact_stab, "pid")),
        web.post(c + "/rp/gang/pact/aid", _rp_action(gangplus3.pact_aid, "pid")),
        web.post(c + "/rp/gang/project", _rp_action(gangplus3.proj_build, "key")),
        web.post(c + "/rp/visit", _rp_action(gangplus3.visit_answer, "vid", "choice")),
        web.post(c + "/rp/lawyer/hire", _rp_action(gangplus3.lawyer_hire)),
        web.post(c + "/rp/lawyer/release", _rp_action(gangplus3.lawyer_release)),
        web.post(c + "/rp/lawyer/clear", _rp_action(gangplus3.lawyer_clear)),
        web.post(c + "/rp/guest", _rp_action(gangplus3.guest_take, "offer")),
        web.post(c + "/rp/attack", _rp_action(gangplus4.attack_answer, "aid", "choice")),
        web.post(c + "/rp/defense", _rp_action(gangplus4.defense_buy, "kind")),
        web.post(c + "/rp/gang/spec", _rp_action(gangplus4.spec_set, "key")),
        web.post(c + "/rp/gang/spec/act", _rp_action(gangplus4.spec_act, "act", "arg")),
        web.post(c + "/rp/gang/watch", _rp_action(gangplus4.watch_start)),
        web.post(c + "/rp/gang/pot", _rp_action(gangplus4.pot_add, "amount")),
        web.post(c + "/rp/gang/tourney", _rp_action(gangplus4.tourney_fight)),
        web.post(c + "/rp/gang/claim", _rp_action(gangplus5.claim, "key")),
        web.post(c + "/rp/gang/sabotage", _rp_action(police.sabotage, "key", "mode")),
        web.post(c + "/rp/gang/fortify", _rp_action(gangplus5.fortify, "key")),
        web.post(c + "/rp/gang/blockade", _rp_action(gangplus5.blockade, "key")),
        web.post(c + "/rp/gang/craft", _rp_action(gangplus5.craft, "recipe", "qty")),
        web.post(c + "/rp/gang/sell", _rp_action(gangplus5.sell, "item", "qty", "buyer")),
        web.post(c + "/rp/gang/sellall", _rp_action(gangplus5.sell_all)),
        web.post(c + "/rp/patron/loan", _rp_action(patrons.loan_accept)),
        web.post(c + "/rp/patron/repay", _rp_action(patrons.loan_repay)),
        web.post(c + "/rp/patron/merc", _rp_action(patrons.merc_hire)),
        web.post(c + "/rp/patron/tribute", _rp_action(patrons.tribute_pay)),
        web.post(c + "/rp/patron/refuse", _rp_action(patrons.tribute_refuse)),
        web.post(c + "/rp/patron/bribe", _rp_action(patrons.mayor_bribe)),
        web.post(c + "/rp/power/god", _rp_action(power.become_godfather)),
        web.post(c + "/rp/power/nominate", _rp_action(power.nominate)),
        web.post(c + "/rp/power/collect", _rp_action(power.collect)),
        web.post(c + "/rp/power/fund", _rp_action(power.fund, "side", "amount", "gid")),
        web.post(c + "/rp/power/pressure", _rp_action(power.pressure, "target")),
        web.post(c + "/rp/power/mayor_anti", _rp_action(power.mayor_anti_godfather)),
        web.post(c + "/rp/power/mayor_war", _rp_action(power.mayor_war)),
        web.post(c + "/rp/power/god_war", _rp_action(power.god_war)),
        web.post(c + "/rp/power/mayor_raid", _rp_action(power.mayor_raid)),
        web.post(c + "/rp/power/god_riot", _rp_action(power.god_riot)),
        web.post(c + "/rp/power/roof", _rp_action(power.roof_pay)),
        web.post(c + "/rp/power/captain", _rp_action(control.appoint_captain, "target")),
        web.post(c + "/rp/power/levy", _rp_action(control.levy, "gid", "mode")),
        web.post(c + "/rp/notify/toggle", _rp_action(__import__("guide").toggle)),
        web.post(c + "/rp/res/settle", _rp_action(__import__("residents").settle, "key")),
        web.post(c + "/rp/res/support", _rp_action(__import__("residents").support, "side")),
        web.post(c + "/rp/res/gift", _rp_action(__import__("residents").gift, "key")),
        web.post(c + "/rp/power/prop", _rp_action(control.decide, "accept")),
        web.post(c + "/rp/power/sabotage", _rp_action(control.sabotage, "gid")),
        web.post(c + "/rp/power/bribe_member", _rp_action(control.bribe_member, "gid")),
        web.post(c + "/rp/power/incite", _rp_action(control.incite, "a", "b")),
        web.post(c + "/rp/police/curfew", _rp_action(control.curfew, "key")),
        web.post(c + "/rp/police/outpost", _rp_action(control.outpost, "key")),
        web.post(c + "/rp/police/outpost_close", _rp_action(control.outpost_close, "key")),
        web.post(c + "/rp/police/kpp", _rp_action(control.kpp, "key")),
        web.post(c + "/rp/justice/bribe", _rp_action(justice.bribe, "amount")),
        web.post(c + "/rp/justice/snitch", _rp_action(justice.snitch)),
        web.post(c + "/rp/justice/defend", _rp_action(justice.court_defend)),
        web.post(c + "/rp/justice/plea", _rp_action(justice.court_plea)),
        web.post(c + "/rp/justice/witness", _rp_action(justice.court_witness, "side")),
        web.post(c + "/rp/debt/take", _rp_action(__import__("debts").take, "gid", "amount", "hours")),
        web.post(c + "/rp/debt/answer", _rp_action(__import__("debts").answer, "did", "accept")),
        web.post(c + "/rp/power/budget", _rp_action(power.mayor_budget, "amount")),
        web.post(c + "/rp/mayor/campaign", _rp_action(gangplus10.mayor_campaign)),
        web.post(c + "/rp/gang/deal", _rp_action(gangplus5.deal_offer, "gid", "kind", "item", "qty", "price")),
        web.post(c + "/rp/gang/deal/answer", _rp_action(gangplus5.deal_answer, "did", "accept")),
        web.post(c + "/rp/gang/truce", _rp_action(gangplus5.truce_buy, "gid")),
        web.post(c + "/rp/gang/op", _rp_action(gangplus5.op_start, "kind")),
        web.post(c + "/rp/gang/op/stage", _rp_action(gangplus5.op_stage)),
        web.post(c + "/rp/gang/scan", _rp_action(gangplus5.counter_scan)),
        web.post(c + "/rp/gang/mole", _rp_action(gangplus5.mole_decide, "mode")),
        web.post(c + "/rp/gang/frame", _rp_action(gangplus5.frame, "gid")),
        web.post(c + "/rp/event", _rp_action(liveevents.act_app, "eid", "action")),
        web.post(c + "/rp/law", _rp_action(liveevents.law_vote_app, "vid", "law")),
        web.post(c + "/rp/contract/accept", _rp_action(gangplus8.contract_accept, "cid", "npc")),
        web.post(c + "/rp/contract/finish", _rp_action(gangplus8.contract_finish, "cid")),
        web.post(c + "/rp/gang/brig/hire", _rp_action(gangplus8.brig_hire, "role")),
        web.post(c + "/rp/gang/brig/train", _rp_action(gangplus8.brig_train, "bid")),
        web.post(c + "/rp/gang/brig/fire", _rp_action(gangplus8.brig_fire, "bid")),
        web.post(c + "/rp/gang/brig/task", _rp_action(gangplus8.brig_task, "bid", "task")),
        web.post(c + "/rp/gang/racket/claim", _rp_action(gangplus8.racket_claim, "key")),
        web.post(c + "/rp/gang/racket/greed", _rp_action(gangplus8.racket_greed, "key", "level")),
        web.post(c + "/rp/gang/racket/collect", _rp_action(gangplus8.racket_collect)),
        web.post(c + "/rp/gang/racket/challenge", _rp_action(gangplus8.racket_challenge, "key")),
        web.post(c + "/rp/gang/debt/lend", _rp_action(__import__("debts").offer, "target", "amount", "hours")),
        web.post(c + "/rp/debt/repay", _rp_action(__import__("debts").repay, "did", "amount")),
        web.post(c + "/rp/gang/debt/audit", _rp_action(gangplus9.debt_audit)),
        web.post(c + "/rp/gang/heir", _rp_action(gangplus9.heir_set, "target")),
        web.post(c + "/rp/gang/abdicate", _rp_action(gangplus9.abdicate)),
        web.post(c + "/rp/gang/emblem", _rp_action(gangplus9.emblem_set, "emoji", "c1", "c2", "motto")),
        web.post(c + "/rp/gang/graffiti", _rp_action(gangplus9.graffiti)),
        web.post(c + "/rp/gang/rules", _rp_action(gangplus9.rules_set, "text")),
        web.post(c + "/rp/gang/trial", _rp_action(gangplus9.trial_open, "defendant", "rule", "evidence")),
        web.post(c + "/rp/gang/trial/vote", _rp_action(gangplus9.trial_vote, "tid", "verdict")),
        web.post(c + "/rp/gang/defect", _rp_action(gangplus9.defect_offer, "target", "offer", "role")),
        web.post(c + "/rp/defect/answer", _rp_action(gangplus9.defect_answer, "did", "accept")),
        web.post(c + "/rp/gang/defect/bounty", _rp_action(gangplus9.defect_bounty, "did", "amount")),
        web.post(c + "/rp/gang/defect/hunt", _rp_action(gangplus9.defect_hunt, "did")),
        web.post(c + "/rp/gang/room/repair", _rp_action(gangplus9.room_repair, "room")),
        web.post(c + "/rp/gang/room/ransack", _rp_action(gangplus9.ransack, "room")),
        web.post(c + "/rp/gang/assault", _rp_action(gangplus9.assault)),
        web.post(c + "/rp/gang/peace", _rp_action(gangplus9.peace_offer, "kind", "amount", "key_a", "key_b")),
        web.post(c + "/rp/gang/peace/answer", _rp_action(gangplus9.peace_answer, "pid", "accept")),
        web.post(c + "/rp/prison/work", _rp_action(gangplus10.prison_work)),
        web.post(c + "/rp/prison/smuggle", _rp_action(gangplus10.prison_smuggle)),
        web.post(c + "/rp/prison/buy", _rp_action(gangplus10.prison_buy_time, "minutes")),
        web.post(c + "/rp/prison/parcel", _rp_action(gangplus10.prison_parcel, "target")),
        web.post(c + "/rp/gang/prison/breakout", _rp_action(gangplus10.prison_breakout, "target")),
        web.post(c + "/rp/gang/prison/lawyer", _rp_action(gangplus10.prison_lawyer, "target")),
        web.post(c + "/rp/council/vote", _rp_action(gangplus10.council_vote, "cand")),
        web.post(c + "/rp/mayor/set", _rp_action(gangplus10.mayor_set, "tax", "curfew", "police", "align")),
        web.post(c + "/rp/mayor/pardon", _rp_action(gangplus10.mayor_pardon, "target")),
        web.post(c + "/rp/mayor/peace", _rp_action(gangplus10.mayor_peace, "gid")),
        web.post(c + "/rp/mayor/crackdown", _rp_action(gangplus10.mayor_crackdown)),
        web.post(c + "/rp/mayor/impeach", _rp_action(gangplus10.mayor_impeach)),
        web.post(c + "/rp/police/join", _rp_action(police.join_force)),
        web.post(c + "/rp/police/leave", _rp_action(police.leave_force)),
        web.post(c + "/rp/police/dismiss", _rp_action(police.dismiss, "target")),
        web.post(c + "/rp/police/patrol", _rp_action(police.patrol)),
        web.post(c + "/rp/police/shift", _rp_action(police.shift)),
        web.post(c + "/rp/police/patrol/district", _rp_action(police.patrol_district, "key")),
        web.post(c + "/rp/police/arrest", _rp_action(police.arrest, "target")),
        web.post(c + "/rp/police/raid", _rp_action(police.raid, "gid")),
        web.post(c + "/rp/police/wiretap", _rp_action(police.wiretap, "gid")),
        web.post(c + "/rp/police/checkpoint", _rp_action(police.checkpoint, "key")),
        web.post(c + "/rp/police/bigcase", _rp_action(police.big_case, "gid")),
        web.post(c + "/rp/gang/bribe", _rp_action(police.bribe_offer, "amount")),
        web.post(c + "/rp/police/bribe", _rp_action(police.bribe_answer, "bid", "accept")),
        web.post(c + "/rp/police/summon", _rp_action(police2.summon, "target")),
        web.post(c + "/rp/police/summon/answer", _rp_action(police2.summon_answer, "sid", "choice")),
        web.post(c + "/rp/police/search", _rp_action(police2.search, "target")),
        web.post(c + "/rp/police/inspect", _rp_action(police2.inspect_point, "key")),
        web.post(c + "/rp/police/lawyer", _rp_action(police2.lawyer_offer, "target")),
        web.post(c + "/rp/police/lawyer/answer", _rp_action(police2.lawyer_answer, "oid", "accept")),
        web.post(c + "/rp/police/ai", _rp_action(police2.ai_mode, "mode")),
        web.post(c + "/rp/patron/atone", _rp_action(world.atone)),
        web.post(c + "/rp/world/impeach", _rp_action(world.impeach_vote)),
        web.post(c + "/rp/duel/side", _rp_action(world.duel_side, "side")),
        web.post(c + "/rp/duel/fund", _rp_action(world.duel_fund, "amount")),
        web.post(c + "/rp/city/hide", _rp_action(world.city_hide)),
        web.post(c + "/rp/city/support", _rp_action(world.city_support)),
        web.post(c + "/rp/city/scab", _rp_action(world.city_scab)),
        web.post(c + "/rp/city/defend", _rp_action(world.city_defend)),
        web.post(c + "/rp/world/shake", _rp_action(world.shake, "cid")),
        web.post(c + "/rp/world/lure", _rp_action(world.lure, "cid")),
        web.post(c + "/rp/world/hush", _rp_action(world.hush)),
        web.post(c + "/rp/spa", _rp_action(balance.spa)),
        web.post(c + "/rp/task/give", _rp_action(assign.give, "target", "kind")),
        web.post(c + "/rp/task/do", _rp_action(assign.do, "tid")),
        web.post(c + "/rp/blitz/start", _rp_action(conflict.blitz_start, "key")),
        web.post(c + "/rp/blitz/hold", _rp_action(conflict.blitz_hold, "key")),
        web.post(c + "/rp/blitz/reinforce", _rp_action(conflict.blitz_reinforce, "key")),
        web.post(c + "/rp/blitz/disperse", _rp_action(conflict.blitz_disperse, "key")),
        web.post(c + "/rp/point/raid", _rp_action(conflict.point_raid, "key")),
        web.post(c + "/rp/convoy/send", _rp_action(conflict.convoy_send, "kind", "size", "escort", "bribe")),
        web.post(c + "/rp/convoy/ambush", _rp_action(conflict.convoy_ambush, "cid")),
        web.post(c + "/rp/police/roadpost", _rp_action(conflict.roadpost)),
        web.post(c + "/rp/lot/auction", _rp_action(conflict.lot_auction, "lid")),
        web.post(c + "/rp/lot/redeem", _rp_action(conflict.lot_redeem, "lid")),
        web.post(c + "/rp/bm/bid", _rp_action(conflict.bm_bid, "amount")),
        web.post(c + "/rp/police/infiltrate", _rp_action(conflict.infiltrate, "gid")),
        web.post(c + "/rp/police/special", _rp_action(conflict.special_op, "gid")),
        web.post(c + "/rp/clash/start", _rp_action(conflict.clash_start, "key")),
        web.post(c + "/rp/clash/join", _rp_action(conflict.clash_join, "key")),
        web.post(c + "/rp/collude/order", _rp_action(conflict.collude_order, "target", "price")),
        web.post(c + "/rp/collude/answer", _rp_action(conflict.collude_answer, "did", "accept")),
        web.post(c + "/rp/collude/tip", _rp_action(conflict.collude_tip, "target")),
        web.post(c + "/rp/charity", _rp_action(balance.charity, "amount")),
        web.post(c + "/rp/welfare/claim", _rp_action(balance.welfare_claim)),
        web.post(c + "/rp/tax/buyout", _rp_action(balance.buyout)),
        web.post(c + "/rp/order", _rp_action(gangplus11.order_create, "target", "hired", "price", "kind")),
        web.post(c + "/rp/gang/order/answer", _rp_action(gangplus11.order_answer, "oid", "accept")),
        web.post(c + "/rp/stash/put", _rp_action(gangplus11.stash_put, "amount")),
        web.post(c + "/rp/stash/take", _rp_action(gangplus11.stash_take, "amount")),
        web.post(c + "/rp/gang/bonus", _rp_action(gangplus11.bonus, "target", "amount")),
        web.post(c + "/rp/gang/fine", _rp_action(gangplus11.fine_member, "target", "amount")),
        web.post(c + "/rp/gang/protect", _rp_action(gangplus11.protect_offer, "target", "price")),
        web.post(c + "/rp/protect/answer", _rp_action(gangplus11.protect_answer, "pid", "accept")),
        web.post(c + "/rp/gang/create", _rp_action(gang.create, "name")),
        web.post(c + "/rp/gang/join", _rp_action(gang.accept, "gid")),
        web.post(c + "/rp/gang/accept", _rp_action(gang.accept, "gid")),
        web.post(c + "/rp/gang/decline", _rp_action(gang.decline, "gid")),
        web.post(c + "/rp/gang/invite", _rp_action(gang.invite, "target")),
        web.post(c + "/rp/gang/uninvite", _rp_action(gang.cancel_invite, "target")),
        web.post(c + "/rp/gang/kick", _rp_action(gang.kick, "target")),
        web.post(c + "/rp/gang/war", _rp_action(gangwar.declare, "gid")),
        web.post(c + "/rp/gang/raid", _rp_action(gangwar.raid)),
        web.post(c + "/rp/gang/betray", _rp_action(gangplus.betray)),
        web.post(c + "/rp/gang/accuse", _rp_action(gangplus.accuse, "target")),
        web.post(c + "/rp/gang/leak", _rp_action(gangplus.leak)),
        web.post(c + "/rp/gang/frame", _rp_action(gangplus.frame, "target")),
        web.post(c + "/rp/gang/clue", _rp_action(gangplus.buy_clue)),
        web.post(c + "/rp/gang/donate", _rp_action(gangplus.donate, "amount")),
        web.post(c + "/rp/gang/bail", _rp_action(gangplus.bail, "target")),
        web.post(c + "/rp/gang/coup", _rp_action(gangplus.coup_start)),
        web.post(c + "/rp/gang/vote", _rp_action(gangplus.coup_vote, "side")),
        web.post(c + "/rp/gang/role", _rp_action(gang.setrole, "target", "role")),
        web.post(c + "/rp/gang/dues", _rp_action(gangplus2.pay_dues)),
        web.post(c + "/rp/gang/plot", _rp_action(gangplus2.plot_start, "method")),
        web.post(c + "/rp/gang/plot/step", _rp_action(gangplus2.plot_step)),
        web.post(c + "/rp/gang/plot/abort", _rp_action(gangplus2.plot_abort)),
        web.post(c + "/rp/gang/rheist", _rp_action(gangplus2.rheist_plan)),
        web.post(c + "/rp/gang/rheist/join", _rp_action(gangplus2.rheist_join)),
        web.post(c + "/rp/gang/interrogate", _rp_action(gangplus2.interrogate, "target")),
        web.post(c + "/rp/gang/amnesty", _rp_action(gangplus2.amnesty)),
        web.post(c + "/rp/gang/repent", _rp_action(gangplus2.repent, "gid")),
        web.post(c + "/rp/gang/repent/decide", _rp_action(gangplus2.repent_decide, "target", "accept")),
        web.post(c + "/rp/gang/meet", _rp_action(gangplus2.meet_call)),
        web.post(c + "/rp/gang/meet/join", _rp_action(gangplus2.meet_attend)),
        web.post(c + "/rp/gang/agent/enlist", _rp_action(gangplus2.agent_enlist)),
        web.post(c + "/rp/gang/agent/report", _rp_action(gangplus2.agent_report)),
        web.post(c + "/rp/gang/agent/quit", _rp_action(gangplus2.agent_quit)),
        web.post(c + "/rp/gang/leave", _rp_action(gang.leave)),
        web.post(c + "/rp/gang/heist", _rp_action(gang.heist)),
        web.post(c + "/rp/work", _rp_action(rp.work, "key")),
        web.post(c + "/rp/dirty", _rp_action(rp.dirty, "key")),
        web.post(c + "/rp/crime", _rp_action(rp.crime, "key")),
        web.post(c + "/rp/collect", _rp_action(rp.collect)),
        web.post(c + "/rp/buy", _rp_action(rp.buy, "kind")),
        web.post(c + "/rp/exchange", _rp_action(rp.exchange, "cm")),
        web.post(c + "/bar/buy", _rp_action(bar.buy, "id")),
        web.post(c + "/rp/bribe", _rp_action(rp.bribe)),
        web.post(c + "/rp/escape", _rp_action(rp.escape)),
        web.post(c + "/rooms/{rid}/bet", h_room_bet),
        web.post(c + "/card", h_card),
        web.get(c + "/poker", h_poker_state),
        web.post(c + "/poker", h_poker_create),
        web.post(c + "/poker/{tid}/join", h_poker_join),
        web.post("/api/poker/{tid}/act", h_poker_act),
        web.post("/api/poker/{tid}/leave", h_poker_leave),
        web.get(c + "/cosmetics", h_cosmetics),
        web.post(c + "/cosmetics/buy", _cosmetic_action(cosmetics.buy)),
        web.post(c + "/cosmetics/equip", _cosmetic_action(cosmetics.equip)),
        web.get(c + "/relations", h_relations),
        web.post(c + "/mafia", h_mafia_create),
        web.post(c + "/mafia/{rid}/join", h_mafia_join),
        web.post("/api/mafia/{rid}/start", h_mafia_start),
        web.post("/api/mafia/{rid}/act", h_mafia_act),
        web.post("/api/mafia/{rid}/leave", h_mafia_leave),
        web.get(c + "/tasks", h_tasks),
        web.post(c + "/quests/claim", h_quest_claim),
        web.post(c + "/boss/hit", h_boss_hit),
        web.static("/static", STATIC, append_version=True),
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    web.run_app(make_app(), host=HOST, port=PORT)
