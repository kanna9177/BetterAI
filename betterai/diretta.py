"""Scraper per diretta.it (livesport/flashscore) — per lo storico ricco di
formazioni, xG, tiri, statistiche per giocatore.

Stesso pattern di sisal.py: per ora solo lo scheletro. Il parser vero si
scrive appena abbiamo un curl di risposta reale da DevTools.

Workflow di prima raccolta:
  1. Brave/Chrome su https://www.diretta.it/calcio/
     (disattiva Brave Shields per diretta.it come abbiamo fatto per sisal)
  2. DevTools F12 -> Network -> Fetch/XHR -> ordina per Size desc -> reload
  3. cerca gli xhr grossi verso 'd.flashscore.ninja' o 'www.diretta.it/x/'
     (nome tipico: 'f_1_1_it_1_...', 'summary_results', 'top_leagues')
  4. tasto destro -> Copy as cURL (bash) -> curl_diretta.txt
  5. python -m betterai diretta-probe --curl curl_diretta.txt --dump diretta.json

Diretta non usa JSON classico: la risposta e' un formato custom con
separatori '~' e coppie 'AA÷valore¬AB÷valore¬...'. Il parser lo svolgera'
in parse_response() dopo aver visto una risposta vera.
"""
import re
import shlex
import urllib.error
import urllib.request

from .sisal import _CURL_DROP_HEADERS, parse_curl  # riusiamo lo stesso parser cURL


def fetch_from_curl(text, timeout=30):
    """Replay di un 'Copy as cURL (bash)' da DevTools. Ritorna il body raw
    (bytes), non JSON: il formato diretta non e' standard."""
    url, headers = parse_curl(text)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_response(raw_bytes):
    """Placeholder. La vera parsing arriva dopo il primo dump.
    Diretta usa un formato tipo:
        AA÷abc123¬AD÷1699999999¬AE÷Inter¬AF÷Milan¬...
    Ritornera' [{date, time, home, away, lineups, stats, ...}, ...]."""
    body = raw_bytes.decode("utf-8", errors="replace")
    return {"preview": body[:200], "size": len(body)}
