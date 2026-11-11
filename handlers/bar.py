import asyncio
import logging
import random
import time
from typing import Dict

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import bar
from database import ensure_user, kv_get, kv_set
from keyboards import app_link_button
from rooms import GameError
from utils import display_name, esc

log = logging.getLogger(__name__)
router = Router()

MODES = {"reply": "ответом на сообщение", "replace": "с заменой сообщения (нужно право удалять)", "off": "выключены"}
_last_slur: Dict[int, float] = {}
_last_out: Dict[int, float] = {}


def _kb() -> InlineKeyboardMarkup:
    rows = []
    for title, items in (("🍸", bar.DRINKS), ("🍽", bar.SNACKS)):
        rows.append([InlineKeyboardButton(text=f"{ic} {t} · {pr} см", callback_data=f"bar:{i}") for i, ic, t, pr, *_ in items[:3]])
        rows.append([InlineKeyboardButton(text=f"{ic} {t} · {pr} см", callback_data=f"bar:{i}") for i, ic, t, pr, *_ in items[3:]])
    rows.append([InlineKeyboardButton(text=f"{ic} {t} · {pr} см", callback_data=f"bar:{i}") for i, ic, t, pr in bar.CURES])
    rows.append([app_link_button("📱 Бар в мини-аппе", "rp")])
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def _status_line(p: dict) -> str:
    s = bar.public(p)
    extra = f", ещё {max(1, s['left'] // 60)} мин" if s["left"] else ""
    return f"{s['icon']} Твой статус: <b>{s['name']}</b>{extra} (градусов: {s['level']})"


@router.message(Command("bar", "pub"))
async def cmd_bar(message: Message) -> None:
    if message.chat.type == "private":
        await message.reply("Бар работает в групповом чате.")
        return
    u = message.from_user
    await ensure_user(u.id, message.chat.id, display_name(u))
    p = await bar.get(message.chat.id, u.id)
    await message.reply(
        "🍻 <b>Бар</b>: напитки и закуски за см.\n"
        "Выпил — речь заплетается, перебрал — уснул, утром похмелье. Закуска снижает градус, рассол лечит.\n\n"
        + _status_line(p),
        reply_markup=_kb(),
    )


@router.callback_query(F.data.startswith("bar:"))
async def cb_bar(callback: CallbackQuery) -> None:
    if not callback.message or not callback.data:
        await callback.answer()
        return
    u = callback.from_user
    chat_id = callback.message.chat.id
    await ensure_user(u.id, chat_id, display_name(u))
    try:
        res = await bar.buy(chat_id, u.id, display_name(u), callback.data.split(":", 1)[1])
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(res["text"][:190], show_alert=True)
    b = res["bar"]
    if b["key"] in ("out", "wasted"):
        await callback.message.answer(f"{b['icon']} <b>{esc(display_name(u))}</b>: {esc(b['name'].lower())}!")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    p = await bar.get(message.chat.id, message.from_user.id)
    await message.reply(_status_line(p))


async def _can_delete(bot: Bot, chat_id: int) -> bool:
    try:
        me = await bot.get_chat_member(chat_id, (await bot.me()).id)
        return bool(getattr(me, "can_delete_messages", False))
    except TelegramAPIError:
        return False


@router.message(Command("drunkmode"))
async def cmd_drunkmode(message: Message, command: CommandObject, bot: Bot) -> None:
    """Админ выбирает, как бот показывает пьяную речь: ответом, с заменой сообщения или выключить."""
    if message.chat.type == "private":
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        await message.reply("Настраивать могут только админы.")
        return
    arg = (command.args or "").strip().lower()
    if arg not in MODES:
        cur = await kv_get(f"drunk_mode:{message.chat.id}") or "replace"
        await message.reply(
            f"🥴 Пьяная речь сейчас: <b>{MODES[cur]}</b>.\nМеняется так: <code>/drunkmode reply</code>, "
            f"<code>/drunkmode replace</code> или <code>/drunkmode off</code>."
        )
        return
    if arg == "replace" and not await _can_delete(bot, message.chat.id):
        await message.reply("Для замены мне нужно право удалять сообщения. Дай его в настройках админов и повтори.")
        return
    await kv_set(f"drunk_mode:{message.chat.id}", arg)
    await message.reply(f"Готово: пьяная речь {MODES[arg]}.")


async def drunk_effect(message: Message, bot: Bot) -> None:
    """Если автор пьян, его сообщение искажается: по умолчанию бот удаляет оригинал и пишет вместо него пьяную версию."""
    user = message.from_user
    text = message.text or ""
    chat_id = message.chat.id
    mode = await kv_get(f"drunk_mode:{chat_id}") or "replace"
    if mode == "off":
        return
    p = await bar.get(chat_id, user.id)
    level = bar.effect_level(p)
    if level == 0:
        return
    now = time.time()
    name = esc(display_name(user))
    replace = mode == "replace" and await _can_delete(bot, chat_id)
    if level == 4:  # спит
        if replace:
            try:
                await message.delete()
                await bot.send_message(chat_id, f"💤 <b>{name}:</b> ззз… (спит)", reply_to_message_id=_reply_id(message))
            except TelegramAPIError:
                pass
        elif now - _last_out.get(user.id, 0) > 300:
            _last_out[user.id] = now
            await message.reply(random.choice([
                f"💤 {name} лежит лицом в салате и что-то бормочет во сне.",
                f"💤 {name} уснул прямо за столом. Не будить.",
            ]))
        return
    if not replace:  # режим «ответом»: не заваливаем чат, отвечаем не на каждое сообщение
        if now - _last_slur.get(user.id, 0) < 15 or not 4 <= len(text) <= 250:
            return
        if random.random() > {1: 0.3, 2: 0.65, 3: 1.0}[level]:
            return
        _last_slur[user.id] = now
    slurred = None
    if level == 3 and len(text) <= 250 and random.random() < 0.5:
        slurred = await bar.ai_drunkify(text)
    slurred = slurred or bar.drunkify(text, level)
    icon = {1: "😄", 2: "🥴", 3: "🤪"}[level]
    line = f"{icon} <b>{name}:</b> {esc(slurred)}"
    if replace:
        try:
            await message.delete()
            await bot.send_message(chat_id, line, reply_to_message_id=_reply_id(message))
            return
        except TelegramAPIError:
            pass  # не получилось удалить: отвечаем ответом
    await message.reply(line)


def _reply_id(message: Message):
    """Если пьяное сообщение было ответом на чужое, пересланная версия тоже отвечает на него."""
    return message.reply_to_message.message_id if message.reply_to_message else None
