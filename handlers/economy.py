import asyncio
import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, User

from database import ensure_user, add_size, claim_daily, buy_title
from keyboards import quick_kb
from webdb import gamble_room, win_factor, GAMBLE_DAILY_CAP
from prompts import ask
from utils import display_name, esc, day_str

router = Router()

MAX_BET = 10

# Защита от двойного запуска ставки одним игроком, пока крутится барабан
_casino_busy: set = set()


async def daily_text(user: User, chat_id: int) -> str:
    name = display_name(user)
    ok, streak, bonus, size = await claim_daily(user.id, chat_id, name, day_str(), day_str(-1))
    if not ok:
        return f"⏳ Бонус за сегодня уже забран. Серия: <b>{streak} дн.</b>\nПриходи завтра!"
    return (
        f"🎁 <b>Ежедневный бонус!</b>\n"
        f"<b>{esc(name)}</b> получает <b>+{bonus} см</b>\n"
        f"🔥 Серия: <b>{streak} дн.</b> (чем длиннее серия, тем жирнее бонус)\n"
        f"📏 Теперь: <b>{size} см</b>"
    )


@router.message(Command("daily"))
async def cmd_daily(message: Message) -> None:
    if not message.from_user:
        return
    await message.reply(
        await daily_text(message.from_user, message.chat.id), reply_markup=quick_kb("dick", "top")
    )


def _slot_result(value: int) -> str:
    """Значение дайса-автомата (1..64) → 'jackpot' | 'triple' | 'pair' | 'none'."""
    v = value - 1
    a, b, c = v % 4, (v // 4) % 4, v // 16
    if value == 64:
        return "jackpot"
    if a == b == c:
        return "triple"
    if a == b or b == c or a == c:
        return "pair"
    return "none"


@router.message(Command("casino", "slots"))
async def cmd_casino(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    user = message.from_user
    chat_id = message.chat.id
    name = display_name(user)

    size, _, _, _ = await ensure_user(user.id, chat_id, name)

    if not (command.args or "").strip():
        await ask(message, "casino", f"🎰 Сколько сантиметров ставишь? Ответь числом от 1 до {MAX_BET}.", "Ставка, например 3")
        return

    try:
        bet = int((command.args or "").split()[0])
    except (ValueError, IndexError):
        await message.reply(f"🎰 Ставка: <code>/casino 3</code> (от 1 до {MAX_BET} см).")
        return

    if bet < 1 or bet > MAX_BET:
        await message.reply(f"🎰 Ставка должна быть от 1 до {MAX_BET} см.")
        return
    if size < bet:
        await message.reply(f"💸 Не хватает сантиметров: у тебя {size} см.")
        return
    if user.id in _casino_busy:
        return
    room = await gamble_room(user.id, chat_id)
    if room <= 0:
        await message.reply(f"🎰 Дневной лимит выигрыша ({GAMBLE_DAILY_CAP} см) исчерпан — казино закрыто, приходи позже.")
        return

    _casino_busy.add(user.id)
    try:
        dice = await message.reply_dice(emoji="🎰")
        await asyncio.sleep(3)
        result = _slot_result(dice.dice.value)

        if result == "jackpot":
            delta, text = bet * 5, "🎉 <b>ДЖЕКПОТ! Три семёрки!</b>"
        elif result == "triple":
            delta, text = bet * 3, "💰 <b>Три в ряд!</b>"
        elif result == "pair":
            delta, text = 0, "🤝 Два одинаковых — ставка возвращена."
        else:
            delta, text = -bet, "💀 Не повезло, казино забирает ставку."

        if delta > 0:
            delta = max(1, round(delta * win_factor(size)))  # богатым платят меньше, бедным больше
        delta = min(delta, room)
        new_size = await add_size(user.id, chat_id, name, delta, 'casino') if delta else size
        sign = f"+{delta}" if delta > 0 else str(delta)
        await message.reply(
            f"{text}\n<b>{esc(name)}</b>: <b>{sign} см</b> → <b>{new_size} см</b>"
        )
    finally:
        _casino_busy.discard(user.id)
