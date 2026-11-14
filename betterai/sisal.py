"""Quote reali dall'api web di Sisal.

Endpoint vero (scoperto via Chrome DevTools, Network > Fetch/XHR > Size desc):

  GET https://betting.sisal.it/api/lettura-palinsesto-sport/palinsesto/prematch/
      schedaDisciplina/{disciplina}/1/80/1000001?offerId=0

con disciplina = 1 per il calcio. Risposta ~15 MB con TUTTO il palinsesto
prematch calcio del brand (423+ eventi, 1800+ mercati, 3800+ quote-set).

Shape (chiavi che ci interessano):

  avvenimentoFeList[i] = {
    descrizione: "Casa - Ospite",
    data:        "2026-11-14T14:00:00.000Z",
    codiceManifestazione: 1049,
    scommessaKeyDataList: [
      {codiceScommessa: 3,     key: "palin-avven-3"},     # 1X2
      {codiceScommessa: 28319, key: "palin-avven-28319"}, # DC
      {codiceScommessa: 7989,  key: "palin-avven-7989"},  # U/O (linee multiple)
      {codiceScommessa: 18,    key: "palin-avven-18"},    # Goal/NoGoal
      {codiceScommessa: 30562, key: "palin-avven-30562"}, # Multigoal totale (ignorato)
    ],
  }

  infoAggiuntivaMap["palin-avven-codiceScommessa-idInfoAggiuntiva"] = {
    stato:    1,                  # 1 = attivo
    soglia:   "2.5",              # linea U/O o vuoto
    esitoList: [
      {descrizione: "1",     quota: 205, stato: 1},   # 2.05 (quota * 100)
      {descrizione: "OVER",  quota: 145, stato: 1},   # 1.45
    ],
  }

Protezione Akamai: serve un browser vero per generare cookies (_abck, bm_sz,
ak_bmsc) e il header Akamai-BM-Telemetry. I cookies durano ~30-60 min, dopo
scadono e si prende 401/403. Workflow suggerito:
  1. apri la pagina palinsesto in Chrome/Brave/Edge una volta al giorno;
  2. DevTools -> Network -> trova l'xhr schedaDisciplina (~1 MB) -> Copy as
     cURL -> salva in curl.txt;
  3. passa il curl a fetch_from_curl() oppure riempi SISAL_* qui sotto.

Per il daily automatizzato serve un passaggio headless-browser (patchright /
camoufox) che ri-genera i cookies: non implementato qui, tool dedicato.
"""
import json
import os
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

from .db import team_id

# Mappa codiceManifestazione -> (sigla, descrizione) estratta dalla risposta
# alberaturaPrematch. Si aggiorna con `python -m betterai sisal-manifestazioni --json ...`.
_MANI_PATH = os.path.join(os.path.dirname(__file__), "sisal_manifestazioni.json")


def _load_manifestazioni():
    """Formato file: {codice: [cc, name, posizione?, top?]}."""
    try:
        with open(_MANI_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    out = {}
    for k, v in raw.items():
        # back-compat: v puo' essere [cc, name] (2) o [cc, name, pos, top] (4)
        cc = v[0] if len(v) > 0 else ""
        name = v[1] if len(v) > 1 else ""
        pos = v[2] if len(v) > 2 else 9999
        top = bool(v[3]) if len(v) > 3 else False
        out[int(k)] = (cc, name, pos, top)
    return out


MANIFESTAZIONI = _load_manifestazioni()

_UNKNOWN_SORT_KEY = (True, 99999, "")  # not-top, pos tardissimo => in fondo


def comp_label(sisal_code):
    """f'sisal:<N>' -> ('SIGLA', 'Nome leggibile')."""
    if not sisal_code or not sisal_code.startswith("sisal:"):
        return ("", sisal_code or "")
    try:
        n = int(sisal_code.split(":", 1)[1])
    except ValueError:
        return ("", sisal_code)
    meta = MANIFESTAZIONI.get(n)
    if meta:
        return (meta[0], meta[1])
    return ("", f"Manifestazione {n}")


def comp_sort_key(sisal_code):
    """Chiave di ordinamento stile Sisal: top prima, poi posizione ASC, poi nome.
    Non conosciuti vanno in fondo."""
    if not sisal_code or not sisal_code.startswith("sisal:"):
        return _UNKNOWN_SORT_KEY
    try:
        n = int(sisal_code.split(":", 1)[1])
    except ValueError:
        return _UNKNOWN_SORT_KEY
    meta = MANIFESTAZIONI.get(n)
    if not meta:
        return _UNKNOWN_SORT_KEY
    cc, name, pos, top = meta
    # top True deve venire prima: ordina come (NOT top, pos, name)
    return (not top, pos, f"{cc} {name}")


def update_manifestazioni_from_alberatura(payload):
    """Aggiorna il file di mapping dalla risposta alberaturaPrematch.
    Salva anche posizione e topManifestazione per l'ordinamento stile Sisal."""
    global MANIFESTAZIONI
    out = {}
    for k, v in (payload.get("manifestazioneMap") or {}).items():
        cm = v.get("codiceManifestazione")
        desc = (v.get("descrizione") or "").strip()
        if cm is None or not desc:
            continue
        parts = desc.split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 3 and parts[0].isupper():
            cc, name = parts
        else:
            cc, name = "", desc
        out[cm] = (cc, name, v.get("posizione", 9999),
                   bool(v.get("topManifestazione")))
    with open(_MANI_PATH, "w", encoding="utf-8") as f:
        json.dump({str(k): list(v) for k, v in sorted(out.items())}, f,
                  ensure_ascii=False, indent=1)
    MANIFESTAZIONI = out
    return len(out)

SISAL_BASE = os.environ.get("SISAL_BASE", "https://betting.sisal.it")
SISAL_PATH_TMPL = os.environ.get(
    "SISAL_PATH_TMPL",
    "/api/lettura-palinsesto-sport/palinsesto/prematch/"
    "schedaDisciplina/{disciplina}/1/80/1000001?offerId=0")

# codiceDisciplina Sisal -> comp code openfootball-ish (se vogliamo mapparlo).
# calcio = 1. basket = 15. tennis = 7. per ora supportiamo solo calcio.
DISCIPLINE = {
    1: "calcio",
}
SISAL_PATH = SISAL_PATH_TMPL.format(disciplina=1)  # retro-compat
SISAL_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Origin": "https://www.sisal.it",
    "Referer": "https://www.sisal.it/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "bet-brand": "175",
    "bet-locale": "it_IT",
    "bet-offer": "0",
}

# Nomi come li scrive Sisal -> come stanno nel db (fonte openfootball).
TEAM_ALIASES = {
    "F.C. Internazionale": "Inter", "Internazionale": "Inter", "Inter": "Inter",
    "A.C. Milan": "AC Milan", "Milan": "AC Milan",
    "Juventus": "Juventus FC",
    "A.S. Roma": "AS Roma", "Roma": "AS Roma",
    "S.S. Lazio": "SS Lazio", "Lazio": "SS Lazio",
    "S.S.C. Napoli": "SSC Napoli", "Napoli": "SSC Napoli",
    "Atalanta": "Atalanta BC", "Fiorentina": "ACF Fiorentina",
    "Hellas Verona": "Hellas Verona FC", "Verona": "Hellas Verona FC",
    "Torino": "Torino FC", "Bologna": "Bologna FC",
    "Udinese": "Udinese Calcio", "Genoa": "Genoa CFC",
    "Cagliari": "Cagliari Calcio", "Lecce": "US Lecce",
    "Empoli": "Empoli FC", "Parma": "Parma Calcio 1913",
    "Como": "Como 1907", "Monza": "AC Monza", "Venezia": "Venezia FC",
    "Cremonese": "US Cremonese", "Sassuolo": "US Sassuolo", "Pisa": "Pisa SC",
}

# codiceScommessa -> famiglia mercato
MARKET_1X2 = 3
MARKET_DC = 28319
MARKET_UO = 7989
MARKET_BTTS = 18
MARKET_MULTIGOAL = 30562  # multigoal totale (1-2, 1-3, ecc. sui gol di entrambe)
WANTED_MARKETS = {MARKET_1X2, MARKET_DC, MARKET_UO, MARKET_BTTS, MARKET_MULTIGOAL}

UO_LINES_OK = {"1.5", "2.5", "3.5"}
# Range multigoal totale accettati (sisal ne espone fino a 5-6)
MGT_RANGES_OK = {"1-2", "1-3", "1-4", "1-5", "2-3", "2-4", "2-5", "2-6",
                 "3-4", "3-5", "3-6", "4-6", "5-6", "0"}

_CURL_DROP_HEADERS = {"if-none-match", "if-modified-since", "content-length", "host"}


def translate_outcome(codice_scommessa, outcome_desc, soglia):
    """(codiceScommessa, descrizione esito, soglia) -> codice markets betterai.
    None se non e' un esito modellato."""
    o = (outcome_desc or "").upper().strip()
    if codice_scommessa == MARKET_1X2 and o in ("1", "X", "2"):
        return f"R:{o}"
    if codice_scommessa == MARKET_DC and o in ("1X", "X2", "12"):
        return f"R:{o}"
    if codice_scommessa == MARKET_BTTS:
        if o == "GOAL":
            return "BTTS:Y"
        if o == "NOGOAL":
            return "BTTS:N"
    if codice_scommessa == MARKET_UO and (soglia or "").strip() in UO_LINES_OK:
        if o == "UNDER":
            return f"TG:U{soglia.strip()}"
        if o == "OVER":
            return f"TG:O{soglia.strip()}"
    if codice_scommessa == MARKET_MULTIGOAL and o in MGT_RANGES_OK:
        # MGT = Multigoal Totale (gol totali nel match). Non modellato da
        # markets.py; salvato per visualizzazione nel dettaglio partita.
        return f"MGT:{o}"
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


def parse_curl(text):
    """Parsea un 'Copy as cURL (bash)' da DevTools. Ritorna (url, headers_dict).
    Scarta If-None-Match e simili per evitare 304 vuoti sul replay."""
    text = re.sub(r"\\\s*\n", " ", text)
    text = re.sub(r"\^\s*\r?\n", " ", text)
    toks = shlex.split(text.strip(), posix=True)
    if not toks or toks[0] != "curl":
        raise ValueError("input non e' un comando curl")
    url = None
    headers = {}
    takes_arg = {"-H", "--header", "-A", "--user-agent", "-b", "--cookie",
                 "-e", "--referer", "-X", "--request", "--url",
                 "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"}
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in ("-H", "--header"):
            k, _, v = toks[i + 1].partition(":")
            if k.strip().lower() not in _CURL_DROP_HEADERS:
                headers[k.strip()] = v.strip()
            i += 2
        elif t in ("-A", "--user-agent"):
            headers["User-Agent"] = toks[i + 1]; i += 2
        elif t in ("-b", "--cookie"):
            headers["Cookie"] = toks[i + 1]; i += 2
        elif t in ("-e", "--referer"):
            headers["Referer"] = toks[i + 1]; i += 2
        elif t == "--url":
            url = toks[i + 1]; i += 2
        elif t in takes_arg:
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            if url is None:
                url = t
            i += 1
    if not url:
        raise ValueError("url non trovato nel curl")
    return url, headers


def fetch(path=None, disciplina=1, headers=None, timeout=30):
    """GET json da Sisal. Stdlib. Raise su errore.
    Se path None usa SISAL_PATH_TMPL con la disciplina indicata."""
    if path is None:
        path = SISAL_PATH_TMPL.format(disciplina=disciplina)
    url = f"{SISAL_BASE}{path}"
    req = urllib.request.Request(url, headers=headers or SISAL_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_from_curl(curl_text, timeout=30):
    """Fetch replaying a 'Copy as cURL (bash)' from DevTools (cookies + headers
    inclusi)."""
    url, headers = parse_curl(curl_text)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def parse_events(payload):
    """Risposta schedaDisciplina -> [{date, time, home, away, quotes: {sel: odds}}].

    Ignora i mercati non modellati da betterai (MULTIGOAL totale ecc.).
    Le quote sono interi * 100 (205 -> 2.05)."""
    iam = payload.get("infoAggiuntivaMap") or {}
    # index: market_key ("palin-avven-codScom") -> [infoAggiuntiva, ...]
    iam_index = defaultdict(list)
    for ik, iv in iam.items():
        parent, _, _ = ik.rpartition("-")
        if parent:
            iam_index[parent].append(iv)

    out = []
    for ev in payload.get("avvenimentoFeList") or []:
        desc = ev.get("descrizione") or ""
        if " - " not in desc:
            continue
        home_raw, away_raw = desc.split(" - ", 1)
        data = ev.get("data") or ""
        date_part, _, time_part = data.partition("T")
        if not date_part:
            continue
        quotes = {}
        for mkt in ev.get("scommessaKeyDataList") or []:
            cs = mkt.get("codiceScommessa")
            if cs not in WANTED_MARKETS:
                continue
            mk = mkt.get("key")
            for iv in iam_index.get(mk, []):
                if iv.get("stato") != 1:
                    continue
                soglia = (iv.get("soglia") or "").strip()
                for esito in iv.get("esitoList") or []:
                    if esito.get("stato") != 1:
                        continue
                    sel = translate_outcome(cs, esito.get("descrizione"), soglia)
                    q = esito.get("quota")
                    if sel and q:
                        quotes[sel] = round(q / 100.0, 2)
        out.append({
            "date": date_part,
            "time": time_part[:5] or None,
            "home": _team_name(home_raw),
            "away": _team_name(away_raw),
            "codice_manifestazione": ev.get("codiceManifestazione"),
            "quotes": quotes,
        })
    return out


def store(con, comp_fallback, events, source="sisal"):
    """Upsert partita + quote. Ogni evento usa come 'comp' il codice di
    manifestazione sisal (sisal:<N>) se presente, altrimenti comp_fallback.

    MIGRAZIONE IN LOCO: se esiste gia' una partita con stesse (date, home, away)
    ma comp diverso E senza risultato, aggiorna il suo comp al nuovo codice
    (le quote agganciate restano). Elimina eventuali duplicati creati da run
    precedenti del parser vecchio. Ritorna (partite, quote) scritte."""
    n_matches = n_quotes = 0
    for ev in events:
        if not ev["home"] or not ev["away"] or not ev["date"]:
            continue
        hid = team_id(con, ev["home"])
        aid = team_id(con, ev["away"])
        season = _season_of(ev["date"])
        cm = ev.get("codice_manifestazione")
        comp = f"sisal:{cm}" if cm else comp_fallback

        # 1. cerca un match esistente con stesse squadre+data senza risultato
        #    (preferisci uno col comp giusto se c'e', altrimenti uno qualunque).
        existing = con.execute(
            """SELECT id, comp FROM matches
               WHERE home_id=? AND away_id=? AND date=? AND hg IS NULL
               ORDER BY (comp = ?) DESC, id ASC
               LIMIT 1""",
            (hid, aid, ev["date"], comp)).fetchone()

        if existing and existing["comp"] != comp:
            # migra: elimina eventuale conflitto che occupa gia' la coppia
            # (comp, season, home, away) e aggiorna la riga esistente.
            con.execute(
                """DELETE FROM matches WHERE comp=? AND season=? AND home_id=? AND away_id=?
                   AND id != ? AND hg IS NULL""",
                (comp, season, hid, aid, existing["id"]))
            con.execute(
                "UPDATE matches SET comp=?, season=?, time=? WHERE id=?",
                (comp, season, ev["time"], existing["id"]))
            mid = existing["id"]
        elif existing:
            # stesso comp, aggiorna solo time
            con.execute("UPDATE matches SET time=? WHERE id=?",
                        (ev["time"], existing["id"]))
            mid = existing["id"]
        else:
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


def sync(con, comp="it.1", curl_file=None, json_file=None, disciplina=1, log=print):
    """Scarica, parsa, salva. Tre modalita' di sorgente (prima vince):
      - json_file: legge una risposta gia' salvata (test/offline/repay).
      - curl_file: replay di un 'Copy as cURL (bash)' da DevTools (cookies).
      - default: fetch con SISAL_HEADERS (funziona solo se i cookies lo permettono)."""
    try:
        if json_file:
            with open(json_file, encoding="utf-8") as f:
                payload = json.load(f)
        elif curl_file:
            with open(curl_file, encoding="utf-8") as f:
                payload = fetch_from_curl(f.read())
        else:
            payload = fetch(disciplina=disciplina)
    except urllib.error.HTTPError as e:
        log(f"sisal: HTTP {e.code} ({e.reason}); probabilmente cookies scaduti")
        return 0, 0
    except urllib.error.URLError as e:
        log(f"sisal: errore rete ({e})")
        return 0, 0
    events = parse_events(payload)
    m, q = store(con, comp, events)
    log(f"sisal: {m} partite, {q} quote")
    con.commit()
    return m, q
