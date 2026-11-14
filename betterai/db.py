"""Database SQLite: squadre, giocatori, partite, pronostici, schedine, storico."""
import os
import sqlite3

DEFAULT_PATH = "data/betterai.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams(
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
-- players e lineups sono pronti per una fonte dati che fornisca le formazioni.
CREATE TABLE IF NOT EXISTS players(
    id INTEGER PRIMARY KEY, team_id INTEGER REFERENCES teams(id),
    name TEXT NOT NULL, position TEXT, UNIQUE(team_id, name));
CREATE TABLE IF NOT EXISTS lineups(
    match_id INTEGER REFERENCES matches(id), team_id INTEGER, player_id INTEGER,
    starter INTEGER, PRIMARY KEY(match_id, player_id));
CREATE TABLE IF NOT EXISTS matches(
    id INTEGER PRIMARY KEY, comp TEXT, season TEXT, round TEXT, date TEXT, time TEXT,
    home_id INTEGER REFERENCES teams(id), away_id INTEGER REFERENCES teams(id),
    hg INTEGER, ag INTEGER, ht_hg INTEGER, ht_ag INTEGER,
    UNIQUE(comp, season, home_id, away_id));
CREATE INDEX IF NOT EXISTS matches_date ON matches(date);
-- Probabilita' grezza del modello per ogni esito del catalogo, per ogni partita.
CREATE TABLE IF NOT EXISTS predictions(
    match_id INTEGER, selection TEXT, prob REAL, result INTEGER,
    PRIMARY KEY(match_id, selection));
CREATE INDEX IF NOT EXISTS pred_open ON predictions(match_id) WHERE result IS NULL;
-- Quanto il modello sbaglia su ogni esito: e' cio' che viene "imparato".
CREATE TABLE IF NOT EXISTS calibration(
    selection TEXT PRIMARY KEY, n INTEGER, hits INTEGER, sum_p REAL);
CREATE TABLE IF NOT EXISTS slips(
    id INTEGER PRIMARY KEY, date TEXT, strategy TEXT, odds REAL, stake REAL,
    status TEXT DEFAULT 'open', payout REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS slips_date ON slips(date);
CREATE INDEX IF NOT EXISTS slips_open ON slips(id) WHERE status = 'open';
-- result: NULL aperta, 1 presa, 0 persa, -1 annullata (partita non giocata).
-- odds_source: 'stimato' (quota = MARGIN/prob) o nome fonte reale ('sisal', ...).
CREATE TABLE IF NOT EXISTS legs(
    id INTEGER PRIMARY KEY, slip_id INTEGER REFERENCES slips(id), match_id INTEGER,
    selection TEXT, prob REAL, odds REAL, xg_home REAL, xg_away REAL,
    result INTEGER, failed TEXT, reason TEXT, odds_source TEXT);
CREATE INDEX IF NOT EXISTS legs_slip ON legs(slip_id);
CREATE INDEX IF NOT EXISTS legs_open ON legs(match_id) WHERE result IS NULL;
CREATE TABLE IF NOT EXISTS learning_log(
    date TEXT PRIMARY KEY, slips INTEGER, won INTEGER, staked REAL, returned REAL,
    legs INTEGER, hits INTEGER, exp_hits REAL, matches INTEGER,
    ll_model REAL, ll_base REAL, lessons TEXT);
-- Quote reali per partita + esito, da una fonte (sisal, betfair, ...).
CREATE TABLE IF NOT EXISTS odds(
    match_id INTEGER REFERENCES matches(id),
    selection TEXT, odds REAL, source TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(match_id, selection, source));
CREATE INDEX IF NOT EXISTS odds_match ON odds(match_id);
"""


def connect(path=DEFAULT_PATH):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # Migrazione non-breaking: la colonna legs.odds_source e' stata aggiunta dopo.
    try:
        con.execute("ALTER TABLE legs ADD COLUMN odds_source TEXT")
    except sqlite3.OperationalError:
        pass  # esiste gia'
    return con


def team_id(con, name):
    con.execute("INSERT OR IGNORE INTO teams(name) VALUES (?)", (name,))
    return con.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()[0]
