"""Mini-dashboard web per vedere partite, quote reali, schedine e modello.

Non e' una copia visuale di Sisal: stessa logica di navigazione (sport ->
palinsesto -> partita -> schedina) ma UI sobria, niente loghi di terzi.
Richiede 'flask' (unico dip oltre numpy, gia' richiesto dal modello).
"""
import json
import socket
from datetime import date, datetime, timedelta

from flask import Flask, redirect, render_template, request, url_for

from . import db, markets, sisal, slips


def local_ip():
    """Restituisce l'IP della macchina sulla LAN, per aprire la dashboard dal telefono."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _row_dict(r):
    return {k: r[k] for k in r.keys()} if r else None


def create_app(db_path=db.DEFAULT_PATH):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path

    @app.context_processor
    def inject_nav():
        return {"today": date.today().isoformat(), "navcls": {}}

    def con():
        return db.connect(app.config["DB_PATH"])

    @app.route("/")
    def home():
        return redirect(url_for("oggi"))

    @app.route("/oggi")
    def oggi():
        day = request.args.get("date") or date.today().isoformat()
        c = con()
        rows = c.execute(
            """SELECT m.id, m.date, m.time, m.comp, m.hg, m.ag,
                      h.name home, a.name away,
                      (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:1' LIMIT 1) q1,
                      (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:X' LIMIT 1) qx,
                      (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:2' LIMIT 1) q2,
                      (SELECT odds FROM odds WHERE match_id=m.id AND selection='TG:O2.5' LIMIT 1) qo,
                      (SELECT odds FROM odds WHERE match_id=m.id AND selection='TG:U2.5' LIMIT 1) qu,
                      (SELECT odds FROM odds WHERE match_id=m.id AND selection='BTTS:Y' LIMIT 1) qby,
                      (SELECT odds FROM odds WHERE match_id=m.id AND selection='BTTS:N' LIMIT 1) qbn
               FROM matches m
               JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
               WHERE m.date = ? ORDER BY m.time, m.id""", (day,)).fetchall()
        prev_d = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        next_d = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        c.close()
        return render_template("oggi.html", day=day, rows=rows,
                               prev_d=prev_d, next_d=next_d, active="oggi")

    @app.route("/partita/<int:match_id>")
    def partita(match_id):
        c = con()
        m = c.execute(
            """SELECT m.*, h.name home, a.name away FROM matches m
               JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
               WHERE m.id = ?""", (match_id,)).fetchone()
        if not m:
            c.close()
            return "partita sconosciuta", 404
        quotes = c.execute(
            """SELECT selection, odds, source, fetched_at FROM odds
               WHERE match_id = ? ORDER BY selection""", (match_id,)).fetchall()
        preds = c.execute(
            "SELECT selection, prob FROM predictions WHERE match_id = ?",
            (match_id,)).fetchall()
        pred_map = {p["selection"]: p["prob"] for p in preds}
        grouped = {}
        for q in quotes:
            fam = markets.family(q["selection"])
            grouped.setdefault(fam, []).append({
                "sel": q["selection"], "label": markets.label(q["selection"]),
                "odds": q["odds"], "source": q["source"],
                "prob": pred_map.get(q["selection"]),
            })
        c.close()
        return render_template("partita.html", m=m, grouped=grouped, active="oggi")

    @app.route("/schedine")
    def schedine():
        day = request.args.get("date") or date.today().isoformat()
        c = con()
        # fallback: ultimo giorno con schedine se oggi vuoto
        has_today = c.execute(
            "SELECT 1 FROM slips WHERE date = ? LIMIT 1", (day,)).fetchone()
        if not has_today:
            last = c.execute("SELECT MAX(date) FROM slips").fetchone()[0]
            if last:
                day = last
        slip_rows = c.execute(
            """SELECT * FROM slips WHERE date = ? ORDER BY id DESC LIMIT 200""",
            (day,)).fetchall()
        legs_by_slip = {}
        for s in slip_rows:
            legs_by_slip[s["id"]] = c.execute(
                """SELECT l.*, h.name home, a.name away, m.date, m.time FROM legs l
                   JOIN matches m ON m.id = l.match_id
                   JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
                   WHERE l.slip_id = ? ORDER BY m.time, m.id""",
                (s["id"],)).fetchall()
        src_mix = c.execute(
            """SELECT COALESCE(odds_source,'stimato') src, COUNT(*) n FROM legs l
               JOIN slips s ON s.id = l.slip_id WHERE s.date = ?
               GROUP BY src ORDER BY n DESC""", (day,)).fetchall()
        c.close()
        return render_template("schedine.html", day=day, slips=slip_rows,
                               legs_by_slip=legs_by_slip, src_mix=src_mix,
                               label=markets.label, active="schedine")

    @app.route("/modello")
    def modello():
        c = con()
        teams = c.execute(
            """SELECT t.id, t.name,
                      (SELECT COUNT(*) FROM matches m
                        WHERE (m.home_id=t.id OR m.away_id=t.id) AND m.hg IS NOT NULL) n_gioc,
                      (SELECT COUNT(*) FROM matches m
                        WHERE (m.home_id=t.id OR m.away_id=t.id) AND m.hg IS NULL) n_futur
               FROM teams t
               WHERE EXISTS (SELECT 1 FROM matches m WHERE m.home_id=t.id OR m.away_id=t.id)
               ORDER BY n_gioc DESC LIMIT 60""").fetchall()
        calib = c.execute(
            "SELECT * FROM calibration WHERE n >= 50 ORDER BY n DESC").fetchall()
        log = c.execute(
            "SELECT * FROM learning_log ORDER BY date DESC LIMIT 30").fetchall()
        odds_stats = c.execute(
            """SELECT source, COUNT(*) n, MIN(fetched_at) first, MAX(fetched_at) last
               FROM odds GROUP BY source ORDER BY n DESC""").fetchall()
        c.close()
        return render_template("modello.html", teams=teams, calib=calib,
                               log=log, odds_stats=odds_stats, label=markets.label,
                               active="modello")

    @app.route("/aggiorna-quote", methods=["POST"])
    def aggiorna_quote():
        """Legge curl.txt nella cwd e ri-sincronizza le quote da Sisal."""
        try:
            with open("curl.txt", encoding="utf-8") as f:
                curl_text = f.read()
        except FileNotFoundError:
            return render_template("aggiorna.html",
                                   error="curl.txt non trovato nella cwd: "
                                         "copia un cURL fresco da DevTools prima."), 400
        c = con()
        try:
            payload = sisal.fetch_from_curl(curl_text)
            events = sisal.parse_events(payload)
            m, q = sisal.store(c, "it.1", events)
            c.commit()
        except Exception as e:
            return render_template("aggiorna.html",
                                   error=f"errore fetch: {e}"), 502
        finally:
            c.close()
        return render_template("aggiorna.html", m=m, q=q)

    @app.template_filter("fmt_odds")
    def fmt_odds(v):
        return f"{v:.2f}" if v is not None else "-"

    @app.template_filter("fmt_prob")
    def fmt_prob(v):
        return f"{100 * v:.0f}%" if v is not None else "-"

    return app


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=db.DEFAULT_PATH)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    create_app(args.db).run(host=args.host, port=args.port, debug=args.debug)
