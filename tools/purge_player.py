#!/usr/bin/env python3
"""Удаление данных игрока из мини-аппа («Район» и связанные функции).

Использование: purge_player.py <user_id> <имя_для_новостей> [--apply]
Без --apply — пробный прогон (ничего не меняет, печатает, сколько строк затронуто).
Остаются данные бота-чата: users, msg_stats, cmd_stats, size_log, quotes, lottery, daily_answers, quest_claims, trivia_wins, boss, boss_dmg, trial.
Структурные таблицы (gang, territory …) не трогаются: если игрок там записан, скрипт только предупреждает.
Идентификатор — 10-значное число, поэтому совпадение по любой целочисленной колонке безопасно.
"""
import json
import sqlite3
import sys

import os
DB = os.environ.get("PURGE_DB", "/opt/tgapp/bot_database.sqlite3")
KEEP = {"users", "msg_stats", "cmd_stats", "size_log", "quotes", "lottery", "daily_answers", "quest_claims", "trivia_wins", "boss", "boss_dmg", "trial"}
STRUCT = {"gang", "territory", "gang_war", "gang_pact", "racket", "season_ctl", "gang_stock"}
SKIP = {"kv", "rp_news", "outbox"}


def scrub(v, uid):
    """Убрать uid из JSON: ключи словаря, элементы списков; одиночные значения становятся 0."""
    if isinstance(v, dict):
        return {k: scrub(x, uid) for k, x in v.items() if str(k) != str(uid)}
    if isinstance(v, list):
        return [scrub(x, uid) for x in v if x != uid]
    return 0 if v == uid and isinstance(v, int) and not isinstance(v, bool) else v


def main():
    uid, name = int(sys.argv[1]), sys.argv[2]
    apply = "--apply" in sys.argv
    db = sqlite3.connect(DB, timeout=60)
    db.execute("PRAGMA busy_timeout = 60000")
    total = 0
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    for t in tables:
        if t in KEEP or t in SKIP:
            continue
        cols = [r[1] for r in db.execute(f"pragma table_info({t})") if "INT" in (r[2] or "").upper() or not r[2]]
        cond = " OR ".join(f"{c} = ?" for c in cols)
        if not cond:
            continue
        n = db.execute(f"select count(*) from {t} where {cond}", [uid] * len(cols)).fetchone()[0]
        if not n:
            continue
        if t in STRUCT:
            print(f"! {t}: {n} строк — структурная таблица, НЕ тронута, проверь вручную")
            continue
        print(f"  {t}: {n}")
        total += n
        if apply:
            db.execute(f"delete from {t} where {cond}", [uid] * len(cols))
    n = db.execute("select count(*) from outbox where chat_id = ?", (uid,)).fetchone()[0]
    print(f"  outbox (личка): {n}")
    if apply:
        db.execute("delete from outbox where chat_id = ?", (uid,))
    like = f"%{name.lstrip('@')}%"
    n = db.execute("select count(*) from rp_news where text like ?", (like,)).fetchone()[0]
    print(f"  rp_news (упоминания «{name}»): {n}")
    if apply:
        db.execute("delete from rp_news where text like ?", (like,))
    kd, kj = 0, 0
    for key, val in db.execute("select key, value from kv").fetchall():
        if str(uid) in key:
            kd += 1
            if apply:
                db.execute("delete from kv where key = ?", (key,))
        elif str(uid) in str(val):
            try:
                data = json.loads(val)
            except Exception:
                continue
            new = scrub(data, uid)
            if new != data:
                kj += 1
                if apply:
                    db.execute("update kv set value = ? where key = ?", (json.dumps(new, ensure_ascii=False), key))
    print(f"  kv: удалено ключей {kd}, очищено JSON-значений {kj}")
    print(("ГОТОВО (применено)" if apply else "ПРОБНЫЙ ПРОГОН (ничего не изменено)") + f": строк в таблицах {total}")
    if apply:
        db.commit()
    db.close()


main()
