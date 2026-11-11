"""Фоновые задачи бота: недельный дайджест, вопрос дня, итоги вопроса и розыгрыш лотереи."""
import asyncio
import logging
import random

import aiosqlite

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, WebAppInfo, InlineKeyboardButton

import chat_events
import events
import live
import rp
import trivia_bank
from config import DB_PATH, WEBAPP_URL
from keyboards import app_link_button
from database import (
    get_answers,
    get_chats,
    get_daily_q,
    kv_get,
    kv_set,
    lottery_due,
    mark_q_summarized,
    open_questions,
    set_daily_q,
    total_messages,
)
from digest import build_digest
from handlers.chatai import refresh_trends
from handlers.daily import ANSWER_REWARD, QUESTIONS, questions
from handlers.disputes import expire as expire_disputes
from handlers.lottery import DRAW_HOUR, draw
from utils import day_str, esc, now_local

log = logging.getLogger(__name__)

DIGEST_WEEKDAY = 6   # воскресенье
DIGEST_HOUR = 20
QUESTION_HOUR = 11   # вопрос дня публикуется с 11:00 до 20:00
QUESTION_UNTIL = 20
SUMMARY_HOUR = DRAW_HOUR


async def _digest(bot: Bot, chats) -> None:
    now = now_local()
    if now.weekday() != DIGEST_WEEKDAY or now.hour < DIGEST_HOUR:
        return
    for chat_id in chats:
        key = f"digest:{now.date().isoformat()}:{chat_id}"
        if await kv_get(key):
            continue
        await kv_set(key, "1")
        text = await build_digest(chat_id)
        if text:
            await _send(bot, chat_id, text)


async def _send(bot: Bot, chat_id: int, text: str):
    try:
        return await bot.send_message(chat_id, text)
    except TelegramAPIError as exc:
        log.warning("Не удалось написать в чат %s: %s", chat_id, exc)
        return None


async def _post_question(bot: Bot, chat_id: int) -> None:
    today = day_str()
    if await get_daily_q(chat_id, today):
        return
    last = await kv_get(f"q_last:{chat_id}")
    pool = [q for q in QUESTIONS if q != last] or QUESTIONS
    question = random.choice(pool)
    sent = await _send(
        bot,
        chat_id,
        f"❓ <b>Вопрос дня</b>\n\n{question}\n\n"
        f"<i>Ответь на это сообщение — получишь +{ANSWER_REWARD} см. В {SUMMARY_HOUR}:00 подведу итоги.</i>",
    )
    if sent:
        await set_daily_q(chat_id, today, sent.message_id, question)
        await kv_set(f"q_last:{chat_id}", question)
        questions[(chat_id, sent.message_id)] = today


async def _summarize_questions(bot: Bot) -> None:
    now = now_local()
    for chat_id, message_id, day in await open_questions(day_str(-2)):
        if day == day_str() and now.hour < SUMMARY_HOUR:
            continue
        await mark_q_summarized(chat_id, day)
        questions.pop((chat_id, message_id), None)
        answers = await get_answers(chat_id, day)
        if not answers:
            await _send(bot, chat_id, "❓ <b>Итоги вопроса дня:</b> ответов ноль. Чат сегодня молчит как рыба.")
            continue
        name, text = random.choice(answers)
        await _send(
            bot,
            chat_id,
            f"❓ <b>Итоги вопроса дня:</b> ответили {len(answers)} чел. (каждому по +{ANSWER_REWARD} см)\n\n"
            f"🏅 <b>Случайный ответ дня</b> от {esc(name)}:\n<i>«{esc(text)}»</i>",
        )


async def _lotteries(bot: Bot) -> None:
    now = now_local()
    max_day = day_str() if now.hour >= DRAW_HOUR else day_str(-1)
    for chat_id, day in await lottery_due(max_day):
        try:
            await draw(bot, chat_id, day)
        except TelegramAPIError as exc:
            log.warning("Розыгрыш в чате %s не отправлен: %s", chat_id, exc)


async def _bosses(bot: Bot, chats) -> None:
    """Босс появляется сам в живых чатах (днём), сбежавший босс сменяется новым."""
    now = now_local()
    if now.hour < 12:
        return
    for chat_id in chats:
        if await total_messages(chat_id, day_str(-6)) == 0:
            continue
        boss, event = await events.ensure_boss(chat_id)
        if event and "spawn" in event and boss:
            try:
                await bot.send_message(
                    chat_id, events.spawn_text({**boss, "name": esc(boss["name"])}),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[app_link_button("👹 К боссу", "rp", chat_id)]]),
                )
            except TelegramAPIError as exc:
                log.warning("Босс не объявлен в чате %s: %s", chat_id, exc)


async def _seasons(bot: Bot, chats) -> None:
    """Первого числа подводит итоги прошлого месяца: призы топ-3 и титул чемпиона."""
    now = now_local()
    if now.hour < 10:
        return
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    for chat_id in chats:
        board = await events.finish_season(chat_id, year, month)
        if not board:
            continue
        medals = ["🥇", "🥈", "🥉"]
        lines = "\n".join(
            f"{medals[i]} {esc(r['username'])} — {r['score']:+d} см (приз +{events.SEASON_PRIZES[i]} см)"
            for i, r in enumerate(board)
        )
        await _send(
            bot, chat_id,
            f"🏁 <b>Итоги сезона: {events.MONTHS[month - 1]}</b>\n{lines}\n\n"
            f"Чемпион получает титул на 30 дней. Новый сезон уже идёт, рейтинг сезона — в мини-аппе!",
        )


async def _rp_news(bot: Bot) -> None:
    """Вечером выходит газета «Вечерний Район» (раз в день на чат, если что-то происходило)."""
    now = now_local()
    if now.hour < 19 or now.hour >= 23:
        return
    import paper
    for chat_id in await rp.active_chats():
        key = f"rp_news:{day_str()}:{chat_id}"
        if await kv_get(key):
            continue
        text = await paper.build(chat_id)
        if not text:
            continue
        await kv_set(key, "1")
        await _send(bot, chat_id, text)


REMIND_HOUR = 19
REMIND_UNTIL = 23


async def _reminders(bot: Bot) -> None:
    """Личное напоминание тем, у кого горит серия бонуса (можно отключить командой /remind)."""
    now = now_local()
    if not REMIND_HOUR <= now.hour < REMIND_UNTIL:
        return
    today = day_str()
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT user_id, MAX(streak) FROM users WHERE chat_id < 0 AND streak >= 2 AND last_daily = ? GROUP BY user_id",
            (day_str(-1),),
        )
        rows = await cur.fetchall()
    for user_id, streak in rows:
        key = f"remind:{today}:{user_id}"
        if await kv_get(key) or await kv_get(f"remind_off:{user_id}"):
            continue
        await kv_set(key, "1")
        try:
            await bot.send_message(
                user_id,
                f"🔥 Серия бонуса: <b>{streak}</b> дн. Заберёшь сегодня — серия продолжится, нет — сгорит.\n"
                f"<i>Отключить напоминания: /remind</i>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🎁 Забрать бонус", web_app=WebAppInfo(url=WEBAPP_URL + "?tab=casino"))
                ]]),
            )
        except TelegramAPIError:
            pass  # человек не запускал бота в личке или заблокировал его


async def run_jobs(bot: Bot) -> None:
    now = now_local()
    chats = await get_chats()
    await _digest(bot, chats)
    await refresh_trends()
    if QUESTION_HOUR <= now.hour < QUESTION_UNTIL:
        for chat_id in chats:
            # вопрос только в живые чаты: хоть одно сообщение за последнюю неделю
            if await total_messages(chat_id, day_str(-6)) > 0:
                await _post_question(bot, chat_id)
    await _summarize_questions(bot)
    await _lotteries(bot)
    await _bosses(bot, chats)
    await live.tick(bot, chats)
    await _rp_news(bot)
    asyncio.create_task(trivia_bank.top_up_daily())
    await _seasons(bot, chats)
    await _reminders(bot)
    await chat_events.tick(bot, chats)
    await expire_disputes(bot)
    import bankraid, city, gangwar, liveevents, missions
    for step in (gangwar.tick(), city.flush_outbox(bot), bankraid.tick(bot, chats), missions.tick(bot, chats), liveevents.tick(bot, chats), __import__("guide").tick(bot)):
        try:
            await step
        except Exception:
            log.exception("Сбой новой фоновой задачи")


async def jobs_loop(bot: Bot) -> None:
    """Раз в минуту проверяет, не пора ли что-то выполнить; пропущенное при простое наверстывает."""
    while True:
        try:
            await run_jobs(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в фоновых задачах")
        await asyncio.sleep(60)
