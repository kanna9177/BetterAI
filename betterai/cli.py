"""Comandi: sync, daily, backtest, report, slips, team."""
import argparse
import json
from datetime import date, timedelta

from . import data, db, learn, markets, sisal, slips
from .model import fit_from_db


def cmd_sync(con, args):
    print("Scarico calendari e risultati...")
    data.sync(con)


def cmd_daily(con, args):
    """Ciclo giornaliero: dati nuovi -> verifica schedine -> nuove schedine."""
    today = args.date or date.today().isoformat()
    cmd_sync(con, args)
    closed = learn.settle(con, today)
    print(f"Giornate verificate: {', '.join(closed) or 'nessuna'}")
    for i in range(args.days_ahead + 1):
        day = (date.fromisoformat(today) + timedelta(days=i)).isoformat()
        n = slips.generate(con, fit_from_db(con, day), day, args.n, args.seed)
        if n:
            print(f"{day}: generate {n} schedine")


def cmd_backtest(con, args):
    """Rivive i giorni passati uno alla volta: pronostica senza vedere il risultato,
    poi verifica e impara, esattamente come nel ciclo giornaliero."""
    days = [r[0] for r in con.execute(
        """SELECT DISTINCT date FROM matches
           WHERE hg IS NOT NULL AND date >= ? AND date <= ? ORDER BY date""",
        (args.start, args.end or "9999"))]
    print(f"Simulo {len(days)} giornate...")
    for i, day in enumerate(days, 1):
        slips.generate(con, fit_from_db(con, day), day, args.n, args.seed)
        learn.settle(con, day)
        if i % 50 == 0:
            print(f"  {i}/{len(days)} ({day})")
    cmd_report(con, args)


def _pct(a, b):
    return f"{100 * a / b:5.1f}%" if b else "    -"


def cmd_report(con, args):
    t = con.execute(
        """SELECT COUNT(*) n, SUM(status='won') won, SUM(stake) st, SUM(payout) ret
           FROM slips WHERE status != 'open'""").fetchone()
    if not t["n"]:
        print("Nessuna schedina verificata.")
        return
    opened = con.execute("SELECT COUNT(*) FROM slips WHERE status='open'").fetchone()[0]
    print(f"\nSCHEDINE SIMULATE  (quote stimate dal modello, non reali)")
    print(f"  verificate {t['n']}, in attesa {opened}, vinte {t['won']} ({_pct(t['won'], t['n']).strip()})")
    print(f"  puntato {t['st']:.2f} EUR, rientrato {t['ret']:.2f} EUR, "
          f"saldo {t['ret'] - t['st']:+.2f} EUR ({100 * (t['ret'] / t['st'] - 1):+.1f}%)")

    print("\nPER STRATEGIA      schedine   vinte    quota media   saldo")
    for r in con.execute(
            """SELECT strategy, COUNT(*) n, SUM(status='won') won, AVG(odds) o,
                      SUM(stake) st, SUM(payout) ret
               FROM slips WHERE status != 'open' GROUP BY strategy ORDER BY strategy"""):
        print(f"  {r['strategy']:<14} {r['n']:>8}  {_pct(r['won'], r['n'])}  {r['o']:>11.2f}  "
              f"{100 * (r['ret'] / r['st'] - 1):+7.1f}%")

    print("\nANDAMENTO MENSILE  esiti presi / attesi    errore 1X2 modello / base")
    for r in con.execute(
            """SELECT substr(date,1,7) mo, SUM(legs) l, SUM(hits) h, SUM(exp_hits) e,
                      SUM(ll_model*matches)/SUM(matches) lm, SUM(ll_base*matches)/SUM(matches) lb
               FROM learning_log GROUP BY mo ORDER BY mo"""):
        ll = f"{r['lm']:.3f} / {r['lb']:.3f}" if r["lm"] is not None else "-"
        print(f"  {r['mo']}          {_pct(r['h'], r['l'])} / {_pct(r['e'], r['l'])}       {ll}")
    print("  (errore = log-loss: piu' basso e' meglio)")

    print("\nCORREZIONI IMPARATE (esiti dove il modello sbagliava di piu')")
    rows = con.execute("SELECT * FROM calibration WHERE n >= 200").fetchall()
    rows.sort(key=lambda r: -abs(r["hits"] - r["sum_p"]) / r["n"])
    for r in rows[:8]:
        print(f"  {markets.label(r['selection']):<42} previsto {_pct(r['sum_p'], r['n'])}"
              f"  reale {_pct(r['hits'], r['n'])}  ({r['n']} partite)")

    last = con.execute("SELECT * FROM learning_log ORDER BY date DESC LIMIT 1").fetchone()
    if last:
        print(f"\nULTIMA GIORNATA {last['date']}: {last['won']}/{last['slips']} schedine vinte")
        for x in json.loads(last["lessons"]):
            print(f"  - {x}")

    quote_src = con.execute(
        """SELECT COALESCE(odds_source, 'stimato') src, COUNT(*) n FROM legs
           GROUP BY src ORDER BY n DESC""").fetchall()
    if quote_src and sum(r["n"] for r in quote_src) > 0:
        tot = sum(r["n"] for r in quote_src)
        print("\nQUOTE USATE NELLE SCHEDINE (fonte / numero / %)")
        for r in quote_src:
            print(f"  {r['src']:<12} {r['n']:>7}  {100 * r['n'] / tot:5.1f}%")


def cmd_slips(con, args):
    day = args.date or con.execute("SELECT MAX(date) FROM slips").fetchone()[0]
    rows = con.execute("SELECT * FROM slips WHERE date = ? ORDER BY id LIMIT ?",
                       (day, args.limit)).fetchall()
    for s in rows:
        state = {"open": "IN ATTESA", "won": "VINTA", "lost": "PERSA"}[s["status"]]
        print(f"\n#{s['id']} {s['date']} [{s['strategy']}] quota {s['odds']:.2f} "
              f"- puntata {s['stake']:.2f} EUR - vincita potenziale "
              f"{s['odds'] * s['stake']:.2f} EUR - {state}")
        for l in con.execute(
                """SELECT l.*, h.name home, a.name away FROM legs l
                   JOIN matches m ON m.id = l.match_id
                   JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
                   WHERE slip_id = ?""", (s["id"],)):
            mark = {None: " ", 1: "V", 0: "X", -1: "-"}[l["result"]]
            print(f"  {mark} {l['home']} - {l['away']}: {markets.label(l['selection'])}"
                  f"  @{l['odds']:.2f} ({100 * l['prob']:.0f}%)")
            if l["reason"]:
                print(f"      {l['reason']}")


def cmd_sisal_sync(con, args):
    sisal.sync(con, comp=args.comp, curl_file=args.curl,
               json_file=args.json, disciplina=args.disciplina)


def cmd_sisal_probe(con, args):
    """Scarica (o rilegge) la risposta grezza di Sisal, parsa e stampa un sommario.
    --json: legge un file locale invece di fare la fetch.
    --curl: replay di un 'Copy as cURL (bash)' da DevTools (per cookies akamai)."""
    try:
        if args.json:
            payload = json.load(open(args.json, encoding="utf-8"))
        elif args.curl:
            with open(args.curl, encoding="utf-8") as f:
                payload = sisal.fetch_from_curl(f.read())
        else:
            payload = sisal.fetch(path=args.path, disciplina=args.disciplina)
    except Exception as e:
        print(f"errore: {e}")
        return
    out = args.dump or "sisal_probe.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    events = sisal.parse_events(payload)
    print(f"salvato {out} ({len(json.dumps(payload))} byte)")
    print(f"partite estratte: {len(events)}")
    if events:
        with_quotes = sum(1 for e in events if e["quotes"])
        n_quotes = sum(len(e["quotes"]) for e in events)
        e = events[0]
        print(f"  con quote: {with_quotes}, quote totali: {n_quotes}")
        print(f"  prima: {e['date']} {e['time'] or ''} {e['home']} - {e['away']}, "
              f"{len(e['quotes'])} quote")


def cmd_web(con, args):
    con.close()  # web app apre la sua connessione per ogni richiesta
    from . import web
    print(f"BetterAI web su http://{args.host}:{args.port}  (db: {args.db})")
    web.create_app(args.db).run(host=args.host, port=args.port, debug=args.debug)


def cmd_team(con, args):
    t = con.execute("SELECT * FROM teams WHERE name LIKE ?", (f"%{args.name}%",)).fetchone()
    if not t:
        print("Squadra non trovata.")
        return
    model = fit_from_db(con, date.today().isoformat())
    print(f"{t['name']}: attacco {model.att.get(t['id'], 1):.2f}, "
          f"difesa {model.dfn.get(t['id'], 1):.2f} (1.00 = media; difesa bassa = subisce poco)")
    for side, mine, theirs, col in (("casa", "hg", "ag", "home_id"),
                                    ("trasferta", "ag", "hg", "away_id")):
        r = con.execute(
            f"""SELECT COUNT(*) n, SUM({mine} > {theirs}) w, SUM({mine} = {theirs}) d,
                       SUM({mine} < {theirs}) l, SUM({mine}) gf, SUM({theirs}) ga
                FROM (SELECT * FROM matches WHERE {col} = ? AND hg IS NOT NULL
                      ORDER BY date DESC LIMIT 19)""", (t["id"],)).fetchone()
        if r["n"]:
            print(f"  ultime {r['n']} in {side}: {r['w']}V {r['d']}N {r['l']}P, "
                  f"gol {r['gf']}-{r['ga']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="betterai", description=__doc__)
    ap.add_argument("--db", default=db.DEFAULT_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="scarica partite e risultati").set_defaults(fn=cmd_sync)
    for name, fn, hlp in (("daily", cmd_daily, "ciclo giornaliero completo"),
                          ("backtest", cmd_backtest, "simula i giorni passati")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("-n", type=int, default=50, help="schedine al giorno")
        p.add_argument("--seed", default="betterai")
        p.set_defaults(fn=fn)
        if name == "daily":
            p.add_argument("--date", help="oggi (AAAA-MM-GG)")
            p.add_argument("--days-ahead", type=int, default=2)
        else:
            p.add_argument("--start", required=True, help="primo giorno (AAAA-MM-GG)")
            p.add_argument("--end")
    sub.add_parser("report", help="risultati e cosa ha imparato").set_defaults(fn=cmd_report)
    p = sub.add_parser("slips", help="mostra le schedine di un giorno")
    p.add_argument("--date")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(fn=cmd_slips)
    p = sub.add_parser("team", help="scheda di una squadra")
    p.add_argument("name")
    p.set_defaults(fn=cmd_team)
    p = sub.add_parser("sisal-sync", help="scarica quote reali da Sisal (vedi betterai/sisal.py)")
    p.add_argument("--comp", default="it.1")
    p.add_argument("--curl", help="file con un 'Copy as cURL (bash)' da DevTools")
    p.add_argument("--json", help="file JSON gia' salvato (offline / replay)")
    p.add_argument("--disciplina", type=int, default=1, help="codice Sisal (1 = calcio)")
    p.set_defaults(fn=cmd_sisal_sync)
    p = sub.add_parser("web", help="dashboard web (richiede 'flask')")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(fn=cmd_web)
    p = sub.add_parser("sisal-probe", help="dump della risposta grezza Sisal e sommario")
    p.add_argument("--path", help="override di SISAL_PATH")
    p.add_argument("--curl", help="file con un 'Copy as cURL (bash)' da DevTools")
    p.add_argument("--json", help="file JSON gia' salvato (offline / replay)")
    p.add_argument("--dump", help="file dove scrivere la risposta (default sisal_probe.json)")
    p.add_argument("--disciplina", type=int, default=1, help="codice Sisal (1 = calcio)")
    p.set_defaults(fn=cmd_sisal_probe)
    args = ap.parse_args(argv)
    con = db.connect(args.db)
    args.fn(con, args)
    con.close()
