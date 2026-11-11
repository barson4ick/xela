import time
from typing import Dict, Tuple

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.filters import Command, CommandObject

from config import WEBAPP_URL
from handlers.anatomy import dick_text, top_text
from handlers.economy import daily_text
from handlers.menu import cmd_menu
from keyboards import app_link_button

router = Router()

_last: Dict[Tuple[int, str], float] = {}


@router.callback_query(F.data.startswith("act:"))
async def handle_quick(callback: CallbackQuery) -> None:
    """Быстрые кнопки под ответами: те же команды, но в один тап."""
    if not callback.data or not callback.message:
        await callback.answer()
        return
    action = callback.data.split(":", 1)[1]
    now = time.monotonic()
    key = (callback.from_user.id, action)
    if now - _last.get(key, 0) < 3:
        await callback.answer("Не так быстро!")
        return
    _last[key] = now

    chat_id = callback.message.chat.id
    if action == "top":
        text = await top_text(chat_id)
    elif action == "daily":
        text = await daily_text(callback.from_user, chat_id)
    elif action == "dick":
        text = await dick_text(callback.from_user, chat_id)
    else:
        await callback.answer()
        return
    await callback.answer()
    await callback.message.answer(text)


def _open_app_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📱 Открыть мини-апп", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )


@router.message(F.text == "📋 Меню")
async def handle_menu_button(message: Message) -> None:
    await cmd_menu(message)


@router.message(Command("app", "start"))
async def cmd_app(message: Message, command: CommandObject) -> None:
    if message.chat.type == "private":
        await message.answer(
            "👋 Привет! Открой мини-апп кнопкой ниже. Постоянной нижней кнопки больше нет.",
            reply_markup=_open_app_kb(),
        )
        return
    await message.reply(
        "📱 Жми — мини-апп откроется прямо поверх чата:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[app_link_button(chat_id=message.chat.id)]]),
    )


@router.message(Command("pinapp"))
async def cmd_pinapp(message: Message, bot: Bot) -> None:
    """Админ закрепляет в чате сообщение с кнопкой мини-аппа — вход всегда под рукой."""
    if message.chat.type == "private":
        await message.reply("Эта команда для группы.")
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        await message.reply("Закрепить кнопку могут только админы.")
        return
    sent = await message.answer(
        "📱 <b>Район</b>\n\nЖми кнопку — мини-апп откроется прямо поверх чата. Это закреплённая кнопка, она всегда под рукой.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[app_link_button("📱 Открыть Район", "rp", message.chat.id)]]),
    )
    try:
        await bot.pin_chat_message(message.chat.id, sent.message_id, disable_notification=True)
    except TelegramAPIError:
        await message.answer("Не смог закрепить: дай мне право закреплять сообщения.")



@router.message(Command("kbdoff"))
async def cmd_kbdoff(message: Message, bot: Bot) -> None:
    if message.chat.type != "private":
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.reply("Кнопку убирают только админы.")
            return
    from aiogram.types import ReplyKeyboardRemove
    await message.answer("Кнопка внизу чата убрана.", reply_markup=ReplyKeyboardRemove())
