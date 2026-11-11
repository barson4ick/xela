import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import trivia_bank
from database import add_size, ensure_user
from utils import display_name, esc

log = logging.getLogger(__name__)
router = Router()

# ───────────────────────────── Викторина ─────────────────────────────

# (вопрос, правильный ответ, [неправильные])
QUESTIONS = [
    ("Столица Австралии?", "Канберра", ["Сидней", "Мельбурн", "Перт"]),
    ("Самая большая планета Солнечной системы?", "Юпитер", ["Сатурн", "Нептун", "Земля"]),
    ("Сколько сторон у шестиугольника?", "6", ["5", "7", "8"]),
    ("Кто написал «Войну и мир»?", "Лев Толстой", ["Фёдор Достоевский", "Антон Чехов", "Иван Тургенев"]),
    ("Химический символ золота?", "Au", ["Ag", "Go", "Gd"]),
    ("В каком году Гагарин полетел в космос?", "1961", ["1957", "1969", "1975"]),
    ("Самый большой океан?", "Тихий", ["Атлантический", "Индийский", "Северный Ледовитый"]),
    ("Сколько игроков одной команды на поле в футболе?", "11", ["9", "10", "12"]),
    ("Какая из птиц не умеет летать?", "Пингвин", ["Орёл", "Ласточка", "Сова"]),
    ("Столица Японии?", "Токио", ["Киото", "Осака", "Нагоя"]),
    ("Кто автор романа «Мастер и Маргарита»?", "Михаил Булгаков", ["Борис Пастернак", "Максим Горький", "Владимир Набоков"]),
    ("Самая высокая гора мира?", "Эверест", ["Эльбрус", "Монблан", "Килиманджаро"]),
    ("Сколько цветов в радуге?", "7", ["5", "6", "9"]),
    ("Каким газом дышат люди?", "Кислород", ["Азот", "Гелий", "Углекислый газ"]),
    ("Самое быстрое наземное животное?", "Гепард", ["Лев", "Антилопа", "Лошадь"]),
    ("Какой язык программирования назван в честь комик-группы?", "Python", ["Java", "Ruby", "Swift"]),
    ("Столица Канады?", "Оттава", ["Торонто", "Монреаль", "Ванкувер"]),
    ("Сколько клеток на шахматной доске?", "64", ["56", "72", "81"]),
    ("Кто основал Microsoft вместе с Полом Алленом?", "Билл Гейтс", ["Стив Джобс", "Илон Маск", "Марк Цукерберг"]),
    ("Сколько будет 7 × 8?", "56", ["54", "48", "64"]),
    ("Самая длинная река Европы?", "Волга", ["Дунай", "Рейн", "Днепр"]),
    ("Какой металл жидкий при комнатной температуре?", "Ртуть", ["Свинец", "Олово", "Алюминий"]),
    ("Как зовут пса из Простоквашино?", "Шарик", ["Бобик", "Тузик", "Барбос"]),
    ("Сколько ног у паука?", "8", ["6", "10", "12"]),
    ("Ближайшая к Солнцу планета?", "Меркурий", ["Венера", "Марс", "Земля"]),
    ("Какой город называют Северной столицей России?", "Санкт-Петербург", ["Мурманск", "Архангельск", "Великий Новгород"]),
    ("Из какого фрукта традиционно делают сидр?", "Яблоко", ["Груша", "Виноград", "Вишня"]),
    ("Кто написал картину «Мона Лиза»?", "Леонардо да Винчи", ["Микеланджело", "Рафаэль", "Рембрандт"]),
    ("В каких единицах измеряется частота?", "Герцы", ["Ньютоны", "Вольты", "Паскали"]),
    ("Сколько материков на Земле (по общепринятой модели)?", "6", ["4", "5", "7"]),
    ('Какая планета известна как Красная планета?', 'Марс', ['Венера', 'Юпитер', 'Меркурий']),
    ('Сколько минут в одних сутках?', '1440', ['1200', '1360', '1640']),
    ('Кто написал «Преступление и наказание»?', 'Фёдор Достоевский', ['Лев Толстой', 'Николай Гоголь', 'Иван Бунин']),
    ('Столица Италии?', 'Рим', ['Милан', 'Неаполь', 'Венеция']),
    ('Какой химический элемент обозначается символом O?', 'Кислород', ['Золото', 'Осмий', 'Олово']),
    ('В какой стране находится Колизей?', 'Италия', ['Греция', 'Испания', 'Турция']),
    ('Сколько струн у классической гитары?', '6', ['4', '5', '7']),
    ('Как называется самая длинная река Африки?', 'Нил', ['Конго', 'Нигер', 'Замбези']),
    ('Какой орган качает кровь по телу?', 'Сердце', ['Печень', 'Лёгкие', 'Почки']),
    ('Кто нарисовал «Звёздную ночь»?', 'Винсент Ван Гог', ['Клод Моне', 'Пабло Пикассо', 'Сальвадор Дали']),
    ('Столица Египта?', 'Каир', ['Александрия', 'Луксор', 'Гиза']),
    ('Сколько градусов в прямом угле?', '90', ['45', '100', '180']),
    ('Как называется наша галактика?', 'Млечный Путь', ['Андромеда', 'Треугольник', 'Сомбреро']),
    ('Какое животное считается самым большим на Земле?', 'Синий кит', ['Слон', 'Кашалот', 'Жираф']),
    ('Кто автор теории относительности?', 'Альберт Эйнштейн', ['Исаак Ньютон', 'Нильс Бор', 'Стивен Хокинг']),
    ('Сколько колец на флаге Олимпийских игр?', '5', ['4', '6', '7']),
    ('Какая страна подарила США Статую Свободы?', 'Франция', ['Великобритания', 'Италия', 'Испания']),
    ('Самая маленькая планета Солнечной системы?', 'Меркурий', ['Марс', 'Плутон', 'Венера']),
    ('Как называется столица Германии?', 'Берлин', ['Мюнхен', 'Гамбург', 'Франкфурт']),
    ('Какой напиток делают из бобов какао?', 'Шоколад', ['Кофе', 'Чай', 'Сидр']),
    ('Сколько месяцев в году имеют 28 дней (хотя бы)?', '12', ['1', '2', '6']),
    ('Кто написал «Евгения Онегина»?', 'Александр Пушкин', ['Михаил Лермонтов', 'Николай Некрасов', 'Фёдор Тютчев']),
    ('Какой океан омывает Африку с востока?', 'Индийский', ['Атлантический', 'Тихий', 'Северный Ледовитый']),
    ('Сколько клавиш у стандартного пианино?', '88', ['76', '92', '100']),
    ('Как зовут главного героя «Гарри Поттера»?', 'Гарри Поттер', ['Рон Уизли', 'Драко Малфой', 'Невилл Долгопупс']),
    ('Что измеряют в амперах?', 'Силу тока', ['Напряжение', 'Мощность', 'Сопротивление']),
    ('Столица Франции?', 'Париж', ['Лион', 'Марсель', 'Ницца']),
    ('Какой газ преобладает в атмосфере Земли?', 'Азот', ['Кислород', 'Углекислый газ', 'Аргон']),
    ('Сколько лет длится столетие?', '100', ['10', '50', '1000']),
    ('В каком городе находится Эйфелева башня?', 'Париж', ['Лондон', 'Рим', 'Берлин']),
    ('Какая птица символизирует мир?', 'Голубь', ['Ворон', 'Сокол', 'Лебедь']),
    ('Из чего делают бумагу?', 'Из древесины', ['Из камня', 'Из стекла', 'Из песка']),
    ('Кто сыграл Джека в фильме «Титаник»?', 'Леонардо ДиКаприо', ['Брэд Питт', 'Том Харди', 'Райан Гослинг']),
    ('Какой самый твёрдый природный материал?', 'Алмаз', ['Гранит', 'Кварц', 'Золото']),
    ('В какой стране изобрели пиццу?', 'Италия', ['Франция', 'США', 'Греция']),
    ('Сколько зубов у взрослого человека (с зубами мудрости)?', '32', ['28', '30', '36']),
    ('Как называется единица измерения массы в системе СИ?', 'Килограмм', ['Грамм', 'Ньютон', 'Тонна']),
    ('Какого цвета получится при смешении синего и жёлтого?', 'Зелёного', ['Оранжевого', 'Фиолетового', 'Коричневого']),
    ('Столица Испании?', 'Мадрид', ['Барселона', 'Севилья', 'Валенсия']),
    ('Какой спорт называют королевой спорта?', 'Лёгкая атлетика', ['Футбол', 'Теннис', 'Плавание']),
]
TRIVIA_REWARD = 2
TRIVIA_TIMEOUT = 30
TRIVIA_CHAT_GAP = 60      # пауза между вопросами в одном чате (сек)
TRIVIA_USER_GAP = 180     # один человек запускает вопрос не чаще раза в 3 минуты
TRIVIA_DAILY_WINS = 5     # наград за верные ответы в сутки на человека
_chat_last: Dict[int, float] = {}
_user_last: Dict[int, float] = {}


@dataclass
class TriviaSession:
    trivia_id: str
    correct: int
    answer_text: str
    message: Message
    answered: Set[int] = field(default_factory=set)


trivias: Dict[int, TriviaSession] = {}  # chat_id → сессия


@router.message(Command("trivia"))
async def cmd_trivia(message: Message) -> None:
    chat_id = message.chat.id
    if chat_id in trivias:
        await message.reply("❓ Вопрос уже в игре — сначала ответьте на него!")
        return

    now = time.monotonic()
    wait_chat = TRIVIA_CHAT_GAP - (now - _chat_last.get(chat_id, -1e9))
    wait_user = TRIVIA_USER_GAP - (now - _user_last.get(message.from_user.id, -1e9))
    if max(wait_chat, wait_user) > 0:
        await message.reply(f"⏳ Следующий вопрос можно запустить через {int(max(wait_chat, wait_user)) + 1} сек.")
        return
    _chat_last[chat_id] = _user_last[message.from_user.id] = now

    question, right, wrong = await trivia_bank.pick(chat_id, QUESTIONS)
    options = [right] + wrong
    random.shuffle(options)
    correct = options.index(right)
    trivia_id = uuid.uuid4().hex[:6]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=opt, callback_data=f"tr:{trivia_id}:{i}")]
            for i, opt in enumerate(options)
        ]
    )
    sent = await message.reply(
        f"🧠 <b>Викторина!</b> (+{TRIVIA_REWARD} см первому, кто ответит верно)\n\n"
        f"<b>{esc(question)}</b>\n<i>У вас {TRIVIA_TIMEOUT} секунд. Одна попытка на человека.</i>",
        reply_markup=kb,
    )
    session = TriviaSession(trivia_id, correct, right, sent)
    trivias[chat_id] = session
    asyncio.create_task(_trivia_timeout(chat_id, session))


async def _trivia_timeout(chat_id: int, session: TriviaSession) -> None:
    await asyncio.sleep(TRIVIA_TIMEOUT)
    if trivias.get(chat_id) is session:
        trivias.pop(chat_id, None)
        try:
            await session.message.edit_text(
                f"⌛ Время вышло! Правильный ответ: <b>{esc(session.answer_text)}</b>."
            )
        except TelegramAPIError:
            pass


@router.callback_query(F.data.startswith("tr:"))
async def handle_trivia(callback: CallbackQuery) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    try:
        _, trivia_id, idx_raw = callback.data.split(":")
        idx = int(idx_raw)
    except ValueError:
        await callback.answer()
        return

    chat_id = callback.message.chat.id
    session = trivias.get(chat_id)
    if not session or session.trivia_id != trivia_id:
        await callback.answer("Этот вопрос уже закрыт.", show_alert=True)
        return

    user = callback.from_user
    if user.id in session.answered:
        await callback.answer("Ты уже отвечал на этот вопрос!", show_alert=True)
        return
    session.answered.add(user.id)

    if idx != session.correct:
        await callback.answer("❌ Неверно! Вторую попытку не дам.", show_alert=True)
        return

    trivias.pop(chat_id, None)
    _chat_last[chat_id] = time.monotonic()
    name = display_name(user)
    await callback.answer("✅ Верно!")
    if await trivia_bank.wins_today(chat_id, user.id) >= TRIVIA_DAILY_WINS:
        text = (
            f"🎉 <b>{esc(name)}</b> отвечает верно: <b>{esc(session.answer_text)}</b>!\n"
            f"Лимит наград на сегодня ({TRIVIA_DAILY_WINS}) исчерпан — играй для славы."
        )
    else:
        await trivia_bank.add_win(chat_id, user.id)
        new_size = await add_size(user.id, chat_id, name, TRIVIA_REWARD, 'trivia')
        text = (
            f"🎉 <b>{esc(name)}</b> отвечает верно: <b>{esc(session.answer_text)}</b>!\n"
            f"Награда: <b>+{TRIVIA_REWARD} см</b> (теперь {new_size} см)"
        )
    try:
        await callback.message.edit_text(text)
    except TelegramAPIError:
        pass


# ───────────────────────────── Виселица ─────────────────────────────

WORDS = [
    "компьютер", "телефон", "холодильник", "шоколад", "велосипед", "самолет",
    "картошка", "дружба", "каникулы", "программист", "мороженое", "телевизор",
    "пельмени", "чемодан", "зеркало", "подушка", "светофор", "бутерброд",
    "динозавр", "гитара", "арбуз", "рюкзак", "крокодил", "вертолет",
    "сковорода", "шашлык", "карусель", "гамбургер", "робот", "будильник",
]
RUS_LETTERS = set("абвгдежзийклмнопрстуфхцчшщъыьэюя")
GALLOWS_LIVES = 6
GALLOWS_REWARD = 3
GALLOWS_TTL = 900


@dataclass
class GallowsGame:
    word: str
    message: Message
    guessed: Set[str] = field(default_factory=set)
    wrong: List[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def masked(self) -> str:
        return " ".join(c.upper() if c in self.guessed else "_" for c in self.word)

    def lives(self) -> int:
        return GALLOWS_LIVES - len(self.wrong)

    def render(self, note: str = "") -> str:
        hearts = "❤️" * self.lives() + "🖤" * len(self.wrong)
        wrong = ", ".join(c.upper() for c in self.wrong) or "—"
        text = (
            f"🪢 <b>Виселица</b>\n\n<code>{self.masked()}</code>\n\n"
            f"{hearts}\nОшибки: {wrong}\n\n"
            f"<i>Пишите по одной русской букве в чат.</i>"
        )
        return f"{text}\n\n{note}" if note else text


gallows_games: Dict[int, GallowsGame] = {}








# ───────────────────────────── Мафия-лайт ─────────────────────────────

MAFIA_MIN, MAFIA_MAX = 4, 12
MAFIA_LOBBY_TIMEOUT = 300
MAFIA_NIGHT = 45
MAFIA_DAY = 75
MAFIA_REWARD = 3


@dataclass
class MafiaGame:
    chat_id: int
    host_id: int
    players: Dict[int, str]
    lobby_msg: Message
    roles: Dict[int, str] = field(default_factory=dict)
    alive: Set[int] = field(default_factory=set)
    phase: str = "lobby"  # lobby | night | day
    picks: Dict[int, int] = field(default_factory=dict)  # мафиози → жертва
    votes: Dict[int, int] = field(default_factory=dict)  # голосующий → цель (0 = пропуск)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    phase_done: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None

    def mafia_alive(self) -> List[int]:
        return [p for p in self.alive if self.roles.get(p) == "mafia"]

    def town_alive(self) -> List[int]:
        return [p for p in self.alive if self.roles.get(p) != "mafia"]


mafia_games: Dict[int, MafiaGame] = {}


def _lobby_text(g: MafiaGame) -> str:
    names = "\n".join(f"• {esc(n)}" for n in g.players.values())
    return (
        f"🕵️ <b>Мафия-лайт</b>\nИгроков: {len(g.players)} (нужно от {MAFIA_MIN})\n{names}\n\n"
        f"Хост — <b>{esc(g.players[g.host_id])}</b>. Когда все собрались, хост жмёт «Начать»."
    )


def _lobby_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🙋 Войти", callback_data="mf:join"),
            InlineKeyboardButton(text="▶️ Начать", callback_data="mf:start"),
        ]]
    )


def _role_row() -> List[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="🎭 Моя роль", callback_data="mf:role")]


def _night_kb(g: MafiaGame) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🔪 {g.players[p]}", callback_data=f"mf:kill:{p}")]
        for p in g.alive
    ]
    rows.append(_role_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _day_kb(g: MafiaGame) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🗳 {g.players[p]}", callback_data=f"mf:vote:{p}")]
        for p in g.alive
    ]
    rows.append([InlineKeyboardButton(text="🤷 Пропустить", callback_data="mf:vote:0")])
    rows.append(_role_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("mafia"))
async def cmd_mafia(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    chat_id = message.chat.id
    if message.chat.type == "private":
        await message.reply("🕵️ Мафия играется только в групповом чате.")
        return
    if chat_id in mafia_games:
        await message.reply("🕵️ Игра уже идёт или собирается!")
        return

    user = message.from_user
    name = display_name(user)
    await ensure_user(user.id, chat_id, name)
    sent = await message.answer("🕵️ Собираем игру...")
    g = MafiaGame(chat_id=chat_id, host_id=user.id, players={user.id: name}, lobby_msg=sent)
    mafia_games[chat_id] = g
    await sent.edit_text(_lobby_text(g), reply_markup=_lobby_kb())
    g.task = asyncio.create_task(_run_mafia(bot, g))


@router.message(Command("mafiastop"))
async def cmd_mafia_stop(message: Message) -> None:
    g = mafia_games.get(message.chat.id)
    if not g or not message.from_user:
        return
    if message.from_user.id != g.host_id:
        await message.reply("Остановить игру может только хост.")
        return
    if g.task:
        g.task.cancel()


@router.callback_query(F.data.startswith("mf:"))
async def handle_mafia(callback: CallbackQuery) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    g = mafia_games.get(callback.message.chat.id)
    if not g:
        await callback.answer("Эта игра уже закончилась.", show_alert=True)
        return

    parts = callback.data.split(":")
    action = parts[1]
    user = callback.from_user
    name = display_name(user)

    if action == "join":
        if g.phase != "lobby":
            await callback.answer("Игра уже началась.", show_alert=True)
        elif user.id in g.players:
            await callback.answer("Ты уже в игре.")
        elif len(g.players) >= MAFIA_MAX:
            await callback.answer("Лобби заполнено.", show_alert=True)
        else:
            g.players[user.id] = name
            await ensure_user(user.id, g.chat_id, name)
            await callback.answer("Ты в игре!")
            try:
                await g.lobby_msg.edit_text(_lobby_text(g), reply_markup=_lobby_kb())
            except TelegramAPIError:
                pass
        return

    if action == "start":
        if g.phase != "lobby":
            await callback.answer()
        elif user.id != g.host_id:
            await callback.answer("Начать может только хост.", show_alert=True)
        elif len(g.players) < MAFIA_MIN:
            await callback.answer(f"Нужно минимум {MAFIA_MIN} игрока.", show_alert=True)
        else:
            g.started.set()
            await callback.answer("Поехали!")
        return

    if action == "role":
        role = g.roles.get(user.id)
        if not role:
            await callback.answer("Ты не участвуешь в этой игре.", show_alert=True)
        elif role == "mafia":
            mates = [g.players[p] for p, r in g.roles.items() if r == "mafia" and p != user.id]
            extra = f"\nСообщник: {', '.join(mates)}" if mates else ""
            await callback.answer(f"🔪 Ты МАФИЯ.{extra}", show_alert=True)
        else:
            await callback.answer("🙂 Ты мирный житель.", show_alert=True)
        return

    if user.id not in g.alive:
        await callback.answer("Ты не в игре (или уже выбыл).", show_alert=True)
        return

    if action == "kill" and g.phase == "night":
        target = int(parts[2])
        if g.roles.get(user.id) != "mafia":
            await callback.answer("😴 Ты спишь. Ночью действует только мафия.", show_alert=True)
        elif target not in g.alive or g.roles.get(target) == "mafia":
            await callback.answer("Эту цель выбрать нельзя.", show_alert=True)
        else:
            g.picks[user.id] = target
            await callback.answer(f"Выбор принят: {g.players[target]}")
            if all(m in g.picks for m in g.mafia_alive()):
                g.phase_done.set()
        return

    if action == "vote" and g.phase == "day":
        target = int(parts[2])
        if target != 0 and target not in g.alive:
            await callback.answer("Эта цель уже выбыла.", show_alert=True)
            return
        g.votes[user.id] = target
        label = g.players[target] if target else "пропуск"
        await callback.answer(f"Голос принят: {label}")
        if all(p in g.votes for p in g.alive):
            g.phase_done.set()
        return

    await callback.answer("Сейчас это недоступно.")


def _plurality(counts: Dict[int, int]) -> Optional[int]:
    """Цель с единственным максимумом голосов, иначе None."""
    if not counts:
        return None
    top = max(counts.values())
    leaders = [t for t, c in counts.items() if c == top]
    return leaders[0] if len(leaders) == 1 else None


def _check_win(g: MafiaGame) -> Optional[str]:
    mafia, town = len(g.mafia_alive()), len(g.town_alive())
    if mafia == 0:
        return "town"
    if mafia >= town:
        return "mafia"
    return None


async def _wait_phase(g: MafiaGame, timeout: int) -> None:
    g.phase_done = asyncio.Event()
    try:
        await asyncio.wait_for(g.phase_done.wait(), timeout)
    except asyncio.TimeoutError:
        pass


async def _finish(bot: Bot, g: MafiaGame, winner: str) -> None:
    mafia_ids = [p for p, r in g.roles.items() if r == "mafia"]
    winners = mafia_ids if winner == "mafia" else [p for p in g.players if p not in mafia_ids]
    for p in winners:
        await add_size(p, g.chat_id, g.players[p], MAFIA_REWARD, 'mafia')
    reveal = "\n".join(
        f"{'🔪' if g.roles[p] == 'mafia' else '🙂'} {esc(g.players[p])}" for p in g.players
    )
    title = "🔪 Побеждает <b>МАФИЯ</b>!" if winner == "mafia" else "🎉 Побеждают <b>МИРНЫЕ</b>!"
    await bot.send_message(
        g.chat_id,
        f"{title}\nКаждый победитель получает <b>+{MAFIA_REWARD} см</b>.\n\n<b>Роли:</b>\n{reveal}",
    )


async def _run_mafia(bot: Bot, g: MafiaGame) -> None:
    try:
        try:
            await asyncio.wait_for(g.started.wait(), MAFIA_LOBBY_TIMEOUT)
        except asyncio.TimeoutError:
            await g.lobby_msg.edit_text("⌛ Игру никто не начал — лобби закрыто.")
            return

        ids = list(g.players)
        random.shuffle(ids)
        mafia_count = 1 if len(ids) < 8 else 2
        g.roles = {p: ("mafia" if i < mafia_count else "town") for i, p in enumerate(ids)}
        g.alive = set(ids)
        await g.lobby_msg.edit_text(
            f"🕵️ <b>Игра началась!</b> Игроков: {len(ids)}, мафии среди них: {mafia_count}.\n"
            f"Нажмите «Моя роль» под любым следующим сообщением — ответ видите только вы.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[_role_row()]),
        )
        await asyncio.sleep(8)

        round_no = 0
        while True:
            round_no += 1

            # Ночь
            g.phase, g.picks = "night", {}
            night_msg = await bot.send_message(
                g.chat_id,
                f"🌙 <b>Ночь {round_no}.</b> Город засыпает, просыпается мафия.\n"
                f"Мафия: выберите жертву ({MAFIA_NIGHT} сек). Остальные — спите 😴",
                reply_markup=_night_kb(g),
            )
            await _wait_phase(g, MAFIA_NIGHT)
            counts: Dict[int, int] = {}
            for target in g.picks.values():
                counts[target] = counts.get(target, 0) + 1
            victim = _plurality(counts) or (random.choice(list(g.picks.values())) if g.picks else None)
            try:
                await night_msg.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass

            if victim is None:
                await bot.send_message(g.chat_id, "☀️ <b>Утро.</b> Мафия проспала — этой ночью никто не пострадал.")
            else:
                g.alive.discard(victim)
                await bot.send_message(
                    g.chat_id,
                    f"☀️ <b>Утро.</b> Этой ночью убит <b>{esc(g.players[victim])}</b> "
                    f"({'мафия' if g.roles[victim] == 'mafia' else 'мирный'}).",
                )
            winner = _check_win(g)
            if winner:
                await _finish(bot, g, winner)
                return

            # День
            g.phase, g.votes = "day", {}
            alive_names = ", ".join(esc(g.players[p]) for p in g.alive)
            day_msg = await bot.send_message(
                g.chat_id,
                f"🗳 <b>День {round_no}.</b> Обсуждайте и голосуйте, кого казнить ({MAFIA_DAY} сек).\n"
                f"Живы: {alive_names}",
                reply_markup=_day_kb(g),
            )
            await _wait_phase(g, MAFIA_DAY)
            try:
                await day_msg.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass

            counts = {}
            for target in g.votes.values():
                counts[target] = counts.get(target, 0) + 1
            executed = _plurality(counts)
            if not executed:
                await bot.send_message(g.chat_id, "⚖️ Голоса разделились или все воздержались — никого не казнят.")
            else:
                g.alive.discard(executed)
                await bot.send_message(
                    g.chat_id,
                    f"⚖️ По решению города казнён <b>{esc(g.players[executed])}</b> — "
                    f"это был {'МАФИОЗИ 🔪' if g.roles[executed] == 'mafia' else 'мирный житель 🙂'}.",
                )
            winner = _check_win(g)
            if winner:
                await _finish(bot, g, winner)
                return
    except asyncio.CancelledError:
        try:
            await bot.send_message(g.chat_id, "🛑 Игра в мафию остановлена хостом.")
        except TelegramAPIError:
            pass
    except Exception:
        log.exception("Сбой в игре мафия")
        try:
            await bot.send_message(g.chat_id, "🤕 Мафия сломалась, игра прервана.")
        except TelegramAPIError:
            pass
    finally:
        mafia_games.pop(g.chat_id, None)
