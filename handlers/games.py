
import html
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict

from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions,
    User,
)

from database import ensure_user, transfer_size

router = Router()


@dataclass
class DuelSession:
    duel_id: str
    chat_id: int
    p1_id: int
    p1_name: str
    p2_id: int
    p2_name: str
    hp1: int = 30
    hp2: int = 30
    turn_id: int = 0
    state: str = "pending"  # "pending" | "active"
    created_at: float = field(default_factory=time.time)


# Оперативное хранилище активных дуэлей в памяти
duels: Dict[str, DuelSession] = {}


def get_display_name(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


DUEL_TTL = 1800  # брошенная дуэль не должна блокировать игроков вечно


def purge_stale_duels() -> None:
    now = time.time()
    for duel_id in [k for k, s in duels.items() if now - s.created_at > DUEL_TTL]:
        duels.pop(duel_id, None)


def is_user_in_duel(chat_id: int, user_id: int) -> bool:
    """Проверяет, участвует ли пользователь в активной или ожидающей дуэли в этом чате."""
    purge_stale_duels()
    for session in duels.values():
        if session.chat_id == chat_id and user_id in (session.p1_id, session.p2_id):
            return True
    return False


def build_challenge_kb(duel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Принять вызов", callback_data=f"duel:accept:{duel_id}"
                ),
                InlineKeyboardButton(
                    text="🏳️ Сбежать", callback_data=f"duel:flee:{duel_id}"
                ),
            ]
        ]
    )


def build_combat_kb(duel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗡 Выпад (7-12 HP)", callback_data=f"duel:lunge:{duel_id}"
                ),
                InlineKeyboardButton(
                    text="🌪 Вертолёт (Риск 14-20 HP)", callback_data=f"duel:heli:{duel_id}"
                ),
            ]
        ]
    )


@router.message(Command("duel"))
async def cmd_duel(message: Message) -> None:
    if not message.from_user:
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚠️ Чтобы вызвать соперника на дуэль, отправьте /duel в ответ (reply) на его сообщение!")
        return

    p1 = message.from_user
    p2 = message.reply_to_message.from_user
    chat_id = message.chat.id

    if p1.id == p2.id:
        await message.reply("🤺 Фехтовать с самим собой перед зеркалом нельзя!")
        return

    if p2.is_bot:
        await message.reply("🤖 Боты не принимают участия в дуэлях!")
        return

    if is_user_in_duel(chat_id, p1.id):
        await message.reply("⚠️ Вы уже находитесь в активной дуэли или ожидаете ответа соперника!")
        return

    if is_user_in_duel(chat_id, p2.id):
        await message.reply("⚠️ Этот игрок уже участвует в другой дуэли!")
        return

    p1_name = get_display_name(p1)
    p2_name = get_display_name(p2)

    await ensure_user(p1.id, chat_id, p1_name)
    await ensure_user(p2.id, chat_id, p2_name)

    duel_id = uuid.uuid4().hex[:8]
    duels[duel_id] = DuelSession(
        duel_id=duel_id,
        chat_id=chat_id,
        p1_id=p1.id,
        p1_name=p1_name,
        p2_id=p2.id,
        p2_name=p2_name,
        hp1=30,
        hp2=30,
        turn_id=p1.id,
        state="pending",
    )

    safe_p1 = html.escape(p1_name)
    safe_p2 = html.escape(p2_name)

    await message.reply(
        f"🧤 <b>Вызов на дуэль!</b>\n"
        f"<b>{safe_p1}</b> бросает перчатку <b>{safe_p2}</b>!\n"
        f"Ставка на кону: <b>3 см</b>.\n\n"
        f"<b>{safe_p2}</b>, примете ли вы бой?",
        reply_markup=build_challenge_kb(duel_id),
    )


@router.callback_query(F.data.startswith("duel:"))
async def handle_duel_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user or not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные кнопки.", show_alert=True)
        return

    _, action, duel_id = parts
    purge_stale_duels()
    session = duels.get(duel_id)

    if not session:
        await callback.answer("Эта дуэль уже завершена или устарела.", show_alert=True)
        return

    user_id = callback.from_user.id
    safe_p1 = html.escape(session.p1_name)
    safe_p2 = html.escape(session.p2_name)

    # Обработка стадии принятия вызова
    if session.state == "pending":
        if user_id != session.p2_id:
            await callback.answer("Нажать кнопку может только вызванный игрок!", show_alert=True)
            return

        if action == "flee":
            duels.pop(duel_id, None)
            await callback.answer("Вы сбежали с поля боя!")
            try:
                await callback.message.edit_text(
                    f"🏳️ <b>{safe_p2}</b> позорно сбежал от дуэли с <b>{safe_p1}</b>!"
                )
            except TelegramAPIError:
                pass
            return

        if action == "accept":
            session.state = "active"
            session.turn_id = session.p1_id
            await callback.answer("Вызов принят! Да начнётся бой!")
            try:
                await callback.message.edit_text(
                    f"⚔️ <b>Дуэль началась!</b>\n"
                    f"❤️ {safe_p1}: <b>{session.hp1} HP</b> | ❤️ {safe_p2}: <b>{session.hp2} HP</b>\n\n"
                    f"👉 Первым атакует: <b>{safe_p1}</b>",
                    reply_markup=build_combat_kb(duel_id),
                )
            except TelegramAPIError:
                pass
            return

    # Обработка боевого цикла
    if session.state == "active":
        if user_id not in (session.p1_id, session.p2_id):
            await callback.answer("Вы не участвуете в этой дуэли!", show_alert=True)
            return

        if user_id != session.turn_id:
            await callback.answer("Сейчас ход вашего противника!", show_alert=True)
            return

        is_p1_attacker = user_id == session.p1_id
        attacker_name = safe_p1 if is_p1_attacker else safe_p2
        defender_name = safe_p2 if is_p1_attacker else safe_p1

        if action == "lunge":
            dmg = random.randint(7, 12)
            hit_log = f"🗡 <b>{attacker_name}</b> делает точный выпад и наносит <b>{dmg} HP</b> урона!"
        elif action == "heli":
            if random.randint(1, 100) <= 40:
                dmg = 0
                hit_log = f"🌪 <b>{attacker_name}</b> крутит «Вертолёт», но промахивается (<b>0 HP</b>)!"
            else:
                dmg = random.randint(14, 20)
                hit_log = f"🌪 <b>{attacker_name}</b> проводит сокрушительный «Вертолёт» на <b>{dmg} HP</b>!"
        else:
            await callback.answer()
            return

        import drunkduel
        dmg, drunk_note = drunkduel.swing(session.chat_id, user_id, dmg)
        hit_log += drunk_note

        if is_p1_attacker:
            session.hp2 = max(0, session.hp2 - dmg)
            target_hp = session.hp2
            session.turn_id = session.p2_id
            next_turn_name = safe_p2
        else:
            session.hp1 = max(0, session.hp1 - dmg)
            target_hp = session.hp1
            session.turn_id = session.p1_id
            next_turn_name = safe_p1

        await callback.answer()

        # Проверка завершения боя
        if target_hp <= 0:
            duels.pop(duel_id, None)
            winner_id = session.p1_id if is_p1_attacker else session.p2_id
            loser_id = session.p2_id if is_p1_attacker else session.p1_id

            new_w_size, new_l_size = await transfer_size(
                user1_id=winner_id,
                user2_id=loser_id,
                chat_id=session.chat_id,
                delta1=3,
                delta2=-3,
                reason="duel",
            )

            try:
                import gangwar
                await gangwar.war_points(session.chat_id, winner_id, "duel")
            except Exception:
                pass
            try:
                await callback.message.edit_text(
                    f"{hit_log}\n\n"
                    f"❤️ {safe_p1}: <b>{session.hp1} HP</b> | ❤️ {safe_p2}: <b>{session.hp2} HP</b>\n\n"
                    f"🏆 <b>Победа!</b> <b>{attacker_name}</b> повергает <b>{defender_name}</b>!\n"
                    f"📈 <b>{attacker_name}</b> получает <b>+3 см</b> (теперь {new_w_size} см)\n"
                    f"📉 <b>{defender_name}</b> теряет <b>-3 см</b> (теперь {new_l_size} см)",
                    reply_markup=drunkduel.cure_kb(session.chat_id, [(session.p1_id, session.p1_name), (session.p2_id, session.p2_name)]),
                )
            except TelegramAPIError:
                pass
            return

        # Продолжение боя
        try:
            await callback.message.edit_text(
                f"{hit_log}\n\n"
                f"❤️ {safe_p1}: <b>{session.hp1} HP</b> | ❤️ {safe_p2}: <b>{session.hp2} HP</b>\n\n"
                f"👉 Сейчас ход: <b>{next_turn_name}</b>",
                reply_markup=build_combat_kb(duel_id),
            )
        except TelegramAPIError:
            pass




@router.callback_query(F.data.startswith("dcure:"))
async def handle_duel_cure(callback: CallbackQuery) -> None:
    uid = int(callback.data.split(":", 1)[1])
    if callback.from_user.id != uid:
        await callback.answer("Это не твоя шаурма 😄", show_alert=True)
        return
    import bar
    from rooms import GameError
    u = callback.from_user
    name = f"@{u.username}" if u.username else u.full_name
    try:
        res = await bar.buy(callback.message.chat.id, uid, name, "shawarma")
    except GameError as exc:
        await callback.answer(str(exc)[:190], show_alert=True)
        return
    await callback.answer(str(res.get("text", "Полегчало 🌯"))[:190], show_alert=True)
