import asyncio
import html as _html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from database import (
    get_chats,
    get_top_titans,
    hero_leader,
    kv_get,
    kv_set,
    top_rep,
    top_talkers,
    total_messages,
)
import events
from utils import day_str, esc, now_local

log = logging.getLogger(__name__)

DIGEST_WEEKDAY = 6  # воскресенье
DIGEST_HOUR = 20


async def build_digest(chat_id: int) -> str | None:
    since = day_str(-6)  # последние 7 дней включая сегодня
    total = await total_messages(chat_id, since)
    if total == 0:
        return None

    lines = ["📰 <b>Итоги недели</b>\n", f"💬 Сообщений в чате: <b>{total}</b>"]

    talkers = await top_talkers(chat_id, since, 3)
    if talkers:
        medals = ["🥇", "🥈", "🥉"]
        lines.append("\n🗣 <b>Главные болтуны:</b>")
        for medal, (name, cnt) in zip(medals, talkers):
            lines.append(f"{medal} {esc(name)} — {cnt}")

    titans = await get_top_titans(chat_id, 1)
    if titans:
        lines.append(f"\n📏 <b>Самый большой:</b> {esc(titans[0][0])} — {titans[0][1]} см")

    rep = await top_rep(chat_id)
    if rep:
        lines.append(f"✨ <b>Любимчик чата:</b> {esc(rep[0])} (карма {rep[1]})")

    hero = await hero_leader(chat_id, since)
    if hero:
        lines.append(f"🏅 <b>Чаще всех был героем дня:</b> {esc(hero[0])} ({hero[1]} раз)")

    recap = await _ai_recap(chat_id, lines)
    if recap:
        lines.append(f"\n🤖 <b>Пересказ недели:</b>\n{esc(recap)}")
    text = "\n".join(lines)
    plain = _html.unescape(re.sub(r"<[^>]+>", "", text))
    await kv_set(f"digest_text:{chat_id}", plain)
    await kv_set(f"digest_ts:{chat_id}", str(int(now_local().timestamp())))
    return text


async def _ai_recap(chat_id: int, lines: list) -> str | None:
    """Короткий живой пересказ недели по фактам (если ИИ недоступен, дайджест выйдет без него)."""
    from handlers.chatai import _complete_gemini, ai_enabled, split_gif, system_prompt
    if not ai_enabled():
        return None
    facts = await events.week_facts(chat_id)
    stats = "\n".join(_html.unescape(re.sub(r"<[^>]+>", "", l)) for l in lines)
    prompt = (
        "Напиши короткий весёлый пересказ недели нашего чата друзей: 3–5 коротких строк, живой речью, без списков "
        "и markdown. Используй ТОЛЬКО факты ниже, ничего не выдумывай, подколы мягкие и дружеские.\n\n"
        f"Статистика:\n{stats}\n\nСобытия:\n{facts or '- ничего особенного'}"
    )
    try:
        text = await _complete_gemini(system_prompt(), prompt, max_tokens=400)
        return split_gif(text)[0] if text else None
    except Exception as exc:
        log.warning("ИИ-пересказ недели не получился: %r", exc)
        return None
