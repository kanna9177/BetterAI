"""Verifica le schedine sui risultati reali e aggiorna cio' che il sistema ha imparato."""
import json
import math
from collections import Counter
from datetime import date, timedelta

from . import markets

VOID_AFTER_DAYS = 10


def _settle_predictions(con):
    rows = con.execute(
        """SELECT p.match_id, p.selection, p.prob, m.hg, m.ag
           FROM predictions p JOIN matches m ON m.id = p.match_id
           WHERE p.result IS NULL AND m.hg IS NOT NULL""").fetchall()
    for r in rows:
        hit = int(not markets.failed_atoms(r["selection"], r["hg"], r["ag"]))
        con.execute("UPDATE predictions SET result = ? WHERE match_id = ? AND selection = ?",
                    (hit, r["match_id"], r["selection"]))
        con.execute(
            """INSERT INTO calibration(selection, n, hits, sum_p) VALUES (?,1,?,?)
               ON CONFLICT(selection) DO UPDATE SET
                 n = n + 1, hits = hits + excluded.hits, sum_p = sum_p + excluded.sum_p""",
            (r["selection"], hit, r["prob"]))


def _settle_legs(con, today):
    rows = con.execute(
        """SELECT l.id, l.selection, l.xg_home, l.xg_away, m.hg, m.ag, m.date
           FROM legs l JOIN matches m ON m.id = l.match_id
           WHERE l.result IS NULL""").fetchall()
    limit = (date.fromisoformat(today) - timedelta(days=VOID_AFTER_DAYS)).isoformat()
    for r in rows:
        if r["hg"] is None:
            if r["date"] < limit:
                con.execute("UPDATE legs SET result = -1, reason = ? WHERE id = ?",
                            ("partita non giocata: evento annullato", r["id"]))
            continue
        failed = markets.failed_atoms(r["selection"], r["hg"], r["ag"])
        reason = None
        if failed:
            reason = (f"finale {r['hg']}-{r['ag']} (gol attesi "
                      f"{r['xg_home']:.1f}-{r['xg_away']:.1f}): fallito "
                      + ", ".join(markets.label(x) for x in failed))
        con.execute("UPDATE legs SET result = ?, failed = ?, reason = ? WHERE id = ?",
                    (int(not failed), ",".join(failed) or None, reason, r["id"]))


def _settle_slips(con):
    days = set()
    for s in con.execute("SELECT id, date, stake FROM slips WHERE status = 'open'").fetchall():
        legs = con.execute("SELECT result, odds FROM legs WHERE slip_id = ?",
                           (s["id"],)).fetchall()
        if any(l["result"] is None for l in legs):
            continue
        if any(l["result"] == 0 for l in legs):
            status, payout = "lost", 0.0
        else:
            status, payout = "won", s["stake"]
            for l in legs:
                if l["result"] == 1:
                    payout *= l["odds"]
        con.execute("UPDATE slips SET status = ?, payout = ? WHERE id = ?",
                    (status, round(payout, 2), s["id"]))
        days.add(s["date"])
    return days


def _log_day(con, day, base):
    if con.execute("SELECT 1 FROM slips WHERE date = ? AND status = 'open' LIMIT 1",
                   (day,)).fetchone():
        return
    s = con.execute(
        """SELECT COUNT(*) n, SUM(status = 'won') won, SUM(stake) staked, SUM(payout) ret
           FROM slips WHERE date = ?""", (day,)).fetchone()
    legs = con.execute(
        """SELECT DISTINCT l.match_id, l.selection, l.prob, l.result, l.failed
           FROM legs l JOIN slips s ON s.id = l.slip_id
           WHERE s.date = ? AND l.result >= 0""", (day,)).fetchall()
    fails = Counter(a for l in legs if l["failed"] for a in l["failed"].split(","))
    # Qualita' del pronostico 1X2 contro una base che usa solo le frequenze storiche.
    ll_model = ll_base = 0.0
    matches = con.execute(
        "SELECT id, hg, ag FROM matches WHERE date = ? AND hg IS NOT NULL", (day,)).fetchall()
    n_eval = 0
    for m in matches:
        sign = "1" if m["hg"] > m["ag"] else "2" if m["ag"] > m["hg"] else "X"
        p = con.execute("SELECT prob FROM predictions WHERE match_id = ? AND selection = ?",
                        (m["id"], f"R:{sign}")).fetchone()
        if p:
            ll_model -= math.log(max(p["prob"], 1e-6))
            ll_base -= math.log(base[sign])
            n_eval += 1
    lessons = [f"{markets.label(a)} fallito {c} volte" for a, c in fails.most_common(3)]
    con.execute(
        "INSERT OR REPLACE INTO learning_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (day, s["n"], s["won"], s["staked"], s["ret"], len(legs),
         sum(l["result"] for l in legs), sum(l["prob"] for l in legs), n_eval,
         ll_model / n_eval if n_eval else None, ll_base / n_eval if n_eval else None,
         json.dumps(lessons, ensure_ascii=False)))


def settle(con, today):
    """Chiude tutto cio' che ha un risultato. Ritorna i giorni di schedine chiusi."""
    _settle_predictions(con)
    _settle_legs(con, today)
    days = _settle_slips(con)
    if days:
        r = con.execute(
            """SELECT SUM(hg > ag) h, SUM(hg = ag) d, SUM(hg < ag) a, COUNT(*) n
               FROM matches WHERE hg IS NOT NULL""").fetchone()
        base = {"1": r["h"] / r["n"], "X": r["d"] / r["n"], "2": r["a"] / r["n"]}
        for day in sorted(days):
            _log_day(con, day, base)
    con.commit()
    return sorted(days)
