"""Genera ogni giorno molte schedine simulate (0 EUR reali) con strategie diverse."""
import random

from . import markets
from .model import score_matrix

MARGIN = 0.93      # quota stimata = 0.93 / probabilita' (aggio del bookmaker)
STAKE = 2.0
CAL_PRIOR = 100.0  # quanto servono prove prima di fidarsi di una correzione

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


def generate(con, model, day, n=50, seed=None):
    if con.execute("SELECT 1 FROM slips WHERE date = ? LIMIT 1", (day,)).fetchone():
        return 0
    cands = predict_matches(con, model, day)
    if len(cands) < 2:
        con.commit()
        return 0
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
        leg_odds = [max(round(MARGIN / p, 2), 1.01) for _, _, p in legs]
        total = 1.0
        for o in leg_odds:
            total *= o
        slip_id = con.execute(
            "INSERT INTO slips(date, strategy, odds, stake) VALUES (?,?,?,?)",
            (day, name, round(total, 2), STAKE)).lastrowid
        con.executemany(
            """INSERT INTO legs(slip_id, match_id, selection, prob, odds, xg_home, xg_away)
               VALUES (?,?,?,?,?,?,?)""",
            [(slip_id, m["id"], s, p, o, m["xg"][0], m["xg"][1])
             for (m, s, p), o in zip(legs, leg_odds)])
        made += 1
    con.commit()
    return made
