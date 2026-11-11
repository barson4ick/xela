import random
import time
from typing import Dict

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import add_size, buy_ticket, ensure_user, lottery_state, lottery_take
from utils import day_str, display_name, esc, now_local

router = Router()

TICKET_COST = 1       # см за билет
MAX_TICKETS = 5       # билетов на игрока за розыгрыш
HOUSE_BONUS = 2       # бонус к банку от казино, если играют минимум двое
DRAW_HOUR = 21        # розыгрыш каждый день в 21:00 по часовому поясу чата

_last_buy: Dict[int, float] = {}


def draw_day() -> str:
    """Билеты, купленные до 21:00, участвуют в сегодняшнем розыгрыше, после — в завтрашнем."""
    return day_str() if now_local().hour < DRAW_HOUR else day_str(1)


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"🎟 Купить билет ({TICKET_COST} см)", callback_data="lot:buy")]]
    )


async def lottery_text(chat_id: int, user_id: int) -> str:
    mine, total, players = await lottery_state(chat_id, draw_day(), user_id)
    when = "сегодня" if draw_day() == day_str() else "завтра"
    bonus = f" + {HOUSE_BONUS} от казино" if players >= 2 else ""
    return (
        f"🎟 <b>Лотерея</b> — розыгрыш {when} в {DRAW_HOUR}:00\n"
        f"Банк: <b>{total * TICKET_COST} см</b>{bonus} · билетов: {total} · участников: {players}\n"
        f"Твои билеты: <b>{mine}/{MAX_TICKETS}</b>. Чем больше билетов — тем выше шанс.\n"
        f"<i>Один участник — деньги возвращаются.</i>"
    )


@router.message(Command("lottery"))
async def cmd_lottery(message: Message) -> None:
    if not message.from_user:
        return
    await ensure_user(message.from_user.id, message.chat.id, display_name(message.from_user))
    await message.reply(await lottery_text(message.chat.id, message.from_user.id), reply_markup=_kb())


@router.callback_query(F.data == "lot:buy")
async def handle_buy(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    user = callback.from_user
    now = time.monotonic()
    if now - _last_buy.get(user.id, 0) < 1:
        await callback.answer()
        return
    _last_buy[user.id] = now

    chat_id = callback.message.chat.id
    await ensure_user(user.id, chat_id, display_name(user))
    status, have = await buy_ticket(user.id, chat_id, draw_day(), TICKET_COST, MAX_TICKETS)
    if status == "poor":
        await callback.answer("Не хватает сантиметров 💸", show_alert=True)
        return
    if status == "cap":
        await callback.answer(f"Лимит: {MAX_TICKETS} билетов на человека", show_alert=True)
        return
    await callback.answer(f"🎟 Билет куплен! У тебя: {have}")
    try:
        await callback.message.edit_text(await lottery_text(chat_id, user.id), reply_markup=_kb())
    except TelegramAPIError:
        pass


async def draw(bot, chat_id: int, day: str) -> None:
    """Розыгрыш за указанный день: победитель забирает банк, одинокий участник получает возврат."""
    entries = await lottery_take(chat_id, day)
    if not entries:
        return
    if len(entries) < 2:
        user_id, name, tickets = entries[0]
        await add_size(user_id, chat_id, name or str(user_id), tickets * TICKET_COST, "lottery_refund")
        await bot.send_message(
            chat_id,
            f"🎟 <b>Лотерея не состоялась:</b> {esc(name)} был один. Билеты ({tickets * TICKET_COST} см) возвращены.",
        )
        return

    pot = sum(t for _, _, t in entries) * TICKET_COST + HOUSE_BONUS
    winner_id, winner_name, winner_tickets = random.choices(entries, weights=[t for _, _, t in entries])[0]
    new_size = await add_size(winner_id, chat_id, winner_name or str(winner_id), pot, "lottery")
    others = ", ".join(f"{esc(n)} ({t})" for u, n, t in entries if u != winner_id)
    await bot.send_message(
        chat_id,
        f"🎉 <b>Лотерея!</b> Банк: <b>{pot} см</b>\n"
        f"Победитель — <b>{esc(winner_name)}</b> (билетов: {winner_tickets}) и забирает всё!\n"
        f"📏 Теперь у него {new_size} см.\n"
        f"Остальные участники: {others}. Завтра ещё один розыгрыш — /lottery",
    )
