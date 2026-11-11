"""Живое закреплённое сообщение чата, пинги о боссе и сообщения «тебя обогнали»."""
import hashlib
import json
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

import database as db
import events
from keyboards import app_link_button
from utils import day_str, esc, now_local

log = logging.getLogger(__name__)

LIVE_EVERY = 600  # секунд между обновлениями закрепа


def live_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        app_link_button("📱 Мини-апп", chat_id=chat_id),
        app_link_button("🏙 Район", "rp", chat_id),
    ], [
        app_link_button("🃏 Арена", "arena", chat_id),
        app_link_button("🏙 Район", "rp", chat_id),
    ]])


def _hours(sec: int) -> str:
    return f"{sec // 3600} ч" if sec >= 3600 else f"{max(1, sec // 60)} мин"


async def live_text(chat_id: int) -> str:
    lines = ["📱 <b>Мини-апп чата</b>"]
    boss, _ = await events.ensure_boss(chat_id)
    if boss and boss["status"] == "alive":
        pct = round(100 * boss["hp"] / boss["max_hp"])
        left = max(0, boss["ends"] - int(time.time()))
        lines.append(f"{boss['emoji']} Босс <b>{esc(boss['name'])}</b>: {pct}% HP, осталось {_hours(left)}")
    elif boss:
        lines.append(f"{boss['emoji']} Босс <b>{esc(boss['name'])}</b> {'повержен' if boss['status'] == 'dead' else 'сбежал'}, скоро новый")
    season = await events.season_view(chat_id)
    top = season["board"][:3]
    if top:
        medals = ["🥇", "🥈", "🥉"]
        lines.append("🏆 Сезон: " + " · ".join(f"{medals[i]} {esc(r['username'])} {r['score']:+d}" for i, r in enumerate(top)))
    _, total, players = await db.lottery_state(chat_id, day_str(), 0)
    lines.append(f"🎟 Лотерея сегодня: {total} билетов, {players} участников (розыгрыш в 21:00)")
    streaks = await db._top_streak(chat_id) if hasattr(db, "_top_streak") else None
    if streaks:
        lines.append(f"🔥 Самая длинная серия бонуса: {esc(streaks[0])} — {streaks[1]} дн.")
    lines.append("Задания дня, арена, мафия и ставки — в приложении.")
    return "\n".join(lines)


async def register(chat_id: int, message_id: int) -> None:
    await db.kv_set(f"live:{chat_id}", str(message_id))
    await db.kv_set(f"live_hash:{chat_id}", "")
    await db.kv_set(f"live_ts:{chat_id}", "0")


async def refresh(bot: Bot, chat_id: int, force: bool = False) -> None:
    raw = await db.kv_get(f"live:{chat_id}")
    if not raw:
        return
    last = float(await db.kv_get(f"live_ts:{chat_id}") or 0)
    if not force and time.time() - last < LIVE_EVERY:
        return
    await db.kv_set(f"live_ts:{chat_id}", str(time.time()))
    text = await live_text(chat_id)
    digest = hashlib.md5(text.encode()).hexdigest()
    if not force and digest == await db.kv_get(f"live_hash:{chat_id}"):
        return
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=int(raw), reply_markup=live_kb(chat_id))
        await db.kv_set(f"live_hash:{chat_id}", digest)
    except TelegramAPIError as exc:
        if "not modified" in str(exc):
            await db.kv_set(f"live_hash:{chat_id}", digest)
        elif "not found" in str(exc) or "can't be edited" in str(exc):
            await db.kv_set(f"live:{chat_id}", "")  # сообщение удалили: закреп больше не обновляем
        else:
            log.warning("Закреп чата %s не обновился: %s", chat_id, exc)


async def boss_pings(bot: Bot, chat_id: int) -> None:
    boss, _ = await events.ensure_boss(chat_id)
    if not boss or boss["status"] != "alive":
        return
    pct = 100 * boss["hp"] / boss["max_hp"]
    left = boss["ends"] - time.time()
    for key, cond, text in (
        (f"bping30:{boss['id']}", pct <= 30, f"{boss['emoji']} <b>{esc(boss['name'])}</b> почти пал: осталось {boss['hp']} HP! Добивайте, награда всем."),
        (f"bping6h:{boss['id']}", left <= 6 * 3600 and pct > 30, f"⏰ {boss['emoji']} <b>{esc(boss['name'])}</b> сбежит через {_hours(int(left))}, а у него ещё {boss['hp']} HP. Всем по удару!"),
    ):
        if cond and not await db.kv_get(key):
            await db.kv_set(key, "1")
            try:
                await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[app_link_button("⚔️ Ударить", "rp", chat_id)]]))
            except TelegramAPIError as exc:
                log.warning("Пинг босса не отправлен: %s", exc)


async def overtakes(bot: Bot, chat_id: int) -> None:
    """Если игрока обогнали в топ-5 сезона — один раз в день пишем ему в личку."""
    now = now_local()
    board = await events.season_board(chat_id, now.year, now.month, limit=5)
    order = [r["user_id"] for r in board]
    names = {r["user_id"]: r["username"] for r in board}
    try:
        prev = json.loads(await db.kv_get(f"season_order:{chat_id}") or "[]")
    except ValueError:
        prev = []
    await db.kv_set(f"season_order:{chat_id}", json.dumps(order))
    for i, uid in enumerate(prev):
        if uid not in order or order.index(uid) <= i:
            continue
        passer = next((p for p in order[: order.index(uid)] if p not in prev[: i + 1]), None)
        key = f"overtake:{day_str()}:{uid}"
        if passer is None or await db.kv_get(key) or await db.kv_get(f"remind_off:{uid}"):
            continue
        await db.kv_set(key, "1")
        try:
            await bot.send_message(
                uid,
                f"📉 В сезоне тебя обогнал <b>{esc(names[passer])}</b>, теперь ты на {order.index(uid) + 1}-м месте. Отыграешься?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[app_link_button("🏆 К рейтингу", "rp", chat_id)]]),
            )
        except TelegramAPIError:
            pass  # в личку бот писать не может, пока человек его не запустил


async def tick(bot: Bot, chats) -> None:
    for chat_id in chats:
        try:
            await refresh(bot, chat_id)
            await boss_pings(bot, chat_id)
            await overtakes(bot, chat_id)
        except Exception:
            log.exception("Ошибка живого табло чата %s", chat_id)
