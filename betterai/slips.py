"""Genera ogni giorno molte schedine simulate (0 EUR reali) con strategie diverse."""
import random

from . import markets
from .model import score_matrix

MARGIN = 0.93      # quota stimata = 0.93 / probabilita' (aggio del bookmaker)
STAKE = 2.0
CAL_PRIOR = 100.0  # quanto servono prove prima di fidarsi di una correzione
ESTIMATED = "stimato"

COMBO = {"R+BTTS", "R+TG", "HG+AG"}
STRATEGIES = {
    "sicura":     dict(legs=(2, 4), p=(0.72, 0.95), families=None),
    "bilanciata": dict(legs=(4, 6), p=(0.58, 0.82), families=None),
    "combo":      dict(legs=(6, 8), p=(0.48, 0.75), families=COMBO),
    "multigoal":  dict(legs=(3, 5), p=(0.45, 0.80), families={"HG+AG"}),
}


def calibration_factors(con):
    """Correzione imparata per esito: frequenza reale / probabilita' prevista."""
    return {r["selection"]: (r["hits"] + CAL_PRIOR) / (r["sum_p"] + CAL_PRIOR)
            for r in con.execute("SELECT * FROM calibration")}


def real_odds_map(con):
    """{(match_id, selection): (quota, fonte)} con la quota migliore tra le fonti
    disponibili (favorevole allo scommettitore). Vuoto se la tabella odds e' vuota."""
    out = {}
    for r in con.execute(
            """SELECT match_id, selection, odds, source FROM odds o1
               WHERE odds = (SELECT MAX(odds) FROM odds o2
                             WHERE o2.match_id = o1.match_id
                               AND o2.selection = o1.selection)"""):
        out[(r["match_id"], r["selection"])] = (r["odds"], r["source"])
    return out


def strategy_weights(con):
    """Piu' schedine alle strategie che finora hanno reso di piu' (minimo garantito)."""
    stats = {r["strategy"]: (r["s"], r["r"]) for r in con.execute(
        """SELECT strategy, SUM(stake) s, SUM(payout) r FROM slips
           WHERE status != 'open' GROUP BY strategy""")}
    w = {}
    for name in STRATEGIES:
        staked, returned = stats.get(name, (0.0, 0.0))
        w[name] = max(((returned + 200.0) / (staked + 200.0)) ** 2, 0.25)
    return w


def predict_matches(con, model, day):
    """Salva i pronostici grezzi di giornata e restituisce i candidati calibrati."""
    factors = calibration_factors(con)
    out = []
    for m in con.execute("SELECT * FROM matches WHERE date = ?", (day,)).fetchall():
        lam, nu = model.xg(m["comp"], m["home_id"], m["away_id"])
        raw = markets.probabilities(score_matrix(lam, nu))
        con.executemany(
            "INSERT OR IGNORE INTO predictions(match_id, selection, prob) VALUES (?,?,?)",
            [(m["id"], s, p) for s, p in raw.items()])
        cal = {s: min(max(p * factors.get(s, 1.0), 0.01), 0.99) for s, p in raw.items()}
        out.append(dict(id=m["id"], xg=(lam, nu), probs=cal))
    return out


def _leg_odds(real, match_id, selection, prob):
    """Quota reale se c'e', altrimenti stimata da MARGIN/prob. Ritorna (quota, fonte)."""
    hit = real.get((match_id, selection))
    if hit:
        return hit
    return max(round(MARGIN / prob, 2), 1.01), ESTIMATED


def generate(con, model, day, n=50, seed=None):
    if con.execute("SELECT 1 FROM slips WHERE date = ? LIMIT 1", (day,)).fetchone():
        return 0
    cands = predict_matches(con, model, day)
    if len(cands) < 2:
        con.commit()
        return 0
    real = real_odds_map(con)
    rng = random.Random(f"{seed}-{day}")
    weights = strategy_weights(con)
    names = list(STRATEGIES)
    seen, made = set(), 0
    for _ in range(n * 4):
        if made >= n:
            break
        name = rng.choices(names, [weights[x] for x in names])[0]
        st = STRATEGIES[name]
        legs, target = [], rng.randint(*st["legs"])
        for m in rng.sample(cands, len(cands)):
            opts = [(s, p) for s, p in m["probs"].items()
                    if st["p"][0] <= p <= st["p"][1]
                    and (st["families"] is None or markets.family(s) in st["families"])]
            if opts:
                legs.append((m, *rng.choice(opts)))
            if len(legs) == target:
                break
        if len(legs) < 2:
            continue
        sig = tuple(sorted((m["id"], s) for m, s, _ in legs))
        if sig in seen:
            continue
        seen.add(sig)
        priced = [(_leg_odds(real, m["id"], s, p), m, s, p) for m, s, p in legs]
        total = 1.0
        for (o, _), _, _, _ in priced:
            total *= o
        slip_id = con.execute(
            "INSERT INTO slips(date, strategy, odds, stake) VALUES (?,?,?,?)",
            (day, name, round(total, 2), STAKE)).lastrowid
        con.executemany(
            """INSERT INTO legs(slip_id, match_id, selection, prob, odds,
                                xg_home, xg_away, odds_source)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(slip_id, m["id"], s, p, o, m["xg"][0], m["xg"][1], src)
             for (o, src), m, s, p in priced])
        made += 1
    con.commit()
    return made
