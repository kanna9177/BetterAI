"""Scarica calendari e risultati reali da openfootball (GitHub, senza chiave API)."""
import json
import urllib.error
import urllib.request

from .db import team_id

BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
COMPS = ["it.1", "en.1", "es.1", "de.1", "fr.1"]
SEASONS = ["2023-24", "2024-25", "2025-26", "2026-27"]


def fetch(season, comp):
    try:
        with urllib.request.urlopen(f"{BASE}/{season}/{comp}.json", timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def store(con, comp, season, payload):
    n = 0
    for m in payload.get("matches", []):
        if not m.get("date") or not m.get("team1") or not m.get("team2"):
            continue
        score = m.get("score") or {}
        if isinstance(score, list):  # la fonte scrive cosi' alcuni finali (gli 0-0)
            score = {"ft": score}
        ft = score.get("ft") or [None, None]
        ht = score.get("ht") or [None, None]
        con.execute(
            """INSERT INTO matches(comp, season, round, date, time, home_id, away_id,
                                   hg, ag, ht_hg, ht_ag)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(comp, season, home_id, away_id) DO UPDATE SET
                 round=excluded.round, date=excluded.date, time=excluded.time,
                 hg=excluded.hg, ag=excluded.ag,
                 ht_hg=excluded.ht_hg, ht_ag=excluded.ht_ag""",
            (comp, season, m.get("round"), m["date"], m.get("time"),
             team_id(con, m["team1"]), team_id(con, m["team2"]),
             ft[0], ft[1], ht[0], ht[1]))
        n += 1
    return n


def sync(con, comps=COMPS, seasons=SEASONS, log=print):
    for season in seasons:
        for comp in comps:
            payload = fetch(season, comp)
            if payload is None:
                log(f"  {season} {comp}: non disponibile")
                continue
            log(f"  {season} {comp}: {store(con, comp, season, payload)} partite")
    con.commit()
