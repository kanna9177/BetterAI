"""Quote reali dalla mobile api di Sisal.

Il sito desktop sta dietro Akamai Bot Manager (sensor_data, _abck): senza un
browser vero non si passa. L'app Android/iOS, invece, parla con un endpoint
JSON "pulito" (nessun sensor, bastano User-Agent + Accept-Language).

Ricetta per riempire SISAL_BASE / SISAL_PATH / SISAL_HEADERS:
  1. mitmproxy -p 8080 (scarica la CA sul telefono).
  2. sul telefono: proxy -> IP_PC:8080. Apri l'app Sisal, vai su Calcio ->
     Serie A, fai qualche tap sulle partite.
  3. nel log di mitmproxy cerca le GET verso api.sisal.it o
     www.sisal.it/api-pdp-matchboard/. Copia URL completo e tutti gli header.
  4. incolla qui o esporta via variabili d'ambiente SISAL_BASE / SISAL_PATH.
  5. python -m betterai sisal-probe --dump data/raw/sisal.json
     Guarda la struttura; se non combacia con parse_events, aggiusta le chiavi.

Sisal controlla l'IP: da fuori Italia la lista eventi torna vuota. Serve un
IP IT (casa, residential proxy o VPS italiano). Non vale la pena ciclare
proxy: una fetch al giorno basta.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .db import team_id

SISAL_BASE = os.environ.get("SISAL_BASE", "https://www.sisal.it")
SISAL_PATH = os.environ.get(
    "SISAL_PATH", "/api-pdp-matchboard/v3/sport/calcio/palinsesto")
SISAL_HEADERS = {
    "User-Agent": "sisal-mobile/6.9 (iPhone; iOS 17.5; Scale/3.00)",
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9",
    "X-Device-Platform": "ios",
}

# Nomi come li scrive Sisal -> come stanno nel db (fonte openfootball).
# Da estendere: ogni nome non in mappa resta uguale e, se non combacia col
# calendario, risulta come partita a se'.
TEAM_ALIASES = {
    "F.C. Internazionale": "Inter", "Internazionale": "Inter", "Inter": "Inter",
    "A.C. Milan": "AC Milan", "Milan": "AC Milan",
    "Juventus": "Juventus FC",
    "A.S. Roma": "AS Roma", "Roma": "AS Roma",
    "S.S. Lazio": "SS Lazio", "Lazio": "SS Lazio",
    "S.S.C. Napoli": "SSC Napoli", "Napoli": "SSC Napoli",
    "Atalanta": "Atalanta BC",
    "Fiorentina": "ACF Fiorentina",
    "Hellas Verona": "Hellas Verona FC", "Verona": "Hellas Verona FC",
    "Torino": "Torino FC",
    "Bologna": "Bologna FC",
    "Udinese": "Udinese Calcio",
    "Genoa": "Genoa CFC",
    "Cagliari": "Cagliari Calcio",
    "Lecce": "US Lecce",
    "Empoli": "Empoli FC",
    "Parma": "Parma Calcio 1913",
    "Como": "Como 1907",
    "Monza": "AC Monza",
    "Venezia": "Venezia FC",
    "Cremonese": "US Cremonese",
    "Sassuolo": "US Sassuolo",
    "Pisa": "Pisa SC",
}

MULTIGOL_RANGES = ("0-1", "1-2", "1-3", "2-3", "2-4")
UO_LINES = ("1.5", "2.5", "3.5")


def translate_selection(bet_type, label):
    """Etichetta Sisal -> codice markets.py. None se non e' un esito che modello.

    Shape tipica dei bet_type Sisal:
      "1X2" / "Esito finale" , "Doppia chance", "Goal/Nogoal",
      "U/O 2.5" o "Under/Over 2.5", "Multigol casa 1-3", "Multigol ospite 2-4".
    """
    bt = (bet_type or "").upper().strip()
    lb = (label or "").upper().strip().replace(",", ".")
    if bt in ("1X2", "ESITO FINALE") and lb in ("1", "X", "2"):
        return f"R:{lb}"
    if bt in ("DC", "DOPPIA CHANCE") and lb in ("1X", "X2", "12"):
        return f"R:{lb}"
    if bt in ("BTTS", "GOAL/NOGOAL", "GOL/NOGOL"):
        if lb in ("GOAL", "GOL"):
            return "BTTS:Y"
        if lb in ("NOGOAL", "NOGOL", "NO GOAL", "NO GOL"):
            return "BTTS:N"
    if "UNDER" in bt or "OVER" in bt or bt.startswith("U/O"):
        for part in (bt + " " + lb).replace("/", " ").split():
            if part in UO_LINES:
                if "UNDER" in lb:
                    return f"TG:U{part}"
                if "OVER" in lb:
                    return f"TG:O{part}"
    if bt.startswith("MULTIGOL CASA") and lb in MULTIGOL_RANGES:
        return f"HG:{lb}"
    if (bt.startswith("MULTIGOL OSPITE") or bt.startswith("MULTIGOL TRASFERTA")) \
            and lb in MULTIGOL_RANGES:
        return f"AG:{lb}"
    return None


def _team_name(raw):
    name = (raw or "").strip()
    return TEAM_ALIASES.get(name, name)


def _season_of(date_str):
    """AAAA-MM-GG -> stagione stile openfootball (es. 2026-27)."""
    y, m, _ = date_str.split("-")
    y = int(y)
    if int(m) >= 7:
        return f"{y}-{str(y + 1)[-2:]}"
    return f"{y - 1}-{str(y)[-2:]}"


def fetch(path=None, params=None, timeout=20):
    """GET json da Sisal. Stdlib, zero dipendenze. Raise su errore."""
    url = f"{SISAL_BASE}{path or SISAL_PATH}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=SISAL_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def parse_events(payload):
    """JSON Sisal -> lista di partite con le loro quote.

    Chiavi attese (da verificare dopo mitm; aggiusta i get se differenti):
      payload["eventi"][*] = {
        "dataOra": "2026-11-20T20:45", "casa": "...", "ospite": "...",
        "quote": [{"tipo": "1X2", "esito": "1", "quota": 1.85}, ...]}
    """
    events = payload.get("eventi") or payload.get("events") or []
    out = []
    for ev in events:
        dt = ev.get("dataOra") or ev.get("startTime") or ev.get("kickOff") or ""
        date_part, _, time_part = dt.partition("T")
        if not date_part:
            continue
        quotes = {}
        for q in ev.get("quote") or ev.get("odds") or []:
            sel = translate_selection(
                q.get("tipo") or q.get("marketType") or "",
                q.get("esito") or q.get("outcome") or q.get("label") or "")
            price = q.get("quota") or q.get("price") or q.get("odds")
            if sel and price:
                quotes[sel] = float(price)
        out.append({
            "date": date_part,
            "time": time_part[:5] or None,
            "home": _team_name(ev.get("casa") or ev.get("home") or ""),
            "away": _team_name(ev.get("ospite") or ev.get("away") or ""),
            "quotes": quotes,
        })
    return out


def store(con, comp, events, source="sisal"):
    """Upsert partita + quote. Ritorna (partite, quote) scritte."""
    n_matches = n_quotes = 0
    for ev in events:
        if not ev["home"] or not ev["away"] or not ev["date"]:
            continue
        hid = team_id(con, ev["home"])
        aid = team_id(con, ev["away"])
        season = _season_of(ev["date"])
        con.execute(
            """INSERT INTO matches(comp, season, date, time, home_id, away_id)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(comp, season, home_id, away_id) DO UPDATE SET
                 date=excluded.date, time=excluded.time""",
            (comp, season, ev["date"], ev["time"], hid, aid))
        mid = con.execute(
            "SELECT id FROM matches WHERE comp=? AND season=? AND home_id=? AND away_id=?",
            (comp, season, hid, aid)).fetchone()[0]
        n_matches += 1
        for sel, price in ev["quotes"].items():
            con.execute(
                """INSERT INTO odds(match_id, selection, odds, source)
                   VALUES (?,?,?,?)
                   ON CONFLICT(match_id, selection, source) DO UPDATE SET
                     odds=excluded.odds, fetched_at=CURRENT_TIMESTAMP""",
                (mid, sel, price, source))
            n_quotes += 1
    return n_matches, n_quotes


def sync(con, comp="it.1", log=print):
    try:
        payload = fetch()
    except urllib.error.URLError as e:
        log(f"sisal: errore rete ({e}); verifica mitm + IP italiano")
        return 0, 0
    events = parse_events(payload)
    m, q = store(con, comp, events)
    log(f"sisal: {m} partite, {q} quote")
    con.commit()
    return m, q
