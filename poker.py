"""Покер (техасский холдем) на сантиметры для мини-аппа: столы на 2–6 игроков, фишка = 1 см."""
import random
import time
import uuid
from itertools import combinations
from typing import Dict, List, Optional

from rooms import GameError

MAX_SEATS = 6
TURN_SECONDS = 30
BREAK_SECONDS = 8        # пауза между раздачами (на ней показан итог)
IDLE_CLOSE = 600         # стол без действий закрываем
MAX_TIMEOUTS = 2         # столько пропущенных ходов подряд — и игрока убирают
LEVELS = {
    "small": {"title": "Малый стол", "buyin": 20, "sb": 1, "bb": 2},
    "big": {"title": "Большой стол", "buyin": 50, "sb": 2, "bb": 5},
}

RANKS = "23456789TJQKA"
SUITS = "♠♥♦♣"
CAT_NAME = ["старшая карта", "пара", "две пары", "тройка", "стрит", "флеш", "фулл-хаус", "каре", "стрит-флеш"]


def card_str(c: int) -> str:
    return RANKS[c // 4].replace("T", "10") + SUITS[c % 4]


def _rank5(cards: List[int]) -> tuple:
    ranks = sorted((c // 4 for c in cards), reverse=True)
    flush = len({c % 4 for c in cards}) == 1
    uniq = sorted(set(ranks), reverse=True)
    straight = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight = uniq[0]
        elif uniq == [12, 3, 2, 1, 0]:  # колесо A-2-3-4-5
            straight = 3
    groups = sorted(((ranks.count(r), r) for r in uniq), reverse=True)
    counts = [g[0] for g in groups]
    tiebreak = [g[1] for g in groups]
    if straight and flush:
        return (8, straight)
    if counts[0] == 4:
        return (7, *tiebreak)
    if counts[:2] == [3, 2]:
        return (6, *tiebreak)
    if flush:
        return (5, *ranks)
    if straight:
        return (4, straight)
    if counts[0] == 3:
        return (3, *tiebreak)
    if counts[:2] == [2, 2]:
        return (2, *tiebreak)
    if counts[0] == 2:
        return (1, *tiebreak)
    return (0, *ranks)


def best_hand(cards: List[int]) -> tuple:
    return max(_rank5(list(c)) for c in combinations(cards, 5))


class Seat:
    def __init__(self, uid: int, name: str, chips: int) -> None:
        self.uid, self.name, self.chips = uid, name, chips
        self.hole: List[int] = []
        self.folded = True      # вне раздачи, пока не раздали
        self.allin = False
        self.bet = 0            # ставка на текущей улице
        self.paid = 0           # вложено за раздачу
        self.acted = False
        self.timeouts = 0
        self.leaving = False


class PokerTable:
    def __init__(self, chat_id: int, host: int, host_name: str, level: str) -> None:
        cfg = LEVELS[level]
        self.tid = uuid.uuid4().hex[:8]
        self.chat_id = chat_id
        self.level = level
        self.buyin, self.sb, self.bb = cfg["buyin"], cfg["sb"], cfg["bb"]
        self.seats: List[Seat] = []
        self.dealer = -1
        self.phase = "wait"      # wait | pre | flop | turn | river | break
        self.board: List[int] = []
        self.deck: List[int] = []
        self.cur_bet = 0
        self.min_raise = self.bb
        self.to_act = -1
        self.deadline = 0.0
        self.next_at = 0.0
        self.last: Optional[dict] = None   # итог последней раздачи
        self.hand_no = 0
        self.created = self.updated = time.time()
        self.outbox: List[str] = []
        self.finished: List[dict] = []     # итоги раздач для квеста и ленты подвигов
        self.cashouts: List[tuple] = []    # (uid, name, chips) — серверу надо вернуть в см
        self.host = host
        self.add(host, host_name)

    # ── места ──
    def seat_of(self, uid: int) -> Optional[Seat]:
        return next((s for s in self.seats if s.uid == uid and not s.leaving), None)

    def add(self, uid: int, name: str) -> None:
        if self.seat_of(uid):
            raise GameError("Ты уже за этим столом")
        if len(self.seats) >= MAX_SEATS:
            raise GameError("За столом нет мест")
        self.seats.append(Seat(uid, name, self.buyin))
        self.updated = time.time()
        if self.phase == "wait":
            self.next_at = time.time() + 3

    def leave(self, uid: int) -> None:
        s = self.seat_of(uid)
        if not s:
            return
        self.updated = time.time()
        if self.phase not in ("pre", "flop", "turn", "river"):
            self._remove(s)
            return
        # посреди раздачи место не убираем: фишки вернём сразу, вложенное остаётся в банке
        was_turn = 0 <= self.to_act < len(self.seats) and self.seats[self.to_act] is s
        s.leaving = True
        self._payout_leaver(s)
        if not s.folded:
            s.folded = True
            if was_turn:
                self._advance()
            else:
                self._check_lone_winner()

    def _payout_leaver(self, s: Seat) -> None:
        if s.chips:
            self.cashouts.append((s.uid, s.name, s.chips))
            s.chips = 0

    def _remove(self, s: Seat) -> None:
        if s.chips:
            self.cashouts.append((s.uid, s.name, s.chips))
            s.chips = 0
        idx = self.seats.index(s)
        self.seats.remove(s)
        if idx <= self.dealer:
            self.dealer -= 1
        if self.to_act >= len(self.seats):
            self.to_act = -1

    def expired(self) -> bool:
        return time.time() - self.updated > IDLE_CLOSE

    def close_all(self) -> None:
        for s in list(self.seats):
            if s.chips:
                self.cashouts.append((s.uid, s.name, s.chips))
                s.chips = 0
        self.seats.clear()

    # ── ход игры ──
    def tick(self) -> None:
        for _ in range(30):
            now = time.time()
            if self.phase in ("pre", "flop", "turn", "river"):
                if now < self.deadline:
                    return
                s = self.seats[self.to_act]
                s.timeouts += 1
                if s.timeouts >= MAX_TIMEOUTS:
                    s.folded = True
                    s.leaving = True
                    self._payout_leaver(s)
                    self.outbox.append(f"⏰ {s.name} не отвечает и вылетает из-за стола")
                    self._advance()
                elif s.bet >= self.cur_bet:
                    self._apply(s, "check", 0, auto=True)
                else:
                    self._apply(s, "fold", 0, auto=True)
                continue
            if self.phase in ("wait", "break"):
                if self.phase == "break" and now >= self.next_at:
                    self._cleanup_after_hand()
                if self.phase in ("wait", "break") and now >= self.next_at and len([x for x in self.seats if x.chips > 0]) >= 2:
                    self._start_hand()
                    continue
            return

    def _cleanup_after_hand(self) -> None:
        for s in list(self.seats):
            if s.leaving or s.chips <= 0:
                self._remove(s)
        self.phase = "wait"
        self.next_at = time.time()
        if self.seats and self.host not in [s.uid for s in self.seats]:
            self.host = self.seats[0].uid

    def _live(self) -> List[Seat]:
        return [s for s in self.seats if not s.folded]

    def _next_idx(self, start: int, pred) -> int:
        n = len(self.seats)
        for k in range(1, n + 1):
            i = (start + k) % n
            if pred(self.seats[i]):
                return i
        return -1

    def _start_hand(self) -> None:
        self.hand_no += 1
        self.updated = time.time()
        self.last = None
        self.board = []
        self.deck = list(range(52))
        random.shuffle(self.deck)
        for s in self.seats:
            s.hole, s.bet, s.paid, s.acted, s.allin = [], 0, 0, False, False
            s.folded = s.chips <= 0
        playing = [s for s in self.seats if not s.folded]
        self.dealer = self._next_idx(self.dealer, lambda s: not s.folded)
        for s in playing:
            s.hole = [self.deck.pop(), self.deck.pop()]
        heads_up = len(playing) == 2
        sb_i = self.dealer if heads_up else self._next_idx(self.dealer, lambda s: not s.folded)
        bb_i = self._next_idx(sb_i, lambda s: not s.folded)
        self._post(self.seats[sb_i], self.sb)
        self._post(self.seats[bb_i], self.bb)
        self.cur_bet = self.bb
        self.min_raise = self.bb
        self.phase = "pre"
        self.to_act = self._next_idx(bb_i, lambda s: not s.folded and not s.allin)
        if self.to_act == -1 or self._round_done():
            self._advance(first=True)
        else:
            self.deadline = time.time() + TURN_SECONDS

    def _post(self, s: Seat, amount: int) -> None:
        amt = min(amount, s.chips)
        s.chips -= amt
        s.bet += amt
        s.paid += amt
        if s.chips == 0:
            s.allin = True

    def _round_done(self) -> bool:
        for s in self.seats:
            if s.folded or s.allin:
                continue
            if not s.acted or s.bet < self.cur_bet:
                return False
        return True

    # ── действия игрока ──
    def act(self, uid: int, action: str, amount: int = 0) -> None:
        self.tick()
        if self.phase not in ("pre", "flop", "turn", "river"):
            raise GameError("Сейчас не идёт торговля")
        s = self.seats[self.to_act]
        if s.uid != uid:
            raise GameError("Сейчас не твой ход")
        self._apply(s, action, amount)
        s.timeouts = 0
        self.updated = time.time()

    def _apply(self, s: Seat, action: str, amount: int, auto: bool = False) -> None:
        to_call = self.cur_bet - s.bet
        if action == "fold":
            s.folded = True
        elif action == "check":
            if to_call > 0:
                raise GameError("Нельзя чекнуть: нужно уравнять ставку")
            s.acted = True
        elif action == "call":
            if to_call <= 0:
                s.acted = True
            else:
                amt = min(to_call, s.chips)
                s.chips -= amt
                s.bet += amt
                s.paid += amt
                s.allin = s.chips == 0
                s.acted = True
        elif action == "raise":
            target = int(amount)
            max_to = s.bet + s.chips
            if target > max_to:
                raise GameError("У тебя нет столько фишек")
            if target <= self.cur_bet:
                raise GameError("Рейз должен быть больше текущей ставки")
            full = target - self.cur_bet >= self.min_raise
            if not full and target != max_to:
                raise GameError(f"Минимальный рейз до {self.cur_bet + self.min_raise}")
            add = target - s.bet
            s.chips -= add
            s.bet = target
            s.paid += add
            s.allin = s.chips == 0
            if full:
                self.min_raise = target - self.cur_bet
            self.cur_bet = target
            for o in self.seats:
                if o is not s and not o.folded and not o.allin:
                    o.acted = False
            s.acted = True
        else:
            raise GameError("Неизвестное действие")
        if self._check_lone_winner():
            return
        self._advance()

    def _check_lone_winner(self) -> bool:
        live = self._live()
        if len(live) == 1 and self.phase in ("pre", "flop", "turn", "river"):
            self._finish(showdown=False)
            return True
        return False

    def _advance(self, first: bool = False) -> None:
        if self._check_lone_winner():
            return
        able_now = [x for x in self.seats if not x.folded and not x.allin]
        lone_ok = len(able_now) == 1 and able_now[0].bet >= self.cur_bet  # торговаться больше не с кем
        if not first and not lone_ok:
            nxt = self._next_idx(self.to_act, lambda x: not x.folded and not x.allin and (not x.acted or x.bet < self.cur_bet))
            if nxt != -1:
                self.to_act = nxt
                self.deadline = time.time() + TURN_SECONDS
                return
        # улица закрыта
        for s in self.seats:
            s.bet, s.acted = 0, False
        self.cur_bet, self.min_raise = 0, self.bb
        able = [s for s in self.seats if not s.folded and not s.allin]
        while True:
            if self.phase == "river":
                self._finish(showdown=True)
                return
            nxt_phase = {"pre": "flop", "flop": "turn", "turn": "river"}[self.phase]
            self.deck.pop()  # сжигаем карту
            self.board += [self.deck.pop() for _ in range(3 if nxt_phase == "flop" else 1)]
            self.phase = nxt_phase
            if len(able) >= 2:
                self.to_act = self._next_idx(self.dealer, lambda x: not x.folded and not x.allin)
                self.deadline = time.time() + TURN_SECONDS
                return
            # торговаться некому — открываем карты до конца

    def _finish(self, showdown: bool) -> None:
        live = self._live()
        pots = self._pots()
        result: List[dict] = []
        scores = {s: best_hand(s.hole + self.board) for s in live} if showdown else {}
        won: Dict[Seat, int] = {}
        for amount, eligible in pots:
            elig = [s for s in eligible if not s.folded]
            if not elig:
                elig = live  # «мёртвые» деньги сбежавшего игрока достаются оставшимся
            if showdown:
                top = max(scores[s] for s in elig)
                winners = [s for s in elig if scores[s] == top]
            else:
                winners = elig
            share, extra = divmod(amount, len(winners))
            order = sorted(winners, key=lambda s: (self.seats.index(s) - self.dealer - 1) % len(self.seats))
            for i, w in enumerate(order):
                won[w] = won.get(w, 0) + share + (1 if i < extra else 0)
        for s in self.seats:
            s.chips += won.get(s, 0)
            if s.leaving and s.chips:
                self._payout_leaver(s)
        for s, amount in won.items():
            result.append({
                "uid": s.uid, "name": s.name, "amount": amount,
                "hand": CAT_NAME[scores[s][0]] if showdown else "",
            })
        self.last = {
            "hand": self.hand_no,
            "showdown": showdown,
            "winners": sorted(result, key=lambda r: -r["amount"]),
            "reveal": {s.uid: [card_str(c) for c in s.hole] for s in live} if showdown else {},
            "board": [card_str(c) for c in self.board],
        }
        big = max((r["amount"] for r in result), default=0)
        if big >= self.buyin:
            top = self.last["winners"][0]
            how = f" ({top['hand']})" if top["hand"] else ""
            self.outbox.append(f"🃏 <b>Покер:</b> {top['name']} забирает банк <b>{top['amount']} см</b>{how}")
        self.finished.append({"uids": [s.uid for s in self.seats if s.hole], "top": self.last["winners"][0] if self.last["winners"] else None})
        self.phase = "break"
        self.next_at = time.time() + BREAK_SECONDS
        self.deadline = 0.0
        self.to_act = -1

    def _pots(self) -> List[tuple]:
        levels = sorted({s.paid for s in self.seats if s.paid > 0})
        pots, prev = [], 0
        for lvl in levels:
            amount = sum(min(s.paid, lvl) - min(s.paid, prev) for s in self.seats)
            eligible = [s for s in self.seats if s.paid >= lvl]
            if amount:
                pots.append((amount, eligible))
            prev = lvl
        return pots

    # ── представление ──
    def summary(self) -> dict:
        return {"tid": self.tid, "level": self.level, "title": LEVELS[self.level]["title"], "buyin": self.buyin,
                "sb": self.sb, "bb": self.bb, "count": len(self.seats), "max": MAX_SEATS,
                "names": [s.name for s in self.seats]}

    def view(self, uid: int) -> dict:
        now = time.time()
        in_play = self.phase in ("pre", "flop", "turn", "river")
        pot = sum(s.paid for s in self.seats)
        me = self.seat_of(uid)
        turn_uid = self.seats[self.to_act].uid if in_play and 0 <= self.to_act < len(self.seats) else 0
        reveal = (self.last or {}).get("reveal", {}) if self.phase == "break" else {}
        seats = []
        for i, s in enumerate(self.seats):
            if s.leaving:
                continue
            if s.uid == uid and s.hole:
                cards = [card_str(c) for c in s.hole]
            elif s.uid in reveal:
                cards = reveal[s.uid]
            elif in_play and s.hole and not s.folded:
                cards = ["?", "?"]
            else:
                cards = []
            seats.append({
                "id": s.uid, "name": s.name, "chips": s.chips, "bet": s.bet,
                "folded": s.folded and bool(s.hole), "allin": s.allin, "cards": cards,
                "dealer": i == self.dealer, "turn": s.uid == turn_uid,
                "sitting": in_play and not s.hole,
            })
        to_call = max(0, self.cur_bet - me.bet) if me else 0
        data = {
            "tid": self.tid, "level": self.level, "title": LEVELS[self.level]["title"], "buyin": self.buyin,
            "sb": self.sb, "bb": self.bb, "phase": self.phase, "board": [card_str(c) for c in self.board],
            "pot": pot, "seats": seats, "you": uid, "last": self.last, "host": self.host,
            "left": max(0, int(self.deadline - now)) if in_play else max(0, int(self.next_at - now)),
            "your_turn": bool(me and turn_uid == uid),
        }
        if me and data["your_turn"]:
            can_raise = me.chips > to_call
            data.update(
                to_call=min(to_call, me.chips), can_raise=can_raise,
                min_raise_to=min(self.cur_bet + self.min_raise, me.bet + me.chips),
                max_raise_to=me.bet + me.chips, pot_raise_to=min(me.bet + me.chips, self.cur_bet + max(pot + to_call, self.min_raise)),
                cur_bet=self.cur_bet, my_bet=me.bet,
            )
        return data
