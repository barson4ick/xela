"""Сводка баланса экономики «Района». Запуск: cd /opt/tgapp && venv/bin/python econ_report.py [дней]
Читает базу только на чтение: снимки денежной массы (kv snap:*) и счётчики потоков (kv flow:*)."""
import json
import sqlite3
import sys
import os
DB_PATH = os.environ.get("DB_PATH", "/opt/tgapp/bot_database.sqlite3")

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
FLOW_NAMES = {"work": "зарплата с работы (после налога)", "work_tax": "налог банд с работы", "upkeep_wealth": "содержание имущества и налог на богатство",
              "gang_upkeep": "штаб, налог на общак, охрана точек", "jail_costs": "цена тюрьмы для банд", "spa": "санаторий", "buyout": "откуп от налога банд", "rebels": "бунтов точек", "charity": "благотворительность", "welfare": "пособия", "stall_fines": "штрафы ларькам",
              "ai_help": "ИИ-помощники в общак", "ai_police": "ИИ-полиция в фонд", "ai_patron": "ИИ-патроны"}

c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
chats = [r[0] for r in c.execute("SELECT DISTINCT chat_id FROM rp_player")]
for chat in chats:
    print(f"═══ Чат {chat} ═══")
    n, money = c.execute("SELECT COUNT(*), COALESCE(SUM(money),0) FROM rp_player WHERE chat_id=? AND user_id>0", (chat,)).fetchone()
    print(f"Игроков: {n}, на руках: {money} ₽ (среднее {money // max(1, n)})")
    rows = c.execute("SELECT money FROM rp_player WHERE chat_id=? AND user_id>0 ORDER BY money DESC", (chat,)).fetchall()
    vals = [r[0] for r in rows]
    if vals:
        top = vals[0]
        print(f"Богатейший: {top} ₽, беднейший: {vals[-1]} ₽, медиана: {vals[len(vals)//2]} ₽, доля богатейшего: {100 * top // max(1, sum(vals))}%")
    print("Банды:")
    for gid, name, is_ai, tr in c.execute("SELECT id, name, is_ai, treasury FROM gang WHERE chat_id=? ORDER BY treasury DESC", (chat,)):
        mem = c.execute("SELECT COUNT(*) FROM gang_member WHERE gang_id=? AND user_id>0", (gid,)).fetchone()[0]
        terr = c.execute("SELECT COUNT(*) FROM territory WHERE owner=?", (gid,)).fetchone()[0]
        pts = c.execute("SELECT COUNT(*) FROM racket WHERE owner=?", (gid,)).fetchone()[0]
        print(f"  {'🤖' if is_ai else '🕴'} {name}: общак {tr} ₽, людей {mem}, районов {terr}, точек {pts}")
    pf = c.execute("SELECT value FROM kv WHERE key=?", (f"pfund:{chat}",)).fetchone()
    print(f"Фонд полиции: {pf[0] if pf else 0} ₽")
    snaps = []
    for k, v in c.execute("SELECT key, value FROM kv WHERE key LIKE ? ORDER BY key", (f"snap:{chat}:%",)):
        snaps.append((k.rsplit(":", 1)[1], json.loads(v)))
    snaps = snaps[-(DAYS + 1):]
    print("\nДенежная масса (утренние снимки):")
    prev = None
    for day, s in snaps:
        total = s["money"] + s["gangs"] + s.get("stash", 0)
        delta = "" if prev is None else f"  Δ {total - prev:+d}"
        print(f"  {day}: игроки {s['money']}, общаки игроков {s['gangs']}, тайники {s.get('stash', 0)}, ИИ-банды {s['ai_gangs']}, фонд полиции {s['pfund']} → всего {total}{delta}")
        prev = total
    print("\nПотоки по дням (счётчики):")
    for k, v in c.execute("SELECT key, value FROM kv WHERE key LIKE ? ORDER BY key", (f"flow:{chat}:%",)):
        day = k.rsplit(":", 1)[1]
        d = json.loads(v)
        print(f"  {day}: " + "; ".join(f"{FLOW_NAMES.get(a, a)} {b}" for a, b in sorted(d.items())))
    print("\nВывод: если денежная масса растёт быстрее, чем потоки-стоки (содержание, налоги, санаторий, откуп) её съедают, на руках копятся деньги, и пора поднимать расходы;")
    print("если масса падает и игроки в минусе по работе, нужно смягчить стоки. Сравнивай Δ по дням и долю богатейшего.")
