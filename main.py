import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent, MenuButtonWebApp, WebAppInfo

from config import BOT_TOKEN, WEBAPP_URL
from database import init_db
from handlers.chatai import load_trends
from handlers.daily import load_open_questions
from jobs import jobs_loop
from handlers import anatomy, chatai, daily, disputes, rpcmd, economy, fun, games, inputs, lottery, menu, minigames, quick, social, tagall, remind, give, onboard
import bankraid, city, marriage, missions
from middlewares import DrunkMiddleware, StatsMiddleware, ThrottleMiddleware

# В меню «/» Telegram показывает плоский список, поэтому здесь только самое ходовое,
# а остальное сгруппировано по разделам в /menu
COMMANDS = [
    ("menu", "Все команды по разделам"),
    ("onboard", "Онбординг: как играть"),
    ("app", "Мини-апп"),
    ("dick", "Замер (раз в 3 часа)"),
    ("top", "Рейтинг чата"),
    ("daily", "Ежедневный бонус"),
    ("casino", "Слоты на сантиметры"),
    ("lottery", "Лотерея"),
    ("duel", "Дуэль (reply)"),
    ("trivia", "Викторина"),
    ("hero", "Герой дня"),
    ("give", "Подарить см (reply)"),
    ("sud", "Суд чата"),
    ("hug", "РП: обнять (ответом)"),
    ("rel", "Твои отношения"),
    ("opg", "Банды и войны района"),
    ("gazeta", "Газета «Вечерний Район»"),
    ("bank", "Ограбить банк всем чатом"),
    ("brak", "Предложить брак (ответом)"),
    ("semya", "Твоя семья"),
    ("all", "Позвать всех"),
]


async def on_error(event: ErrorEvent) -> bool:
    """Одно упавшее событие не должно оставлять чат в тишине."""
    logging.getLogger("bot").error(
        "Ошибка в обработчике: %r", event.exception, exc_info=event.exception
    )
    message = event.update.message or (
        event.update.callback_query.message if event.update.callback_query else None
    )
    if message:
        try:
            await message.answer("🤕 Что-то пошло не так, я уже записал это в лог.")
        except Exception:
            pass
    return True


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Инициализация и миграция таблиц SQLite перед запуском поллинга
    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.message.outer_middleware(ThrottleMiddleware())
    dp.message.outer_middleware(StatsMiddleware())
    dp.message.outer_middleware(DrunkMiddleware())
    dp.errors.register(on_error)

    # Регистрация модульных роутеров (chatai — последним: ловит всё остальное)
    dp.include_router(anatomy.router)
    dp.include_router(games.router)
    dp.include_router(inputs.router)
    dp.include_router(daily.router)
    dp.include_router(lottery.router)
    dp.include_router(economy.router)
    dp.include_router(menu.router)
    dp.include_router(quick.router)
    dp.include_router(social.router)
    dp.include_router(minigames.router)
    dp.include_router(fun.router)
    dp.include_router(onboard.router)
    dp.include_router(give.router)
    dp.include_router(disputes.router)
    dp.include_router(rpcmd.router)
    dp.include_router(tagall.router)
    dp.include_router(remind.router)
    import liveevents
    for r in (city.router, bankraid.router, marriage.router, missions.router, liveevents.router):
        dp.include_router(r)
    dp.include_router(chatai.router)

    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])

    # Кнопка «Мини-апп» рядом с полем ввода в личных чатах с ботом
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="Район", web_app=WebAppInfo(url=WEBAPP_URL))
    )

    # Сброс старых апдейтов и старт поллинга
    await bot.delete_webhook(drop_pending_updates=True)
    await load_open_questions()
    await load_trends()
    digest_task = asyncio.create_task(jobs_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        digest_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
