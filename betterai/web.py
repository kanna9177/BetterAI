"""Mini-dashboard web per vedere partite, quote reali, schedine e modello.

Non e' una copia visuale di Sisal: stessa logica di navigazione (sport ->
palinsesto -> partita -> schedina) ma UI sobria, niente loghi di terzi.
Richiede 'flask' (unico dip oltre numpy, gia' richiesto dal modello).
"""
import json
import secrets
import socket
from datetime import date, datetime, timedelta

from flask import Flask, redirect, render_template, request, url_for

from . import db, markets, sisal, slips


def sisal_share_url_for_slip(con, slip_id):
    """Costruisce l'URL 'Giocala su Sisal' per una schedina. None se qualche
    leg non ha quota/tag Sisal corrispondente (es. mercato non coperto)."""
    rows = con.execute(
        """SELECT o.odds, o.source_id FROM legs l
           LEFT JOIN odds o ON o.match_id = l.match_id
                            AND o.selection = l.selection
                            AND o.source = 'sisal'
           WHERE l.slip_id = ?""",
        (slip_id,)).fetchall()
    if any(r["source_id"] is None or r["odds"] is None for r in rows):
        return None
    from . import sisal as _sisal
    return _sisal.share_url([(r["source_id"], r["odds"]) for r in rows])


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

    COMP_LABELS = {
        "it.1": ("ITA", "Serie A"),
        "en.1": ("ENG", "Premier League"),
        "es.1": ("ESP", "La Liga"),
        "de.1": ("GER", "Bundesliga"),
        "fr.1": ("FRA", "Ligue 1"),
    }
    SPORTS = [
        {"key": "calcio", "label": "Calcio", "active": True},
        {"key": "tennis", "label": "Tennis", "active": False},
        {"key": "basket", "label": "Basket", "active": False},
        {"key": "volley", "label": "Volley", "active": False},
        {"key": "rugby",  "label": "Rugby",  "active": False},
    ]

    def comp_label(code):
        if not code:
            return ("", "")
        if code in COMP_LABELS:
            return COMP_LABELS[code]
        if code.startswith("sisal:"):
            return sisal.comp_label(code)
        # fallback: "en.2" -> ("ENG", "Serie 2"), or just raw
        if "." in code:
            cc, _, num = code.partition(".")
            return (cc.upper(), f"Lega {num}")
        return (code.upper(), "")

    @app.context_processor
    def inject_nav():
        today = date.today()
        day_tabs = [
            {"key": "tutti",  "label": "Tutti",            "date": None},
            {"key": "oggi",   "label": "Oggi",             "date": today.isoformat()},
            {"key": "domani", "label": "Domani",           "date": (today + timedelta(days=1)).isoformat()},
            {"key": "d2",     "label": (today + timedelta(days=2)).strftime("%d/%m"),
                              "date": (today + timedelta(days=2)).isoformat()},
        ]
        return {"today": today.isoformat(), "sports": SPORTS, "day_tabs": day_tabs,
                "comp_label": comp_label}

    def con():
        return db.connect(app.config["DB_PATH"])

    @app.route("/")
    def home():
        return redirect(url_for("oggi"))

    @app.route("/oggi")
    def oggi():
        day = request.args.get("date")  # None = tutti
        c = con()
        where = "WHERE m.date = ?" if day else "WHERE m.date >= ?"
        day_arg = day or date.today().isoformat()
        rows = c.execute(
            f"""SELECT m.id, m.date, m.time, m.comp, m.hg, m.ag,
                       h.name home, a.name away,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:1' LIMIT 1) q1,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:X' LIMIT 1) qx,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:2' LIMIT 1) q2,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:1X' LIMIT 1) qdc1,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:X2' LIMIT 1) qdc2,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='R:12' LIMIT 1) qdc3,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='TG:U2.5' LIMIT 1) qu,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='TG:O2.5' LIMIT 1) qo,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='BTTS:Y' LIMIT 1) qby,
                       (SELECT odds FROM odds WHERE match_id=m.id AND selection='BTTS:N' LIMIT 1) qbn,
                       (SELECT COUNT(*) FROM odds WHERE match_id=m.id) nq
                FROM matches m
                JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
                {where} ORDER BY m.date, m.time, m.id""", (day_arg,)).fetchall()
        # Raggruppa per comp e ordina stile Sisal (top prima, poi per posizione).
        groups_tmp = {}
        for r in rows:
            cc = r["comp"] or "altro"
            groups_tmp.setdefault(cc, []).append(r)
        groups = dict(sorted(groups_tmp.items(),
                             key=lambda kv: sisal.comp_sort_key(kv[0])))
        # TOP SCOMMESSE = prime 6 partite della competizione piu' popolosa, altrimenti prime 6 globali
        top = rows[:6] if rows else []
        # schedine AI del giorno per sidebar
        slips_today = c.execute(
            """SELECT * FROM slips WHERE date = ? ORDER BY id DESC LIMIT 6""",
            (day or date.today().isoformat(),)).fetchall()
        c.close()
        active_day = day or "tutti"
        return render_template("oggi.html",
                               day=day, active_day=active_day,
                               groups=groups, top=top, slips_today=slips_today,
                               active="oggi")

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
        share_urls = {s["id"]: sisal_share_url_for_slip(c, s["id"]) for s in slip_rows}
        src_mix = c.execute(
            """SELECT COALESCE(odds_source,'stimato') src, COUNT(*) n FROM legs l
               JOIN slips s ON s.id = l.slip_id WHERE s.date = ?
               GROUP BY src ORDER BY n DESC""", (day,)).fetchall()
        c.close()
        return render_template("schedine.html", day=day, slips=slip_rows,
                               legs_by_slip=legs_by_slip, src_mix=src_mix,
                               share_urls=share_urls,
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

    FAMILIES = [
        ("R",       "1X2 / Doppia Chance"),
        ("BTTS",    "Goal / NoGoal"),
        ("TG",      "Under / Over"),
        ("R+BTTS",  "Combo 1X2 + Goal/NoGoal"),
        ("R+TG",    "Combo 1X2 + U/O"),
        ("HG+AG",   "Multigoal casa + ospite"),
    ]

    @app.route("/biglietto-ai", methods=["GET", "POST"])
    def biglietto_ai():
        c = con()
        today = date.today().isoformat()
        day = request.form.get("day") or request.args.get("day") or today
        # Competizioni disponibili per il giorno, ordinate stile Sisal (top prima).
        comps = sorted(
            (r["comp"] for r in c.execute(
                "SELECT DISTINCT comp FROM matches WHERE date = ?", (day,))),
            key=sisal.comp_sort_key)
        made_slips = []
        if request.method == "POST":
            sel_comps = set(request.form.getlist("comp")) or None
            sel_families = set(request.form.getlist("family")) or None
            seed = (secrets.token_hex(4) if request.form.get("randomize") == "on"
                    else request.form.get("seed") or "mario")
            try:
                opts = dict(
                    comps=sel_comps,
                    families=sel_families,
                    min_legs=int(request.form.get("min_legs", 3)),
                    max_legs=int(request.form.get("max_legs", 5)),
                    min_total_odds=float(request.form.get("min_odds", 1.8)),
                    max_total_odds=float(request.form.get("max_odds", 3.5)),
                    n_slips=int(request.form.get("n_slips", 10)),
                    seed=seed,
                )
            except ValueError:
                return render_template("biglietto_ai.html", day=day, comps=comps,
                                       families=FAMILIES, made=[], error="input non valido",
                                       active="ai"), 400
            from .model import fit_from_db
            model = fit_from_db(c, day)
            ids = slips.generate_custom(c, model, day, **opts)
            if ids:
                q = "SELECT * FROM slips WHERE id IN ({})".format(",".join(["?"] * len(ids)))
                made_slips = c.execute(q, ids).fetchall()
        legs_by_slip = {}
        for s in made_slips:
            legs_by_slip[s["id"]] = c.execute(
                """SELECT l.*, h.name home, a.name away, m.date, m.time FROM legs l
                   JOIN matches m ON m.id = l.match_id
                   JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
                   WHERE l.slip_id = ? ORDER BY m.time, m.id""", (s["id"],)).fetchall()
        share_urls = {s["id"]: sisal_share_url_for_slip(c, s["id"]) for s in made_slips}
        c.close()
        return render_template("biglietto_ai.html", day=day, comps=comps,
                               comp_label=comp_label, families=FAMILIES,
                               made=made_slips, legs_by_slip=legs_by_slip,
                               share_urls=share_urls,
                               label=markets.label, active="ai",
                               form=request.form)

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
