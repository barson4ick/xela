"""Игровые комнаты арены: дуэль, крестики-нолики, камень-ножницы-бумага (синхронизация опросом)."""
import random
import time
import uuid
from typing import Dict, List, Optional

BOT_ID = 0
OPEN_TTL = 600       # открытая комната без соперника живёт 10 минут
IDLE_TTL = 900       # брошенная партия закрывается через 15 минут
CLAIM_AFTER = 75     # через сколько секунд молчания соперника можно забрать победу

GAMES = {
    "duel": {"title": "Дуэль", "icon": "⚔️", "stake": 3},
}
TTT_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
RPS_BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
RPS_ICON = {"rock": "✊", "scissors": "✌️", "paper": "🖐"}


class GameError(Exception):
    pass


class Room:
    def __init__(self, chat_id: int, game: str, host: int, host_name: str, vs_bot: bool) -> None:
        self.rid = uuid.uuid4().hex[:8]
        self.chat_id = chat_id
        self.game = game
        self.stake = 0 if vs_bot else GAMES[game]["stake"]
        self.vs_bot = vs_bot
        self.players: List[int] = [host]
        self.names: Dict[int, str] = {host: host_name}
        self.status = "open"  # open | playing | done
        self.winner: Optional[int] = None  # id победителя; None — ничья/не определён
        self.settled = False
        self.bets: Dict[int, tuple] = {}  # зритель → (на кого, сколько, имя)
        self.bets_done = False
        self.log: List[str] = []
        self.created = self.updated = time.time()
        self.state: dict = {}
        if vs_bot:
            self._add(BOT_ID, "🤖 Бот-тренер")
            self._start()

    # ── жизненный цикл ──
    def _add(self, uid: int, name: str) -> None:
        self.players.append(uid)
        self.names[uid] = name

    def join(self, uid: int, name: str) -> None:
        if self.status != "open" or len(self.players) >= 2:
            raise GameError("В эту комнату уже нельзя войти")
        if uid in self.players:
            raise GameError("Ты уже в этой комнате")
        self._add(uid, name)
        self._start()

    def other(self, uid: int) -> int:
        return self.players[1] if uid == self.players[0] else self.players[0]

    def _start(self) -> None:
        self.status = "playing"
        self.updated = time.time()
        first = random.choice(self.players)
        if self.game == "duel":
            self.state = {"hp": {p: 30 for p in self.players}, "turn": first}
        elif self.game == "ttt":
            # players[0] — X, players[1] — O; X ходит первым
            self.state = {"board": [None] * 9, "turn": self.players[0]}
        else:
            self.state = {"choice": {}, "score": {p: 0 for p in self.players}, "round": 1, "last": None}
        self.log.append("Игра началась!")
        if self.vs_bot:
            self._bot_move()

    def finish(self, winner: Optional[int], text: str) -> None:
        self.status = "done"
        self.winner = winner
        self.updated = time.time()
        self.log.append(text)

    def expired(self) -> bool:
        age = time.time() - self.updated
        return (self.status == "open" and age > OPEN_TTL) or (self.status == "playing" and age > IDLE_TTL)

    def name(self, uid: int) -> str:
        return self.names.get(uid, "?")

    # ── ходы ──
    def act(self, uid: int, action: str) -> None:
        if uid not in self.players:
            raise GameError("Ты не участвуешь в этой игре")
        if self.status != "playing":
            raise GameError("Игра не идёт")
        if action == "claim":
            return self._claim(uid)
        {"duel": self._act_duel, "ttt": self._act_ttt, "rps": self._act_rps}[self.game](uid, action)
        self.updated = time.time()
        if self.vs_bot and self.status == "playing":
            self._bot_move()

    def _claim(self, uid: int) -> None:
        waiting_on = self.state.get("turn")
        if self.game == "rps":
            waiting_on = next((p for p in self.players if p not in self.state["choice"]), None)
        if waiting_on is None or waiting_on == uid:
            raise GameError("Сейчас ждут не соперника")
        if time.time() - self.updated < CLAIM_AFTER:
            raise GameError(f"Подожди, у соперника ещё есть время ({CLAIM_AFTER} сек)")
        self.finish(uid, f"{self.name(waiting_on)} не ходит — победа {self.name(uid)} по времени")

    def _act_duel(self, uid: int, action: str) -> None:
        st = self.state
        if st["turn"] != uid:
            raise GameError("Сейчас ход соперника")
        foe = self.other(uid)
        me = self.name(uid)
        if action == "lunge":
            dmg = random.randint(7, 12)
            self.log.append(f"🗡 {me} делает выпад: −{dmg} HP")
        elif action == "heli":
            if random.randint(1, 100) <= 40:
                dmg = 0
                self.log.append(f"🌪 {me} крутит «Вертолёт», но промахивается")
            else:
                dmg = random.randint(14, 20)
                self.log.append(f"🌪 {me} проводит «Вертолёт»: −{dmg} HP")
        else:
            raise GameError("Неизвестное действие")
        import drunkduel
        dmg, note = drunkduel.swing(self.chat_id, uid, dmg)
        if note:
            self.log.append(note.strip())
        st["hp"][foe] = max(0, st["hp"][foe] - dmg)
        if st["hp"][foe] <= 0:
            self.finish(uid, f"🏆 {me} побеждает!")
        else:
            st["turn"] = foe

    def _ttt_winner(self) -> Optional[int]:
        b = self.state["board"]
        for a, c, d in TTT_LINES:
            if b[a] is not None and b[a] == b[c] == b[d]:
                return b[a]
        return None

    def _act_ttt(self, uid: int, action: str) -> None:
        st = self.state
        if st["turn"] != uid:
            raise GameError("Сейчас ход соперника")
        try:
            cell = int(action.split(":")[1])
            assert action.startswith("cell:") and 0 <= cell <= 8
        except (ValueError, IndexError, AssertionError):
            raise GameError("Неизвестное действие")
        if st["board"][cell] is not None:
            raise GameError("Клетка занята")
        st["board"][cell] = uid
        win = self._ttt_winner()
        if win is not None:
            self.finish(win, f"🏆 {self.name(win)} собирает линию!")
        elif all(c is not None for c in st["board"]):
            self.finish(None, "🤝 Ничья!")
        else:
            st["turn"] = self.other(uid)

    def _act_rps(self, uid: int, action: str) -> None:
        st = self.state
        if action not in RPS_BEATS:
            raise GameError("Неизвестное действие")
        if uid in st["choice"]:
            raise GameError("Ты уже выбрал, ждём соперника")
        st["choice"][uid] = action
        if len(st["choice"]) < 2:
            return
        a, b = self.players
        ca, cb = st["choice"][a], st["choice"][b]
        if ca == cb:
            result = "ничья в раунде"
        elif RPS_BEATS[ca] == cb:
            st["score"][a] += 1
            result = f"раунд за {self.name(a)}"
        else:
            st["score"][b] += 1
            result = f"раунд за {self.name(b)}"
        st["last"] = {"choice": {a: ca, b: cb}, "result": result}
        self.log.append(f"{RPS_ICON[ca]} {self.name(a)} vs {self.name(b)} {RPS_ICON[cb]} — {result}")
        st["choice"] = {}
        st["round"] += 1
        for p in self.players:
            if st["score"][p] >= 2:
                self.finish(p, f"🏆 {self.name(p)} выигрывает матч!")
                return

    # ── бот-тренер ──
    def _bot_move(self) -> None:
        st = self.state
        if self.game == "duel":
            if st["turn"] == BOT_ID:
                self._act_duel(BOT_ID, random.choice(["lunge", "lunge", "heli"]))
        elif self.game == "ttt":
            if st["turn"] == BOT_ID:
                self._act_ttt(BOT_ID, f"cell:{self._ttt_bot_cell()}")
        else:
            if BOT_ID not in st["choice"] and len(self.players) == 2:
                human = next(p for p in self.players if p != BOT_ID)
                if human in st["choice"]:
                    self._act_rps(BOT_ID, random.choice(list(RPS_BEATS)))

    def _ttt_bot_cell(self) -> int:
        board = self.state["board"]
        free = [i for i, c in enumerate(board) if c is None]
        human = next(p for p in self.players if p != BOT_ID)
        for mark in (BOT_ID, human):  # сначала выигрыш, потом блок
            for a, c, d in TTT_LINES:
                line = [board[a], board[c], board[d]]
                if line.count(mark) == 2 and line.count(None) == 1:
                    return (a, c, d)[line.index(None)]
        if 4 in free:
            return 4
        corners = [i for i in (0, 2, 6, 8) if i in free]
        return random.choice(corners or free)

    # ── представление для клиента ──
    def summary(self) -> dict:
        return {
            "rid": self.rid,
            "game": self.game,
            "stake": self.stake,
            "host": self.players[0],
            "host_name": self.names[self.players[0]],
            "age": int(time.time() - self.created),
        }

    def view(self, uid: int) -> dict:
        st = self.state
        out = {
            "rid": self.rid,
            "game": self.game,
            "stake": self.stake,
            "vs_bot": self.vs_bot,
            "status": self.status,
            "you": uid,
            "players": [{"id": p, "name": self.names[p]} for p in self.players],
            "winner": self.winner,
            "log": self.log[-6:],
            "idle": int(time.time() - self.updated),
            "claim_after": CLAIM_AFTER,
        }
        if self.status == "open":
            return out
        if self.game == "duel":
            out["hp"] = {str(p): v for p, v in st["hp"].items()}
            out["turn"] = st["turn"]
        elif self.game == "ttt":
            out["board"] = [
                None if c is None else ("X" if c == self.players[0] else "O") for c in st["board"]
            ]
            out["turn"] = st["turn"]
        else:
            out["score"] = {str(p): v for p, v in st["score"].items()}
            out["round"] = st["round"]
            out["chosen"] = {str(p): (p in st["choice"]) for p in self.players}
            out["my_choice"] = st["choice"].get(uid)
            if st["last"]:
                out["last"] = {
                    "choice": {str(p): c for p, c in st["last"]["choice"].items()},
                    "result": st["last"]["result"],
                }
        return out
