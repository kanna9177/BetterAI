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
    # Template e static ricaricati ad ogni richiesta: evita di dover chiudere
    # il server quando si cambia un .html o il CSS.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

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
            raw_max_leg = request.form.get("max_per_leg", "").strip()
            try:
                opts = dict(
                    comps=sel_comps,
                    families=sel_families,
                    min_legs=int(request.form.get("min_legs", 3)),
                    max_legs=int(request.form.get("max_legs", 5)),
                    min_total_odds=float(request.form.get("min_odds", 1.8)),
                    max_total_odds=float(request.form.get("max_odds", 3.5)),
                    max_per_leg=float(raw_max_leg) if raw_max_leg else None,
                    only_real_odds=request.form.get("only_real") == "on",
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

    DB_TABLES = [
        ("teams",        "Squadre",          "Nome e id di ogni squadra incontrata"),
        ("matches",      "Partite",          "Calendario e risultati"),
        ("odds",         "Quote",            "Quote reali per partita + esito, per fonte"),
        ("predictions",  "Pronostici",       "Probabilita' calcolate dal modello"),
        ("calibration",  "Calibrazione",     "Scarto fra previsto e reale, per esito"),
        ("slips",        "Schedine",         "Ogni schedina simulata"),
        ("legs",         "Esiti schedina",   "Dettaglio di ogni leg nelle schedine"),
        ("learning_log", "Log apprendimento", "Sommario giornaliero + lezioni imparate"),
    ]
    DB_TABLE_SET = {t[0] for t in DB_TABLES}

    @app.route("/database")
    def database():
        c = con()
        rows = []
        for name, lbl, desc in DB_TABLES:
            n = c.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            rows.append({"name": name, "label": lbl, "desc": desc, "n": n})
        c.close()
        return render_template("database.html", tables=rows, active="db")

    @app.route("/database/<table>")
    def database_table(table):
        if table not in DB_TABLE_SET:
            return "tabella sconosciuta", 404
        c = con()
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        per = 50
        offset = (page - 1) * per
        total = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        probe = c.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
        cols = list(probe.keys()) if probe else []
        rows = c.execute(
            f"SELECT * FROM {table} LIMIT ? OFFSET ?", (per, offset)).fetchall()
        c.close()
        pages = (total + per - 1) // per
        label = next((lbl for n, lbl, _ in DB_TABLES if n == table), table)
        return render_template("database_table.html", table=table, label=label,
                               cols=cols, rows=rows, page=page, pages=pages,
                               per=per, total=total, active="db")

    @app.route("/algoritmo")
    def algoritmo():
        c = con()
        day = request.args.get("date") or date.today().isoformat()
        from .model import fit_from_db, score_matrix
        model = fit_from_db(c, day)
        # Top squadre per attacco + per difesa
        name_of = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM teams")}
        atts = [(tid, v) for tid, v in model.att.items() if tid in name_of]
        dfns = [(tid, v) for tid, v in model.dfn.items() if tid in name_of]
        top_att = [{"name": name_of[t], "v": v}
                   for t, v in sorted(atts, key=lambda kv: -kv[1])[:20]]
        top_dfn = [{"name": name_of[t], "v": v}
                   for t, v in sorted(dfns, key=lambda kv: kv[1])[:20]]
        # Medie comp
        comp_mu = []
        for comp, (mh, ma) in sorted(model.mu.items())[:30]:
            cc, name = comp_label(comp)
            comp_mu.append({"code": comp, "cc": cc, "name": name,
                            "mh": mh, "ma": ma})
        # Partite di oggi con xG + top 3 probabilita'
        matches_today = []
        for m in c.execute(
                """SELECT m.id, m.comp, m.date, m.time, m.home_id, m.away_id,
                          h.name home, a.name away
                   FROM matches m
                   JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
                   WHERE m.date = ? ORDER BY m.time""", (day,)).fetchall():
            lam, nu = model.xg(m["comp"], m["home_id"], m["away_id"])
            probs = {r["selection"]: r["prob"] for r in c.execute(
                "SELECT selection, prob FROM predictions WHERE match_id = ?",
                (m["id"],))}
            top3 = sorted(probs.items(), key=lambda kv: -kv[1])[:5]
            cc, cname = comp_label(m["comp"])
            matches_today.append({
                "id": m["id"], "time": m["time"], "home": m["home"], "away": m["away"],
                "cc": cc, "cname": cname, "xg_home": lam, "xg_away": nu,
                "top": [{"sel": s, "label": markets.label(s), "prob": p} for s, p in top3],
            })
        # Lessons giornate recenti
        lessons = []
        for r in c.execute(
                "SELECT * FROM learning_log ORDER BY date DESC LIMIT 15").fetchall():
            try:
                ls = json.loads(r["lessons"] or "[]")
            except Exception:
                ls = []
            lessons.append({"date": r["date"], "slips": r["slips"], "won": r["won"],
                            "ll_model": r["ll_model"], "ll_base": r["ll_base"],
                            "lines": ls})
        # Calibration top deviations
        calib_dev = []
        for r in c.execute(
                """SELECT selection, n, hits, sum_p,
                          ABS(hits*1.0/n - sum_p/n) AS dev
                   FROM calibration WHERE n >= 30 ORDER BY dev DESC LIMIT 15""").fetchall():
            calib_dev.append({"sel": r["selection"], "label": markets.label(r["selection"]),
                              "n": r["n"], "previsto": r["sum_p"]/r["n"],
                              "reale": r["hits"]/r["n"], "dev": r["dev"]})
        c.close()
        return render_template("algoritmo.html", day=day,
                               top_att=top_att, top_dfn=top_dfn,
                               comp_mu=comp_mu, matches_today=matches_today,
                               lessons=lessons, calib_dev=calib_dev,
                               mu_all=model.mu_all, active="algoritmo")

    @app.route("/aggiorna-risultati", methods=["POST"])
    def aggiorna_risultati():
        """Fa settle: verifica tutte le schedine aperte sui risultati giunti."""
        from . import learn
        today = date.today().isoformat()
        c = con()
        closed = learn.settle(c, today)
        c.close()
        return render_template("aggiorna_risultati.html", closed=closed, today=today)

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
