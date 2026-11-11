"""Мафия для мини-аппа: роли видны только игроку, ночные ходы кнопками, фазы по таймеру."""
import random
import time
import uuid
from typing import Dict, List, Optional

from rooms import GameError

MIN_PLAYERS, MAX_PLAYERS = 4, 12
OPEN_TTL = 900
NIGHT_SECONDS = 40
DAY_SECONDS = 90
REWARD = 3
IDLE_END = 600  # партию без движения закрываем

ROLE_TITLE = {
    "mafia": ("🔪", "Мафия", "Ночью выбери жертву вместе с напарником. Днём притворяйся мирным."),
    "doctor": ("💉", "Доктор", "Каждую ночь можешь спасти одного игрока (в том числе себя)."),
    "sheriff": ("🕵️", "Комиссар", "Каждую ночь проверяй одного игрока: мафия он или нет."),
    "town": ("🙂", "Мирный", "Днём вычисляй мафию и голосуй за казнь."),
}


class MafiaRoom:
    def __init__(self, chat_id: int, host: int, host_name: str) -> None:
        self.rid = uuid.uuid4().hex[:8]
        self.chat_id = chat_id
        self.host = host
        self.players: Dict[int, str] = {host: host_name}
        self.status = "open"  # open | night | day | done
        self.roles: Dict[int, str] = {}
        self.alive: List[int] = []
        self.round = 0
        self.deadline = 0.0
        self.picks: Dict[int, int] = {}   # ночные действия: игрок → цель
        self.votes: Dict[int, int] = {}   # дневные голоса: игрок → цель (0 — воздержался)
        self.checks: Dict[int, List[dict]] = {}  # результаты проверок комиссара
        self.log: List[str] = []
        self.winner: Optional[str] = None
        self.settled = False
        self.created = self.updated = time.time()
        self.outbox: List[str] = []  # сообщения в чат, которые должен отправить сервер

    # ── лобби ──
    def join(self, uid: int, name: str) -> None:
        if self.status != "open":
            raise GameError("Игра уже идёт")
        if uid in self.players:
            raise GameError("Ты уже в лобби")
        if len(self.players) >= MAX_PLAYERS:
            raise GameError("Лобби заполнено")
        self.players[uid] = name
        self.updated = time.time()

    def leave(self, uid: int) -> None:
        if self.status == "open":
            self.players.pop(uid, None)
            if uid == self.host and self.players:
                self.host = next(iter(self.players))
            self.updated = time.time()
        elif self.status in ("night", "day") and uid in self.alive:
            self.alive.remove(uid)
            self.log.append(f"🏃 {self.players[uid]} сбежал из города")
            self._check_end()

    def start(self, uid: int) -> None:
        if self.status != "open":
            raise GameError("Игра уже идёт")
        if uid != self.host:
            raise GameError("Начать может только хост")
        n = len(self.players)
        if n < MIN_PLAYERS:
            raise GameError(f"Нужно минимум {MIN_PLAYERS} игрока")
        ids = list(self.players)
        random.shuffle(ids)
        mafia = 1 if n < 8 else 2
        roles = ["mafia"] * mafia
        if n >= 6:
            roles += ["doctor", "sheriff"]
        roles += ["town"] * (n - len(roles))
        self.roles = dict(zip(ids, roles))
        self.alive = list(ids)
        self.round = 0
        self.outbox.append(
            f"🕵️ <b>Мафия началась!</b> Игроков: {n}, мафии: {mafia}"
            + (", есть доктор и комиссар" if n >= 6 else "")
            + ". Роли и ночные ходы — в мини-аппе (вкладка «Арена»)."
        )
        self._night()

    # ── фазы ──
    def _mafia_alive(self) -> List[int]:
        return [p for p in self.alive if self.roles[p] == "mafia"]

    def _night(self) -> None:
        self.round += 1
        self.status = "night"
        self.picks = {}
        self.deadline = time.time() + NIGHT_SECONDS
        self.updated = time.time()
        self.log.append(f"🌙 Ночь {self.round}: город засыпает")

    def _day(self) -> None:
        self.status = "day"
        self.votes = {}
        self.deadline = time.time() + DAY_SECONDS
        self.updated = time.time()

    def _night_actors(self) -> List[int]:
        return [p for p in self.alive if self.roles[p] in ("mafia", "doctor", "sheriff")]

    def act(self, uid: int, action: str) -> None:
        if self.status not in ("night", "day"):
            raise GameError("Сейчас ходов нет")
        if uid not in self.alive:
            raise GameError("Ты выбыл из игры")
        try:
            kind, target = action.split(":")
            target = int(target)
        except ValueError:
            raise GameError("Неизвестное действие")
        role = self.roles[uid]
        if self.status == "night":
            if kind != {"mafia": "kill", "doctor": "heal", "sheriff": "check"}.get(role):
                raise GameError("Сейчас твой выбор не требуется — спи 😴")
            if target not in self.alive:
                raise GameError("Этот игрок уже выбыл")
            if role == "mafia" and self.roles[target] == "mafia":
                raise GameError("Своих не убиваем")
            if role == "sheriff" and target == uid:
                raise GameError("Себя проверять незачем")
            if uid in self.picks:
                raise GameError("Выбор уже сделан")
            self.picks[uid] = target
            if role == "sheriff":
                self.checks.setdefault(uid, []).append(
                    {"round": self.round, "name": self.players[target], "mafia": self.roles[target] == "mafia"}
                )
            if all(p in self.picks for p in self._night_actors()):
                self._resolve_night()
        else:
            if kind != "vote":
                raise GameError("Сейчас день — время голосовать")
            if target != 0 and target not in self.alive:
                raise GameError("Этот игрок уже выбыл")
            if target == uid:
                raise GameError("За себя голосовать нельзя")
            self.votes[uid] = target
            if all(p in self.votes for p in self.alive):
                self._resolve_day()
        self.updated = time.time()

    def tick(self) -> None:
        """Продвигает игру по таймеру (вызывается при каждом запросе состояния)."""
        now = time.time()
        if self.status == "night" and now >= self.deadline:
            self._resolve_night()
        elif self.status == "day" and now >= self.deadline:
            self._resolve_day()

    def _resolve_night(self) -> None:
        kills: Dict[int, int] = {}
        for p, t in self.picks.items():
            if self.roles[p] == "mafia":
                kills[t] = kills.get(t, 0) + 1
        victim = None
        if kills:
            top = max(kills.values())
            victim = random.choice([t for t, c in kills.items() if c == top])
        healed = next((t for p, t in self.picks.items() if self.roles[p] == "doctor"), None)
        if victim is None:
            text = "☀️ Утро. Мафия проспала — все живы."
        elif victim == healed:
            text = "☀️ Утро. Мафия напала, но доктор успел спасти жертву — все живы!"
        else:
            self.alive.remove(victim)
            kind = "мафиози" if self.roles[victim] == "mafia" else "мирный"
            text = f"☀️ Утро. Ночью убит {self.players[victim]} ({kind})."
        self.log.append(text)
        self.outbox.append(f"{text}\nОбсуждайте в чате и голосуйте в мини-аппе ({DAY_SECONDS} сек).")
        if not self._check_end():
            self._day()

    def _resolve_day(self) -> None:
        counts: Dict[int, int] = {}
        for t in self.votes.values():
            if t:
                counts[t] = counts.get(t, 0) + 1
        top = max(counts.values()) if counts else 0
        leaders = [t for t, c in counts.items() if c == top]
        if len(leaders) != 1:
            text = "⚖️ Голоса разделились — никого не казнят."
        else:
            out = leaders[0]
            self.alive.remove(out)
            kind = "МАФИОЗИ 🔪" if self.roles[out] == "mafia" else "мирный житель 🙂"
            text = f"⚖️ Город казнил {self.players[out]} — это был {kind}."
        self.log.append(text)
        self.outbox.append(text)
        if not self._check_end():
            self._night()
            self.outbox.append(f"🌙 Наступила ночь {self.round}.")

    def _check_end(self) -> bool:
        mafia = len(self._mafia_alive())
        town = len(self.alive) - mafia
        if mafia == 0:
            self.winner = "town"
        elif mafia >= town:
            self.winner = "mafia"
        else:
            return False
        self.status = "done"
        self.updated = time.time()
        reveal = "\n".join(f"{ROLE_TITLE[self.roles[p]][0]} {n}" for p, n in self.players.items())
        head = "🔪 Побеждает МАФИЯ!" if self.winner == "mafia" else "🎉 Побеждают МИРНЫЕ!"
        self.outbox.append(f"{head} Победителям по +{REWARD} см.\n\nРоли:\n{reveal}")
        return True

    def winners(self) -> List[int]:
        if self.winner is None:
            return []
        return [p for p, r in self.roles.items() if (r == "mafia") == (self.winner == "mafia")]

    def expired(self) -> bool:
        age = time.time() - self.updated
        return (self.status == "open" and age > OPEN_TTL) or (self.status in ("night", "day") and age > IDLE_END)

    # ── представление ──
    def summary(self) -> dict:
        return {"rid": self.rid, "host_name": self.players[self.host], "count": len(self.players), "max": MAX_PLAYERS}

    def view(self, uid: int) -> dict:
        role = self.roles.get(uid)
        done = self.status == "done"
        out = {
            "rid": self.rid,
            "status": self.status,
            "round": self.round,
            "host": self.host,
            "you": uid,
            "min": MIN_PLAYERS,
            "left": max(0, int(self.deadline - time.time())) if self.status in ("night", "day") else 0,
            "players": [
                {
                    "id": p,
                    "name": n,
                    "alive": p in self.alive if self.roles else True,
                    "role": self.roles.get(p) if (done or p == uid) else None,
                    "mate": bool(role == "mafia" and self.roles.get(p) == "mafia" and p != uid),
                    "voted": (p in self.votes) if self.status == "day" else False,
                }
                for p, n in self.players.items()
            ],
            "log": self.log[-8:],
            "winner": self.winner,
            "winners": self.winners() if done else [],
            "reward": REWARD,
        }
        if role:
            icon, title, hint = ROLE_TITLE[role]
            out["role"] = {"key": role, "icon": icon, "title": title, "hint": hint}
            out["is_alive"] = uid in self.alive
            out["acted"] = uid in self.picks if self.status == "night" else uid in self.votes
            out["needs_night"] = self.status == "night" and role in ("mafia", "doctor", "sheriff") and uid in self.alive
            out["my_vote"] = self.votes.get(uid) if self.status == "day" else None
            out["my_pick"] = self.picks.get(uid) if self.status == "night" else None
            out["checks"] = self.checks.get(uid, [])
        return out
