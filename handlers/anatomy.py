import html
import random
import time
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from config import DICK_COOLDOWN, ROB_COOLDOWN
from keyboards import quick_kb
import webdb
from database import (
    ensure_user,
    update_dick_roll,
    transfer_size,
    get_top_titans,
    get_black_holes,
)

router = Router()


def format_display_name(user: User) -> str:
    """Формирует отображаемое имя или @username пользователя."""
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def format_cooldown(seconds_left: int) -> str:
    """Форматирует остаток кулдауна в вид: Xч Yм Zс."""
    hours = seconds_left // 3600
    minutes = (seconds_left % 3600) // 60
    seconds = seconds_left % 60
    return f"{hours}ч {minutes}м {seconds}с"


async def dick_text(user: User, chat_id: int) -> str:
    """Замер размера: возвращает готовый текст ответа (используется командой и кнопкой)."""
    user_id = user.id
    username = format_display_name(user)
    now_ts = int(time.time())

    size, _, last_roll, _ = await ensure_user(user_id, chat_id, username)

    elapsed = now_ts - last_roll
    if elapsed < DICK_COOLDOWN:
        remaining = DICK_COOLDOWN - elapsed
        return f"⏳ Следующий замер через: <b>{format_cooldown(remaining)}</b>"

    # Генерация значений: 1% -> +25, 2% -> -15, 97% -> от -5 до +10
    roll_chance = random.randint(1, 100)
    if roll_chance == 1:
        delta = 25
        flavor = "🎰 <b>КРИТИЧЕСКИЙ УСПЕХ (ДЖЕКПОТ)!</b>"
    elif roll_chance in (2, 3):
        delta = -15
        flavor = "🥶 <b>КРИТИЧЕСКИЙ ПРОВАЛ (ЗАМОРОЗКА)!</b>"
    else:
        delta = random.randint(-5, 10)
        if delta > 0:
            flavor = "📈 Растём!"
        elif delta < 0:
            flavor = "📉 На улице холодно..."
        else:
            flavor = "⚖️ Без изменений."

    new_size = await update_dick_roll(user_id, chat_id, username, delta, now_ts)
    sign = f"+{delta}" if delta > 0 else str(delta)
    safe_name = html.escape(username)

    return (
        f"{flavor}\n"
        f"Игрок: <b>{safe_name}</b>\n"
        f"Изменение: <b>{sign} см</b>\n"
        f"Текущий размер: <b>{new_size} см</b>"
    )


@router.message(Command("dick"))
async def cmd_dick(message: Message) -> None:
    if not message.from_user:
        return
    text = await dick_text(message.from_user, message.chat.id)
    await message.reply(text, reply_markup=quick_kb("top", "daily"))


async def top_text(chat_id: int) -> str:
    titans = await get_top_titans(chat_id, limit=5)
    black_holes = await get_black_holes(chat_id, limit=3)

    if not titans:
        return "📊 В этом чате ещё никто не делал замер. Начните с команды /dick!"

    lines = ["🏆 <b>Титаны чата (Топ-5):</b>"]
    for idx, (uname, size, rep, title) in enumerate(titans, start=1):
        safe_uname = html.escape(uname)
        badge = f" <i>{html.escape(title)}</i>" if title else ""
        lines.append(f"{idx}. <b>{safe_uname}</b>{badge} — <b>{size} см</b> (Репутация: {rep})")

    if black_holes:
        lines.append("\n🕳 <b>Чёрная дыра (Анти-топ):</b>")
        for idx, (uname, size, rep) in enumerate(black_holes, start=1):
            safe_uname = html.escape(uname)
            lines.append(f"{idx}. <b>{safe_uname}</b> — <b>{size} см</b> (Репутация: {rep})")

    return "\n".join(lines)


@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    await message.reply(await top_text(message.chat.id), reply_markup=quick_kb("dick", "daily"))


async def rob_text(
    thief_id: int, thief_name: str, victim_id: int, victim_name: str, chat_id: int
) -> tuple:
    """Ограбление. Возвращает (текст, жертва_потеряла_сантиметры)."""
    now_ts = int(time.time())

    _, _, _, last_rob = await ensure_user(thief_id, chat_id, thief_name)
    victim_size, _, _, _ = await ensure_user(victim_id, chat_id, victim_name)

    elapsed = now_ts - last_rob
    if elapsed < ROB_COOLDOWN:
        remaining = ROB_COOLDOWN - elapsed
        return (
            f"🚓 Вы ещё залечиваете раны после прошлого дела!\n"
            f"Следующее ограбление через: <b>{format_cooldown(remaining)}</b>",
            False,
        )

    if victim_size <= 0:
        return "🛡 С него нечего взять (размер цели ≤ 0 см)!", False

    safe_thief = html.escape(thief_name)
    safe_victim = html.escape(victim_name)

    # 40% успех, 60% провал
    if random.randint(1, 100) <= 40:
        max_steal = min(4, victim_size)
        stolen = random.randint(1, max_steal)
        new_thief_size, new_victim_size = await transfer_size(
            user1_id=thief_id,
            user2_id=victim_id,
            chat_id=chat_id,
            delta1=stolen,
            delta2=-stolen,
            rob_ts_for_user1=now_ts,
            reason="rob",
        )
        return (
            f"🥷 <b>Успешное ограбление!</b>\n"
            f"<b>{safe_thief}</b> незаметно отпилил <b>{stolen} см</b> у <b>{safe_victim}</b>!\n\n"
            f"📏 <b>{safe_thief}</b>: {new_thief_size} см\n"
            f"📏 <b>{safe_victim}</b>: {new_victim_size} см",
            True,
        )

    penalty = 3
    new_thief_size, new_victim_size = await transfer_size(
        user1_id=thief_id,
        user2_id=victim_id,
        chat_id=chat_id,
        delta1=-penalty,
        delta2=penalty,
        rob_ts_for_user1=now_ts,
        reason="rob_fail",
    )
    return (
        f"🚨 <b>Провал! Вор пойман с поличным!</b>\n"
        f"<b>{safe_thief}</b> пытался обокрасть <b>{safe_victim}</b>, но попался.\n"
        f"Штраф <b>{penalty} см</b> передан жертве в качестве компенсации!\n\n"
        f"📏 <b>{safe_thief}</b>: {new_thief_size} см\n"
        f"📏 <b>{safe_victim}</b>: {new_victim_size} см",
        False,
    )


@router.message(Command("rob"))
async def cmd_rob(message: Message) -> None:
    if not message.from_user:
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Команда /rob вызывается только в ответ (reply) на сообщение жертвы!")
        return

    thief = message.from_user
    victim = message.reply_to_message.from_user

    if thief.id == victim.id:
        await message.reply("🤡 Нельзя ограбить самого себя!")
        return

    if victim.is_bot:
        await message.reply("🤖 Ботов грабить бесполезно — у них только микросхемы!")
        return

    text, stolen = await rob_text(
        thief.id, format_display_name(thief), victim.id, format_display_name(victim), message.chat.id
    )
    markup = None
    if stolen:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="🥷 Отомстить", callback_data=f"rv:{thief.id}:{victim.id}")
            ]]
        )
    await message.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("rv:"))
async def handle_revenge(callback: CallbackQuery) -> None:
    """Кнопка «Отомстить»: нажать может только пострадавший, ответное ограбление."""
    if not callback.data or not callback.message:
        await callback.answer()
        return
    try:
        _, thief_id, victim_id = callback.data.split(":")
        thief_id, victim_id = int(thief_id), int(victim_id)
    except ValueError:
        await callback.answer()
        return

    if callback.from_user.id != victim_id:
        await callback.answer("Мстить может только пострадавший!", show_alert=True)
        return

    chat_id = callback.message.chat.id
    enemy = await webdb.player(chat_id, thief_id)
    if not enemy:
        await callback.answer("Вор скрылся без следа.", show_alert=True)
        return

    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    text, _ = await rob_text(
        victim_id, format_display_name(callback.from_user), thief_id, enemy["username"], chat_id
    )
    await callback.message.answer(f"😤 <b>Месть!</b>\n{text}")
